"""The app model. An app = a manifest (what it is, which intents it handles, which HUD cards it shows, which
permissions it needs) + a handler. The OS gives each handler a Caps object that only opens the doors its manifest
asked for (docs.read, camera, memory.read, memory.write, guide.control); anything else raises PermissionDenied.

Built-in apps wrap features the work API already has:
  ask        docs Q&A with citations              (workapi /ask pipeline: retrieval -> provider.answer_docs -> guard)
  guide      one step at a time from the doc       (workapi.start_guide / _move, grounded.guide_view)
  gauge      a reading, checked against the doc's range; photo readings must be confirmed by the worker
  notes      notes / open loops / forget / "done, log it" (memory.run_command)
  checklist  a PM checklist built from a numbered list in the docs, ticked item by item
  handoff    end-of-shift summary from today's memory (nothing is sent anywhere)
"""
import re, time
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace

from fastapi import HTTPException

import grounded, memory, retrieval, workapi
from . import osdb
from .hud import card

PERMS = {"docs.read", "camera", "memory.read", "memory.write", "guide.control"}


class PermissionDenied(Exception):
    pass


@dataclass
class Manifest:
    id: str
    name: str
    description: str
    intents: list
    cards: list
    permissions: list
    examples: list = field(default_factory=list)   # phrases for the keyword fallback router
    default_intent: str = ""

    def public(self):
        return asdict(self)


@dataclass
class Req:
    text: str
    intent: str
    ctx: object
    worker_id: str
    workspace_id: str
    verbosity: str = "normal"
    match: object = None


class Caps:
    """What one app may touch, for one request."""

    def __init__(self, manifest, req, jpeg=None):
        self.m, self.req, self._jpeg = manifest, req, jpeg
        self.store = workapi.store()

    def need(self, perm):
        if perm not in self.m.permissions:
            raise PermissionDenied(f"app '{self.m.id}' has no '{perm}' permission")

    # docs
    def search(self, q, k=None):
        self.need("docs.read")
        q = doc_query(q, self.req.ctx)
        return retrieval.search(self.store.chunks_for(self.req.workspace_id), q, self.req.ctx.doc_label,
                                k=k or workapi.TOP_K, embedder=workapi.S["embedder"])

    def pages(self, doc_id):
        self.need("docs.read")
        return self.store.pages_for(doc_id)

    # camera
    @property
    def has_photo(self):
        return self._jpeg is not None

    @property
    def photo(self):
        self.need("camera")
        return self._jpeg

    # memory
    def remember(self, kind, text, meta=None):
        self.need("memory.write")
        return self.store.add_memory(self.req.worker_id, kind, text, {"label": self.req.ctx.asset, **(meta or {})})

    def recall(self, kinds=None):
        self.need("memory.read")
        return self.store.memory(self.req.worker_id, kinds=kinds)

    # guide
    def guide(self):
        self.need("guide.control")
        return self.store.get_guide(self.req.worker_id)

    # per-app state (namespaced by app id; no permission needed for an app's own scratch)
    def state(self, key, default=None):
        return osdb.state_get(self.req.worker_id, f"{self.m.id}:{key}", default)

    def set_state(self, key, value):
        osdb.state_set(self.req.worker_id, f"{self.m.id}:{key}", value)


def doc_query(q, ctx):
    """Workers say the asset tag (P-12); docs don't contain it, and an unknown tag sinks retrieval coverage. Drop the
    tag from the question; retrieval still leans toward the asset's model (CP-450) through the context label."""
    if ctx.asset:
        q = re.sub(re.escape(ctx.asset), " ", q, flags=re.I)
    return re.sub(r"\s+", " ", q).strip(" ?,.") or (ctx.model or "")


def res(c, detail="", data=None, signal=None):
    return {"card": c, "speech": c.get("speech", ""), "detail": detail, "data": data or {}, "signal": signal}


def title(name, ctx, extra=""):
    bits = [name] + ([ctx.asset] if ctx.asset else []) + ([extra] if extra else [])
    return " · ".join(bits)


STOPW = set("what whats what's is are the a an of on in for to do does how my this that it at with and or".split())


def topic(q):
    """A short, stable label for a question ('pressure'), so 'Is P-12 pressure normal?' and 'P-12 pressure ok?'
    count as the same habit."""
    for w in re.findall(r"[a-z][a-z0-9-]+", (q or "").lower()):
        if w not in STOPW and len(w) >= 4 and not re.match(r"^[a-z]-?\d", w) and w not in ("normal", "check", "tell", "maximum", "minimum", "much", "many"):
            return w[:-1] if w.endswith("s") and not w.endswith("ss") else w
    return ""


# ====================================================================== Ask
def ask_app(req, caps):
    q, ctx, st = req.text.strip(), req.ctx, caps.store
    t0 = time.time()
    if not q:
        if not caps.has_photo:
            return res(card("ask", title("Ask", ctx), ["Ask anything about your job", "Your docs answer, with source"],
                            "Ask me anything about the job.", kind="idle"))
        body = SimpleNamespace(worker_id=req.worker_id, workspace_id=req.workspace_id)
        o = workapi.photo_only(t0, st, body, caps.photo, ctx.asset)
        return res(card("ask", title("Ask", ctx), o["answer_hud"], o["answer_full"], ungrounded=True),
                   detail="photo")
    hits = caps.search(q)
    prov = workapi.S["provider"]
    if not hits:
        caps.remember("ask", f"Q: {q} (not in docs)", {"question": q, "hud": grounded.NOT_IN_DOCS_HUD, "not_in_docs": True})
        return res(card("ask", title("Ask", ctx), grounded.NOT_IN_DOCS_HUD, grounded.NOT_IN_DOCS_FULL),
                   detail=topic(q), data={"not_in_docs": True, "sources": []})
    llm_hits = workapi.page_context(st, hits) if prov.name != "mock" else None
    jpeg = caps.photo if caps.has_photo else None
    try:
        r = prov.answer_docs(doc_query(q, ctx), llm_hits or hits, jpeg)
    except Exception as e:  # noqa: BLE001
        return res(card("ask", title("Ask", ctx), ["AI service error", "Try again in a moment"], "The AI service failed."),
                   detail=topic(q), data={"error": str(e)[:200]})
    if r is None:
        caps.remember("ask", f"Q: {q} (not in docs)", {"question": q, "hud": grounded.NOT_IN_DOCS_HUD, "not_in_docs": True})
        return res(card("ask", title("Ask", ctx), grounded.NOT_IN_DOCS_HUD, grounded.NOT_IN_DOCS_FULL),
                   detail=topic(q), data={"not_in_docs": True, "sources": []})
    hud, full, srcs = r
    unverified = []
    if prov.name != "mock":
        hud, full, unverified = grounded.guard_numbers(hud, full, q, llm_hits or hits, bool(jpeg))
    caps.remember("ask", f"Q: {q} → {' / '.join(hud)}", {"question": q, "hud": hud, "not_in_docs": False,
                                                          "sources": [f"{s['title']} p.{s['page']}" for s in srcs]})
    guide_ok = False
    if srcs:
        pages = st.pages_for(srcs[0]["doc_id"])
        seq = next((p["seq"] for p in pages if p["page"] == srcs[0]["page"]), None)
        guide_ok = seq is not None and bool(grounded.extract_steps(pages, seq))
    lines, speech = list(hud), full
    has_src = bool(lines) and lines[-1].startswith("Source:")
    if req.verbosity == "short" and len(lines) > 2 and has_src:
        lines = [lines[0], lines[-1]]                       # one line + source
        speech = re.split(r"(?<=[.!?])\s", full, maxsplit=1)[0]
    elif req.verbosity == "steps" and guide_ok:
        speech = full + " Say 'walk me through it' for the steps."
    return res(card("ask", title("Ask", ctx), lines, speech, sources=srcs),
               detail=topic(q), data={"sources": srcs, "guide_available": guide_ok, "unverified_numbers": unverified})


# ====================================================================== Guide
def _guide_card(g, ctx, finished=False):
    hud, full, srcs, gv = grounded.guide_view(g)
    if finished:
        n = len(g["steps"])
        hud, full = [f"All {n} steps done", "Say 'done, log it'", hud[-1]], "That was the last step. Say 'done, log it'."
    return card("guide", title("Guide", ctx, f"{gv['step']}/{gv['total']}"), hud, full, sources=srcs, guide=gv), gv


def guide_app(req, caps):
    ctx, st = req.ctx, caps.store
    if req.intent in ("guide.next", "guide.prev"):
        caps.need("guide.control")
        try:
            o = workapi._move(req.worker_id, +1 if req.intent == "guide.next" else -1, None)
        except HTTPException:
            return res(card("guide", title("Guide", ctx), ["No guide running", "Say 'how do I …' to start"],
                            "No guide is running. Ask how to do something to start one.", kind="idle"))
        g = caps.guide()
        c, gv = _guide_card(g, ctx, finished=bool(o.get("finished")))
        return res(c, detail=f"step {gv['step']}", data={"guide": gv, "finished": bool(o.get("finished"))})
    if req.intent == "guide.goto":
        n = int(req.match.group(1)) if req.match else 1
        g = caps.guide()
        if g:
            g["idx"] = max(0, min(len(g["steps"]) - 1, n - 1))
            st.set_guide_idx(req.worker_id, g["idx"])
            if g["mem_id"] and (m := st.get_memory(g["mem_id"])):
                st.update_memory(m["id"], meta={**m["meta"], "step": g["idx"] + 1})
            c, gv = _guide_card(g, ctx)
            return res(c, detail=f"step {gv['step']}", data={"guide": gv})
        # no guide yet: start one for this asset, then jump
        req.text = req.text or ctx.doc_label
    if req.intent == "guide.idle" and not req.text.strip():
        g = caps.guide()
        if g:
            c, gv = _guide_card(g, ctx)
            return res(c, detail=f"step {gv['step']}", data={"guide": gv})
        return res(card("guide", title("Guide", ctx), ["Say 'how do I …'", "I'll go one step at a time"],
                        "Ask how to do a job and I'll walk you through it from your docs.", kind="idle"))
    caps.need("guide.control"); caps.need("memory.write")
    q = re.sub(r"\bstep\s*\d+\b", " ", req.text, flags=re.I).strip() or ctx.doc_label or req.text
    hits = caps.search(q)
    g = workapi.start_guide(SimpleNamespace(worker_id=req.worker_id, workspace_id=req.workspace_id), hits, ctx.asset) \
        if hits else None
    if not g:  # no numbered procedure: answer it as a question instead of a dead end
        r = run("ask", Req(req.text, "ask.question", ctx, req.worker_id, req.workspace_id, req.verbosity),
                caps._jpeg if caps.has_photo else None)
        r["data"]["fell_back_from"] = "guide"
        r["card"]["speech"] = "No numbered steps for that in your docs. " + r["card"]["speech"]
        r["speech"] = r["card"]["speech"]
        r["app"] = "ask"
        return r
    if req.intent == "guide.goto" and req.match:
        g["idx"] = max(0, min(len(g["steps"]) - 1, int(req.match.group(1)) - 1))
        st.set_guide_idx(req.worker_id, g["idx"])
    c, gv = _guide_card(g, ctx)
    name = (st.get_memory(g["mem_id"]) or {}).get("meta", {}).get("title") if g["mem_id"] else None
    detail = f"step {gv['step']}" if req.intent == "guide.goto" else grounded.short_title(name or g["title"], 3)
    return res(c, detail=detail, data={"guide": gv})


# ====================================================================== Gauge
RANGE_RE = re.compile(r"(?:normal|operating|rated)[^.\n]*?(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*"
                      r"(psi|bar|kpa|°?c|°?f|rpm|a|v)\b", re.I)
MAX_RE = re.compile(r"(?:maximum|max\.?)\s+([a-z ]{0,30}?)\s*(?:is|:)?\s*(\d+(?:\.\d+)?)\s*(psi|bar|kpa|°?c|°?f|rpm)\b", re.I)
READ_RE = re.compile(r"(?:reads?|reading(?: is)?|is at|at|shows?|=)?\s*(\d+(?:\.\d+)?)\s*(psi|bar|kpa|°?c|°?f|rpm|degrees)?",
                     re.I)
QUANTITY = {"temp": "temperature", "temperature": "temperature", "hot": "temperature", "pressure": "pressure",
            "psi": "pressure", "bar": "pressure"}


def spoken_value(text, asset=""):
    """The reading in what the worker said: 'P-12 reads 62 psi' -> 62. Asset tags and codes (P-12, WO-881, SK-450)
    are removed first so their digits are never taken as a reading."""
    t = re.sub(re.escape(asset), " ", text, flags=re.I) if asset else text
    t = re.sub(r"\b[A-Za-z]{1,4}-?\d+[A-Za-z]?\b", " ", t)
    m = READ_RE.search(t)
    return float(m.group(1)) if m and m.group(1) else None


def quantity_of(text):
    """pressure | temperature | None (unknown: then no range is applied, a reading is only saved)."""
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if w in QUANTITY: return QUANTITY[w]
    if re.search(r"\d\s*(?:°\s?[cf]|degrees)\b", text or "", re.I): return "temperature"
    return None


def spoken_unit(text):
    m = re.search(r"\d\s*(psi|bar|kpa|°\s?[cf]|degrees)\b", text or "", re.I)
    return m.group(1).upper().replace("°", "").replace(" ", "") if m else ""


def doc_range(caps, quantity):
    """The documented normal range for this asset from the docs: {lo, hi, unit, sentence, src, low_tip, high_tip}."""
    if not quantity:
        return None
    label = (caps.req.ctx.doc_label or "").lower()
    if not label:
        return None   # ranges are machine-specific: no machine in context, no range
    hits = caps.search(f"normal {quantity}") or caps.search(quantity)
    for h in hits:
        if label not in (h["title"] + " " + h["text"]).lower():
            continue  # this passage is about another machine
        for p in caps.pages(h["doc_id"]):
            if p["page"] != h["page"]: continue
            text = p["text"]
            for line in text.split("\n"):
                m = RANGE_RE.search(line)
                if m and (quantity != "temperature" or m.group(3).lower().endswith(("c", "f"))):
                    unit = m.group(3).upper().replace("°", "")
                    lo, hi = float(m.group(1)), float(m.group(2))
                    tips = {}
                    for l2 in text.split("\n"):
                        t = re.match(r"\s*(below|under|above|over)\s+(\d+(?:\.\d+)?)\s*\w*\s*[:,-]\s*(.+)", l2, re.I)
                        if t: tips["low" if t.group(1).lower() in ("below", "under") else "high"] = (float(t.group(2)), t.group(3).strip())
                    return {"lo": lo, "hi": hi, "unit": unit, "sentence": line.strip(), "tips": tips,
                            "src": grounded.source_of(h, line.strip())}
                m = MAX_RE.search(line)
                if m and quantity == "temperature":
                    return {"lo": None, "hi": float(m.group(2)), "unit": m.group(3).upper().replace("°", ""),
                            "sentence": line.strip(), "tips": {}, "src": grounded.source_of(h, line.strip())}
    return None


def fmt(v):
    return str(int(v)) if float(v).is_integer() else str(v)


def judge(v, rng):
    lo, hi, unit = rng["lo"], rng["hi"], rng["unit"]
    span = f"{fmt(lo)}-{fmt(hi)}" if lo is not None else f"max {fmt(hi)}"
    if lo is not None and v < lo:
        tip = rng["tips"].get("low")
        return "low", [f"⚠ {fmt(v)} {unit} LOW (normal {span})", tip[1] if tip and v < tip[0] else "Check before running"]
    if v > hi:
        tip = rng["tips"].get("high")
        return "high", [f"⚠ {fmt(v)} {unit} HIGH (normal {span})", tip[1] if tip and v > tip[0] else "Check before running"]
    return "ok", [f"{fmt(v)} {unit}: in range ({span})"]


def gauge_app(req, caps):
    ctx, text = req.ctx, req.text
    q = quantity_of(text)
    if req.intent == "gauge.reject":
        caps.set_state("pending", None)
        return res(card("gauge", title("Gauge", ctx), ["Reading discarded", "Say it: 'reads 62'"], "Discarded. Say the reading."))
    pending = caps.state("pending")
    value, source = None, "spoken"
    if req.intent == "gauge.confirm" and pending:
        value, q, source = pending["value"], pending["quantity"], "photo, confirmed by you"
        caps.set_state("pending", None)
    else:
        value = spoken_value(text, ctx.asset)
    rng = doc_range(caps, q)
    if rng and spoken_unit(text) and spoken_unit(text) not in (rng["unit"], "DEGREES"):
        rng = None   # said bar, docs give PSI: don't convert silently, don't judge
    if value is None and caps.has_photo:
        jpeg = caps.photo
        prov = workapi.S["provider"]
        hits = caps.search(f"normal {q or 'reading'}") or []
        try:
            r = prov.answer_docs(doc_query(text or f"What does the {q or ''} gauge read? Is it normal?", ctx), hits, jpeg) if hits else None
        except Exception:  # noqa: BLE001
            r = None
        if r and r[0] == grounded.CANT_READ_HUD:
            return res(card("gauge", title("Gauge", ctx), grounded.CANT_READ_HUD, "I can't read that. More light or move closer."),
                       detail=q)
        v = None
        if r and prov.name != "mock":  # the mock can't read photos; never invent a reading for it
            v = spoken_value(" ".join(l for l in r[0] if not l.startswith("Source:")), ctx.asset)
            if v is None: v = spoken_value(r[1], ctx.asset)
        if v is not None:  # a number read off the photo: never shown as fact or saved until the worker confirms it
            caps.set_state("pending", {"value": v, "quantity": q or "pressure"})
            unit = rng["unit"] if rng else ""
            return res(card("gauge", title("Gauge", ctx), [f"Reads {fmt(v)} {unit}?".replace(" ?", "?"),
                                                            "Say 'yes' or say the reading", "⚠ Photo reading: not verified"],
                            f"I think it reads {fmt(v)}. Say yes to confirm, or tell me the reading."),
                       detail=q, data={"needs_confirm": True, "photo_value": v})
        lines = ["No number I can trust", "Say it: 'reads 62'"]
        if rng: lines.append(f"Normal {fmt(rng['lo'])}-{fmt(rng['hi'])} {rng['unit']}" if rng["lo"] is not None else f"Max {fmt(rng['hi'])} {rng['unit']}")
        return res(card("gauge", title("Gauge", ctx), lines, "I couldn't read a number I trust. Tell me the reading."), detail=q)
    if value is None:
        lines = ["Say the reading or send a photo"]
        if rng:
            lines.append(f"Normal {fmt(rng['lo'])}-{fmt(rng['hi'])} {rng['unit']}" if rng["lo"] is not None else f"Max {fmt(rng['hi'])} {rng['unit']}")
            lines.append(grounded.source_line(rng["src"]))
        return res(card("gauge", title("Gauge", ctx), lines, "Tell me the reading.", kind="idle"), detail=q,
                   data={"range": rng and {k: rng[k] for k in ("lo", "hi", "unit")}})
    if not rng:
        q = q or "reading"
        caps.remember("note", f"Reading {ctx.asset or ''} {q} {fmt(value)} (no range in docs)".replace("  ", " "),
                      {"done": True, "reading": value, "quantity": q})
        return res(card("gauge", title("Gauge", ctx), [f"Saved: {q} {fmt(value)}", "No range for this in docs", "I won't judge it"],
                        f"Saved {fmt(value)}. Your docs don't give a normal range, so I won't say if it's OK."),
                   detail=q, data={"value": value, "status": "unknown"})
    status, lines = judge(value, rng)
    caps.remember("note", f"Reading {ctx.asset or ''} {q} {fmt(value)} {rng['unit']} ({status})".replace("  ", " "),
                  {"done": True, "reading": value, "quantity": q, "status": status, "via": source})
    if req.verbosity == "short" and status == "ok":
        lines = lines[:1]
    lines = lines[:2] + [grounded.source_line(rng["src"])]
    speech = (f"{fmt(value)} {rng['unit']} is {'within' if status == 'ok' else 'outside'} the normal range "
              f"from your docs: {rng['sentence']}") + (f" Reading source: {source}." if source != "spoken" else "")
    return res(card("gauge", title("Gauge", ctx), lines, speech, sources=[rng["src"]]),
               detail=q, data={"value": value, "status": status, "unit": rng["unit"], "via": source,
                               "range": {"lo": rng["lo"], "hi": rng["hi"]}})


# ====================================================================== Notes
def notes_app(req, caps):
    ctx = req.ctx
    if req.intent in ("notes.list", "notes.idle"):
        open_ = [i for i in caps.recall({"note"}) if not i["meta"].get("done")]
        lines = [f"Open: {i['text']}" for i in open_[:2]] or ["No open loops", "Say 'note: …' to add one"]
        return res(card("notes", f"Notes · {len(open_)} open", lines,
                        "; ".join(i["text"] for i in open_) or "You have no open notes.", kind="idle"),
                   detail="open loops", data={"open": [i["text"] for i in open_]})
    name = req.intent.split(".", 1)[1]
    caps.need("memory.write")
    arg = req.match[1] if req.match and isinstance(req.match, tuple) else None
    hud, full = memory.run_command(caps.store, req.worker_id, req.workspace_id, name, arg, ctx.asset)
    return res(card("notes", title("Notes", ctx), hud, full), detail=name)


# ====================================================================== Checklist
CHECK_WORDS = re.compile(r"\b(pm|checklist|check list|preventive|preventative|maintenance|inspection|round|run|start|open|"
                         r"do|the|for|my|a|on|please)\b", re.I)


def _check_card(cl, ctx):
    items, done = cl["items"], cl["done"]
    n, k = len(items), sum(done)
    if k == n:
        return card("checklist", f"Checklist · {n}/{n} done ✓", [f"All {n} items checked", "Logged to your memory",
                    grounded.source_line(cl["src"])], f"All {n} items are checked. I logged it.", sources=[cl["src"]])
    nxt = [i for i, d in enumerate(done) if not d]
    lines = [f"☐ {nxt[0] + 1}. {items[nxt[0]]['text']}"]
    if len(nxt) > 1: lines.append(f"☐ {nxt[1] + 1}. {items[nxt[1]]['text']}")
    lines.append(grounded.source_line(cl["src"]))
    return card("checklist", title("Checklist", ctx, f"{k}/{n}"), lines,
                f"Item {nxt[0] + 1} of {n}: {items[nxt[0]]['text']}. Say 'check' when it's done.", sources=[cl["src"]])


def checklist_app(req, caps):
    ctx = req.ctx
    cl = caps.state("active")
    if req.intent == "checklist.tick":
        if not cl:
            return res(card("checklist", "Checklist", ["No checklist open", "Say 'PM checklist'"], "No checklist is open.", kind="idle"))
        nxt = [i for i, d in enumerate(cl["done"]) if not d]
        if nxt: cl["done"][nxt[0]] = True
        if all(cl["done"]):
            caps.remember("job_done", f"Checklist done: {cl['name']}" + (f" on {ctx.asset}" if ctx.asset else ""),
                          {"title": cl["name"]})
            caps.set_state("active", None)
        else:
            caps.set_state("active", cl)
        return res(_check_card(cl, ctx), detail="tick", data={"done": sum(cl["done"]), "total": len(cl["items"])})
    if req.intent == "checklist.idle" and cl:
        return res(_check_card(cl, ctx), detail="PM")
    q = CHECK_WORDS.sub(" ", req.text)
    q = re.sub(r"\s+", " ", q).strip(" ,.?") or ctx.doc_label
    if not q:
        return res(card("checklist", "Checklist", ["Which machine?", "Look at its tag or say it"], "Which machine?", kind="idle"))
    for h in caps.search(q):
        pages = caps.pages(h["doc_id"])
        seq = next((p["seq"] for p in pages if p["page"] == h["page"]), None)
        found = grounded.extract_steps(pages, seq) if seq is not None else None
        if found:
            steps, _, _, name = found
            cl = {"items": [{"text": s["text"], "page": s["page"]} for s in steps], "done": [False] * len(steps),
                  "name": name or grounded.short_title(h["title"]), "src": {**grounded.source_of(h, steps[0]["text"]), "page": steps[0]["page"]}}
            caps.set_state("active", cl)
            return res(_check_card(cl, ctx), detail="PM", data={"total": len(steps), "name": cl["name"]})
    return res(card("checklist", title("Checklist", ctx), ["No checklist in your docs", "Upload the PM sheet"],
                    "I didn't find a numbered checklist for that in your docs."), detail="PM")


# ====================================================================== Handoff
def handoff_app(req, caps):
    ctx = req.ctx
    since = time.time() - (ctx.shift_minute * 60 + 600 if ctx.shift_minute is not None else 12 * 3600)
    items = [i for i in caps.recall() if i["ts"] >= since]
    jobs = [i["text"] for i in items if i["kind"] == "job_done"]
    open_notes = [i["text"] for i in caps.store.memory(req.worker_id, kinds={"note"})
                  if not i["meta"].get("done")]
    guides = [f"{i['meta'].get('title', 'guide')} at step {i['meta'].get('step', 1)}/{i['meta'].get('total', '?')}"
              + (f" on {i['meta']['label']}" if i["meta"].get("label") else "")
              for i in items if i["kind"] == "guide" and not i["meta"].get("done")]
    readings = [i["text"] for i in items if i["kind"] == "note" and i["meta"].get("reading") is not None]
    gaps = [i["meta"].get("question") for i in items if i["kind"] == "ask" and i["meta"].get("not_in_docs") and i["meta"].get("question")]
    lines = [f"Done: {jobs[0].replace('Done: ', '')}" if jobs else "No jobs logged today"]
    if open_notes or guides: lines.append(f"Open: {(guides + open_notes)[0]}")
    flagged = [r for r in readings if r.endswith(("(low)", "(high)"))]
    if flagged: lines.append(flagged[0])
    elif readings: lines.append(readings[0])
    parts = [f"{len(jobs)} jobs done" + (": " + "; ".join(jobs) if jobs else ""),
             f"{len(guides)} unfinished guides" + (": " + "; ".join(guides) if guides else ""),
             f"{len(open_notes)} open notes" + (": " + "; ".join(open_notes) if open_notes else ""),
             f"{len(readings)} readings" + (": " + "; ".join(readings) if readings else "")]
    if gaps: parts.append(f"{len(gaps)} questions your docs couldn't answer: " + "; ".join(gaps))
    summary = ". ".join(parts) + "."
    return res(card("handoff", f"Handoff · {len(jobs)} done · {len(open_notes) + len(guides)} open", lines,
                    summary + " Nothing is sent; you choose who sees this."),
               detail="", data={"summary": summary, "jobs": jobs, "open": guides + open_notes, "readings": readings,
                                "doc_gaps": gaps, "shared": False})


# ====================================================================== registry
APPS = {}


def register(manifest, handler):
    bad = set(manifest.permissions) - PERMS
    if bad: raise ValueError(f"unknown permissions {bad}")
    APPS[manifest.id] = (manifest, handler)


register(Manifest("ask", "Ask", "Ask anything about your job; answers come from your team's docs with the source.",
                  ["ask.question", "ask.photo"], ["answer", "not_in_docs"], ["docs.read", "camera", "memory.write"],
                  ["what is", "why", "spec", "limit", "manual", "where"], "ask.question"), ask_app)
register(Manifest("guide", "Guide", "One step at a time from the procedure in your docs. Next / back / step N.",
                  ["guide.start", "guide.next", "guide.prev", "guide.goto", "guide.idle"], ["step"],
                  ["docs.read", "guide.control", "memory.write"],
                  ["walk me through", "procedure", "next step", "steps", "sop"], "guide.idle"),
         guide_app)
register(Manifest("gauge", "Gauge", "Say or photograph a reading; checked against the range in your docs. "
                  "Photo readings need your confirmation.", ["gauge.read", "gauge.photo", "gauge.confirm", "gauge.reject"],
                  ["reading", "confirm"], ["camera", "docs.read", "memory.write"],
                  ["gauge", "reading", "meter"], "gauge.read"), gauge_app)
register(Manifest("notes", "Notes", "Notes and open loops; 'done, log it'; 'forget that'; 'what do you know about me'.",
                  ["notes.note", "notes.done", "notes.done_cancel", "notes.forget", "notes.about", "notes.list", "notes.idle"], ["note", "list"],
                  ["memory.read", "memory.write"], ["remind me", "open loops", "my notes", "notes"], "notes.idle"),
         notes_app)
register(Manifest("checklist", "Checklist", "A PM checklist built from a numbered list in your docs; say 'check' per item.",
                  ["checklist.start", "checklist.tick", "checklist.idle"], ["checklist"], ["docs.read", "memory.write"],
                  ["checklist", "inspection", "preventive maintenance", "check off"], "checklist.idle"), checklist_app)
register(Manifest("handoff", "Handoff", "End-of-shift summary from today's memory. Nothing is sent anywhere.",
                  ["handoff.summary"], ["summary"], ["memory.read"],
                  ["handoff", "end of shift", "shift summary", "wrap up"], "handoff.summary"), handoff_app)


def run(app_id, req, jpeg=None):
    m, h = APPS[app_id]
    return h(req, Caps(m, req, jpeg))


from . import nav_app  # noqa: E402,F401  (registers the Navigator app, G46; see NAVIGATOR.md)
from . import ear_app  # noqa: E402,F401  (registers the Machine Ear app; see MACHINE_EAR.md)
