import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from bridge import Bridge, FakeHalo
from hud import paginate
def test_paginate_fits():
    for p in paginate("Normal discharge pressure is 55-70 PSI. Check the relief valve before restarting the unit."):
        rows = p.split("\n"); assert len(rows) <= 4 and all(len(r) <= 14 for r in rows)
def test_long_word(): assert all(len(r) <= 14 for p in paginate("x"*40) for r in p.split("\n"))
def test_flow():
    seen = {}
    def ask(q, j): seen["q"], seen["j"] = q, j; return {"answer_hud": "word "*30}
    d = FakeHalo(); b = Bridge(d, ask); b.on_ask("what psi?")
    assert seen["q"] == "what psi?" and seen["j"].startswith(b"\xff\xd8") and len(b.pages) > 1
    b.on_tap(1); assert d.shown[-1] == b.pages[1]
    b.on_tap(2); assert d.shown[-1] == b.pages[0]
    b.on_tap(3); assert d.shown[-1] is None and not b.pages
def test_fallback(): 
    d = FakeHalo(); Bridge(d, lambda q, j: {}).on_ask("x"); assert d.shown[0] == "No answer"


def test_confirm_flow():
    from bridge import Bridge, FakeHalo
    d = FakeHalo(); calls = []
    ask = lambda q, j: {"needs_confirm": True, "pending_id": "p1", "answer_hud": ["secret 42 psi"]}
    conf = lambda pid, ok: (calls.append((pid, ok)), {"answer_hud": ["42 psi ok"] if ok else ["Reading discarded"]})[1]
    b = Bridge(d, ask, conf); b.on_ask("gauge?")
    assert "42" not in d.shown[-1] and calls == []
    b.on_tap(2); assert calls == []
    b.on_voice("Confirm"); assert calls == [("p1", True)] and d.shown[-1] == "42 psi ok"
    b.on_ask("again"); b.on_tap(3); assert calls[-1] == ("p1", False) and "discarded" in d.shown[-1]


def test_list_answer_hud_paginates():  # G43: the real server sends answer_hud as a list of lines
    d = FakeHalo(); b = Bridge(d, lambda q, j: {"answer_hud": ["Normal: 55-70 PSI", "Source: CP-450 Pump p.14"]})
    b.on_ask("psi?")
    assert "55-70" in d.shown[0] and all(len(r) <= 14 for p in b.pages for r in p.split("\n"))
