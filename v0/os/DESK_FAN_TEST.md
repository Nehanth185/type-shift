# Desk-fan test: Machine Ear on a real microphone

**Status: NOT RUN.** Nobody has done this test yet. The quality gate (`ear_gate.py`) and the replay harness
(`ear_tools/replay.py`) have only been run on synthetic sound and public recordings (see "What is untested").
This page is how to run it for the first time.

What it checks: the system learns a healthy fan from 10 passes, stays quiet on more healthy passes, and raises an
ALERT when a coin is taped to one blade.

You need: a desk fan, a coin, tape, a laptop or phone, Python with the server requirements installed.

## 1. Record the healthy fan (about 75 seconds)

One pass is 3 seconds. 10 healthy passes are needed to learn, and more are needed to check for false alarms, so record
**75 seconds** (25 passes). That leaves room for a few rejected passes.

1. Put the fan on a table, set one speed, and **do not change the speed** for the whole test.
2. Put the mic 30 to 50 cm from the fan, to the side, **out of the air stream** (wind on the mic ruins the recording).
   Mark the spot: the faulty recording must be made from the same spot.
3. Do not talk, type or move the mic while recording. Turn off music and notifications.
4. Record 75 seconds and save it as `healthy.wav`:
   - **Linux laptop:** `arecord -f S16_LE -r 16000 -c 1 -d 75 healthy.wav`
   - **Mac laptop:** record with QuickTime Player (File > New Audio Recording), save as `healthy.m4a`, then
     `afconvert -f WAVE -d LEI16@16000 -c 1 healthy.m4a healthy.wav`
   - **Windows laptop:** Sound Recorder, set the format to WAV in its settings, save as `healthy.wav`.
   - **Phone:** use the voice recorder app. If it has a "lossless" or WAV setting, turn it on. Copy the file to the
     laptop. If it is not a WAV (for example `.m4a`), convert it: on a Mac use the `afconvert` line above; otherwise
     `ffmpeg -i healthy.m4a -ac 1 -ar 16000 -c:a pcm_s16le healthy.wav` (ffmpeg is not installed by this repository).

The harness reads PCM WAV only (8, 16 or 32-bit, any sample rate). It does not read m4a, mp3 or 24-bit WAV.

## 2. Tape the coin and record the faulty fan (about 45 seconds)

1. **Switch the fan off and unplug it.** Wait until the blades stop.
2. Remove the front guard if you can. Tape one small coin flat near the tip of **one** blade. Wrap the tape fully
   around the blade so the coin cannot fly off.
3. **Put the guard back on.** Never run the fan with a taped coin and no guard. Stand to the side when you start it.
4. Plug in, same speed, mic on the same spot. If the fan shakes hard or walks on the table, stop and use a smaller coin.
5. Record 45 seconds (15 passes) the same way and save it as `faulty.wav`.
6. Unplug the fan and remove the coin.

## 3. Run it

Terminal 1, start the server (mock mode, no key needed):

```bash
cd v0/server
pip install -r requirements.txt
AUTH_MODE=off python server.py
```

Terminal 2, replay the two recordings, healthy first:

```bash
cd v0/os
python ear_tools/replay.py /path/to/healthy.wav --asset desk-fan-1 --expect healthy
python ear_tools/replay.py /path/to/faulty.wav  --asset desk-fan-1 --expect faulty
```

To repeat the test, use a new name (`--asset desk-fan-2`): a machine keeps its baseline on the server.
If the server is on another port or machine, add `--server http://host:port`.

## 4. Read the result

Each run prints one line per pass, then a summary: passes sent, passes rejected and why, the anomaly score of every
pass, and a last line `RESULT: PASS` or `RESULT: FAIL`. Exit code: 0 pass, 1 fail, 2 could not run.

The test **passes** only if both runs print `RESULT: PASS`:

| Run | PASS means | FAIL means |
|---|---|---|
| `healthy.wav --expect healthy` | 10 passes were learned and the healthy fan never reached ALERT | fewer than 10 passes got through, or a healthy fan raised an ALERT (false alarm) |
| `faulty.wav --expect faulty` | the fan with the coin reached ALERT (3 odd passes in the last 4) | no ALERT: the fault was missed |

Also look at these two things. They do not change PASS/FAIL, but report them:

- **Rejected passes.** With a quiet room and no talking, no pass should be rejected. If healthy passes are rejected, the
  printed reason says which check fired (`talking`, `clipping`, `level_too_low`, `level_too_high`,
  `level_off_reference`). A reject on a clean recording means the gate thresholds are wrong for your mic.
- **Scores.** Healthy passes after the 10th should read `normal` with a healthy-percentile under 0.95. Faulty passes
  should read `ODD`. The score is "how unlike this fan's healthy sound", not a fault probability.

Optional gate check: record 15 seconds of the healthy fan while you talk next to the mic, and replay it with
`--asset desk-fan-1` and no `--expect`. Expected: the talking passes are `REJECTED  talking`.

If a faulty pass is rejected with `level_off_reference`, the coin made the fan more than 12 dB louder or quieter than
healthy. The gate then treats it as a mic problem and does not score it. That is a known limit; report it.

## What is untested

- **Real microphone:** not tested. No laptop, phone or glasses mic recording has been through the gate or the harness.
- **Real fan, real coin fault:** not tested. This whole page is unverified instructions.
- **Recording commands** (`arecord`, `afconvert`, QuickTime, Sound Recorder, phone apps, `ffmpeg`): not run by us.
- **Phone audio processing** (automatic gain, noise suppression, m4a compression): effect on the scores unknown.
- **Gate thresholds:** set on synthetic machine sound, 240 public ESC-50 clips of machines (vacuum cleaner, washing
  machine, engine, train, airplane, helicopter) and 61 three-second clips of public read speech (Open Speech
  Repository, 8 kHz). Those clips were used to choose the thresholds, so the numbers below are not an independent
  test: 1 of 239 machine clips was flagged as talking; talking was caught in 57 of 61 speech clips alone, 53 of 61 with
  speech as loud as a synthetic machine, 41 of 61 with speech 5 dB quieter, 1 of 61 with speech 10 dB quieter.
  None of these files are in the repository. MIMII could not be downloaded for this work.
- **The server itself does not run the gate.** Only this harness does. Any other client that posts straight to
  `/os/ear/pass` (the simulator page, the glasses app) is not gated yet.

What was run: the automated tests in `tests/test_ear_gate_replay.py` (synthetic audio, real server code in mock mode),
and the two commands above against a local server with synthetic WAV files.
