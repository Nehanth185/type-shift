import json
import workapi
import server
from fastapi.testclient import TestClient
from conftest import CTX, jpeg_b64, learn_on, say


def events(c, w="w1"):
    return c.get("/os/me/events", params={"worker_id": w}).json()["events"]


def test_delete_one_event(client):
    learn_on(client)
    say(client, "reads 62 psi"); say(client, "handoff")
    evs = events(client)
    assert len(evs) == 2
    assert client.delete(f"/os/me/events/{evs[0]['id']}", params={"worker_id": "w1"}).status_code == 200
    assert [e["id"] for e in events(client)] == [evs[1]["id"]]
    assert client.delete(f"/os/me/events/{evs[0]['id']}", params={"worker_id": "w1"}).status_code == 404


def test_forget_all_events(client):
    learn_on(client)
    say(client, "reads 62 psi"); say(client, "handoff")
    assert client.delete("/os/me/events", params={"worker_id": "w1"}).json()["deleted"] == 2
    assert events(client) == []
    h = client.get("/os/home", params={"worker_id": "w1"}).json()
    assert h["ready"] == []


def test_forget_me_removes_memory_too(client):
    learn_on(client)
    say(client, "note: check leak"); say(client, "reads 62 psi")
    r = client.delete("/os/me", params={"worker_id": "w1"}).json()
    assert r["deleted_events"] >= 1 and r["deleted_memory"] >= 2
    assert events(client) == [] and client.get("/memory", params={"worker_id": "w1"}).json()["items"] == []


def test_voice_forget_everything(client):
    learn_on(client)
    say(client, "reads 62 psi")
    j = say(client, "forget everything")
    assert j["data"]["deleted"] >= 2 and events(client) == []


def test_forget_that_removes_latest_event(client):
    learn_on(client)
    say(client, "reads 62 psi"); say(client, "note: call Sam")
    n = len(events(client))
    say(client, "forget that")
    assert len(events(client)) == n - 1


def test_learning_off_stops_logging(client):
    learn_on(client)
    say(client, "reads 62 psi")
    say(client, "stop learning")
    say(client, "reads 63 psi")
    assert len(events(client)) == 1
    assert client.get("/os/me/settings", params={"worker_id": "w1"}).json()["learning"] is False


def test_workers_isolated(client):
    learn_on(client, "w1"); learn_on(client, "w2")
    say(client, "reads 62 psi", worker="w1")
    assert events(client, "w2") == []
    eid = events(client, "w1")[0]["id"]
    assert client.delete(f"/os/me/events/{eid}", params={"worker_id": "w2"}).status_code == 404
    assert len(events(client, "w1")) == 1


def test_token_decides_worker(tmp_path):
    workapi.init(workapi.S["provider"], db_path=str(tmp_path / "a.db"), embedder=None, auth_mode="worker")
    t1, t2 = workapi.store().add_worker("w1"), workapi.store().add_worker("w2")
    c = TestClient(server.app)
    h1, h2 = {"Authorization": f"Bearer {t1}"}, {"Authorization": f"Bearer {t2}"}
    assert c.get("/os/me/events").status_code == 401
    c.post("/os/me/settings", json={"learning": True}, headers=h1)
    assert c.post("/os/input", json={"text": "handoff", "context": CTX}, headers=h1).status_code == 200
    assert c.get("/os/me/events", params={"worker_id": "w1"}, headers=h2).status_code == 403
    assert c.get("/os/me/events", headers=h2).json()["events"] == []
    assert len(c.get("/os/me/events", headers=h1).json()["events"]) == 1
    assert c.post("/os/input", json={"worker_id": "w1", "text": "x"}, headers=h2).status_code == 403


def test_no_employer_or_workspace_read_endpoint():
    for r in server.app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/os"): continue
        params = {p.name for p in getattr(r, "dependant", None).query_params} if getattr(r, "dependant", None) else set()
        if "events" in path or "model" in path:
            assert "workspace_id" not in params, path
            assert "/me" in path, path       # only the worker's own view exists
    paths = {getattr(r, "path", "") for r in server.app.routes}
    assert not any(p.startswith("/os/") and ("team" in p or "employer" in p or "admin" in p) for p in paths)


def test_photos_never_stored(client):
    learn_on(client)
    b64 = jpeg_b64()
    say(client, "what does the gauge read", image_b64=b64)
    rows = workapi.store().q("SELECT * FROM os_events")
    blob = json.dumps(rows) + json.dumps(workapi.store().q("SELECT * FROM os_state"))
    assert b64[:40] not in blob and "image" not in blob.lower()
