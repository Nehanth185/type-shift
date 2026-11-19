import glob, json
from nav_tree import warnings

def test_ac20_rings_flagged():
    t = json.load(open(glob.glob("trees/ac20.json")[0]))
    w = warnings(t)
    print(w)
    assert any("rings" in x for x in w)

def test_clean_tree_no_warning():
    t = {"symptoms": [{"id": "s", "causes": [{"id": "a", "expect": {"x": "bad"}}, {"id": "b", "expect": {"y": "bad"}}]}]}
    assert warnings(t) == []

def test_cp450_warnings_listed():
    t = json.load(open("trees/cp450.json")); print(warnings(t))
