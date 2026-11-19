"""G43 integration fixes: /confirm ownership + shape, v0 glasses path auth in worker mode, slow LLM/OCR calls no
longer freeze the event loop (other workers and /health stay responsive)."""
import base64, socket, threading, time, urllib.request

import pytest
from fastapi.testclient import TestClient

import seed_demo, server, workapi
from conftest import check_hud, jpeg_b64
from llm import MockProvider


class FakeVision:
    name = "fake"
    def __init__(self, delay=0.0): self.delay = delay
    def answer_docs(self, q, hits, jpeg):
        time.sleep(self.delay)
        h = hits[0]
        return ["Gauge reads 88 PSI", f"Source: {h['title']} p.{h['page']}"], "The gauge reads 88 PSI.", \
               [{"doc_id": h["doc_id"], "title": h["title"], "page": h["page"], "snippet": "x"}]


@pytest.fixture
def two_workers(tmp_path):
    st = workapi.init(MockProvider(), db_path=str(tmp_path / "g43.db"), embedder=None, auth_mode="worker")
    seed_demo.seed(st)
    tok = {"w1": st.add_worker("w1"), "w2": st.add_worker("w2")}
    yield TestClient(server.app), tok
    workapi.init(MockProvider(), db_path=str(tmp_path / "g43off.db"), embedder=None, auth_mode="off")


def H(t): return {"Authorization": f"Bearer {t}"}


def test_confirm_only_by_owner_and_same_shape_as_ask(two_workers):
    c, tok = two_workers
    workapi.S["provider"] = FakeVision()
    b = c.post("/ask", json={"workspace_id": "demo", "question": "Is this pressure normal?", "image_b64": jpeg_b64()},
               headers=H(tok["w1"])).json()
    assert b["needs_confirm"]
    assert "88" not in b["answer_full"] and "88" not in " ".join(b["answer_hud"])  # held back in speech too
    assert c.post("/confirm", json={"pending_id": b["pending_id"]}, headers=H(tok["w2"])).status_code == 404
    assert c.post("/confirm", json={"pending_id": b["pending_id"], "worker_id": "w1"}, headers=H(tok["w2"])).status_code == 403
    r = c.post("/confirm", json={"pending_id": b["pending_id"]}, headers=H(tok["w1"]))  # still there for its owner
    assert r.status_code == 200, r.text
    d = r.json()
    check_hud(d["answer_hud"])
    assert d["confirmed"] and "88" in " ".join(d["answer_hud"]) and isinstance(d["latency_ms"], int)
    assert isinstance(d["sources"][0], dict) and d["sources"][0]["page"] == "14" and d["sources"][0]["title"]
    assert c.post("/confirm", json={"pending_id": b["pending_id"]}, headers=H(tok["w1"])).status_code == 404


def test_v0_glasses_path_needs_a_token_in_worker_mode(two_workers, monkeypatch):
    c, tok = two_workers
    monkeypatch.setattr(server, "TOKEN", "")
    jpeg = base64.b64decode(jpeg_b64())
    files = {"image": ("p.jpg", jpeg, "image/jpeg")}
    assert c.post("/ask?audio=none", files=files).status_code == 401
    assert c.post("/ask?audio=none", files=files, headers={"X-Device-Token": "wt_wrong"}).status_code == 401
    r = c.post("/ask?audio=none", files=files, headers={"X-Device-Token": tok["w1"]})
    assert r.status_code == 200 and len(r.json()["hud_text"]) <= 40
    assert c.get("/last").status_code == 401
    assert c.get("/last", headers=H(tok["w1"])).status_code == 200


def _serve():
    import uvicorn
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True); th.start()
    for _ in range(100):
        if srv.started: break
        time.sleep(0.05)
    return srv, f"http://127.0.0.1:{port}"


def _get_ms(url):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t = time.perf_counter(); op.open(url, timeout=10).read(); return (time.perf_counter() - t) * 1000


def test_slow_llm_and_upload_do_not_block_other_requests(demo, monkeypatch):
    workapi.S["provider"] = FakeVision(delay=1.5)
    real_add = workapi.add_document
    monkeypatch.setattr(workapi, "add_document", lambda *a: (time.sleep(1.5), real_add(*a))[1])  # stands in for OCR
    srv, base = _serve()
    try:
        import json as _j
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        body = _j.dumps({"workspace_id": "demo", "worker_id": "w1", "question": "Is this pressure normal?"}).encode()
        slow = threading.Thread(target=lambda: op.open(urllib.request.Request(
            base + "/ask", body, {"Content-Type": "application/json"}), timeout=10).read())
        slow.start(); time.sleep(0.3)
        assert _get_ms(base + "/health") < 700, "an LLM call in flight blocked /health"
        slow.join()
        bnd = "xx43"
        mp = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"workspace_id\"\r\n\r\ndemo\r\n--{bnd}\r\n"
              f"Content-Disposition: form-data; name=\"file\"; filename=\"n.md\"\r\nContent-Type: text/markdown\r\n\r\n"
              f"# Note\nCompressor oil change every 2000 hours.\r\n--{bnd}--\r\n").encode()
        up = threading.Thread(target=lambda: op.open(urllib.request.Request(
            base + "/docs", mp, {"Content-Type": f"multipart/form-data; boundary={bnd}"}), timeout=10).read())
        up.start(); time.sleep(0.3)
        assert _get_ms(base + "/health") < 700, "a slow upload (OCR) blocked /health"
        up.join()
    finally:
        srv.should_exit = True; time.sleep(0.3)
