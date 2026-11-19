"""G34: GET /health fields and the structured JSON request log (no secrets in either)."""
import json, logging

import server
import workapi

SECRET = "sk-test-SECRET-123"


def _records(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "requests"]


def test_health_fields(fresh):
    h = fresh.get("/health").json()
    assert h["status"] == "ok" and h["version"] == server.VERSION
    assert isinstance(h["uptime_s"], (int, float)) and h["uptime_s"] >= 0
    assert h["llm_backend"] == "mock" and h["docs_indexed"] == 0
    assert h["ok"] is True and h["auth"] == "off"          # older fields still there


def test_health_counts_docs(demo):
    assert demo.get("/health").json()["docs_indexed"] == 3


def test_health_has_no_secrets(fresh, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", SECRET); monkeypatch.setenv("LLM_API_KEY", SECRET)
    monkeypatch.setattr(server, "TOKEN", SECRET)
    body = fresh.get("/health").text
    assert SECRET not in body and json.loads(body)["token_required"] is True
    assert not any(k in json.loads(body) for k in ("api_key", "token", "device_token", "key"))


def test_health_and_structured_log(fresh, caplog):   # first version (research loop), kept
    with caplog.at_level(logging.INFO, logger="requests"):
        r = fresh.get("/health?secret=abc")
    assert r.status_code == 200
    rec = _records(caplog)[-1]
    assert rec["path"] == "/health" and rec["status"] == 200 and rec["method"] == "GET"
    assert "secret" not in json.dumps(rec) and rec["latency_ms"] >= 0


def test_log_line_shape_and_request_id(fresh, caplog):
    with caplog.at_level(logging.INFO, logger="requests"):
        r = fresh.get("/docs", params={"workspace_id": "demo"})
        r2 = fresh.get("/health", headers={"X-Request-ID": "abc-123"})
    a, b = _records(caplog)[-2:]
    assert set(a) == {"ts", "request_id", "method", "path", "status", "latency_ms"}
    assert a["path"] == "/docs" and a["status"] == 200 and isinstance(a["latency_ms"], float)
    assert r.headers["X-Request-ID"] == a["request_id"] and len(a["request_id"]) == 16
    assert b["request_id"] == "abc-123" == r2.headers["X-Request-ID"]   # a plain client id is echoed


def test_bad_client_request_id_replaced(fresh, caplog):
    with caplog.at_level(logging.INFO, logger="requests"):
        r = fresh.get("/health", headers={"X-Request-ID": 'x"; DROP\n' + "a" * 80})
    rid = _records(caplog)[-1]["request_id"]
    assert r.headers["X-Request-ID"] == rid and len(rid) == 16 and "DROP" not in rid


def test_log_never_has_auth_tokens_or_bodies(fresh, caplog):
    with caplog.at_level(logging.INFO, logger="requests"):
        fresh.post("/ask", json={"workspace_id": "demo", "worker_id": "w1", "question": "pump pressure " + SECRET},
                   headers={"Authorization": f"Bearer {SECRET}", "X-Device-Token": SECRET})
        fresh.get(f"/memory?worker_id=w1&token={SECRET}", headers={"Authorization": f"Bearer {SECRET}"})
        fresh.post("/docs", files={"file": ("n.txt", f"note {SECRET}".encode())}, data={"workspace_id": "demo"})
    recs = _records(caplog)
    assert len(recs) >= 3 and [r["path"] for r in recs[-3:]] == ["/ask", "/memory", "/docs"]
    everything = "\n".join(r.getMessage() for r in caplog.records)   # every logger, not just "requests"
    assert SECRET not in everything and "Bearer" not in everything


def test_error_status_logged(fresh, caplog):
    with caplog.at_level(logging.INFO, logger="requests"):
        fresh.delete("/docs/doc_nope")
        fresh.post("/ask", json={"workspace_id": "demo", "worker_id": "w1"})
    assert [r["status"] for r in _records(caplog)[-2:]] == [404, 422]


def test_auth_failure_logged_without_token(monkeypatch, tmp_path, caplog):
    from fastapi.testclient import TestClient
    from llm import MockProvider
    workapi.init(MockProvider(), db_path=str(tmp_path / "a.db"), token="", log_dir=str(tmp_path / "l"),
                 embedder=None, auth_mode="worker")
    c = TestClient(server.app)
    with caplog.at_level(logging.INFO, logger="requests"):
        r = c.get("/memory", headers={"Authorization": f"Bearer {SECRET}"})
    assert r.status_code == 401 and _records(caplog)[-1]["status"] == 401
    assert SECRET not in "\n".join(x.getMessage() for x in caplog.records)
