"""GET /memory, DELETE /memory/{id}, DELETE /memory?worker_id=  + memory voice commands"""
from conftest import ask, jpeg_b64


def items(c, w="w1"):
    r = c.get("/memory", params={"worker_id": w}); assert r.status_code == 200
    return r.json()["items"]


def test_asks_are_remembered_as_text_only(demo):
    img = jpeg_b64()
    ask(demo, "Is this pressure normal?", image_b64=img)
    it = items(demo)
    assert len(it) == 1 and {"id", "kind", "text", "created_at"} <= set(it[0])
    assert it[0]["kind"] == "ask" and "Is this pressure normal?" in it[0]["text"]
    assert img[:40] not in str(it)  # the photo is never stored
    assert it[0]["created_at"].endswith("Z") and "T" in it[0]["created_at"]


def test_delete_one_item(demo):
    ask(demo, "Is this pressure normal?")
    ask(demo, "What is the OLED address?")
    first = items(demo)[0]
    assert demo.delete(f"/memory/{first['id']}").json() == {"deleted": first["id"]}
    assert [i["id"] for i in items(demo)] != [] and first["id"] not in [i["id"] for i in items(demo)]
    assert demo.delete(f"/memory/{first['id']}").status_code == 404


def test_delete_checks_owner_when_given(demo):
    ask(demo, "Is this pressure normal?", worker="alice")
    mid = items(demo, "alice")[0]["id"]
    assert demo.delete(f"/memory/{mid}", params={"worker_id": "bob"}).status_code == 404
    assert len(items(demo, "alice")) == 1


def test_forget_all_only_touches_that_worker(demo):
    for w in ("alice", "bob"):
        ask(demo, "Is this pressure normal?", worker=w)
        ask(demo, "Show me the seal swap", mode="guide", worker=w)
    r = demo.delete("/memory", params={"worker_id": "alice"}).json()
    assert r["deleted"] == 2 and items(demo, "alice") == []
    assert len(items(demo, "bob")) == 2
    assert demo.post("/guide/next", json={"worker_id": "alice"}).status_code == 404  # active guide forgotten too
    assert demo.post("/guide/next", json={"worker_id": "bob"}).status_code == 200


def test_no_employer_read_path(demo):
    ask(demo, "Is this pressure normal?", worker="alice")
    assert demo.get("/memory").status_code == 422  # must name a worker
    assert demo.get("/memory", params={"workspace_id": "demo"}).status_code == 422
    assert demo.delete("/memory").status_code == 422
    paths = {getattr(r, "path", "") for r in demo.app.routes}
    assert not any(p.startswith("/workspace") or "team" in p or "admin" in p for p in paths)


def test_voice_note_forget_about(demo):
    demo.post("/context", json={"worker_id": "w1", "trigger": "open_job", "label": "glasses-v0"})
    b = ask(demo, "Note: try pull-ups tomorrow.")
    assert b["answer_hud"] == ["Saved to glasses-v0 notes", "Open loop: try pull-ups tomorrow"] and b["sources"] == []
    note = [i for i in items(demo) if i["kind"] == "note"][0]
    assert note["text"] == "try pull-ups tomorrow" and note["label"] == "glasses-v0"
    about = ask(demo, "What do you know about me?")
    assert about["answer_hud"][0].startswith("I keep 2 notes") and "No photos or audio" in about["answer_full"]
    f = ask(demo, "Forget that")
    assert f["answer_hud"][0] == "Forgot it"
    assert [i["kind"] for i in items(demo)] == ["context"]


def test_done_log_it_closes_guide(demo):
    demo.post("/context", json={"worker_id": "w1", "trigger": "see_machine", "label": "CP-450"})
    ask(demo, "Show me the seal swap", mode="guide")
    b = ask(demo, "Done, log it.")       # G46: step 1/6 -> asks first, logs nothing
    assert b["answer_hud"][0] == "Only on step 1/6" and not any(i["kind"] == "job_done" for i in items(demo))
    assert demo.post("/guide/next", json={"worker_id": "w1"}).status_code == 200   # guide still open
    b = ask(demo, "Done, log it.")       # saying it again confirms
    assert b["answer_hud"] == ["Logged: Mechanical seal replacement", "CP-450"]
    kinds = [i["kind"] for i in items(demo)]
    assert "job_done" in kinds
    assert demo.post("/guide/next", json={"worker_id": "w1"}).status_code == 404


def test_done_mid_guide_needs_confirm_g46(demo):
    """The G40 bug: 'Done, log it' on step 2/6 logged the job. Now it asks; 'log it anyway' logs; finishing doesn't ask."""
    demo.post("/context", json={"worker_id": "w1", "trigger": "see_machine", "label": "CP-450"})
    ask(demo, "Show me the seal swap", mode="guide")
    demo.post("/guide/next", json={"worker_id": "w1"})
    b = ask(demo, "Done, log it")
    assert b["answer_hud"][0] == "Only on step 2/6" and "log it anyway" in b["answer_full"]
    assert not any(i["kind"] == "job_done" for i in items(demo))
    b = ask(demo, "Log it anyway")
    assert b["answer_hud"][0] == "Logged: Mechanical seal replacement"
    ask(demo, "Show me the seal swap", mode="guide")
    for _ in range(6): demo.post("/guide/next", json={"worker_id": "w1"})
    assert ask(demo, "Done, log it")["answer_hud"][0] == "Logged: Mechanical seal replacement"   # finished: no question
