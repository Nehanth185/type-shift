# Worker OS v0: one page

**What it is:** a thin OS layer on the existing glasses server. Glasses-agnostic: anything that can send text (a
speech transcript), an optional photo and a little context gets back a HUD card + speech. Mounted at `/os/*` by one
line in `server/server.py`. Demo industry: factory maintenance (fictional CP-450 pump docs).

```
 glasses / phone / simulator            (any device: MentraOS app, Halo bridge, os-sim.html)
   text + photo? + context {asset, model, work_order, location, shift_minute, shift_id}
        │  POST /os/input
        ▼
 ┌─ context.py ── asset (or last /context label) · P-12 → CP-450 remembered · phase of shift
 ├─ router.py ─── tapped app > OS cmds > pending "yes/no" > memory cmds > guide/checklist nav
 │                > app rules > LLM classifier (real model only) > keyword phrases > Ask
 ├─ apps.py ───── manifest + handler, Caps gate permissions per app
 │     Ask (docs Q&A + source) · Guide (steps from doc) · Gauge (reading vs doc range, photo = confirm)
 │     Notes (open loops) · Checklist (PM list from doc, tick) · Handoff (today's summary, never sent)
 │     Navigator (G46: symptom → one documented check at a time → cause → team fix; see NAVIGATOR.md)
 │        └── reuses workapi / grounded / retrieval / memory (no server module changed)
 ├─ osdb.py ───── os_events (PRIVATE, per worker, only if learning ON) · os_settings · os_state
 ├─ learn.py ──── counts → sequence · time-of-shift · place  → top-3 next actions + WHY
 └─ proactive.py  pre-fetch each guess WITHOUT the LLM (doc sentence / range / step text) → ready cards
        │
        ▼
 { app, intent, routed_by, card{title, ≤3 lines, why?}, speech, data, ready[ ], verbosity }
   every shown string ≤ 40 chars and ≤ 7 words (hud.violations() is asserted in every test)
```

## Learns how you work (the differentiator)
| Model | Example WHY on the card | Rule |
|---|---|---|
| Sequence | `After Gauge P-12 pressure: 3/3 times` | P(next \| previous action this shift) |
| Time of shift | `Shift start: 8 of 9 shifts` | share of past shifts with it in this phase |
| Place | `At P-12 you open Guide 5/7` | P(action \| asset you're at) |
| Verbosity | answers: short / normal / steps | "shorter"/"more" (+ Guide right after Ask = wants steps) |

score = .55 seq + .30 time + .15 place; a pattern needs ≥2 sightings; things already done as often as usual this
shift are damped ×0.2. All counts, no black box: `GET /os/me/model` lists the patterns in plain words.

**Synthetic hit rate** (3 made-up personas × 10 shifts, `eval/RESULTS.md`, shifts 2-10, 231 actions):
top-1 **37%** / top-3 **49%** vs "your 3 most frequent actions" baseline 4% / 18%. Routine mechanic 45/60,
new hire 45/61, breakdown tech 27/33 (only 26% of his work is routine). Two rules were tuned after the first run
(28/44); real-worker numbers will be lower.

## Privacy, built in
- Learning is **off until the worker turns it on** (`OS_LEARNING_DEFAULT=0`; "start/stop learning" by voice too).
- Worker sees everything (`GET /os/me/events`, `/os/me/model`), deletes one event, all events, or everything
  incl. memory (`DELETE /os/me`, or say "forget everything"). "Forget that" also drops the OS's record of it.
- No endpoint reads events by workspace or for an employer (a test walks every `/os` route to check).
  With `AUTH_MODE=worker` the token decides the worker; another worker's id → 403.
- Events are text only: no photos, no audio. Photo readings are never saved until the worker says "yes".

## Real vs mock
| Real now | Mock / not yet |
|---|---|
| Routing rules, app model + permissions, context, event log, learning, ready cards, privacy endpoints, HUD limits, simulator against the real server | Mock LLM in tests: Ask answers are the doc's own sentence; photo gauge reading only tested with a fake model |
| Gauge range check from the doc text (regex), refuses to judge without a machine, a range, or matching units | LLM intent fallback code exists but is unmeasured on a real model |
| Guide/Checklist steps copied from docs | Sample docs have no real PM checklist (it reuses the seal-swap steps) |
| 75 OS tests + 129 server tests pass | Hit rates are synthetic only; no real worker has used it |

## Run it
```
cd prototype/v0/server && SEED_DEMO=1 python server.py      # mock mode without a key
open http://localhost:8000/os/sim  → "Play 3 demo shifts"   # ready card with WHY appears at shift 4
cd ../os && python -m pytest tests -q && python eval/synth_eval.py
cd ../server && python -m pytest -q                          # run the two suites separately
python ../os/sim_check.py   # browser check (Playwright; may need LD_LIBRARY_PATH for Chromium libs here)
```
The folder is named `os/`, so it's loaded as package `worker_os` via `boot.py` (importing it as `os` would shadow
Python's `os`).

## Next steps to run on MentraOS / Halo
1. **MentraOS app:** in `mentra_app/`, send each transcription to `POST /os/input` instead of `/ask`, with
   `context.asset` from a QR/nameplate scan and `shift_minute` from the phone clock; render `card.title` +
   `card.lines` with the text-wall layout; show `ready[0]` on the dashboard (title + `why`). Map tap = open
   `ready[0].action`, swipe = "next"/"back".
2. **Halo bridge:** same call from `halo_bridge/`; the 3-line HUD already matches the card. Put `why` on a
   long-press so it doesn't take a display line.
3. **Real data:** turn learning on for 2-3 real testers (with consent), log 2 weeks, rerun the hit-rate script on
   their own events (same code path), and compare to the synthetic numbers.
4. Rank-based tuning only after real data; add "dismiss" feedback to down-weight ignored cards; prefetch with the
   real LLM only for the top-1 card if latency allows.
