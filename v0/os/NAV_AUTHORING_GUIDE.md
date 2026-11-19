# Fault Tree Authoring Guide (for senior techs)
Bottom line
1. A tree = symptoms -> likely causes -> checks. Each cause needs one check only it can fail (an "exclusive" check), or Navigator can't confirm it.
2. Every quote and number must be on the cited manual page. Validator rejects otherwise.
3. Put safety (lockout/tagout, run-state) gates before any check that needs them.
4. A human reviews before `reviewed:true`; drafts from AI never load.

## Steps
| Step | Do |
|---|---|
| 1 | Pick one symptom (e.g. "conveyor belt tracks off") |
| 2 | List 4-8 causes, most common first |
| 3 | For each cause write a check: what to do, tool, time (min), expected reading |
| 4 | Make sure each cause has a check that others would pass |
| 5 | Cite manual page + quote |
| 6 | Run nav_tree.validate and nav_tree.warnings; fix flags |
| 7 | Review, set reviewed:true |

## Conveyor/motor tree skeleton (todo: real manual page needed)
Symptoms: belt tracks off; motor trips overload; belt slips. Causes: misaligned roller, worn lagging, low tension, bearing seized, overload setting wrong, jammed load. [EST: authoring a real tree needs a fetched manual; not done]
