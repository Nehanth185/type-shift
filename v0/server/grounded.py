"""Answers grounded in the worker's docs: HUD formatting, source lines, guide-step extraction, and the
doc-answer prompt/parse used by real LLM providers. Pure functions, no I/O, so they are easy to test.

HUD contract: at most 3 lines, at most 7 words and HUD_CHARS characters (default 40) per line. The last line of a
doc answer is always its source.
"""
import os, re

from retrieval import SYN, tokens

HUD_LINES, HUD_WORDS = 3, 7
HUD_CHARS = int(os.environ.get("HUD_CHARS", "40"))  # same cap as the v0 loop's llm.clamp_hud
NOT_IN_DOCS_HUD = ["Not in your docs", "I won't guess this one", "Upload the manual ▸"]
NOT_IN_DOCS_FULL = ("I couldn't find this in your team's docs, so I won't guess. "
                    "Upload the manual or procedure that covers it, or check with your lead.")
CANT_READ_HUD = ["Can't read that", "More light or move closer"]
STEP_RE = re.compile(r"^\s*(?:step\s*)?(\d{1,2})\s*[.):]\s+(.+)$", re.I)
WARN_RE = re.compile(r"^\s*(?:[-*>]\s*)?(?:⚠\s*)?(?:\*\*)?\s*(warning|danger|caution)\b\s*[:!-]?\s*(?:\*\*)?\s*(.+)$", re.I)


def _fits(ws, words, chars):
    """How many of the words ws fit on one line of <= words words and <= chars characters (at least 1)."""
    n, length = 0, -1
    for w in ws[:words]:
        if length + 1 + len(w) > chars: break
        length += 1 + len(w); n += 1
    return max(1, n)


def _ellipsize(line, chars):
    """line + '…', dropping trailing words (or characters) so the result is <= chars."""
    line = line.rstrip(".,;: ")
    while len(line) + 1 > chars and " " in line:
        line = line.rsplit(" ", 1)[0].rstrip(".,;: ")
    return line[:chars - 1] + "…"


def wrap(text, max_lines=HUD_LINES, words=HUD_WORDS, chars=None):
    """Wrap into <=max_lines lines of <=words words and <=chars characters, preferring breaks after , . ; : when a
    line is long enough. Cut text is marked with a trailing '…'."""
    chars = chars or HUD_CHARS
    ws = [w if len(w) <= chars else w[:chars - 1] + "…" for w in re.sub(r"\s+", " ", text or "").strip().split(" ") if w]
    lines = []
    while ws:
        take = _fits(ws, words, chars)
        if len(ws) > take:
            brk = [i + 1 for i in range(3, take) if ws[i].endswith((",", ".", ";", ":"))]
            if brk: take = brk[-1]
        lines.append(" ".join(ws[:take])); ws = ws[take:]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ellipsize(lines[-1], chars)
    return lines


def clamp_line(text, words=HUD_WORDS, chars=None):
    """One HUD line: <= words words and <= chars characters; '…' marks a cut."""
    chars = chars or HUD_CHARS
    ws = [w for w in re.sub(r"\s+", " ", text or "").strip().split(" ") if w]
    line = " ".join(ws)
    if len(ws) <= words and len(line) <= chars: return line
    return _ellipsize(" ".join(ws[:_fits(ws, words, chars - 1) if len(ws) > 1 else 1]), chars)


def short_title(title, words=3):
    t = re.sub(r"\(.*?\)|\[.*?\]", "", title or "").strip(" -:")
    return " ".join(t.split()[:words]) or "doc"


def source_line(src):
    return clamp_line(f"Source: {short_title(src['title'])} p.{src['page']}")


def source_of(hit, snippet=None):
    snip = snippet or re.sub(r"\s+", " ", hit["text"]).strip()
    return {"doc_id": hit["doc_id"], "title": hit["title"], "page": hit["page"],
            "snippet": snip[:200] + ("…" if len(snip) > 200 else "")}


def is_heading(line):
    line = line.strip()
    return bool(line) and not re.search(r"[.!?:]$", line) and len(line.split()) <= 9 and not STEP_RE.match(line)


def sentences(text, headings=False):
    text = re.sub(r"\s*\n\s*", "\n", text or "")
    out = []
    for block in text.split("\n"):
        if is_heading(block) and not headings: continue
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9⚠\"(])", block.strip()):
            s = s.strip()
            if len(s.split()) >= 3: out.append(s)
    return out


def _match(term, toks):
    return term in toks or any(v in toks for v in SYN.get(term, ()))


def best_sentences(question, hits, n=2):
    """Pick the sentences that share the most rare question words. Deterministic (ties -> document order)."""
    q = set(tokens(question))
    cands = []
    for hi, h in enumerate(hits[:2]):
        for si, s in enumerate(sentences(h["text"])):
            st = set(tokens(s))
            overlap = sum(1 for t in q if _match(t, st))
            if overlap: cands.append((-overlap, hi, si, s, h))
    cands.sort(key=lambda c: c[:3])
    return [(c[3], c[4]) for c in cands[:n]]


def missing_terms(question, hits):
    """Question words that appear nowhere in the passages (even as a synonym)."""
    toks = set()
    for h in hits: toks |= set(tokens(h["title"] + " " + h["text"]))
    words = re.findall(r"[A-Za-z0-9][\w.-]*", question)
    return [w for w in words if tokens(w) and not any(_match(t, toks) for t in tokens(w))]


def mock_answer(question, hits):
    """Extractive answer: the best-matching sentence from the top passages, verbatim. No model, no guessing.
    If some question words are not in the docs at all (e.g. "torque" when the manual gives none), it says so and
    labels the sentence as the closest match instead of presenting it as the answer."""
    picks = best_sentences(question, hits)
    if not picks:
        ss = sentences(hits[0]["text"]) or [hits[0]["text"][:160]]
        picks = [(ss[0], hits[0])]
    main, h = picks[0]
    src = source_of(h, main)
    miss = missing_terms(question, hits)
    srcs = [src] + [source_of(hh, s) for s, hh in picks[1:] if (hh["doc_id"], hh["page"]) != (h["doc_id"], h["page"])]
    cite = f"(From {h['title']}, page {h['page']}.)"
    if miss:
        hud = [clamp_line(f"No '{miss[0]}' in docs. Closest:")] + wrap(main, max_lines=1) + [source_line(src)]
        full = f"Your docs don't mention {', '.join(repr(m) for m in miss[:3])}, so I won't guess. Closest passage: {main} {cite}"
        return hud, full, srcs
    hud = wrap(main, max_lines=HUD_LINES - 1) + [source_line(src)]
    return hud, " ".join(s for s, _ in picks) + " " + cite, srcs


# ---------------- guide mode: steps are copied from the doc, never written by a model
def extract_steps(pages, start_seq):
    """pages: [{seq,page,text}] of one doc. Find the numbered list on/around page start_seq.
    -> (steps [{n, text, page}], warning or None, first_page) or None."""
    by_seq = {p["seq"]: p for p in pages}

    def items(p):
        its, cur = [], None
        for line in p["text"].split("\n"):
            m = STEP_RE.match(re.sub(r"[*_`]", "", line))
            if m:
                cur = {"n": int(m.group(1)), "text": m.group(2).strip(), "page": p["page"]}
                its.append(cur)
            elif cur and line.startswith(("   ", "\t")) and line.strip():
                cur["text"] += " " + line.strip()
            elif not line.strip():
                cur = None
        return its

    def warning(p):
        for line in p["text"].split("\n"):
            m = WARN_RE.match(line)
            if m: return re.sub(r"[*_`]", "", m.group(2)).strip()
        return None

    allits = []  # every numbered item in the doc, in order, with the page sequence it sits on
    for sq in sorted(by_seq):
        for it in items(by_seq[sq]):
            it["seq"] = sq
            allits.append(it)
    here = [i for i, it in enumerate(allits) if it["seq"] == start_seq]
    if not here: return None
    i = here[0]
    while i > 0 and allits[i]["n"] > 1 and allits[i - 1]["n"] == allits[i]["n"] - 1:
        i -= 1  # this page continues a list that started earlier
    run = [allits[i]]
    for it in allits[i + 1:]:
        if it["n"] != run[-1]["n"] + 1: break
        run.append(it)
    if len(run) < 2: return None
    seqs = range(run[0]["seq"], run[-1]["seq"] + 1)
    warn = next((w for w in (warning(by_seq[q]) for q in seqs if q in by_seq) if w), None)
    # section name: the last heading-looking line above step 1 on its page
    name = None
    for line in by_seq[run[0]["seq"]]["text"].split("\n"):
        if STEP_RE.match(re.sub(r"[*_`]", "", line)) and line.strip().startswith(("1", "Step 1", "step 1", "**1")): break
        l = re.sub(r"^\s*#+\s*|[*_`]", "", line).strip()
        if is_heading(l) and not l.lower().startswith(("fictional", "tools")): name = l
    if name:
        name = re.sub(r"^section\s+[\w.]+\s*[:.-]\s*", "", name, flags=re.I)
        name = re.sub(r"\s*\(.*?\)", "", name).strip() or None
    for it in run:
        it["text"] = re.sub(r"\s+", " ", it["text"]).rstrip()
        it.pop("seq", None)
    return run, warn, run[0]["page"], name


def guide_view(g):
    """g: active guide row -> (answer_hud, answer_full, sources, guide)."""
    i, steps = g["idx"], g["steps"]
    st, n = steps[i], len(steps)
    src = {"doc_id": g["doc_id"], "title": g["title"], "page": st["page"], "snippet": st["text"][:200]}
    hud = []
    if i == 0 and g.get("warning"): hud.append(clamp_line("⚠ " + g["warning"]))
    hud += wrap(f"Step {i + 1}/{n}: {st['text']}", max_lines=HUD_LINES - 1 - len(hud))
    hud.append(source_line(src))
    full = f"Step {i + 1} of {n}: {st['text']}"
    if i == 0 and g.get("warning"): full = f"Warning: {g['warning']} " + full
    if i == n - 1: full += " That's the last step. Say 'done, log it' when finished."
    return hud, full, [src], {"step": i + 1, "total": n, "text": st["text"]}


# ---------------- real LLM: prompt + parse
DOC_SYSTEM = """You are the assistant inside a worker's smart glasses. Answer ONLY from the numbered passages from the worker's own team docs. If the passages do not contain the specific fact or step asked for, your whole reply must be the single word NOT_IN_DOCS, with no HUD, FULL or CITE lines. Do not offer a related passage instead.
If an image is attached it is what the worker sees now; use it only to read labels or gauges, and never guess a number you cannot read clearly (then write HUD: Can't read that).
Never invent a number, torque, dose, setting or step that is not in the passages. Safety warnings in the passages come first.

HUD line 1 must carry the key fact from the passages (the number, limit, part or action). If the question needs a reading the worker has not given, still give the documented value on line 1 and ask for the reading on line 2.

Reply in exactly this format, nothing else:
HUD: <line 1, max 7 words> | <line 2, max 7 words>
FULL: <1 to 3 short spoken sentences, under 50 words>
CITE: <passage numbers you used, e.g. 1,3>"""


def doc_prompt(question, hits, has_image=False):
    blocks = [f"[{i + 1}] {h['title']}, page {h['page']}:\n{h.get('page_text', h['text'])[:1600]}" for i, h in enumerate(hits)]
    # Without this line Nemotron answered text-only questions with "I can't read the gauge from what you showed me".
    photo = ("A photo from the glasses is attached." if has_image else
             "No photo is attached. Do not mention a photo, image or gauge reading; answer from the passages only.")
    return "Passages:\n\n" + "\n\n".join(blocks) + f"\n\n{photo}\nWorker's question: {question}"


# A model that ignores "reply with exactly NOT_IN_DOCS" usually refuses in its own words inside the answer format
# ("HUD: Torque not listed | Check manual"). Measured with Nemotron 3.5 Super VL on 2026-11-12 (REAL_MODEL_RESULTS.md).
REFUSAL_RE = re.compile(
    r"\b(not (?:in|listed|found|given|stated|specified|mentioned|covered|included|provided|documented|described)\b"
    r"|only (?:cover|covers|include|includes|describe|describes|mention|mentions|discuss|discusses)\b"
    r"|(?:does|do|did)(?: not|n't) (?:state|list|say|give|mention|include|contain|specify|cover|provide)"
    r"|no (?:\w+ )?(?:value|torque|information|info|steps?|procedure|data) (?:is |are )?(?:given|listed|provided|stated|found)"
    r"|(?:isn't|is not|aren't|are not) (?:in|covered|listed|given)"
    r"|can't find|cannot find|couldn't find|could not find|no mention)", re.I)


def is_refusal(hud_text, full):
    """True when the reply says the docs lack the answer. Judged on HUD line 1 and the first FULL sentence only,
    so an answer that adds a caveat later ("... torque is not listed") still counts as an answer."""
    first = re.split(r"(?<=[.!?])\s", full.strip(), maxsplit=1)[0] if full else ""
    return bool(REFUSAL_RE.search(hud_text.split("|")[0]) or REFUSAL_RE.search(first))


def parse_doc_reply(text, hits):
    """-> (hud_lines, full, sources) or None when the model says the docs don't have it: NOT_IN_DOCS as the whole
    reply, in HUD line 1, in FULL or as the CITE, or a refusal written in words. A stray NOT_IN_DOCS elsewhere
    (Nemotron once wrote 'HUD: After 7 days of running | NOT_IN_DOCS' with a correct FULL and CITE: 1) is dropped."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S | re.I).replace("**", "").strip()
    marker = re.compile(r"NOT[_ ]IN[_ ]DOCS", re.I)
    if not text or marker.match(text):
        return None
    hud = re.search(r"^\s*HUD\s*:\s*(.+)$", text, re.M | re.I)
    full = re.search(r"^\s*FULL\s*:\s*(.+?)(?=^\s*CITE\s*:|\Z)", text, re.M | re.I | re.S)
    cite = re.search(r"^\s*CITE\s*:\s*(.+)$", text, re.M | re.I)
    if not hud and not full and marker.search(text):
        return None
    full = re.sub(r"\s+", " ", full.group(1)).strip() if full else re.sub(r"\s+", " ", text).strip()
    hud_parts = [p.strip() for p in hud.group(1).split("|")] if hud else []
    cite_txt = cite.group(1) if cite else ""
    if (hud_parts and marker.search(hud_parts[0])) or marker.search(full) or \
            (marker.search(cite_txt) and not re.search(r"\d", cite_txt)):
        return None
    if is_refusal(hud.group(1) if hud else "", full):
        return None
    parts = [p for p in hud_parts if p and not marker.search(p)] or [full]
    lines = []
    for p in parts:
        lines += wrap(p, max_lines=HUD_LINES - 1 - len(lines))
        if len(lines) >= HUD_LINES - 1: break
    nums = [int(x) for x in re.findall(r"\d+", cite.group(1))] if cite else []
    used = [hits[n - 1] for n in dict.fromkeys(nums) if 1 <= n <= len(hits)] or hits[:1]
    srcs = [source_of(h, _supporting_sentence(full, h)) for h in used]
    return lines[:HUD_LINES - 1] + [source_line(srcs[0])], full, srcs


def _supporting_sentence(answer, hit):
    """The sentence on the cited page that best matches the model's answer, shown as the source snippet (the chunk's
    first 200 characters were often the doc's disclaimer header). None -> source_of falls back to the chunk."""
    page = {**hit, "text": hit.get("page_text", hit["text"])}
    picks = best_sentences(answer, [page], n=1)
    return picks[0][0] if picks else None


# ---------------- guard: numbers the docs don't contain
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")  # also digits inside codes: IS7P205 -> 7, 205


def _nums(text):
    return {n.replace(",", ".").lstrip("0") or "0" for n in NUM_RE.findall(text or "")}


def guard_numbers(hud, full, question, hits, has_image):
    """A real model sometimes states a number that is in neither the cited pages nor the question: on 2 of 7
    Nemotron runs it 'read' 60 PSI off a gauge that showed 0 (REAL_MODEL_RESULTS.md). Those numbers come from the
    photo or from nowhere, so they are never shown as if the docs said them: their HUD line is replaced with a
    warning, and the full answer says to check. -> (hud, full, unverified_numbers)"""
    known = _nums(question)
    for h in hits: known |= _nums(h.get("page_text", h["text"])) | _nums(h["title"]) | _nums(str(h["page"]))
    body = hud[:-1] if hud and hud[-1].startswith("Source:") else hud
    bad = sorted((_nums(" ".join(body)) | _nums(full)) - known, key=lambda n: (len(n), n))
    if not bad: return hud, full, []
    warn = "⚠ Photo reading: check it yourself" if has_image else "⚠ Not in docs: verify number"
    new = [clamp_line(warn) if _nums(l) & set(bad) else l for l in body]
    new = list(dict.fromkeys(new))  # one warning line, not two
    tail = hud[len(body):]
    # the warning goes FIRST: the full answer is spoken, and "reads 60 PSI, which is normal" must not be heard as fact
    note = (f"Check this yourself: {', '.join(bad)} is not in your docs" +
            (", it is my reading of the photo and I may be wrong. " if has_image else ". "))
    return new[:HUD_LINES - len(tail)] + tail, note + full, bad
