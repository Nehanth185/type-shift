# Put the demo server online (Render, free, no card)

What you get: a public link like `https://glasses-demo-server.onrender.com` where anyone can ask the 3 demo manuals a question, and the companion app at `/app/`. Free plan facts (render.com/docs/free, checked 2026-11-13): 512 MB RAM, it **sleeps after 15 minutes** with no visits, waking takes **about 1 minute**, and its disk is **wiped every time it sleeps or restarts**.

What that means for us:
- Uploaded manuals and worker memory disappear when it sleeps. That's why the 3 demo manuals live in the repo (`sample_workspace/`) and get reloaded on every start (`SEED_DEMO_DOCS=1`). Fine for demos, **not** for a pilot where a customer uploads their own manuals.
- No voice on Render: text answers only (no Piper/Whisper, they don't fit). Retrieval is BM25 (no PyTorch).
- Measured locally with the slim install: about **48 MB** of RAM after startup and after 76 questions (mock mode). Lots of room under 512 MB.

## Before you start (5 min)

1. The code must be in a GitHub repo you own. Two ways:
   - **Whole project in one repo** (folders `prototype/v0/server`, `prototype/v0/companion`, …): keep `render.yaml` as it is. This also gives you the companion app at `/app/`.
   - **Only the `server` folder as its own repo**: open `render.yaml` and delete the line `rootDir: prototype/v0/server`. (The companion app won't be there; `/ask` and `/health` still work.)
2. Do **not** commit `.env` or `voices/` or `data/`. Check that `.gitignore` lists `server/.env` and `server/data/` (it does today).

## Deploy (10 min)

1. Go to https://dashboard.render.com/register and **sign up with GitHub**. Pick the free **Hobby** workspace. Don't add a card; you don't need one.
2. Dashboard → **New** → **Blueprint**. Connect GitHub if asked and pick your repo.
3. **Blueprint file path**: if `render.yaml` is not at the top of the repo, type its path, e.g. `prototype/v0/server/render.yaml`. (Render looks for `render.yaml` at the repo root unless you tell it otherwise.)
4. Render shows one service, `glasses-demo-server`, plan **Free**. Click **Apply** / **Deploy**. It starts in **mock mode** (canned, deterministic answers, costs nothing, nothing leaves the server).
5. Optional, real model answers: service → **Environment** → add `NVIDIA_API_KEY` = your NIM key → Save (it redeploys). The key stays in Render, never in the repo. Free NIM calls are limited, and a public link means strangers can spend them (see "Risks"). Delete the variable to go back to mock mode.
6. Wait for the build. The first build takes a few minutes (it installs 5 small packages).
7. When the log says `Application startup complete` and `[seed] SEED_DEMO_DOCS: loaded 3 demo docs`, open:
   - `https://<your-service>.onrender.com/health` → should show `"status":"ok"`, `"docs_indexed":3`, `"retriever":"bm25"`, `"db":null`.
   - `https://<your-service>.onrender.com/app/` → the companion app (whole-repo layout only).
   - `https://<your-service>.onrender.com/apidocs` → try `POST /ask` with `{"workspace_id":"demo","worker_id":"w1","question":"Is this pressure normal?"}`. Expected: "Normal discharge pressure is 55-70 PSI." with "Source: CP-450 Pump Manual p.14".

## Before every demo

- **Wake it 2 minutes before.** Open `/health` in your browser about 2 minutes before you show anyone. The first load can take ~1 minute (Render shows a loading page). Once it shows `"status":"ok"`, you're good for 15 minutes of no traffic; any request resets the timer.
- During a long pitch, refresh `/health` now and then if nobody is using it.
- Don't upload anything you need to keep. It's gone after the next sleep.

## If something goes wrong

| You see | Do this |
|---|---|
| Build fails on a package | Check the log line. `PYTHON_VERSION` is pinned to 3.12.8 in `render.yaml` (Render's own default is 3.14); keep it. |
| "No open ports detected" / deploy times out | The start command must be `uvicorn server:app --host 0.0.0.0 --port $PORT` (already in `render.yaml`). |
| `/health` shows `"docs_indexed":0` | `SEED_DEMO_DOCS` must be `1` (Environment tab). Then **Manual Deploy → Deploy latest commit**. |
| Answers say "AI service error" | The NIM key is wrong or out of calls. Delete `NVIDIA_API_KEY` in the Environment tab to go back to mock mode. |
| Service "suspended" | The free workspace has 750 instance hours and 5 GB bandwidth a month. With no card Render suspends instead of billing; it comes back next month. One sleeping demo service won't hit this. |

## Risks to know

- **It's public.** `AUTH_MODE` is `off`, so anyone with the link can ask questions and upload docs (until the next sleep wipes them). With a NIM key set, strangers can spend your free calls. Keep the link to people you show it to, or stay in mock mode. (`AUTH_MODE=worker` exists but needs worker tokens created with `workers.py`, which you can't run on the free plan because it has no shell, and the tokens would be wiped on sleep anyway.)
- Demo manuals are fictional and say so. Don't present them as real equipment or clinical guidance.
- You're under 18: I couldn't confirm Render's minimum age from their terms page. Let a parent know about the account.

## Local check of the slim install (optional)

```
python -m venv .venv-render && . .venv-render/bin/activate
pip install -r requirements-render.txt
SEED_DEMO_DOCS=1 RENDER=true TTS_ENGINE=none STT_ENGINE=none uvicorn server:app --port 8000
# then open http://localhost:8000/health
```
