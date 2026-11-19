"""G37: Render free-tier deploy. Seed loader for a wiped disk, /health on Render, and the slim install
(no sentence-transformers/torch, no faster-whisper/piper, no numpy) still serving grounded /ask answers."""
import json, os, subprocess, sys, textwrap

import workapi
from conftest import ask
from llm import MockProvider

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)


def test_seed_if_empty_loads_demo_into_fresh_db(tmp_path):
    import seed_demo
    workapi.init(MockProvider(), db_path=str(tmp_path / "fresh.db"), embedder=None)
    docs = seed_demo.seed_if_empty(workapi.store())
    assert len(docs) == 3 and {d["title"] for d in docs} == {d["title"] for d in workapi.store().list_docs("demo")}
    assert workapi.store().q("SELECT COUNT(*) AS n FROM docs")[0]["n"] == 3
    assert seed_demo.seed_if_empty(workapi.store()) == []          # second start on same disk: no duplicates
    assert workapi.store().q("SELECT COUNT(*) AS n FROM docs")[0]["n"] == 3


def test_seed_if_empty_leaves_non_empty_db_alone(fresh):
    import seed_demo
    r = fresh.post("/docs", data={"workspace_id": "acme", "title": "Own doc"},
                   files={"file": ("own.md", b"# Own\n\nThe valve torque is 12 Nm.\n", "text/markdown")})
    assert r.status_code in (200, 201), r.text
    assert seed_demo.seed_if_empty(workapi.store()) == []
    assert workapi.store().list_docs("demo") == []


def test_seeded_db_answers_with_citation(tmp_path):
    import seed_demo
    from fastapi.testclient import TestClient
    import server
    workapi.init(MockProvider(), db_path=str(tmp_path / "s.db"), embedder=None)
    seed_demo.seed_if_empty(workapi.store())
    b = ask(TestClient(server.app), "Is this pressure normal?")
    assert b["answer_hud"][-1] == "Source: CP-450 Pump Manual p.14"


def test_health_hides_db_path_on_render(fresh, monkeypatch):
    assert fresh.get("/health").json()["db"]                      # local: path shown (old behaviour)
    monkeypatch.setenv("RENDER", "true")
    h = fresh.get("/health").json()
    assert h["db"] is None and h["status"] == "ok" and ".db" not in json.dumps(h)


def test_make_embedder_falls_back_to_bm25_without_sentence_transformers(monkeypatch):
    import retrieval
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)   # import now raises ImportError
    monkeypatch.setenv("RETRIEVER", "embed"); monkeypatch.delenv("EMBED_BASE_URL", raising=False)
    assert retrieval.make_embedder() is None


SLIM = textwrap.dedent("""
    import importlib.abc, json, os, sys
    BLOCK = {"sentence_transformers", "torch", "transformers", "faster_whisper", "ctranslate2", "piper",
             "onnxruntime", "numpy"}
    class Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCK: raise ImportError("blocked for slim test: " + name)
    sys.meta_path.insert(0, Blocker())
    for m in list(sys.modules):
        if m.split(".")[0] in BLOCK: del sys.modules[m]
    sys.path.insert(0, os.getcwd())
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app)
    h = c.get("/health").json()
    a = c.post("/ask", json={"workspace_id": "demo", "worker_id": "w1", "question": "Is this pressure normal?"}).json()
    loaded = sorted(m for m in sys.modules if m.split(".")[0] in BLOCK)
    print("RESULT " + json.dumps({"health": h, "ask": a, "loaded": loaded}))
""")


def test_slim_server_starts_and_answers_without_heavy_packages(tmp_path):
    """Fresh process, heavy packages made unimportable, RETRIEVER=embed asked for: BM25 fallback, demo docs
    seeded from SEED_DEMO_DOCS, a cited answer, and none of the blocked modules loaded."""
    env = {k: v for k, v in os.environ.items() if k not in ("NVIDIA_API_KEY", "EMBED_BASE_URL")}
    env.update({"LLM_PROVIDER": "mock", "RETRIEVER": "embed", "SEED_DEMO_DOCS": "1", "SEED_DEMO": "",
                "TTS_ENGINE": "auto", "STT_ENGINE": "auto", "RENDER": "true", "REQUEST_LOG": "0",
                "GLASSES_DB": str(tmp_path / "render.db"), "LOG_DIR": str(tmp_path / "logs"), "AUTH_MODE": "off"})
    p = subprocess.run([sys.executable, "-c", SLIM], cwd=SERVER, env=env, capture_output=True, text=True, timeout=120)
    line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert p.returncode == 0 and line, p.stdout + p.stderr
    r = json.loads(line[7:])
    h, a = r["health"], r["ask"]
    assert h["retriever"] == "bm25" and h["docs_indexed"] == 3 and h["db"] is None
    assert h["tts"] == "none" and h["stt"] == "none"
    assert a["answer_hud"][-1] == "Source: CP-450 Pump Manual p.14" and a["sources"]
    assert r["loaded"] == []
