# Changelog: prototype/v0 software

## 2026-11-20: Machine Ear pass quality gate + replay harness (untested on a real mic)

`os/ear_gate.py` rejects a 3-second pass that has talking, clipping or a wrong level, and says why. `os/ear_tools/replay.py` cuts a WAV into 3-second passes, gates each, posts the good ones to `/os/ear/pass` and prints passes sent, passes rejected with reasons, and the score of every pass (`--expect healthy|faulty` prints PASS/FAIL). A rejected pass is never sent, so it cannot change a baseline. The server, the engine and the OS are unchanged: the gate runs in the harness only, other clients are not gated. `os/DESK_FAN_TEST.md` has the desk-fan steps. Tests: `os/tests/test_ear_gate_replay.py` (synthetic audio). **Not run on a real microphone or a real machine.**

## 2026-11-18: Machine Ear fan test + noise work (v2, opt-in)

Fan test: MIMII fan id_00/id_02 at 6/0/-6 dB, plus pumps id_00/id_02 at 0 and -6 dB (`os/ear_tools/ear_eval_v2.py`, `eval/ear_v2_cells.json`, `ear_results.json["v2"]`). New `ear_engine.embed_pass()` + baseline noise-floor subtraction (v2), tuned on pump id_04 only. Held-out result is **mixed**: fans better (e.g. fan id_02 -6 dB AUC 0.643 → 0.727, walk-by 1% → 24%), pumps at real -6 dB **worse** (id_00 0.773 → 0.727, walk-by 26% → 2%). So v2 is **opt-in** (`MACHINE_EAR_FEATURES=v2`); app default stays v1. Old v1 baselines keep working. `step()` gained an optional evidence rule (off). No change to `/os/input` or the manifest. OS tests 146 → 157, server 131 unchanged. Details: `os/MACHINE_EAR.md` (v2 section).

## 2026-11-17: Machine Ear app (sensorless predictive maintenance demo)

New OS app `machine_ear` (`os/ear_engine.py`, `os/ear_app.py`, `/os/ear/*`, `simulator/machine-ear-sim.html`). Walk-by passes -> per-machine healthy baseline -> LEARNING/LISTENING/SUSPECT/ALERT -> one tap opens Navigator on the noise branch; a saved fix labels the alert's sound. MIMII pump id_00 AUC 0.992 clean, 0.983 on 3 s passes, but **0.773 with MIMII's real -6 dB factory noise** (26% of faulty walk-bys alert within 12 passes). Details and risks: `os/MACHINE_EAR.md`. Core edits: +1 line `apps.py`, +1 line `api.py`, app list in `tests/test_apps.py`. OS tests 123 -> 146, server 131 unchanged.

## 2026-11-13: full-system integration run (G43)

New `integration/shift_scenario.py`: one maintenance tech's shift through the real server with every client on it (fake_device v0 loop with Piper + Whisper, companion in Chromium, Mentra real-server harness, Halo bridge). Mock: 53 pass / 0 fail. NVIDIA NIM: 45 pass / 0 fail. The one known gap: the v0 firmware path is not grounded in the docs. Results and top risks: `integration/FEATURE_MATRIX.md`.
Fixes:
- companion: confirm code ran outside the app closure (`$ is not defined` on every load; Confirm/Discard dead). A 429 now shows "Too many questions".
- `/confirm`: only the asking worker may confirm (else 404). The response has the same shape as `/ask` (source objects, clamped HUD, `latency_ms`).
- photo reading held for confirm: `answer_full` no longer says the number (Mentra speaks it). It keeps the documented range.
- LLM call, OCR upload and v0 STT/LLM/TTS run in a worker thread, so they no longer freeze other requests and `/health`.
- v0 `/ask` and `/last` need a worker token when `AUTH_MODE=worker` and no `DEVICE_TOKEN` (they were open).
- halo_bridge: `http_ask` sends `workspace_id` and `mode:"answer"` (it got 422 before). `on_ask` handles list `answer_hud`.
- mentra_app: 429 shows "Too many questions" (harness +1 check). API.md documents `/confirm`, OCR and the rate limit.
- pytest 126 -> 129 (`tests/test_g43_integration.py`). Halo 5 -> 8 (`tests/test_bridge_real.py`, real server). Mentra real harness 23 -> 24.

## 2026-11-13: Render free-plan deploy kit (G37)

Nothing deployed. `server/DEPLOY_RENDER.md` (step by step, incl. "wake it 2 min before a demo"), `server/render.yaml` (free plan, `uvicorn server:app --host 0.0.0.0 --port $PORT`, health check `/health`), `server/requirements-render.txt` (fastapi, uvicorn, python-multipart, pillow, pypdf; no Piper/Whisper, no sentence-transformers/torch, no numpy).
- `SEED_DEMO_DOCS=1`: `seed_demo.seed_if_empty()` loads `sample_workspace/` into workspace "demo" only when the DB has no docs (Render wipes the disk on sleep/restart). `SEED_DEMO=1` unchanged.
- `/health`: `"db": null` when `RENDER` is set.
- Measured with the slim install, mock mode: RSS ~48 MB after startup + seed, ~48 MB after 76 `/ask` calls (limit 512 MB).
- pytest 120 -> 126 (`tests/test_render_slim.py`: seed loader x3, /health on Render, embedder fallback, and a fresh process with sentence-transformers/torch/faster-whisper/piper/numpy made unimportable that still seeds, answers with a citation and loads none of them).

## 2026-11-12 (later): first run against a real model (Nemotron 3.5 Super VL on NVIDIA NIM)

Local only. Used the user's NIM key (about 250 free calls, estimated from run logs). Nothing published or spent. Full results: `REAL_MODEL_RESULTS.md`.

**Measured**
- Grounded eval (new, 30 questions): **17/19** answers right with the right page, **11/11** refusals, **0** unflagged hallucinations. Model alone (gate off): 11/11 refusals. Median **0.64 s**, p90 1.59 s per model answer.
- Vision concept test: **Nemotron 70%**, Llama 3.2 90B 63%, SmolVLM best 33%. Nemotron said "Can't read that" on the unreadable plate, while Llama made up a maker name. But Nemotron **invents analog gauge readings** (3 different readings in 3 runs on a gauge at 0).
- Companion vs real server + real model **80/80**, Mentra harness **23/23**. All mock suites still green: pytest **105** (was 71), v0 loop 11/11, companion real 80/80, companion mock 36/36, Mentra 23/23 and 26/26, tsc clean.

**Fixed (provider path and what the real model exposed)**
- Refusals written in the model's own words, or with NOT_IN_DOCS on a later line, were shown as cited answers. A stray NOT_IN_DOCS beside a correct answer threw the answer away.
- Null or empty replies (reasoning-only, cut-off `<think>`) were read as "Not in your docs". They now raise and show `AI service error`.
- Timeouts: one deadline `LLM_TIMEOUT`, default **18 s** (was 90 s per request and could run longer), under the Mentra adapter's 20 s. It covers the request, queue polling and one retry on 429/5xx.
- The model now gets the cited **page**, not the 110-word chunk (chunks cut "Call the | charge nurse"). The stemmer folds -e and doubled consonants. "how long / usually / often / typically" no longer block the relevance gate.
- Prompt: says whether a photo is attached, and puts the documented fact on HUD line 1.
- **Number guard** (new `unverified_numbers`): a number in neither the cited pages nor the question gets a warning on the HUD line, and the spoken answer starts with "Check this yourself". New `refused_by: retrieval|model`.
- The source snippet is the supporting sentence (it used to be the chunk's first 200 characters, often the disclaimer). Every ask logs `image` and latency; model refusals log the raw reply.
- Test harnesses: `test_companion_real.py --llm nim` and `LLM=nim run_harness_real.sh` (mock stays the default). The Mentra real harness waits for state instead of fixed sleeps.

**Still untested:** field photos beyond 2 eval cases; an invented reading that equals a doc number (the guard can't see it); `RETRIEVER=embed`; large docs; load and rate limits; real hardware and the Mentra runtime.

## 2026-11-12: companion app works against the real server; per-worker tokens

Local only: no accounts, no API keys, nothing published. The LLM was the mock provider throughout.

**What broke when the companion app first ran against the real server** (it had only ever run against the canned `mock_server.py`):
- **HUD lines too wide.** The server limited lines to 7 words, not characters. "Normal discharge pressure is 55-70 PSI." (39 characters) and every guide step overflowed the preview, which fit 24-29 characters depending on window width. The mock's canned lines were all 28 characters or less, so this never showed.
- **Photo-only ask returned 422.** The app allows a photo with no question; the server required a question.
- **The guide could never finish from the app.** Next was disabled on the last step, so the server's `finished: true` (and with it "Done, log it") was unreachable.
- **Worker auth didn't exist**, and the app couldn't send any token: with `DEVICE_TOKEN` set, every app call got 401.
- **The app wasn't served by the server**, so its default server URL (the page's own origin) pointed at nothing.
- Smaller ones: the file picker offered `.docx` (the server says 415); delete-one-memory didn't send `worker_id`; the mock disagreed with the real contract (`{docs:[...]}` vs a bare list, `{ok:true}` vs `{deleted}`, 409 vs 404 with no guide).

**Fixed**
- Server: HUD lines are now ≤7 words **and ≤40 characters** (`HUD_CHARS`, the same cap the v0 loop already used). This applies to answers, guide steps and ready cards.
- Server: `question` may be empty when `image_b64` is sent. The answer then comes from the photo only, marked `ungrounded: true`, with no sources and the last line "Not from your docs".
- Server: the companion is served at `/app/` and `/` redirects there. `.py` files and `tests/` are not served.
- Server: **`AUTH_MODE=worker`**. Every work-API call needs `Authorization: Bearer <worker token>`, and the token decides who the worker is. A `worker_id` that doesn't match the token gets 403, and a missing one is filled in from the token. Workers can be locked to one workspace. Another worker's memory item gets 404. Tokens are made with `workers.py add|list|revoke` and stored as SHA-256 only; no HTTP endpoint mints them. There is a new `GET /whoami`, and `/health` now reports `auth`. JSON `/ask` is now authorised by the work API rather than `DEVICE_TOKEN`; the v0 glasses forms still use `DEVICE_TOKEN`.
- Companion: Worker token setting, sent as a Bearer token. On connect the app reads `/whoami`, fills in the worker ID, and shows "connected · mock LLM · as w1". On the last step, Next becomes **Finish ✓**. The preview now fits exactly 40 characters per line at any width. The client clamps to 7 words and 40 characters. Delete-one-memory sends `worker_id`. `.docx` was removed from the picker.
- Mock server: brought in line with API.md (list shapes, `{deleted}`, 404, `finished`).
- Mentra adapter: worker token in config and the phone settings. With a token, `worker_id` is left out of requests. 401, 403 and a stale guide now show specific HUD messages instead of "Can't reach answers". `Source.page` is typed as a string.
- API.md, the READMEs, and the companion contract table were updated to match.

**Tests (all green, run 2026-11-12)**
| Suite | Before | Now |
|---|---|---|
| server pytest | 58 | **71** (+11 auth, +1 photo-only, +1 `/app` serving; HUD checks now include the 40-character cap) |
| server glasses loop `tests/run_tests.sh` | 11 | **11** |
| companion vs mock (`test_companion.py`) | 36 | **36** (check 17 now walks through Finish) |
| companion vs **real server** (`test_companion_real.py`, new) | n/a | **80** (42 with worker tokens + 38 without) |
| mentra harness vs mock | 26 | **26** |
| mentra harness vs **real server** (`run_harness_real.sh`, new) | n/a | **23** |
| mentra `tsc --noEmit` | 0 errors | **0 errors** |

**Still untested**
- Any real LLM (NIM/OpenAI). All answers came from the deterministic mock. The mock's extractive answers can quote a weak sentence: "How do I prime the IV pump?" got the sample guide's disclaimer as its first line.
- Real phone camera or iOS Safari, a real Mentra runtime or glasses, and the Bun build and `mentra-miniapp pack` after these changes (no Bun here).
- Whether 40 characters per line suits the actual display (the firmware OLED fits about 12 characters per line at the large font and wraps on its own).
- Large or multi-hundred-page PDFs, concurrent users, HTTPS. Worker tokens sit in browser localStorage in plain text.
- There are no roles: any worker can upload or delete the docs of a workspace they can reach. `AUTH_MODE=off` (the default) still trusts `worker_id`.

## 2026-11-13 G27 gauge-reading confirm flow
- /ask on a photo whose answer has numbers not in the docs now returns needs_confirm=true + pending_id; HUD still shows the doc fact plus the ⚠ warning (reading hidden).
- POST /confirm {pending_id, ok} -> ok=true reveals the model's reading marked "Confirmed by worker"; ok=false discards. One-shot (second call 404). Pending store is in-memory (lost on restart).
- tests/test_confirm.py; pytest 109 passed.
