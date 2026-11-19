# Do all the features work together? (G43, 2026-11-13)

**Short answer: yes on the phone, Mentra and Halo paths, after 8 fixes. No on the DIY hardware path: the firmware's `/ask` never uses the docs.**

One script plays a maintenance tech's shift (Maria, worker token, workspace `plant7`) through the **real server over HTTP**, with every client pointed at that one server: `integration/shift_scenario.py`.

| Run | Result | Wall time |
|---|---|---|
| Mock LLM (`python3 integration/shift_scenario.py`) | **53 pass, 0 fail**, 1 known gap | 35 s |
| Real model, NVIDIA NIM Nemotron 3.5 Super VL (`--llm nim --skip-confirm`) | **45 pass, 0 fail**, 1 known gap (run twice) | 46–54 s |
| HUD check (every response that reaches glasses: `answer_hud`, ready-card `lines`, v0 `hud_text`) | **0 violations** (29 mock, 27 NIM responses checked, ≤3 lines × ≤40 chars) | |

Before the fixes, the same chain broke in 6 places (see "Bugs fixed" below). Raw results: `results_mock.json`, `results_nim.json`.

## Feature matrix

Latency is the client's full round trip on a laptop (mock / NIM). "Phase 2" = a stand-in vision endpoint that invents "88 PSI", run through the real `openai` provider code, because mock mode never flags a reading.

| Feature | Works alone? | Works in the chain? | Model | Latency mock / NIM | Known gaps |
|---|---|---|---|---|---|
| `/health`, request IDs, JSON request log | ✅ pytest | ✅ client `X-Request-ID` echoed and logged; no token in the log | n/a | 4 / 4 ms | |
| Worker tokens (401, 403 workspace lock, `/whoami`) | ✅ | ✅ | n/a | 3–5 ms | No roles: any worker can delete team docs |
| Upload PDF / MD | ✅ | ✅ | n/a | 5–10 ms | |
| **OCR of a scanned PDF** | ✅ (`test_ocr.py`) | ✅ text indexed, answers and a guide come from it | n/a | 1.2 s / 1.2–1.8 s per page | Needs `tesseract` + `pdftoppm` on the server (not on Render's slim image) |
| Cited answer ("CP-450 discharge pressure") | ✅ | ✅ 55–70 PSI, Source p.14 | mock + **real** | 7 / 400–690 ms | |
| Answer from the OCR'd card (ISO VG 220) | n/a | ✅ cites the scanned card | mock + **real** | 9 / 410–740 ms | |
| "Not in your docs" refusal (forklift torque) | ✅ | ✅ no sources | mock + real (refused by retrieval, no model call) | 4–7 ms | |
| Guide mode (6-step seal swap: next ×6, finish, prev, "done, log it") | ✅ | ✅ also a 4-step guide from the OCR'd card | doc text only (no model) | 3–9 ms per step | |
| Gauge photo + question | ✅ | ✅ | mock + **real** | 28 ms / 2.2–4.7 s | Real model, run 1: said **60 PSI on a gauge at 0**. The guard caught it. Run 2: "can't read the gauge clearly" |
| **Photo reading confirm** (hold, confirm, discard, expiry, owner only) | ✅ | ✅ server, companion buttons, Halo voice "confirm" | stand-in vision + real (run 1) | 4–7 ms | Mentra adapter has no confirm step, so it never shows a held reading |
| Photo only, no question | ✅ | ✅ marked "Not from your docs" | mock + real | 12 ms / 1.1–1.3 s | |
| Memory: note, "what do you remember", "forget that", delete one, privacy | ✅ | ✅ w2 can't see or delete Maria's items | n/a | 2–5 ms | |
| **Fault Navigator** (G46, OS app via `/os/input`) | ✅ 38 OS tests, `sim_nav_check.py` | ⚠ not yet in `shift_scenario.py` | code only (no model); trees reviewed by hand | ~2-5 ms per check (TestClient) | Fictional CP-450 tree only; rules-only answer parsing |
| Ready cards (start_shift, see_machine, open_job) | ✅ | ✅ the CP-450 card recalls the note and "seal swap done" | n/a | 3–8 ms | Rules only, not learned |
| Rate limit (burst 10, +30/min per worker) | ✅ | ✅ 429 + Retry-After; other endpoints still work | n/a | | Memory commands count, too. **Unpaced, the script hit 429 on its 11th question**, so it now waits 1 s between questions |
| Concurrency (slow model or OCR doesn't freeze others) | ✅ new test | ✅ `/health` 3–10 ms during OCR and during a 2 s model call | | | Single process: limiter, audio cache and `/last` are in memory |
| **v0 glasses loop** (`fake_device.py`: photo + typed, PCM, spoken WAV, no token) | ✅ `run_tests.sh` | ✅ hud_text ≤40, 16 kHz WAV, 401 without a token | mock + **real** | 0.8–1.2 s / 2.5–3.9 s (incl. Piper TTS) | ⚠ **GAP: no docs, citations, memory or number guard on this path** |
| Voice: Piper TTS, Whisper tiny.en STT | ✅ | ✅ | local | Whisper ~1–2 s | tiny.en heard "gauge" as **"sketch"** in 1 of 2 runs |
| Companion web app (`/app/`) | ✅ 80/80 real, 36/36 mock | ✅ token, ask, sources, memory, confirm buttons, no page errors | mock + real | 130 / 520–700 ms | |
| Mentra miniapp (fake session) | ✅ 24/24, `tsc` 0 errors | ✅ its real-server harness, pointed at the scenario server: 24/24 mock, 24/24 NIM | mock + **real** | harness 3.6 s / 13–22 s | No confirm UI. Real Mentra runtime untested |
| Halo bridge (FakeHalo) | ✅ 8/8 (incl. new real-server tests) | ✅ cited answer in 14-char pages, confirm by voice | mock + real | 20 ms / 1.0 s | Real BLE device untested |
| Firmware | compiles only | ❌ not in the chain (no hardware); `fake_device.py` stands in | | | Camera, OLED, audio, battery untested |
| Browser simulator (`glasses-sim.html`) | standalone | ❌ not wired to this server (calls Gemini, OpenAI or NIM directly) | | | Demo tool only |

## Top 5 breaks and risks

1. **The hardware glasses don't use the docs.** Firmware → multipart `/ask` (v0) → raw vision prompt: no citations, no memory, no guide, **no number guard**. That is the path where a made-up "60 PSI" goes straight to the OLED and the speaker. Fix (needs your call on how a device maps to a worker): send v0 questions through the grounded pipeline, using the worker token as the device token. Auth for this path is already in place (fix 5).
2. **The real model still invents gauge readings.** On a gauge whose needle sat at 0, Nemotron said "60 PSI, within normal" (run 1 of 2). The guard and confirm flow now hold it back on the HUD *and* in speech. But "confirm" asks the worker to accept a number they haven't seen. That is a UX risk, and the Mentra adapter has no confirm step at all.
3. **Mock mode hides the riskiest path.** The mock never flags a number, so no mock suite exercised confirm. That is how the companion's dead Confirm buttons (fix 1) slipped past 80/80 green checks. Phase 2 of the scenario now covers this with a stand-in vision endpoint.
4. **Rate limit vs real use.** A burst of 10 counts memory commands ("done, log it", "remember…") and retrieval refusals that cost nothing. A fast run of taps hits 429. Normal pacing is fine. Consider exempting memory commands.
5. **One process, nothing on real hardware.** The limiter, audio cache and `/last` live in memory on one process with SQLite. Mentra runtime, Halo BLE, camera, OLED, bone audio and battery are all untested. Whisper tiny.en mishears domain words ("gauge" → "sketch").

## Bugs fixed in G43 (each has a test)

1. **Companion:** the confirm code sat outside the app's closure. Every page load threw `$ is not defined`, and Confirm/Discard did nothing (`companion/app.js`; real-server suite went 76/80 → 80/80).
2. **`/confirm`:** any worker could confirm or discard another worker's held reading. It now returns the same shape as `/ask` (source objects, clamped HUD, `latency_ms`). Before, the companion showed "undefined" as the source (`workapi.py`, `store.pending_get`).
3. **Held reading leaked in speech:** the HUD hid the number, but `answer_full` (spoken by Mentra) still said it. It now says it's holding the reading back and gives the documented range.
4. **Server froze during slow work:** the LLM call (up to 18 s), OCR uploads and the v0 STT/LLM/TTS ran on the event loop, so every other worker and `/health` waited. They now run in a worker thread.
5. **v0 `/ask` and `/last` were open** with `AUTH_MODE=worker` and no `DEVICE_TOKEN`. Anyone on the Wi-Fi could spend LLM calls and see the last photo. Both now need a worker token.
6. **Halo bridge:** `http_ask` sent `mode:"ask"` and no `workspace_id`, which got a 422 every time. `on_ask` also crashed on the list-shaped `answer_hud`.
7. **Mentra + companion:** a 429 showed "Can't reach answers". It now shows "Too many questions / Wait a few seconds".
8. **Stale docs:** API.md and ingest said "no OCR in v0", and `/confirm` was undocumented.

## Test counts after the fixes

Server pytest **129** (was 126) · Halo bridge **8** (was 5) · Mentra real-server harness **24/24** (was 23) · Mentra mock harness 26/26 · `tsc` 0 errors · companion vs real server **80/80** · companion vs mock 36/36.

## Run it
`python3 integration/shift_scenario.py [--llm nim] [--skip-browser] [--skip-confirm]`. It needs the server requirements. Optional extras: `piper-tts` + `faster-whisper` (voice steps), `tesseract` + `pdftoppm` (OCR), Playwright Chromium (companion steps) and Node 22+ (Mentra). Missing extras show as SKIP, not FAIL. In this sandbox Chromium needed 4 missing system libraries (libatk, libatspi, libXcomposite, libXdamage), extracted locally and passed via `LD_LIBRARY_PATH`.
