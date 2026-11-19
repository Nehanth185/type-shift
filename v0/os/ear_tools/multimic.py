"""Multi-mic front ends for the Machine Ear study (research code, numpy only). Each front end turns one pass of
multi-channel audio (samples x mics, 16 kHz) into the 128 v1 numbers (ear_engine._shape on a log-mel), so the SAME
Mahalanobis baseline + walk-by rule is used for every layout and the only thing that changes is how mics are combined.

Geometry: MIMII TAMAGO-03, 8 mics on a circle, diameter 68 mm, 45 deg apart, mic #1 at 0 deg, #2 at 45 deg ...
[FETCHED: MIMII paper arXiv:1909.09347, Fig. 1]. Channel k (0-based) is assumed to be mic #(k+1) [assumption, checked
against measured inter-channel delays in the study notes].

  single      one channel                          (today's product)
  powavg      average the mel POWER of the mics    (incoherent; no geometry needed; robust to head motion)
  das         delay-and-sum steered at azimuth th  (coherent; needs geometry and a direction)
  mpdr        MVDR/MPDR: per-bin w = R^-1 d / d^H R^-1 d, R = this pass's own covariance + diagonal loading
  fusion      score each mic separately, combine the scores (mean or max) - lives in the eval, not here
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ear_engine as E

C = 343.0
R_ARR = 0.034
ANG = np.arange(8) * np.pi / 4
POS = R_ARR * np.c_[np.cos(ANG), np.sin(ANG)]          # 8 x 2 metres
FREQS = np.fft.rfftfreq(E.N_FFT, 1 / E.SR)
LAYOUTS = {"1 mic": [0], "2 adjacent (26 mm)": [0, 1], "2 opposite (68 mm)": [0, 4], "4 mics": [0, 2, 4, 6],
           "8 mics": list(range(8))}


def stft(X):
    """X: samples x M -> frames x bins x M complex (same framing as ear_engine.melpow)."""
    X = np.asarray(X, np.float32)
    n = 1 + (len(X) - E.N_FFT) // E.HOP
    idx = np.arange(E.N_FFT)[None, :] + E.HOP * np.arange(n)[:, None]
    win = np.hanning(E.N_FFT).astype(np.float32)[None, :, None]
    return np.fft.rfft(X[idx] * win, axis=1)


def steer(mics, th):
    """Far-field plane wave from azimuth th (radians) -> bins x M steering vector (phase only)."""
    u = np.array([np.cos(th), np.sin(th)])
    tau = -(POS[mics] @ u) / C                          # arrival time relative to array centre
    return np.exp(-2j * np.pi * FREQS[:, None] * tau[None, :])


def shape_from_power(P):
    """frames x 64 mel power -> 128 v1 numbers."""
    return E._shape(10 * np.log10(P + 1e-10)).astype(np.float64)


def fe_single(S, mics, **k): return shape_from_power(np.abs(S[:, :, 0]) ** 2 @ E.mel_fb().T)


def fe_powavg(S, mics, **k): return shape_from_power((np.abs(S) ** 2).mean(2) @ E.mel_fb().T)


def fe_das(S, mics, th=0.0, **k):
    d = steer(mics, th)
    Y = (S * d.conj()[None]).mean(2)
    return shape_from_power(np.abs(Y) ** 2 @ E.mel_fb().T)


def fe_mpdr(S, mics, th=0.0, load=0.1, **k):
    d = steer(mics, th)                                  # bins x M
    R = np.einsum("tfm,tfn->fmn", S, S.conj()) / len(S)  # bins x M x M
    tr = np.real(np.einsum("fmm->f", R)) / S.shape[2]
    R = R + load * tr[:, None, None] * np.eye(S.shape[2])[None]
    Ri_d = np.linalg.solve(R, d[:, :, None])[:, :, 0]
    w = Ri_d / np.einsum("fm,fm->f", d.conj(), Ri_d)[:, None]
    Y = np.einsum("fm,tfm->tf", w.conj(), S)
    return shape_from_power(np.abs(Y) ** 2 @ E.mel_fb().T)


FE = {"single": fe_single, "powavg": fe_powavg, "das": fe_das, "mpdr": fe_mpdr}


def srp_power(S, mics, ths, fmin=300, fmax=6000):
    """Steered response power (delay-and-sum output power, 300 Hz-6 kHz) for each candidate azimuth."""
    band = (FREQS >= fmin) & (FREQS <= fmax)
    out = []
    for th in ths:
        Y = (S[:, band] * steer(mics, th)[band].conj()[None]).mean(2)
        out.append(float((np.abs(Y) ** 2).mean()))
    return np.array(out)
