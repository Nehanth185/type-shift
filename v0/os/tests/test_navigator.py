"""Fault Navigator (G46): review gate, engine, safety gates, refusals, Fix Capture, privacy, routing, done-confirm."""
import json, os, shutil

import pytest
import workapi
from conftest import CTX, FakeRealProvider, say
from worker_os import nav_engine as E
from worker_os import nav_tree as T
from worker_os.router import route

TREE = T.for_model("CP-450")


def lines(j): return " | ".join(j["card"]["lines"])


def low_pressure_to_strainer(c, worker="w1"):
    """Walk the strainer fault: answers are what a clogged strainer looks like."""
    j = say(c, "pump P-12 low pressure", worker=worker)
    for _ in range(12):
        if j["card"]["title"].startswith("Likely cause"): return j
        chk = j["data"].get("check", {}).get("id")
        if j["data"].get("gate"): j = say(c, "locked out" if j["data"]["gate"] == "loto" else "running", worker=worker); continue
        ans = {"suction_pressure": "reads 2 psi", "strainer_dp": "8 psi", "suction_valve": "yes", "discharge_valve": "yes",
               "sight_glass": "no", "rotation": "yes", "impeller": "no"}[chk]
        j = say(c, ans, worker=worker)
    return j


# ------------------------------------------------------------------ tree format + review gate
def test_shipped_tree_is_reviewed_and_valid():
    t = json.load(open(os.path.join(T.TREES, "cp450.json")))
    assert t["reviewed"] is True and T.validate(t) == []
    assert TREE and len(TREE["symptoms"]) == 4 and all(c["page"] for c in TREE["checks"])


def test_unreviewed_tree_is_not_loaded(tmp_path):
    t = json.load(open(os.path.join(T.TREES, "cp450.json")))
    shutil.copytree(os.path.join(T.TREES, "docs"), tmp_path / "docs")
    t["reviewed"] = False
    t["docs"]["manual"]["file"] = os.path.join(T.TREES, t["docs"]["manual"]["file"])
    (tmp_path / "cp450.json").write_text(json.dumps(t))
    trees, skipped = T.load_all(str(tmp_path))
    assert trees == {} and "not reviewed" in skipped["cp450.json"]


def test_validator_catches_made_up_quote_and_spec():
    t = json.load(open(os.path.join(T.TREES, "cp450.json")))
    t["checks"][2]["spec"]["hi"] = 99          # 99 PSI is not on page 30
    t["symptoms"][0]["causes"][0]["quote"] = "Kick the pump twice"
    bad = T.validate(t)
    assert any("spec number 99" in b for b in bad) and any("Kick the pump" in b for b in bad)


def test_drafts_never_load():
    trees, _ = T.load_all()
    assert "cp-450" in trees and "tm5_pump" not in [t["id"] for t in trees.values()]
    d = json.load(open(os.path.join(T.TREES, "drafts", "sta-rite-sx218.draft.json")))
    assert d["reviewed"] is False and d["source"]["url"].startswith("https://www.pentair.com/")


def test_table_parser_on_x_matrix():
    txt = ("                          Electrical            Mechanical\n"
           " Symptom                  A     B     C       A     B\n"
           " Pump stops               X     X\n"
           " Abnormal noise                       X       X\n\n"
           " Electrical\n   A. No voltage.     Check breaker.\n   B. One phase out.\n   C. Low voltage.\n"
           " Mechanical\n   A. Obstruction.    Remove it.\n   B. Wrong rotation.\n")
    p = T.parse_x_matrix(txt)
    assert [s["causes"] for s in p["symptoms"]] == [["Electrical:A", "Electrical:B"], ["Electrical:C", "Mechanical:A"]]
    assert p["causes"]["Mechanical:A"]["cause"] == "Obstruction."


# ------------------------------------------------------------------ engine
@pytest.mark.parametrize("sym,cause", [(s["id"], c["id"]) for s in TREE["symptoms"] for c in s["causes"]])
def test_engine_finds_every_documented_cause(sym, cause):
    """Planted fault for every cause in the tree, answered as the tree predicts (synthetic)."""
    s = next(x for x in TREE["symptoms"] if x["id"] == sym)
    c = next(x for x in s["causes"] if x["id"] == cause)
    sess = E.new_session(TREE, s, "P-12")
    for _ in range(30):
        if E.found(TREE, sess): break
        chk, _ = E.next_check(TREE, sess)
        assert chk is not None, (cause, sess["answers"])
        o = c["expect"].get(chk["id"], "ok")
        E.update(TREE, sess, chk, "high" if o == "out" else o)
    assert E.found(TREE, sess)["id"] == cause


def test_judge_readings_and_units():
    chk = TREE["_checks"]["suction_pressure"]
    assert E.judge(chk, "reads 2 psi", "P-12")[:2] == ("low", 2.0)
    assert E.judge(chk, "P-12 is at 9", "P-12")[:2] == ("ok", 9.0)     # the 12 in P-12 is not a reading
    assert E.judge(chk, "20 psi")[0] == "high"
    assert E.judge(chk, "3 bar")[2] == "say it in PSI"                 # never converted silently
    assert E.judge(chk, "looks fine")[2] == "say the number"
    yn = TREE["_checks"]["sight_glass"]                                   # ok_if = no
    assert E.judge(yn, "yes")[0] == "bad" and E.judge(yn, "no")[0] == "ok"


# ------------------------------------------------------------------ routing
def test_routes():
    assert route("pump P-12 is leaking")[:2] == ("navigator", "nav.start")
    assert route("troubleshoot")[:2] == ("navigator", "nav.start")
    assert route("reads 2 psi")[0] == "gauge"                                   # no session: gauge as before
    assert route("reads 2 psi", active={"nav": True})[:2] == ("navigator", "nav.answer")
    assert route("yes", active={"nav": True})[:2] == ("navigator", "nav.answer")
    assert route("fixed it, cleaned the strainer", active={"nav": True})[:2] == ("navigator", "nav.fix")
    assert route("done, log it", active={"nav": True})[:2] == ("navigator", "nav.done")
    assert route("note: order a gasket", active={"nav": True})[0] == "notes"        # notes still work mid-diagnosis
    assert route("What is the normal discharge pressure?", active={"nav": True})[0] == "ask"


# ------------------------------------------------------------------ the app through /os/input
def test_full_flow_cites_page_every_check(client):
    j = say(client, "pump P-12 low pressure")
    assert j["app"] == "navigator" and j["card"]["title"] == "Check 1 · P-12"
    seen = []
    while not j["card"]["title"].startswith("Likely cause"):
        assert j["card"]["lines"][-1].startswith("Source: CP-450") and " p." in j["card"]["lines"][-1]
        seen.append(j["data"].get("check", {}).get("id"))
        chk = j["data"]["check"]["id"]
        j = say(client, {"suction_pressure": "reads 2 psi", "strainer_dp": "8 psi", "suction_valve": "yes",
                         "sight_glass": "no", "discharge_valve": "yes"}[chk])
    assert j["card"]["lines"][0] == "Suction strainer clogged" and j["card"]["lines"][1].startswith("Fix:")
    assert j["card"]["lines"][2] == "Source: CP-450 Troubleshooting p.30"
    assert "strainer_dp" in seen   # confirmed by the check that points only at the strainer


def test_bad_unit_and_retry(client):
    j = say(client, "pump P-12 low pressure")
    while j["data"]["check"]["kind"] != "reading":
        j = say(client, "yes")
    n = j["data"]["checks_done"]
    j = say(client, "3 bar")
    assert "Say it in PSI" in lines(j) and j["data"]["checks_done"] == n


def test_lockout_gate_comes_before_loto_check(client):
    j = say(client, "P-12 is noisy")
    for _ in range(10):
        if j["data"].get("gate") == "loto": break
        chk = j["data"]["check"]["id"]
        j = say(client, {"suction_pressure": "10 psi", "bearing_temp": "40 c"}[chk])
    assert j["data"]["gate"] == "loto" and j["card"]["lines"][0].startswith("⚠ Lock out")   # warning first, always
    assert j["card"]["lines"][-1] == "Source: CP-450 Pump Manual p.21"
    nxt = j["data"]["next_check"]
    j2 = say(client, "3 mm")                      # a reading before confirming lockout is not taken
    assert j2["data"].get("gate") == "loto" and j2["data"]["checks_done"] == j["data"]["checks_done"]
    j3 = say(client, "locked out")
    assert j3["data"]["check"]["id"] == nxt and TREE["_checks"][nxt]["needs"] == "loto"


def test_refuses_without_reviewed_tree_or_symptom(client):
    j = say(client, "troubleshoot this", ctx={**CTX, "asset": "F-3", "model": "Forklift 9"})
    assert j["card"]["lines"][0] == "Not in your docs" and j["data"]["not_in_docs"]
    j = say(client, "troubleshoot P-12")
    assert j["card"]["lines"][0] == "Which symptom?"
    j = say(client, "it's leaking")
    assert j["data"]["check"]["page"] == "31"
    j = say(client, "troubleshoot", worker="w9", ctx={"shift_minute": 5})
    assert j["card"]["lines"][0] == "Which machine?"


def test_llm_never_picks_a_check(client):
    p = FakeRealProvider(route_to="ask")
    old = workapi.S["provider"]
    workapi.S["provider"] = p
    try:
        j = low_pressure_to_strainer(client)
        assert j["card"]["title"].startswith("Likely cause")
        say(client, "fixed")
    finally:
        workapi.S["provider"] = old
    assert p.posts == 0


def test_not_that_rules_out_and_goes_on(client):
    low_pressure_to_strainer(client)
    j = say(client, "not that")
    assert not j["card"]["title"].startswith("Likely cause") or j["card"]["lines"][0] != "Suction strainer clogged"
    assert "suction_strainer" not in json.dumps(j["data"]["top_causes"][:1])


# ------------------------------------------------------------------ Fix Capture + privacy
def test_fix_capture_team_owned_notes_private(client):
    say(client, "note: P-12 strainer gasket looks cracked", worker="w1")   # personal note
    low_pressure_to_strainer(client, worker="w1")
    j = say(client, "fixed: cleaned the strainer basket", worker="w1")
    assert j["card"]["title"] == "Fix saved · P-12" and j["data"]["shared_with"] == "workspace"
    assert j["data"]["cause_id"] == "strainer_clogged"
    # the next tech on P-12 sees it first
    j2 = say(client, "P-12 low flow again", worker="w2")
    assert j2["card"]["lines"][0] == "Last time here: Suction strainer clogg" or \
        j2["card"]["lines"][0].startswith("Last time here: Suction strainer")
    assert j2["data"]["last_time"]["fix"] == "cleaned the strainer basket"
    assert j2["data"]["top_causes"][0]["cause"] == "strainer_clogged"
    # ... but never w1's personal note
    blob = json.dumps(j2) + json.dumps(client.get("/os/nav/fixes", params={"workspace_id": "demo"}).json())
    assert "gasket looks cracked" not in blob
    assert client.get("/memory", params={"worker_id": "w2"}).json()["items"] == [] or \
        all("gasket" not in i["text"] for i in client.get("/memory", params={"worker_id": "w2"}).json()["items"])
    # team list: everyone in the workspace sees it; only the author deletes it
    fx = client.get("/os/nav/fixes", params={"workspace_id": "demo", "asset": "P-12"}).json()["fixes"]
    assert len(fx) == 1 and fx[0]["cause"] == "Suction strainer clogged" and "author" not in fx[0]
    assert client.delete(f"/os/nav/fixes/{fx[0]['id']}", params={"worker_id": "w2"}).status_code == 403
    assert client.delete(f"/os/nav/fixes/{fx[0]['id']}", params={"worker_id": "w1"}).status_code == 200


def test_fix_on_other_asset_is_labelled(client):
    low_pressure_to_strainer(client, worker="w1")
    say(client, "fixed", worker="w1")
    j = say(client, "low pressure", worker="w2", ctx={**CTX, "asset": "P-14"})
    assert j["card"]["lines"][0].startswith("Other CP-450s:")


def test_trees_endpoint(client):
    j = client.get("/os/nav/trees").json()
    assert "cp450" in [t["id"] for t in j["loaded"]] and "tm5_pump" not in [t["id"] for t in j["loaded"]] and "sta-rite-sx218.draft.json" in j["drafts_not_loaded"]


# ------------------------------------------------------------------ "done, log it" must not close a job mid-way
def test_done_mid_diagnosis_asks_first(client):
    say(client, "pump P-12 is leaking")
    j = say(client, "done, log it")
    assert j["card"]["lines"][:2] == ["Cause not found yet", "Log as done anyway?"]
    j = say(client, "no")
    assert j["data"].get("check") and "Not logged" in lines(j)
    say(client, "done, log it")
    j = say(client, "yes")
    assert j["card"]["lines"][0] == "Logged, cause not found"
    assert client.get("/os/nav/fixes", params={"workspace_id": "demo"}).json()["fixes"] == []


def test_done_mid_guide_asks_first_in_os(client):
    say(client, "how do I replace the mechanical seal")
    say(client, "next")
    j = say(client, "done, log it")
    assert j["card"]["lines"][0] == "Only on step 2/6"
    mem = client.get("/memory", params={"worker_id": "w1"}).json()["items"]
    assert not any(i["kind"] == "job_done" for i in mem)
    j = say(client, "no")
    assert j["card"]["lines"][0] == "OK, not logged" and say(client, "next")["data"]["guide"]["step"] == 3
    say(client, "done, log it")
    j = say(client, "yes")
    assert j["card"]["lines"][0].startswith("Logged:")


def test_all_normal_answers_end_in_refusal_not_a_guess(client):
    """A fault the tree doesn't cover: every check is normal -> 'Not in your docs', never a made-up cause."""
    ok = {"suction_pressure": "10 psi", "strainer_dp": "1 psi", "suction_valve": "yes", "discharge_valve": "yes",
          "sight_glass": "no", "rotation": "yes", "impeller": "no"}
    j = say(client, "pump P-12 low pressure")
    for _ in range(15):
        if j["card"]["title"].startswith(("Not in your docs", "Not confirmed", "Likely")): break
        j = say(client, ("locked out" if j["data"]["gate"] == "loto" else "running") if j["data"].get("gate")
                else ok[j["data"]["check"]["id"]])
    assert j["card"]["title"].startswith("Not in your docs") and j["data"]["not_in_docs"]
    assert j["card"]["lines"][0] == "No documented cause fits"
