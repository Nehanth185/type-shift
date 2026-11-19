"""Machine Ear evaluation on MIMII pump subsets (see split_pump_id00.json / split_pump_id02.json). Baselines are fit on
HEALTHY clips only; abnormal clips are only ever used for scoring. Writes eval/ear_results.json; MACHINE_EAR.md quotes it.
Run:  cd prototype/v0/os && python ear_tools/ear_eval.py          (~1 min, numpy only)
      python ear_tools/ear_eval.py --ae                            (adds the autoencoder comparison, needs scikit-learn)
Conditions (all on held-out clips):
  clean            MIMII 6 dB files as published (they already contain MIMII's own factory noise at +6 dB SNR)
  real -6 dB       the SAME recordings from MIMII's -6 dB release (real factory noise, 12 dB louder): baseline from
                   6 dB clips (mismatch) and baseline from -6 dB healthy clips (matched)
  +plant X dB      our SYNTHETIC plant noise (pink + hum + impacts, ear_engine.plant_noise) added on top at X dB SNR;
                   baseline clean (mismatch) or baseline with fresh noise of the same kind (matched: learned in the plant)
  +neighbour X dB  other REAL pumps (MIMII id_02/id_06 healthy clips) mixed in: a neighbouring machine
  gain +-6 dB      every clip randomly 6 dB louder/quieter (standing 1 m vs 2 m away), baseline too
  3 s slice        the first 3 s of each clip (a short walk-by) instead of 10 s
"""
import json, os, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); OS = os.path.dirname(HERE)
sys.path.insert(0, OS)
import ear_engine as E

DATA = os.path.abspath(os.path.join(OS, "..", "..", "..", "web", "downloads", "mimii"))
AE = "--ae" in sys.argv
t0 = time.time()


def load(folder, lab, names): return [E.read_wav(os.path.join(DATA, folder, lab, n)) for n in names]


def auc(neg, pos):
    neg, pos = np.asarray(neg), np.asarray(pos)
    r = np.argsort(np.argsort(np.concatenate([neg, pos]))) + 1
    return float((r[len(neg):].sum() - len(pos) * (len(pos) + 1) / 2) / (len(neg) * len(pos)))


def pauc(neg, pos, fpr=0.1):
    """Partial AUC over false-positive rate 0..0.1, scaled to 0..1 (DCASE style)."""
    neg = np.sort(np.asarray(neg))[::-1]; pos = np.asarray(pos)
    k = max(1, int(np.floor(fpr * len(neg))))
    return float(np.mean([(pos > t).mean() for t in neg[:k]]))


def ci(neg, pos, B=1000, seed=0):
    r = np.random.default_rng(seed); neg, pos = np.asarray(neg), np.asarray(pos)
    v = [auc(r.choice(neg, len(neg)), r.choice(pos, len(pos))) for _ in range(B)]
    return [round(float(np.percentile(v, 2.5)), 3), round(float(np.percentile(v, 97.5)), 3)]


def emb(clips, secs=None):
    return np.array([E.embed(x[: int(secs * E.SR)] if secs else x) for x in clips])


NB = [E.read_wav(os.path.join(DATA, d, "normal", f)) for d in ("6_dB_pump_id_02", "6_dB_pump_id_06")
      for f in sorted(os.listdir(os.path.join(DATA, d, "normal")))[:25]]


def noisy(clips, snr, kind, seed):
    r = np.random.default_rng(seed); out = []
    for x in clips:
        n = E.plant_noise(len(x), r) if kind == "plant" else np.roll(NB[r.integers(len(NB))], -int(r.integers(0, E.SR * 9)))
        out.append(E.mix(x, n, snr))
    return out


def gain(clips, seed):
    r = np.random.default_rng(seed)
    return [x * 10 ** (r.uniform(-6, 6) / 20) for x in clips]


ROWS = []


def row(machine, name, base, En, Ea, note=""):
    sn, sa = base.raw(En), base.raw(Ea)
    thr = base.cal[int(np.ceil(E.ODD_PCT * len(base.cal))) - 1]
    r = {"machine": machine, "condition": name, "auc": round(auc(sn, sa), 3), "ci95": ci(sn, sa),
         "pauc": round(pauc(sn, sa), 3), "odd_rate_healthy": round(float((sn > thr).mean()), 2),
         "odd_rate_faulty": round(float((sa > thr).mean()), 2), "note": note}
    ROWS.append(r)
    print(f"{machine} | {name:38s} AUC {r['auc']:.3f} {r['ci95']} pAUC {r['pauc']:.3f} "
          f"odd healthy {r['odd_rate_healthy']:.2f} faulty {r['odd_rate_faulty']:.2f}", flush=True)
    return sn, sa


def fit(Etr): return E.Baseline(Etr).calibrate()


# ------------------------------------------------------------------ pump id_00 (main)
sp = json.load(open(os.path.join(HERE, "split_pump_id00.json")))
tr, tn, ta = (load("6_dB_pump_id_00", "normal", sp["train_normal"]), load("6_dB_pump_id_00", "normal", sp["test_normal"]),
              load("6_dB_pump_id_00", "abnormal", sp["test_abnormal"]))
rtr, rtn, rta = (load("-6_dB_pump_id_00", "normal", sp["train_normal"]), load("-6_dB_pump_id_00", "normal", sp["test_normal"]),
                 load("-6_dB_pump_id_00", "abnormal", sp["test_abnormal"]))
M = "pump id_00"
Etr, Etn, Eta = emb(tr), emb(tn), emb(ta)
B = fit(Etr)
row(M, "clean, 10 s", B, Etn, Eta, "MIMII 6 dB as published")
row(M, "clean, 3 s slice", fit(emb(tr, 3)), emb(tn, 3), emb(ta, 3), "short walk-by")
row(M, "gain +-6 dB random", fit(emb(gain(tr, 1))), emb(gain(tn, 2)), emb(gain(ta, 3)), "distance change")
row(M, "real -6 dB, baseline from 6 dB", B, emb(rtn), emb(rta), "MIMII's own plant noise; mismatch")
row(M, "real -6 dB, baseline at -6 dB", fit(emb(rtr)), emb(rtn), emb(rta), "MIMII's own plant noise; matched")
for snr in (6, 0, -6):
    En, Ea = emb(noisy(tn, snr, "plant", 11)), emb(noisy(ta, snr, "plant", 12))
    row(M, f"+plant {snr:+d} dB, baseline clean", B, En, Ea, "synthetic noise; mismatch")
    row(M, f"+plant {snr:+d} dB, baseline in noise", fit(emb(noisy(tr, snr, "plant", 13))), En, Ea, "synthetic noise; matched")
for snr in (6, 0):
    row(M, f"+neighbour pump {snr:+d} dB, baseline in noise", fit(emb(noisy(tr, snr, "nb", 14))),
        emb(noisy(tn, snr, "nb", 15)), emb(noisy(ta, snr, "nb", 16)), "real other pumps; matched")

# ------------------------------------------------------------------ pump id_02 (second machine, harder in DCASE)
sp2 = json.load(open(os.path.join(HERE, "split_pump_id02.json")))
tr2, tn2, ta2 = (load("6_dB_pump_id_02", "normal", sp2["train_normal"]), load("6_dB_pump_id_02", "normal", sp2["test_normal"]),
                 load("6_dB_pump_id_02", "abnormal", sp2["test_abnormal"]))
M2 = "pump id_02"
B2 = fit(emb(tr2))
row(M2, "clean, 10 s", B2, emb(tn2), emb(ta2), "70 healthy clips in the baseline")
row(M2, "clean, 3 s slice", fit(emb(tr2, 3)), emb(tn2, 3), emb(ta2, 3), "short walk-by")
En, Ea = emb(noisy(tn2, 0, "plant", 21)), emb(noisy(ta2, 0, "plant", 22))
row(M2, "+plant +0 dB, baseline in noise", fit(emb(noisy(tr2, 0, "plant", 23))), En, Ea, "synthetic noise; matched")

# ------------------------------------------------------------------ baseline size: how many healthy passes are enough
small = []
for n in (10, 20, 40, 150):
    v = []
    for s in range(5):
        idx = np.random.default_rng(s).choice(len(Etr), n, replace=False)
        b = fit(Etr[idx]); v.append(auc(b.raw(Etn), b.raw(Eta)))
    small.append({"healthy_passes": n, "auc_mean": round(float(np.mean(v)), 3), "auc_min": round(float(np.min(v)), 3)})
    print("baseline size", small[-1], flush=True)

# ------------------------------------------------------------------ comparison detectors (pump id_00, clean)
comp = [{"detector": "Mahalanobis on 128 log-mel shape stats (app default)", "auc": ROWS[0]["auc"]}]
Z = lambda X: (X - B.mu) / B.sd
d = np.sqrt(((Z(Etn)[:, None] - Z(Etr)[None]) ** 2).sum(-1)); da = np.sqrt(((Z(Eta)[:, None] - Z(Etr)[None]) ** 2).sum(-1))
comp.append({"detector": "k-NN (k=5) on the same stats", "auc": round(auc(np.sort(d)[:, :5].mean(1), np.sort(da)[:, :5].mean(1)), 3)})
if AE:
    from sklearn.neural_network import MLPRegressor
    mm = np.concatenate([E.logmel(x) for x in tr[:60]]); mu, sd = mm.mean(0), mm.std(0) + 1e-3
    def frames(x):
        m = (E.logmel(x) - mu) / sd
        return np.concatenate([m[i:len(m) - 4 + i] for i in range(5)], 1)
    F = np.concatenate([frames(x) for x in tr])[::3]
    ae = MLPRegressor(hidden_layer_sizes=(128, 32, 128), max_iter=25, random_state=0).fit(F, F)
    err = lambda cl: np.array([np.mean((ae.predict(f) - f) ** 2) for f in map(frames, cl)])
    comp.append({"detector": "Autoencoder, 5-frame log-mel (DCASE-2020-baseline style, sklearn MLP 128-32-128, 25 epochs)",
                 "auc": round(auc(err(tn), err(ta)), 3)})
for c in comp: print("compare", c, flush=True)


# ------------------------------------------------------------------ walk-by: sequences of passes through the state machine
def walk(base, Eh, Ef, n_seq=1000, passes=12, seed=0):
    r = np.random.default_rng(seed)
    thr = base.cal[int(np.ceil(E.ODD_PCT * len(base.cal))) - 1]
    oh, of = base.raw(Eh) > thr, base.raw(Ef) > thr
    fa, det, delays = 0, 0, []
    for _ in range(n_seq):
        for odd, faulty in ((oh, False), (of, True)):
            st, hist = E.LISTENING, []
            for i in range(passes):
                o = bool(odd[r.integers(len(odd))]); st = E.step(st, o, hist); hist.append(o)
                if st == E.ALERT:
                    if faulty: det += 1; delays.append(i + 1)
                    else: fa += 1
                    break
    return {"false_alert_healthy_12_passes": round(fa / n_seq, 3), "alert_faulty_within_12_passes": round(det / n_seq, 3),
            "median_passes_to_alert": float(np.median(delays)) if delays else None,
            "odd_rate_healthy": round(float(oh.mean()), 3), "odd_rate_faulty": round(float(of.mean()), 3)}


WB = {"pump id_00, clean, 3 s passes": walk(fit(emb(tr, 3)), emb(tn, 3), emb(ta, 3)),
      "pump id_00, real -6 dB, baseline at -6 dB, 3 s passes": walk(fit(emb(rtr, 3)), emb(rtn, 3), emb(rta, 3)),
      "pump id_02, +plant 0 dB, baseline in noise, 3 s": walk(fit(emb(noisy(tr2, 0, "plant", 23), 3)),
                                                              emb(noisy(tn2, 0, "plant", 21), 3), emb(noisy(ta2, 0, "plant", 22), 3))}
for k, v in WB.items(): print("walk-by", k, v, flush=True)

# single-pass rule (no state machine) for contrast: alert on the first odd pass
out = os.path.join(OS, "eval", "ear_results.json")
prev = json.load(open(out)) if os.path.exists(out) else {}
if not AE and prev.get("comparison"):
    comp += [c for c in prev["comparison"] if c["detector"].startswith("Autoencoder")]
json.dump({"rows": ROWS, "baseline_size": small, "comparison": comp, "walkby": WB,
           "n": {"id_00": [len(tr), len(tn), len(ta)], "id_02": [len(tr2), len(tn2), len(ta2)]},
           "settings": {"odd_pct": E.ODD_PCT, "alert": f"{E.ALERT_K} of last {E.ALERT_N}", "clear": E.CLEAR_N},
           "seconds": round(time.time() - t0)}, open(out, "w"), indent=1)
print(f"done in {time.time() - t0:.0f}s -> {out}")
