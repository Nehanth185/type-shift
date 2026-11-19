"""pytest fixtures: every test gets a fresh SQLite DB and the deterministic mock LLM. No network, no API key."""
import base64, io, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path[:0] = [SERVER, os.path.join(SERVER, "tools")]
_TMP = tempfile.mkdtemp(prefix="glasses-tests-")
os.environ.update({"LLM_PROVIDER": "mock", "TTS_ENGINE": "none", "STT_ENGINE": "none", "SEED_DEMO": "",
                   "GLASSES_DB": os.path.join(_TMP, "import.db"), "LOG_DIR": os.path.join(_TMP, "logs"),
                   "DEVICE_TOKEN": "", "RETRIEVER": "bm25", "AUTH_MODE": "off"})
os.environ.pop("NVIDIA_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
import workapi  # noqa: E402
from llm import MockProvider  # noqa: E402

HUD_LINES, HUD_WORDS, HUD_CHARS = 3, 7, 40


def check_hud(lines):
    assert isinstance(lines, list) and 1 <= len(lines) <= HUD_LINES, lines
    for l in lines:
        assert len(l.split()) <= HUD_WORDS, f"too many words: {l!r}"
        assert len(l) <= HUD_CHARS, f"too many characters: {l!r}"


@pytest.fixture
def fresh(tmp_path):
    workapi.init(MockProvider(), db_path=str(tmp_path / "t.db"), token="", log_dir=str(tmp_path / "logs"), embedder=None)
    import memory
    memory._DONE_PENDING.clear()
    return TestClient(server.app)


@pytest.fixture
def demo(fresh):
    import seed_demo
    docs = seed_demo.seed(workapi.store())
    assert len(docs) == 3
    fresh.docs = {d["title"]: d for d in docs}
    return fresh


def ask(client, question, mode="answer", worker="w1", ws="demo", **kw):
    r = client.post("/ask", json={"workspace_id": ws, "worker_id": worker, "question": question, "mode": mode, **kw})
    assert r.status_code == 200, r.text
    body = r.json()
    check_hud(body["answer_hud"])
    assert isinstance(body["latency_ms"], int)
    return body


def jpeg_b64(color=(200, 200, 200), fmt="JPEG"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(buf, fmt)
    return base64.b64encode(buf.getvalue()).decode()


import pytest as _pt


@_pt.fixture(autouse=True)
def _reset_ask_limit():
    import workapi
    workapi.ASK_LIMIT.b.clear()
    yield
