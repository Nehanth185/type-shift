"""v1 work API: team docs, grounded /ask (JSON), guide mode, personal memory, ready cards.
Contract: ../API.md. Mounted by server.py next to the v0 endpoints.
"""
import base64, binascii, io, json, os, time, uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Literal, Optional

import grounded, ingest, memory, retrieval
from llm import DEFAULT_QUESTION
from store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "20")) * 1024 * 1024
TOP_K = int(os.environ.get("RETRIEVAL_TOP_K", "4"))
S = {"store": None, "provider": None, "embedder": None, "token": "", "log_dir": None, "auth_mode": "off"}
router = APIRouter()


def init(provider, db_path=None, token="", log_dir=None, embedder="auto", auth_mode=None):
    """auth_mode: "off" (worker_id trusted as sent; optional shared DEVICE_TOKEN) or "worker" (every request
    carries Authorization: Bearer <worker token>; the token decides who the worker is). Default: env AUTH_MODE."""
    S["store"] = Store(db_path or os.environ.get("GLASSES_DB", os.path.join(HERE, "data", "glasses.db")))
    S["provider"] = provider
    S["embedder"] = retrieval.make_embedder() if embedder == "auto" else embedder
    S["token"], S["log_dir"] = token, log_dir
    mode = (auth_mode or os.environ.get("AUTH_MODE", "off")).strip().lower()
    if mode not in ("off", "worker"): raise ValueError(f"AUTH_MODE must be off or worker, not {mode!r}")
    S["auth_mode"] = mode
    return S["store"]


def store() -> Store:
    if S["store"] is None: raise RuntimeError("workapi.init() not called")
    return S["store"]


def _bearer(authorization):
    a = (authorization or "").strip()
    return a[7:].strip() if a[:7].lower() == "bearer " else ""


def caller(x_device_token="", authorization=""):
    """Who is calling. AUTH_MODE=worker -> the worker row for the Bearer token (401 if missing/unknown).
    AUTH_MODE=off -> None (anyone), after the optional shared DEVICE_TOKEN check (header X-Device-Token,
    or the same value as a Bearer token so the companion app can send it)."""
    if S["auth_mode"] == "worker":
        w = store().worker_for_token(_bearer(authorization))
        if not w:
            raise HTTPException(401, "worker token missing or invalid (Authorization: Bearer <token>)",
                                headers={"WWW-Authenticate": "Bearer"})
        return w
    if S["token"] and S["token"] not in (x_device_token, _bearer(authorization)):
        raise HTTPException(401, "bad device token")
    return None


def who(x_device_token: str = Header(default=""), authorization: str = Header(default="")):
    return caller(x_device_token, authorization)


def auth(tok):  # kept for older callers: shared device-token check only
    caller(tok, "")


def worker_of(w, claimed):
    """The worker a request acts as. With a token, a different claimed worker_id is refused (403), a missing one
    is filled in from the token. Without auth, worker_id is required and trusted."""
    claimed = (claimed or "").strip()
    if w is None:
        if not claimed: raise HTTPException(422, "worker_id is required")
        return claimed
    if claimed and claimed != w["worker_id"]:
        raise HTTPException(403, f"this token belongs to worker '{w['worker_id']}', not '{claimed}'")
    return w["worker_id"]


def workspace_of(w, ws):
    ws = (ws or "").strip()
    if not ws: raise HTTPException(422, "workspace_id is required")
    if w is not None and w["workspace_id"] and ws != w["workspace_id"]:
        raise HTTPException(403, f"worker '{w['worker_id']}' has no access to workspace '{ws}'")
    return ws


def add_document(workspace_id, filename, data):
    title, kind, pages = ingest.parse(filename, data)
    chunks = ingest.chunk_pages(pages)
    if not chunks: raise ingest.IngestError("no searchable text in this file")
    embs = None
    if S["embedder"] is not None:
        embs = [retrieval.pack(v) for v in S["embedder"].encode([f"{title}. {t}" for _, t in chunks])]
    doc_id = store().add_doc(workspace_id, title, os.path.basename(filename), kind, pages, chunks, embs)
    return {"doc_id": doc_id, "title": title, "pages": len(pages), "chunks": len(chunks)}


# ---------------- docs
@router.post("/docs")
async def upload_doc(file: UploadFile = File(...), workspace_id: str = Form(...), w=Depends(who)):
    workspace_id = workspace_of(w, workspace_id)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD: raise HTTPException(413, f"file is over {MAX_UPLOAD // 1024 // 1024} MB")
    if not ingest.kind_for(file.filename): raise HTTPException(415, "only .pdf, .md and .txt files are supported")
    try:  # G43: in a worker thread, so a scanned PDF's OCR (seconds per page) doesn't freeze every other request
        return await run_in_threadpool(add_document, workspace_id.strip(), file.filename, data)
    except ingest.IngestError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/docs")
def list_docs(workspace_id: str, w=Depends(who)):
    return store().list_docs(workspace_of(w, workspace_id))


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: str, w=Depends(who)):
    d = store().get_doc(doc_id)
    if not d: raise HTTPException(404, "no such doc")
    workspace_of(w, d["workspace_id"])
    store().delete_doc(doc_id)
    return {"deleted": doc_id}


# ---------------- ask
class AskIn(BaseModel):
    workspace_id: str = Field(min_length=1)
    worker_id: Optional[str] = None  # required unless a worker token says who this is
    question: str = Field(default="", max_length=1000)  # may be empty when image_b64 is sent
    image_b64: Optional[str] = None
    mode: Literal["answer", "guide"] = "answer"


def decode_image(b64):
    if not b64: return None
    if b64.startswith("data:"): b64 = b64.split(",", 1)[-1]
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "image_b64 is not valid base64") from None
    if raw[:2] == b"\xff\xd8": return raw
    try:  # PNG/WebP from a phone or browser: convert to JPEG for the vision model
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "image_b64 must be a JPEG or PNG") from None


def out(t0, hud, full, sources, guide=None, **extra):
    hud = [grounded.clamp_line(l) for l in hud if l and l.strip()][:grounded.HUD_LINES] or ["No answer"]
    return {"answer_hud": hud, "answer_full": full, "sources": sources, "guide": guide,
            "latency_ms": round((time.time() - t0) * 1000), **extra}


def log_ask(rec):
    if not S["log_dir"]: return
    os.makedirs(S["log_dir"], exist_ok=True)
    with open(os.path.join(S["log_dir"], "asks_v1.jsonl"), "a") as f:  # text only; photos are never stored
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}) + "\n")


def start_guide(body, hits, label):
    st = store()
    for h in hits:
        pages = st.pages_for(h["doc_id"])
        seq = next((p["seq"] for p in pages if p["page"] == h["page"]), None)
        if seq is None: continue
        found = grounded.extract_steps(pages, seq)
        if not found: continue
        steps, warning, _, name = found
        gname = name or grounded.short_title(h["title"])
        mem_id = st.add_memory(body.worker_id, "guide", f"Started: {gname}" + (f" on {label}" if label else ""),
                               {"label": label, "title": gname, "doc": h["title"], "doc_id": h["doc_id"], "step": 1,
                                "total": len(steps), "done": False})
        st.set_guide(body.worker_id, mem_id, body.workspace_id, h["doc_id"], h["title"], steps, warning, [])
        return st.get_guide(body.worker_id)
    return None


class TokenBucket:
    """Per-worker limiter: `cap` burst, refills `per_min` tokens a minute."""
    def __init__(self, cap=10, per_min=30, clock=time.time):
        self.cap, self.rate, self.clock, self.b = cap, per_min / 60.0, clock, {}

    def take(self, key):
        now = self.clock()
        tok, last = self.b.get(key, (self.cap, now))
        tok = min(self.cap, tok + (now - last) * self.rate)
        if tok < 1:
            self.b[key] = (tok, now)
            return False, int((1 - tok) / self.rate) + 1
        self.b[key] = (tok - 1, now)
        return True, 0


ASK_LIMIT = TokenBucket(int(os.environ.get("ASK_BURST", "10")), int(os.environ.get("ASK_PER_MIN", "30")))


async def ask_json(request: Request, x_device_token: str = ""):
    """POST /ask with a JSON body. server.py's /ask routes here when Content-Type is application/json
    (multipart and raw-JPEG bodies keep the v0 behaviour)."""
    w = caller(x_device_token or request.headers.get("x-device-token", ""), request.headers.get("authorization", ""))
    from pydantic import ValidationError
    try:
        body = AskIn.model_validate(await request.json())
    except ValidationError as e:
        return JSONResponse({"detail": json.loads(e.json(include_url=False))}, status_code=422)
    except ValueError:
        raise HTTPException(400, "body is not valid JSON") from None
    body.worker_id = worker_of(w, body.worker_id)
    body.workspace_id = workspace_of(w, body.workspace_id)
    ok, wait = ASK_LIMIT.take(body.worker_id or "anon")
    if not ok:
        raise HTTPException(429, f"too many questions, try again in {wait}s", headers={"Retry-After": str(wait)})
    # G43: the LLM call blocks for up to LLM_TIMEOUT (18 s); run it in a worker thread so other workers' requests
    # (and /health) are served meanwhile instead of queueing behind it on the event loop.
    return await run_in_threadpool(_answer, body)


def _answer(body):
    t0, st = time.time(), store()
    jpeg = decode_image(body.image_b64)
    q = body.question.strip()
    if not q and not jpeg: raise HTTPException(422, "send a question, a photo (image_b64), or both")
    label = memory.current_label(st, body.worker_id)
    if not q:
        return photo_only(t0, st, body, jpeg, label)

    cmd = memory.command(q)
    if cmd:
        hud, full = memory.run_command(st, body.worker_id, body.workspace_id, cmd[0], cmd[1], label)
        return out(t0, hud, full, [], None, kind="memory")

    hits = retrieval.search(st.chunks_for(body.workspace_id), q, label, k=TOP_K, embedder=S["embedder"])
    if not hits:
        st.add_memory(body.worker_id, "ask", f"Q: {q} (not in docs)", {"label": label, "question": q,
                                                                        "hud": grounded.NOT_IN_DOCS_HUD, "not_in_docs": True})
        log_ask({"worker": body.worker_id, "ws": body.workspace_id, "q": q, "hits": 0})
        return out(t0, grounded.NOT_IN_DOCS_HUD, grounded.NOT_IN_DOCS_FULL, [], None, not_in_docs=True,
                   refused_by="retrieval")

    note = ""
    if body.mode == "guide":
        g = start_guide(body, hits, label)
        if g:
            hud, full, srcs, gv = grounded.guide_view(g)
            log_ask({"worker": body.worker_id, "ws": body.workspace_id, "q": q, "guide": g["title"], "steps": len(g["steps"])})
            return out(t0, hud, full, srcs, gv)
        note = " I didn't find a numbered procedure for this in your docs, so here is the closest answer."

    llm_hits = page_context(st, hits) if S["provider"].name != "mock" else None
    try:
        res = S["provider"].answer_docs(q, llm_hits or hits, jpeg)
    except Exception as e:  # noqa: BLE001  - always give the glasses something to show
        print(f"[ask] LLM error: {e}")
        log_ask({"worker": body.worker_id, "ws": body.workspace_id, "q": q, "error": str(e)[:300], "image": bool(jpeg),
                 "latency_ms": round((time.time() - t0) * 1000)})
        return out(t0, ["AI service error", "Try again in a moment"], "The AI service failed.", [], None,
                   error=str(e)[:300])
    if res is None:
        st.add_memory(body.worker_id, "ask", f"Q: {q} (not in docs)", {"label": label, "question": q,
                                                                        "hud": grounded.NOT_IN_DOCS_HUD, "not_in_docs": True})
        log_ask({"worker": body.worker_id, "ws": body.workspace_id, "q": q, "hits": len(hits), "refused_by": "model",
                 "raw": (getattr(S["provider"], "last_raw", "") or "")[:400],
                 "image": bool(jpeg), "latency_ms": round((time.time() - t0) * 1000)})
        return out(t0, grounded.NOT_IN_DOCS_HUD, grounded.NOT_IN_DOCS_FULL, [], None, not_in_docs=True,
                   refused_by="model")
    hud, full, srcs = res
    raw_hud, raw_full = hud, full
    hud, full, unverified = grounded.guard_numbers(hud, full, q, llm_hits if llm_hits is not None else hits, bool(jpeg)) \
        if S["provider"].name != "mock" else (hud, full, [])
    st.add_memory(body.worker_id, "ask", f"Q: {q} → {' / '.join(hud)}",
                  {"label": label, "question": q, "hud": hud, "not_in_docs": False,
                   "sources": [f"{s['title']} p.{s['page']}" for s in srcs]})
    log_ask({"worker": body.worker_id, "ws": body.workspace_id, "q": q, "hud": hud,
             "sources": [f"{s['title']} p.{s['page']}" for s in srcs], "image": bool(jpeg), "unverified": unverified,
             "latency_ms": round((time.time() - t0) * 1000)})
    extra = {"unverified_numbers": unverified} if unverified else {}
    if unverified and jpeg:  # photo reading the docs can't back: worker must confirm before it is shown as a reading
        pid = uuid.uuid4().hex[:8]
        st.pending_put(pid, {"worker": body.worker_id, "hud": raw_hud, "full": raw_full, "srcs": srcs, "nums": unverified}, ttl=int(os.environ.get("CONFIRM_TTL", "60")))
        extra.update(needs_confirm=True, pending_id=pid)
        # G43: the spoken/app answer held the number back too. Before, answer_full still said it ("...Yes, 60 PSI is
        # within the normal range" from Nemotron on a gauge reading 0), and Mentra speaks answer_full aloud.
        said = "; ".join(l for l in hud if not l.startswith(("⚠", "Source:")))  # guarded lines: doc numbers only
        full = ("I read a number off the photo that isn't in your docs, so I'm holding it back. Read the gauge "
                "yourself, or confirm to see my reading." + (f" Your docs say: {said}." if said else "")
                + (f" Source: {srcs[0]['title']}, page {srcs[0]['page']}." if srcs else ""))
    if body.mode == "answer" and srcs:  # tell the app a step-by-step version exists for this page
        pages = st.pages_for(srcs[0]["doc_id"])
        seq = next((p["seq"] for p in pages if p["page"] == srcs[0]["page"]), None)
        if seq is not None and grounded.extract_steps(pages, seq):
            extra["guide_available"] = True
            note = f" There are numbered steps for this on page {srcs[0]['page']}; ask in guide mode to go one step at a time."
    return out(t0, hud, full + note, srcs, None, **extra)


class ConfirmIn(BaseModel):
    pending_id: str
    worker_id: Optional[str] = None
    ok: bool = True


@router.post("/confirm")
def confirm(body: ConfirmIn, w=Depends(who)):
    t0 = time.time()
    me = worker_of(w, body.worker_id) if (w is not None or body.worker_id) else None
    p = store().pending_get(body.pending_id)
    # G43: only the worker who took the photo can confirm or discard it (same 404 as "unknown", so ids aren't probed)
    if p is None or (me and p.get("worker") != me):
        raise HTTPException(404, "nothing waiting for confirmation")
    store().pending_pop(body.pending_id)
    # same shape as /ask (source objects, clamped HUD) so the companion and glasses render it with the same code
    if body.ok:
        return out(t0, p["hud"], "Confirmed by worker. " + p["full"], p["srcs"], None, confirmed=True)
    return out(t0, ["Reading discarded", "Read it yourself"], "Discarded.", [], None, confirmed=False)


PAGE_CONTEXT_CHARS = int(os.environ.get("LLM_PAGE_CHARS", "1500"))


def page_context(st, hits):
    """For a real model, send each hit's whole page (once per doc+page, best hit first) instead of the ~110-word chunk.
    Chunks can end mid-sentence ("...Call the" | "charge nurse if the alarm repeats."), and the model then rightly
    says the passage is cut off. Pages longer than LLM_PAGE_CHARS keep a window around the chunk."""
    out, seen, cache = [], set(), {}
    for h in hits:
        key = (h["doc_id"], h["page"])
        if key in seen: continue
        seen.add(key)
        pages = cache.setdefault(h["doc_id"], st.pages_for(h["doc_id"]))
        text = "\n".join(p["text"] for p in pages if p["page"] == h["page"]) or h["text"]
        if len(text) > PAGE_CONTEXT_CHARS:
            i = max(0, text.find(h["text"][:60]))
            a = max(0, min(i - (PAGE_CONTEXT_CHARS - len(h["text"])) // 2, len(text) - PAGE_CONTEXT_CHARS))
            text = text[a:a + PAGE_CONTEXT_CHARS]
        out.append({**h, "page_text": text})  # "text" stays the matched chunk: it is the source snippet the app shows
    return out


def photo_only(t0, st, body, jpeg, label):
    """A photo with no question: there is nothing to search the docs for, so the vision model describes the photo
    (v0 path). The answer is marked ungrounded and carries no sources."""
    try:
        ans = S["provider"].ask(jpeg, DEFAULT_QUESTION)
    except Exception as e:  # noqa: BLE001
        print(f"[ask] LLM error: {e}")
        return out(t0, ["AI service error", "Try again in a moment"], "The AI service failed.", [], None,
                   error=str(e)[:300])
    if ans.hud.lower().startswith("can't read"):
        hud = grounded.CANT_READ_HUD
    else:
        hud = grounded.wrap(ans.hud, max_lines=2) + ["Not from your docs"]
    full = ans.say + " This is from the photo only, not from your team's docs. Ask a question to search the docs."
    st.add_memory(body.worker_id, "ask", f"Q: (photo only) → {' / '.join(hud)}",
                  {"label": label, "question": "", "hud": hud, "not_in_docs": True})
    log_ask({"worker": body.worker_id, "ws": body.workspace_id, "q": "", "hud": hud, "image": True})
    return out(t0, hud, full, [], None, ungrounded=True)


# ---------------- guide
class WorkerIn(BaseModel):
    worker_id: Optional[str] = None


def _move(worker_id, delta, w):
    worker_id = worker_of(w, worker_id)
    t0, st = time.time(), store()
    g = st.get_guide(worker_id)
    if not g: raise HTTPException(404, "no active guide: ask with mode 'guide' first")
    n = len(g["steps"])
    if delta > 0 and g["idx"] == n - 1:  # swiped past the last step
        if g["mem_id"] and (m := st.get_memory(g["mem_id"])):
            st.update_memory(m["id"], meta={**m["meta"], "step": n, "done": True})
        hud, full, srcs, gv = grounded.guide_view(g)
        return out(t0, [f"All {n} steps done", "Say 'done, log it'", hud[-1]],
                   "That was the last step. Say 'done, log it' to save it.", srcs, gv, finished=True)
    g["idx"] = max(0, min(n - 1, g["idx"] + delta))
    st.set_guide_idx(worker_id, g["idx"])
    if g["mem_id"] and (m := st.get_memory(g["mem_id"])):
        st.update_memory(m["id"], meta={**m["meta"], "step": g["idx"] + 1})
    hud, full, srcs, gv = grounded.guide_view(g)
    return out(t0, hud, full, srcs, gv)


@router.post("/guide/next")
def guide_next(body: WorkerIn, w=Depends(who)):
    return _move(body.worker_id, +1, w)


@router.post("/guide/prev")
def guide_prev(body: WorkerIn, w=Depends(who)):
    return _move(body.worker_id, -1, w)


# ---------------- memory (worker-owned; no employer endpoint by design)
@router.get("/memory")
def get_memory(worker_id: Optional[str] = None, w=Depends(who)):
    worker_id = worker_of(w, worker_id)
    return {"worker_id": worker_id, "items": [memory.public(i) for i in store().memory(worker_id)]}


@router.delete("/memory/{mem_id}")
def delete_memory(mem_id: str, worker_id: Optional[str] = None, w=Depends(who)):
    owner = worker_of(w, worker_id) if (w is not None or worker_id) else None  # with a token: always the owner
    it = store().get_memory(mem_id)
    if not it or (owner and it["worker_id"] != owner): raise HTTPException(404, "no such memory item")
    store().delete_memory(mem_id)
    return {"deleted": mem_id}


@router.delete("/memory")
def forget_all(worker_id: Optional[str] = None, w=Depends(who)):
    worker_id = worker_of(w, worker_id)
    return {"worker_id": worker_id, "deleted": store().forget_worker(worker_id)}


# ---------------- context -> ready card
class ContextIn(BaseModel):
    worker_id: Optional[str] = None
    trigger: Literal["see_machine", "open_job", "start_shift"]
    label: str = Field(default="", max_length=120)


@router.post("/context")
def context(body: ContextIn, w=Depends(who)):
    body.worker_id = worker_of(w, body.worker_id)
    st, label = store(), body.label.strip()
    card = memory.ready_card(st, body.worker_id, body.trigger, label)
    st.add_memory(body.worker_id, "context", f"{body.trigger.replace('_', ' ')}: {label or '-'}",
                  {"label": label, "trigger": body.trigger})
    for old in st.memory(body.worker_id, kinds={"context"})[10:]:  # keep the 10 latest contexts only
        st.delete_memory(old["id"])
    return card


# ---------------- identity
@router.get("/whoami")
def whoami(w=Depends(who)):
    """Who the server thinks you are. AUTH_MODE=off: nobody in particular (worker_id is taken from each request)."""
    if w is None: return {"auth": S["auth_mode"], "worker_id": None, "workspace_id": None}
    return {"auth": S["auth_mode"], "worker_id": w["worker_id"], "workspace_id": w["workspace_id"]}
