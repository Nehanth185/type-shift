# Work API v1 (prototype/v0/server)

The contract the companion web app (`companion/`) and the MentraOS adapter (`mentra_app/`) build against.
Base URL: `http://<laptop-ip>:8000`. All bodies are JSON unless noted. CORS is open (`CORS_ORIGINS`, default `*`).
FastAPI's own docs page lives at **`/apidocs`** (because `/docs` is ours). The companion web app is served at **`/app/`** (`GET /` redirects there), so it needs no CORS setup.
Checked end to end: `companion/tests/test_companion_real.py` (browser, both auth modes) and `mentra_app/tests/run_harness_real.sh`.

**Auth** (`AUTH_MODE`, default `off`):
- `AUTH_MODE=off`: `worker_id` is trusted as sent. If the server sets `DEVICE_TOKEN`, every endpoint needs it, as header `X-Device-Token: <token>` or `Authorization: Bearer <token>` (401 otherwise).
- `AUTH_MODE=worker`: every work-API call needs `Authorization: Bearer <worker token>` (401 + `WWW-Authenticate: Bearer` otherwise). The **token decides who the worker is**:
  `worker_id` may be left out of bodies and queries (the token's worker is used); a `worker_id` that differs from the token's is refused with **403** (never silently rewritten).
  A worker can be locked to one workspace (`workers.py add w1 --workspace demo`); other workspaces give 403.
  Tokens are made, rotated and revoked with the local CLI `server/workers.py` (`add`, `list`, `revoke`); only a SHA-256 of each token is stored. There is no HTTP endpoint that mints tokens.
  `DEVICE_TOKEN` is not used for the work API in this mode; it still guards the v0 glasses forms of `/ask` (multipart / raw JPEG) and nothing else.
- Any worker in a workspace can upload and delete that workspace's docs (no roles yet).

**HUD rule:** `answer_hud` / `lines` are at most 3 lines, at most 7 words **and at most 40 characters** each (`HUD_CHARS`, default 40, the same cap as the v0 loop). Cut text ends in `…`. A doc answer's last HUD line is always its source, e.g. `Source: CP-450 Pump Manual p.14`.

**Pages** are strings: the printed page label (PDF page labels, or `<!-- page: 14 -->` markers in MD/TXT), else `"1"`, `"2"`, ...

Errors use FastAPI's shape `{"detail": ...}` with 400 (bad body/image), 401 (token missing or wrong), 403 (token is for another worker, or worker locked to another workspace), 404 (unknown id / no active guide / another worker's memory item), 413 (file too big), 415 (file type), 422 (validation, missing `worker_id` without a token, unreadable or text-less file).

---

## Team docs (per workspace)

### `POST /docs` (multipart/form-data)
Fields: `file` (`.pdf`, `.md`, `.markdown`, `.txt`; max 20 MB), `workspace_id`.
```json
{"doc_id": "doc_3477815480d5", "title": "CP-450 Pump Manual (fictional excerpt)", "pages": 3, "chunks": 4}
```
Title = PDF metadata title, else the first `# Heading`, else the file name. Scanned pages (no text layer) are OCR'd with `pdftoppm` + `tesseract` when both are installed (~1.2–1.8 s a page, measured G43; runs off the event loop); without them, or if OCR finds nothing → 422.

### `GET /docs?workspace_id=demo`
A JSON **array**, oldest first:
```json
[{"doc_id": "doc_…", "title": "…", "filename": "cp450_pump_manual_excerpt.pdf", "kind": "pdf",
  "pages": 3, "chunks": 4, "created_at": "2026-11-11T15:41:00Z"}]
```

### `DELETE /docs/{doc_id}`
`{"deleted": "doc_…"}` or 404 (403 if a workspace-locked worker deletes another workspace's doc).

---

## Ask

### `POST /ask` (Content-Type: application/json)
```json
{"workspace_id": "demo", "worker_id": "w1", "question": "Is this pressure normal?",
 "image_b64": "<optional JPEG or PNG, base64, data: URL prefix allowed>", "mode": "answer"}
```
`mode`: `"answer"` (default) or `"guide"`. `worker_id`: required without a worker token, optional with one.
`question` may be empty (or left out) **only when `image_b64` is sent**; neither → 422.
```json
{
  "answer_hud": ["Normal discharge pressure is 55-70 PSI.", "Source: CP-450 Pump Manual p.14"],
  "answer_full": "Normal discharge pressure is 55-70 PSI. … (From CP-450 Pump Manual (fictional excerpt), page 14.)",
  "sources": [{"doc_id": "doc_…", "title": "CP-450 Pump Manual (fictional excerpt)", "page": "14",
               "snippet": "Normal discharge pressure is 55-70 PSI."}],
  "guide": null,
  "latency_ms": 3
}
```
- **Not in your docs:** when retrieval finds nothing relevant, or a real LLM says the passages don't have it (`NOT_IN_DOCS`, or a refusal in its own words such as "the manual does not list a torque value"):
  `answer_hud = ["Not in your docs", "I won't guess this one", "Upload the manual ▸"]`, `sources = []`, extra `"not_in_docs": true` and `"refused_by": "retrieval" | "model"`.
- **Numbers the docs don't contain** (real LLM only): any number in the answer that is in neither the cited pages nor the question (usually the model's reading of a photo) is listed in `"unverified_numbers": ["60"]`. Its HUD line is replaced with `⚠ Photo reading: check it yourself` (with a photo) or `⚠ Not in docs: verify number`, and `answer_full` starts with `Check this yourself: 60 is not in your docs…`. Added 2026-11-12 after Nemotron "read" 60 PSI off a gauge at 0.
  **With a photo** the reading is held back until the worker confirms it (G43): extra `"needs_confirm": true, "pending_id": "…"`, and `answer_full` no longer says the number either (`I read a number off the photo that isn't in your docs, so I'm holding it back… Your docs say: 55-70 PSI. Source: …`), because Mentra speaks `answer_full` aloud. See `POST /confirm`.
- **Partial match** (mock): if a question word appears nowhere in the docs (e.g. "torque"), HUD starts `No 'torque' in docs. Closest:` and `answer_full` says it won't guess.
- **Dark photo** (mock): `["Can't read that", "More light or move closer"]`, `sources = []`.
- **Photo only, no question:** there is nothing to search the docs for, so the vision model describes the photo (the v0 path). `sources = []`, last HUD line `Not from your docs`, extra `"ungrounded": true`; a dark photo still gives `Can't read that`.
- **`mode: "guide"`:** finds the numbered procedure in the best-matching page and starts it (see Guide). Steps are copied from the doc, never written by a model. If no numbered list is found, it answers normally with `guide: null` and says so in `answer_full`.
- **Memory voice commands** (no doc lookup, `sources = []`, extra `"kind": "memory"`):
  `"Note: …" / "Remember …" / "Remind me to …"` save an open loop; `"Done, log it"` finishes the active guide and logs the job (G46: on an unfinished guide it first asks "Only on step k/n"; "log it anyway" or saying it again logs it); `"Forget that"` deletes the newest memory item; `"What do you know about me?"` summarizes memory.
- Optional extra fields (safe to ignore): `not_in_docs`, `refused_by`, `unverified_numbers`, `ungrounded` (photo-only answer), `guide_available` (an answer's page also has numbered steps), `finished`, `kind`, `error` (LLM failure, empty model reply, or no answer within `LLM_TIMEOUT`, default 18 s; HUD then says `AI service error`).
- The v0 glasses forms of `/ask` (multipart `image` + `question`, or a raw `image/jpeg` body) are unchanged and still return `{hud_text, speech_text, audio_url, …}`. They do **not** search the docs (no sources, memory or guide).
  Auth: `DEVICE_TOKEN` if set; else, with `AUTH_MODE=worker`, a worker token as `X-Device-Token` or Bearer (G43: this path and `GET /last` used to be open in worker mode).
- **Rate limit:** JSON `/ask` is limited per worker (`ASK_BURST` 10, refill `ASK_PER_MIN` 30). Over it: **429** `{"detail": "too many questions, try again in Ns"}` + `Retry-After`. Memory commands count too.

### `POST /confirm`
Body `{"pending_id": "…", "ok": true|false, "worker_id": optional}`. Only the worker who asked can confirm (another worker gets 404, like an unknown or expired id; expiry `CONFIRM_TTL`, default 60 s).
`ok: true` -> the held answer, same shape as `/ask` (`answer_hud`, `answer_full`, `sources` as objects, `latency_ms`) plus `"confirmed": true`. `ok: false` -> `["Reading discarded", "Read it yourself"]`, `"confirmed": false`.

---

## Guide (step by step)

### `POST /guide/next` and `POST /guide/prev`
Body: `{"worker_id": "w1"}` (or `{}` with a worker token). Returns the same shape as `/ask`, with `guide` filled:
```json
{"answer_hud": ["Step 2/6: Drain the casing through the", "drain plug at the bottom (Figure 7).", "Source: CP-450 Pump Manual p.21"],
 "answer_full": "Step 2 of 6: …", "sources": [{"doc_id": "…", "title": "…", "page": "21", "snippet": "…"}],
 "guide": {"step": 2, "total": 6, "text": "Drain the casing through the drain plug at the bottom (Figure 7)."},
 "latency_ms": 1}
```
- Step 1 puts the doc's `WARNING:` line first on the HUD (`⚠ Lock out the pump before service.`).
- `prev` on step 1 stays on step 1. `next` on the last step returns `"finished": true` and HUD `All N steps done` (`guide` still shows step N). The guide stays active until `"Done, log it"` (or forget-all). The companion shows **Finish ✓** on the last step for this.
- 404 when the worker has no active guide. One active guide per worker; a new guide replaces the old one.

---

## Personal memory (per worker, worker-controlled)

Text facts only: questions asked and their HUD answers, guides started/finished, notes, recent contexts. **No photos, no audio.** There is deliberately **no endpoint that reads memory by workspace or employer.**

`worker_id` in the calls below is optional with a worker token (the token's worker is used; another worker's id → 403).

### `GET /memory?worker_id=w1`
```json
{"worker_id": "w1", "items": [{"id": "mem_…", "kind": "ask", "text": "Q: Is this pressure normal? → …",
                               "created_at": "2026-11-11T15:41:00Z", "label": "CP-450"}]}
```
Newest first. `kind` ∈ `ask | guide | job_done | note | context`. `label` = the asset/job the item belongs to (or null).

### `DELETE /memory/{id}` (optional `?worker_id=` must match the owner)
`{"deleted": "mem_…"}` or 404. With a worker token only the token's own items can be deleted (someone else's id → 404, same as a missing one).

### `DELETE /memory?worker_id=w1` (forget everything)
`{"worker_id": "w1", "deleted": 7}`. Also ends that worker's active guide.

---

## Context → ready card

### `POST /context`
```json
{"worker_id": "w1", "trigger": "see_machine", "label": "CP-450"}
```
`trigger` ∈ `see_machine | open_job | start_shift`. `worker_id` optional with a worker token.
```json
{"trigger": "see_machine", "label": "CP-450",
 "lines": ["CP-450 · Mechanical seal done 11/11", "Last: Normal discharge pressure is 55-70 PSI.", "▸ Do a leak check in 7…"],
 "last_answer": "Normal discharge pressure is 55-70 PSI. · Source: CP-450 Pump Manual p.14",
 "usual_next_step": "Do a leak check in 7 days",
 "unfinished": ["do a leak check in 7 days"]}
```
Rules (v1, no model): items match the label (case-insensitive); `start_shift` looks across all labels.
`unfinished` = guides not finished (`"<guide>: step k/n"`) + open notes. `usual_next_step` = resume an unfinished guide › an open note › the guide you start most here (`"Start: …"`) › the question you ask most here (`"Ask: …"`) › null.
First visit: `lines = ["CP-450", "Nothing saved here yet", "Ask anything ▸"]`.
Side effect: the label becomes the worker's current context for 8 hours (`CONTEXT_TTL_HOURS`): later questions are tagged with it and retrieval leans toward it. Only the 10 newest contexts are kept.

---

## Other
- `GET /health` (no auth needed, never returns keys or tokens):
  ```json
  {"status": "ok", "version": "0.1.0", "uptime_s": 812.4, "llm_backend": "nim", "docs_indexed": 3,
   "ok": true, "provider": "nim", "model": "…", "tts": "piper:…", "stt": "none", "token_required": false,
   "auth": "off", "retriever": "bm25", "db": "data/glasses.db"}   // "db": null when RENDER is set (hosted)
  ```
  `status` is `ok`, or `degraded` (and `ok: false`, `docs_indexed: null`) when the database can't be read. `llm_backend` is `mock | openai | nim`. `docs_indexed` counts docs across all workspaces. `token_required` only says whether a token is set, never the token. The fields after `docs_indexed` are the older ones, kept for the companion and test scripts.
- **Request IDs and request log:** every response carries `X-Request-ID`. Send your own (letters, digits, `.`, `_`, `-`, at most 64 characters) and it is echoed; anything else is replaced with a new 16-character id. The server writes one JSON line per request to stderr (logger `requests`):
  `{"ts": 1763045000.123, "request_id": "9f1c2e…", "method": "POST", "path": "/ask", "status": 200, "latency_ms": 41.7}`.
  It never logs headers (so no `Authorization` or `X-Device-Token`), query strings, or bodies. `REQUEST_LOG=0` turns it off.
- `GET /whoami` → `{"auth": "worker", "worker_id": "w1", "workspace_id": null}` for a worker token (`worker_id: null` when `AUTH_MODE=off`). The companion calls it on connect and fills in its Worker ID from it.
- `GET /app/…` → the companion web app (`COMPANION_DIR`, default `../companion`; its `.py` files and `tests/` are not served).
- Sample workspace: `workspace_id = "demo"` after `python seed_demo.py` or `SEED_DEMO=1`.
