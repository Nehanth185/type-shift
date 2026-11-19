"""LLM provider selection + the real-LLM doc-answer path (with _post stubbed: no network, no key),
retrieval gate, optional embeddings path (fake embedder), device token on the new endpoints."""
import hashlib, math

import grounded, llm, retrieval, workapi
from conftest import ask, check_hud, jpeg_b64
from fastapi.testclient import TestClient
import server


def test_provider_selection(monkeypatch):
    for k in ("LLM_PROVIDER", "NVIDIA_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert llm.make_provider().name == "mock"
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    p = llm.make_provider()
    assert p.name == "nim" and p.base_url == "https://integrate.api.nvidia.com/v1"
    assert p.model == "nvidia/nemotron-3.5-super-vl-120b-a12b"
    monkeypatch.setenv("LLM_PROVIDER", "openai"); monkeypatch.setenv("LLM_BASE_URL", "http://localhost:9/v1")
    monkeypatch.setenv("LLM_API_KEY", "k"); monkeypatch.setenv("LLM_MODEL", "some/model")
    p = llm.make_provider()
    assert p.name == "openai" and p.base_url == "http://localhost:9/v1" and p.model == "some/model"
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert llm.make_provider().name == "mock"


class Stub(llm.OpenAICompatProvider):
    def __init__(self, reply, model="nvidia/nemotron-3.5-super-vl-120b-a12b"):
        super().__init__("http://stub/v1", "k", model)
        self.reply, self.sent = reply, []

    def _post(self, body):
        self.sent.append(body)
        if isinstance(self.reply, Exception): raise self.reply
        return {"choices": [{"message": {"content": self.reply}}]}


def use(demo, provider):
    workapi.S["provider"] = provider
    return provider


def test_llm_doc_answer_request_and_citation(demo):
    p = use(demo, Stub("<think>hmm</think>HUD: CP-450 normal: 55-70 PSI | Gauge reads about 62 PSI ok\n"
                       "FULL: Normal is 55 to 70 PSI and the gauge shows about 62.\nCITE: 1"))
    b = ask(demo, "Is this pressure normal?", image_b64=jpeg_b64())
    body = p.sent[0]
    assert body["model"] == "nvidia/nemotron-3.5-super-vl-120b-a12b" and body["reasoning_effort"] == "none"
    assert body["messages"][0]["role"] == "system" and "NOT_IN_DOCS" in body["messages"][0]["content"]
    user = body["messages"][1]["content"]
    assert "[1] CP-450 Pump Manual" in user[0]["text"] and user[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # 62 is not in the docs (it is the model's reading of the photo), so since 2026-11-12 it is flagged, not shown as fact
    assert b["answer_hud"] == ["CP-450 normal: 55-70 PSI", "⚠ Photo reading: check it yourself", "Source: CP-450 Pump Manual p.14"]
    assert b["unverified_numbers"] == ["62"]
    assert b["sources"][0]["page"] == "14" and "55-70 PSI" in b["answer_full"] and "62" not in b["answer_full"]  # G43: held back until /confirm


def test_llm_not_in_docs_and_bad_cites(demo):
    use(demo, Stub("NOT_IN_DOCS"))
    b = ask(demo, "Is this pressure normal?")
    assert b["answer_hud"][0] == "Not in your docs" and b["sources"] == []
    use(demo, Stub("HUD: a very long line that keeps going and going and going past seven words\nFULL: x\nCITE: 9"))
    b = ask(demo, "Is this pressure normal?")
    check_hud(b["answer_hud"])
    assert b["sources"] and b["answer_hud"][-1].startswith("Source:")  # invalid cite -> top passage


def test_llama_profile_merges_system_prompt(demo):
    p = use(demo, Stub("HUD: ok\nFULL: ok\nCITE: 1", model="meta/llama-3.2-90b-vision-instruct"))
    ask(demo, "Is this pressure normal?")
    msgs = p.sent[0]["messages"]
    assert [m["role"] for m in msgs] == ["user"] and msgs[0]["content"][0]["text"].startswith(grounded.DOC_SYSTEM[:40])


def test_llm_error_still_answers_the_glasses(demo):
    use(demo, Stub(RuntimeError("LLM HTTP 500: boom")))
    b = ask(demo, "Is this pressure normal?")
    assert b["answer_hud"][0] == "AI service error" and "boom" in b["error"]


def test_retrieval_gate_and_synonyms():
    chunks = [{"chunk_id": 1, "doc_id": "d", "seq": 0, "page": "1", "title": "Pump", "emb": None,
               "text": "Mechanical seal replacement. Normal discharge pressure is 55-70 PSI."},
              {"chunk_id": 2, "doc_id": "d", "seq": 1, "page": "2", "title": "Pump", "emb": None,
               "text": "Clean the strainer weekly."}]
    assert retrieval.search(chunks, "seal swap")[0]["page"] == "1"  # swap ~ replacement
    assert retrieval.search(chunks, "how do I bake bread") == []
    assert retrieval.search(chunks, "what do you think") == []  # only stop words
    assert retrieval.search([], "anything") == []


class FakeEmbedder:
    name = "fake-hash"

    def encode(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 32
            for w in retrieval.tokens(t):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 32] += 1
            n = math.sqrt(sum(x * x for x in v)) or 1
            out.append([x / n for x in v])
        return out


def test_embedding_path_with_fake_embedder(tmp_path):
    from llm import MockProvider
    import seed_demo
    workapi.init(MockProvider(), db_path=str(tmp_path / "e.db"), embedder=FakeEmbedder())
    seed_demo.seed(workapi.store())
    c = TestClient(server.app)
    hits = retrieval.search(workapi.store().chunks_for("demo"), "Is this pressure normal?", embedder=FakeEmbedder())
    assert hits and hits[0]["cosine"] is not None
    b = ask(c, "Is this pressure normal?")
    assert b["sources"][0]["page"] == "14"
    assert c.get("/health").json()["retriever"] == "bm25+embed:fake-hash"


def test_device_token_required_on_new_endpoints(tmp_path):
    from llm import MockProvider
    workapi.init(MockProvider(), db_path=str(tmp_path / "a.db"), token="secret", embedder=None)
    c = TestClient(server.app)
    assert c.get("/docs", params={"workspace_id": "x"}).status_code == 401
    assert c.get("/memory", params={"worker_id": "x"}).status_code == 401
    assert c.post("/context", json={"worker_id": "x", "trigger": "open_job", "label": "y"}).status_code == 401
    assert c.post("/guide/next", json={"worker_id": "x"}).status_code == 401
    assert c.get("/docs", params={"workspace_id": "x"}, headers={"X-Device-Token": "secret"}).status_code == 200


def test_hud_wrap_rules():
    for text in ["one two three", "a " * 40, "Reassemble, refill, open both valves, and run for 10 minutes while you check."]:
        lines = grounded.wrap(text)
        assert len(lines) <= 3 and all(len(l.split()) <= 7 for l in lines)
    assert grounded.wrap("a " * 40)[-1].endswith("…")
    assert grounded.clamp_line("one two three four five six seven eight") == "one two three four five six seven…"
    # character cap (HUD_CHARS, default 40): long words wrap/cut by width, not just by word count
    long = "Normal discharge pressure is 55-70 PSI at rated flow, measured downstream of the valve."
    assert all(len(l) <= grounded.HUD_CHARS and len(l.split()) <= 7 for l in grounded.wrap(long))
    assert len(grounded.clamp_line("x" * 90)) == grounded.HUD_CHARS and grounded.clamp_line("x" * 90).endswith("…")
    assert grounded.wrap("Step 2/6: Drain the casing through the drain plug at the bottom (Figure 7).", max_lines=2) == \
        ["Step 2/6: Drain the casing through the", "drain plug at the bottom (Figure 7)."]
