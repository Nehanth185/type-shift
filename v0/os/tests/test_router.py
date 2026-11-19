import pytest
from conftest import FakeRealProvider
from worker_os.router import keyword_route, llm_route, route

CASES = [
    ("reads 48 psi", "gauge", "gauge.read"),
    ("pressure is 62 PSI", "gauge", "gauge.read"),
    ("how do I replace the mechanical seal", "guide", "guide.start"),
    ("walk me through the seal swap", "guide", "guide.start"),
    ("go to step 4", "guide", "guide.goto"),
    ("PM checklist for the pump", "checklist", "checklist.start"),
    ("give me my handoff", "handoff", "handoff.summary"),
    ("end of shift", "handoff", "handoff.summary"),
    ("note: check leak in 7 days", "notes", "notes.note"),
    ("forget that", "notes", "notes.forget"),
    ("what do you know about me", "notes", "notes.about"),
    ("open loops", "notes", "notes.list"),
    ("What is the normal discharge pressure?", "ask", "ask.question"),
    ("home", "os", "os.home"),
    ("shorter", "os", "os.shorter"),
    ("stop learning", "os", "os.learning_off"),
    ("what have you learned", "os", "os.model"),
    ("open gauge", "gauge", "gauge.read"),
    ("switch to checklist", "checklist", "checklist.idle"),
]


@pytest.mark.parametrize("text,app,intent", CASES)
def test_rules(text, app, intent):
    a, i, how, _ = route(text)
    assert (a, i) == (app, intent), (text, a, i, how)
    assert how.startswith("rule") or how == "default"


def test_nav_only_when_active():
    assert route("next")[:2] != ("guide", "guide.next")
    assert route("next", active={"guide": True})[:2] == ("guide", "guide.next")
    assert route("back", active={"guide": True})[:2] == ("guide", "guide.prev")
    assert route("check", active={"checklist": True})[:2] == ("checklist", "checklist.tick")
    assert route("done", active={"checklist": True})[:2] == ("checklist", "checklist.tick")
    assert route("done, log it", active={"guide": True, "checklist": True})[:2] == ("notes", "notes.done")


def test_pending_confirm():
    assert route("yes", active={"gauge_pending": True})[:2] == ("gauge", "gauge.confirm")
    assert route("no", active={"gauge_pending": True})[:2] == ("gauge", "gauge.reject")
    assert route("yes")[0] == "ask"


def test_photo_routes():
    assert route("what does the gauge read", has_photo=True)[:2] == ("gauge", "gauge.photo")
    assert route("", has_photo=True)[:2] == ("ask", "ask.photo")
    assert route("", has_photo=False)[:2] == ("os", "os.home")


def test_explicit_app_wins():
    a, i, how, _ = route("", explicit_app="gauge")
    assert (a, i, how) == ("gauge", "gauge.read", "explicit")
    a, i, how, _ = route("how do I replace the seal", explicit_app="guide")
    assert (a, i, how) == ("guide", "guide.start", "explicit")
    assert route("what is this", explicit_app="ask")[:2] == ("ask", "ask.question")
    with pytest.raises(KeyError):
        route("x", explicit_app="nope")


def test_keyword_fallback_is_conservative():
    assert keyword_route("show me the inspection") == "checklist"
    assert keyword_route("what is the normal pressure") is None   # questions stay with Ask
    assert route("the meter thing please")[0] == "gauge"
    assert route("the meter thing please")[2] == "keywords"


def test_llm_fallback_only_with_real_model():
    p = FakeRealProvider(route_to="checklist")
    a, i, how, _ = route("can we do the monthly stuff on this one", provider=p)
    assert (a, how) == ("checklist", "llm") and p.posts == 1
    from llm import MockProvider
    assert route("can we do the monthly stuff on this one", provider=MockProvider())[2] == "default"


def test_llm_fallback_failure_is_safe():
    assert llm_route(FakeRealProvider(fail=True), "anything") is None
    assert llm_route(FakeRealProvider(route_to="I think pizza"), "anything") is None
    a, _, how, _ = route("can we do the monthly stuff", provider=FakeRealProvider(fail=True))
    assert (a, how) == ("ask", "default")


def test_rules_run_before_llm():
    p = FakeRealProvider(route_to="handoff")
    assert route("reads 48 psi", provider=p)[0] == "gauge" and p.posts == 0
