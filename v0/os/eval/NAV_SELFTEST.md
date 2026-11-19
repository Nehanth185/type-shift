# Navigator planted-fault self-test (SYNTHETIC)

Made-up faults on the fictional CP-450 tree; the 'worker' answers exactly as the tree predicts (except the 2 noisy rows). This tests the engine's logic, not real-world speed. Seeded faults flatter us. Run: `python eval/nav_selftest.py`.

**Navigator: 37 checks / 104.5 min for 10 faults, 10/10 right. Naive linear: 40 checks / 125.0 min, 10/10 right.** (Minutes = the tree's per-check estimate + safety-gate time; both are reviewer guesses.)

| Planted fault | Said | Navigator checks / min | Linear checks / min | Navigator right? |
|---|---|---|---|---|
| strainer_clogged | "P-12 low pressure" | 5 / 5.0 | 4 / 4.0 | yes |
| suction_valve_closed | "low flow on P-12" | 3 / 2.5 | 4 / 4.0 | yes |
| worn_impeller | "pressure dropped" | 7 / 31.0 | 7 / 31.0 | yes |
| wrong_rotation | "not pumping after motor work" | 4 / 3.0 | 6 / 6.0 | yes |
| seal_worn | "it's leaking" | 4 / 4.0 | 3 / 3.5 | yes |
| casing_bolts_loose | "leaking at the casing" | 5 / 19.0 | 4 / 18.5 | yes |
| drain_plug_loose | "dripping under the pump" | 2 / 1.0 | 5 / 19.0 | yes |
| low_voltage | "motor keeps tripping" | 2 / 11.0 | 2 / 11.0 | yes |
| coupling_misaligned | "noisy and vibrating" | 4 / 27.0 | 4 / 27.0 | yes |
| cavitation | "grinding noise" | 1 / 1.0 | 1 / 1.0 | yes |

## One wrong answer (robustness)

| Fault | Wrong answer on | Navigator | Linear |
|---|---|---|---|
| seal_installed_wrong | seal_drips | 5 checks, found None | 5 checks, found None |
| air_leak_suction | sight_glass | 7 checks, found None | 7 checks, found None |

## After 3 team fixes on P-12 (all 'strainer clogged')

- Same fault again: 5 → 4 checks (5.0 → 4.5 min).
- A different fault (suction valve): 3 → 3 checks (2.5 → 3.5 min), still right. The boost costs a little when history misleads.

## Tuning disclosure

Run 1 (first engine, symmetric 0.9/0.1 answer model, 'found' = any abnormal check of the top cause, fix boost 2x per fix uncapped): Navigator 43 checks / 152 min vs linear 43 / 166, 10/10 right; after 3 strainer fixes the suction-valve fault came out WRONG (not found); one noisy case named the strainer from a shared reading.
Changed after run 1 (each is in nav_engine.py): abnormal findings weigh more than normal ones (false alarm 3%, miss 8%), 'found' needs an abnormal result that points only at that cause, fix boost +1x per fix capped at 3. Same 10 faults were used to judge the change, so these numbers are optimistic. Real faults will fall off the tree.
