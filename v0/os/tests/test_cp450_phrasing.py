"""G78: extra phrasing tests for cp450 symptoms."""
import os
from worker_os import nav_engine as E
from worker_os import nav_tree as T
TREE = T.load_file(os.path.join(T.TREES, "cp450.json"), require_reviewed=False)
PHR = {
 "low_pressure": ["the pump pressure dropped again", "weak flow at the discharge", "we are losing pressure"],
 "leak": ["there is a puddle under the pump", "seal is dripping", "pump leaks at the flange"],
 "motor_trips": ["motor keeps tripping", "the motor is overheating", "breaker tripped on startup"],
 "noise": ["pump is making a grinding noise", "loud rattling from the casing", "pump is vibrating badly"],
}
def test_phrasings():
    for sid, ps in PHR.items():
        for p in ps:
            s = E.symptom_of(TREE, p)
            assert s and s["id"] == sid, (p, s and s["id"])
