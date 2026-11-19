# Navigator coverage report (G83)

Auto-generated from trees/*.json, 2026-11-15. Lists each symptom and checks/fixes count; flags thin branches.


## ac20 (reviewed=True)

| symptom | checks | fixes | flag |
|---|---|---|---|
| Slow pressure build | 5 | 5 |  |
| High temperature trip | 4 | 4 |  |

## cp450 (reviewed=True)

| symptom | checks | fixes | flag |
|---|---|---|---|
| Low pressure or flow | 6 | 6 |  |
| Pump leaking | 5 | 5 |  |
| Motor trips or runs hot | 4 | 4 |  |
| Noise or vibration | 4 | 4 |  |

## tm5_pump (reviewed=False)

| symptom | checks | fixes | flag |
|---|---|---|---|
| No water or low pressure | 4 | 4 |  |
| Pump fails to function | 1 | 1 | THIN |
| Pump leaks in operation | 1 | 1 | THIN |

Known gap: tm5 'cavitation noise' not in the manual, so no tree. Keys: ['id', 'name', 'doc', 'page', 'quote', 'phrases', 'causes']