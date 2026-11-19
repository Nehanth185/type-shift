"""Worker OS HTTP API, mounted under /os by server.py (one include line). Auth is the work API's own
(workapi.who / worker_of): with AUTH_MODE=worker the token decides the worker, and a worker can only ever see or
delete their own events. There is no endpoint that reads events by workspace or for an employer.

  POST   /os/input          text (speech transcript) + optional photo + context -> routed app's HUD card + speech
  GET    /os/home           home card + proactive ready cards (with WHY)
  GET    /os/apps           app manifests
  GET    /os/me/settings    learning flag (opt-in) + verbosity      POST /os/me/settings  change them
  GET    /os/me/events      the worker's own event log              DELETE /os/me/events/{id}   DELETE /os/me/events
  GET    /os/me/model       what the OS has learned, in plain words
  DELETE /os/me             forget everything: OS events + OS state + the worker's memory items
  GET    /os/sim            the OS simulator page (simulator/os-sim.html)
"""
import os, time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import memory, workapi
from . import apps, osdb, proactive, nav_app
from . import context as ctxmod
from . import router as irouter
from .hud import card
from .learn import Model, action_key, learnable

router = APIRouter(prefix="/os", tags=["worker-os"])
router.include_router(nav_app.router)   # /os/nav/trees, /os/nav/fixes (team fix records, G46)
router.include_router(apps.ear_app.router)   # /os/ear/* (Machine Ear: walk-by passes, demo, sim; see MACHINE_EAR.md)
HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.abspath(os.path.join(HERE, "..", "..", "..", "simulator", "os-sim.html"))
LIMIT = workapi.TokenBucket(int(os.environ.get("OS_BURST", "60")), int(os.environ.get("OS_PER_MIN", "240")))


class InputIn(BaseModel):
    worker_id: Optional[str] = None
    workspace_id: str = Field(default="demo", min_length=1)
    text: str = Field(default="", max_length=1000)
    image_b64: Optional[str] = None
    context: dict = Field(default_factory=dict)
    app: Optional[str] = None   # set when the worker tapped an app on the HUD


def active_state(wid):
    st = workapi.store()
    return {"guide": st.get_guide(wid) is not None,
            "checklist": osdb.state_get(wid, "checklist:active") is not None,
            "gauge_pending": osdb.state_get(wid, "gauge:pending") is not None,
            "nav": (osdb.state_get(wid, "navigator:session") or {}).get("status") in ("active", "found", "exhausted", "need_symptom"),
            "done_pending": memory.done_pending(wid)}


def verbosity_for(wid, ctx, evs):
    s = osdb.settings(wid)
    if s["verbosity"] in ("short", "normal", "steps"):
        return s["verbosity"], "Set by you in settings"
    once = osdb.state_get(wid, "verbosity_shift")
    if once and once.get("shift_id") == ctx.shift_id:
        return once["value"], "You asked for this earlier this shift"
    return Model.verbosity(evs)


def home_card(wid, ctx):
    st = workapi.store()
    rc = memory.ready_card(st, wid, "see_machine" if ctx.asset else "start_shift", ctx.asset)
    head = " · ".join(x for x in (ctx.asset or "Home", ctx.work_order) if x)
    when = f"{ctx.shift_minute // 60}:{ctx.shift_minute % 60:02d} into shift" if ctx.shift_minute is not None else ""
    lines = [l for l in rc["lines"][1:] if l and l not in ("Nothing saved here yet", "Ask anything ▸")]
    lines = ([when] if when else []) + lines + ([] if lines else ["Ask anything ▸"])
    return card("home", head, lines, "", kind="home", open_loops=rc["unfinished"])


def os_command(intent, wid, ws, ctx, m):
    if intent == "os.home":
        return {"card": home_card(wid, ctx), "detail": ""}
    if intent in ("os.shorter", "os.more"):
        v = "short" if intent == "os.shorter" else "steps"
        osdb.state_set(wid, "verbosity_shift", {"shift_id": ctx.shift_id, "value": v})
        c = card("os", "Got it", ["Shorter answers from now" if v == "short" else "More detail from now",
                                  "Say 'settings' to change"], "Okay, " + ("shorter" if v == "short" else "more detail") + ".")
        return {"card": c, "detail": "", "signal": "shorter" if v == "short" else "more"}
    if intent in ("os.learning_off", "os.learning_on"):
        on = intent == "os.learning_on"
        osdb.set_settings(wid, learning=on)
        c = card("os", "Learning " + ("on" if on else "off"),
                 ["I'll learn how you work" if on else "I stopped learning", "Only you can see it" if on else
                  "Your log is kept; say 'forget everything'"],
                 "Learning is on. Only you can see what I learn." if on else "Learning is off. Nothing new is logged.")
        return {"card": c, "detail": ""}
    if intent == "os.model":
        evs = osdb.events(wid)
        mdl = Model(evs)
        pats = mdl.summary()
        v, why = Model.verbosity(evs)
        c = card("os", f"Learned from {mdl.n} actions", pats[:3] or ["Nothing learned yet"],
                 (" ".join(p + "." for p in pats) or "I haven't learned any patterns yet.") + f" Answer style: {v}. {why}.")
        return {"card": c, "detail": "", "data": {"patterns": pats, "verbosity": v}}
    if intent == "os.forget_all":
        n = osdb.forget(wid) + workapi.store().forget_worker(wid)
        return {"card": card("os", "Forgot everything", [f"Deleted {n} items", "Learning setting kept"],
                             f"Deleted {n} items about you."), "detail": "", "data": {"deleted": n}}
    if intent == "os.dismiss":
        return {"card": card("os", "OK", ["Hidden"], ""), "detail": "", "signal": "dismiss"}
    raise HTTPException(400, f"unknown OS command {intent}")


@router.post("/input")
def os_input(body: InputIn, request: Request, w=Depends(workapi.who)):
    wid = workapi.worker_of(w, body.worker_id)
    ws = workapi.workspace_of(w, body.workspace_id)
    ok, wait = LIMIT.take(wid)
    if not ok:
        raise HTTPException(429, f"too many requests, try again in {wait}s", headers={"Retry-After": str(wait)})
    t0 = time.time()
    st = osdb.st()
    ctx = ctxmod.build(body.context, wid, st)
    jpeg = workapi.decode_image(body.image_b64)
    try:
        app_id, intent, routed_by, m = irouter.route(body.text, jpeg is not None, active_state(wid),
                                                     workapi.S["provider"], body.app)
    except KeyError:
        raise HTTPException(404, f"no app '{body.app}'") from None
    evs = osdb.events(wid)
    verbosity, vwhy = verbosity_for(wid, ctx, evs)
    if app_id == "os":
        r = os_command(intent, wid, ws, ctx, m)
    else:
        req = apps.Req(body.text, intent, ctx, wid, ws, verbosity, m)
        try:
            r = apps.run(app_id, req, jpeg)
        except apps.PermissionDenied as e:
            raise HTTPException(403, str(e)) from None
        app_id = r.get("app", app_id)
    c = r["card"]
    if intent == "notes.forget":
        osdb.delete_latest_event(wid)   # "forget that" also forgets the OS's record of it
    elif (app_id != "os" and c.get("kind") != "idle") or r.get("signal"):   # just opening an app is not a habit
        asset = ctx.asset if app_id not in ("notes", "handoff") else ""
        osdb.log_event(wid, {"shift_id": ctx.shift_id, "shift_minute": ctx.shift_minute, "phase": ctx.phase,
                             "app": app_id, "intent": intent, "asset": asset, "ctx_asset": ctx.asset,
                             "work_order": ctx.work_order, "location": ctx.location,
                             "action": action_key(app_id, asset, r.get("detail", "")) if app_id != "os" else "",
                             "detail": r.get("detail", ""), "text": body.text, "signal": r.get("signal")})
    ready = proactive.ready_cards(wid, ws, ctx, k=3)
    s = osdb.settings(wid)
    return {"app": app_id, "intent": intent, "routed_by": routed_by, "card": c, "speech": c.get("speech", ""),
            "data": r.get("data", {}), "ready": ready, "verbosity": verbosity, "verbosity_why": vwhy,
            "learning": s["learning"], "context": ctx.public(), "latency_ms": round((time.time() - t0) * 1000)}


@router.get("/home")
def os_home(worker_id: Optional[str] = None, workspace_id: str = "demo", asset: str = "", model: str = "",
            work_order: str = "", location: str = "", shift_minute: Optional[int] = None, shift_id: str = "",
            w=Depends(workapi.who)):
    wid = workapi.worker_of(w, worker_id)
    ws = workapi.workspace_of(w, workspace_id)
    st = osdb.st()
    ctx = ctxmod.build({"asset": asset, "model": model, "work_order": work_order, "location": location,
                        "shift_minute": shift_minute, "shift_id": shift_id}, wid, st)
    s = osdb.settings(wid)
    return {"card": home_card(wid, ctx), "ready": proactive.ready_cards(wid, ws, ctx, k=3), "learning": s["learning"],
            "learning_note": None if s["learning"] else "Learning is off. Turn it on to get ready cards.",
            "apps": [{"id": m.id, "name": m.name} for m, _ in apps.APPS.values()], "context": ctx.public()}


@router.get("/apps")
def os_apps():
    return [m.public() for m, _ in apps.APPS.values()]


class SettingsIn(BaseModel):
    worker_id: Optional[str] = None
    learning: Optional[bool] = None
    verbosity: Optional[Literal["auto", "short", "normal", "steps"]] = None


@router.get("/me/settings")
def get_settings(worker_id: Optional[str] = None, w=Depends(workapi.who)):
    wid = workapi.worker_of(w, worker_id)
    return {"worker_id": wid, **osdb.settings(wid), "learning_default": "on" if osdb.LEARNING_DEFAULT else "off (opt-in)"}


@router.post("/me/settings")
def set_settings(body: SettingsIn, w=Depends(workapi.who)):
    wid = workapi.worker_of(w, body.worker_id)
    osdb.st()
    return {"worker_id": wid, **osdb.set_settings(wid, body.learning, body.verbosity)}


@router.get("/me/events")
def my_events(worker_id: Optional[str] = None, w=Depends(workapi.who)):
    wid = workapi.worker_of(w, worker_id)
    osdb.st()
    return {"worker_id": wid, "events": [{k: e[k] for k in ("id", "ts", "shift_id", "shift_minute", "phase", "app",
                                                            "intent", "asset", "work_order", "location", "detail",
                                                            "text", "signal")} for e in osdb.events(wid)]}


@router.delete("/me/events/{eid}")
def delete_my_event(eid: str, worker_id: Optional[str] = None, w=Depends(workapi.who)):
    wid = workapi.worker_of(w, worker_id)
    osdb.st()
    if not osdb.delete_event(wid, eid): raise HTTPException(404, "no such event")
    return {"deleted": eid}


@router.delete("/me/events")
def delete_my_events(worker_id: Optional[str] = None, w=Depends(workapi.who)):
    wid = workapi.worker_of(w, worker_id)
    osdb.st()
    return {"worker_id": wid, "deleted": osdb.forget(wid)}


@router.get("/me/model")
def my_model(worker_id: Optional[str] = None, w=Depends(workapi.who)):
    wid = workapi.worker_of(w, worker_id)
    osdb.st()
    evs = osdb.events(wid)
    m = Model(evs)
    v, why = Model.verbosity(evs)
    return {"worker_id": wid, "actions_learned_from": m.n, "patterns": m.summary(10), "verbosity": v,
            "verbosity_why": why, "learning": osdb.settings(wid)["learning"]}


@router.delete("/me")
def forget_me(worker_id: Optional[str] = None, w=Depends(workapi.who)):
    wid = workapi.worker_of(w, worker_id)
    osdb.st()
    ev = osdb.forget(wid)
    mem = workapi.store().forget_worker(wid)
    return {"worker_id": wid, "deleted_events": ev, "deleted_memory": mem}


@router.get("/sim", include_in_schema=False)
def sim():
    if not os.path.isfile(SIM): raise HTTPException(404, "simulator/os-sim.html not found")
    return FileResponse(SIM, media_type="text/html")
