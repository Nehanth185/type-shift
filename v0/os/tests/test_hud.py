import pytest
from conftest import CTX, learn_on, say
from worker_os.hud import card, violations

INPUTS = ["What is the normal discharge pressure on P-12?", "how do I replace the mechanical seal", "next", "step 3",
          "reads 48 psi", "reads 62 psi", "PM checklist", "check", "note: " + "very long note " * 20, "open loops",
          "handoff", "home", "shorter", "what have you learned", "open guide", "open ask", "x" * 300,
          "What is the warranty phone number for the forklift?", "what do you know about me", "done, log it"]


def test_every_card_fits(client):
    learn_on(client)
    for d in range(2):
        for t in INPUTS:
            say(client, t, ctx={**CTX, "shift_id": f"h{d}", "asset": "P-12-LONG-ASSET-NAME-THAT-GOES-ON"})
    # say() asserts violations() == [] for the card and every ready card


def test_card_clamps():
    c = card("ask", "A very long title that will never fit on the glasses display", ["word " * 30, "x" * 90, "a", "b"],
             why="because you always do this thing at the start of every single shift")
    assert violations(c) == []
    assert len(c["lines"]) == 3 and c["lines"][0].endswith("…")


def test_violations_detects():
    assert violations({"title": "ok", "lines": ["x" * 41]})
    assert violations({"title": "ok", "lines": []})
    assert violations({"title": "one two three four five six seven eight", "lines": ["ok"]})


def test_home_card_fits(client):
    h = client.get("/os/home", params={"worker_id": "w1", "asset": "P-12", "work_order": "WO-123456789",
                                       "shift_minute": 125}).json()
    assert violations(h["card"]) == [] and h["card"]["lines"][0] == "2:05 into shift"
