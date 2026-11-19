"""Navigator app: "pump P-12 is leaking" -> one check at a time, with the page, until the cause; then Fix Capture.

  start     symptom + asset -> reviewed fault tree for the asset's model (no tree / not reviewed / symptom not in it
            -> "Not in your docs", no guessing). Team fixes for this machine first: "Last time here: ...".
  answer    yes / no / a reading ("reads 2 psi") / "skip". Readings are judged against the tree's spec, units must
            match (never converted). Safety gates come BEFORE the check that needs them: lockout for LOTO checks,
            "pump running" for RUN checks after a lockout.
  found     "fixed" (or "fixed: <what you did>") saves a TEAM-owned fix record for this asset. "no, not that" rules
            the cause out and goes on.
  stop      "stop troubleshooting". "done, log it" mid-diagnosis asks first.
Team fixes are shared inside the workspace (founder decision 2026-11-13). Personal notes stay in the worker's private
memory and are never read here.
"""
import re, time

from fastapi import APIRouter, Depends, HTTPException

import grounded, workapi
from store import new_id
from . import nav_engine as E
from . import nav_tree
from .apps import Manifest, register, res, title
from .hud import card

router = APIRouter(prefix="/nav", tags=["navigator"])
FIX_SCHEMA = """CREATE TABLE IF NOT EXISTS nav_fixes (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, model TEXT, asset TEXT, symptom TEXT, cause_id TEXT, cause TEXT,
  fix_text TEXT, checks INTEGER, minutes REAL, author TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS nav_fixes_ws ON nav_fixes(workspace_id, model, asset, ts);"""


# ------------------------------------------------------------------ team fix records (workspace-shared)
def fx():
    s = workapi.store()
    if not getattr(s, "_nav_ready", False):
        with s.lock:
            s.db.executescript(FIX_SCHEMA); s.db.commit()
        s._nav_ready = True
    return s


def save_fix(ws, model, asset, symptom, cause_id, cause, fix_text, checks, minutes, author):
    fid = new_id("fix")
    fx().x("INSERT INTO nav_fixes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (fid, ws, model, asset, symptom, cause_id, cause, fix_text[:300], checks, minutes, author, time.time()))
    return fid


def fixes(ws, model=None, asset=None, symptom=None):
    q, a = "SELECT * FROM nav_fixes WHERE workspace_id=?", [ws]
    if model: q += " AND lower(model)=lower(?)"; a.append(model)
    if asset: q += " AND lower(asset)=lower(?)"; a.append(asset)
    if symptom: q += " AND symptom=?"; a.append(symptom)
    return fx().q(q + " ORDER BY ts DESC", tuple(a))


def public_fix(f, wid=None):
    return {"id": f["id"], "asset": f["asset"], "model": f["model"], "symptom": f["symptom"], "cause": f["cause"],
            "cause_id": f["cause_id"], "fix": f["fix_text"], "checks": f["checks"], "minutes": f["minutes"],
            "date": time.strftime("%Y-%m-%d", time.localtime(f["ts"])), "mine": wid is not None and f["author"] == wid}


# ------------------------------------------------------------------ cards
def src(tree, obj):
    d = tree["docs"][obj["doc"]]
    return {"doc_id": f"tree:{tree['id']}:{obj['doc']}", "title": d["title"], "page": str(obj["page"]),
            "snippet": obj.get("quote", "")}


def fmt(v):
    return nav_tree.fmt(v)


def spec_line(chk):
    if chk["kind"] == "reading":
        s = chk["spec"]
        line = f"Spec {fmt(s['lo'])}-{fmt(s['hi'])} {s['unit']} · ~{fmt(chk['minutes'])} min"
    else:
        line = f"Say yes or no · ~{fmt(chk['minutes'])} min"
    return ("⚠ Electrician only · " + line.split(" · ")[0]) if chk.get("who") == "electrician" else line


def check_speech(chk, n):
    s = f"Check {n}: {chk['hud']} "
    if chk["kind"] == "reading":
        sp = chk["spec"]
        s += f"Spec is {fmt(sp['lo'])} to {fmt(sp['hi'])} {sp['unit']}. Say the reading."
    else:
        s += "Say yes or no."
    if chk.get("who") == "electrician": s += " This one is for a qualified electrician only."
    return s + f" Page {chk['page']}. Say skip if you can't check it."


def check_card(tree, sess, ctx, chk, first=None, problem=None):
    n = len(sess["answers"]) + 1
    lines = [first] if first else []
    lines += [chk["hud"]] + ([] if first else [problem or spec_line(chk)]) + [grounded.source_line(src(tree, chk))]
    sp = (f"{first}. " if first else "") + (f"I need it as: {problem}. " if problem else "") + check_speech(chk, n)
    head = f"Check {n}" + (f" · {ctx.asset}" if ctx.asset else "")
    return card("navigator", head, lines, sp, sources=[src(tree, chk)], nav_check=chk["id"])


def gate_card(tree, ctx, gid, first=None):
    g = tree["gates"][gid]
    lines = list(g["hud"])[:2] + [grounded.source_line(src(tree, g))]   # the warning never gives up a line
    speech = ("Safety first. Lock out and tag out the pump motor before this check. Say 'locked out' when done."
              if gid == "loto" else
              "The next check needs the pump running for at least 2 minutes. Clear your lockout the way your site "
              "requires, then say 'running'.")
    return card("navigator", title("Safety first", ctx), lines, (f"{first}. " if first else "") + speech,
                sources=[src(tree, g)], nav_gate=gid)


def found_card(tree, ctx, cause, p, n):
    return card("navigator", title("Likely cause", ctx), [cause["name"], "Fix: " + cause["fix"],
                                                          grounded.source_line(src(tree, cause))],
                f"Most likely cause after {n} checks: {cause['name']}. Fix: {cause['fix']}. Page {cause['page']}. "
                f"When it's fixed, say 'fixed', or tell me what you found. Say 'not that' if it isn't it.",
                sources=[src(tree, cause)])


def refuse(ctx, lines, speech, data=None):
    return res(card("navigator", title("Navigator", ctx), lines, speech), detail="refused",
               data={"not_in_docs": True, **(data or {})})


# ------------------------------------------------------------------ flow
def _sess(caps):
    return caps.state("session")


def _save(caps, sess):
    caps.set_state("session", sess)


def _symptom(tree, sid):
    return next(s for s in tree["symptoms"] if s["id"] == sid)


def advance(tree, sess, req, caps, first=None):
    ctx = req.ctx
    sym = _symptom(tree, sess["symptom"])
    c = E.found(tree, sess)
    if c:
        sess.update(status="found", found=c["id"], current=None, gate=None)
        _save(caps, sess)
        return res(found_card(tree, ctx, c, sess["post"][c["id"]], len(sess["answers"])), detail=sym["name"],
                   data=_data(sess, found=c))
    chk, _ = E.next_check(tree, sess)
    if chk is None:
        sess.update(status="exhausted", current=None, gate=None)
        _save(caps, sess)
        best, p = E.best_unconfirmed(tree, sess)
        if best:
            return res(card("navigator", title("Not confirmed", ctx),
                            [f"Maybe: {best['name']}", "No check left in your docs", "Ask your lead before fixing"],
                            f"The checks in your docs can't confirm it. The best match is {best['name']}, about "
                            f"{round(p * 100)} percent, from page {best['page']}. Ask your lead before acting on it.",
                            sources=[src(tree, best)]),
                       detail=sym["name"], data=_data(sess, unconfirmed=best["id"], not_in_docs=True))
        return res(card("navigator", title("Not in your docs", ctx),
                        ["No documented cause fits", "Stop and ask your lead", "Say 'found it: …' to save"],
                        "None of the causes in your docs fits these answers, so I won't guess. Stop and check with your "
                        "lead. If you find it, say 'found it' and what it was, and I'll save it for the team."),
                   detail=sym["name"], data=_data(sess, not_in_docs=True))
    need = chk.get("needs", "any")
    gid = "loto" if need == "loto" and sess["state"] != "locked" else "run" if need == "run" and sess["state"] == "locked" else None
    sess.update(status="active", current=chk["id"], gate=gid)
    _save(caps, sess)
    if gid:
        return res(gate_card(tree, ctx, gid, first), detail=sym["name"], data=_data(sess, gate=gid, next_check=chk["id"]))
    return res(check_card(tree, sess, ctx, chk, first), detail=sym["name"], data=_data(sess, check=chk))


def _data(sess, **kw):
    top = E.ranked(sess)[:3]
    d = {"session": {k: sess[k] for k in ("symptom", "status", "state", "minutes") if k in sess},
         "checks_done": len([a for a in sess["answers"] if a["outcome"] != "skipped"]),
         "top_causes": [{"cause": k, "p": round(v, 3)} for k, v in top]}
    for k, v in kw.items():
        d[k] = v["id"] if isinstance(v, dict) and "id" in v else v
    if "check" in kw and isinstance(kw["check"], dict):
        d["check"] = {"id": kw["check"]["id"], "page": kw["check"]["page"], "kind": kw["check"]["kind"],
                      "spec": kw["check"].get("spec")}
    return d


def start(req, caps, tree_folder=None):
    ctx = req.ctx
    model = ctx.model or ctx.doc_label
    if not model:
        _save(caps, None)
        return res(card("navigator", "Navigator", ["Which machine?", "Scan its tag or say it"], "Which machine is it?",
                        kind="idle"), detail="")
    tree = nav_tree.for_model(model) if tree_folder is None else nav_tree.load_all(tree_folder)[0].get(model.lower())
    if not tree:
        draft = any(model.lower().replace("-", "") in p.lower().replace("-", "")
                    for p in nav_tree.load_all()[1])
        return refuse(ctx, ["Not in your docs", "No reviewed fault tree", "Ask your lead ▸"],
                      f"I don't have a reviewed troubleshooting tree for {model}, so I won't guess. "
                      + ("A draft is waiting for review." if draft else "Upload its manual's troubleshooting table."),
                      {"model": model})
    sym = E.symptom_of(tree, req.text)
    if not sym:
        _save(caps, {"status": "need_symptom", "tree": tree["id"]})
        names = [s["name"] for s in tree["symptoms"]]
        return res(card("navigator", title("Navigator", ctx), ["Which symptom?"] + names[:2],
                        "What's it doing? Your docs cover: " + "; ".join(names) + ". If it's none of these, they don't "
                        "cover it and I won't guess."), detail="", data={"symptoms": names})
    past = fixes(req.workspace_id, model=model, symptom=sym["id"])
    sess = E.new_session(tree, sym, ctx.asset, past)
    sess["model"] = model
    here = [f for f in past if ctx.asset and (f["asset"] or "").lower() == ctx.asset.lower()]
    first = None
    if here:
        first = f"Last time here: {here[0]['cause']}"
    elif past:
        first = f"Other {model}s: {past[0]['cause']}"
    out = advance(tree, sess, req, caps, first)
    out["data"]["last_time"] = public_fix(here[0]) if here else None
    out["data"]["team_fixes_used"] = len(past)
    return out


SKIP = re.compile(r"^\s*(?:skip(?: it| that)?|can'?t check(?: it| that)?|cannot check|don'?t know|not sure|pass)\s*[.!]?$", re.I)
NOT_IT = re.compile(r"^\s*(?:no|nope|not (?:it|that)|that'?s not it|wrong|it'?s not that|no,? (?:it'?s )?not (?:it|that))\b", re.I)
FIX_RE = re.compile(r"^\s*(?:found it|fixed(?: it)?|it was(?: the)?|the (?:problem|cause|fault) was(?: the)?|cause was|"
                    r"fix was|turned out(?: to be)?(?: the)?|resolved|that was it|that fixed it|yes,? fixed)\b\s*[:,.-]?\s*(.*)$", re.I)


def answer(req, caps):
    ctx, text = req.ctx, req.text.strip()
    sess = _sess(caps)
    if not sess or sess.get("status") == "need_symptom":
        return start(req, caps)
    tree = nav_tree.load_all()[0].get((sess.get("model") or "").lower())
    if not tree:
        _save(caps, None)
        return refuse(ctx, ["Not in your docs", "Fault tree no longer loaded"], "That fault tree isn't loaded anymore.")
    if sess.get("pending") == "done":
        if E.YES.match(text):
            return finish_unresolved(req, caps, sess, tree)
        sess["pending"] = None
        _save(caps, sess)
        return resume(tree, sess, req, caps, "Not logged. Back to it")
    if sess["status"] == "found":
        if NOT_IT.match(text):
            sess["post"][sess["found"]] = 1e-9   # the worker looked: it's not that
            z = sum(sess["post"].values()); sess["post"] = {k: v / z for k, v in sess["post"].items()}
            sess["ruled_out"] = sess.get("ruled_out", []) + [sess["found"]]
            sess["found"] = None
            return advance(tree, sess, req, caps, "Ruled out. Next check")
        if E.YES.match(text) or FIX_RE.match(text):
            return capture(req, caps, sess, tree)
        return resume(tree, sess, req, caps)
    if sess["status"] == "exhausted":
        return resume(tree, sess, req, caps)
    if sess.get("gate"):
        g = tree["gates"][sess["gate"]]
        said = re.sub(r"[^a-z' ]+", " ", text.lower()).strip()
        if said in g["confirm"] or E.YES.match(text):
            sess["state"], sess["gate"] = g["enter"], None
            sess.setdefault("gates_done", []).append(g["enter"])
            chk = tree["_checks"][sess["current"]]
            _save(caps, sess)
            return res(check_card(tree, sess, ctx, chk), detail=_symptom(tree, sess["symptom"])["name"], data=_data(sess, check=chk))
        return res(gate_card(tree, ctx, sess["gate"]), detail="gate", data=_data(sess, gate=sess["gate"]))
    chk = tree["_checks"][sess["current"]]
    if SKIP.match(text):
        E.skip(sess, chk)
        return advance(tree, sess, req, caps, "Skipped. Next")
    outcome, value, problem = E.judge(chk, text, ctx.asset)
    if problem:
        _save(caps, sess)
        return res(check_card(tree, sess, ctx, chk, problem=problem[0].upper() + problem[1:]), detail="retry",
                   data=_data(sess, check=chk, problem=problem))
    E.update(tree, sess, chk, outcome, value)
    if chk["kind"] == "reading":
        sp = chk["spec"]
        word = {"ok": "in spec", "low": "LOW", "high": "HIGH"}[outcome]
        first = f"{fmt(value)} {sp['unit']}: {word}" if outcome == "ok" else f"⚠ {fmt(value)} {sp['unit']} {word}"
    else:
        first = "OK, noted" if outcome == "ok" else "⚠ Not normal, noted"
    return advance(tree, sess, req, caps, first)


def resume(tree, sess, req, caps, first=None):
    if sess["status"] == "found":
        c = next(x for x in _symptom(tree, sess["symptom"])["causes"] if x["id"] == sess["found"])
        return res(found_card(tree, req.ctx, c, sess["post"][c["id"]], len(sess["answers"])), detail="found",
                   data=_data(sess, found=c))
    if sess["status"] == "exhausted" or not sess.get("current"):
        return advance(tree, sess, req, caps, first)
    if sess.get("gate"):
        return res(gate_card(tree, req.ctx, sess["gate"], first), detail="gate", data=_data(sess, gate=sess["gate"]))
    chk = tree["_checks"][sess["current"]]
    return res(check_card(tree, sess, req.ctx, chk, first), detail="resume", data=_data(sess, check=chk))


def _match_cause(sym, text):
    words = {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())} - {"pump", "with", "that", "this", "fixed", "replaced", "the"}
    best, score = None, 0
    for c in sym["causes"]:
        cw = set(re.findall(r"[a-z]{4,}", (c["name"] + " " + c["id"].replace("_", " ")).lower()))
        s = len(words & cw)
        if s > score: best, score = c, s
    return best


def capture(req, caps, sess, tree, said=None):
    """Fix Capture: the worker's own words + the cause -> a team-owned fix record for this asset."""
    ctx = req.ctx
    sym = _symptom(tree, sess["symptom"]) if sess.get("symptom") else None
    m = FIX_RE.match(req.text or "")
    said = (m.group(1).strip(" .") if m else "") if said is None else said
    cause = None
    if sess.get("found"):
        cause = next(x for x in sym["causes"] if x["id"] == sess["found"])
    if said and sym:
        cause = _match_cause(sym, said) or (cause if not said else None)
    cname = cause["name"] if cause else (said[:60] or "Unknown cause")
    fix_text = said or (cause["fix"] if cause else "")
    n = len([a for a in sess.get("answers", []) if a["outcome"] != "skipped"])
    fid = save_fix(req.workspace_id, sess.get("model") or ctx.doc_label, ctx.asset, sess.get("symptom") or "",
                   cause["id"] if cause else "other", cname, fix_text, n, sess.get("minutes", 0), req.worker_id)
    caps.remember("job_done", f"Fixed: {cname}" + (f" on {ctx.asset}" if ctx.asset else "") + f" ({time.strftime('%-m/%-d')})",
                  {"title": "Troubleshoot " + (sym["name"] if sym else ""), "nav_fix": fid})
    _save(caps, None)
    return res(card("navigator", title("Fix saved", ctx), [cname, f"Team sees it on {ctx.asset or 'this machine'}",
                                                          "Your notes stay private"],
                    f"Saved for the team: {cname}" + (f", {fix_text}" if fix_text and fix_text != cname else "")
                    + f". The next tech on {ctx.asset or 'this machine'} sees it first. Your personal notes stay private."),
               detail="fix", data={"fix_id": fid, "cause": cname, "cause_id": cause["id"] if cause else "other",
                                   "checks": n, "shared_with": "workspace"})


def finish_unresolved(req, caps, sess, tree):
    ctx = req.ctx
    sym = _symptom(tree, sess["symptom"])
    caps.remember("job_done", f"Done: troubleshoot {sym['name'].lower()}" + (f" on {ctx.asset}" if ctx.asset else "")
                  + " (cause not found)", {"title": "Troubleshoot " + sym["name"], "unresolved": True})
    _save(caps, None)
    return res(card("navigator", title("Logged", ctx), ["Logged, cause not found", "No fix saved for the team"],
                    "Logged as done without a cause. Nothing was saved for the team."), detail="done")


def nav_app(req, caps):
    ctx = req.ctx
    sess = _sess(caps)
    if req.intent == "nav.stop":
        _save(caps, None)
        return res(card("navigator", title("Navigator", ctx), ["Stopped troubleshooting", "Nothing saved"],
                        "Stopped. Nothing was saved."), detail="stop")
    if req.intent == "nav.idle":
        if sess and sess.get("status") in ("active", "found", "exhausted"):
            tree = nav_tree.load_all()[0].get((sess.get("model") or "").lower())
            if tree: return resume(tree, sess, req, caps)
        return res(card("navigator", title("Navigator", ctx), ["Say what it's doing", "e.g. 'P-12 is leaking'"],
                        "Tell me what the machine is doing and I'll go one check at a time.", kind="idle"))
    if req.intent == "nav.start":
        if re.match(r"^\s*(?:stop|cancel|end|quit|exit)\b", req.text or "", re.I):
            return res(card("navigator", title("Navigator", ctx), ["Nothing to stop", "Say what it's doing"],
                            "No troubleshooting is running.", kind="idle"), detail="stop")
        return start(req, caps)
    if req.intent == "nav.fix":
        tree = nav_tree.load_all()[0].get(((sess or {}).get("model") or "").lower()) if sess else None
        return capture(req, caps, sess or {}, tree) if tree else capture(req, caps, sess or {}, {"symptoms": []})
    if req.intent == "nav.done":
        if not sess: return start(req, caps)
        tree = nav_tree.load_all()[0].get((sess.get("model") or "").lower())
        if sess.get("status") == "found":
            return capture(req, caps, sess, tree, said="")
        sess["pending"] = "done"
        _save(caps, sess)
        return res(card("navigator", title("Navigator", ctx), ["Cause not found yet", "Log as done anyway?",
                                                               "Say yes or no"],
                        "We haven't found the cause yet. Log this as done anyway? Say yes or no."),
                   detail="confirm", data={"needs_confirm": True})
    return answer(req, caps)


register(Manifest("navigator", "Navigator",
                  "Troubleshoot a fault: say the symptom; one documented check at a time, with the page, until the cause. "
                  "Saves the fix for the team.",
                  ["nav.start", "nav.answer", "nav.fix", "nav.stop", "nav.done", "nav.idle"],
                  ["check", "safety_gate", "likely_cause", "fix_saved", "not_in_docs"],
                  ["docs.read", "memory.write"], ["troubleshoot", "diagnose", "fault", "what's wrong"], "nav.idle"),
         nav_app)


# ------------------------------------------------------------------ HTTP (team fixes + tree status)
@router.get("/trees")
def nav_trees():
    trees, skipped = nav_tree.load_all()
    seen = {}
    for t in trees.values():
        seen[t["id"]] = {"id": t["id"], "models": t["models"], "reviewed_by": t.get("reviewed_by"),
                         "symptoms": [s["name"] for s in t["symptoms"]], "checks": len(t["checks"])}
    import glob, os
    drafts = [os.path.basename(p) for p in glob.glob(os.path.join(nav_tree.TREES, "drafts", "*.json"))]
    return {"loaded": list(seen.values()), "skipped": skipped, "drafts_not_loaded": sorted(drafts)}


@router.get("/fixes")
def nav_fixes(workspace_id: str = "demo", asset: str = "", model: str = "", worker_id: str = None,
              w=Depends(workapi.who)):
    """Team fix records for a machine. Shared inside the workspace by design; personal notes are never here."""
    ws = workapi.workspace_of(w, workspace_id)
    wid = workapi.worker_of(w, worker_id) if (worker_id or (w and w.get("worker_id"))) else None
    return {"workspace_id": ws, "fixes": [public_fix(f, wid) for f in fixes(ws, model or None, asset or None)]}


@router.delete("/fixes/{fid}")
def nav_fix_delete(fid: str, workspace_id: str = "demo", worker_id: str = None, w=Depends(workapi.who)):
    ws = workapi.workspace_of(w, workspace_id)
    wid = workapi.worker_of(w, worker_id)
    r = fx().q("SELECT author FROM nav_fixes WHERE id=? AND workspace_id=?", (fid, ws))
    if not r: raise HTTPException(404, "no such fix")
    if r[0]["author"] != wid: raise HTTPException(403, "only the tech who saved a fix can delete it")
    fx().x("DELETE FROM nav_fixes WHERE id=?", (fid,))
    return {"deleted": fid}
