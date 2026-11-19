import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine import Navigator
def test_refuse_unknown():
    n = Navigator(); assert "refused" in n.start("CP-450", "smells funny"); assert "refused" in n.start("X9", "low pressure")
def test_flow_strainer():
    n = Navigator(); r = n.start("CP-450", "low discharge pressure"); sid = r["session"]
    assert r["card"]["check_id"] == "read_gauge" and len(r["card"]["line1"]) <= 40
    c = n.answer(sid, 42); assert c["check_id"] in ("casing_hot", "strainer", "air_leak")
    seen = [c["check_id"]]
    while not c.get("done"):
        c = n.answer(sid, "yes" if c["check_id"] == "strainer" else "no")
        if not c.get("done"): seen.append(c["check_id"])
    assert c["ranked_causes"][0][0] == "clogged_strainer"
def test_every_card_cited():
    n = Navigator(); r = n.start("CP-450", "low flow"); c = r["card"]
    while not c.get("done"):
        assert c["page"] and "p." in c["line2"]; c = n.answer(r["session"], "no") if c["check_id"] != "read_gauge" else n.answer(r["session"], 40)
def test_normal_pressure_ends():
    n = Navigator(); r = n.start("CP450", "low pressure"); assert n.answer(r["session"], 60).get("done")
def test_fix_capture_boosts():
    n = Navigator(); n.start("CP-450","low pressure"); sid=list(n.sessions)[0]
    n.fix(sid, "worn_seal"); r = n.start("CP-450","low pressure"); w = n.sessions[r["session"]]["w"]; assert w["worn_seal"] > 0.1
