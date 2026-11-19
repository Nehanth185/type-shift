# type-shift: Worker OS for smart glasses (prototype)

An AI layer for display smart glasses that frontline workers use. It answers only from the plant's own manuals, with the page cited, and walks techs to the root cause of a fault one documented check at a time.

- `v0/firmware`: ESP32-S3 (XIAO Sense) glasses firmware. Compiles. Not run on hardware yet.
- `v0/server`: FastAPI server for doc-grounded answers with citations and "not in your docs" refusals. See `REAL_MODEL_RESULTS.md`.
- `v0/os`: Worker OS v0, one input router across apps (Ask, Guide, Gauge, Notes, Checklist, Handoff, Machine Ear).
- `v0/machine_ear`: listens to machines as techs walk past and flags drift before a breakdown.
- `v0/navigator`: Fault Navigator.
- `v0/mentra_app`: MentraOS mini-app front end.

Status: an early prototype. All test data is synthetic or public (MIMII). No customer data.
