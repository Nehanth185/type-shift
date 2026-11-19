"""Smart-glasses v0 server.  Run:  python server.py   (or: uvicorn server:app --host 0.0.0.0 --port 8000)

v1 work API (docs, grounded JSON /ask, guide, memory, ready cards): see ../API.md and workapi.py.
POST /ask with Content-Type: application/json goes to the v1 handler; the v0 forms below are unchanged.

POST /ask   multipart/form-data:
              image     JPEG (required)       - the photo from the glasses
              question  text (optional)       - typed question
              audio     WAV (optional)        - spoken question (transcribed offline if faster-whisper is installed)
              device_id text (optional)
            or a raw image/jpeg body (question in ?q=...)
            query: ?audio=url (default) | pcm (WAV inline as base64) | none
            -> {"hud_text", "speech_text", "audio_url" | "pcm_b64", "sample_rate", "question", "model", "timings_ms"}
GET  /audio/{id}.wav   the spoken answer (16 kHz mono 16-bit)
GET  /health           status, version, uptime_s, llm_backend, docs_indexed (+ provider, model, TTS/STT engines)
GET  /last             the last photo the glasses sent (handy for aiming the camera)
GET  /app/             the companion web app (../companion), served from here so it needs no CORS setup

Env: NVIDIA_API_KEY (no key -> mock mode), LLM_PROVIDER=mock|openai, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY,
     TTS_ENGINE=auto|piper|espeak|none, PIPER_VOICE=path.onnx, STT_ENGINE=auto|none, WHISPER_MODEL=tiny.en,
     DEVICE_TOKEN (optional shared secret, must match the firmware), PORT=8000, LOG_DIR=logs,
     GLASSES_DB=data/glasses.db, SEED_DEMO=1 (load sample_workspace as workspace "demo"), RETRIEVER=bm25|embed,
     CORS_ORIGINS=*, AUTH_MODE=off|worker (worker: per-worker Bearer tokens, see workers.py),
     COMPANION_DIR=../companion, REQUEST_LOG=1 (0: no JSON request log on stderr),
     SEED_DEMO_DOCS=1 (hosted demo: load sample_workspace into "demo" only when the DB has no docs),
     RENDER (set by Render itself: /health then omits the DB file path). Hosting: DEPLOY_RENDER.md
"""
import base64, json, os, time, uuid
from collections import OrderedDict

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import workapi
from llm import DEFAULT_QUESTION, make_provider
from speech import STT, TTS

HERE = os.path.dirname(os.path.abspath(__file__))
try:  # optional .env next to this file (KEY=value lines)
    for line in open(os.path.join(HERE, ".env")):
        k, _, v = line.strip().partition("=")
        if k and not k.startswith("#") and v: os.environ.setdefault(k.strip(), v.strip())
except FileNotFoundError:
    pass

provider, tts, stt = make_provider(), TTS(), STT()
TOKEN = os.environ.get("DEVICE_TOKEN", "")
LOG_DIR = os.path.join(HERE, os.environ.get("LOG_DIR", "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
AUDIO = OrderedDict()        # id -> wav bytes (last 20 answers, in memory)
LAST = {"jpeg": None}
app = FastAPI(title="smart-glasses v0 + work API v1", docs_url="/apidocs", redoc_url=None)  # /docs is ours
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])
# ---- structured request log (G34): one JSON line per request on logger "requests".
# Logs method, path (no query string), status, latency_ms and a request id. Never headers, tokens, query or bodies.
import logging, re
VERSION = "0.1.0"          # bump with CHANGELOG.md
STARTED = time.time()
REQUEST_LOG = os.environ.get("REQUEST_LOG", "1") not in ("0", "false", "off")
_reqlog = logging.getLogger("requests")
_reqlog.setLevel(logging.INFO)
if REQUEST_LOG and not _reqlog.handlers:   # stderr, next to uvicorn's own log; still propagates (pytest caplog)
    _h = logging.StreamHandler(); _h.setFormatter(logging.Formatter("%(message)s")); _reqlog.addHandler(_h)
_RID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    t0 = time.perf_counter(); status = 500
    rid = request.headers.get("x-request-id", "")
    if not _RID_OK.match(rid): rid = uuid.uuid4().hex[:16]   # a client id is echoed only if it is short and plain
    request.state.request_id = rid
    try:
        resp = await call_next(request); status = resp.status_code
        resp.headers["X-Request-ID"] = rid
        return resp
    finally:
        if REQUEST_LOG:
            _reqlog.info(json.dumps({"ts": round(time.time(), 3), "request_id": rid, "method": request.method,
                                     "path": request.url.path, "status": status,
                                     "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}))


workapi.init(provider, token=TOKEN, log_dir=LOG_DIR)
app.include_router(workapi.router)
import runpy; os_router = runpy.run_path(os.path.join(HERE, "..", "os", "boot.py"))["router"]  # G44 Worker OS (../os, package worker_os)
app.include_router(os_router)
if os.environ.get("SEED_DEMO", "") in ("1", "true"):
    import seed_demo
    seed_demo.seed(workapi.store())
if os.environ.get("SEED_DEMO_DOCS", "") in ("1", "true"):   # G37: fresh disk on every Render restart
    import seed_demo
    _seeded = seed_demo.seed_if_empty(workapi.store())
    print(f"[seed] SEED_DEMO_DOCS: loaded {len(_seeded)} demo docs" if _seeded else "[seed] SEED_DEMO_DOCS: DB already has docs")
COMPANION = os.path.abspath(os.environ.get("COMPANION_DIR", os.path.join(HERE, "..", "companion")))
if os.path.isfile(os.path.join(COMPANION, "index.html")):
    # html=True serves index.html at /app/. Only the app's own files are reachable (mock_server.py is not served:
    # StaticFiles serves any file in the folder, so the .py and tests are excluded by the check below).
    class _CompanionFiles(StaticFiles):
        async def get_response(self, path, scope):
            if path.endswith(".py") or path.startswith("tests"):
                raise HTTPException(404, "not found")
            return await super().get_response(path, scope)
    app.mount("/app", _CompanionFiles(directory=COMPANION, html=True), name="companion")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/app/")


@app.get("/health")
def health():
    """Liveness + config summary. No auth, no secrets (no keys, tokens, URLs with credentials)."""
    emb = workapi.S["embedder"]
    try:
        docs = workapi.store().q("SELECT COUNT(*) AS n FROM docs")[0]["n"]; db_ok = True
    except Exception:  # noqa: BLE001  - health must answer even if the DB is broken
        docs, db_ok = None, False
    return {"status": "ok" if db_ok else "degraded", "version": VERSION,
            "uptime_s": round(time.time() - STARTED, 1), "llm_backend": provider.name, "docs_indexed": docs,
            # older fields, kept for the companion, Mentra adapter and test scripts
            "ok": db_ok, "provider": provider.name, "model": getattr(provider, "model", "mock"),
            "tts": tts.engine, "stt": stt.engine, "token_required": bool(TOKEN),
            "auth": workapi.S["auth_mode"],
            "retriever": f"bm25+embed:{emb.name}" if emb else "bm25",
            # hosted (Render sets RENDER=true): don't publish the server's file layout
            "db": None if os.environ.get("RENDER") else getattr(workapi.S["store"], "path", None)}


def _v0_ok(x_device_token="", authorization=""):
    """G43: who may use the v0 glasses forms (/ask multipart or raw JPEG, /last). DEVICE_TOKEN set -> it must match.
    No DEVICE_TOKEN but AUTH_MODE=worker -> a worker token (X-Device-Token or Bearer); before this the v0 path was
    open to anyone on the network in worker mode. AUTH_MODE=off and no DEVICE_TOKEN -> open (bench setup)."""
    if TOKEN: return x_device_token == TOKEN
    if workapi.S["auth_mode"] == "worker":
        return bool(workapi.store().worker_for_token(x_device_token or workapi._bearer(authorization)))
    return True


@app.get("/last")
def last(x_device_token: str = Header(default=""), authorization: str = Header(default="")):
    if not _v0_ok(x_device_token, authorization): raise HTTPException(401, "bad device token")
    if not LAST["jpeg"]: raise HTTPException(404, "no photo yet")
    return Response(LAST["jpeg"], media_type="image/jpeg")


@app.get("/audio/{aid}.wav")
def audio(aid: str):
    wav = AUDIO.get(aid)
    if wav is None: raise HTTPException(404, "expired")
    return Response(wav, media_type="audio/wav", headers={"Content-Length": str(len(wav))})


@app.post("/ask")
async def ask(request: Request, x_device_token: str = Header(default=""), authorization: str = Header(default=""),
              audio: str = "url", q: str = ""):
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):  # v1 work API: docs-grounded answer (see ../API.md); auth there
        return await workapi.ask_json(request, x_device_token)
    if not _v0_ok(x_device_token, authorization): raise HTTPException(401, "bad device token")
    t = {"start": time.time()}
    jpeg, question, voice, device = None, q, None, ""
    if ctype.startswith("multipart/"):
        form = await request.form()
        if "image" in form: jpeg = await form["image"].read()
        question = (form.get("question") or question or "").strip()
        if "audio" in form and hasattr(form["audio"], "read"): voice = await form["audio"].read()
        device = form.get("device_id") or ""
    else:
        jpeg = await request.body()
    if not jpeg or jpeg[:2] != b"\xff\xd8": raise HTTPException(400, "need a JPEG in field 'image'")
    LAST["jpeg"] = jpeg

    heard = ""
    if voice and not question:
        heard = await run_in_threadpool(stt.transcribe, voice); question = heard
    t["stt"] = time.time()
    question = question or DEFAULT_QUESTION

    ws = os.environ.get("V0_WORKSPACE", "")
    if ws:  # G47: grounded path. Firmware questions go through docs retrieval + number guard, not the raw vision model.
        body = workapi.AskIn(workspace_id=ws, worker_id=f"v0-{device or 'glasses'}", question=question,
                             image_b64=base64.b64encode(jpeg).decode())
        try:
            r = await run_in_threadpool(workapi._answer, body)
        except HTTPException: raise
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"hud_text": "AI service error", "speech_text": "", "error": str(e)[:300]})
        hud = "\n".join(r["answer_hud"]); say = r["answer_full"]
        wav = await run_in_threadpool(tts.synth, say) if audio != "none" else None
        o = {"hud_text": hud, "speech_text": say, "question": question, "heard": heard, "model": "grounded",
             "sample_rate": 16000, "grounded": True, "sources": r.get("sources", [])}
        if wav:
            if audio == "pcm": o["pcm_b64"] = base64.b64encode(wav).decode()
            else:
                aid = uuid.uuid4().hex[:12]; AUDIO[aid] = wav
                while len(AUDIO) > 20: AUDIO.popitem(last=False)
                o["audio_url"] = f"/audio/{aid}.wav"
        return o
    try:
        ans = await run_in_threadpool(provider.ask, jpeg, question)
    except Exception as e:  # noqa: BLE001  - always give the glasses something to show
        print(f"[ask] LLM error: {e}")
        return JSONResponse({"hud_text": "AI service error", "speech_text": "", "error": str(e)[:300]})
    t["llm"] = time.time()

    out = {"hud_text": ans.hud, "speech_text": ans.say, "question": question, "heard": heard,
           "model": ans.model, "sample_rate": 16000}
    wav = await run_in_threadpool(tts.synth, ans.say) if audio != "none" else None
    t["tts"] = time.time()
    if wav:
        if audio == "pcm":
            out["pcm_b64"] = base64.b64encode(wav).decode()
        else:
            aid = uuid.uuid4().hex[:12]; AUDIO[aid] = wav
            while len(AUDIO) > 20: AUDIO.popitem(last=False)
            out["audio_url"] = f"/audio/{aid}.wav"
    out["timings_ms"] = {"stt": round((t["stt"] - t["start"]) * 1000), "llm": round((t["llm"] - t["stt"]) * 1000),
                         "tts": round((t["tts"] - t["llm"]) * 1000), "total": round((t["tts"] - t["start"]) * 1000)}
    with open(os.path.join(LOG_DIR, "asks.jsonl"), "a") as f:  # text only; photos are not stored
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "device": device, "question": question,
                            "hud": ans.hud, "say": ans.say, "raw": ans.raw, "model": ans.model,
                            "jpeg_bytes": len(jpeg), **out["timings_ms"]}) + "\n")
    print(f"[ask] {out['timings_ms']['total']} ms  HUD: {ans.hud!r}")
    return out


if __name__ == "__main__":
    import socket
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
    except OSError:
        ip = "127.0.0.1"
    print(f"\n  Glasses server: put  SERVER_URL \"http://{ip}:{port}\"  in firmware secrets.h")
    print(f"  provider={provider.name} model={getattr(provider, 'model', 'mock')} tts={tts.engine} stt={stt.engine}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
