# Prototype v0 software: status (updated 2026-11-12)

Flow: tap the button on the glasses. The XIAO takes a photo and sends it over Wi-Fi to the laptop server. The server asks the vision model, makes speech from the answer, and sends the reply back. A short line appears on the OLED, and the full answer plays on the bone transducer.

| Piece | State | Proof |
|---|---|---|
| firmware/glasses_v0 | **Compiles** (arduino-cli, esp32 core 3.3.12, XIAO_ESP32S3, OPI PSRAM). 1.15 MB = 34% of flash, 64 KB = 19% of RAM. | firmware/compile_log.txt |
| server (mock mode) | **Runs end to end**: 11/11 tests pass. Covers photo to HUD text plus 16 kHz WAV, typed question, spoken question (offline Whisper heard "What is the pressure on this gauge?"), inline audio, dark photo giving "Can't read that", bad input, and the real OpenAI-compatible request path against a fake endpoint. | server/tests/last_test_run.txt |
| server work API v1 (docs, grounded answers, guide, memory, ready cards, per-worker tokens) | **105/105 pytest** (mock + stubbed real-provider tests, incl. every raw Nemotron reply seen). Contract in API.md. | server/tests/last_pytest_run.txt |
| companion web app vs the **real** server (mock LLM) | **80/80** browser checks (42 with worker tokens + 38 without): upload PDF, ask, citations, photo, guide to the end, memory view/delete, ready cards, auth errors, mobile. Still 36/36 vs the canned mock. | companion/tests/results_real.json |
| mentra_app (fake session) vs the **real** server | **23/23**, worker-token mode. Still 26/26 vs the canned mock. `tsc` 0 errors. | mentra_app/tests/harness_real_results.json |
| **Real model** (NVIDIA NIM, Nemotron 3.5 Super VL) | **Runs, 2026-11-12.** Grounded eval: 17/19 answers right with the right page, 11/11 "not in docs" refusals, 0 unflagged hallucinations, median 0.64 s per model answer. Companion vs real server + real model 80/80, Mentra harness 23/23. Vision concept test 70% (SmolVLM best was 33%). | REAL_MODEL_RESULTS.md |
| On real hardware | **Untested**: no parts yet. | see below |

Untested until the parts arrive: camera init and photo quality (OV3660 or OV5640 auto-focus), OLED plus camera sharing I2C, MAX98357A sound and volume through bone conduction, the PDM mic recording level, deep-sleep current and wake-on-button, battery life, heat.

Default model is `nvidia/nemotron-3.5-super-vl-120b-a12b` (the old default `meta/llama-3.2-90b-vision-instruct` is listed by NVIDIA as ending 2026-11-16). Measured against the real model on 2026-11-12: see REAL_MODEL_RESULTS.md. Known risk: it sometimes invents analog gauge readings; the server now flags any number that isn't in the docs.

## Run the work API (v1) on a laptop

```
cd prototype/v0/server
pip install -r requirements.txt -r requirements-dev.txt
SEED_DEMO=1 python server.py          # mock answers, loads the sample workspace "demo"
python -m pytest tests                # 105 tests, mock mode, no key, no network
python tests/grounded_eval.py --provider mock   # 30-question grounded eval (add --provider nim for the real model)
bash tests/run_tests.sh               # the original 11 glasses-loop tests
python3 ../companion/tests/test_companion_real.py   # companion in headless Chromium vs this server (needs playwright + chromium)
bash ../mentra_app/tests/run_harness_real.sh        # Mentra adapter (fake session) vs this server (Node 22+)
# same two suites against the real model (needs NVIDIA_API_KEY; ~20 and ~5 NIM calls):
python3 ../companion/tests/test_companion_real.py --llm nim ; LLM=nim bash ../mentra_app/tests/run_harness_real.sh
```
Try it:
```
curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"workspace_id":"demo","worker_id":"me","question":"Is this pressure normal?","mode":"answer"}'
curl -F workspace_id=demo -F file=@my_manual.pdf localhost:8000/docs
```
Companion app: open `http://<laptop-ip>:8000/app/`.

Per-worker sign-in (recommended once more than one person uses it):
```
AUTH_MODE=worker SEED_DEMO=1 python server.py
python workers.py add w1 --workspace demo    # prints w1's token once; paste it in the companion's Settings > Worker token
python workers.py list | python workers.py revoke w1
```
With `AUTH_MODE=worker` the token decides who the worker is, so one worker can't read or delete another's memory by sending a different `worker_id`.

Real answers: put `NVIDIA_API_KEY=...` in server/.env (provider `nim`), or `LLM_PROVIDER=openai` + `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL` for any OpenAI-compatible API. `LLM_TIMEOUT` (default 18 s, kept under the Mentra adapter's 20 s) caps each answer including NVIDIA queueing and one retry on 429/5xx; `LLM_PAGE_CHARS` (default 1500) is how much of each cited page the model sees.

How it works: uploads become pages and ~110-word chunks in SQLite (`server/data/glasses.db`). `/ask` searches the workspace with BM25 (optional embeddings: `RETRIEVER=embed`), and answers only if enough of the question's rare words are in a passage, else "Not in your docs". Mock mode quotes the best sentence; a real LLM gets the top 4 passages and must cite them. Guide steps are always copied from the doc. Memory and ready cards are simple rules in `memory.py`.

Docs: API.md (work API contract), WIRING.md (every wire explained), BUILD_STEPS.md (unboxing to first answer).

## Index (refreshed 2026-11-15)
- server/ FastAPI backend (docs RAG, guide, memory, confirm) | os/ Worker OS + Fault Navigator (NAVIGATOR.md, COVERAGE.md)
- companion/ web app | mentra_app/ MentraOS miniapp | halo_bridge/ Halo adapter | firmware/ XIAO ESP32-S3
- integration/FEATURE_MATRIX.md | API.md | TESTING.md (run all tests) | REAL_MODEL_RESULTS.md | WIRING.md, BUILD_STEPS.md | CHANGELOG.md
Status: hardware untested; nothing bought.
