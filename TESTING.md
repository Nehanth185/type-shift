# How to run all tests
From prototype/v0:
- OS (Worker OS + Navigator): `cd os && python -m pytest tests -q` (119+ pass)
- Server: `cd server && python -m pytest -q` (130 at last count)
- Companion / Mentra app harnesses: see each folder's README (mock servers, no hardware needed).
Real-model tests need the NVIDIA NIM key (not required for the mock suites).
Hardware firmware is compile-only; untested on a device.
