"""POST /ask (JSON, v1): grounded answers, citations, "not in your docs", images, validation."""
import base64

import pytest

from conftest import ask, check_hud, jpeg_b64

SCENE_QUESTIONS = ["Is this pressure normal?", "Which pins are I2C on this board?", "Why is the screen blank?",
                   "How do I clear this pump alarm?", "What does the occlusion alarm mean?",
                   "What is the maximum casing temperature?", "What is the OLED address?"]


def test_answer_cites_source(demo):
    b = ask(demo, "Is this pressure normal?")
    assert b["answer_hud"][0] == "Normal discharge pressure is 55-70 PSI."
    assert b["answer_hud"][-1] == "Source: CP-450 Pump Manual p.14"
    s = b["sources"][0]
    assert set(s) == {"doc_id", "title", "page", "snippet"} and s["page"] == "14" and "55-70 PSI" in s["snippet"]
    assert "page 14" in b["answer_full"] and b["guide"] is None


@pytest.mark.parametrize("q", SCENE_QUESTIONS)
def test_every_doc_answer_has_source_and_fits_hud(demo, q):
    b = ask(demo, q)
    assert b["sources"], q
    assert b["answer_hud"][-1].startswith("Source:")
    check_hud(b["answer_hud"])


def test_engineer_pins(demo):
    b = ask(demo, "Which pins are I2C on this board?")
    hud = " ".join(b["answer_hud"])
    assert "D4 (GPIO5)" in hud and "D5 (GPIO6)" in hud and "bench notes p.1" in hud


@pytest.mark.parametrize("q", ["What's the weather tomorrow?", "How do I make pasta?", "Who won the game last night?"])
def test_not_in_your_docs_instead_of_guessing(demo, q):
    b = ask(demo, q)
    assert b["answer_hud"][0] == "Not in your docs" and b["sources"] == [] and b.get("not_in_docs") is True
    assert "won't guess" in b["answer_full"]


def test_empty_workspace_is_not_in_docs(fresh):
    b = ask(fresh, "Is this pressure normal?", ws="empty")
    assert b["answer_hud"][0] == "Not in your docs"


def test_workspaces_do_not_leak(demo):
    b = ask(demo, "Is this pressure normal?", ws="other-company")
    assert b["sources"] == []


def test_missing_key_word_is_flagged_not_answered(demo):
    b = ask(demo, "What is the torque for the impeller bolts?")
    assert b["answer_hud"][0] == "No 'torque' in docs. Closest:"
    assert "don't mention 'torque'" in b["answer_full"] and b["sources"]


def test_answer_mode_says_when_steps_exist(demo):
    b = ask(demo, "How do I clear the air-in-line alarm?")
    assert b.get("guide_available") is True and "guide mode" in b["answer_full"]


def test_dark_image_cant_read(demo):
    b = ask(demo, "Is this pressure normal?", image_b64=jpeg_b64((5, 5, 5)))
    assert b["answer_hud"][0] == "Can't read that" and b["sources"] == []


def test_png_and_data_url_images_accepted(demo):
    b = ask(demo, "Is this pressure normal?", image_b64="data:image/png;base64," + jpeg_b64(fmt="PNG"))
    assert b["sources"][0]["page"] == "14"


def test_bad_image_and_validation(demo):
    base = {"workspace_id": "demo", "worker_id": "w1", "question": "Is this pressure normal?"}
    assert demo.post("/ask", json={**base, "image_b64": base64.b64encode(b"not an image").decode()}).status_code == 400
    assert demo.post("/ask", json={**base, "image_b64": "%%%"}).status_code == 400
    assert demo.post("/ask", json={**base, "mode": "chat"}).status_code == 422
    assert demo.post("/ask", json={"workspace_id": "demo", "worker_id": "w1"}).status_code == 422
    assert demo.post("/ask", json={**base, "question": ""}).status_code == 422
    assert demo.post("/ask", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 400


def test_v0_multipart_ask_still_works(demo):
    raw = base64.b64decode(jpeg_b64())
    r = demo.post("/ask?audio=none", files={"image": ("p.jpg", raw, "image/jpeg")}, data={"question": "hi"})
    assert r.status_code == 200 and "hud_text" in r.json()
    r = demo.post("/ask?audio=none", content=raw, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200 and "hud_text" in r.json()


def test_health_reports_retriever(demo):
    h = demo.get("/health").json()
    assert h["provider"] == "mock" and h["retriever"] == "bm25"


def test_photo_only_ask_is_answered_from_the_photo_and_marked_ungrounded(demo):
    r = demo.post("/ask", json={"workspace_id": "demo", "worker_id": "w1", "question": "", "image_b64": jpeg_b64()})
    assert r.status_code == 200, r.text
    b = r.json()
    check_hud(b["answer_hud"])
    assert b["ungrounded"] is True and b["sources"] == [] and b["answer_hud"][-1] == "Not from your docs"
    dark = demo.post("/ask", json={"workspace_id": "demo", "worker_id": "w1", "image_b64": jpeg_b64((5, 5, 5))}).json()
    assert dark["answer_hud"][0] == "Can't read that"
    assert demo.post("/ask", json={"workspace_id": "demo", "worker_id": "w1", "question": "  "}).status_code == 422
    assert demo.post("/ask", json={"workspace_id": "demo", "question": "pressure?"}).status_code == 422  # no worker_id, no token


def test_companion_app_is_served_and_its_python_is_not(fresh):
    r = fresh.get("/app/")
    assert r.status_code == 200 and "Work Glasses" in r.text
    assert fresh.get("/app/app.js").status_code == 200
    assert fresh.get("/app/mock_server.py").status_code == 404
    assert fresh.get("/app/tests/test_companion.py").status_code == 404
    assert fresh.get("/", follow_redirects=False).headers["location"] == "/app/"


def test_g47_v0_firmware_ask_is_grounded_when_workspace_set(demo, monkeypatch):
    monkeypatch.setenv("V0_WORKSPACE", "demo")
    raw = base64.b64decode(jpeg_b64())
    r = demo.post("/ask?audio=none", files={"image": ("p.jpg", raw, "image/jpeg")}, data={"question": "Is this pressure normal?"})
    b = r.json()
    assert r.status_code == 200 and b["grounded"] is True and "55-70 PSI" in b["hud_text"] and b["sources"]
    r = demo.post("/ask?audio=none", files={"image": ("p.jpg", raw, "image/jpeg")}, data={"question": "How do I make pasta?"})
    assert r.json()["hud_text"].startswith("Not in your docs")
