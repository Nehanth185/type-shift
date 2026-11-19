"""Proactive "ready cards": the model's top guesses for what this worker does next, each pre-fetched (the answer or
step is already on the card) and labelled with WHY. Shown only when the worker has turned learning on.

Pre-fetch never calls the LLM (cost, latency, and a guess should not trigger a model call): Ask previews use the
extractive answer from the docs (grounded.mock_answer, the quoted sentence + source), Gauge shows the documented
range, Guide shows the step text from the doc.
"""
from collections import Counter

import grounded, retrieval, workapi
from . import apps, osdb
from .hud import card
from .learn import APP_NAMES, START, Model, learnable, split

MIN_SCORE = 0.15


def model_for(wid):
    return Model(osdb.events(wid))


def shift_state(evs, ctx):
    mine = [e for e in evs if e.get("shift_id") == ctx.shift_id and learnable(e)]
    return (mine[-1]["action"] if mine else START), Counter(e["action"] for e in mine)


def preview(action, text, ctx, wid, ws, evs=()):
    """-> (lines, open_action) for one predicted action, without calling the LLM."""
    app, asset, detail = split(action)
    c2 = type(ctx)(**{**ctx.public(), "asset": asset or ctx.asset})
    if asset and asset != ctx.asset:
        c2.model = (osdb.state_get(wid, "asset_models", {}) or {}).get(asset, "")
    req = apps.Req(text or "", "", c2, wid, ws)
    st = workapi.store()
    if app == "ask" and text:
        hits = retrieval.search(st.chunks_for(ws), apps.doc_query(text, c2), c2.doc_label, k=workapi.TOP_K,
                                embedder=workapi.S["embedder"])
        if hits:
            hud, _, _ = grounded.mock_answer(apps.doc_query(text, c2), hits)
            return [hud[0], hud[-1]], {"app": "ask", "text": text}
        return [f"Ask: {text}"], {"app": "ask", "text": text}
    if app == "gauge":
        rng = apps.doc_range(apps.Caps(apps.APPS["gauge"][0], req), detail or "pressure")
        if rng:
            span = f"{apps.fmt(rng['lo'])}-{apps.fmt(rng['hi'])}" if rng["lo"] is not None else f"max {apps.fmt(rng['hi'])}"
            return [f"Normal {detail or 'pressure'} {span} {rng['unit']}", grounded.source_line(rng["src"])], \
                {"app": "gauge", "text": detail or ""}
        return [f"Log {detail or 'a'} reading"], {"app": "gauge", "text": detail or ""}
    if app == "guide":
        g = st.get_guide(wid)
        if detail.startswith("step") and g:
            n = int(detail.split()[-1]) if detail.split()[-1].isdigit() else g["idx"] + 1
            if 1 <= n <= len(g["steps"]):
                return grounded.wrap(f"Step {n}: {g['steps'][n - 1]['text']}", max_lines=2), {"app": "guide", "text": detail}
        if detail.startswith("step"):  # reopen the procedure this worker usually runs on this asset, then jump
            starts = [e for e in evs if e.get("intent") == "guide.start" and e.get("asset") == asset and e.get("text")]
            if starts:
                return [f"{starts[-1]['detail'] or 'Guide'}: {detail}"], {"app": "guide", "text": f"{starts[-1]['text']} {detail}"}
        return [f"Start: {detail or 'procedure'}"], {"app": "guide", "text": text or f"how do I {detail}"}
    if app == "checklist":
        return [f"PM checklist {asset}".strip()], {"app": "checklist", "text": text or f"PM checklist {c2.doc_label}"}
    if app == "handoff":
        jobs = sum(1 for i in st.memory(wid, kinds={"job_done"}))
        return [f"Wrap up: {jobs} jobs logged"], {"app": "handoff", "text": ""}
    if app == "notes":
        n = sum(1 for i in st.memory(wid, kinds={"note"}) if not i["meta"].get("done"))
        return [f"{n} open loops"], {"app": "notes", "text": ""}
    return [APP_NAMES.get(app, app)], {"app": app, "text": text or ""}


def ready_cards(wid, ws, ctx, k=3):
    if not osdb.settings(wid)["learning"]:
        return []
    evs = osdb.events(wid)
    m = Model(evs)
    if m.n < 3:
        return []
    prev, done = shift_state(evs, ctx)
    out = []
    for p in m.predict(prev, ctx.phase, ctx.asset, done, k=k):
        if p["score"] < MIN_SCORE or p.get("support", 0) < 2:
            continue
        app, asset, detail = split(p["action"])
        lines, act = preview(p["action"], m.text.get(p["action"]), ctx, wid, ws, evs)
        act = {**act, "asset": asset or ctx.asset}
        out.append(card(app, f"Ready · {p['label']}", lines, p["why_full"], why=p["why"], why_full=p["why_full"],
                        kind="ready", action=act, score=p["score"], parts=p["parts"]))
    return out
