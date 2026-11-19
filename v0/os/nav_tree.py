"""Fault trees for the Navigator: load, validate, review gate, and draft extraction.

Format `worker-os/fault-tree@1` (JSON, one file per machine model, in os/trees/):
  symptoms[]  -> causes[] (prior, expect{check_id: outcome}, fix)  -> checks[] (hud, kind yesno|reading, spec, page)
Every symptom, cause and check names the doc + page it came from and a `quote` copied from that page. The validator
re-reads the page and refuses the tree if a quote or a spec number is not on it.

Review gate: a tree is only loaded for diagnosis when it says `"reviewed": true` (set by a person after reading it
against the manual). Drafts (by the table parser or an LLM) are written with `"reviewed": false` into trees/drafts/
and are never loaded by the engine.
"""
import glob, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
TREES = os.path.join(HERE, "trees")
FORMAT = "worker-os/fault-tree@1"
HUD_CHARS, HUD_WORDS = 40, 7
OUTCOMES = {"yesno": ("ok", "bad"), "reading": ("ok", "low", "high")}
NEEDS = {"any", "run", "loto"}


class TreeError(ValueError):
    pass


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def doc_pages(path):
    """{page_label: text} for a Markdown doc with <!-- page: N --> marks (same rule as server/ingest.py)."""
    import ingest
    text = open(path, encoding="utf-8").read()
    return {p: t for p, t in ingest._split_text_pages(text)}


def fmt(v):
    return str(int(v)) if float(v).is_integer() else str(v)


def validate(tree, base=TREES):
    """-> list of problems (empty = valid). Checks structure, HUD limits, and that every quote and spec number is on
    the cited page of the cited doc."""
    bad = []
    if tree.get("format") != FORMAT: bad.append(f"format must be {FORMAT}")
    if not tree.get("models"): bad.append("no models")
    docs = {}
    for key, d in (tree.get("docs") or {}).items():
        p = os.path.normpath(os.path.join(base, d.get("file", "")))
        if not os.path.isfile(p): bad.append(f"doc {key}: file missing {d.get('file')}"); continue
        docs[key] = {_k: _norm(v) for _k, v in doc_pages(p).items()}

    def cited(where, obj, extra_numbers=()):
        dk, pg, q = obj.get("doc"), str(obj.get("page", "")), obj.get("quote", "")
        if dk not in docs: bad.append(f"{where}: unknown doc {dk!r}"); return
        if pg not in docs[dk]: bad.append(f"{where}: page {pg} not in doc {dk}"); return
        if not q or _norm(q) not in docs[dk][pg]: bad.append(f"{where}: quote not found on p.{pg}: {q!r}")
        for n in extra_numbers:
            if fmt(n) not in docs[dk][pg]: bad.append(f"{where}: spec number {fmt(n)} not on p.{pg}")

    def hud_ok(where, s):
        if len(s) > HUD_CHARS or len(s.split()) > HUD_WORDS: bad.append(f"{where}: HUD text too long {s!r}")

    checks = {}
    for c in tree.get("checks", []):
        cid = c.get("id")
        if not cid or cid in checks: bad.append(f"check id missing/duplicate {cid!r}"); continue
        checks[cid] = c
        if c.get("kind") not in OUTCOMES: bad.append(f"check {cid}: kind must be yesno|reading")
        if c.get("needs", "any") not in NEEDS: bad.append(f"check {cid}: needs must be any|run|loto")
        hud_ok(f"check {cid}", c.get("hud", ""))
        if not isinstance(c.get("minutes"), (int, float)) or c["minutes"] <= 0: bad.append(f"check {cid}: minutes > 0")
        if c.get("kind") == "yesno" and c.get("ok_if") not in ("yes", "no"): bad.append(f"check {cid}: ok_if yes|no")
        nums = ()
        if c.get("kind") == "reading":
            s = c.get("spec") or {}
            if not ({"lo", "hi", "unit"} <= set(s)) or s["lo"] > s["hi"]: bad.append(f"check {cid}: spec needs lo<=hi+unit")
            else: nums = (s["lo"], s["hi"])
        cited(f"check {cid}", c, nums)
    for gid, g in (tree.get("gates") or {}).items():
        cited(f"gate {gid}", g)
        for l in g.get("hud", []): hud_ok(f"gate {gid}", l)
    for n in ("loto", "run"):
        if any(c.get("needs") == n for c in checks.values()) and n not in (tree.get("gates") or {}):
            bad.append(f"checks need '{n}' but there is no '{n}' gate")
    seen = set()
    for s in tree.get("symptoms", []):
        sid = s.get("id")
        if not sid or sid in seen: bad.append(f"symptom id missing/duplicate {sid!r}")
        seen.add(sid)
        cited(f"symptom {sid}", s)
        if not s.get("phrases"): bad.append(f"symptom {sid}: no phrases")
        if not s.get("causes"): bad.append(f"symptom {sid}: no causes")
        for c in s.get("causes", []):
            w = f"cause {sid}/{c.get('id')}"
            cited(w, c)
            hud_ok(w + " name", c.get("name", "")); hud_ok(w + " fix", "Fix: " + c.get("fix", ""))
            if not 0 < float(c.get("prior", 0)) <= 1: bad.append(f"{w}: prior in (0,1]")
            if not c.get("expect"): bad.append(f"{w}: expects no check result (can't be told apart)")
            for k, o in (c.get("expect") or {}).items():
                if k not in checks: bad.append(f"{w}: unknown check {k}"); continue
                allowed = set(OUTCOMES[checks[k]["kind"]]) - {"ok"} | ({"out"} if checks[k]["kind"] == "reading" else set())
                if o not in allowed: bad.append(f"{w}: outcome {o!r} not allowed for {k}")
    return bad


def load_file(path, require_reviewed=True):
    tree = json.load(open(path, encoding="utf-8"))
    if require_reviewed and tree.get("reviewed") is not True:
        raise TreeError(f"{os.path.basename(path)}: not reviewed (set \"reviewed\": true after a person checks it)")
    probs = validate(tree, os.path.dirname(path))
    if probs:
        raise TreeError(f"{os.path.basename(path)}: " + "; ".join(probs[:5]))
    tree["_path"] = path
    tree["_checks"] = {c["id"]: c for c in tree["checks"]}
    return tree


_CACHE = {}


def load_all(folder=TREES):
    """Reviewed, valid trees in `folder` (not drafts/). -> ({model_lower: tree}, {file: reason it was skipped})"""
    if folder in _CACHE: return _CACHE[folder]
    trees, skipped = {}, {}
    for p in sorted(glob.glob(os.path.join(folder, "*.json"))):
        try:
            t = load_file(p)
        except (TreeError, ValueError, KeyError) as e:
            skipped[os.path.basename(p)] = str(e); continue
        for m in t["models"]: trees[m.lower()] = t
    _CACHE[folder] = (trees, skipped)
    return _CACHE[folder]


def for_model(model, folder=TREES):
    return load_all(folder)[0].get((model or "").lower())


# ------------------------------------------------------------------ drafting (never loaded until reviewed)
def parse_x_matrix(layout_text):
    """Draft extractor for the common 'symptom rows x cause columns, X marks' troubleshooting table (pdftotext
    -layout output). Deterministic, no model. Returns {"groups": [...], "symptoms": [{name, causes: [col ids]}],
    "causes": {col_id: text}}. Column letters repeat per group (Electrical A-I, Mechanical A-F, ...), so a column is
    'Group:Letter'."""
    lines = layout_text.split("\n")
    hdr_i = next((i for i, l in enumerate(lines) if re.match(r"^\s*Symptom\s+([A-Z]\s+){3,}", l)), None)
    if hdr_i is None: return None
    hdr = lines[hdr_i]
    cols = [(m.start(), m.group()) for m in re.finditer(r"\b[A-Z]\b", hdr)]
    group_line = lines[hdr_i - 1] if hdr_i else ""
    groups = [(m.start(), m.group()) for m in re.finditer(r"[A-Z][a-z]+", group_line)]
    def group_of(x):
        g = [name for pos, name in groups if pos <= x + 12]
        return g[-1] if g else (groups[0][1] if groups else "")
    # group boundaries: a letter sequence restarts at 'A'
    names, gi = [], -1
    for pos, letter in cols:
        if letter == "A": gi += 1
        gname = [n for _, n in groups][gi] if gi < len(groups) else f"G{gi}"
        names.append((pos, f"{gname}:{letter}"))
    symptoms = []
    for l in lines[hdr_i + 1:]:
        if not l.strip(): break
        m = re.match(r"^\s*(\S.*?)\s{2,}", l)
        if not m or "X" not in l[m.end() - 1:]: break
        xs = [x.start() for x in re.finditer(r"X", l[m.end():])]
        got = []
        for x in xs:
            x += m.end()
            col = min(names, key=lambda n: abs(n[0] - x))
            got.append(col[1])
        symptoms.append({"name": m.group(1).strip(), "causes": got})
    causes, cur = {}, None
    for l in lines[hdr_i + len(symptoms) + 1:]:
        g = re.match(r"^\s*(Electrical|Mechanical|System|Hydraulic)\s*$", l)
        if g: cur = g.group(1); continue
        c = re.match(r"^\s*([A-I])\.\s+(.+?)(?:\s{2,}(.+))?$", l)
        if c and cur:
            clean = lambda x: re.sub(r"[\x00-\x1f]", "", x or "").strip()
            causes[f"{cur}:{c.group(1)}"] = {"cause": clean(c.group(2)), "action": clean(c.group(3))}
    return {"groups": [n for _, n in groups], "symptoms": symptoms, "causes": causes}


LAST_ERROR = ""
LLM_PROMPT = """You are drafting a troubleshooting tree from ONE manual page for a human reviewer.
Use only what is written on the page. Output JSON only:
{"symptoms":[{"name":...,"quote":"<exact words from the page>","causes":[{"name":...,"fix":...,"quote":"<exact words>"}]}]}
If the page has no troubleshooting table, output {"symptoms":[]}.
PAGE:
"""


def llm_draft(provider, page_text):
    """Optional LLM draft (real model only, e.g. NVIDIA NIM). Returns parsed JSON or None. The result is a DRAFT:
    it is never loaded by the engine until a person sets reviewed=true; quotes are checked against the page."""
    if provider is None or getattr(provider, "name", "mock") == "mock" or not hasattr(provider, "_post"):
        return None
    from llm import message_text, strip_think
    body = {"model": getattr(provider, "model", ""), "messages": [{"role": "user", "content": LLM_PROMPT + page_text[:12000]}],
            "max_tokens": 8000, "temperature": 0, "stream": False, **(getattr(provider, "prof", {}) or {}).get("extra", {})}
    if hasattr(provider, "timeout"): provider.timeout = max(provider.timeout or 0, 100)  # offline drafting, not a HUD call
    global LAST_ERROR
    try:
        raw = strip_think(message_text(provider._post(body)))
    except Exception as e:  # noqa: BLE001
        LAST_ERROR = repr(e)[:300]
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        out = json.loads(m.group()) if m else None
    except ValueError as e:
        LAST_ERROR = f"not JSON: {e}; tail={raw[-120:]!r}"
        return None
    if not out: return None
    page = _norm(page_text)
    for s in out.get("symptoms", []):  # mark every quote the page doesn't contain, so the reviewer sees it
        s["quote_on_page"] = _norm(s.get("quote")) in page if s.get("quote") else False
        for c in s.get("causes", []):
            c["quote_on_page"] = _norm(c.get("quote")) in page if c.get("quote") else False
    return out


def draft_from_matrix(parsed, *, doc_title, page, url, model):
    """Turn parse_x_matrix() output into a tree-format DRAFT (reviewed=false). It has symptoms and causes with
    citations, but no checks/specs/priors: a reviewer must write those from the manual before setting reviewed=true."""
    causes = parsed["causes"]
    return {"format": FORMAT, "id": re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-"), "models": [model],
            "reviewed": False, "drafted_by": "nav_tree.parse_x_matrix (deterministic table parser, no model)",
            "source": {"title": doc_title, "page": page, "url": url},
            "todo_for_reviewer": ["check every X against the page image", "write one check per cause (hud <=40 chars, "
                                  "kind, spec with page)", "set priors (or leave equal)", "add lockout gates",
                                  "then set reviewed=true"],
            "symptoms": [{"name": s["name"], "page": page,
                          "causes": [{"col": col, "name": causes.get(col, {}).get("cause", "?"),
                                      "fix": causes.get(col, {}).get("action", ""), "page": page,
                                      "prior": round(1 / len(s["causes"]), 3)} for col in s["causes"]]}
                         for s in parsed["symptoms"]],
            "checks": []}


def warnings(tree):
    """Authoring warnings (not errors): causes the engine can never single out.
    A cause is 'unresolvable' when no check outcome it expects is exclusive to it, i.e. every (check, outcome)
    pair it expects is also expected by another cause of the same symptom AND some other cause expects a superset
    of its signature (or an identical one)."""
    out = []
    for s in tree.get("symptoms", []):
        causes = s.get("causes", [])
        sigs = {c["id"]: set((c.get("expect") or {}).items()) for c in causes}
        for c in causes:
            mine = sigs[c["id"]]
            if not mine: continue
            others = [o for o in causes if o["id"] != c["id"]]
            if not any(o for o in others if mine - sigs[o["id"]] == set()) : continue
            twins = [o["id"] for o in others if mine <= sigs[o["id"]]]
            out.append(f"symptom {s.get('id')}: cause {c['id']} can't be singled out; its signature "
                       f"{sorted(mine)} is also expected by {twins}. Add a check only {c['id']} fails.")
    return out
