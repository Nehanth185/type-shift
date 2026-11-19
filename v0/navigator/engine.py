"""Fault Navigator: next-best-check engine. Code ranks; every card needs a doc page cite."""
import json, os, uuid
HERE = os.path.dirname(__file__)
def load(path=None):
    return json.load(open(path or os.path.join(HERE, "faults.json")))
class Navigator:
    def __init__(self, data=None, past_fixes=None):
        self.d = data or load(); self.sessions = {}; self.past = past_fixes or {}
    def _symptom(self, text):
        t = text.lower()
        for k, v in self.d["symptoms"].items():
            if any(m in t for m in v["match"]): return k
    def start(self, asset, symptom):
        if asset.lower().replace(" ", "").replace("-", "") != self.d["asset"].lower().replace("-", ""):
            return {"refused": f"No docs for asset {asset}"}
        k = self._symptom(symptom)
        if not k: return {"refused": "Docs don't cover that symptom"}
        s = self.d["symptoms"][k]
        w = {c: v["prior"] * (1 + 0.5 * self.past.get((k, c), 0)) for c, v in s["causes"].items()}
        sid = uuid.uuid4().hex[:8]
        self.sessions[sid] = {"sym": k, "w": w, "done": [], "log": []}
        return {"session": sid, "card": self._next(sid)}
    def _norm(self, w):
        t = sum(w.values()) or 1
        return {c: v / t for c, v in w.items()}
    def _next(self, sid):
        S = self.sessions[sid]; s = self.d["symptoms"][S["sym"]]
        left = [c for c in s["checks"] if c["id"] not in S["done"] and c.get("page")]
        if not left: return {"done": True, "ranked_causes": self._ranked(sid)}
        p = self._norm(S["w"])
        def score(c):
            gain = max([abs(1 - c["yes"].get(k, 1)) * p[k] if "yes" in c else 0 for k in p] + [0.01])
            return gain / c["min"]
        if not S["done"]: c = left[0]  # first check always the gauge confirm
        else: c = max(left, key=score)
        n = len(S["done"]) + 1
        return {"check_id": c["id"], "line1": f"CHECK {n}/{len(s['checks'])}: {c['text']}"[:40],
                "line2": f"p.{c['page']} | ~{c['min']} min", "kind": c["kind"], "page": c["page"]}
    def _ranked(self, sid):
        p = self._norm(self.sessions[sid]["w"]); s = self.d["symptoms"][self.sessions[sid]["sym"]]
        return [(c, round(v, 2), s["causes"][c]["fix"]) for c, v in sorted(p.items(), key=lambda x: -x[1])]
    def answer(self, sid, result):
        S = self.sessions[sid]; s = self.d["symptoms"][S["sym"]]
        cid = self.last(sid)["check_id"]; c = next(x for x in s["checks"] if x["id"] == cid)
        yes = result in ("yes", "y", True) or (isinstance(result, (int, float)) and c["id"] == "read_gauge" and result < 50)
        if result == "skip": pass
        else:
            br = c.get("yes" if yes else "no", {})
            if "done" in br: S["done"].append(cid); return {"done": True, "note": br["done"]}
            for k, m in br.items(): S["w"][k] *= m
        S["done"].append(cid); S["log"].append((cid, result))
        return self.last_set(sid, self._next(sid))
    def last_set(self, sid, card): self.sessions[sid]["card"] = card; return card
    def last(self, sid):
        S = self.sessions[sid]
        if "card" not in S: S["card"] = self._next(sid)
        return S["card"]
    def fix(self, sid, text):
        S = self.sessions[sid]; self.past[(S["sym"], text)] = self.past.get((S["sym"], text), 0) + 1
        return {"saved": True, "team_owned": True, "text": text}
