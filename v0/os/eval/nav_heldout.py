"""Held-out A/B: every cause in the CP-450 tree NOT used in nav_selftest (not used for tuning). SYNTHETIC.
Arm 'linear' = the manual's own table order = what a worker does with plain manual search. Run: python eval/nav_heldout.py"""
import os, sys, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv = ["x"]
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    spec = importlib.util.spec_from_file_location("st", os.path.join(HERE, "nav_selftest.py")); st = importlib.util.module_from_spec(spec); spec.loader.exec_module(st)
used = {f[1] for f in st.FAULTS} | {n[1] for n in st.NOISY}
rows = []
for sym in st.tree["symptoms"]:
    for c in sym["causes"]:
        if c["id"] in used: continue
        rows.append((sym["id"], c["id"], st.run(sym["id"], c["id"]), st.run(sym["id"], c["id"], linear=True)))
n = len(rows); L = ["# Navigator held-out A/B (SYNTHETIC)", "", f"{n} causes never used for tuning. Linear = manual table order (plain search).", "",
     "| Cause | Nav checks/min | Linear checks/min | Nav right |", "|---|---|---|---|"]
for s, c, a, b in rows:
    L.append(f"| {c} | {a['checks']} / {a['minutes']} | {b['checks']} / {b['minutes']} | {'yes' if a['correct'] else 'NO'} |")
tn = sum(r[2]['minutes'] for r in rows); tl = sum(r[3]['minutes'] for r in rows)
L += ["", f"**Totals: Nav {sum(r[2]['checks'] for r in rows)} checks / {tn:.1f} min, {sum(r[2]['correct'] for r in rows)}/{n} right. Linear {sum(r[3]['checks'] for r in rows)} / {tl:.1f} min, {sum(r[3]['correct'] for r in rows)}/{n}.**"]
open(os.path.join(HERE, "NAV_HELDOUT.md"), "w").write("\n".join(L)); print("\n".join(L))
