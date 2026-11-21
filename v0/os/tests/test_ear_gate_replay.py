"""Machine Ear quality gate (ear_gate.py, D4) and replay harness (ear_tools/replay.py, D5).
Everything here is SYNTHETIC audio (ear_engine.synth_machine, a synthetic voice made below) against the real server in
mock mode. Nothing in this file was recorded with a real microphone or from a real machine."""
import base64, importlib.util, json, os, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from worker_os import ear_app
from worker_os import ear_engine as E
from worker_os import ear_gate as G

_spec = importlib.util.spec_from_file_location(
    "ear_replay", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ear_tools", "replay.py"))
R = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(R)

SR, N = E.SR, E.SR * 3
GAIN = 0.3            # synth_machine is about -14 dBFS; x GAIN is about -24 dBFS, a realistic mic level


def machine(seed=0, fault=0.0, gain=GAIN):
    return E.synth_machine(N, np.random.default_rng(seed), fault=fault) * gain


def voice(n=N, seed=0, f_lo=110.0, f_hi=170.0):
    """SYNTHETIC talking (unit RMS): a pulse train whose pitch glides, two vowel-like resonances, syllables with gaps."""
    rng = np.random.default_rng(seed); t = np.arange(n) / SR
    f0 = f_lo + (f_hi - f_lo) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t + rng.uniform(0, 6)))
    ph = 2 * np.pi * np.cumsum(f0) / SR
    y = sum(np.sin(k * ph) / k * (np.exp(-((k * f0 - 700) / 600) ** 2 / 2) + 0.5 * np.exp(-((k * f0 - 1800) / 500) ** 2 / 2))
            for k in range(1, 25))
    syl = np.convolve((np.sin(2 * np.pi * 3.0 * t + rng.uniform(0, 6)) > -0.2).astype(float), np.ones(400) / 400, "same")
    y = y * syl
    return (y / np.sqrt(np.mean(y ** 2) + 1e-9)).astype(np.float32)


def talk_over(seed=0, db=0.0):
    """A healthy machine pass with someone talking `db` dB louder than the machine."""
    m = machine(seed)
    return m + voice(seed=seed) * np.sqrt(np.mean(m ** 2)) * 10 ** (db / 20)


def wav_round(x):
    return E.read_wav(E.to_wav(x))


# ------------------------------------------------------------------ D4: the gate
@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("fault", [0.0, 0.5, 1.0])
def test_gate_accepts_clean_machine_passes_healthy_and_faulty(seed, fault):
    for x in (machine(seed, fault), wav_round(machine(seed, fault)), wav_round(machine(seed, fault, gain=1.0))):
        v = G.check(x)
        assert v.ok and v.reasons == [] and v.notes == [] and v.why == "ok", v.why


def test_gate_accepts_machine_in_plant_noise_and_with_a_steady_or_switching_hum():
    t = np.arange(N) / SR
    for seed in range(4):
        m = machine(seed)
        rm = np.sqrt(np.mean(m ** 2))
        hum = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 12)); hum = hum / np.sqrt(np.mean(hum ** 2)) * rm
        for x in (E.mix(m, E.plant_noise(N, np.random.default_rng(seed)), 0.0),      # impacts, hum, pink noise
                  m + hum,                                                          # steady harmonic tone at 150 Hz
                  m + 2 * hum * (np.sin(2 * np.pi * 1.5 * t) > 0)):                  # the same tone cycling on/off
            v = G.check(x)
            assert v.ok, v.why


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("db", [10.0, 0.0, -3.0])
def test_gate_rejects_talking_over_the_machine(seed, db):
    v = G.check(talk_over(seed, db))
    assert not v.ok and v.reasons == [G.TALKING], (v.reasons, v.metrics)
    assert "talking" in v.why and "voiced speech" in v.why
    assert v.metrics["voiced_s"] >= G.TALK_SECONDS and 100 <= v.metrics["pitch_hz"] <= 180


def test_gate_rejects_talking_alone_and_after_a_wav_round_trip():
    for x in (voice(seed=3) * 0.05, wav_round(talk_over(1, 6.0))):
        assert G.TALKING in G.check(x).reasons


def test_talking_needs_a_moving_pitch_and_enough_voiced_time():
    flat = voice(seed=0, f_lo=140.0, f_hi=140.0) * 0.05        # a buzzer: same comb, pitch never moves
    is_talk, m = G.talking(flat)
    assert not is_talk and m["pitch_spread"] < G.PITCH_SPREAD
    short = machine(0); short[:SR // 8] += voice(SR // 8, seed=0) * 0.1    # 0.125 s of voice: under TALK_SECONDS
    assert G.TALKING not in G.check(short).reasons


def test_gate_rejects_clipping_at_full_scale():
    v = G.check(wav_round(machine(0, gain=6.0)))               # overdriven and saved: flat at +-1
    assert not v.ok and G.CLIPPING in v.reasons and "clipping" in v.why
    assert v.metrics["clip_full"] >= G.CLIP_FRAC


def test_gate_rejects_clipping_below_full_scale_flat_tops():
    x = np.clip(machine(0, gain=1.0), -0.4, 0.4)               # a limiter / preamp clipping before the converter
    v = G.check(x)
    assert v.reasons == [G.CLIPPING], v.reasons
    assert v.metrics["clip_full"] == 0 and v.metrics["clip_flat"] >= G.FLAT_FRAC


def test_a_few_full_scale_samples_are_not_clipping():
    x = machine(0); x[[100, 5000, 9000]] = 1.0                 # 3 isolated peaks out of 48000
    assert G.check(x).ok
    full, flat, peak = G.clip_stats(x)
    assert 0 < full < G.CLIP_FRAC and flat == 0 and peak == 1.0


def test_gate_rejects_wrong_level_too_low_and_silence():
    for x in (machine(0, gain=0.0005), np.zeros(N, np.float32)):
        v = G.check(x)
        assert v.reasons == [G.LEVEL_LOW] and "level too low" in v.why


def test_gate_rejects_wrong_level_too_high_without_clipping():
    x = (0.9 * np.sin(2 * np.pi * 220 * np.arange(N) / SR)).astype(np.float32)     # -3.9 dBFS, peak 0.9
    v = G.check(x)
    assert v.reasons == [G.LEVEL_HIGH] and "level too high" in v.why


def test_gate_rejects_level_far_from_the_machines_reference_both_ways():
    ref = G.level_dbfs(machine(0, gain=0.1))                    # about -34 dBFS
    for g in (0.5, 1.0, 2.0):                                   # +-6 dB (1 m vs 2 m away): accepted
        assert G.check(machine(1, gain=0.1 * g), ref).ok
    for g, sign in ((0.15, "-"), (6.0, "+")):                    # -16 dB and +16 dB: rejected
        v = G.check(machine(1, gain=0.1 * g), ref)
        assert v.reasons == [G.LEVEL_OFF], v.reasons
        assert f"level {sign}16 dB" in v.why and "healthy reference" in v.why
    assert G.check(machine(1, gain=0.015)).ok                   # no reference yet: only the absolute limits apply


def test_gate_reports_every_reason_at_once():
    x = wav_round(np.clip(talk_over(0, 6.0) * 12, -1, 1))       # talking, overdriven, far too loud
    v = G.check(x, ref_db=-24.0)
    assert set(v.reasons) == {G.CLIPPING, G.LEVEL_HIGH, G.TALKING}
    assert len(v.notes) == 3 and v.why.count(";") == 2
    v = G.check(np.clip(talk_over(0, 6.0) * 2, -0.4, 0.4), ref_db=-60.0)
    assert set(v.reasons) == {G.CLIPPING, G.LEVEL_OFF, G.TALKING}


def test_gate_rejects_too_short_and_does_not_touch_the_audio():
    v = G.check(machine(0)[: SR // 2])
    assert v.reasons == [G.TOO_SHORT] and "too short" in v.why
    x = talk_over(0); before = x.copy()
    G.check(x, -24.0)
    assert np.array_equal(x, before)


def test_level_dbfs_ignores_dc_and_handles_empty():
    x = machine(0)
    assert abs(G.level_dbfs(x) - G.level_dbfs(x + 0.2)) < 0.01
    assert abs(G.level_dbfs(x * 10) - G.level_dbfs(x) - 20) < 0.01
    assert G.level_dbfs(np.zeros(0)) == float("-inf")


def test_level_reference_is_the_median_and_needs_three_passes():
    r = G.LevelRef()
    assert r.value is None
    r.add(-20); r.add(-22)
    assert r.value is None
    r.add(-60)                                                  # one outlier does not move the median much
    assert r.value == -22
    assert G.LevelRef([-10, -20, -30, -40]).value == -25


# ------------------------------------------------------------------ D5: the replay harness
def sender(client, seen=None):
    def send(body):
        if seen is not None: seen.append(body)
        r = client.post("/os/ear/pass", json=body)
        return r.status_code, r.json()
    return send


def snapshot(asset, ws="demo"):
    row = [dict(r) for r in ear_app.db().q("SELECT * FROM ear_assets WHERE workspace_id=? AND asset=?", (ws, asset))]
    n = ear_app.db().q("SELECT COUNT(*) AS n FROM ear_passes WHERE workspace_id=? AND asset=?", (ws, asset))[0]["n"]
    return json.dumps(row, sort_keys=True, default=str), n


BAD = {"talk": lambda s: talk_over(s, 6.0), "clip": lambda s: machine(s, gain=6.0),
       "quiet": lambda s: machine(s, gain=0.0005), "loud": lambda s: machine(s, gain=GAIN * 8),
       "far": lambda s: machine(s, gain=GAIN * 0.1)}     # 20 dB under the healthy level: only wrong once a reference exists


def recording(plan, fault=0.0, seed0=0):
    """plan: list of 'ok' | 'talk' | 'clip' | 'quiet' | 'loud' | 'far', one 3-second pass each."""
    return np.concatenate([machine(seed0 + i, fault) if k == "ok" else BAD[k](seed0 + i) for i, k in enumerate(plan)])


def test_cut_makes_back_to_back_three_second_passes():
    x = np.arange(int(10.5 * SR), dtype=np.float32)
    passes, left = R.cut(x)
    assert [len(p) for p in passes] == [N, N, N] and abs(left - 1.5) < 1e-6
    assert passes[0][0] == 0 and passes[1][0] == N and passes[2][-1] == 3 * N - 1
    assert R.cut(x[: N - 1]) == ([], (N - 1) / SR)
    assert len(R.cut(x, seconds=2)[0]) == 5


def test_replay_sends_only_good_passes_and_rejected_ones_never_change_the_baseline(client):
    plan = ["ok", "ok", "talk", "ok", "clip", "ok", "ok", "quiet", "ok", "ok", "ok", "ok", "ok", "far", "ok", "ok"]
    seen, lines, ref = [], [], G.LevelRef()
    s = R.replay(recording(plan), sender(client, seen), asset="fan-a", ref=ref, out=lines.append)
    good = [i + 1 for i, k in enumerate(plan) if k == "ok"]
    assert s["passes"] == 16 and s["sent"] == len(seen) == 12 and s["rejected"] == 4 and s["errors"] == 0
    assert [r["n"] for r in s["rows"] if r["status"] == R.SENT] == good
    assert set(s["rejected_why"]) >= {G.TALKING, G.CLIPPING, G.LEVEL_LOW, G.LEVEL_OFF}
    assert s["rejected_why"][G.TALKING] == [3] and s["rejected_why"][G.LEVEL_LOW] == [8]
    assert s["rejected_why"][G.LEVEL_OFF] == [14] and 5 in s["rejected_why"][G.CLIPPING]
    # what each good pass sent is exactly that 3-second slice
    x = recording(plan)
    for body, n in zip(seen, good):
        assert np.array_equal(E.read_wav(base64.b64decode(body["audio_b64"])), wav_round(x[(n - 1) * N: n * N]))
        assert body["asset"] == "fan-a" and body["workspace_id"] == "demo"
    # control: the same recording with the bad passes cut out -> the stored machine is identical
    R.replay(recording(plan)[np.repeat([k == "ok" for k in plan], N)], sender(client), asset="fan-b", out=lambda *_: None)
    a, b = ear_app.get_asset("demo", "fan-a"), ear_app.get_asset("demo", "fan-b")
    assert a["baseline"] == b["baseline"] and len(a["baseline"]["E"]) == E.MIN_BASELINE
    assert a["pending"] == b["pending"] == [] and a["history"] == b["history"] and a["state"] == b["state"] == "LISTENING"
    assert snapshot("fan-a")[1] == snapshot("fan-b")[1] == 12
    # the level reference holds the 10 learned passes only
    assert len(ref.levels) == E.MIN_BASELINE and abs(ref.value - G.level_dbfs(machine(0))) < 1.0
    # every rejection was printed with its reason
    rej = [ln for ln in lines if "REJECTED" in ln]
    assert len(rej) == 4 and "talking" in rej[0] and "clipping" in rej[1] and "level too low" in rej[2] \
        and "healthy reference" in rej[3]


@pytest.mark.parametrize("learned", [0, 4, 10, 13])
def test_a_recording_of_only_bad_passes_changes_nothing_on_the_server(client, learned):
    """Fresh machine, machine half-way through learning, machine just done learning, machine already listening."""
    ref = G.LevelRef()
    if learned:
        R.replay(recording(["ok"] * learned), sender(client), asset="fan-c", ref=ref, out=lambda *_: None)
    before, levels, seen = snapshot("fan-c"), list(ref.levels), []
    s = R.replay(recording(["talk", "clip", "quiet", "loud", "talk"], seed0=40), sender(client, seen), asset="fan-c",
                 ref=ref, out=lambda *_: None)
    assert s["sent"] == 0 and s["rejected"] == 5 and seen == []
    assert snapshot("fan-c") == before and ref.levels == levels
    assert all(r["score"] is None for r in s["rows"])


def test_replay_scores_after_learning_and_a_fault_reaches_alert(client):
    ref, lines = G.LevelRef(), []
    h = R.replay(recording(["ok"] * 13), sender(client), asset="fan-d", ref=ref, out=lines.append)
    assert h["state"] == "LISTENING" and not h["alerted"]
    assert [r["score"] for r in h["rows"][:10]] == [None] * 10 and [r["baseline_passes"] for r in h["rows"][:3]] == [1, 2, 3]
    assert all(isinstance(r["score"], float) and 0 <= r["pct"] <= 1 for r in h["rows"][10:])
    assert R.verdict(h, "healthy") == (True, "healthy baseline learned, no ALERT on the healthy recording")
    assert R.verdict(h, "faulty")[0] is False
    f = R.replay(recording(["ok", "talk", "ok", "ok", "ok"], fault=1.0, seed0=60), sender(client), asset="fan-d", ref=ref,
                 out=lines.append)
    assert f["sent"] == 4 and f["rejected_why"] == {G.TALKING: [2]} and f["alerted"] and f["state"] == "ALERT"
    assert all(r["odd"] for r in f["rows"] if r["status"] == R.SENT)
    assert R.verdict(f, "faulty") == (True, "the fault raised an ALERT")
    assert R.verdict(f, "healthy") == (False, "healthy recording raised an ALERT (false alarm)")
    assert len(ref.levels) == E.MIN_BASELINE                    # scored passes never move the level reference
    out = []
    assert R.print_summary(f, "faulty.wav", "fan-d", "faulty", out.append) is True
    text = "\n".join(out)
    assert "passes sent:     4" in text and "passes rejected: 1" in text and "talking: 1  (pass 2)" in text
    assert text.count("healthy-percentile") == 4 and "no score (rejected)  [talking" in text
    assert "machine state now: ALERT" in text and "RESULT: PASS (the fault raised an ALERT)" in text
    for r in f["rows"]:
        if r["status"] == R.SENT: assert f"score {r['score']:.3f}" in text


def test_verdicts_when_nothing_or_too_little_was_sent(client):
    s = R.replay(recording(["quiet", "quiet"]), sender(client), asset="fan-e", out=lambda *_: None)
    assert R.verdict(s, "healthy") == R.verdict(s, "faulty") == (False, "no pass was sent, nothing was tested")
    out = []
    assert R.print_summary(s, "x.wav", "fan-e", "healthy", out.append) is False
    assert "machine state now: unchanged (nothing sent)" in "\n".join(out) and out[-1].startswith("RESULT: FAIL")
    s = R.replay(recording(["ok"] * 4), sender(client), asset="fan-e", out=lambda *_: None)
    assert R.verdict(s, "healthy") == (False, "only 4 of 10 healthy passes learned, record more healthy sound")
    assert R.verdict(s, "faulty")[0] is False and "still learning" in R.verdict(s, "faulty")[1]
    assert R.print_summary(s, "x.wav", "fan-e", None, [].append) is True        # no --expect: nothing to fail
    s = R.replay(recording(["ok"] * 8, seed0=4), sender(client), asset="fan-e", out=lambda *_: None)   # healthy, scored
    assert R.verdict(s, "faulty")[0] is False and "no ALERT on the faulty recording" in R.verdict(s, "faulty")[1]


def test_a_new_baseline_restarts_the_level_reference(client):
    ref = G.LevelRef([-50.0, -50.0])                            # leftovers from an earlier baseline of the same name
    R.replay(recording(["ok"] * 3), sender(client), asset="fan-f", ref=ref, out=lambda *_: None)
    assert len(ref.levels) == 3 and abs(ref.value - G.level_dbfs(machine(0))) < 1.0


def test_an_alert_from_before_the_recording_is_not_counted_as_a_detection(client):
    quiet = lambda *_: None
    R.replay(recording(["ok"] * 10), sender(client), asset="fan-g", out=quiet)
    assert R.replay(recording(["ok"] * 4, fault=1.0, seed0=40), sender(client), asset="fan-g", out=quiet)["alerted"]
    s = R.replay(recording(["ok"] * 4, seed0=60), sender(client), asset="fan-g", out=quiet)    # healthy sound, latched ALERT
    assert s["state"] == "ALERT" and not s["alerted"] and s["alert_before"]
    assert not any(r["odd"] for r in s["rows"])
    for expect in ("faulty", "healthy"):
        ok, line = R.verdict(s, expect)
        assert ok is False and "already in ALERT before this recording" in line
    # a faulty recording played as "healthy" on a machine that was NOT in ALERT is a false alarm
    R.replay(recording(["ok"] * 10), sender(client), asset="fan-h", out=quiet)
    f = R.replay(recording(["ok"] * 4, fault=1.0, seed0=40), sender(client), asset="fan-h", out=quiet)
    assert f["alerted"] and not f["alert_before"]
    assert R.verdict(f, "healthy") == (False, "healthy recording raised an ALERT (false alarm)")


def test_server_errors_are_reported_and_not_counted_as_sent():
    lines, ref = [], G.LevelRef()
    s = R.replay(recording(["ok", "talk", "ok"]), lambda body: (422, {"detail": "nope"}), asset="fan-g", ref=ref,
                 out=lines.append)
    assert s["sent"] == 0 and s["errors"] == 2 and s["rejected"] == 1 and ref.levels == []
    assert "ERROR" in lines[0] and "server answered 422: nope" in lines[0]
    out = []
    R.print_summary(s, "x.wav", "fan-g", None, out.append)
    assert "not accepted by the server: 2" in "\n".join(out) and "no score (not accepted by the server)" in "\n".join(out)


# ------------------------------------------------------------------ the HTTP sender and the command line
class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append((self.path, dict(self.headers), body))
        code, payload = self.server.answers.pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(raw))); self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def http():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.seen, srv.answers = [], []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown(); srv.server_close()


def test_http_sender_posts_json_with_the_token_and_returns_errors(http):
    http.answers += [(200, {"state": "LEARNING"}), (422, {"detail": "bad wav"}), (500, b"<html>boom</html>")]
    send = R.http_sender(f"http://127.0.0.1:{http.server_port}/", token="tok")
    assert send({"asset": "a"}) == (200, {"state": "LEARNING"})
    assert send({"asset": "a"}) == (422, {"detail": "bad wav"})
    assert send({"asset": "a"}) == (500, {"detail": "<html>boom</html>"})
    path, headers, body = http.seen[0]
    assert path == "/os/ear/pass" and body == {"asset": "a"}
    assert headers["Authorization"] == "Bearer tok" and headers["X-Device-Token"] == "tok"
    http.answers.append((200, {}))
    R.http_sender(f"http://127.0.0.1:{http.server_port}")({"asset": "a"})
    assert "Authorization" not in http.seen[-1][1]


def test_http_sender_says_when_the_server_is_down():
    with pytest.raises(R.ServerDown, match="cannot reach http://127.0.0.1:9/os/ear/pass"):
        R.http_sender("http://127.0.0.1:9", timeout=2)({"asset": "a"})


def run_main(client, monkeypatch, tmp_path, args):
    def machine_of(server, workspace, asset, token="", timeout=30.0):
        return (client.get("/os/ear/status", params={"workspace_id": workspace, "asset": asset}).json()["machines"] or [None])[0]
    monkeypatch.setattr(R, "http_sender", lambda server, token="", timeout=30.0: sender(client))
    monkeypatch.setattr(R, "http_machine", machine_of)
    lines = []
    code = R.main([*args, "--levels", str(tmp_path / "levels.json"), "--server", "http://test"], out=lines.append)
    return code, "\n".join(lines)


def test_command_line_drops_a_stale_level_reference_for_a_machine_the_server_never_heard(client, monkeypatch, tmp_path):
    (tmp_path / "levels.json").write_text(json.dumps({"http://test|demo|fan-new": [-50.0] * 10, "other": [-20.0] * 3}))
    w = tmp_path / "h.wav"; w.write_bytes(E.to_wav(recording(["ok"] * 11)))
    code, text = run_main(client, monkeypatch, tmp_path, [str(w), "--asset", "fan-new", "--expect", "healthy"])
    assert code == 0 and "old level reference dropped" in text and "passes sent:     11" in text, text
    saved = json.loads((tmp_path / "levels.json").read_text())
    assert len(saved["http://test|demo|fan-new"]) == 10 and saved["http://test|demo|fan-new"][0] > -30
    assert saved["other"] == [-20.0] * 3
    code, text = run_main(client, monkeypatch, tmp_path, [str(w), "--asset", "fan-new"])       # known now: kept
    assert code == 0 and "old level reference dropped" not in text and "(10 learned passes)" in text


def test_http_machine_reads_status_and_reports_refusals(http, monkeypatch):
    seen = []

    def do_GET(self):
        seen.append(self.path)
        code, payload = self.server.answers.pop(0)
        raw = json.dumps(payload).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(raw))); self.end_headers()
        self.wfile.write(raw)
    monkeypatch.setattr(_Handler, "do_GET", do_GET, raising=False)
    url = f"http://127.0.0.1:{http.server_port}"
    http.answers += [(200, {"machines": []}), (200, {"machines": [{"asset": "desk fan", "state": "LISTENING"}]}),
                     (401, {"detail": "bad device token"})]
    assert R.http_machine(url, "demo", "desk fan") is None
    assert R.http_machine(url, "demo", "desk fan")["state"] == "LISTENING"
    assert seen[0] == "/os/ear/status?workspace_id=demo&asset=desk+fan"
    with pytest.raises(R.ServerDown, match="server answered 401 to the status check: bad device token"):
        R.http_machine(url, "demo", "desk fan")
    with pytest.raises(R.ServerDown, match="cannot reach"):
        R.http_machine("http://127.0.0.1:9", "demo", "a", timeout=2)


def test_command_line_healthy_then_faulty_keeps_the_level_file(client, monkeypatch, tmp_path):
    hw, fw = tmp_path / "healthy.wav", tmp_path / "faulty.wav"
    hw.write_bytes(E.to_wav(np.concatenate([recording(["ok", "talk"] + ["ok"] * 11), machine(0)[:SR]])))
    fw.write_bytes(E.to_wav(recording(["ok", "ok", "far", "ok", "ok"], fault=1.0, seed0=70)))
    code, text = run_main(client, monkeypatch, tmp_path, [str(hw), "--asset", "Desk-Fan", "--expect", "healthy"])
    assert code == 0, text
    assert "healthy level reference: none yet" in text and "40.0 s -> 3-second passes" in text
    assert "passes cut:      13  (last 1.0 s too short for a pass, not used)" in text
    assert "passes sent:     12" in text and "passes rejected: 1" in text and "REJECTED  talking" in text
    assert "RESULT: PASS" in text
    saved = json.loads((tmp_path / "levels.json").read_text())
    assert list(saved) == ["http://test|demo|desk-fan"] and len(saved["http://test|demo|desk-fan"]) == E.MIN_BASELINE
    code, text = run_main(client, monkeypatch, tmp_path, [str(fw), "--asset", "Desk-Fan", "--expect", "faulty"])
    assert code == 0, text
    assert "healthy level reference: -24 dBFS (10 learned passes)" in text      # read back from the file
    assert "level_off_reference: 1  (pass 3)" in text and "RESULT: PASS (the fault raised an ALERT)" in text
    # the machine is in ALERT now: a further run cannot be judged either way, exit code 1
    for expect in ("healthy", "faulty"):
        code, text = run_main(client, monkeypatch, tmp_path, [str(fw), "--asset", "Desk-Fan", "--expect", expect])
        assert code == 1 and "RESULT: FAIL (the machine was already in ALERT before this recording" in text, text
    # a second machine goes into the same level file without touching the first
    code, _ = run_main(client, monkeypatch, tmp_path, [str(hw), "--asset", "fan-2"])
    assert code == 0 and set(json.loads((tmp_path / "levels.json").read_text())) == {
        "http://test|demo|desk-fan", "http://test|demo|fan-2"}


def test_command_line_unreadable_file_and_server_down(tmp_path):
    bad = tmp_path / "voice.m4a"; bad.write_bytes(b"not a wav")
    lines = []
    assert R.main([str(bad), "--asset", "a"], out=lines.append) == 2
    assert "cannot read" in lines[0] and "PCM WAV" in lines[0]
    assert R.main([str(tmp_path / "missing.wav"), "--asset", "a"], out=lines.append) == 2
    ok = tmp_path / "ok.wav"; ok.write_bytes(E.to_wav(recording(["ok", "ok"])))
    lines = []
    code = R.main([str(ok), "--asset", "a", "--server", "http://127.0.0.1:9", "--levels", str(tmp_path / "l.json")],
                  out=lines.append)
    assert code == 2 and lines[-1].startswith("STOPPED: cannot reach") and not (tmp_path / "l.json").exists()


def test_level_file_survives_garbage(tmp_path):
    p = tmp_path / "l.json"
    assert R.load_levels(str(p), "k").levels == []
    p.write_text("[1, 2")
    assert R.load_levels(str(p), "k").levels == []
    R.save_levels(str(p), "k", G.LevelRef([-20.0, -21.0, -22.0]))
    assert R.load_levels(str(p), "k").value == -21.0 and R.load_levels(str(p), "other").levels == []
    p.write_text("[1, 2]")
    assert R.load_levels(str(p), "k").levels == []
    R.save_levels(str(p), "k", G.LevelRef([-30.0]))
    assert json.loads(p.read_text()) == {"k": [-30.0]}
