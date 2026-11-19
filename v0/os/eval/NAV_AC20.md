# AC-20 second-machine test (SYNTHETIC, one run, untuned)

Fictional compressor tree, same engine, no code changes. Answers follow the tree itself, so this tests logic and format, not field speed.

| Planted fault | Nav checks/min | Linear checks/min | Nav right |
|---|---|---|---|
| filter_clog | 4 / 10.5 | 1 / 7.0 | yes |
| drain_open | 2 / 2.5 | 3 / 13.5 | yes |
| check_valve | 3 / 3.5 | 4 / 14.5 | yes |
| line_leak | 4 / 6.5 | 5 / 17.5 | yes |
| rings | 5 / 17.5 | 5 / 17.5 | NO |
| fins_blocked | 4 / 8.0 | 1 / 6.5 | yes |
| low_oil | 1 / 0.5 | 2 / 7.0 | yes |
| fan_dead | 2 / 1.0 | 3 / 11.5 | yes |
| hot_room | 3 / 1.5 | 4 / 12.0 | yes |

**Totals: Nav 28 checks / 51.5 min, 8/9 right. Linear 28 / 107.0, 8/9.**

## Finding
"rings" fails for both arms: its only abnormal check (slow pump-up) is shared with filter_clog and check_valve, so no check is exclusive to it and the engine refuses to declare it found. That is correct behavior and a tree-authoring gap: a cause needs one check only it fails. Next: add a validator warning for causes with no exclusive check.
