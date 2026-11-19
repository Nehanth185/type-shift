"""Fault Navigator engine. Plain code, no model: it picks the next check, judges answers against the spec, and
decides when a cause is found. The LLM never chooses a check (it is not even imported here).

Model (single fault, all counts):
  P(cause)            tree prior x team-fix boost (+1x per fix on this asset, +0.3x per fix on another
                      asset of the model, at most 3 fixes per cause), normalized
  P(outcome | cause)  0.9 for the abnormal result the tree says this cause gives (0.08 it looks normal anyway);
                      a check the cause doesn't touch reads abnormal 3% of the time (false alarm)
  next check          max  expected information gain / (minutes + gate minutes)   (cheap and telling first)
                      ties -> cheaper, then tree order. A check that can't change anything is never asked.
  found               top cause >= 0.9 AND an abnormal result that points only at it (see found())
  out of docs         no informative check left and no cause found -> say so, never guess
A session is a plain dict (JSON), so it lives in os_state and survives restarts.
"""
import math, re

HIT, MISS, FALSE_ALARM, FOUND_P = 0.9, 0.08, 0.03, 0.9
TEAM_BOOST_ASSET, TEAM_BOOST_MODEL, BOOST_CAP = 1.0, 0.3, 3   # per team fix, at most 3 fixes counted per cause


def _outcomes(check):
    return ("ok", "bad") if check["kind"] == "yesno" else ("ok", "low", "high")


def lik(check, cause, outcome):
    """P(outcome of `check` | `cause` is the fault). A cause shows its predicted abnormal result 90% of the time
    (MISS = it looks normal anyway); a check this cause doesn't touch looks abnormal only FALSE_ALARM of the time.
    So an abnormal finding is strong evidence and a normal one is weaker (run 2 of the self-test; run 1 used a
    symmetric 0.9/0.1, see eval/NAV_SELFTEST.md)."""
    pred = cause["expect"].get(check["id"], "ok")
    outs = _outcomes(check)
    bad = [o for o in outs if o != "ok"]
    if pred == "ok":
        return 1 - FALSE_ALARM if outcome == "ok" else FALSE_ALARM / len(bad)
    want = bad if pred == "out" else [pred]
    if outcome in want: return HIT / len(want)
    if outcome == "ok": return MISS
    return max(1e-6, 1 - HIT - MISS) / max(1, len(bad) - len(want))


def _entropy(ps):
    return -sum(p * math.log2(p) for p in ps if p > 0)


def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _stems(text):
    return [_stem(w) for w in re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()]


def symptom_of(tree, text):
    """Which documented symptom the worker described. Exact phrase wins (longest first); else stemmed phrase
    match (leaks/leaking/leaked); else None. Never guesses beyond that."""
    t = " " + re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()) + " "
    best = None
    for s in tree["symptoms"]:
        for ph in s["phrases"]:
            if f" {ph} " in t and (best is None or len(ph) > best[1]):
                best = (s, len(ph))
    if best: return best[0]
    ts = " " + " ".join(_stems(text)) + " "
    for s in tree["symptoms"]:
        for ph in s["phrases"]:
            p = " ".join(_stems(ph))
            if p and f" {p} " in ts and (best is None or len(p) > best[1]):
                best = (s, len(p))
    return best[0] if best else None


def priors(symptom, fixes=(), asset=""):
    """fixes: team fix records [{cause_id, asset}] for this model+symptom."""
    p = {}
    for c in symptom["causes"]:
        n_here = min(BOOST_CAP, sum(1 for f in fixes if f.get("cause_id") == c["id"] and f.get("asset") == asset and asset))
        n_model = min(BOOST_CAP, sum(1 for f in fixes if f.get("cause_id") == c["id"] and not (f.get("asset") == asset and asset)))
        p[c["id"]] = float(c["prior"]) * (1 + TEAM_BOOST_ASSET * n_here + TEAM_BOOST_MODEL * n_model)
    z = sum(p.values())
    return {k: v / z for k, v in p.items()}


def new_session(tree, symptom, asset="", fixes=(), linear=False):
    return {"tree": tree["id"], "symptom": symptom["id"], "asset": asset, "post": priors(symptom, fixes, asset),
            "answers": [], "state": "running", "gate": None, "pending": None, "status": "active", "linear": linear,
            "minutes": 0.0, "found": None}


def _symptom(tree, sess):
    return next(s for s in tree["symptoms"] if s["id"] == sess["symptom"])


def candidate_checks(tree, sess):
    sym = _symptom(tree, sess)
    used = {a["check"] for a in sess["answers"]}
    ids = []
    for c in sym["causes"]:              # tree order: cause order, then each cause's checks
        for k in c["expect"]:
            if k not in ids and k not in used: ids.append(k)
    return [tree["_checks"][k] for k in ids]


def gate_minutes(tree, sess, check):
    need = check.get("needs", "any")
    if need == "loto" and sess["state"] != "locked": return tree["gates"]["loto"]["minutes"]
    if need == "run" and sess["state"] == "locked": return tree["gates"]["run"]["minutes"]
    return 0.0


def gain(tree, sess, check):
    sym = _symptom(tree, sess)
    causes = {c["id"]: c for c in sym["causes"]}
    post = sess["post"]
    h0 = _entropy(post.values())
    exp_h = 0.0
    for o in _outcomes(check):
        po = sum(post[cid] * lik(check, causes[cid], o) for cid in post)
        if po <= 0: continue
        cond = [post[cid] * lik(check, causes[cid], o) / po for cid in post]
        exp_h += po * _entropy(cond)
    return h0 - exp_h


def next_check(tree, sess):
    """-> (check, score) or (None, 0). Linear mode (the naive baseline) takes the tree order instead."""
    best = None
    for i, c in enumerate(candidate_checks(tree, sess)):
        g = gain(tree, sess, c)
        if sess.get("linear"):   # naive baseline: the table's own order, every listed check, no ranking
            return c, g
        if g < 0.01: continue
        cost = c["minutes"] + gate_minutes(tree, sess, c)
        key = (round(g / cost, 6), -cost, -i)
        if best is None or key > best[0]: best = (key, c, g / cost)
    return (best[1], best[2]) if best else (None, 0.0)


def update(tree, sess, check, outcome, value=None):
    sym = _symptom(tree, sess)
    causes = {c["id"]: c for c in sym["causes"]}
    post = {cid: p * lik(check, causes[cid], outcome) for cid, p in sess["post"].items()}
    z = sum(post.values()) or 1.0
    sess["post"] = {k: v / z for k, v in post.items()}
    sess["answers"].append({"check": check["id"], "outcome": outcome, "value": value})
    sess["minutes"] += check["minutes"]
    return sess


def skip(sess, check):
    sess["answers"].append({"check": check["id"], "outcome": "skipped", "value": None})
    return sess


def ranked(sess):
    return sorted(sess["post"].items(), key=lambda kv: -kv[1])


def exclusive_checks(sym, cause):
    """Checks where only this cause (in this symptom) predicts that abnormal result."""
    out = []
    for k, o in cause["expect"].items():
        if not any(c is not cause and c["expect"].get(k) == o for c in sym["causes"]): out.append(k)
    return out


def found(tree, sess):
    """The cause, if the evidence is in: top >= FOUND_P AND an abnormal result on a check that points only at it.
    (A cause with no such check, e.g. 'seal worn' whose drips also fit two other causes, needs one abnormal check
    of its own plus the others ruled out by the posterior.) Run 2 of the self-test added the 'points only at it'
    part: with one wrong answer, run 1's rule named the strainer from a shared low suction reading alone."""
    sym = _symptom(tree, sess)
    cid, p = ranked(sess)[0]
    if p < FOUND_P: return None
    c = next(x for x in sym["causes"] if x["id"] == cid)
    abn = {a["check"] for a in sess["answers"] if a["check"] in c["expect"] and a["outcome"] not in ("ok", "skipped")
           and (c["expect"][a["check"]] == "out" or a["outcome"] == c["expect"][a["check"]])}
    excl = exclusive_checks(sym, c)
    return c if (abn & set(excl)) or (not excl and abn) else None


def best_unconfirmed(tree, sess, floor=0.5):
    """When no check is left: the top cause if it is >= floor and one of its own checks was abnormal (shown as
    'not confirmed', never as the answer)."""
    sym = _symptom(tree, sess)
    cid, p = ranked(sess)[0]
    c = next(x for x in sym["causes"] if x["id"] == cid)
    hits = [a for a in sess["answers"] if a["check"] in c["expect"] and a["outcome"] not in ("ok", "skipped")]
    return (c, p) if p >= floor and hits else (None, p)


# ------------------------------------------------------------------ answers -> outcomes (rules only)
UNIT_WORDS = {"psi": "PSI", "bar": "BAR", "kpa": "KPA", "c": "C", "celsius": "C", "degrees": "C", "deg": "C",
              "f": "F", "fahrenheit": "F", "a": "A", "amp": "A", "amps": "A", "ampere": "A", "amperes": "A", "v": "V",
              "volt": "V", "volts": "V", "nm": "NM", "newton": "NM", "mm": "MM", "millimeter": "MM", "millimeters": "MM",
              "drip": "DRIPS/MIN", "drips": "DRIPS/MIN"}
NUM = re.compile(r"(-?\d+(?:\.\d+)?)\s*(°\s?[cf]\b|[a-z]+)?", re.I)
YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|correct|it is|it does|affirmative|true|i do|there are|there is)\b", re.I)
NO = re.compile(r"^\s*(?:no|nope|nah|negative|it isn'?t|it is not|it doesn'?t|none|false|there aren'?t|there isn'?t)\b",
                re.I)


def reading(text, asset=""):
    """'reads 2.5 psi' -> (2.5, 'PSI'); unit '' when not said. Asset tags and codes are removed first."""
    t = re.sub(re.escape(asset), " ", text, flags=re.I) if asset else text
    t = re.sub(r"\b[A-Za-z]{1,4}-\d+[A-Za-z]?\b", " ", t)
    m = NUM.search(t)
    if not m: return None, ""
    u = (m.group(2) or "").lower().replace("°", "").replace(" ", "")
    return float(m.group(1)), UNIT_WORDS.get(u, "")


def judge(check, text, asset=""):
    """-> (outcome, value, problem). problem is a short reason when the answer can't be used (then outcome None)."""
    if check["kind"] == "yesno":
        if YES.match(text): said = "yes"
        elif NO.match(text): said = "no"
        else: return None, None, "say yes or no"
        return ("ok" if said == check["ok_if"] else "bad"), said, None
    v, unit = reading(text, asset)
    if v is None: return None, None, "say the number"
    spec = check["spec"]
    if unit and unit != spec["unit"].upper():
        return None, v, f"say it in {spec['unit']}"   # never convert silently
    if v < spec["lo"]: return "low", v, None
    if v > spec["hi"]: return "high", v, None
    return "ok", v, None
