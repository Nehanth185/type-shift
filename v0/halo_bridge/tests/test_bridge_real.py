"""G43: the Halo bridge's HTTP functions against the REAL server (uvicorn, mock LLM, AUTH_MODE=worker, demo docs).
Before G43 http_ask sent mode "ask" and no workspace_id (422 from the server) and on_ask crashed on the list HUD."""
import io, json, os, socket, subprocess, sys, tempfile, time, urllib.request
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
SERVER = os.path.join(HERE, "..", "..", "server")
from bridge import Bridge, FakeHalo, http_ask, http_confirm  # noqa: E402

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"


def _jpeg(color=(170, 170, 170)):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (160, 120), color).save(b, "JPEG"); return b.getvalue()


@pytest.fixture(scope="module")
def live():
    tmp = tempfile.mkdtemp(prefix="halo-real-")
    env = {**os.environ, "LLM_PROVIDER": "mock", "TTS_ENGINE": "none", "STT_ENGINE": "none", "RETRIEVER": "bm25",
           "SEED_DEMO": "1", "AUTH_MODE": "worker", "DEVICE_TOKEN": "", "GLASSES_DB": os.path.join(tmp, "g.db"),
           "LOG_DIR": os.path.join(tmp, "logs"), "REQUEST_LOG": "0"}
    env.pop("NVIDIA_API_KEY", None)
    tok = subprocess.run([sys.executable, "workers.py", "add", "halo1", "--workspace", "demo"], cwd=SERVER, env=env,
                         capture_output=True, text=True, check=True).stdout.strip()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--port", str(port), "--log-level", "warning"],
                         cwd=SERVER, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try: urllib.request.urlopen(base + "/health", timeout=1); break
        except Exception: time.sleep(0.1)
    yield base, tok
    p.terminate(); p.wait(5)


def test_http_ask_real_server_cited_answer(live):
    base, tok = live
    d = FakeHalo(photo=_jpeg())
    b = Bridge(d, lambda q, j: http_ask(base, tok, q, j))
    r = b.on_ask("Is this pressure normal?")
    assert r["sources"] and r["answer_hud"][-1].startswith("Source:")
    assert d.shown[-1] == b.pages[0]
    assert all(len(row) <= 14 for p in b.pages for row in p.split("\n"))
    assert "p.14" in " ".join(b.pages).replace("\n", " ")


def test_http_confirm_unknown_id_is_404(live):
    base, tok = live
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as e:
        http_confirm(base, tok, "nope", True)
    assert e.value.code == 404
