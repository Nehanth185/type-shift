"""Machine Ear engine: healthy-sound baseline per machine -> anomaly score per walk-by pass -> state machine.
numpy only, CPU, no model download. Imported lazily by ear_app.py so the server still starts without numpy
(the slim Render install leaves numpy out; Machine Ear then answers 503).

  features   log-mel (64 bands, 50 Hz-8 kHz, 1024-pt FFT, hop 512 @ 16 kHz) -> per-band mean (overall level removed)
             + per-band std over the pass = 128 numbers (v1, embed()).
             v2 (embed_pass(), 2026-11-18, OPT-IN via MACHINE_EAR_FEATURES=v2; app default stays v1): the same 128 numbers PLUS a 64-band x 16-quantile
             sketch of the pass's frame power (no time order, no audio). The baseline pools its healthy sketches into a
             per-band NOISE FLOOR (10th percentile), and every pass is re-described with half that floor subtracted
             before the log ("spectral subtraction learned from the baseline"). Held-out MIMII: better on fans and on
             pumps in mild noise, WORSE on pumps in real -6 dB noise. Not a general win; see MACHINE_EAR.md (v2).
  baseline   ~10-150 healthy passes of ONE machine: standardise, shrunk covariance, Mahalanobis distance
  calibrate  5-fold: each healthy pass scored by a baseline that never saw it -> the score's percentile among healthy
             passes. A pass is "odd" when it is above the 95th healthy percentile (per-pass false alarm ~5% by design)
  walk-by    LEARNING (building baseline) -> LISTENING -> SUSPECT (1 odd pass) -> ALERT (3 odd in the last 4 passes,
             or, v2, the average evidence of the last 4 passes >= EVID_T: several "slightly odd" passes add up)
             SUSPECT -> LISTENING after 3 normal passes in a row. ALERT stays until a person clears it.
  explain    the mel bands whose level moved most vs the baseline -> "2-4 kHz louder". A hint, not a diagnosis.
The score is "how unlike this machine's own healthy sound", not a failure probability.
"""
import io, wave

import numpy as np

SR = 16000
N_FFT, HOP, N_MELS, FMIN, FMAX = 1024, 512, 64, 50.0, 8000.0
ODD_PCT = 0.95          # a pass above this healthy percentile counts as odd
ALERT_K, ALERT_N = 3, 4  # ALERT when >= K odd among the last N passes
CLEAR_N = 3             # SUSPECT -> LISTENING after this many normal passes in a row
MIN_BASELINE = 10       # passes needed before LISTENING
SHRINK = 0.2            # covariance shrinkage toward the diagonal
# v2 noise handling (tuned on MIMII pump id_04 only; never on the reported machines)
N_Q = 16                 # quantiles kept per band in the pass sketch
FLOOR_Q = 10.0           # baseline noise floor = this percentile of pooled healthy frame power, per band
SUB_ALPHA, SUB_BETA = 0.5, 0.02   # subtract alpha*floor; never below beta*original power
EVID_N, EVID_T = 4, None  # evidence rule: mean z of last EVID_N passes >= EVID_T -> ALERT (None = off; set below)
_QS = np.linspace(2.5, 97.5, N_Q)
V1_DIM = 2 * N_MELS
V2_DIM = V1_DIM + N_MELS * N_Q


# ------------------------------------------------------------------ audio
def read_wav(data):
    """bytes or path of a PCM WAV -> float32 mono at 16 kHz in [-1, 1]. Other rates are resampled (linear)."""
    w = wave.open(io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data)
    ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(n)
    if sw == 2: x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif sw == 4: x = np.frombuffer(raw, "<i4").astype(np.float32) / 2 ** 31
    elif sw == 1: x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    else: raise ValueError(f"unsupported sample width {sw}")
    x = x.reshape(-1, ch)[:, 0]
    if sr != SR:
        t = np.arange(int(len(x) * SR / sr)) * sr / SR
        x = np.interp(t, np.arange(len(x)), x).astype(np.float32)
    return x


def to_wav(x, sr=SR):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return b.getvalue()


# ------------------------------------------------------------------ features
def _hz2mel(f): return 2595 * np.log10(1 + f / 700)
def _mel2hz(m): return 700 * (10 ** (m / 2595) - 1)


_FB = None
EDGES_HZ = _mel2hz(np.linspace(_hz2mel(FMIN), _hz2mel(FMAX), N_MELS + 2))   # band b spans EDGES_HZ[b]..EDGES_HZ[b+2]


def mel_fb():
    global _FB
    if _FB is None:
        bins = np.fft.rfftfreq(N_FFT, 1 / SR)
        fb = np.zeros((N_MELS, len(bins)), np.float32)
        for b in range(N_MELS):
            lo, c, hi = EDGES_HZ[b], EDGES_HZ[b + 1], EDGES_HZ[b + 2]
            fb[b] = np.clip(np.minimum((bins - lo) / (c - lo), (hi - bins) / (hi - c)), 0, None)
        _FB = fb
    return _FB


def melpow(x):
    x = np.asarray(x, np.float32)
    if len(x) < N_FFT: x = np.pad(x, (0, N_FFT - len(x)))
    n = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(N_FFT).astype(np.float32), axis=1)) ** 2
    return spec @ mel_fb().T                                   # frames x 64, linear power


def logmel(x):
    return 10 * np.log10(melpow(x) + 1e-10)                    # frames x 64, dB


def embed(x):
    """One pass of audio -> 128 numbers: the spectral SHAPE (per-band mean of the log-mel minus the pass's overall
    level) and per-band std over time. Overall loudness is removed on purpose: on a head-worn mic it mostly says how
    far you stood from the machine, not how healthy it is. NOTE: chosen after a first level-sensitive run had been
    scored on the test clips (see MACHINE_EAR.md, 'How many looks at the test set')."""
    m = logmel(x)
    mean = m.mean(0)
    return np.concatenate([mean - mean.mean(), m.std(0)]).astype(np.float64)


def _shape(m):
    mean = m.mean(0)
    return np.concatenate([mean - mean.mean(), m.std(0)])


def embed_pass(x):
    """v2: one pass -> V2_DIM numbers = embed(x) (128) + per-band power quantile sketch in dB (64 x 16, level removed).
    Quantiles keep no time order, so no speech or audio can be rebuilt from them."""
    P = melpow(x)
    m = 10 * np.log10(P + 1e-10)
    P = P / (P.mean() + 1e-12)
    sk = 10 * np.log10(np.percentile(P, _QS, axis=0).T + 1e-10)    # 64 x N_Q, dB
    return np.concatenate([_shape(m), sk.ravel()]).astype(np.float64)


def noise_floor(E2):
    """Per-band noise floor (linear, level-normalised) from v2 healthy passes: pooled FLOOR_Q-th percentile."""
    sk = 10 ** (np.asarray(E2)[:, V1_DIM:].reshape(len(E2), N_MELS, N_Q) / 10)
    return np.percentile(np.concatenate(list(sk), 1), FLOOR_Q, axis=1)


def denoised(E2, floor):
    """v2 passes -> 128 numbers (band shape + spread) after subtracting SUB_ALPHA x the baseline's noise floor.
    The quantile transform is monotone per band, so this equals subtracting from every frame, then summarising."""
    E2 = np.atleast_2d(E2)
    sk = 10 ** (E2[:, V1_DIM:].reshape(len(E2), N_MELS, N_Q) / 10)
    P = np.maximum(sk - SUB_ALPHA * floor[None, :, None], SUB_BETA * sk)
    m = 10 * np.log10(P + 1e-10)                               # n x 64 x N_Q
    mean = m.mean(2)
    return np.concatenate([mean - mean.mean(1, keepdims=True), m.std(2)], 1)


# ------------------------------------------------------------------ baseline
class Baseline:
    """Healthy sound of one machine. fit() on healthy passes only; score() = Mahalanobis distance.
    v1: passes from embed() (128 numbers). v2: passes from embed_pass() (V2_DIM); scored after noise-floor subtraction."""

    def __init__(self, E=None):
        if E is not None: self.fit(E)

    def fit(self, E):
        E = np.asarray(E, np.float64)
        if len(E) < 3: raise ValueError("need at least 3 healthy passes")
        self.E = E
        self.v2 = E.shape[1] == V2_DIM
        self.mu1, self.sd1 = E[:, :V1_DIM].mean(0), E[:, :V1_DIM].std(0) + 1e-3    # for explain()
        if self.v2:
            self.floor = noise_floor(E)
            F = denoised(E, self.floor)
        else:
            F = E
        self.mu, self.sd = F.mean(0), F.std(0) + 1e-3
        Z = (F - self.mu) / self.sd
        C = np.cov(Z, rowvar=False) if len(E) > 1 else np.eye(E.shape[1])
        C = (1 - SHRINK) * C + SHRINK * np.diag(np.diag(C)) + 1e-3 * np.eye(len(C))
        self.P = np.linalg.inv(C)
        return self

    def feats(self, e):
        e = np.atleast_2d(np.asarray(e, np.float64))
        if e.shape[1] != self.E.shape[1]: raise ValueError(f"pass has {e.shape[1]} numbers, baseline {self.E.shape[1]}")
        return denoised(e, self.floor) if self.v2 else e

    def raw(self, e):
        z = (self.feats(e) - self.mu) / self.sd
        return np.sqrt(np.einsum("ij,jk,ik->i", z, self.P, z))

    def score(self, e):
        return float(self.raw(e)[0])

    def calibrate(self, folds=5, seed=0):
        """Out-of-fold healthy scores: each healthy pass scored by a baseline fit without it."""
        n = len(self.E)
        idx = np.random.default_rng(seed).permutation(n)
        out = np.zeros(n)
        k = min(folds, n)
        for f in range(k):
            te = idx[f::k]
            tr = np.setdiff1d(idx, te)
            if len(tr) < 3: tr = idx
            out[te] = Baseline(self.E[tr]).raw(self.E[te])
        self.cal = np.sort(out)
        return self

    def pct(self, s):
        """Share of healthy passes that scored at or below s (0..1)."""
        cal = getattr(self, "cal", None)
        if cal is None: self.calibrate(); cal = self.cal
        return float(np.searchsorted(cal, s, side="right") / len(cal))

    def explain(self, e, top=6):
        """Bands whose mean level moved most vs healthy (in healthy std units) -> a frequency range and direction."""
        d = (np.asarray(e)[:N_MELS] - self.mu1[:N_MELS]) / self.sd1[:N_MELS]
        order = np.argsort(-np.abs(d))[:top]
        lo, hi = EDGES_HZ[order.min()], EDGES_HZ[order.max() + 2]
        louder = float(np.sign(d[order]).sum()) >= 0
        return {"lo_hz": float(lo), "hi_hz": float(hi), "louder": louder, "z_max": float(np.abs(d).max()),
                "band": band_text(lo, hi), "bands": [int(b) for b in order]}

    # persistence (plain JSON: the healthy embeddings; everything else is recomputed)
    def zscore(self, s):
        """Evidence of one pass in healthy-sigma units (log-normal fit to the out-of-fold healthy scores)."""
        if getattr(self, "cal", None) is None: self.calibrate()
        lc = np.log(self.cal + 1e-9)
        return float((np.log(s + 1e-9) - lc.mean()) / (lc.std() + 1e-9))

    def to_json(self):
        return {"v": 2 if self.v2 else 1, "dim": int(self.E.shape[1]), "E": np.round(self.E, 2 if self.v2 else 3).tolist()}

    @classmethod
    def from_json(cls, d):
        return cls(np.asarray(d["E"], np.float64)).calibrate()


def band_text(lo, hi):
    def k(h): return f"{h / 1000:.1f}".rstrip("0").rstrip(".")
    if hi < 1000: return f"{int(round(lo, -1))}-{int(round(hi, -1))} Hz"
    if lo >= 1000: return f"{k(lo)}-{k(hi)} kHz"
    return f"{int(round(lo, -1))} Hz-{k(hi)} kHz"


def hint_for(ex):
    """Hand-written, unreviewed hint: where to start looking. NOT a diagnosis (see MACHINE_EAR.md)."""
    if ex["hi_hz"] <= 600: return "low hum: check mounting, balance"
    if ex["lo_hz"] >= 2000: return "high pitch: check bearing"
    return "broad change: check flow, cavitation"


# ------------------------------------------------------------------ walk-by state machine
LEARNING, LISTENING, SUSPECT, ALERT = "LEARNING", "LISTENING", "SUSPECT", "ALERT"


def step(state, odd, history, n_baseline=MIN_BASELINE, z_hist=None, evid_t="default"):
    """One pass. state: current state; odd: this pass above the healthy percentile; history: list of earlier odd flags
    (newest last, LISTENING/SUSPECT passes only). Returns the new state. Pure function, tested directly.
    z_hist (optional, v2): evidence z of the recent passes INCLUDING this one (newest last). When given and the mean of
    the last EVID_N reaches evid_t (default EVID_T), ALERT even if fewer than ALERT_K passes crossed the odd line."""
    if state == LEARNING:
        return LISTENING if n_baseline >= MIN_BASELINE else LEARNING
    if state == ALERT:
        return ALERT                       # only a person clears an alert (false alarm / fixed / relearn)
    h = (history + [odd])[-ALERT_N:]
    if sum(h) >= ALERT_K:
        return ALERT
    t = EVID_T if evid_t == "default" else evid_t
    if t is not None and z_hist is not None and len(z_hist) >= EVID_N and float(np.mean(z_hist[-EVID_N:])) >= t:
        return ALERT
    if odd:
        return SUSPECT
    if state == SUSPECT:
        tail = (history + [odd])[-CLEAR_N:]
        return LISTENING if len(tail) == CLEAR_N and not any(tail) else SUSPECT
    return LISTENING


# ------------------------------------------------------------------ noise (for the plant-noise tests and the simulator)
def mix(x, noise, snr_db):
    """Add noise so that power(x)/power(noise) = snr_db (both trimmed/tiled to len(x))."""
    n = np.resize(np.asarray(noise, np.float32), len(x))
    px, pn = float(np.mean(x ** 2)) + 1e-12, float(np.mean(n ** 2)) + 1e-12
    return (x + n * np.sqrt(px / (pn * 10 ** (snr_db / 10)))).astype(np.float32)


def plant_noise(n, rng, kind="mixed"):
    """SYNTHETIC factory noise: pink-ish broadband + a 50/60 Hz-family hum + random metal impacts. Not a recording."""
    w = rng.standard_normal(n).astype(np.float32)
    f = np.fft.rfft(w); f /= np.sqrt(np.maximum(np.fft.rfftfreq(n, 1 / SR), 20)); pink = np.fft.irfft(f, n)
    pink /= np.std(pink) + 1e-9
    t = np.arange(n) / SR
    hz = rng.choice([50.0, 60.0])
    hum = sum(np.sin(2 * np.pi * hz * k * t + rng.uniform(0, 6.28)) / k for k in (1, 2, 3, 6))
    hits = np.zeros(n, np.float32)
    for _ in range(rng.integers(2, 8)):
        i = rng.integers(0, max(1, n - 2000)); L = rng.integers(400, 2000)
        hits[i:i + L] += rng.standard_normal(L) * np.exp(-np.arange(L) / (L / 5)) * rng.uniform(2, 6)
    out = {"pink": pink, "hum": hum, "impacts": hits}.get(kind)
    if out is None: out = pink + 0.5 * hum + hits
    return (out / (np.std(out) + 1e-9)).astype(np.float32)


def synth_machine(n, rng, fault=0.0, f0=None):
    """SYNTHETIC pump-like sound for tests and the no-dataset fallback: shaft harmonics + broadband flow noise.
    fault>0 adds a bearing-like 3-4 kHz component with impacts at a defect rate. Clearly labelled synthetic."""
    t = np.arange(n) / SR
    f0 = f0 or 24.6 + rng.normal(0, 0.05)
    x = sum(np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 6.28)) / k for k in range(1, 8)) * 0.3
    flow = rng.standard_normal(n).astype(np.float32)
    F = np.fft.rfft(flow); fr = np.fft.rfftfreq(n, 1 / SR); F *= np.exp(-fr / 2500); flow = np.fft.irfft(F, n)
    x = x + 0.5 * flow / (np.std(flow) + 1e-9)
    if fault > 0:
        car = np.sin(2 * np.pi * 3400 * t)
        env = (np.sin(2 * np.pi * 87.0 * t) > 0.95).astype(np.float32)
        x = x + fault * (0.4 * car * (0.3 + env) + 0.15 * rng.standard_normal(n) * env)
    return (0.2 * x / (np.std(x) + 1e-9)).astype(np.float32)
