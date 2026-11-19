# Draft fault trees (never loaded)

Nothing in this folder is used for diagnosis. A draft becomes a tree only when a person checks it against the manual,
writes the checks/specs, sets `"reviewed": true` and moves it up to `trees/`.

## Sta-Rite SX218 close-coupled centrifugal pump (real, public manual)
- Source [FETCHED 2026-11-13]: https://www.pentair.com/content/dam/extranet/web/nam/sta-rite/manuals/sx218-close-coupled-motor-centrifugal-pump-iom.pdf
  (local copy `web/downloads/starite_sx218_iom.pdf`, 36 pages; troubleshooting matrix on printed page 17:
  7 symptoms x 18 causes in 3 groups Electrical A-I / Mechanical A-F / System A-C, with corrective actions).
- `sta-rite-sx218.draft.json`: table parser (`nav_tree.parse_x_matrix`, no model). 50 symptom→cause links.
  Checked by eye against the rendered page image: **50/50 match** (all 7 rows).
- `sta-rite-sx218.llm-draft.json`: NVIDIA NIM Nemotron 3.5 Super VL, same page text, temperature 0.
  Against the 50 true links: **36 right, 22 extra, 14 missed (precision 62%, recall 72%)**; 35/58 cause quotes found
  verbatim on the page. Worst row: "Overheating" (5 missed). One earlier run put "No voltage in power system" under
  "Not enough water delivered" (wrong).
- Lesson: an LLM reading an X-matrix mixes up columns. Use the parser (or a human) for the links; the LLM is at most a
  helper for wording, and the review gate stays mandatory.
- Missing before this could be reviewed: the matrix has causes and remedies but no checks with spec ranges, priors,
  or lockout steps. Those have to be written from the rest of the manual. Third-party text: we cite page and URL
  and keep short quotes only; the PDF copy is for our own review.
