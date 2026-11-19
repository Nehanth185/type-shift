"""Worker OS tests: fresh SQLite DB per test, deterministic mock LLM, no network. Run: cd prototype/v0/os && pytest tests
(Kept apart from server/tests so the two conftest files never collide; run both suites, see OS_DESIGN.md.)"""
import base64, io, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OSDIR = os.path.dirname(HERE)
SERVER = os.path.join(os.path.dirname(OSDIR), "server")
sys.path[:0] = [SERVER]
_TMP = tempfile.mkdtemp(prefix="os-tests-")
os.environ.update({"LLM_PROVIDER": "mock", "TTS_ENGINE": "none", "STT_ENGINE": "none", "SEED_DEMO": "",
                   "SEED_DEMO_DOCS": "", "GLASSES_DB": os.path.join(_TMP, "import.db"),
                   "LOG_DIR": os.path.join(_TMP, "logs"), "DEVICE_TOKEN": "", "RETRIEVER": "bm25", "AUTH_MODE": "off",
                   "REQUEST_LOG": "0", "OS_LEARNING_DEFAULT": "0"})
os.environ.pop("NVIDIA_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402,F401  (mounts /os through boot.py)
import workapi  # noqa: E402
from llm import MockProvider  # noqa: E402
import worker_os  # noqa: E402  (registered by boot.py)
from worker_os.hud import violations  # noqa: E402

CTX = {"asset": "P-12", "model": "CP-450", "work_order": "WO-881", "location": "Line 2", "shift_minute": 10,
       "shift_id": "d1"}


@pytest.fixture
def client(tmp_path):
    workapi.init(MockProvider(), db_path=str(tmp_path / "t.db"), token="", log_dir=str(tmp_path / "logs"),
                 embedder=None, auth_mode="off")
    worker_os.api.LIMIT.b.clear()
    import memory
    memory._DONE_PENDING.clear()
    import seed_demo
    seed_demo.seed(workapi.store())
    return TestClient(server.app)


def learn_on(c, worker="w1"):
    r = c.post("/os/me/settings", json={"worker_id": worker, "learning": True})
    assert r.status_code == 200 and r.json()["learning"] is True


def say(c, text, worker="w1", ctx=None, status=200, **kw):
    r = c.post("/os/input", json={"worker_id": worker, "workspace_id": "demo", "text": text,
                                  "context": CTX if ctx is None else ctx, **kw})
    assert r.status_code == status, r.text
    if status != 200: return r.json()
    j = r.json()
    assert violations(j["card"]) == [], (j["card"], violations(j["card"]))
    for rc in j["ready"]:
        assert violations(rc) == [], (rc, violations(rc))
        assert rc.get("why"), rc
    return j


def jpeg_b64(color=(200, 200, 200)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


class FakeRealProvider:
    """Stands in for a real model: 'reads' 62 off any photo, and classifies text as `route_to`."""
    name = "nim"
    model = "fake"

    def __init__(self, route_to="checklist", fail=False):
        self.route_to, self.fail, self.posts = route_to, fail, 0

    def _post(self, body):
        self.posts += 1
        if self.fail: raise RuntimeError("down")
        return {"choices": [{"message": {"content": self.route_to}}]}

    def answer_docs(self, question, hits, jpeg=None):
        import grounded
        src = grounded.source_of(hits[0])
        return ["Gauge reads 62 PSI", grounded.source_line(src)], "The gauge reads 62 PSI.", [src]

    def ask(self, jpeg, question):
        from llm import Answer
        return Answer("Gauge 62 PSI", "It reads 62 PSI.", "", "fake", 0.0)
