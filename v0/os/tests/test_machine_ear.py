"""Machine Ear: engine, walk-by state machine, /os/ear endpoints, the ALERT -> Navigator handoff, privacy rules.
Uses SYNTHETIC machine audio (ear_engine.synth_machine) so it runs anywhere; the MIMII test at the end is skipped
when the dataset subset isn't on disk."""
import base64, json, os

import numpy as np
import pytest

import workapi
from conftest import CTX, say
from worker_os import ear_engine as E
from worker_os import ear_app
from worker_os.hud import violations

SECS = 3


def wav_b64(fault=0.0, seed=0, gain=1.0):
    x = E.synth_machine(E.SR * SECS, np.random.default_rng(seed), fault=fault) * gain
    return base64.b64encode(E.to_wav(x)).decode()


def ear_pass(c, fault=0.0, seed=0, asset="P-12", worker="w1", status=200, **kw):
    body = {"worker_id": worker, "workspace_id": "demo", "asset": asset, "model": "CP-450",
            "audio_b64": wav_b64(fault, seed), **kw}
    r = c.post("/os/ear/pass", json=body)
    assert r.status_code == status, r.text
    j = r.json()
    if status == 200:
        assert not violations(j["card"]), violations(j["card"])
        assert len(j["machine"]["state_line"]) <= 40
    return j


def learn(c, asset="P-12", n=E.MIN_BASELINE):
    for i in range(n):
        j = ear_pass(c, 0.0, seed=100 + i, asset=asset)
    assert j["state"] == "LISTENING"
    return j


# ------------------------------------------------------------------ engine
def test_embed_shape_and_gain_invariant():
    x = E.synth_machine(E.SR * 2, np.random.default_rng(1))
    a, b = E.embed(x), E.embed(x * 2.0)       # 6 dB louder = standing closer
    assert a.shape == (2 * E.N_MELS,)
    assert np.allclose(a, b, atol=1e-3)


def test_wav_roundtrip_and_resample():
    x = E.synth_machine(E.SR, np.random.default_rng(2))
    y = E.read_wav(E.to_wav(x))
    assert len(y) == len(x) and np.max(np.abs(x - y)) < 1e-3
    z = E.read_wav(E.to_wav(x[::2], sr=8000))
    assert abs(len(z) - len(x)) <= 2


def test_baseline_scores_fault_higher_synthetic():
    r = np.random.default_rng(3)
    B = E.Baseline([E.embed(E.synth_machine(E.SR * SECS, r)) for _ in range(20)]).calibrate()
    h = [B.score(E.embed(E.synth_machine(E.SR * SECS, r))) for _ in range(10)]
    f = [B.score(E.embed(E.synth_machine(E.SR * SECS, r, fault=1.0))) for _ in range(10)]
    assert min(f) > max(h)
    ex = B.explain(E.embed(E.synth_machine(E.SR * SECS, r, fault=1.0)))
    assert ex["lo_hz"] >= 1500 and ex["louder"]          # the synthetic fault is a 3.4 kHz component
    assert "kHz" in ex["band"] and len(ex["band"]) <= 16


def test_percentile_calibration_is_sane():
    r = np.random.default_rng(4)
    B = E.Baseline([E.embed(E.synth_machine(E.SR * SECS, r)) for _ in range(30)]).calibrate()
    assert B.pct(0) == 0 and B.pct(1e9) == 1


def test_mix_hits_requested_snr():
    r = np.random.default_rng(5)
    x = E.synth_machine(E.SR, r); n = E.plant_noise(E.SR, r)
    y = E.mix(x, n, 0.0)
    noise = y - x
    assert abs(10 * np.log10(np.mean(x ** 2) / np.mean(noise ** 2))) < 0.1


# ------------------------------------------------------------------ state machine (pure)
def run(seq, state=E.LISTENING):
    hist = []
    out = []
    for o in seq:
        state = E.step(state, o, hist)
        if state != E.ALERT or not out or out[-1] != E.ALERT: hist.append(o)
        out.append(state)
    return out


def test_one_odd_pass_is_only_suspect():
    assert run([False, True]) == [E.LISTENING, E.SUSPECT]


def test_three_of_four_odd_is_alert():
    assert run([True, False, True, True])[-1] == E.ALERT


def test_two_of_four_odd_never_alerts():
    assert E.ALERT not in run([True, False, True, False] * 3)


def test_suspect_clears_after_three_normal():
    assert run([True, False, False, False]) == [E.SUSPECT, E.SUSPECT, E.SUSPECT, E.LISTENING]


def test_alert_is_sticky():
    assert run([True, True, True] + [False] * 6)[-1] == E.ALERT


# ------------------------------------------------------------------ API: learn, listen, suspect, alert
def test_learning_then_listening(client):
    j = ear_pass(client, 0.0, seed=1)
    assert j["state"] == "LEARNING" and "LEARNING 1/10" in j["machine"]["state_line"]
    j = learn(client)
    assert j["machine"]["state_line"] == "P-12 · LISTENING · sounds normal"


def test_healthy_passes_stay_quiet(client):
    learn(client)
    states = [ear_pass(client, 0.0, seed=500 + i)["state"] for i in range(8)]
    assert "ALERT" not in states


def test_fault_goes_suspect_then_alert_with_band(client):
    learn(client)
    j = ear_pass(client, 1.0, seed=900)
    assert j["state"] == "SUSPECT" and j["pass"]["odd"]
    ear_pass(client, 1.0, seed=901)
    j = ear_pass(client, 1.0, seed=902)
    assert j["state"] == "ALERT"
    c = j["card"]
    assert c["kind"] == "alert" and c["lines"][0].startswith("⚠ P-12 ALERT")
    assert "kHz" in c["lines"][0]
    assert c["action"]["then"] == {"app": "navigator", "text": "P-12 noisy"}
    assert "not a diagnosis" in c["speech"]


def alert(client):
    learn(client)
    for s in range(3):
        j = ear_pass(client, 1.0, seed=910 + s)
    assert j["state"] == "ALERT"
    return j


def test_tap_opens_navigator_noise_branch(client):
    alert(client)
    j = say(client, "open", app="machine_ear")
    assert j["app"] == "navigator"
    assert j["data"]["session"]["symptom"] == "noise"
    assert j["data"]["from_machine_ear"]["asset"] == "P-12"
    assert not violations(j["card"])
    # the same branch a worker gets by voice
    nxt = say(client, "no")
    assert nxt["app"] == "navigator"


def test_status_through_os_input_contract(client):
    alert(client)
    j = say(client, "", app="machine_ear")
    assert j["app"] == "machine_ear" and j["card"]["ear_state"] == "ALERT"
    for k in ("app", "intent", "routed_by", "card", "speech", "data", "ready"):
        assert k in j                       # /os/input response shape unchanged
    j = say(client, "", app="machine_ear", ctx={})
    assert "ALERT" in j["card"]["lines"][0]


def test_false_alarm_clears(client):
    alert(client)
    j = say(client, "false alarm", app="machine_ear")
    assert j["card"]["lines"][0] == "P-12 · LISTENING · sounds normal"
    assert ear_pass(client, 0.0, seed=42)["state"] == "LISTENING"


def test_relearn(client):
    alert(client)
    j = say(client, "relearn", app="machine_ear")
    assert "LEARNING 0/10" in j["card"]["lines"][0]


def test_navigator_fix_labels_the_sound_and_relearns(client):
    alert(client)
    say(client, "open", app="machine_ear")
    say(client, "fixed: replaced the motor bearing")
    st = client.get("/os/ear/status", params={"workspace_id": "demo", "asset": "P-12"}).json()["machines"][0]
    j = say(client, "", app="machine_ear")
    assert "LEARNING" in j["card"]["lines"][0], st
    labels = client.get("/os/ear/labels", params={"workspace_id": "demo"}).json()["labels"]
    assert len(labels) == 1 and labels[0]["asset"] == "P-12" and "kHz" in labels[0]["band"]


def test_machines_are_separate(client):
    learn(client, "P-12")
    j = ear_pass(client, 0.0, seed=7, asset="P-14")
    assert j["state"] == "LEARNING"


def test_no_audio_and_no_worker_stored(client):
    learn(client)
    b64 = wav_b64(1.0, 77)
    client.post("/os/ear/pass", json={"worker_id": "secret-worker", "workspace_id": "demo", "asset": "P-12", "audio_b64": b64})
    s = workapi.store()
    blob = json.dumps(s.q("SELECT * FROM ear_assets")) + json.dumps(s.q("SELECT * FROM ear_passes"))
    assert "secret-worker" not in blob and b64[100:160] not in blob
    assert not s.q("SELECT * FROM os_events WHERE worker_id='secret-worker'") if _has_worker_col(s) else True


def _has_worker_col(s):
    try:
        s.q("SELECT worker_id FROM os_events LIMIT 1"); return True
    except Exception:  # noqa: BLE001
        return False


def test_bad_input_refused(client):
    r = client.post("/os/ear/pass", json={"worker_id": "w1", "workspace_id": "demo", "asset": "P-12", "audio_b64": "bm90IGEgd2F2"})
    assert r.status_code == 422
    r = client.post("/os/ear/pass", json={"worker_id": "w1", "workspace_id": "demo", "asset": "P-12"})
    assert r.status_code == 422
    r = client.post("/os/ear/pass", json={"worker_id": "w1", "workspace_id": "demo", "asset": "P-12", "demo_clip": "../../etc/passwd"})
    assert r.status_code == 422


def test_every_hud_line_fits():
    a = {"asset": "P-1234", "state": "ALERT", "pending": [], "history": [True] * 4, "baseline": None,
         "last": {"explain": {"band": "2.6-6.4 kHz", "hint": "high pitch: check bearing"}}}
    for st in ("LEARNING", "LISTENING", "SUSPECT", "ALERT"):
        a["state"] = st
        assert not violations(ear_app.ear_card(a)), st


# ------------------------------------------------------------------ real data (skipped without the MIMII subset)
HAVE_MIMII = os.path.isdir(os.path.join(ear_app.DATA, ear_app.DEMO_SET, "abnormal"))


@pytest.mark.skipif(not HAVE_MIMII, reason="MIMII subset not downloaded (ear_tools/fetch_mimii.py)")
def test_mimii_demo_walkby(client):
    r = client.post("/os/ear/demo/seed", json={"worker_id": "w1", "workspace_id": "demo", "asset": "P-12", "passes": 60})
    assert r.json()["source"].startswith("MIMII")
    clips = client.get("/os/ear/demo/clips").json()
    assert clips["license"] == "CC BY-SA 4.0"
    states = []
    for name in clips["normal"][:6]:
        states.append(client.post("/os/ear/pass", json={"worker_id": "w1", "workspace_id": "demo", "asset": "P-12",
                                                          "demo_clip": name}).json()["state"])
    assert "ALERT" not in states
    for name in clips["abnormal"][:6]:
        j = client.post("/os/ear/pass", json={"worker_id": "w1", "workspace_id": "demo", "asset": "P-12",
                                               "demo_clip": name}).json()
        if j["state"] == "ALERT": break
    assert j["state"] == "ALERT"
