import os, sys
from collections import Counter

from conftest import CTX, learn_on, say
from worker_os.learn import START, Model, phase_of

HERE = os.path.dirname(os.path.abspath(__file__))


def ev(action, shift, minute, ts, ctx_asset="", signal=None, intent=None):
    app, asset, detail = action.split("|")
    return {"action": action, "app": app, "asset": asset, "detail": detail, "shift_id": shift, "shift_minute": minute,
            "phase": phase_of(minute), "ts": ts, "ctx_asset": ctx_asset, "signal": signal, "intent": intent or f"{app}.x"}


def routine(n=5):
    out = []
    for d in range(n):
        base = d * 86400
        out += [ev("gauge|P-12|pressure", f"s{d}", 5, base + 1), ev("guide|P-12|step 4", f"s{d}", 10, base + 2, "P-12"),
                ev("handoff||", f"s{d}", 460, base + 3, "P-12")]
    return out


def test_sequence_prediction_and_why():
    m = Model(routine())
    p = m.predict("gauge|P-12|pressure", "start", "P-12", Counter({"gauge|P-12|pressure": 1}))
    assert p[0]["action"] == "guide|P-12|step 4"
    assert p[0]["why"] == "After Gauge P-12 pressure: 5/5 times"
    assert "5 of 5" in p[0]["why_full"] and len(p[0]["why"]) <= 40


def test_shift_start_prediction():
    p = Model(routine()).predict(START, "start", "", Counter())
    assert p[0]["action"] == "gauge|P-12|pressure" and "first" in p[0]["why"]


def test_done_actions_are_damped():
    m = Model(routine())
    p = m.predict(START, "start", "", Counter({"gauge|P-12|pressure": 1}))
    assert p[0]["action"] != "gauge|P-12|pressure"


def test_one_offs_are_not_habits():
    evs = routine(3) + [ev("ask|C-3|noise", "s2", 20, 2 * 86400 + 2.5)]
    p = Model(evs).predict(START, "first_half", "C-3", Counter())
    assert all(x["action"] != "ask|C-3|noise" or x["score"] < 0.05 for x in p)


def test_verbosity():
    short = [ev("ask|P-12|pressure", "s1", 5, i, signal="shorter") for i in range(3)]
    assert Model.verbosity(short)[0] == "short"
    more = [ev("ask|P-12|x", "s1", 5, 1), ev("guide|P-12|seal", "s1", 6, 2, intent="guide.start"),
            ev("ask|P-12|y", "s1", 7, 3, signal="more"), ev("ask|P-12|z", "s1", 8, 4, signal="more")]
    v, why = Model.verbosity(more)
    assert v == "steps" and "more" in why
    assert Model.verbosity([])[0] == "normal"


def test_synthetic_eval_beats_baseline():
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "eval"))
    import synth_eval
    evs = synth_eval.trace()
    assert all(e["synthetic"] for e in evs)
    r = synth_eval.evaluate(evs)["ALL (shifts 2-10)"]["warm"]
    assert r["top3"] >= r["base3"] + 0.15 and r["top1"] >= r["base1"] + 0.15


# ---------------- through the API
def shift(c, d, extra=()):
    steps = [(8, "reads 62 psi"), (12, "mechanical seal replacement step 4"), (450, "handoff")] + list(extra)
    for minute, text in sorted(steps):
        say(c, text, ctx={**CTX, "shift_minute": minute, "shift_id": f"s{d}"})


def test_learning_is_opt_in(client):
    for d in range(3): shift(client, d)
    assert client.get("/os/me/events", params={"worker_id": "w1"}).json()["events"] == []
    h = client.get("/os/home", params={"worker_id": "w1", "asset": "P-12", "shift_minute": 3, "shift_id": "s9"}).json()
    assert h["ready"] == [] and h["learning"] is False and "Turn it on" in h["learning_note"]
    s = client.get("/os/me/settings", params={"worker_id": "w1"}).json()
    assert s["learning_default"] == "off (opt-in)"


def test_proactive_ready_card_with_why(client):
    learn_on(client)
    for d in range(3): shift(client, d)
    h = client.get("/os/home", params={"worker_id": "w1", "asset": "P-12", "shift_minute": 3, "shift_id": "s9"}).json()
    top = h["ready"][0]
    assert top["title"] == "Ready · Gauge P-12 pressure"
    assert top["lines"][0] == "Normal pressure 55-70 PSI"                 # pre-fetched from the docs
    assert top["why"] == "You open this first (3/3 shifts)"
    assert top["action"]["app"] == "gauge"
    # after the gauge reading, the next ready card is guide step 4
    j = say(client, "reads 60 psi", ctx={**CTX, "shift_minute": 6, "shift_id": "s9"})
    assert j["ready"][0]["title"] == "Ready · Guide P-12 step 4"
    assert j["ready"][0]["why"].startswith("After Gauge P-12 pressure: 3/3")
    # tapping the card opens step 4 directly
    act = j["ready"][0]["action"]
    j = say(client, act["text"], app=act["app"], ctx={**CTX, "shift_minute": 7, "shift_id": "s9"})
    assert j["data"]["guide"]["step"] == 4


def test_verbosity_adapts_ask(client):
    learn_on(client)
    q = "What is the normal discharge pressure?"
    long_ = say(client, q)
    assert len(long_["card"]["lines"]) == 3 or len(long_["card"]["lines"]) == 2
    say(client, "shorter")
    j = say(client, q)
    assert j["verbosity"] == "short" and len(j["card"]["lines"]) == 2 and j["card"]["lines"][-1].startswith("Source:")


def test_learned_verbosity_across_shifts(client):
    learn_on(client)
    for d in range(3):
        say(client, "shorter", ctx={**CTX, "shift_id": f"v{d}"})
    j = say(client, "What is the normal discharge pressure?", ctx={**CTX, "shift_id": "v9"})
    assert j["verbosity"] == "short" and "shorter" in j["verbosity_why"]


def test_model_endpoint_explains(client):
    learn_on(client)
    for d in range(3): shift(client, d)
    m = client.get("/os/me/model", params={"worker_id": "w1"}).json()
    assert m["actions_learned_from"] == 9
    assert "You usually start shifts with Gauge P-12 pressure (3/3)" in m["patterns"]
    j = say(client, "what have you learned")
    assert j["card"]["title"] == "Learned from 9 actions"
