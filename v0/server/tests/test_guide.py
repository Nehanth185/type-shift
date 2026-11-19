"""Guide mode: POST /ask mode=guide, POST /guide/next, POST /guide/prev"""
import grounded
from conftest import ask, check_hud


def nxt(c, w="w1"):
    r = c.post("/guide/next", json={"worker_id": w}); assert r.status_code == 200, r.text
    check_hud(r.json()["answer_hud"]); return r.json()


def prv(c, w="w1"):
    r = c.post("/guide/prev", json={"worker_id": w}); assert r.status_code == 200, r.text
    check_hud(r.json()["answer_hud"]); return r.json()


def test_seal_swap_guide_walkthrough(demo):
    b = ask(demo, "Show me the seal swap", mode="guide")
    assert b["guide"] == {"step": 1, "total": 6, "text": "Close the suction valve and the discharge valve."}
    assert b["answer_hud"][0] == "⚠ Lock out the pump before service."  # safety warning first
    assert b["answer_hud"][-1] == "Source: CP-450 Pump Manual p.21" and b["sources"][0]["page"] == "21"
    assert prv(demo)["guide"]["step"] == 1  # can't go before step 1
    s2 = nxt(demo)
    assert s2["guide"]["step"] == 2 and "drain plug" in s2["guide"]["text"] and "⚠" not in s2["answer_hud"][0]
    for _ in range(2): last = nxt(demo)
    assert last["guide"]["step"] == 4 and last["sources"][0]["page"] == "22"  # list continues on the next page
    assert prv(demo)["guide"]["step"] == 3
    for _ in range(3): last = nxt(demo)
    assert last["guide"]["step"] == 6 and last["guide"]["total"] == 6
    fin = nxt(demo)
    assert fin.get("finished") is True and fin["answer_hud"][0] == "All 6 steps done"


def test_guide_is_per_worker(demo):
    ask(demo, "Show me the seal swap", mode="guide", worker="a")
    ask(demo, "How do I clear the air-in-line alarm?", mode="guide", worker="b")
    assert nxt(demo, "a")["guide"]["total"] == 6
    b = nxt(demo, "b")
    assert b["guide"] == {"step": 2, "total": 4, "text": "Open the pump door and find the air bubble."}


def test_new_guide_replaces_old(demo):
    ask(demo, "Show me the seal swap", mode="guide")
    nxt(demo)
    b = ask(demo, "Steps for the occlusion alarm", mode="guide")
    assert b["guide"]["step"] == 1 and b["guide"]["total"] == 4 and b["guide"]["text"] == "Press Pause."


def test_no_active_guide_404(fresh):
    assert fresh.post("/guide/next", json={"worker_id": "nobody"}).status_code == 404
    assert fresh.post("/guide/prev", json={"worker_id": "nobody"}).status_code == 404
    assert fresh.post("/guide/next", json={}).status_code == 422


def test_guide_without_numbered_steps_falls_back_to_answer(demo):
    b = ask(demo, "What is the maximum casing temperature?", mode="guide")
    assert b["guide"] is None and b["sources"] and "didn't find a numbered procedure" in b["answer_full"]


def test_guide_not_in_docs(demo):
    b = ask(demo, "Guide me through changing a car tire", mode="guide")
    assert b["guide"] is None and b["answer_hud"][0] == "Not in your docs"


def test_extract_steps_handles_list_split_across_pages():
    pages = [{"seq": 0, "page": "7", "text": "## Filter change\nWARNING: Power off first.\n1. Open panel.\n2. Pull filter."},
             {"seq": 1, "page": "8", "text": "3. Insert new filter.\n4. Close panel.\n\nOther text.\n1. Unrelated list."}]
    steps, warn, first, name = grounded.extract_steps(pages, 1)  # hit on page 8 walks back to step 1 on page 7
    assert [s["n"] for s in steps] == [1, 2, 3, 4] and [s["page"] for s in steps] == ["7", "7", "8", "8"]
    assert warn == "Power off first." and first == "7" and name == "Filter change"
    assert grounded.extract_steps([{"seq": 0, "page": "1", "text": "no list here"}], 0) is None
