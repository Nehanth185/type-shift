"""Planted-fault self-test for the Fault Navigator (SYNTHETIC: faults and answers are made up, the tree is the
fictional CP-450 one). For each planted fault the 'worker' answers every check the way the tree says that cause looks.

Arms: Navigator (info gain per minute, code only) vs naive linear (the table's own order, every listed check).
Both use the same stop rule (top cause >= 0.9 with one of its own checks abnormal) and the same safety gates.
Also: 2 faults with one wrong answer (robustness), and a rerun after 3 team fixes were logged on P-12.
  python eval/nav_selftest.py    -> eval/NAV_SELFTEST.md + eval/nav_selftest.json
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OS = os.path.dirname(HERE)
sys.path.append(os.path.join(os.path.dirname(OS), "server"))
sys.path.append(OS)
import boot  # noqa: E402
W = boot.load()
E, T = W.nav_engine, W.nav_tree
tree = T.for_model("CP-450")

FAULTS = [  # (symptom, true cause, worker's words)
    ("low_pressure", "strainer_clogged", "P-12 low pressure"),
    ("low_pressure", "suction_valve_closed", "low flow on P-12"),
    ("low_pressure", "worn_impeller", "pressure dropped"),
    ("low_pressure", "wrong_rotation", "not pumping after motor work"),
    ("leak", "seal_worn", "it's leaking"),
    ("leak", "casing_bolts_loose", "leaking at the casing"),
    ("leak", "drain_plug_loose", "dripping under the pump"),
    ("motor_trips", "low_voltage", "motor keeps tripping"),
    ("noise", "coupling_misaligned", "noisy and vibrating"),
    ("noise", "cavitation", "grinding noise"),
]
NOISY = [("leak", "seal_installed_wrong", "seal_drips"), ("low_pressure", "air_leak_suction", "sight_glass")]


def answer_for(chk, cause, flip=None):
    pred = cause["expect"].get(chk["id"], "ok")
    if pred == "out": pred = "high"
    if flip == chk["id"]:   # one wrong answer: report the opposite of the truth
        pred = "ok" if pred != "ok" else ("bad" if chk["kind"] == "yesno" else "high")
    if chk["kind"] == "yesno":
        return "bad" if pred == "bad" else "ok"
    return pred


def run(sym_id, cause_id, linear=False, fixes=(), flip=None, asset="P-12"):
    sym = next(s for s in tree["symptoms"] if s["id"] == sym_id)
    cause = next(c for c in sym["causes"] if c["id"] == cause_id)
    s = E.new_session(tree, sym, asset, fixes, linear=linear)
    gate_min, path = 0.0, []
    for _ in range(40):
        if E.found(tree, s): break
        chk, _ = E.next_check(tree, s)
        if chk is None: break
        g = E.gate_minutes(tree, s, chk)
        if g:
            gate_min += g
            s["state"] = "locked" if chk["needs"] == "loto" else "running"
        E.update(tree, s, chk, answer_for(chk, cause, flip))
        path.append(chk["id"])
    f = E.found(tree, s)
    return {"checks": len(path), "minutes": round(s["minutes"] + gate_min, 1), "found": f["id"] if f else None,
            "correct": bool(f and f["id"] == cause_id), "path": path}


rows = []
for sym, cid, words in FAULTS:
    assert E.symptom_of(tree, words)["id"] == sym, words
    rows.append({"fault": cid, "symptom": sym, "said": words, "nav": run(sym, cid), "linear": run(sym, cid, linear=True)})
noisy = [{"fault": cid, "wrong_answer_on": flip, "nav": run(sym, cid, flip=flip), "linear": run(sym, cid, linear=True, flip=flip)}
         for sym, cid, flip in NOISY]
fx = [{"cause_id": "strainer_clogged", "asset": "P-12"}] * 3
after = {"repeat_fault": run("low_pressure", "strainer_clogged", fixes=fx),
         "other_fault": run("low_pressure", "suction_valve_closed", fixes=fx),
         "repeat_before": rows[0]["nav"], "other_before": rows[1]["nav"]}


def tot(arm, key, rs=rows): return sum(r[arm][key] for r in rs)


summ = {"faults": len(rows), "nav_correct": tot("nav", "correct"), "lin_correct": tot("linear", "correct"),
        "nav_checks": tot("nav", "checks"), "lin_checks": tot("linear", "checks"),
        "nav_minutes": round(tot("nav", "minutes"), 1), "lin_minutes": round(tot("linear", "minutes"), 1)}
json.dump({"summary": summ, "rows": rows, "noisy": noisy, "after_fixes": after}, open(os.path.join(HERE, "nav_selftest.json"), "w"), indent=1)

L = ["# Navigator planted-fault self-test (SYNTHETIC)", "",
     "Made-up faults on the fictional CP-450 tree; the 'worker' answers exactly as the tree predicts (except the 2 noisy rows). "
     "This tests the engine's logic, not real-world speed. Seeded faults flatter us. Run: `python eval/nav_selftest.py`.", "",
     f"**Navigator: {summ['nav_checks']} checks / {summ['nav_minutes']} min for 10 faults, {summ['nav_correct']}/10 right. "
     f"Naive linear: {summ['lin_checks']} checks / {summ['lin_minutes']} min, {summ['lin_correct']}/10 right.** "
     "(Minutes = the tree's per-check estimate + safety-gate time; both are reviewer guesses.)", "",
     "| Planted fault | Said | Navigator checks / min | Linear checks / min | Navigator right? |", "|---|---|---|---|---|"]
for r in rows:
    L.append(f"| {r['fault']} | \"{r['said']}\" | {r['nav']['checks']} / {r['nav']['minutes']} | {r['linear']['checks']} / "
             f"{r['linear']['minutes']} | {'yes' if r['nav']['correct'] else 'NO → ' + str(r['nav']['found'])} |")
L += ["", "## One wrong answer (robustness)", "", "| Fault | Wrong answer on | Navigator | Linear |", "|---|---|---|---|"]
for r in noisy:
    def cell(a): return f"{a['checks']} checks, {'right' if a['correct'] else 'found ' + str(a['found'])}"
    L.append(f"| {r['fault']} | {r['wrong_answer_on']} | {cell(r['nav'])} | {cell(r['linear'])} |")
L += ["", "## After 3 team fixes on P-12 (all 'strainer clogged')", "",
      f"- Same fault again: {after['repeat_before']['checks']} → {after['repeat_fault']['checks']} checks "
      f"({after['repeat_before']['minutes']} → {after['repeat_fault']['minutes']} min).",
      f"- A different fault (suction valve): {after['other_before']['checks']} → {after['other_fault']['checks']} checks "
      f"({after['other_before']['minutes']} → {after['other_fault']['minutes']} min), "
      f"{'still right' if after['other_fault']['correct'] else 'WRONG'}. The boost costs a little when history misleads.", "",
      "## Tuning disclosure", "",
      "Run 1 (first engine, symmetric 0.9/0.1 answer model, 'found' = any abnormal check of the top cause, fix boost "
      "2x per fix uncapped): Navigator 43 checks / 152 min vs linear 43 / 166, 10/10 right; after 3 strainer fixes the "
      "suction-valve fault came out WRONG (not found); one noisy case named the strainer from a shared reading.",
      "Changed after run 1 (each is in nav_engine.py): abnormal findings weigh more than normal ones (false alarm 3%, "
      "miss 8%), 'found' needs an abnormal result that points only at that cause, fix boost +1x per fix capped at 3. "
      "Same 10 faults were used to judge the change, so these numbers are optimistic. Real faults will fall off the tree.", ""]
open(os.path.join(HERE, "NAV_SELFTEST.md"), "w").write("\n".join(L))
print("\n".join(L))
