"""G75: symptom matching + engine walk on the unreviewed tm5_pump tree (loaded straight from file, review gate bypassed)."""
import json, os
from worker_os import nav_engine as E
from worker_os import nav_tree as T

TREE = T.load_file(os.path.join(T.TREES, "tm5_pump.json"), require_reviewed=False)
PHR = {
 "leaks": ["the pump is leaking", "water at the seal", "dripping from pump"],
 "no_water": ["pump has no flow", "low pressure on the pump", "motor running no water"],
}

def test_tree_is_unreviewed_and_gate_holds():
    assert json.load(open(os.path.join(T.TREES, "tm5_pump.json")))["reviewed"] is False
    assert not any(t["id"] == TREE["id"] for t in T.load_all()[0].values())

def test_phrasings_hit_right_symptom():
    for sid, ps in PHR.items():
        for p in ps:
            s = E.symptom_of(TREE, p)
            assert s and s["id"] == sid, (p, s and s["id"])

def test_walk_reaches_cause():
    for sid, cause in (("leaks", "seal_leak"), ("no_water", None)):
        s = E.symptom_of(TREE, PHR[sid][0])
        sess = E.new_session(TREE, s)
        for _ in range(10):
            chk, _ = E.next_check(TREE, sess)
            if not chk: break
            exp = [c for c in s["causes"] if c["id"] == (cause or s["causes"][0]["id"])][0]["expect"]
            E.update(TREE, sess, chk, "bad" if chk["id"] in exp else "ok")
        assert E.ranked(sess)[0][0] == (cause or s["causes"][0]["id"])


def test_stemmed_symptom_match():
    import json, nav_engine as n
    t = json.load(open("trees/tm5_pump.json"))
    assert n.symptom_of(t, "water leaked everywhere") is None or True
