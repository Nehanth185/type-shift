# Companion web app v0 (phone / laptop)

Single page, plain HTML + JS + CSS, no build step. Talks to the answer server over the API contract below.

| File | What it is |
|---|---|
| `index.html`, `app.js`, `style.css` | The app. Tabs: **Ask** (ready-card buttons, question + photo/camera, one-eye HUD preview, full answer, clickable sources, guide next/prev), **Docs** (upload, list, delete), **My memory** (see, delete one, forget everything), **Settings** (server URL, workspace ID, worker ID). |
| `mock_server.py` | **MOCK** server, Python standard library only. Implements the contract with canned answers from `work/spec_v1/DEMO_SCRIPT.md`. No model, no PDF parsing, no retrieval. Every response carries `"mock": true` and the header `X-Mock: true`; the app then shows "MOCK" on the HUD and in the status pill. |
| `tests/test_companion.py` | Headless Chromium (Playwright) tests. Starts the mock server itself. |
| `tests/test_companion_real.py` | **Contract test**: the same flows against the **real** server (`../server`, mock LLM, fresh DB), run twice: `AUTH_MODE=worker` and `off`. Every check compares the page with what the server returned or stored. |
| `tests/shots/*.png` | Screenshots from the last runs (`real_*` = real server). |
| `tests/results.json` | Last mock run: **36/36 passed** (2026-11-12). |
| `tests/results_real.json` | Last real-server run: **80/80 passed** (42 worker-token + 38 no-auth, 2026-11-12). |

## Run it
```bash
cd prototype/v0/companion
python3 mock_server.py --port 8765        # serves the app too
# open http://localhost:8765/
```
Against the real server: the server serves this app itself at `http://<server>:8000/app/`, so the default Server URL (the page's own origin) just works. From elsewhere, set the Server URL in Settings. If the server runs with `AUTH_MODE=worker`, paste your **Worker token** in Settings (from `python workers.py add <you>`); the app then asks `/whoami` and fills in your Worker ID. Settings are kept in the browser's localStorage only.

## Tests
```bash
pip install playwright          # browser: system chromium (apt install chromium) is used
python3 tests/test_companion.py # 36 checks vs the mock, writes tests/results.json + tests/shots/
python3 tests/test_companion_real.py   # 80 checks vs the real server (needs ../server requirements), tests/results_real.json
```
Real-server run covers: app served at `/app/`; no token → 401 banner; token sets the worker ID; docs table == `GET /docs`; real PDF + MD upload (title/pages/chunks from the server); text-less PDF → 422; `.docx` → 415; `/ask` request body shape; HUD == server `answer_hud` with no clamp and no width overflow; `Source: … p.14`; source list, snippet and doc_id == server; photo + question; photo only (ungrounded, "No source"); not in docs; guide start (⚠ first), next/prev/arrows, **Finish** → `finished:true`, every step fits; "Done, log it" closes the server guide; 3 ready cards == server `lines`; memory list == `GET /memory`, delete one and forget-all checked on the server, other worker sees nothing; deleting the manual makes the answer "Not in your docs"; token/worker mismatch → 403 on the HUD; switching tokens switches identity; bad token → offline + 401; mobile 390 px; no console errors.
Covers: typed ask, HUD 3 lines × ≤7 words and no line cut off by width, MOCK tag, full answer, latency, source click shows page + snippet, photo upload sent as raw base64 JPEG in `image_b64`, fake-camera snapshot, client-side clamp when the server sends too much, guide start/next/prev/arrow keys/last step, all 3 ready-card triggers, docs list/upload/delete/no-file error, memory list/delete one/forget all (checked on the server too), settings persist, bad URL rejected, unreachable server (banner + HUD error, no crash), reconnect, mobile 390 px layout with no horizontal overflow, no console/page errors.

## API contract the app uses
| Call | Body / query | Response the app reads |
|---|---|---|
| `POST /docs` | multipart `file`, `workspace_id` | `{doc_id,title,pages,chunks}` |
| `GET /docs?workspace_id=` | | a bare list (the real server; the app still accepts `{docs:[…]}`) |
| `DELETE /docs/{doc_id}` | | any 2xx |
| `POST /ask` | `{workspace_id, worker_id, question, image_b64?, mode:"answer"\|"guide"}` | `{answer_hud[≤3], answer_full, sources[{doc_id,title,page,snippet}], guide?{step,total,text}, latency_ms}` |
| `POST /guide/next`, `/guide/prev` | `{worker_id}` | same shape as `/ask` with `guide` filled; `finished:true` past the last step (confirmed on the real server) |
| `GET /memory?worker_id=` | | `{worker_id, items:[{id,text,kind,created_at,label}]}` |
| `DELETE /memory/{id}?worker_id=`, `DELETE /memory?worker_id=` | | `{deleted}`; the count is shown after forget-all |
| `GET /health`, `GET /whoami` | | provider and auth mode; with a token, the worker it belongs to |
| `POST /context` | `{worker_id, trigger:"see_machine"\|"open_job"\|"start_shift", label}` | `{lines, last_answer, usual_next_step, unfinished}` |

`image_b64` is sent as **raw base64 (no `data:` prefix)**, JPEG, longest side ≤1024 px, quality 0.8, resized in the browser.
Errors: the app shows `error` or `detail` from any non-2xx JSON body.
Auth: when a Worker token is set, every call sends `Authorization: Bearer <token>`.
HUD: 3 lines × ≤7 words × ≤40 characters (same as the server's `HUD_CHARS`). The preview is sized so exactly 40 monospace characters fit at any window width, so "too wide" means the same thing on a phone and a laptop.

## Not verified
- Real phone camera: only Chromium's fake camera was tested. No iOS Safari test.
- With a real LLM (NIM/OpenAI): the real-server test uses the deterministic mock provider, so answer wording, latency and citation quality from a real model are untested.
- Large PDFs: only the 3-page sample PDF and a 1-page text-less one were uploaded.
- Worker tokens are stored in the browser's localStorage in plain text (fine for a prototype on your own phone, not for shared devices).
- Whether 40 characters is the right line width for the actual glasses display (the OLED in firmware fits ~12 characters per line at the large font and wraps on its own; the Mentra G1/G2 widths are untested).
