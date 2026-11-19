# Glasses server (v0)

```
pip install -r requirements.txt
# voice for Piper TTS (63 MB, one time) - already present in voices/ if you copied this folder whole:
curl -L -o voices/en_US-lessac-low.onnx      https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/low/en_US-lessac-low.onnx
curl -L -o voices/en_US-lessac-low.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/low/en_US-lessac-low.onnx.json
python server.py                 # mock mode if no NVIDIA_API_KEY in env or .env
python fake_device.py            # pretend to be the glasses
bash tests/run_tests.sh          # full end-to-end test, no key needed
```

- `llm.py`: the system prompt (short HUD line plus a longer spoken answer; "Can't read that" instead of guessing) and the providers: `mock`, and `openai` (any OpenAI-compatible API, NVIDIA NIM by default). Empty/null model replies raise instead of reading as "not in docs"; one overall deadline `LLM_TIMEOUT` (18 s) with one retry on 429/5xx.
- `speech.py`: offline TTS (Piper, falling back to espeak-ng) and optional STT (faster-whisper tiny.en).
- `server.py`: `POST /ask`, `GET /audio/{id}.wav`, `GET /health`, `GET /last`, and the companion app at `/app/`. A JSON `POST /ask` goes to the v1 work API.
- Auth: `AUTH_MODE=worker` makes every work-API call carry a per-worker `Authorization: Bearer` token, and the token decides the worker (`workers.py add|list|revoke`, tokens stored hashed). Default `AUTH_MODE=off` trusts `worker_id`; `DEVICE_TOKEN` stays the shared secret for the v0 glasses. Details in `../API.md`.
- v1 work API (contract: `../API.md`): `workapi.py` (endpoints), `store.py` (SQLite), `ingest.py` (PDF/MD/TXT → pages → chunks), `retrieval.py` (BM25 + relevance gate, optional embeddings), `grounded.py` (HUD lines, citations, guide steps, LLM doc prompt), `memory.py` (voice commands, ready cards), `seed_demo.py` + `sample_workspace/` (3 small docs, 2 fictional), `tools/minipdf.py` (tiny PDF writer for samples/tests).
- `tests/grounded_eval.py` + `tests/grounded_cases.json`: 30-question grounded-answer eval over the sample workspace (score, refusal layer, latency). Results with the real model: `../REAL_MODEL_RESULTS.md`.
- Tests: `python -m pytest tests` (v1, mock) and `bash tests/run_tests.sh` (v0 loop). Swagger UI is at `/apidocs`. Logs question, answer and timings to `logs/asks.jsonl`. Photos are not saved to disk; only the latest stays in memory, for `/last`.
- Hosting a public demo (Render free plan, no card): `DEPLOY_RENDER.md`, `render.yaml`, `requirements-render.txt` (no voice, no PyTorch; BM25). `SEED_DEMO_DOCS=1` reloads `sample_workspace/` at startup when the DB is empty (Render wipes the disk on every sleep). With `RENDER` set, `/health` returns `"db": null`.
- No Piper? Install espeak-ng (`sudo apt install espeak-ng`, `brew install espeak-ng`, or the Windows installer from its GitHub releases), and the server uses it automatically.
