# Machine Ear v0 (2026-11-17): one page

**What it does:** each time a worker walks past a tagged machine, the glasses mic sends a few seconds of sound. The
server compares it with **that machine's own healthy sound**. One odd pass = `SUSPECT`. Repeated odd passes =
`ALERT`, plus which frequency band changed. **One tap** on the alert opens the **Fault Navigator** on the machine's
"Noise or vibration" branch, the same branch a worker gets by saying "P-12 noisy". When the tech saves the fix, the alert's sound
fingerprint is stored **with the confirmed cause** (the labelled library), and the machine relearns its baseline.
The score means "unlike this machine's healthy sound". It is **not** a failure probability or a diagnosis.

```
 glasses mic (3 s while walking by) + asset tag (QR/nameplate)
        │ POST /os/ear/pass {asset, audio_b64 WAV}        ← new endpoint; /os/input contract untouched
        ▼
 ear_engine.py   log-mel 64 bands → 128 numbers (spectral shape + variation; overall loudness removed)
                 Mahalanobis distance to this machine's healthy passes → percentile among healthy (5-fold calibrated)
                 odd = above the 95th healthy percentile
 walk-by state   LEARNING (first 10 passes = baseline) → LISTENING → SUSPECT (1 odd) → ALERT (3 odd of last 4)
                 SUSPECT → LISTENING after 3 normal in a row · ALERT stays until a person acts
        ▼
 HUD  "⚠ P-12 ALERT · 350-890 Hz" / "broad change: check flow, cavitation" / "Tap: troubleshoot noise ▸"
 tap  /os/input {app:"machine_ear", text:"open"} → Navigator nav.start "P-12 noisy" → Check 1 with page
 "false alarm" → LISTENING (pass not added to baseline) · "relearn" → LEARNING · Navigator fix → label + relearn
```

## Results (MIMII pump, held-out clips; baseline fit on healthy clips only) [measured here, `eval/ear_results.json`]

Data: MIMII pump **id_00**, 150 healthy clips for the baseline, then a test set of 100 healthy + 100 faulty clips. Pump **id_02**: 70 healthy for the baseline, then 40 healthy + 60 faulty
to test. 10 s clips unless noted. The 95% CI is a bootstrap over the test clips. pAUC = partial AUC at a false-alarm rate of 0-10% (DCASE style).

| Machine | Condition | AUC [95% CI] | pAUC | Odd rate healthy / faulty |
|---|---|---|---|---|
| id_00 | clean (MIMII 6 dB as published) | **0.992** [0.982-0.998] | 0.934 | 0.05 / 0.93 |
| id_00 | clean, **3 s** slice (walk-by length) | **0.983** [0.968-0.994] | 0.886 | 0.01 / 0.84 |
| id_00 | random gain ±6 dB (stood closer/farther) | 0.992 [0.982-0.998] | 0.934 | 0.05 / 0.93 |
| id_00 | **REAL plant noise**: MIMII -6 dB, baseline learned at -6 dB | **0.773** [0.703-0.834] | **0.213** | 0.02 / **0.17** |
| id_00 | REAL -6 dB, baseline from the quieter 6 dB clips | 0.762 [0.691-0.828] | 0.237 | **0.88** / 0.99 |
| id_00 | +SYNTHETIC plant noise +6 dB SNR, baseline in noise | 0.989 [0.979-0.996] | 0.906 | 0.03 / 0.90 |
| id_00 | +SYNTHETIC plant noise 0 dB, baseline in noise | 0.987 [0.976-0.995] | 0.879 | 0.05 / 0.87 |
| id_00 | +SYNTHETIC plant noise -6 dB, baseline in noise | 0.978 [0.962-0.991] | 0.826 | 0.06 / 0.86 |
| id_00 | +SYNTHETIC plant noise -6 dB, baseline clean | 0.969 [0.939-0.991] | 0.900 | **0.38** / 0.97 |
| id_00 | +real neighbour pump (id_02/06) 0 dB, baseline in noise | 0.958 [0.932-0.980] | 0.820 | 0.07 / 0.85 |
| id_02 | clean | 0.940 [0.893-0.976] | 0.633 | 0.00 / 0.48 |
| id_02 | clean, 3 s slice | 0.913 [0.857-0.958] | 0.617 | 0.00 / 0.38 |
| id_02 | +SYNTHETIC plant noise 0 dB, baseline in noise | 0.887 [0.822-0.942] | 0.571 | 0.03 / 0.52 |

**Walk-by state machine** (1,000 simulated sequences of 12 passes each, drawing held-out 3 s clips at random):

| Scenario | False ALERT, healthy machine | ALERT, faulty machine | Median passes to ALERT |
|---|---|---|---|
| id_00 clean | 0.0% | 99.9% | 3 |
| id_00 REAL -6 dB plant noise, baseline at -6 dB | 0.0% | **26%** | 7 |
| id_02 + synthetic 0 dB, baseline in noise | 0.0% | 76% | 6 |

**Other detectors on id_00 clean:** k-NN (k=5) on the same features gave 0.986. An autoencoder on 5-frame log-mel
(DCASE-2020-baseline style, sklearn MLP) gave 0.905. **Baseline size** (id_00 clean, 5 random draws): 10 healthy passes give
0.969 (min 0.956), 20 give 0.983, 40 give 0.989, 150 give 0.992. Speed: ~1.3 ms to embed a 3 s pass, ~11 ms to load and
calibrate a 150-pass baseline, all on CPU with numpy. No GPU, no model download, no PANNs (not needed at this stage).

### How to read these numbers honestly
- **The headline is the real-noise row, not the clean row.** On MIMII's own factory noise at -6 dB, AUC falls to 0.77
  and pAUC to 0.21. In the walk-by sim, only 26% of faulty sequences raise an ALERT within 12 passes. With today's
  thresholds that means "silent but mostly blind" in a loud spot.
- **My synthetic plant noise is much easier than real factory noise:** synthetic noise at -6 dB gave AUC 0.978, while real
  noise at -6 dB gave 0.773. Don't use the synthetic-noise rows to argue that it works in loud plants.
- **The baseline must be learned in place:** a baseline recorded at a quieter time marks 88% of healthy passes as odd (real -6 dB)
  or 38% (synthetic -6 dB). That would cause constant false alarms.
- **Optimism I can't remove with this data:** in MIMII, faulty clips were recorded in separate sessions from healthy clips. They
  are also ~5 dB louder on average, and loudness alone gives AUC 0.70 here. My features remove overall loudness, and the
  gain-jitter row is unchanged, but a session or setup difference could still be helping. **Small subset, two machine
  IDs of one machine type (pump), my own random split. These numbers are not comparable to DCASE leaderboards or to
  Applied Resonance's reported pump 0.880** (different subsets, SNRs and protocol).
- **How many looks at the test set:** 3. Run 1 used level-sensitive features (clean 0.997, real -6 dB mismatched 0.854). Then I ran
  a probe (gain-normalised features 0.9915, loudness alone 0.70). Run 2 is the current table. I switched to loudness-removed features
  for field reasons: on a head-worn mic, loudness mostly tracks distance. That choice costs points on this data, which I
  accept. Thresholds (95th percentile, 3 of 4) were set before the walk-by sim and not tuned on it. The one sim demo run
  had a healthy clip score in the 100th percentile, which happens. The rule absorbed it.


## v2 (2026-11-18): fan test + noise-floor subtraction. Result: mixed, so it is OPT-IN, not the default

**Fan test (new):** MIMII fan id_00 and id_02 at MIMII's 6 / 0 / -6 dB releases (80 healthy clips for the baseline, 60 healthy
+ 60 faulty held out, seeded split). Pumps: the same id_00 / id_02 splits as above, now also at 0 and -6 dB. Every cell learns
its baseline **at the same noise level** (in place). Data fetched by range requests, ~0.85 GB new, CC BY-SA 4.0.
"6 dB" is MIMII's least-noisy release; there is **no truly clean MIMII audio**, so "clean" below means 6 dB.

**What v2 is:** each pass also keeps a 64-band × 16-quantile sketch of its frame power (no time order, no audio). The
baseline pools its healthy sketches into a per-band noise floor (10th percentile) and every pass is re-described after
subtracting half that floor ("spectral subtraction learned from the baseline"). Same Mahalanobis scorer, same 95th-pct
odd line, same 3-of-4 rule. `ear_engine.embed_pass()`; switch on with `MACHINE_EAR_FEATURES=v2`.

**Protocol:** all tuning on **pump id_04 only** (never reported). Settings frozen in writing before any held-out machine
was scored (`work/ear_research/FROZEN_CONFIG.md`). Held-out cells were scored **once** with v2. 95% CI = bootstrap over test clips.

| Machine | SNR | AUC 10 s v1 → v2 [95% CI] | AUC 3 s v1 → v2 | Walk-by ALERT on faulty, 3 s passes, v1 → v2 | False ALERT healthy v1 → v2 |
|---|---|---|---|---|---|
| pump id_00 | 6 dB | 0.992 [.982-.998] → 0.991 [.980-.997] | 0.983 → 0.969 | 99.9% → 99.8% | 0% → 0% |
| pump id_00 | 0 dB | 0.893 [.847-.933] → 0.907 [.865-.942] | 0.875 → 0.864 | 91.9% → **87.0%** | 0.2% → 0.3% |
| pump id_00 | **-6 dB** | 0.773 [.703-.834] → **0.727** [.656-.791] | 0.778 → **0.698** | 26.2% → **1.8%** | 0% → 0% |
| pump id_02 | 6 dB | 0.940 [.893-.976] → 0.958 [.923-.985] | 0.913 → 0.949 | 54.6% → **96.9%** | 0% → 0% |
| pump id_02 | 0 dB | 0.874 [.787-.940] → 0.867 [.779-.934] | 0.811 → 0.856 | 46.2% → 49.9% | 0% → 0% |
| pump id_02 | **-6 dB** | 0.730 [.624-.824] → 0.717 [.615-.809] | 0.684 → 0.677 | 6.7% → **1.4%** | 0% → 0% |
| fan id_00 | 6 dB | 0.824 [.742-.893] → 0.872 [.803-.929] | 0.791 → 0.812 | 0.3% → **69.1%** | 0% → 0% |
| fan id_00 | 0 dB | 0.603 [.505-.701] → 0.663 [.565-.750] | 0.578 → 0.638 | 1.5% → 33.0% | 0% → 0.5% |
| fan id_00 | **-6 dB** | 0.625 [.518-.719] → 0.624 [.522-.720] | 0.621 → 0.609 | 5.7% → 9.3% | 0.8% → 0.5% |
| fan id_02 | 6 dB | 1.000 → 1.000 | 1.000 → 1.000 | 98.6% → 99.7% | 0% → 0% |
| fan id_02 | 0 dB | 0.923 [.867-.967] → 0.973 [.945-.995] | 0.922 → 0.894 | 18.8% → 41.4% | 0% → 0.2% |
| fan id_02 | **-6 dB** | 0.643 [.539-.740] → 0.727 [.636-.818] | 0.650 → 0.667 | 1.2% → **24.1%** | 0.7% → 1.7% |

Dev machine (pump id_04, tuning only, 3 s): -6 dB 0.846 → 0.928, walk-by 7.5% → 81.5%. **That gain did not transfer to the
held-out pumps.** The v1 numbers for pump id_00 6 dB / -6 dB reproduce yesterday's table exactly (0.992 / 0.773).

**Plain reading:**
- **Fans:** v2 helps at every noise level (walk-by detection up on 5 of 6 cells, often from near zero). But fan id_00 is
  hard for both (AUC ~0.62 at -6 dB, near chance at the low end of the CI), and fan id_02 at 6 dB is trivially separable (1.000),
  which smells of a recording-session difference rather than an easy fault.
- **Pumps in real -6 dB noise (the target):** v2 is **worse** on both held-out pumps. The CIs overlap, but the walk-by
  rate collapses (26% → 2%). So v2 cannot be the default.
- **At -6 dB nothing here is usable for a walk-by alert:** best held-out walk-by detection is 26% (pump id_00, v1) and
  24% (fan id_02, v2). Mostly silent, mostly blind, same as yesterday's conclusion.
- Why not "v2 for fans, v1 for pumps"? That rule was read off the test table. Choosing it now would be tuning on the test set.
  A fair test needs a third machine type or new IDs (fan id_04 / id_06 are not downloaded yet).

**Tried and rejected on the dev machine** (pump id_04 at -6 dB, 3 s, AUC; v1 = 0.846): per-band 10/50/90th percentile
features 0.836; fine 3.9 Hz spectrum (narrowband/harmonic lines) with k-NN 0.819, top-z 0.788, PCA 0.656; envelope modulation
spectrum ≤ 0.66; k-NN on v1 features 0.831; rank fusions of these ≤ 0.849; per-pass min-statistics subtraction (no baseline)
0.863-0.866; frame-level Gaussian on log-mel frames 0.895; fusing v1 + v2 z-scores 0.904 (and walk-by fell to 9%, so worse
than v2 alone); "evidence" rule (ALERT when the mean z of the last 4 passes ≥ T): +0-10 points of walk-by detection with v2 at
-6 dB but more false ALERTs at 6 dB (1.5% vs 0.3% at T=1.75), so it ships OFF (`EVID_T=None`). **Longer passes:**
10 s instead of 3 s adds ~0-0.08 AUC at -6 dB (v1 dev 0.846 → 0.880; held-out pump id_02 0.684 → 0.730). **Pretrained
embeddings (PANNs/BEATs):** not tried. No torch in this sandbox; CNN14 is ~80M parameters, which is not CPU-light on glasses or a
phone; PANNs code/weights are MIT, BEATs MIT (repo) [from memory, not re-checked today]. Worth a server-side test later.

**Speed (CPU, numpy, 2-core sandbox):** per 3 s pass, embed v1 ≈ 0.7 ms, v2 ≈ 1.2-1.4 ms; score ≈ 0.02 ms (v1) / 0.05 ms (v2);
fit + 5-fold calibrate a baseline 6-58 ms (v2 slower: floor and refits). A v2 baseline JSON is ~7.5 KB per pass (150 passes ≈ 1.1 MB),
v1 ≈ 1 KB per pass.

**How many looks at the test data (v2 work):** each held-out cell was scored **once** with v2 (one resumable run). After the first
cells (pump id_00 -6 dB worse) I tested one more idea (v1 + v2 fusion) **on the dev machine only**, rejected it there, and did
not re-score the test set. Pump id_00 has now been scored 4 times in total since 2026-11-17 (3 before, see above, + this run).

## Real vs simulated
| Real | Simulated / not done |
|---|---|
| MIMII recordings (real pumps, real factory background, real faults: leakage, contamination, clogging) | Glasses mic: never tested. MIMII used an 8-mic array about 50 cm from the machine; we use channel 0 |
| Engine, calibration, state machine, API, Navigator handoff, label-on-fix, all running in the server | "Walking past": a fixed clip, cut to 3 s. No footsteps, head turns, or Doppler. Distance only as ±6 dB gain |
| MIMII -6 dB set = real plant noise (from the dataset authors' factories) | Plant noise at other SNRs = my synthetic pink + hum + impacts |
| | Gradual wear (drift over weeks): MIMII faults are present or absent, with no trend data |
| | Band → "where to look" hint is hand-written and unreviewed. It does NOT steer Navigator's check order |

## Open risks
1. **Raw mic access on glasses (biggest unknown).** Voice-tuned mics run noise suppression and beamforming that may remove
   machine sound, and many platforms don't give third-party apps raw PCM. Not checked for Mentra / Even G2 / Meta [estimate].
   A phone in a chest pocket or a clip-on mic is the fallback.
2. **Loud plants (85-100 dB).** Still unsolved (v2 section): at MIMII's real -6 dB, held-out AUC is 0.62-0.77 and walk-by
   detection ≤ 26% for pumps and fans, with either feature set. Tried 2026-11-18: noise-floor subtraction (helps fans, hurts
   pumps), narrowband/fine spectrum, modulation, k-NN, fusion, evidence accumulation, longer passes. Untested: directional or
   contact/close mic, multi-mic beamforming (MIMII has 8 channels; we use 1), pretrained embeddings server-side. A 3-second
   walk-by may only work near the machine or in quieter areas.
3. **Mic on a moving head.** Distance, angle and the worker's own noise (steps, breath, talking, radio) change every pass.
   Gain is handled, but direction and self-noise are not. We need a quality gate that rejects a pass when the worker talks or
   the level is far off.
4. **Which machine is it?** We rely on the asset tag in context (QR / nameplate). A wrong tag poisons that machine's baseline.
5. **Baseline poisoning.** LEARNING assumes the machine is healthy for its first 10 passes. If it isn't, the fault becomes
   "normal". The UI says so ("Only do this if it's healthy").
6. **Dataset license.** MIMII is CC BY-SA 4.0: commercial use is OK with attribution, and ShareAlike applies to adaptations.
   Whether a trained model is an "adaptation" is unclear, so ask a lawyer before selling a model trained on it [estimate]. Do not
   use the DCASE 2020 dev set commercially: it is CC BY-NC-SA 4.0 [FETCHED, see `web/downloads/mimii/README.md`].
7. **Surveillance optics.** Passes store no worker id and no audio, only a 128-number fingerprint, so it can't become a
   "where was Bob" log. Keep it that way.
8. **Overclaiming.** "Predictive" implies lead time. We have shown detection of an existing fault, not early warning.

## Files
| File | What |
|---|---|
| `ear_engine.py` | Features, Baseline (Mahalanobis + 5-fold calibration + band explain), `step()` state machine, noise mixing, synthetic machine sound. numpy only |
| `ear_app.py` | App `machine_ear` + `/os/ear/pass`, `/status`, `/labels`, `/demo/seed`, `/demo/clips`, `/demo/audio/…`, `/sim`. Tables `ear_assets`, `ear_passes`, `ear_labels` |
| `tests/test_machine_ear.py` | 23 tests: engine, state machine, API, tap → Navigator, false alarm/relearn, fix → label, no audio/worker stored, HUD limits, MIMII walk-by |
| `tests/test_machine_ear_v2.py` | 11 tests: v2 sketch shape/gain/no time order, sketch subtraction ≈ framewise, v2 baseline + JSON + dim guard, v1 baselines still load, evidence rule off by default, app default v1, opt-in v2, old v1 machine keeps scoring |
| `ear_tools/ear_eval_v2.py` → `eval/ear_v2_cells.json` + `ear_results.json["v2"]` | v1 vs v2, pump + fan, 6/0/-6 dB (resumable) |
| `ear_tools/fetch_mimii.py`, `remote_zip.py` | Free subset download via HTTP range requests (no full 8 GB zip) |
| `ear_tools/ear_eval.py` → `eval/ear_results.json` | The tables above (`--ae` adds the autoencoder) |
| `ear_tools/split_pump_id00.json`, `split_pump_id02.json` | Fixed train/test splits |
| `../../../simulator/machine-ear-sim.html` (`/os/ear/sim`) | Worker walks past P-12 (MIMII) and P-14 (synthetic). Pass table, HUD, alert → Navigator |
| `sim_ear_check.py` → `sim_machine_ear.png`, `sim_machine_ear_alert_hud.png` | Browser check + screenshots |
| `../../../web/downloads/mimii/` (+ README with license) | The audio subset (~276 MB, not for git) |
| edits | `apps.py` +1 import line, `api.py` +1 `include_router` line, `tests/test_apps.py` app list +`machine_ear`. Nothing else in core changed |

**Contract notes for Nehanth (core owner):** audio does not go through `/os/input`, because that contract is text + photo + context.
It has its own endpoint. The manifest declares `docs.read` (it opens Navigator). The core has no `microphone` permission yet,
which is worth adding when you next touch core. Without numpy the server still starts, and `/os/ear/*` answers 503.

## Tests (run 2026-11-18, after v2)
OS **157 pass** (146 + 11 new v2 tests) · server **131 pass** · `tests/test_machine_ear.py` also passes with `MACHINE_EAR_FEATURES=v2`.
Browser sims not re-run (app default unchanged).

## Tests (run 2026-11-17)
OS **146 pass** (was 123; +23 Machine Ear) · server **131 pass** (unchanged) · integration mock `--skip-browser` 49 pass /
0 fail · `sim_check.py` PASS · `sim_nav_check.py` PASS · `sim_ear_check.py` PASS (Chromium needed 4 system libs installed:
libatk1.0-0, libxcomposite1, libxdamage1, libatspi2.0-0).

## Run it
```
cd prototype/v0/os && python ear_tools/fetch_mimii.py --budget 100          # repeat until 350/350; then the -6 dB / id_02 lines in the README
python ear_tools/ear_eval.py                                                # tables (~40 s)
cd ../server && SEED_DEMO=1 python server.py  →  http://localhost:8000/os/ear/sim  → ▶ Walk-by demo   (?notap stops at the ALERT)
```

## Next steps (free)
1. **$0 live moment:** record a desk fan on a phone (healthy for 10 passes, then tape a coin to a blade) and post the WAVs to
   `/os/ear/pass`. This is the first test on a real consumer mic.
2. **Raw-mic check** on whatever glasses we can borrow or emulate (MentraOS SDK docs first): can an app get unprocessed PCM?
3. **Fix the real-noise gap:** try pretrained embeddings on CPU (BEATs/PANNs) on the same real -6 dB split. Add a pass-quality gate
   (speech/level). Adapt the baseline per location.
4. **Drift data:** find a run-to-failure acoustic set (bearing run-to-failure sets exist for vibration; acoustic is rarer)
   to test early warning, not just detection.
5. Let the band hint pick the **first check** inside the noise branch (bearing vs cavitation). That needs a small Navigator
   change (founder approval), plus a reviewed mapping.
