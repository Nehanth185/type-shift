"""Machine Ear v2 (2026-11-18): pass sketch + baseline noise-floor subtraction, evidence rule (off by default),
backward compatibility with v1 baselines stored before the upgrade. SYNTHETIC audio, runs anywhere."""
import json

import numpy as np

import workapi
from worker_os import ear_engine as E
from worker_os import ear_app

from test_machine_ear import ear_pass, learn, SECS


def _x(seed, fault=0.0, n=SECS):
    return E.synth_machine(E.SR * n, np.random.default_rng(seed), fault=fault)


def test_embed_pass_shape_gain_invariant_and_starts_with_v1():
    x = _x(1)
    a, b = E.embed_pass(x), E.embed_pass(x * 2.0)
    assert a.shape == (E.V2_DIM,) and E.V2_DIM == E.V1_DIM + E.N_MELS * E.N_Q
    assert np.allclose(a, b, atol=1e-2)                      # level removed in both parts
    assert np.allclose(a[:E.V1_DIM], E.embed(x), atol=1e-6)    # first 128 numbers are exactly v1


def test_sketch_keeps_no_time_order():
    x = _x(2, n=4)
    y = np.concatenate([x[len(x) // 2:], x[: len(x) // 2]])      # same audio, halves swapped
    d = np.abs(E.embed_pass(x)[E.V1_DIM:] - E.embed_pass(y)[E.V1_DIM:])
    assert np.median(d) < 0.2 and d.mean() < 0.5                  # dB; only the frames at the cut differ


def test_denoised_matches_framewise_subtraction():
    """Quantile sketch -> subtract -> summarise == subtract on every frame -> summarise (monotone per band), roughly."""
    X = [_x(10 + i) for i in range(12)]
    E2 = np.array([E.embed_pass(x) for x in X])
    fl = E.noise_floor(E2)
    P = E.melpow(X[0]); P = P / P.mean()
    Q = np.maximum(P - E.SUB_ALPHA * fl, E.SUB_BETA * P)
    m = 10 * np.log10(Q + 1e-10); mean = m.mean(0)
    direct = np.concatenate([mean - mean.mean(), m.std(0)])
    approx = E.denoised(E2[:1], fl)[0]
    assert np.corrcoef(direct, approx)[0, 1] > 0.98
    assert np.abs(direct[:E.N_MELS] - approx[:E.N_MELS]).mean() < 0.5        # dB


def test_v2_baseline_scores_fault_higher_and_explains():
    r = np.random.default_rng(3)
    B = E.Baseline([E.embed_pass(E.synth_machine(E.SR * SECS, r)) for _ in range(20)]).calibrate()
    assert B.v2 and B.floor.shape == (E.N_MELS,)
    h = [B.score(E.embed_pass(E.synth_machine(E.SR * SECS, r))) for _ in range(10)]
    f = [B.score(E.embed_pass(E.synth_machine(E.SR * SECS, r, fault=1.0))) for _ in range(10)]
    assert min(f) > max(h)
    ex = B.explain(E.embed_pass(E.synth_machine(E.SR * SECS, r, fault=1.0)))
    assert ex["lo_hz"] >= 1500 and ex["louder"] and "kHz" in ex["band"]
    assert B.zscore(max(f)) > B.zscore(min(h))


def test_v2_still_separates_in_synthetic_noise():
    """Faint fault under loud synthetic pink noise, baseline learned in that noise. NOT a claim that v2 beats v1 here:
    on this synthetic case v1 is slightly better (0.98 vs 0.96); v2's gain was on real MIMII noise, see MACHINE_EAR.md."""
    r = np.random.default_rng(4)
    mk = lambda fault: E.mix(E.synth_machine(E.SR * SECS, r, fault=fault), E.plant_noise(E.SR * SECS, r, "pink"), -6)
    tr = [mk(0.0) for _ in range(30)]; hn = [mk(0.0) for _ in range(15)]; ft = [mk(0.6) for _ in range(15)]

    def sep(emb):
        B = E.Baseline([emb(x) for x in tr])
        h, f = B.raw(np.array([emb(x) for x in hn])), B.raw(np.array([emb(x) for x in ft]))
        return np.mean(f[:, None] > h[None, :])
    assert sep(E.embed_pass) > 0.9 and sep(E.embed) > 0.9


def test_v2_json_roundtrip_and_dim_guard():
    r = np.random.default_rng(5)
    B = E.Baseline([E.embed_pass(E.synth_machine(E.SR * SECS, r)) for _ in range(12)]).calibrate()
    j = json.loads(json.dumps(B.to_json()))
    assert j["v"] == 2 and j["dim"] == E.V2_DIM
    B2 = E.Baseline.from_json(j)
    e = E.embed_pass(E.synth_machine(E.SR * SECS, r, fault=1.0))
    assert abs(B2.score(e) - B.score(e)) / B.score(e) < 0.05
    try:
        B2.score(E.embed(E.synth_machine(E.SR * SECS, r))); assert False, "v1 pass into v2 baseline must fail"
    except ValueError:
        pass


def test_v1_baseline_still_loads_and_scores():
    r = np.random.default_rng(6)
    B = E.Baseline([E.embed(E.synth_machine(E.SR * SECS, r)) for _ in range(12)]).calibrate()
    assert not B.v2 and B.to_json()["v"] == 1
    assert E.Baseline.from_json(B.to_json()).score(E.embed(E.synth_machine(E.SR * SECS, r))) > 0


def test_evidence_rule_off_by_default_and_works_when_set():
    assert E.EVID_T is None
    z = [1.9, 1.9, 1.9, 1.9]
    assert E.step(E.LISTENING, False, [False, False, False], z_hist=z) == E.LISTENING      # default: off
    assert E.step(E.LISTENING, False, [False, False, False], z_hist=z, evid_t=1.5) == E.ALERT
    assert E.step(E.LISTENING, False, [False, False, False], z_hist=z[:3], evid_t=1.5) == E.LISTENING  # needs 4
    assert E.step(E.LISTENING, True, [True, True], z_hist=None) == E.ALERT              # 3 of 4 unchanged


def test_app_default_stays_v1(client, monkeypatch):
    monkeypatch.delenv("MACHINE_EAR_FEATURES", raising=False)
    learn(client)
    a = ear_app.get_asset("demo", "P-12")
    assert a["baseline"]["v"] == 1 and a["baseline"]["dim"] == E.V1_DIM


def test_app_learns_a_v2_baseline_when_opted_in(client, monkeypatch):
    monkeypatch.setenv("MACHINE_EAR_FEATURES", "v2")
    learn(client)
    a = ear_app.get_asset("demo", "P-12")
    assert a["baseline"]["v"] == 2 and a["baseline"]["dim"] == E.V2_DIM
    j = ear_pass(client, 1.0, seed=901)
    assert j["machine"]["last"]["pct"] is not None
    s = workapi.store()
    fp = json.loads(s.q("SELECT last FROM ear_assets WHERE asset='P-12'")[0]["last"])["fingerprint"]
    assert len(fp) == E.V1_DIM                                   # stored fingerprint stays 128 numbers


def test_app_keeps_scoring_an_old_v1_baseline(client, monkeypatch):
    """With v2 switched on, a machine that learned under v1 keeps its v1 baseline (no forced relearn)."""
    monkeypatch.setenv("MACHINE_EAR_FEATURES", "v2")
    r = np.random.default_rng(7)
    a = ear_app.get_asset("demo", "P-99")
    a.update(state="LISTENING", pending=[], history=[], alert_ts=None, last=None,
             baseline=E.Baseline([E.embed(E.synth_machine(E.SR * SECS, r)) for _ in range(12)]).to_json())
    a["_B"] = None
    ear_app.put_asset(a)
    j = ear_pass(client, 0.0, seed=902, asset="P-99")
    assert j["state"] in ("LISTENING", "SUSPECT")
    assert ear_app.get_asset("demo", "P-99")["baseline"]["v"] == 1
