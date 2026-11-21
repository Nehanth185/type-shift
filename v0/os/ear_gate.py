"""Machine Ear pass quality gate (D4): decide whether one walk-by pass is fit to be scored or learned from.
numpy only. Runs BEFORE a pass is sent to /os/ear/pass (see ear_tools/replay.py), so a rejected pass never reaches the
server and can never change a machine's healthy baseline. Nothing in the engine, the app or the core OS is changed.

A pass is rejected, with the reason printed, when it has:
  talking    voiced speech on top of the machine sound. Per 64 ms frame: take what stands out above the pass's own
             steady spectrum (per-bin median over the pass = the machine), and look for a harmonic comb with a pitch of
             75-400 Hz. Talking = runs of such frames whose pitch glides (noise gives isolated frames with random
             pitch) AND a pitch that moves over the pass (a steady machine tone does not).
  clipping   samples stuck at full scale, or flat tops (runs of equal samples at the pass's peak).
  level      too quiet (mic covered, muted, too far), too loud, or far from this machine's reference level.
             The reference is the median level of the ACCEPTED healthy learning passes (LevelRef); rejected passes
             never enter it.
Limits: UNTESTED on a real microphone and a real machine. Thresholds were chosen on synthetic machine sound and on
public clips kept outside the repository (ESC-50 machine sounds, Open Speech Repository read speech; numbers in
DESK_FAN_TEST.md). Talking much quieter than the machine is not detected. A fault that makes the machine more than
REF_TOL_DB louder or quieter is rejected as a level problem instead of being scored (the reason says so).
The server does not call this gate: only ear_tools/replay.py does.
"""
from dataclasses import dataclass, field

import numpy as np

SR = 16000
TALKING, CLIPPING, LEVEL_LOW, LEVEL_HIGH, LEVEL_OFF, TOO_SHORT = (
    "talking", "clipping", "level_too_low", "level_too_high", "level_off_reference", "too_short")

MIN_SECONDS = 1.0        # shorter than this cannot be judged
MIN_DBFS = -55.0         # RMS below this = no usable signal
MAX_DBFS = -6.0          # RMS above this = overdriven
REF_TOL_DB = 12.0        # allowed distance from the machine's reference level (the engine is level-blind to +-6 dB)
REF_MIN_PASSES = 3       # the reference is used once this many accepted learning passes exist
CLIP_ABS = 0.985         # |sample| at or above this counts as full scale
CLIP_FRAC = 0.001        # share of full-scale samples that means clipping (0.1% = 48 samples in 3 s)
FLAT_RUN = 3             # equal consecutive samples at the peak = a flat top
FLAT_MIN_PEAK = 0.25     # flat tops are only looked for when the peak is at least this (-12 dBFS)
FLAT_FRAC = 0.0005       # share of flat-top samples that means clipping below full scale
N_FFT, HOP, PAD = 1024, 256, 4096      # 64 ms frames every 16 ms, zero-padded for a fine pitch grid
BAND_HZ = (60.0, 2500.0)               # where voiced harmonics are looked for
F0_HZ = (75.0, 400.0)                  # speaking pitch range
SUB = 3.0                # a bin "stands out" when its power is above SUB x the pass's median for that bin
ACTIVE = 0.5             # frame is active when standing-out power >= ACTIVE x the steady power in the band
HARM = 0.5               # frame is voiced when the harmonic-comb strength (0..1) reaches this
PITCH_JUMP = 0.10        # frame-to-frame pitch change allowed inside one voiced run
RUN_FRAMES = 6           # a voiced run is at least this many frames (~0.1 s)
TALK_SECONDS = 0.25      # voiced time (in runs) in a pass that means talking
PITCH_SPREAD = 0.025     # ...and the pitch must move by at least this (relative IQR); a machine tone does not


@dataclass
class Verdict:
    ok: bool
    reasons: list = field(default_factory=list)     # reason codes, e.g. ["talking", "clipping"]
    notes: list = field(default_factory=list)       # one printable sentence per reason
    level_db: float = float("-inf")                 # RMS level, dBFS (DC removed)
    metrics: dict = field(default_factory=dict)

    @property
    def why(self):
        return "; ".join(self.notes) if self.notes else "ok"


class LevelRef:
    """This machine's reference level = median level of ACCEPTED healthy learning passes. add() is only ever called
    for a pass the gate accepted and the server learned from, so a rejected pass cannot move it."""

    def __init__(self, levels=None):
        self.levels = [float(v) for v in (levels or [])]

    def add(self, level_db):
        self.levels.append(round(float(level_db), 2))

    @property
    def value(self):
        return float(np.median(self.levels)) if len(self.levels) >= REF_MIN_PASSES else None


def level_dbfs(x):
    x = np.asarray(x, np.float64)
    if not len(x): return float("-inf")
    return float(10 * np.log10(np.mean((x - x.mean()) ** 2) + 1e-12))


def clip_stats(x):
    """(share of samples at full scale, share of samples inside flat tops at the pass's own peak, peak)."""
    x = np.asarray(x, np.float32)
    a = np.abs(x)
    peak = float(a.max()) if len(a) else 0.0
    full = float(np.mean(a >= CLIP_ABS)) if len(a) else 0.0
    flat = 0.0
    if peak >= FLAT_MIN_PEAK and len(x) > FLAT_RUN:
        top = a >= peak - 1.5 / 32768                     # within one 16-bit step of the peak
        eq = top[1:] & top[:-1] & (x[1:] == x[:-1])       # neighbours stuck at the same top value
        d = np.diff(np.concatenate([[0], eq.astype(np.int8), [0]]))
        runs = np.flatnonzero(d == -1) - np.flatnonzero(d == 1) + 1       # samples in each stuck run
        flat = float(runs[runs >= FLAT_RUN].sum() / len(x))
    return full, flat, peak


def voiced_frames(x):
    """Per frame: (active, comb strength 0..1, pitch Hz). 'Active' and the comb are measured on what stands out above
    the pass's own steady spectrum, so a steady machine (tones included) gives nothing."""
    x = np.asarray(x, np.float32)
    if len(x) < N_FFT: x = np.pad(x, (0, N_FFT - len(x)))
    n = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    P = np.abs(np.fft.rfft(x[idx] * np.hanning(N_FFT).astype(np.float32), PAD, axis=1)) ** 2
    hz = np.fft.rfftfreq(PAD, 1 / SR)
    band = (hz >= BAND_HZ[0]) & (hz <= BAND_HZ[1])
    P = P[:, band].astype(np.float64)
    steady = np.median(P, axis=0) + 1e-12
    over = np.maximum(P - SUB * steady, 0.0)
    active = over.sum(1) / steady.sum()
    W = np.zeros((n, PAD // 2 + 1))
    w = np.log1p(over / (SUB * steady))                   # how far each bin stands out, compressed
    W[:, band] = w - w.mean(1, keepdims=True)
    cep = np.fft.irfft(W, PAD, axis=1) * (PAD / 2)        # peaks spaced f0 apart along frequency -> peak at lag SR/f0
    cep = cep / (np.maximum(W, 0).sum(1, keepdims=True) + 1e-9)       # ~1 for a clean comb, ~0 for noise
    lo, hi = int(SR / F0_HZ[1]), int(SR / F0_HZ[0])
    short = cep[:, int(SR / 1500):lo].max(1)              # a peak here = pitch above the speaking range (tone, whistle)
    seg = cep[:, lo:hi + 1]
    best = seg.max(1)
    first = np.array([np.argmax(r >= 0.85 * b) if b > 0 else 0 for r, b in zip(seg, best)])   # lowest strong lag
    comb = np.where(short >= 0.85 * best, 0.0, np.maximum(best, 0.0))
    return active, comb, SR / (lo + first)


def talking(x):
    """(is talking, metrics). A frame is voiced when it is active and has a harmonic comb in the speaking pitch range.
    Only RUNS of voiced frames whose pitch glides (no jumps) count: noise gives isolated frames with random pitch."""
    active, comb, f0 = voiced_frames(x)
    v = (active >= ACTIVE) & (comb >= HARM)
    keep = np.zeros(len(v), bool)
    i = 0
    while i < len(v):
        if not v[i]:
            i += 1; continue
        j = i
        while j + 1 < len(v) and v[j + 1] and abs(f0[j + 1] / f0[j] - 1) <= PITCH_JUMP: j += 1
        if j - i + 1 >= RUN_FRAMES: keep[i:j + 1] = True
        i = j + 1
    secs = float(keep.sum() * HOP / SR)
    spread = 0.0
    if keep.any():
        q1, q2, q3 = np.percentile(f0[keep], [25, 50, 75])
        spread = float((q3 - q1) / q2)
    m = {"voiced_s": round(secs, 2), "pitch_spread": round(spread, 3),
         "pitch_hz": round(float(np.median(f0[keep])), 0) if keep.any() else None}
    return secs >= TALK_SECONDS and spread >= PITCH_SPREAD, m


def check(x, ref_db=None):
    """Judge one pass (float mono in [-1, 1] at 16 kHz). ref_db: this machine's reference level (LevelRef.value) or
    None. Returns a Verdict; verdict.ok is False when any check fails, and verdict.notes says why in plain words."""
    x = np.asarray(x, np.float32)
    v = Verdict(ok=True)
    if len(x) < MIN_SECONDS * SR:
        v.ok = False; v.reasons.append(TOO_SHORT)
        v.notes.append(f"too short ({len(x) / SR:.1f} s, need {MIN_SECONDS:.0f} s)")
        return v
    v.level_db = lv = level_dbfs(x)
    full, flat, peak = clip_stats(x)
    v.metrics.update(level_db=round(lv, 1), clip_full=round(full, 5), clip_flat=round(flat, 5), peak=round(peak, 3))
    if full >= CLIP_FRAC or flat >= FLAT_FRAC:
        v.reasons.append(CLIPPING)
        v.notes.append(f"clipping ({100 * max(full, flat):.2f}% of samples at the limit, peak {peak:.2f})")
    if lv < MIN_DBFS:
        v.reasons.append(LEVEL_LOW)
        v.notes.append(f"level too low ({lv:.0f} dBFS, need at least {MIN_DBFS:.0f}): mic covered, muted or too far")
    elif lv > MAX_DBFS:
        v.reasons.append(LEVEL_HIGH)
        v.notes.append(f"level too high ({lv:.0f} dBFS, limit {MAX_DBFS:.0f}): mic too close or gain too high")
    elif ref_db is not None and abs(lv - ref_db) > REF_TOL_DB:
        v.reasons.append(LEVEL_OFF)
        v.notes.append(f"level {lv - ref_db:+.0f} dB from this machine's healthy reference ({ref_db:.0f} dBFS, "
                       f"allowed +-{REF_TOL_DB:.0f}): mic moved, or the machine itself got much louder/quieter")
    if LEVEL_LOW not in v.reasons:                         # nothing to hear in a silent pass
        talk, m = talking(x)
        v.metrics.update(m)
        if talk:
            v.reasons.append(TALKING)
            v.notes.append(f"talking ({m['voiced_s']:.1f} s of voiced speech, pitch ~{m['pitch_hz']:.0f} Hz)")
    v.ok = not v.reasons
    return v
