"""POST /context -> ready card (rules over personal memory)"""
from conftest import ask, check_hud


def card(c, label, trigger="see_machine", w="w1"):
    r = c.post("/context", json={"worker_id": w, "trigger": trigger, "label": label})
    assert r.status_code == 200, r.text
    b = r.json()
    assert {"lines", "last_answer", "usual_next_step", "unfinished"} <= set(b)
    check_hud(b["lines"])
    return b


def test_first_visit_is_empty(demo):
    b = card(demo, "CP-450")
    assert b["lines"] == ["CP-450", "Nothing saved here yet", "Ask anything ▸"]
    assert b["last_answer"] is None and b["usual_next_step"] is None and b["unfinished"] == []


def test_scene_a_second_visit(demo):
    card(demo, "CP-450")
    ask(demo, "Is this pressure normal?")
    ask(demo, "Show me the seal swap", mode="guide")
    demo.post("/guide/next", json={"worker_id": "w1"})
    mid = card(demo, "CP-450")  # walked away mid-job
    assert mid["unfinished"] == ["Mechanical seal replacement: step 2/6"]
    assert mid["usual_next_step"] == "Resume Mechanical seal replacement step 2"
    assert mid["last_answer"].startswith("Normal discharge pressure is 55-70 PSI.")
    ask(demo, "Done, log it anyway")   # G46: unfinished guide (step 2/6) needs "anyway" or a repeat
    ask(demo, "Remind me to do a leak check in 7 days")
    later = card(demo, "cp-450")  # label match is case-insensitive
    assert later["lines"][0].startswith("cp-450 · Mechanical seal done")
    assert later["unfinished"] == ["do a leak check in 7 days"]
    assert later["usual_next_step"] == "Do a leak check in 7 days"


def test_card_is_scoped_to_label(demo):
    card(demo, "CP-450")
    ask(demo, "Note: check coupling guard")
    other = card(demo, "CP-999")
    assert other["unfinished"] == [] and other["last_answer"] is None


def test_start_shift_shows_everything_open(demo):
    card(demo, "CP-450"); ask(demo, "Note: check coupling guard")
    card(demo, "glasses-v0", "open_job"); ask(demo, "Note: try pull-ups tomorrow")
    b = card(demo, "day shift", "start_shift")
    assert set(b["unfinished"]) == {"check coupling guard", "try pull-ups tomorrow"}


def test_usual_next_step_from_repeat_question(demo):
    for _ in range(2):
        card(demo, "glasses-v0", "open_job")
        ask(demo, "What is the OLED address?")
    b = card(demo, "glasses-v0", "open_job")
    assert b["usual_next_step"] == "Ask: What is the OLED address?"


def test_context_is_per_worker_and_validated(demo):
    card(demo, "CP-450", w="alice"); ask(demo, "Note: check seal", worker="alice")
    assert card(demo, "CP-450", w="bob")["unfinished"] == []
    assert demo.post("/context", json={"worker_id": "w1", "trigger": "teleport", "label": "x"}).status_code == 422
    assert demo.post("/context", json={"trigger": "open_job", "label": "x"}).status_code == 422


def test_only_ten_contexts_kept(demo):
    for i in range(14):
        card(demo, f"asset-{i}")
    ctx = [i for i in demo.get("/memory", params={"worker_id": "w1"}).json()["items"] if i["kind"] == "context"]
    assert len(ctx) == 10
