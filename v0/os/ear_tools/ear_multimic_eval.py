"""Machine Ear multi-mic study: does combining MIMII's 8 array mics lift detection in loud plants (0 / -6 dB)?
Front ends in multimic.py; every front end feeds the SAME v1 128-number features + Mahalanobis baseline + 3-of-4 rule.
Protocol (same as ear_eval_v2.py): baseline learned at the SAME SNR from healthy clips only; abnormal clips only scored;
3 s passes (first 3 s of each clip; the 8-ch download holds only the first 3 s); AUC with 1000-resample bootstrap 95% CI;
walk-by = 1000 sequences x 12 passes, odd = above the healthy 95th pct (out-of-fold), ALERT = 3 odd of last 4.
  dev     python ear_tools/ear_multimic_eval.py dev            pump id_04 only (tuning; never reported as a result)
  test    python ear_tools/ear_multimic_eval.py test           held-out machines, FROZEN methods only, each cell once
          (cached in eval/ear_multimic_cells.json; a cell already there is never re-scored)
  merge   python ear_tools/ear_multimic_eval.py merge          -> eval/ear_multimic_results.json
Data: web/downloads/mimii8 (fetch_mimii8.py). MIMII (Hitachi, CC BY-SA 4.0, zenodo.org/records/3384388)."""
import json, os, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); OS = os.path.dirname(HERE)
sys.path.insert(0, OS); sys.path.insert(0, HERE)
import ear_engine as E
import multimic as M
from ear_eval_v2 import auc, ci, split_for

D8 = os.path.abspath(os.path.join(OS, "..", "..", "..", "web", "downloads", "mimii8"))
CELLS = os.path.join(OS, "eval", "ear_multimic_cells.json")
FROZEN = os.path.join(OS, "eval", "ear_multimic_frozen.json")
THS = np.linspace(0, 2 * np.pi, 24, endpoint=False)


def load(snr, m, i, lab, names):
    return [np.load(os.path.join(D8, f"{snr}_{m}_{i}", lab, n[:-4] + ".npy")).astype(np.float32) / 32768 for n in names]


def dev_split(snr):
    d = os.path.join(D8, f"{snr}_pump_id_04")
    nn = sorted(f[:-4] + ".wav" for f in os.listdir(os.path.join(d, "normal")))
    aa = sorted(f[:-4] + ".wav" for f in os.listdir(os.path.join(d, "abnormal")))
    r = np.random.default_rng(0); nn = list(r.permutation(nn)); aa = list(r.permutation(aa))
    return {"train_normal": sorted(nn[:70]), "test_normal": sorted(nn[70:120]), "test_abnormal": sorted(aa[:60])}


def oof(F, folds=5, seed=0):
    """Out-of-fold healthy scores (unsorted, aligned with F) - same folds as Baseline.calibrate."""
    n = len(F); idx = np.random.default_rng(seed).permutation(n); out = np.zeros(n)
    for f in range(folds):
        te = idx[f::folds]; tr = np.setdiff1d(idx, te)
        out[te] = E.Baseline(F[tr]).raw(F[te])
    return out


def walk(thr, sh, sf, n_seq=1000, passes=12, seed=0):
    r = np.random.default_rng(seed); oh, of = sh > thr, sf > thr
    fa = det = 0
    for _ in range(n_seq):
        for odd, faulty in ((oh, False), (of, True)):
            st, hist = E.LISTENING, []
            for k in range(passes):
                o = bool(odd[r.integers(len(odd))]); st = E.step(st, o, hist); hist.append(o)
                if st == E.ALERT:
                    if faulty: det += 1
                    else: fa += 1
                    break
    return {"alert_faulty": round(det / n_seq, 3), "false_alert": round(fa / n_seq, 3),
            "odd_h": round(float(oh.mean()), 3), "odd_f": round(float(of.mean()), 3)}


def pick_direction(Str, mics, rule):
    """Choose the steering azimuth from HEALTHY baseline passes only."""
    if rule == "srp_max":       # loudest direction (machine if it dominates; the noise if the noise does)
        return float(THS[np.argmax(sum(M.srp_power(S, mics, THS) for S in Str))])
    if rule == "srp_min":       # quietest direction (null on the dominant noise)
        return float(THS[np.argmin(sum(M.srp_power(S, mics, THS) for S in Str))])
    if rule == "stable":        # direction whose healthy features are tightest (lowest mean OOF Mahalanobis)
        best = None
        for th in THS[::2]:
            F = np.array([M.fe_das(S, mics, th=th) for S in Str]); v = float(np.median(oof(F)))
            if best is None or v < best[0]: best = (v, float(th))
        return best[1]
    raise ValueError(rule)


def run_method(meth, Str, Stn, Sta):
    """meth: {"layout", "fe": single|powavg|das|mpdr|fuse_mean|fuse_max, "dir": rule}. S* = lists of STFTs (all 8 mics)."""
    mics = M.LAYOUTS[meth["layout"]]
    sub = lambda L: [S[:, :, mics] for S in L]
    tr, tn, ta = sub(Str), sub(Stn), sub(Sta)
    t0 = time.time(); info = {}
    if meth["fe"].startswith("fuse"):
        zs = []
        for k in range(len(mics)):
            F = np.array([M.fe_single(S[:, :, k:k + 1], [0]) for S in tr]); B = E.Baseline(F)
            h = oof(F); lc = np.log(h); z = lambda s: (np.log(s) - lc.mean()) / lc.std()
            zs.append((z(h), z(B.raw(np.array([M.fe_single(S[:, :, k:k + 1], [0]) for S in tn]))),
                       z(B.raw(np.array([M.fe_single(S[:, :, k:k + 1], [0]) for S in ta])))))
        agg = np.mean if meth["fe"] == "fuse_mean" else np.max
        h, sh, sf = (agg(np.array([z[j] for z in zs]), 0) for j in range(3))
    else:
        kw = {}
        if meth["fe"] in ("das", "mpdr"):
            kw["th"] = pick_direction(tr, mics, meth.get("dir", "srp_max")); info["steer_deg"] = round(np.degrees(kw["th"]))
        f = M.FE[meth["fe"]]
        F = np.array([f(S, mics, **kw) for S in tr]); B = E.Baseline(F)
        h = oof(F); sh = B.raw(np.array([f(S, mics, **kw) for S in tn])); sf = B.raw(np.array([f(S, mics, **kw) for S in ta]))
    ms = (time.time() - t0) / (len(tr) + len(tn) + len(ta)) * 1e3
    thr = np.sort(h)[int(np.ceil(E.ODD_PCT * len(h))) - 1]
    return {"auc": round(auc(sh, sf), 3), "ci95": ci(sh, sf), "walk": walk(thr, sh, sf),
            "ms_per_pass_frontend_and_score": round(ms, 1), **info}


def stfts(snr, m, i, sp):
    L = lambda k, lab: [M.stft(x[: 3 * E.SR]) for x in load(snr, m, i, lab, sp[k])]
    return L("train_normal", "normal"), L("test_normal", "normal"), L("test_abnormal", "abnormal")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "dev":
        meths = json.loads(sys.argv[2])
        for snr in sys.argv[3].split(","):
            Str, Stn, Sta = stfts(snr, "pump", "id_04", dev_split(snr))
            for me in meths:
                r = run_method(me, Str, Stn, Sta)
                print("DEV", snr, json.dumps(me), r["auc"], r["ci95"], r["walk"], r.get("steer_deg"), flush=True)
    elif mode == "test":
        fz = json.load(open(FROZEN)); cells = json.load(open(CELLS)) if os.path.exists(CELLS) else {}
        t_end = time.time() + float(os.environ.get("BUDGET", 95))
        for m, i in [("pump", "id_00"), ("pump", "id_02"), ("fan", "id_00"), ("fan", "id_02")]:
            for snr in ("0_dB", "-6_dB"):
                k = f"{m} {i} {snr}"
                if k in cells or time.time() > t_end: continue
                sp = split_for(m, i)
                Str, Stn, Sta = stfts(snr, m, i, sp)
                cells[k] = {"n": [len(Str), len(Stn), len(Sta)], "test_looks": 1,
                            "methods": {name: run_method(me, Str, Stn, Sta) for name, me in fz["methods"].items()}}
                json.dump(cells, open(CELLS, "w"), indent=1)
                print(k, {n: (v["auc"], v["walk"]["alert_faulty"]) for n, v in cells[k]["methods"].items()}, flush=True)
        left = [f"{m} {i} {s}" for m, i in [("pump", "id_00"), ("pump", "id_02"), ("fan", "id_00"), ("fan", "id_02")]
                for s in ("0_dB", "-6_dB") if f"{m} {i} {s}" not in cells]
        print("all cells done" if not left else f"left: {left}")
    elif mode == "merge":
        out = {"date": "2026-11-18", "frozen": json.load(open(FROZEN)), "cells": json.load(open(CELLS)),
               "protocol": __doc__}
        json.dump(out, open(os.path.join(OS, "eval", "ear_multimic_results.json"), "w"), indent=1)
        print("merged")
