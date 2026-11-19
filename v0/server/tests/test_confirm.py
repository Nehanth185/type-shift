"""Photo readings with numbers the docs lack need worker confirmation before being shown."""
import workapi
from conftest import jpeg_b64


class FakeVision:
    name = "fake"
    def answer_docs(self, q, hits, jpeg):
        h = hits[0]
        return ["Gauge reads 88 PSI", f"Source: {h['title']} p.{h['page']}"], "The gauge reads 88 PSI.", \
               [{"doc_id": h["doc_id"], "title": h["title"], "page": h["page"], "snippet": ""}]


def _ask(c):
    return c.post("/ask", json={"workspace_id": "demo", "worker_id": "w1", "question": "Is this pressure normal?",
                                "mode": "answer", "image_b64": jpeg_b64()})


def test_unconfirmed_reading_hidden_then_shown(demo):
    workapi.S["provider"] = FakeVision()
    r = _ask(demo)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["needs_confirm"] and "88" not in " ".join(b["answer_hud"]) and "⚠" in " ".join(b["answer_hud"])
    c = demo.post("/confirm", json={"pending_id": b["pending_id"], "worker_id": "w1"}).json()
    assert c["confirmed"] and "88" in " ".join(c["answer_hud"])
    assert demo.post("/confirm", json={"pending_id": b["pending_id"]}).status_code == 404


def test_rejected_reading_discarded(demo):
    workapi.S["provider"] = FakeVision()
    b = _ask(demo).json()
    c = demo.post("/confirm", json={"pending_id": b["pending_id"], "ok": False}).json()
    assert not c["confirmed"] and "88" not in " ".join(c["answer_hud"])


def test_pending_expires(demo, monkeypatch):
    import time as _t
    workapi.S["provider"] = FakeVision()
    monkeypatch.setenv("CONFIRM_TTL", "-1")
    b = _ask(demo).json()
    assert demo.post("/confirm", json={"pending_id": b["pending_id"]}).status_code == 404
