import pytest
import workapi
from conftest import CTX, FakeRealProvider, jpeg_b64, say
from worker_os import apps


def test_apps_listed_with_manifests(client):
    ms = client.get("/os/apps").json()
    assert {m["id"] for m in ms} == {"ask", "guide", "gauge", "notes", "checklist", "handoff", "navigator", "machine_ear"}
    for m in ms:
        assert m["intents"] and m["permissions"] and m["default_intent"] in m["intents"]
        assert set(m["permissions"]) <= apps.PERMS


def test_ask_cites_source(client):
    j = say(client, "What is the normal discharge pressure on P-12?")
    assert j["app"] == "ask" and j["card"]["lines"][-1].startswith("Source: CP-450")
    assert "55-70" in " ".join(j["card"]["lines"])
    assert j["data"]["sources"][0]["page"] == "14"


def test_ask_not_in_docs(client):
    j = say(client, "What is the warranty phone number for the forklift?")
    assert j["card"]["lines"][0] == "Not in your docs" and j["data"]["not_in_docs"]


def test_guide_flow(client):
    j = say(client, "how do I replace the mechanical seal")
    assert j["app"] == "guide" and j["data"]["guide"] == {"step": 1, "total": 6, "text": j["data"]["guide"]["text"]}
    assert j["card"]["lines"][0].startswith("⚠ Lock out")
    assert say(client, "next")["data"]["guide"]["step"] == 2
    assert say(client, "back")["data"]["guide"]["step"] == 1
    j = say(client, "step 6")
    assert j["data"]["guide"]["step"] == 6
    j = say(client, "next")
    assert j["data"]["finished"] and j["card"]["lines"][0] == "All 6 steps done"
    assert say(client, "done, log it")["card"]["lines"][0].startswith("Logged:")


def test_guide_goto_without_active_guide_starts_it(client):
    j = say(client, "mechanical seal replacement step 4")
    assert j["app"] == "guide" and j["data"]["guide"]["step"] == 4


def test_guide_without_steps_falls_back_to_ask(client):
    j = say(client, "how do I know the normal discharge pressure")
    assert j["app"] == "ask" and j["data"]["fell_back_from"] == "guide"
    assert j["speech"].startswith("No numbered steps")


@pytest.mark.parametrize("text,status,first", [
    ("reads 48 psi", "low", "⚠ 48 PSI LOW (normal 55-70)"),
    ("P-12 reads 62 psi", "ok", "62 PSI: in range (55-70)"),
    ("pressure is 80 psi", "high", "⚠ 80 PSI HIGH (normal 55-70)"),
])
def test_gauge_spoken(client, text, status, first):
    j = say(client, text)
    assert j["app"] == "gauge" and j["data"]["status"] == status and j["data"]["value"] != 12
    assert j["card"]["lines"][0] == first and j["card"]["lines"][-1].startswith("Source:")
    mem = client.get("/memory", params={"worker_id": "w1"}).json()["items"]
    assert any(i["text"].startswith("Reading P-12 pressure") for i in mem)


def test_gauge_low_gives_doc_tip(client):
    j = say(client, "reads 45 psi")
    assert "strainer" in j["card"]["lines"][1]


def test_gauge_unknown_asset_no_range(client):
    ctx = {"asset": "FAN-9", "shift_minute": 30, "shift_id": "d1"}
    j = say(client, "vibration reads 7", ctx=ctx)
    assert j["data"]["status"] == "unknown" and "won't judge" in j["card"]["lines"][2]


def test_gauge_photo_mock_asks_for_spoken_value(client):
    j = say(client, "what does the gauge read", image_b64=jpeg_b64())
    assert j["intent"] == "gauge.photo" and j["card"]["lines"][0] == "No number I can trust"


def test_gauge_photo_real_needs_confirm(client):
    workapi.S["provider"] = FakeRealProvider()
    j = say(client, "what does the pressure gauge read", image_b64=jpeg_b64())
    assert j["data"]["needs_confirm"] and j["card"]["lines"][0] == "Reads 62 PSI?"
    assert "not verified" in j["card"]["lines"][2]
    mem = client.get("/memory", params={"worker_id": "w1"}).json()["items"]
    assert not any("Reading" in i["text"] for i in mem)          # nothing saved before the worker confirms
    j = say(client, "yes")
    assert j["intent"] == "gauge.confirm" and j["data"]["status"] == "ok" and "confirmed by you" in j["data"]["via"]


def test_gauge_photo_reject(client):
    workapi.S["provider"] = FakeRealProvider()
    say(client, "what does the pressure gauge read", image_b64=jpeg_b64())
    j = say(client, "no")
    assert j["card"]["lines"][0] == "Reading discarded"
    assert say(client, "yes")["app"] != "gauge"   # nothing pending any more


def test_gauge_dark_photo(client):
    j = say(client, "read this gauge", image_b64=jpeg_b64((5, 5, 5)))
    assert j["card"]["lines"][0] == "Can't read that"


def test_notes(client):
    say(client, "note: check leak in 7 days")
    j = say(client, "open loops")
    assert j["data"]["open"] == ["check leak in 7 days"]
    j = say(client, "forget that")
    assert j["card"]["lines"][0] == "Forgot it"
    assert say(client, "open loops")["data"]["open"] == []


def test_checklist_until_done(client):
    j = say(client, "PM checklist")
    assert j["app"] == "checklist" and j["data"]["total"] == 6
    assert j["card"]["lines"][0].startswith("☐ 1.")
    for k in range(1, 6):
        j = say(client, "check")
        assert j["data"]["done"] == k
    j = say(client, "check")
    assert j["card"]["title"].endswith("6/6 done ✓")
    mem = client.get("/memory", params={"worker_id": "w1"}).json()["items"]
    assert any(i["kind"] == "job_done" and "Checklist done" in i["text"] for i in mem)


def test_handoff_summary(client):
    say(client, "how do I replace the mechanical seal")
    say(client, "done, log it anyway")   # G46: plain "done, log it" on step 1/6 now asks first
    say(client, "note: check leak in 7 days")
    say(client, "reads 45 psi")
    say(client, "What is the warranty phone number for the forklift?")
    j = say(client, "handoff")
    d = j["data"]
    assert len(d["jobs"]) == 1 and d["open"] == ["check leak in 7 days"] and d["shared"] is False
    assert any("(low)" in r for r in d["readings"]) and d["doc_gaps"]
    assert "Nothing is sent" in j["speech"]


def test_permissions_enforced():
    m = apps.Manifest("x", "X", "", ["x.run"], ["c"], ["memory.read"])
    from worker_os.context import Ctx
    caps = apps.Caps(m, apps.Req("", "x.run", Ctx(), "w1", "demo"), jpeg=b"\xff\xd8")
    with pytest.raises(apps.PermissionDenied):
        caps.search("pressure")
    with pytest.raises(apps.PermissionDenied):
        caps.photo
    with pytest.raises(apps.PermissionDenied):
        caps.remember("note", "x")
    assert caps.has_photo   # knowing a photo exists is fine; reading it is not


def test_handoff_cannot_read_camera_or_docs():
    m, _ = apps.APPS["handoff"]
    assert "camera" not in m.permissions and "docs.read" not in m.permissions and "memory.write" not in m.permissions


def test_unknown_permission_rejected():
    with pytest.raises(ValueError):
        apps.register(apps.Manifest("bad", "Bad", "", ["b"], [], ["employer.read"]), lambda r, c: None)


def test_unknown_app_404(client):
    say(client, "hi", app="nope", status=404)


def test_context_remembers_asset_model(client):
    say(client, "reads 62 psi")                                   # sends model CP-450 for P-12
    j = say(client, "reads 62 psi", ctx={"asset": "P-12", "shift_id": "d1"})
    assert j["context"]["model"] == "CP-450" and j["data"]["status"] == "ok"
