"""Provider-path fixes found by running the real model (Nemotron 3.5 Super VL on NIM, 2026-11-12). No network:
a local throwaway HTTP server stands in for the API where the HTTP layer itself is under test."""
import json, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import grounded, llm, workapi
from conftest import ask
from test_providers_retrieval import Stub, use

HITS = [{"doc_id": "d1", "title": "CP-450 Pump Manual", "page": "21", "text": "Remove the four casing bolts."}]


# ---------- empty / odd replies are errors, never a silent "Not in your docs"
@pytest.mark.parametrize("out", [
    {"choices": [{"message": {"content": None, "reasoning_content": "thinking..."}, "finish_reason": "length"}]},
    {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
    {"choices": [{"message": {"content": "<think>still thinking when max_tokens ran out"}, "finish_reason": "length"}]},
    {"choices": []},
])
def test_empty_reply_raises(out):
    with pytest.raises(llm.LLMEmptyReply):
        llm.message_text(out)


def test_content_as_parts_and_think_variants():
    assert llm.message_text({"choices": [{"message": {"content": [{"type": "text", "text": "HUD: a"}]}}]}) == "HUD: a"
    assert llm.strip_think("<think>x</think>HUD: ok") == "HUD: ok"
    assert llm.strip_think("reasoning without opening tag</think>\nHUD: ok") == "HUD: ok"
    assert llm.strip_think("HUD: ok") == "HUD: ok"


class RawStub(Stub):
    def _post(self, body):
        self.sent.append(body)
        return self.reply


def test_null_content_shows_service_error_not_refusal(demo):
    use(demo, RawStub({"choices": [{"message": {"content": None, "reasoning_content": "..."}, "finish_reason": "length"}]}))
    r = ask(demo, "What is the normal discharge pressure?")
    assert r["answer_hud"][0] == "AI service error" and not r.get("not_in_docs") and "no answer text" in r["error"]


# ---------- refusals the model writes in its own words (measured with Nemotron)
@pytest.mark.parametrize("reply", [
    "HUD: Torque not listed | Check manual\nFULL: The provided passage does not state a torque value for the CP-450 casing bolts.\nCITE: 1",
    "HUD: Not in docs | Impeller replacement missing\nFULL: The provided passages do not include impeller replacement steps.\nCITE: NOT_IN_DOCS",
    "HUD: Sorry\nFULL: NOT_IN_DOCS",
    "NOT_IN_DOCS",
    "HUD: No schedule given | Ask your lead\nFULL: The manual doesn't say how often to clean the strainer.\nCITE: 1",
])
def test_refusal_in_words_is_not_in_docs(reply):
    assert grounded.parse_doc_reply(reply, HITS) is None


def test_answer_with_later_caveat_is_still_an_answer():
    reply = ("HUD: Use 13 mm wrench | Remove four casing bolts\n"
             "FULL: Remove the four casing bolts with a 13 mm wrench. A torque value is not listed.\nCITE: 1")
    hud, full, srcs = grounded.parse_doc_reply(reply, HITS)
    assert hud[-1].startswith("Source:") and srcs[0]["page"] == "21"


def test_word_refusal_end_to_end(demo):
    use(demo, Stub("HUD: Torque not listed | Check manual\nFULL: The passage does not state a torque value.\nCITE: 1"))
    r = ask(demo, "What torque should I use on the CP-450 casing bolts?")
    assert r["not_in_docs"] is True and r["sources"] == [] and r["refused_by"] == "model"
    assert r["answer_hud"][0] == "Not in your docs"


def test_retrieval_refusal_is_labelled(demo):
    r = ask(demo, "What is the warranty period for the flux capacitor?")
    assert r["not_in_docs"] is True and r["refused_by"] == "retrieval"


# ---------- HTTP layer: overall deadline, one retry on 429/5xx
class _H(BaseHTTPRequestHandler):
    plan = []  # list of (status, delay_s)
    calls = 0

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        status, delay = _H.plan[min(_H.calls, len(_H.plan) - 1)]
        _H.calls += 1
        time.sleep(delay)
        body = json.dumps({"choices": [{"message": {"content": "HUD: ok\nSAY: ok"}}]} if status == 200 else {"error": "x"}).encode()
        try:
            self.send_response(status); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a): pass


@pytest.fixture
def fake_api():
    srv = HTTPServer(("127.0.0.1", 0), _H); _H.calls = 0
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_retry_once_on_429(fake_api, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*"); monkeypatch.setenv("no_proxy", "*")
    _H.plan = [(429, 0), (200, 0)]
    p = llm.OpenAICompatProvider(fake_api, "k", "some/model", timeout=20)
    assert llm.message_text(p._post({"model": "m", "messages": []})) == "HUD: ok\nSAY: ok" and _H.calls == 2


def test_no_retry_on_400(fake_api, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*"); monkeypatch.setenv("no_proxy", "*")
    _H.plan = [(400, 0)]
    p = llm.OpenAICompatProvider(fake_api, "k", "some/model", timeout=20)
    with pytest.raises(Exception):
        p._post({"model": "m", "messages": []})
    assert _H.calls == 1


def test_deadline_is_enforced(fake_api, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*"); monkeypatch.setenv("no_proxy", "*")
    _H.plan = [(200, 6)]
    p = llm.OpenAICompatProvider(fake_api, "k", "some/model", timeout=2)
    t = time.time()
    with pytest.raises(Exception):
        p._post({"model": "m", "messages": []})
    assert time.time() - t < 5


def test_timeout_env(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT", "12")
    assert llm.OpenAICompatProvider("http://x/v1", "k", "m").timeout == 12


# ---------- the model gets whole pages; the app's source snippet stays the matched chunk
def test_model_sees_whole_page_snippet_stays_chunk(demo):
    p = use(demo, Stub("HUD: Call charge nurse | if alarm repeats\nFULL: Call the charge nurse if the alarm repeats.\nCITE: 1"))
    r = ask(demo, "Who do I call if the air-in-line alarm keeps repeating?")
    prompt = p.sent[-1]["messages"][-1]["content"][0]["text"]
    assert "Call the charge nurse if the alarm repeats." in prompt          # chunk 1 alone ends at "...Call the"
    assert "No photo is attached." in prompt
    assert r["sources"][0]["page"] == "1" and r["sources"][0]["snippet"] == "Call the charge nurse if the alarm repeats."
    assert len(r["sources"][0]["snippet"]) <= 201


def test_photo_line_when_image_sent(demo):
    from conftest import jpeg_b64
    p = use(demo, Stub("HUD: Normal is 55-70 PSI\nFULL: Normal discharge pressure is 55-70 PSI.\nCITE: 1"))
    ask(demo, "Is this discharge pressure normal?", image_b64=jpeg_b64())
    assert "A photo from the glasses is attached." in p.sent[-1]["messages"][-1]["content"][0]["text"]


# ---------- numbers the docs don't contain are never shown as doc facts
def test_invented_gauge_reading_is_flagged(demo):
    from conftest import jpeg_b64
    use(demo, Stub("HUD: 55-70 PSI | Gauge reads about 60 PSI\nFULL: The gauge shows about 60 PSI, within 55-70 PSI.\nCITE: 1"))
    r = ask(demo, "Is this discharge pressure normal for the CP-450?", image_b64=jpeg_b64())
    assert r["unverified_numbers"] == ["60"]
    assert r["answer_hud"][0] == "55-70 PSI" and r["answer_hud"][1].startswith("⚠ Photo reading")
    assert r["answer_hud"][-1].startswith("Source:") and r["answer_full"].startswith("I read a number off the photo") \
        and "60" not in r["answer_full"] and "55-70 PSI" in r["answer_full"]  # G43: photo readings held back in speech too


def test_code_read_from_photo_is_flagged(demo):
    from conftest import jpeg_b64
    use(demo, Stub("HUD: Serial IS7P205\nFULL: The serial number is IS7P205.\nCITE: 1"))
    r = ask(demo, "What is the serial number on this CP-450 pump?", image_b64=jpeg_b64())
    if not r.get("not_in_docs"):
        assert "205" in r["unverified_numbers"] and r["answer_hud"][0].startswith("⚠ Photo reading")


def test_doc_numbers_pass_untouched(demo):
    use(demo, Stub("HUD: 55-70 PSI normal | Run 2 min first\nFULL: Normal discharge pressure is 55-70 PSI.\nCITE: 1"))
    r = ask(demo, "What is the normal discharge pressure on the CP-450?")
    assert "unverified_numbers" not in r and r["answer_hud"][0] == "55-70 PSI normal"


def test_number_from_question_is_fine(demo):
    use(demo, Stub("HUD: 45 PSI is low | Check suction strainer\nFULL: 45 PSI is below 50: check the strainer.\nCITE: 1"))
    r = ask(demo, "The pump reads 45 PSI. What should I check?")
    assert "unverified_numbers" not in r


# ---------- every raw Nemotron reply seen on 2026-11-12, with how it must be read
REAL_REPLIES = [  # (raw reply, refusal?)
    ("HUD: Torque not specified | Ask for torque value\nFULL: The CP-450 manual does not list a torque value for the casing bolts.\nCITE: 1", True),
    ("HUD: Not in docs | Ask for reading\nFULL: NOT_IN_DOCS\nCITE:", True),
    ("HUD: Not in docs | Not in docs\nFULL: NOT_IN_DOCS\nCITE:", True),
    ("HUD: Not specified | Ask supervisor\nFULL: The passages do not state how often the suction strainer should be cleaned.\nCITE: NOT_IN_DOCS", True),
    ("HUD: Not in docs | Ask again\nFULL: The passages do not state the maximum flow rate for the CP-450 pump.\nCITE: 1", True),
    ("HUD: Impeller replacement not documented | Seal replacement only\nFULL: The passages only cover mechanical seal replacement, not impeller replacement.\nCITE: 1", True),
    ("HUD: Call info missing | in passage\nFULL: The passage cuts off before saying who to call for a repeating air-in-line alarm.\nCITE: 1", None),  # chunk bug, fixed upstream
    ("HUD: After 7 days of running | NOT_IN_DOCS\nFULL: Do a leak check again after 7 days of running.\nCITE: 1", False),
    ("HUD: Normal discharge pressure | 55-70 PSI\nFULL: Normal discharge pressure is 55-70 PSI.\nCITE: 1", False),
    ("HUD: 55-70 PSI normal | Need current pressure reading\nFULL: Normal discharge pressure is 55-70 PSI. Please tell me the pressure shown on the discharge gauge after the pump has run for at least 2 minutes.\nCITE: 1", False),
    ("HUD: 55-70 PSI | Can't read gauge\nFULL: Normal discharge pressure is 55-70 PSI. I can't clearly read the pressure gauge in the image, so please tell me the current reading.\nCITE: 1", False),
]


@pytest.mark.parametrize("raw,refusal", [r for r in REAL_REPLIES if r[1] is not None])
def test_real_nemotron_replies(raw, refusal):
    out = grounded.parse_doc_reply(raw, HITS)
    assert (out is None) == refusal, out
    if out:
        assert not any("NOT_IN_DOCS" in l for l in out[0]) and out[0][-1].startswith("Source:")
