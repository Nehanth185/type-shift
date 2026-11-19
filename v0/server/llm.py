"""Vision-LLM providers for the glasses server.

Provider "openai" works with any OpenAI-compatible /chat/completions endpoint
(NVIDIA NIM by default, also OpenAI, Groq, OpenRouter, a local vLLM/Ollama, ...).
Provider "mock" needs no key and no network, so the whole loop can be tested.

Two jobs:
  ask(jpeg, question)                       v0 glasses loop: photo -> one HUD line + spoken sentence
  answer_docs(question, hits, jpeg=None)    v1: answer only from retrieved doc passages, with citations
                                            -> (hud_lines, full, sources) or None for "not in your docs"
Select with LLM_PROVIDER=mock | nim | openai (see make_provider).
"""
import base64, io, json, os, re, time, urllib.error, urllib.request
from dataclasses import dataclass

from PIL import Image

SYSTEM_PROMPT = """You are the assistant inside a field worker's AI smart glasses. The image is what the worker sees right now through the glasses camera. Your answer is shown on a tiny display AND read aloud through a bone-conduction speaker.

Reply in exactly this format, two lines, nothing else:
HUD: <at most 7 words for the display>
SAY: <1 to 3 short spoken sentences, under 45 words>

Rules:
- Copy label text, part numbers, model and serial numbers, readings and units exactly as printed.
- Never guess numbers or text. If you cannot clearly read it, or you are not sure, write HUD: Can't read that, and in SAY tell the worker what would help (move closer, more light, hold steady).
- If something looks unsafe (live electrical, pressure, moving machinery, missing PPE, lockout/tagout), say that first in both lines.
- For procedures, give only the next step, and remind them to follow their site procedure for anything safety-critical.
- No markdown, no lists, no preamble."""

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
# meta/llama-3.2-90b-vision-instruct is listed by NVIDIA as ending 2026-11-16, so the default moved to Nemotron VL.
NIM_DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"

DEFAULT_QUESTION = "What am I looking at? Read any important label, number or gauge, and tell me anything I should do or watch out for."

# Per-model quirks (learned in simulator/concept_test): Llama 3.2 Vision on NIM accepts only user/assistant roles.
MODEL_PROFILES = {
    "meta/llama-3.2-90b-vision-instruct": {"no_system": True},
    "meta/llama-3.2-11b-vision-instruct": {"no_system": True},
    "nvidia/nemotron-3.5-super-vl-120b-a12b": {"extra": {"reasoning_effort": "none"}},
}
NVCF_STATUS = "https://api.nvcf.nvidia.com/v2/nvcf/pexec/status/"


@dataclass
class Answer:
    hud: str
    say: str
    raw: str
    model: str
    latency_s: float


def jpeg_data_url(jpeg: bytes, max_side=1024, max_b64=180_000) -> str:
    """Shrink until the base64 is under NIM's inline-image limit (~180 KB)."""
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    k = min(1.0, max_side / max(im.size))
    if k < 1:
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    q = 90
    while True:
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=q)
        b = base64.b64encode(buf.getvalue()).decode()
        if len(b) <= max_b64 or (q <= 40 and max(im.size) <= 320):
            return "data:image/jpeg;base64," + b
        if q > 60:
            q -= 15
        else:
            im = im.resize((int(im.width * 0.8), int(im.height * 0.8)), Image.LANCZOS)


def strip_think(text) -> str:
    """Remove reasoning a model put in its content: <think>...</think>, an orphan '...</think>' (opening tag eaten by
    the API), or an unclosed '<think>...' (reply cut off mid-thought, so nothing usable follows)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S | re.I)
    text = re.sub(r"<think>.*\Z", "", text, flags=re.S | re.I)
    return text.strip()


class LLMEmptyReply(RuntimeError):
    """The API answered 200 but with no usable text (null content, only reasoning, or cut off)."""


def message_text(out: dict) -> str:
    """Text of the first choice. Raises LLMEmptyReply instead of returning '' so an empty reply is never mistaken
    for a refusal ("not in your docs") or a blank answer. Handles content given as a list of parts."""
    try:
        ch = out["choices"][0]
    except (KeyError, IndexError, TypeError):
        raise LLMEmptyReply(f"no choices in reply: {str(out)[:200]}") from None
    msg = ch.get("message") or {}
    c = msg.get("content")
    if isinstance(c, list):
        c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
    text = strip_think(c or "")
    if not text:
        why = ch.get("finish_reason") or "?"
        rc = msg.get("reasoning_content") or msg.get("reasoning") or ""
        rtok = ((out.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
        raise LLMEmptyReply(f"model returned no answer text (finish_reason={why}, reasoning chars={len(rc)}, "
                            f"reasoning tokens={rtok}); raise max_tokens or turn reasoning off")
    return text


def parse_reply(text: str):
    text = strip_think(text)
    text = text.replace("**", "").replace("`", "")
    hud = re.search(r"^\s*HUD\s*:\s*(.+)$", text, re.M | re.I)
    say = re.search(r"^\s*SAY\s*:\s*(.+)$", text, re.M | re.I | re.S)
    hud = hud.group(1).strip() if hud else ""
    say = re.sub(r"\s+", " ", say.group(1)).strip() if say else ""
    if not hud and not say:  # model ignored the format: use the text itself
        say = re.sub(r"\s+", " ", text).strip()
    if not hud:
        hud = " ".join(say.split()[:7])
    if not say:
        say = hud
    return clamp_hud(hud), say


def clamp_hud(hud: str, max_words=7, max_chars=40) -> str:
    hud = re.sub(r"\s+", " ", hud).strip().strip('"').rstrip(".")
    words = hud.split()[:max_words]
    out = ""
    for w in words:
        if len(out) + len(w) + (1 if out else 0) > max_chars:
            break
        out = f"{out} {w}".strip()
    return out or "No answer"


class MockProvider:
    """No key, no network. Gives a deterministic answer based on the image so the loop is testable."""
    name = "mock"

    def ask(self, jpeg: bytes, question: str) -> Answer:
        t0 = time.time()
        im = Image.open(io.BytesIO(jpeg)).convert("L")
        w, h = im.size
        small = im.resize((32, 24))
        px = list(small.getdata()) if not hasattr(small, "get_flattened_data") else list(small.get_flattened_data())
        mean = sum(px) / len(px)
        if mean < 40:
            hud, say = "Can't read that", "It's too dark for me to read anything. Turn on a light or move closer, then tap again."
        else:
            hud = "Mock: photo received"
            say = (f"Mock mode. I got a {w} by {h} photo, average brightness {mean:.0f} out of 255. "
                   f"Your question was: {question[:120].rstrip('.?! ')}. Set NVIDIA_API_KEY to get real answers.")
        raw = f"HUD: {hud}\nSAY: {say}"
        hud, say = parse_reply(raw)
        return Answer(hud, say, raw, "mock", time.time() - t0)


    def answer_docs(self, question, hits, jpeg=None):
        """Deterministic: quote the best-matching sentence from the passages. A too-dark photo -> can't read."""
        import grounded
        if jpeg:
            im = Image.open(io.BytesIO(jpeg)).convert("L").resize((32, 24))
            px = list(im.get_flattened_data()) if hasattr(im, "get_flattened_data") else list(im.getdata())
            if sum(px) / len(px) < 40:
                return grounded.CANT_READ_HUD, "It's too dark for me to read anything. Turn on a light or move closer.", []
        return grounded.mock_answer(question, hits)


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, base_url: str, api_key: str, model: str, timeout=None):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.timeout = float(timeout or os.environ.get("LLM_TIMEOUT", "18"))
        self.prof = MODEL_PROFILES.get(model, {})
        if os.environ.get("LLM_NO_SYSTEM_ROLE") in ("1", "true"):
            self.prof = {**self.prof, "no_system": True}

    def build_request(self, jpeg: bytes, question: str) -> dict:
        img = {"type": "image_url", "image_url": {"url": jpeg_data_url(jpeg)}}
        user = f"Worker's question: {question}"
        if self.prof.get("no_system"):
            messages = [{"role": "user", "content": [{"type": "text", "text": SYSTEM_PROMPT + "\n\n" + user}, img]}]
        else:
            messages = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": [{"type": "text", "text": user}, img]}]
        return {"model": self.model, "messages": messages, "max_tokens": 160, "temperature": 0.2, "top_p": 0.7,
                "stream": False, **self.prof.get("extra", {})}

    def _post(self, body: dict):
        """POST /chat/completions. One overall deadline (LLM_TIMEOUT, default 18 s: under the
        Mentra adapter's 20 s request timeout, so the glasses get the server's error, not "can't reach") covers the first request, NVIDIA's
        202 queue polling and one retry on 429/5xx or a network error. Measured Nemotron p90 is ~2 s
        (REAL_MODEL_RESULTS.md); the deadline is for NVIDIA queue spikes."""
        hdr = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}
        deadline = time.time() + self.timeout
        left = lambda: max(1.0, deadline - time.time())
        for attempt in (1, 2):
            req = urllib.request.Request(self.base_url + "/chat/completions", json.dumps(body).encode(), hdr, method="POST")
            try:
                resp = urllib.request.urlopen(req, timeout=left())
                status, data, reqid = resp.status, resp.read(), resp.headers.get("NVCF-REQID")
                while status == 202 and reqid:  # NVIDIA queues busy requests: poll until done or out of time
                    if time.time() >= deadline:
                        raise TimeoutError(f"NVIDIA queue: no answer within {self.timeout:.0f} s (request {reqid})")
                    time.sleep(1)
                    r = urllib.request.urlopen(urllib.request.Request(NVCF_STATUS + reqid, headers={
                        "Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}), timeout=left())
                    status, data, reqid = r.status, r.read(), r.headers.get("NVCF-REQID", reqid)
                return json.loads(data)
            except urllib.error.HTTPError as e:
                retry = e.code in (429, 500, 502, 503, 504) and attempt == 1 and deadline - time.time() > 4
                if not retry: raise
                wait = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(float(wait) if wait and wait.replace(".", "").isdigit() else 2.0, 5.0))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt == 2 or deadline - time.time() < 4 or isinstance(e, TimeoutError) and "queue" in str(e):
                    raise RuntimeError(f"LLM unreachable or too slow: {e}") from None
                time.sleep(1)
        raise RuntimeError("LLM request failed twice")

    def build_doc_request(self, question, hits, jpeg=None) -> dict:
        import grounded
        content = [{"type": "text", "text": grounded.doc_prompt(question, hits, has_image=bool(jpeg))}]
        if jpeg:
            content.append({"type": "image_url", "image_url": {"url": jpeg_data_url(jpeg)}})
        if self.prof.get("no_system"):
            content[0]["text"] = grounded.DOC_SYSTEM + "\n\n" + content[0]["text"]
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "system", "content": grounded.DOC_SYSTEM}, {"role": "user", "content": content}]
        return {"model": self.model, "messages": messages, "max_tokens": 220, "temperature": 0.1, "top_p": 0.7,
                "stream": False, **self.prof.get("extra", {})}

    def answer_docs(self, question, hits, jpeg=None):
        import grounded
        try:
            raw = message_text(self._post(self.build_doc_request(question, hits, jpeg)))
            self.last_raw = raw  # kept for the ask log when the reply is judged a refusal
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from None
        return grounded.parse_doc_reply(raw, hits)

    def ask(self, jpeg: bytes, question: str) -> Answer:
        t0 = time.time()
        try:
            raw = message_text(self._post(self.build_request(jpeg, question)))
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from None
        hud, say = parse_reply(raw)
        return Answer(hud, say, raw, self.model, time.time() - t0)


def make_provider():
    """LLM_PROVIDER=mock  -> MockProvider (also the default when no key is set)
       LLM_PROVIDER=nim   -> NVIDIA NIM: NVIDIA_API_KEY, model LLM_MODEL (default Nemotron 3.5 Super VL)
       LLM_PROVIDER=openai-> any OpenAI-compatible API: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
       unset + NVIDIA_API_KEY -> nim"""
    kind = os.environ.get("LLM_PROVIDER", "").lower()
    nv_key = os.environ.get("NVIDIA_API_KEY", "")
    key = os.environ.get("LLM_API_KEY") or nv_key
    if kind == "mock" or (not kind and not key):
        return MockProvider()
    if kind == "nim" or (not kind and nv_key and not os.environ.get("LLM_BASE_URL")):
        p = OpenAICompatProvider(NIM_BASE_URL, nv_key or key, os.environ.get("LLM_MODEL", NIM_DEFAULT_MODEL))
        p.name = "nim"
        return p
    return OpenAICompatProvider(
        base_url=os.environ.get("LLM_BASE_URL", NIM_BASE_URL),
        api_key=key,
        model=os.environ.get("LLM_MODEL", NIM_DEFAULT_MODEL),
    )
