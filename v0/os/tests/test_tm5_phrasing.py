"""G80: tm5_pump phrasing symptoms."""
import os
from worker_os import nav_engine as E
from worker_os import nav_tree as T
TREE = T.load_file(os.path.join(T.TREES, "tm5_pump.json"), require_reviewed=False)
PHR = {
 "no_water": ["no water coming out", "pump has low pressure", "weak flow"],
 "pump_fails": ["the pump will not start", "pump fails to run"],
 "leaks": ["pump leaks during operation", "water is leaking from the pump"],
}
def test_phrasings():
    for sid, ps in PHR.items():
        for p in ps:
            s = E.symptom_of(TREE, p)
            assert s and s["id"] == sid, (p, s and s["id"])
