"""Machine Ear app: the glasses listen to a machine each time a worker walks past it and warn before a breakdown.

  pass       POST /os/ear/pass   {worker_id, workspace_id, asset, model?, audio_b64 (WAV) | demo_clip, noise_snr?}
             -> score vs this machine's healthy baseline -> LEARNING / LISTENING / SUSPECT / ALERT + a HUD card.
             Audio is a separate endpoint on purpose: the /os/input contract (text + photo + context) is unchanged.
  status     /os/input {app: "machine_ear"} -> the state of ctx.asset (or every machine heard, if no asset)
  tap        /os/input {app: "machine_ear", text: "open"} on an ALERT -> the Navigator, started on the matching
             symptom ("noise"), i.e. the same card a worker gets by saying "P-12 noisy". Nothing in Navigator changed.
  clear      "false alarm" (back to LISTENING, the pass is NOT added to the baseline), "relearn" (new baseline).
             A Navigator team fix saved for this machine after the alert clears it too: the alert's sound fingerprint
             is stored with the confirmed cause (the labelled library), and the baseline is relearned (a repaired
             machine sounds different).
Data rules: machine state is workspace-shared (it's about the machine, not the worker). No audio is stored, only the
128-number fingerprint (with opt-in v2 features, baseline passes also keep a 64x16 band-power quantile sketch: no
time order, so no speech can be rebuilt from it). Passes do not record WHO walked by (so this can't become a worker-location log).
Engine: ear_engine.py (numpy). Results and limits: MACHINE_EAR.md.
"""
import base64, binascii, glob, json, os, re, time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from typing import Optional

import workapi
from store import new_id
from .apps import Manifest, register, res, title
from .hud import card

router = APIRouter(prefix="/ear", tags=["machine-ear"])
HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.abspath(os.path.join(HERE, "..", "..", "..", "simulator", "machine-ear-sim.html"))
DATA = os.environ.get("MIMII_DIR") or os.path.abspath(os.path.join(HERE, "..", "..", "..", "web", "downloads", "mimii"))
DEMO_SET = "6_dB_pump_id_00"
MAX_AUDIO_B64 = 2_000_000          # ~60 s of 16 kHz 16-bit mono
SCHEMA = """CREATE TABLE IF NOT EXISTS ear_assets (
  workspace_id TEXT NOT NULL, asset TEXT NOT NULL, model TEXT, state TEXT, baseline TEXT, history TEXT, last TEXT,
  alert_ts REAL, updated REAL, PRIMARY KEY (workspace_id, asset));
CREATE TABLE IF NOT EXISTS ear_passes (
  id TEXT PRIMARY KEY, workspace_id TEXT, asset TEXT, ts REAL, score REAL, pct REAL, odd INTEGER, state TEXT);
CREATE TABLE IF NOT EXISTS ear_labels (
  id TEXT PRIMARY KEY, workspace_id TEXT, asset TEXT, model TEXT, fix_id TEXT, cause TEXT, band TEXT, score REAL,
  fingerprint TEXT, ts REAL);"""

SYMPTOM_TEXT = "noisy"   # cp450 / tm5 trees: "Noise or vibration". The hint picks where to look, not the symptom.


def E():
    """The engine needs numpy; the slim Render install has none. Import lazily so the server always starts."""
    try:
        from . import ear_engine
        return ear_engine
    except ImportError as e:  # pragma: no cover - depends on the install
        raise HTTPException(503, f"Machine Ear needs numpy on the server ({e})") from None


def db():
    s = workapi.store()
    if not getattr(s, "_ear_ready", False):
        with s.lock:
            s.db.executescript(SCHEMA); s.db.commit()
        s._ear_ready = True
    return s


# ------------------------------------------------------------------ per-machine record
def get_asset(ws, asset):
    r = db().q("SELECT * FROM ear_assets WHERE workspace_id=? AND lower(asset)=lower(?)", (ws, asset))
    if not r:
        return {"workspace_id": ws, "asset": asset, "model": "", "state": "LEARNING", "baseline": None, "pending": [],
                "history": [], "last": None, "alert_ts": None, "new": True}
    a = dict(r[0])
    a["baseline"] = json.loads(a["baseline"]) if a["baseline"] else None
    h = json.loads(a["history"] or "{}")
    a["history"], a["pending"] = h.get("odd", []), h.get("pending", [])
    a["last"] = json.loads(a["last"]) if a["last"] else None
    return a


def put_asset(a):
    db().x("INSERT OR REPLACE INTO ear_assets VALUES (?,?,?,?,?,?,?,?,?)",
           (a["workspace_id"], a["asset"], a.get("model") or "", a["state"], json.dumps(a["baseline"]) if a["baseline"] else None,
            json.dumps({"odd": a["history"][-10:], "pending": a["pending"]}), json.dumps(a["last"]) if a["last"] else None,
            a.get("alert_ts"), time.time()))


def all_assets(ws):
    return [get_asset(ws, r["asset"]) for r in db().q("SELECT asset FROM ear_assets WHERE workspace_id=? ORDER BY asset", (ws,))]


def use_v2():
    """v2 features (baseline noise-floor subtraction) are OPT-IN: on held-out MIMII they helped fans and mild-noise
    pumps but hurt pumps in loud -6 dB noise (MACHINE_EAR.md, v2 section). Default stays v1."""
    return os.environ.get("MACHINE_EAR_FEATURES", "v1").lower() == "v2"


def _baseline(a):
    eng = E()
    if a.get("_B") is None and a["baseline"]:
        a["_B"] = eng.Baseline.from_json(a["baseline"])
    return a.get("_B")


def fix_since_alert(a):
    """A Navigator team fix saved for this machine after the alert fired (read-only look at nav_fixes)."""
    if a["state"] != "ALERT" or not a.get("alert_ts"): return None
    try:
        from .nav_app import fixes
        fs = [f for f in fixes(a["workspace_id"], asset=a["asset"]) if f["ts"] >= a["alert_ts"]]
    except Exception:  # noqa: BLE001
        return None
    return fs[0] if fs else None


def close_with_fix(a, f):
    """Alert -> confirmed cause: keep the alert's fingerprint as a labelled example, then relearn (machine changed)."""
    last = a.get("last") or {}
    db().x("INSERT INTO ear_labels VALUES (?,?,?,?,?,?,?,?,?,?)",
           (new_id("earlab"), a["workspace_id"], a["asset"], a.get("model") or "", f["id"], f["cause"],
            (last.get("explain") or {}).get("band", ""), last.get("score"), json.dumps(last.get("fingerprint") or []),
            time.time()))
    a.update(state="LEARNING", baseline=None, pending=[], history=[], alert_ts=None, _B=None)
    a["last"] = {"fixed": f["cause"]}
    put_asset(a)


# ------------------------------------------------------------------ HUD
def state_line(a, info=None):
    """The one HUD line (<=40 chars, <=7 words) for a machine's state."""
    info = info or {}
    s, tag = a["state"], a["asset"]
    if s == "LEARNING":
        return f"{tag} · LEARNING {len(a['pending'])}/{E().MIN_BASELINE} passes"
    if s == "LISTENING":
        return f"{tag} · LISTENING · sounds normal"
    if s == "SUSPECT":
        h = a["history"][-E().ALERT_N:]
        return f"{tag} · SUSPECT · {sum(h)} of {len(h)} odd"
    band = ((a.get("last") or {}).get("explain") or {}).get("band", "")
    return f"⚠ {tag} ALERT · {band}" if band else f"⚠ {tag} ALERT · unlike baseline"


def ear_card(a, ctx_asset=None, info=None):
    s = a["state"]
    line = state_line(a, info)
    last = a.get("last") or {}
    if s == "LEARNING":
        lines = [line, "Learning its healthy sound", "Walk past as usual"]
        speech = f"Learning how {a['asset']} sounds when healthy. {len(a['pending'])} of {E().MIN_BASELINE} passes."
    elif s == "LISTENING":
        lines = [line, f"Last pass: {pct_text(last)}"]
        speech = f"{a['asset']} sounds like its healthy baseline."
    elif s == "SUSPECT":
        lines = [line, "Unusual sound, needs more passes", f"Last pass: {pct_text(last)}"]
        speech = (f"{a['asset']} sounded unusual. I need more passes before I alert. "
                  "This is an anomaly score, not a failure prediction.")
    else:
        ex = last.get("explain") or {}
        lines = [line, ex.get("hint") or "Sounds unlike its baseline", "Tap: troubleshoot noise ▸"]
        speech = (f"{a['asset']} has sounded unlike its healthy baseline on repeated passes"
                  + (f", mostly around {ex['band'].replace('-', ' to ')}" if ex.get("band") else "")
                  + ". Anomaly score, not a diagnosis. Tap to troubleshoot.")
    extra = {"ear_state": s, "state_line": line}
    if s == "ALERT":
        extra["action"] = {"app": "machine_ear", "text": "open", "label": "Troubleshoot noise",
                           "then": {"app": "navigator", "text": f"{a['asset']} {SYMPTOM_TEXT}"}}
    return card("machine_ear", f"Machine Ear · {a['asset']}", lines, speech,
                kind="alert" if s == "ALERT" else "app", **extra)


def pct_text(last):
    if not last or last.get("pct") is None: return "no pass yet"
    p = last["pct"]
    return "odd (top 5% vs healthy)" if last.get("odd") else f"normal ({int(round(p * 100))}th pct)"


def public(a):
    last = a.get("last") or {}
    return {"asset": a["asset"], "model": a.get("model") or "", "state": a["state"], "state_line": state_line(a),
            "baseline_passes": len((a["baseline"] or {}).get("E", [])) or len(a["pending"]),
            "recent_odd": a["history"][-E().ALERT_N:],
            "last": {k: last.get(k) for k in ("score", "pct", "odd", "explain", "fixed") if k in last}}


# ------------------------------------------------------------------ one pass
def do_pass(ws, asset, model, x):
    """Score one walk-by pass and move the machine's state. Returns (asset record, info)."""
    eng = E()
    a = get_asset(ws, asset)
    if model: a["model"] = model
    f = fix_since_alert(a)
    if f: close_with_fix(a, f)
    if a["baseline"] and a["state"] != "LEARNING":     # score with whatever the stored baseline was learned with
        v2 = a["baseline"].get("dim") == eng.V2_DIM
    else:                                              # learning: v1 unless MACHINE_EAR_FEATURES=v2 (opt-in, see MACHINE_EAR.md)
        v2 = use_v2()
    e = eng.embed_pass(x) if v2 else eng.embed(x)
    info = {"seconds": round(len(x) / eng.SR, 2), "prev_state": a["state"]}
    if a["state"] == "LEARNING" or not a["baseline"]:
        a["pending"] = [p for p in a["pending"] if len(p) == len(e)]   # drop pre-upgrade passes
        a["pending"].append(e.round(2).tolist())
        a["state"] = "LEARNING"
        if len(a["pending"]) >= eng.MIN_BASELINE:
            a["baseline"] = eng.Baseline(a["pending"]).to_json()
            a["pending"], a["state"], a["history"] = [], "LISTENING", []
        a["last"] = {"score": None, "pct": None, "odd": None}
    else:
        B = _baseline(a)
        s = B.score(e); p = B.pct(s); odd = p > eng.ODD_PCT
        new = eng.step(a["state"], odd, a["history"])
        if a["state"] != "ALERT": a["history"].append(bool(odd))
        ex = B.explain(e)
        ex["hint"] = eng.hint_for(ex)
        if new == "ALERT" and a["state"] != "ALERT": a["alert_ts"] = time.time()
        if new == "ALERT" and a["state"] == "ALERT":   # keep the evidence that raised the alert
            ex = (a.get("last") or {}).get("explain") or ex
        a["state"] = new
        a["last"] = {"score": round(s, 3), "pct": round(p, 3), "odd": bool(odd), "explain": ex,
                     "fingerprint": e[:eng.V1_DIM].round(2).tolist()}
        info.update(score=round(s, 3), pct=round(p, 3), odd=bool(odd))
    put_asset(a)
    db().x("INSERT INTO ear_passes VALUES (?,?,?,?,?,?,?,?)",
           (new_id("pass"), ws, asset, time.time(), info.get("score"), info.get("pct"),
            None if info.get("odd") is None else int(info["odd"]), a["state"]))
    return a, info


# ------------------------------------------------------------------ the app (through /os/input)
FALSE_ALARM = re.compile(r"\b(?:false alarm|ignore|it'?s fine|sounds fine|clear(?: it)?|dismiss)\b", re.I)
RELEARN = re.compile(r"\b(?:relearn|re-learn|new baseline|reset baseline|learn again|reset)\b", re.I)
OPEN = re.compile(r"^\s*(?:open|tap|troubleshoot|go|yes|check it|fix it)\b", re.I)


def ear_app(req, caps):
    ctx, ws = req.ctx, req.workspace_id
    asset = ctx.asset
    if not asset:
        rows = all_assets(ws)
        lines = [state_line(a) for a in sorted(rows, key=lambda a: ["ALERT", "SUSPECT", "LEARNING", "LISTENING"].index(a["state"]))][:3]
        return res(card("machine_ear", "Machine Ear", lines or ["No machines heard yet", "Walk past a tagged machine"],
                        f"{len(rows)} machines heard." if rows else "No machines heard yet.", kind="idle"),
                   data={"machines": [public(a) for a in rows]})
    a = get_asset(ws, asset)
    f = fix_since_alert(a)
    if f: close_with_fix(a, f)
    t = req.text or ""
    if a["state"] == "ALERT" and OPEN.match(t):
        from . import apps
        r = apps.run("navigator", apps.Req(f"{asset} {SYMPTOM_TEXT}", "nav.start", ctx, req.worker_id, ws, req.verbosity),
                     None)
        r["app"] = "navigator"
        r["data"]["from_machine_ear"] = {"asset": asset, "explain": (a.get("last") or {}).get("explain")}
        return r
    if t and FALSE_ALARM.search(t) and a["state"] in ("ALERT", "SUSPECT"):
        a.update(state="LISTENING", history=[], alert_ts=None)
        a["last"] = {**(a.get("last") or {}), "cleared": "false_alarm"}
        put_asset(a)
        return res(card("machine_ear", title("Machine Ear", ctx), [state_line(a), "Marked false alarm", "Not added to baseline"],
                        "Marked as a false alarm. I'll keep listening."), detail="false alarm", signal="dismiss",
                   data={"machine": public(a)})
    if t and RELEARN.search(t):
        a.update(state="LEARNING", baseline=None, pending=[], history=[], alert_ts=None, last=None)
        put_asset(a)
        return res(card("machine_ear", title("Machine Ear", ctx), [state_line(a), "Old baseline dropped",
                                                                  "Only do this if it's healthy"],
                        "Relearning. The next passes become the healthy baseline, so only do this when the machine is OK."),
                   detail="relearn", data={"machine": public(a)})
    c = ear_card(a)
    if a.get("new"):
        c = card("machine_ear", f"Machine Ear · {asset}", [f"{asset} · not heard yet", "Walk past to start learning"],
                 f"I haven't heard {asset} yet.", kind="idle")
    return res(c, detail="status", data={"machine": public(a)})


register(Manifest("machine_ear", "Machine Ear",
                  "Listens to machines as you walk by; warns when one sounds unlike its healthy self, then opens "
                  "troubleshooting. Anomaly score, not a diagnosis.",
                  ["ear.status"], ["state", "alert"], ["docs.read"],   # no "microphone" perm in core yet: audio uses /os/ear/pass
                  ["machine ear", "listen to the machine", "how does it sound"], "ear.status"), ear_app)


# ------------------------------------------------------------------ HTTP
class PassIn(BaseModel):
    worker_id: Optional[str] = None
    workspace_id: str = Field(default="demo", min_length=1)
    asset: str = Field(min_length=1, max_length=60)
    model: str = Field(default="", max_length=60)
    audio_b64: Optional[str] = None
    demo_clip: Optional[str] = None      # "normal/00000011.wav" | "abnormal/00000002.wav" | "synth:normal" | "synth:fault"
    noise_snr: Optional[float] = None    # demo only: add SYNTHETIC plant noise at this SNR (dB)
    seconds: Optional[float] = Field(default=None, gt=0.5, le=60)   # use only the first N seconds (a short walk-by)


def demo_audio(name, seed=0):
    eng = E()
    import numpy as np
    if name.startswith("synth:"):
        return eng.synth_machine(eng.SR * 10, np.random.default_rng(seed), fault=1.0 if name.endswith("fault") else 0.0)
    if not re.fullmatch(r"(normal|abnormal)/\d{8}\.wav", name):
        raise HTTPException(422, "demo_clip must look like normal/00000011.wav")
    p = os.path.join(DATA, DEMO_SET, name)
    if not os.path.isfile(p): raise HTTPException(404, f"demo clip not on this server: {name}")
    return eng.read_wav(p)


@router.post("/pass")
def ear_pass(body: PassIn, w=Depends(workapi.who)):
    workapi.worker_of(w, body.worker_id)     # who walked by is checked, never stored
    ws = workapi.workspace_of(w, body.workspace_id)
    eng = E()
    if body.audio_b64:
        if len(body.audio_b64) > MAX_AUDIO_B64: raise HTTPException(413, "audio too long (max ~60 s)")
        try:
            x = eng.read_wav(base64.b64decode(body.audio_b64, validate=True))
        except (binascii.Error, ValueError, EOFError, Exception) as e:  # noqa: BLE001
            raise HTTPException(422, f"audio_b64 must be a PCM WAV ({str(e)[:80]})") from None
    elif body.demo_clip:
        x = demo_audio(body.demo_clip, seed=int(time.time() * 1000) % 100000)
    else:
        raise HTTPException(422, "send audio_b64 (WAV) or demo_clip")
    if body.seconds: x = x[: int(body.seconds * eng.SR)]
    if body.noise_snr is not None:
        import numpy as np
        x = eng.mix(x, eng.plant_noise(len(x), np.random.default_rng()), body.noise_snr)
    if len(x) < eng.SR // 2: raise HTTPException(422, "need at least 0.5 s of audio")
    a, info = do_pass(ws, body.asset.strip(), body.model.strip(), x)
    return {"asset": a["asset"], "state": a["state"], "prev_state": info["prev_state"], "pass": info,
            "card": ear_card(a), "machine": public(a), "speech": ear_card(a)["speech"]}


@router.get("/status")
def ear_status(workspace_id: str = "demo", asset: str = "", w=Depends(workapi.who)):
    ws = workapi.workspace_of(w, workspace_id)
    rows = [get_asset(ws, asset)] if asset else all_assets(ws)
    return {"workspace_id": ws, "machines": [public(a) for a in rows if not a.get("new")]}


@router.get("/labels")
def ear_labels(workspace_id: str = "demo", asset: str = "", w=Depends(workapi.who)):
    """The labelled library: alert fingerprints joined to the cause a tech confirmed (no audio, no worker id)."""
    ws = workapi.workspace_of(w, workspace_id)
    q, args = "SELECT id, asset, model, fix_id, cause, band, score, ts FROM ear_labels WHERE workspace_id=?", [ws]
    if asset: q += " AND lower(asset)=lower(?)"; args.append(asset)
    return {"workspace_id": ws, "labels": db().q(q + " ORDER BY ts DESC", tuple(args))}


class SeedIn(BaseModel):
    worker_id: Optional[str] = None
    workspace_id: str = Field(default="demo", min_length=1)
    asset: str = Field(min_length=1, max_length=60)
    model: str = ""
    passes: int = Field(default=40, ge=3, le=150)
    seconds: Optional[float] = Field(default=None, gt=0.5, le=10)


@router.post("/demo/seed")
def ear_demo_seed(body: SeedIn, w=Depends(workapi.who)):
    """Demo only: build a machine's healthy baseline from the MIMII healthy TRAIN clips (never the test clips the
    simulator plays), or from SYNTHETIC audio when the dataset isn't on this server. Says which in 'source'."""
    workapi.worker_of(w, body.worker_id)
    ws = workapi.workspace_of(w, body.workspace_id)
    eng = E()
    import numpy as np
    split = os.path.join(HERE, "ear_tools", "split_pump_id00.json")
    names = json.load(open(split))["train_normal"] if os.path.isfile(split) else []
    files = [os.path.join(DATA, DEMO_SET, "normal", n) for n in names]
    files = [p for p in files if os.path.isfile(p)][: body.passes]
    if files:
        X, source = [eng.read_wav(p) for p in files], f"MIMII {DEMO_SET} healthy train clips"
    else:
        r = np.random.default_rng(0)
        X, source = [eng.synth_machine(eng.SR * 10, r) for _ in range(body.passes)], "SYNTHETIC pump sound"
    if body.seconds: X = [x[: int(body.seconds * eng.SR)] for x in X]
    a = get_asset(ws, body.asset)
    a.update(model=body.model or a.get("model") or "", state="LISTENING", pending=[], history=[], alert_ts=None,
             baseline=eng.Baseline([(eng.embed_pass if use_v2() else eng.embed)(x) for x in X]).to_json(), last=None)
    a["_B"] = None
    put_asset(a)
    return {"asset": a["asset"], "state": a["state"], "baseline_passes": len(X), "source": source}


@router.get("/demo/clips")
def ear_demo_clips():
    """Held-out TEST clips the simulator may play (never used in the demo baseline)."""
    split = os.path.join(HERE, "ear_tools", "split_pump_id00.json")
    out = {"normal": [], "abnormal": [], "source": "synthetic", "license": None}
    if os.path.isfile(split) and os.path.isdir(os.path.join(DATA, DEMO_SET)):
        sp = json.load(open(split))
        for lab in ("normal", "abnormal"):
            out[lab] = [f"{lab}/{n}" for n in sp["test_" + lab] if os.path.isfile(os.path.join(DATA, DEMO_SET, lab, n))]
        if out["normal"] and out["abnormal"]:
            out.update(source=f"MIMII {DEMO_SET} (Hitachi, Zenodo 3384388)", license="CC BY-SA 4.0")
    if not out["normal"] or not out["abnormal"]:
        out.update(normal=["synth:normal"], abnormal=["synth:fault"], source="synthetic")
    return out


@router.get("/demo/audio/{lab}/{name}")
def ear_demo_audio(lab: str, name: str):
    """The demo clip as WAV so the simulator can play what the glasses 'heard' (channel 0, 16 kHz)."""
    if lab == "synth":
        eng = E(); import numpy as np
        return Response(eng.to_wav(demo_audio(f"synth:{name}")), media_type="audio/wav")
    x = demo_audio(f"{lab}/{name}")
    return Response(E().to_wav(x), media_type="audio/wav")


@router.get("/sim", include_in_schema=False)
def ear_sim():
    if not os.path.isfile(SIM): raise HTTPException(404, "simulator/machine-ear-sim.html not found")
    return FileResponse(SIM, media_type="text/html")
