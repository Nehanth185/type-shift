# Sample workspace "demo"

Loaded by `python seed_demo.py` or `SEED_DEMO=1 python server.py`. Three small docs, one per demo scene:

| File | Scene | What it is |
|---|---|---|
| cp450_pump_manual_excerpt.pdf | A, mechanic | **FICTIONAL**, self-written. A made-up CP-450 pump manual excerpt, printed pages 14, 21, 22 (PDF page labels). Built from `source/cp450_pump_manual.md` with `python tools/minipdf.py sample_workspace/source/cp450_pump_manual.md sample_workspace/cp450_pump_manual_excerpt.pdf`. |
| unit_iv_pump_guide.md | B, nurse | **FICTIONAL**, self-written. Not clinical advice; not based on any real pump or hospital policy. |
| glasses_v0_bench_notes.md | C, engineer | Self-written notes about our own prototype; pin facts copied from `firmware/glasses_v0/config.h` and `WIRING.md`. Untested on hardware. |

Scene B's "night shift handoff" is deliberately not here: patient notes would come from the hospital system per request and must not sit in a team doc store or in personal memory.

No third-party text is included, so there are no copyright questions.
