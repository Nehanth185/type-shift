"""Per-worker tokens (AUTH_MODE=worker): the token, not the request body, decides who the worker is."""
import os, subprocess, sys

import pytest
from fastapi.testclient import TestClient

import seed_demo, server, workapi
from conftest import SERVER, check_hud, jpeg_b64
from llm import MockProvider


@pytest.fixture
def secured(tmp_path):
    st = workapi.init(MockProvider(), db_path=str(tmp_path / "auth.db"), embedder=None, auth_mode="worker")
    seed_demo.seed(st)
    c = TestClient(server.app)
    c.tok = {"w1": st.add_worker("w1"), "w2": st.add_worker("w2"), "locked": st.add_worker("locked", "plant-9")}
    c.h = lambda who: {"Authorization": f"Bearer {c.tok[who]}"}
    yield c
    workapi.init(MockProvider(), db_path=str(tmp_path / "off.db"), embedder=None, auth_mode="off")


def test_every_work_endpoint_needs_a_worker_token(secured):
    c = secured
    calls = [("GET", "/docs?workspace_id=demo", None), ("GET", "/memory?worker_id=w1", None),
             ("DELETE", "/memory?worker_id=w1", None), ("DELETE", "/memory/mem_x", None), ("DELETE", "/docs/doc_x", None),
             ("POST", "/context", {"worker_id": "w1", "trigger": "open_job", "label": "x"}),
             ("POST", "/guide/next", {"worker_id": "w1"}), ("POST", "/guide/prev", {"worker_id": "w1"}),
             ("POST", "/ask", {"workspace_id": "demo", "worker_id": "w1", "question": "pressure?"}), ("GET", "/whoami", None)]
    for method, path, body in calls:
        for hdr in ({}, {"Authorization": "Bearer wt_not_a_real_token"}, {"Authorization": c.tok["w1"]}):
            r = c.request(method, path, json=body, headers=hdr)
            assert r.status_code == 401, (method, path, hdr, r.status_code, r.text)
            assert r.headers.get("www-authenticate") == "Bearer"
    r = c.post("/docs", data={"workspace_id": "demo"}, files={"file": ("a.md", b"# A\nhello world text")})
    assert r.status_code == 401
    assert c.get("/health").status_code == 200 and c.get("/health").json()["auth"] == "worker"


def test_token_decides_the_worker(secured):
    c = secured
    assert c.get("/whoami", headers=c.h("w1")).json() == {"auth": "worker", "worker_id": "w1", "workspace_id": None}
    # claiming someone else's worker_id is refused, not silently rewritten
    r = c.post("/ask", json={"workspace_id": "demo", "worker_id": "w2", "question": "Is this pressure normal?"}, headers=c.h("w1"))
    assert r.status_code == 403 and "belongs to worker 'w1'" in r.json()["detail"]
    assert c.get("/memory", params={"worker_id": "w2"}, headers=c.h("w1")).status_code == 403
    assert c.delete("/memory", params={"worker_id": "w2"}, headers=c.h("w1")).status_code == 403
    assert c.post("/context", json={"worker_id": "w2", "trigger": "see_machine", "label": "CP-450"}, headers=c.h("w1")).status_code == 403
    # worker_id may be left out: the token fills it in
    r = c.post("/ask", json={"workspace_id": "demo", "question": "Is this pressure normal?"}, headers=c.h("w1"))
    assert r.status_code == 200, r.text
    check_hud(r.json()["answer_hud"])
    assert c.get("/memory", headers=c.h("w1")).json()["worker_id"] == "w1"
    assert len(c.get("/memory", headers=c.h("w1")).json()["items"]) == 1


def test_memory_is_private_between_workers(secured):
    c = secured
    c.post("/ask", json={"workspace_id": "demo", "worker_id": "w1", "question": "Note: leak check CP-450 Friday"}, headers=c.h("w1"))
    mine = c.get("/memory", params={"worker_id": "w1"}, headers=c.h("w1")).json()["items"]
    assert len(mine) == 1
    assert c.get("/memory", headers=c.h("w2")).json()["items"] == []
    # w2 cannot delete w1's item, even knowing its id (404, same as a missing id: no existence leak)
    assert c.delete(f"/memory/{mine[0]['id']}", headers=c.h("w2")).status_code == 404
    assert c.delete(f"/memory/{mine[0]['id']}", params={"worker_id": "w1"}, headers=c.h("w2")).status_code == 403
    assert c.delete("/memory", headers=c.h("w2")).json() == {"worker_id": "w2", "deleted": 0}
    assert len(c.get("/memory", headers=c.h("w1")).json()["items"]) == 1
    assert c.delete(f"/memory/{mine[0]['id']}", headers=c.h("w1")).json() == {"deleted": mine[0]["id"]}
    assert c.get("/memory", headers=c.h("w1")).json()["items"] == []


def test_guide_belongs_to_the_token_holder(secured):
    c = secured
    r = c.post("/ask", json={"workspace_id": "demo", "worker_id": "w1", "question": "How do I replace the mechanical seal?",
                             "mode": "guide"}, headers=c.h("w1"))
    assert r.json()["guide"]["step"] == 1
    assert c.post("/guide/next", json={}, headers=c.h("w2")).status_code == 404  # w2 has no guide
    assert c.post("/guide/next", json={"worker_id": "w1"}, headers=c.h("w2")).status_code == 403
    assert c.post("/guide/next", json={}, headers=c.h("w1")).json()["guide"]["step"] == 2


def test_workspace_lock(secured):
    c = secured
    assert c.get("/docs", params={"workspace_id": "demo"}, headers=c.h("locked")).status_code == 403
    assert c.get("/docs", params={"workspace_id": "plant-9"}, headers=c.h("locked")).json() == []
    r = c.post("/ask", json={"workspace_id": "demo", "question": "Is this pressure normal?"}, headers=c.h("locked"))
    assert r.status_code == 403
    r = c.post("/docs", data={"workspace_id": "demo"}, files={"file": ("a.md", b"# A\nhello world text here")}, headers=c.h("locked"))
    assert r.status_code == 403
    demo_doc = c.get("/docs", params={"workspace_id": "demo"}, headers=c.h("w1")).json()[0]["doc_id"]
    assert c.delete(f"/docs/{demo_doc}", headers=c.h("locked")).status_code == 403
    assert c.delete(f"/docs/{demo_doc}", headers=c.h("w1")).status_code == 200


def test_rotate_and_revoke(secured):
    c = secured
    st = workapi.store()
    old = c.tok["w1"]
    new = st.add_worker("w1")
    assert c.get("/whoami", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert c.get("/whoami", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    st.revoke_worker("w1")
    assert c.get("/whoami", headers={"Authorization": f"Bearer {new}"}).status_code == 401
    # only hashes are stored
    rows = st.q("SELECT * FROM workers")
    assert rows and all(new not in str(r) and c.tok["w2"] not in str(r) for r in rows)


def test_device_token_ignored_in_worker_mode_but_v0_glasses_still_use_it(tmp_path, monkeypatch):
    st = workapi.init(MockProvider(), db_path=str(tmp_path / "d.db"), token="dev-secret", embedder=None, auth_mode="worker")
    tok = st.add_worker("w1")
    c = TestClient(server.app)
    assert c.get("/docs", params={"workspace_id": "x"}, headers={"X-Device-Token": "dev-secret"}).status_code == 401
    assert c.get("/docs", params={"workspace_id": "x"}, headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    workapi.init(MockProvider(), db_path=str(tmp_path / "e.db"), token="", embedder=None, auth_mode="off")


def test_off_mode_accepts_device_token_as_bearer_and_still_needs_worker_id(tmp_path):
    workapi.init(MockProvider(), db_path=str(tmp_path / "o.db"), token="dev-secret", embedder=None, auth_mode="off")
    c = TestClient(server.app)
    assert c.get("/memory", params={"worker_id": "w1"}).status_code == 401
    assert c.get("/memory", params={"worker_id": "w1"}, headers={"Authorization": "Bearer dev-secret"}).status_code == 200
    assert c.get("/memory", headers={"X-Device-Token": "dev-secret"}).status_code == 422  # no token identity, no worker_id
    assert c.get("/whoami", headers={"X-Device-Token": "dev-secret"}).json()["auth"] == "off"
    workapi.init(MockProvider(), db_path=str(tmp_path / "p.db"), token="", embedder=None, auth_mode="off")


def test_bad_auth_mode_rejected(tmp_path):
    with pytest.raises(ValueError):
        workapi.init(MockProvider(), db_path=str(tmp_path / "x.db"), embedder=None, auth_mode="maybe")
    workapi.init(MockProvider(), db_path=str(tmp_path / "y.db"), embedder=None, auth_mode="off")


def test_workers_cli(tmp_path):
    env = {**os.environ, "GLASSES_DB": str(tmp_path / "cli.db")}
    run = lambda *a: subprocess.run([sys.executable, "workers.py", *a], cwd=SERVER, env=env, capture_output=True, text=True)
    r = run("add", "w9", "--workspace", "demo")
    assert r.returncode == 0 and r.stdout.strip().startswith("wt_"), r
    tok = r.stdout.strip()
    from store import Store
    assert Store(str(tmp_path / "cli.db")).worker_for_token(tok)["workspace_id"] == "demo"
    assert "w9\tworkspace=demo" in run("list").stdout
    assert run("revoke", "w9").returncode == 0 and run("revoke", "w9").returncode == 1
    assert Store(str(tmp_path / "cli.db")).worker_for_token(tok) is None


def test_v0_glasses_ask_keeps_device_token_while_json_ask_uses_worker_token(tmp_path, monkeypatch):
    st = workapi.init(MockProvider(), db_path=str(tmp_path / "v0.db"), embedder=None, auth_mode="worker")
    seed_demo.seed(st)
    tok = st.add_worker("w1")
    monkeypatch.setattr(server, "TOKEN", "dev-secret")
    c = TestClient(server.app)
    import base64
    jpeg = base64.b64decode(jpeg_b64())
    assert c.post("/ask?audio=none", files={"image": ("p.jpg", jpeg, "image/jpeg")}).status_code == 401
    r = c.post("/ask?audio=none", files={"image": ("p.jpg", jpeg, "image/jpeg")}, headers={"X-Device-Token": "dev-secret"})
    assert r.status_code == 200 and "hud_text" in r.json()
    r = c.post("/ask", json={"workspace_id": "demo", "question": "Is this pressure normal?"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["sources"]
    workapi.init(MockProvider(), db_path=str(tmp_path / "v0off.db"), embedder=None, auth_mode="off")
