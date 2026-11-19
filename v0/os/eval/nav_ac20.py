"""Second-machine test (SYNTHETIC): AC-20 fictional compressor tree, run ONCE, no tuning. python eval/nav_ac20.py"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); OS = os.path.dirname(HERE)
sys.path += [os.path.join(os.path.dirname(OS), "server"), OS]
import boot
W = boot.load(); E, T = W.nav_engine, W.nav_tree
tree = T.for_model("AC-20")
assert tree, "AC-20 tree not loaded"
def ans(chk, cause):
    p = cause["expect"].get(chk["id"], "ok")
    if p == "out": p = "high"
    return ("bad" if p == "bad" else "ok") if chk["kind"] == "yesno" else p
def run(sym, cid, linear=False):
    sy = next(s for s in tree["symptoms"] if s["id"] == sym); ca = next(c for c in sy["causes"] if c["id"] == cid)
    s = E.new_session(tree, sy, "C-3", (), linear=linear); gm = 0; n = 0
    for _ in range(40):
        if E.found(tree, s): break
        c, _ = E.next_check(tree, s)
        if c is None: break
        g = E.gate_minutes(tree, s, c)
        if g: gm += g; s["state"] = "locked" if c["needs"] == "loto" else "running"
        E.update(tree, s, c, ans(c, ca)); n += 1
    f = E.found(tree, s)
    return n, round(s["minutes"] + gm, 1), bool(f and f["id"] == cid)
rows = []
for sy in tree["symptoms"]:
    for c in sy["causes"]:
        w = sy["phrases"][0]
        assert E.symptom_of(tree, w)["id"] == sy["id"]
        rows.append((c["id"], run(sy["id"], c["id"]), run(sy["id"], c["id"], True)))
L = ["# AC-20 second-machine test (SYNTHETIC, one run, untuned)", "",
     "Fictional compressor tree, same engine, no code changes. Answers follow the tree itself, so this tests logic and format, not field speed.", "",
     "| Planted fault | Nav checks/min | Linear checks/min | Nav right |", "|---|---|---|---|"]
for cid, a, b in rows: L.append(f"| {cid} | {a[0]} / {a[1]} | {b[0]} / {b[1]} | {'yes' if a[2] else 'NO'} |")
L += ["", f"**Totals: Nav {sum(r[1][0] for r in rows)} checks / {round(sum(r[1][1] for r in rows),1)} min, {sum(r[1][2] for r in rows)}/{len(rows)} right. "
      f"Linear {sum(r[2][0] for r in rows)} / {round(sum(r[2][1] for r in rows),1)}, {sum(r[2][2] for r in rows)}/{len(rows)}.**"]
open(os.path.join(HERE, "NAV_AC20.md"), "w").write("\n".join(L)); print("\n".join(L))
