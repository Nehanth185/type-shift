"""Machine Ear v1 (shipped 2026-11-17) vs v2 (noise-floor subtraction, 2026-11-18) on HELD-OUT machines, real MIMII noise.
v2 settings were frozen after tuning on pump id_04 ONLY (see MACHINE_EAR.md "v2"). Pump id_04 is never reported here.
Every cell: baseline learned at the SAME SNR (in place) from healthy clips only; abnormal clips only scored.
  AUC at 10 s and 3 s (bootstrap 95% CI, 1000 resamples over test clips); walk-by sim on 3 s passes (1000 sequences of
  12 passes, drawn with replacement from held-out clips; ALERT = 3 odd of last 4, odd = above healthy 95th pct).
Resumable: finished cells are cached in eval/ear_v2_cells.json, so it can be re-run in short shells.
  cd prototype/v0/os && python ear_tools/ear_eval_v2.py            (repeat until "all cells done")
  python ear_tools/ear_eval_v2.py --merge                            (writes the v2 section of eval/ear_results.json)
MIMII (Hitachi, CC BY-SA 4.0, zenodo.org/records/3384388). "6 dB" is MIMII's least noisy release, not clean audio."""
import json, os, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); OS = os.path.dirname(HERE)
sys.path.insert(0, OS)
import ear_engine as E

DATA = os.path.abspath(os.path.join(OS, "..", "..", "..", "web", "downloads", "mimii"))
CELLS = os.path.join(OS, "eval", "ear_v2_cells.json")
SNRS = ("6_dB", "0_dB", "-6_dB")
MACHINES = [("pump", "id_00"), ("pump", "id_02"), ("fan", "id_00"), ("fan", "id_02")]   # fan id_04/id_06 not downloaded (time)


def auc(neg, pos):
    from scipy.stats import rankdata
    neg, pos = np.asarray(neg, float), np.asarray(pos, float)
    r = rankdata(np.concatenate([neg, pos]))
    return float((r[len(neg):].sum() - len(pos) * (len(pos) + 1) / 2) / (len(neg) * len(pos)))


def ci(neg, pos, B=1000, seed=0):
    r = np.random.default_rng(seed); neg, pos = np.asarray(neg), np.asarray(pos)
    v = [auc(r.choice(neg, len(neg)), r.choice(pos, len(pos))) for _ in range(B)]
    return [round(float(np.percentile(v, 2.5)), 3), round(float(np.percentile(v, 97.5)), 3)]


def ls(snr, m, i, lab):
    d = os.path.join(DATA, f"{snr}_{m}_{i}", lab)
    return set(f for f in os.listdir(d) if f.endswith(".wav")) if os.path.isdir(d) else set()


def split_for(m, i):
    if m == "pump":
        return json.load(open(os.path.join(HERE, f"split_pump_{i.replace('_', '')}.json")))
    # fans: names present at all three SNRs, seeded shuffle, 80 baseline / 60 healthy test / 60 faulty test
    nn = sorted(set.intersection(*[ls(s, m, i, "normal") for s in SNRS]))
    aa = sorted(set.intersection(*[ls(s, m, i, "abnormal") for s in SNRS]))
    if len(nn) < 140 or len(aa) < 60: return None               # wait until all three SNRs are fully on disk
    r = np.random.default_rng(0); nn = list(r.permutation(nn)); aa = list(r.permutation(aa))
    return {"train_normal": sorted(nn[:80]), "test_normal": sorted(nn[80:140]), "test_abnormal": sorted(aa[:60])}


def walk(B, sh, sf, n_seq=1000, passes=12, seed=0):
    r = np.random.default_rng(seed)
    thr = B.cal[int(np.ceil(E.ODD_PCT * len(B.cal))) - 1]
    oh, of = sh > thr, sf > thr
    fa, det, delays = 0, 0, []
    for _ in range(n_seq):
        for odd, faulty in ((oh, False), (of, True)):
            st, hist = E.LISTENING, []
            for k in range(passes):
                o = bool(odd[r.integers(len(odd))]); st = E.step(st, o, hist); hist.append(o)
                if st == E.ALERT:
                    if faulty: det += 1; delays.append(k + 1)
                    else: fa += 1
                    break
    return {"false_alert_healthy": round(fa / n_seq, 3), "alert_faulty": round(det / n_seq, 3),
            "median_passes": float(np.median(delays)) if delays else None,
            "odd_h": round(float(oh.mean()), 3), "odd_f": round(float(of.mean()), 3)}


def cell(m, i, snr):
    sp = split_for(m, i)
    if sp is None: return None
    need = {"normal": set(sp["train_normal"]) | set(sp["test_normal"]), "abnormal": set(sp["test_abnormal"])}
    for lab, want in need.items():
        if not want <= ls(snr, m, i, lab): return None            # not fully downloaded yet
    L = lambda k, lab: [E.read_wav(os.path.join(DATA, f"{snr}_{m}_{i}", lab, n)) for n in sp[k]]
    tr, tn, ta = L("train_normal", "normal"), L("test_normal", "normal"), L("test_abnormal", "abnormal")
    out = {"n": [len(tr), len(tn), len(ta)]}
    for ver, emb in (("v1", E.embed), ("v2", E.embed_pass)):
        r = {}
        for secs in (10, 3):
            cut = lambda X: [x[: int(secs * E.SR)] for x in X]
            t0 = time.time(); Etr = np.array([emb(x) for x in cut(tr)]); Etn = np.array([emb(x) for x in cut(tn)])
            Eta = np.array([emb(x) for x in cut(ta)]); ms_emb = (time.time() - t0) / (len(tr) + len(tn) + len(ta)) * 1e3
            t0 = time.time(); B = E.Baseline(Etr).calibrate(); ms_fit = (time.time() - t0) * 1e3
            t0 = time.time(); sh, sf = B.raw(Etn), B.raw(Eta); ms_sc = (time.time() - t0) / (len(tn) + len(ta)) * 1e3
            r[f"{secs}s"] = {"auc": round(auc(sh, sf), 3), "ci95": ci(sh, sf), "ms_embed": round(ms_emb, 2),
                             "ms_score": round(ms_sc, 3), "ms_fit_calibrate": round(ms_fit, 1)}
            if secs == 3: r["walk_3s"] = walk(B, sh, sf)
        out[ver] = r
    return out


if __name__ == "__main__":
    cells = json.load(open(CELLS)) if os.path.exists(CELLS) else {}
    if "--merge" not in sys.argv:
        t_end = time.time() + float(os.environ.get("BUDGET", 95))
        for m, i in MACHINES:
            for snr in SNRS:
                k = f"{m} {i} {snr}"
                if k in cells or time.time() > t_end: continue
                try:
                    c = cell(m, i, snr)
                except Exception as e:  # noqa: BLE001
                    print("ERR", k, e); continue
                if c is None: print("missing data", k); continue
                cells[k] = c; json.dump(cells, open(CELLS, "w"), indent=1)
                print(k, "v1", c["v1"]["10s"]["auc"], c["v1"]["3s"]["auc"], c["v1"]["walk_3s"]["alert_faulty"],
                      "| v2", c["v2"]["10s"]["auc"], c["v2"]["3s"]["auc"], c["v2"]["walk_3s"]["alert_faulty"], flush=True)
        left = [f"{m} {i} {s}" for m, i in MACHINES for s in SNRS if f"{m} {i} {s}" not in cells]
        print("all cells done" if not left else f"left: {left}")
    else:
        out = os.path.join(OS, "eval", "ear_results.json")
        res = json.load(open(out))
        res["v2"] = {"date": "2026-11-18", "cells": cells,
                     "settings": {"floor_pct": E.FLOOR_Q, "alpha": E.SUB_ALPHA, "beta": E.SUB_BETA, "quantiles": E.N_Q,
                                  "evidence_rule": E.EVID_T, "tuned_on": "pump id_04 only (not reported)"},
                     "protocol": "baseline learned at the same SNR; abnormal never in a baseline; fans 80/60/60 seeded split"}
        json.dump(res, open(out, "w"), indent=1)
        print("merged", len(cells), "cells ->", out)
