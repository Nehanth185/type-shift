"""G79: unrelated input must not match a symptom."""
import os
from worker_os import nav_engine as E
from worker_os import nav_tree as T
TREE = T.load_file(os.path.join(T.TREES, "cp450.json"), require_reviewed=False)
NEG = ["what time is lunch", "order new gloves", "hello", "the weather is nice today", "how do I log my hours"]
def test_negatives():
    bad = [(p, E.symptom_of(TREE, p)["id"]) for p in NEG if E.symptom_of(TREE, p)]
    assert not bad, bad
