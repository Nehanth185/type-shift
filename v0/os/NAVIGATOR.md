# Fault Navigator v0 (G46, 2026-11-13): one page

**What it does:** the worker says the symptom ("pump P-12 is leaking"). The glasses show **one check at a time, with the
manual page**, judge each answer against the spec, put the **lockout warning before** any check that needs it, and stop
at the cause. "Fixed" saves a **team fix** for that machine, so the next tech sees "Last time here: …" first.
It's a Worker OS app (`navigator`), routed through `/os/input`. No new UI.

```
"pump P-12 low pressure"  ──/os/input──▶ router (rule) ──▶ navigator app
   asset P-12 → model CP-450 ──▶ trees/cp450.json  (loaded ONLY if "reviewed": true and every quote/spec is on its page)
   team fixes for CP-450 + symptom ──▶ boost those causes; "Last time here: Suction strainer clogged"
   engine (code, no LLM): next check = max info-gain per minute (+ lockout/restart minutes)
   ┌ Check 2 · P-12              ┐     answers: "yes" / "no" / "reads 2 psi" / "skip" / "not that"
   │ Suction gauge reading?      │     "3 bar" → "Say it in PSI" (never converts)
   │ Spec 5-15 PSI · ~1 min      │     LOTO check → "⚠ Lock out + tag out motor … p.21" first
   │ Source: CP-450 Trouble… p.30│     found = top cause ≥ 0.9 AND an abnormal result that points only at it
   └─────────────────────────────┘     nothing fits → "Not in your docs · Stop and ask your lead"
   "fixed: cleaned the basket" ──▶ nav_fixes (workspace-shared)  +  "Fixed: …" in the worker's PRIVATE memory
```

## Files (all new unless marked)
| File | What |
|---|---|
| `trees/cp450.json` | Hand-written, **reviewed** tree for the fictional CP-450: 4 symptoms, 19 causes, 18 checks, 2 safety gates. Every item quotes its page |
| `trees/docs/cp450_troubleshooting.md` | Fictional troubleshooting pages 30-31 (the demo manual had none). **Not** in the demo workspace (server tests pin 3 docs), so Ask can't answer from it yet |
| `trees/drafts/` | Drafts, **never loaded**: the real Sta-Rite SX218 manual (parser + NIM drafts), see its README |
| `nav_tree.py` | Format `worker-os/fault-tree@1`, validator (quote + spec numbers must be on the cited page, HUD ≤40 chars/7 words), review gate, X-matrix table parser, optional LLM draft |
| `nav_engine.py` | Posterior, next check, spec judge, found/unconfirmed. Does not import the LLM |
| `nav_app.py` | App + cards + Fix Capture + `GET /os/nav/trees`, `GET /os/nav/fixes`, `DELETE /os/nav/fixes/{id}` (author only) |
| `nav_draft.py` | `python nav_draft.py PDF PAGE --model X --url U [--llm]` → `trees/drafts/*.json` |
| `eval/nav_selftest.py` → `eval/NAV_SELFTEST.md` | 10 planted faults vs naive linear order |
| `tests/test_navigator.py` | 38 tests |
| `sim_nav_check.py` | Browser check of the simulator's "▶ Navigator demo (2 techs)" |
| edits | `router.py` (+nav rules), `api.py` (+3 lines), `apps.py` (+1 import, +1 intent), `server/memory.py` (done-confirm), `simulator/os-sim.html` (Navigator card) |

## Planted-fault self-test (SYNTHETIC; `eval/NAV_SELFTEST.md`)
| 10 planted faults | Checks | Minutes* | Right cause |
|---|---|---|---|
| **Navigator** | **37** | **104.5** | 10/10 |
| Naive linear (the table's order) | 40 | 125.0 | 10/10 |

\*Minutes are the tree's per-check guesses + lockout time. **Honest read:** on a small tree that already lists likely
causes first, the win is time (-16%), not check count (-7.5%). Biggest wins: drain plug 2 vs 5 checks, wrong rotation
4 vs 6. After 3 team fixes, the same fault took 4 checks instead of 5. With one wrong answer, both arms stopped
without naming a cause (safe, not helpful). **Tuned once:** run 1 was 43 vs 43 checks and named a wrong cause after
team fixes; I changed 3 rules and judged them on the same 10 faults, so the numbers are optimistic.

## Real manual (step 1)
Sta-Rite SX218 IOM, troubleshooting matrix p.17 **[FETCHED]**
https://www.pentair.com/content/dam/extranet/web/nam/sta-rite/manuals/sx218-close-coupled-motor-centrifugal-pump-iom.pdf
- Table parser: 50/50 symptom→cause links. I checked them by eye against the page image.
- NIM (Nemotron 3.5 Super VL) on the same page: precision 62%, recall 72%. It mixes up the X columns. **The review gate is needed.**
- Not loadable yet: the table gives causes and remedies but no checks with spec ranges. A person has to write those.

## Rules I kept
- The LLM **never** picks a check (a test runs a whole diagnosis with a stand-in real model and checks 0 calls).
- No reviewed tree, symptom not in it, or nothing fits → refuse. No machine → "Which machine?"
- Fixes are team-owned; personal notes are never read (a test plants a private note and checks the next tech can't
  see it). Only the author can delete their fix.
- **Bug fixed:** "Done, log it" on step 2/6 logged the job. It now asks ("Only on step 2/6"). "Log it anyway", saying
  it again, or "yes" in the OS logs it. In Navigator, "done" with no cause found asks first and saves no team fix.

## Tests
OS **113** (was 75) · server **130** (was 129; 3 tests updated, they had the old mid-guide "done") · integration mock
`--skip-browser` 49 pass / 0 fail · `sim_check.py` PASS · `sim_nav_check.py` PASS (Chromium needed 4 local libs via `LD_LIBRARY_PATH`).

## Not done / next
1. Founder: re-check `trees/cp450.json` (I wrote and "reviewed" it myself. That isn't a human review).
2. A real tree: write checks and specs for the SX218 (or a pump the G38 interviewees use), then review it.
3. Week 2: run the 10 faults on NIM end to end (speech → answer parsing is rules-only now, so free speech like
   "kinda bubbly" fails), add photo readings (confirm step) for gauge checks.
4. Put the troubleshooting pages in the demo workspace so Ask and Navigator cite the same doc (needs a server-test change).
5. Navigator isn't in learned ready cards yet (`learn.APP_NAMES`). The "last time" card shows only once the worker starts.
6. The ranking still asks a cheap valve check before the "last time" cause's own check. That's fine for time, but it may feel odd to a veteran.
