"""Learns how one worker works, from that worker's own private event log. Pure functions, no I/O.

Three small, explainable models (counts, not a neural net), all rebuilt from the event list on demand:
  sequence   P(next action | previous action in this shift)      "after Gauge P-12 you open Guide step 4 (7/9)"
  time       share of past shifts where the action happened in this part of the shift   "shift start (8 of 9 shifts)"
  place      P(action | the asset you're at now)                  "at P-12 you usually open Guide (5/7)"
score = 0.55*sequence + 0.30*time + 0.15*place (+ tiny popularity tie-break); an action already done as many times
as it usually is per shift is damped x0.2 (you check P-12 once a shift, so after you did it, stop suggesting it).
The WHY shown on the card is the part that contributed most, with the raw counts behind it.

Verbosity: explicit "shorter"/"more detail" (+-1) and starting a Guide right after an Ask on the same asset (+0.5,
you wanted steps) -> "short" | "normal" | "steps", over the last 20 signals.

An action key is "app|asset|detail", e.g. "gauge|P-12|pressure", "guide|P-12|step 4", "handoff||".
Events are dicts: {action, app, asset, detail, intent, phase, shift_id, shift_minute, ctx_asset, ts, text, signal}.
"""
from collections import Counter, defaultdict

START = "<start>"
APP_NAMES = {"ask": "Ask", "guide": "Guide", "gauge": "Gauge", "notes": "Notes", "handoff": "Handoff",
             "checklist": "Checklist"}
NAV_INTENTS = {"guide.next", "guide.prev"}   # swipes inside a guide: not a decision worth predicting
WEIGHTS = {"seq": 0.55, "phase": 0.30, "asset": 0.15}
MIN_SUPPORT = 2          # a pattern needs at least this many observations to count
DECAY_PER_DAY = 0.97     # old habits fade slowly
DAMP_DONE = 0.2
PHASE_WORDS = {"start": "shift start", "first_half": "first half of shift", "second_half": "second half of shift",
               "end": "end of shift", "unknown": "this time"}


def phase_of(minute, shift_len=480):
    if minute is None: return "unknown"
    if minute < 60: return "start"
    if minute >= shift_len - 60: return "end"
    return "first_half" if minute < shift_len / 2 else "second_half"


def action_key(app, asset="", detail=""):
    return f"{app}|{asset or ''}|{detail or ''}"


def split(action):
    app, asset, detail = (action.split("|") + ["", ""])[:3]
    return app, asset, detail


def label(action):
    app, asset, detail = split(action)
    return " ".join(x for x in (APP_NAMES.get(app, app.title()), asset, detail) if x)


def learnable(e):
    return e.get("app") in APP_NAMES and e.get("intent") not in NAV_INTENTS and bool(e.get("action"))


class Model:
    def __init__(self, events, weights=None, now=None):
        self.w = dict(WEIGHTS, **(weights or {}))
        evs = sorted((e for e in events if learnable(e)), key=lambda e: e.get("ts", 0))
        self.n = len(evs)
        now = now if now is not None else (evs[-1].get("ts", 0) if evs else 0)
        self.seq, self.seq_raw = defaultdict(Counter), defaultdict(Counter)
        self.seq_n, self.seq_n_raw = Counter(), Counter()
        self.asset, self.asset_raw = defaultdict(Counter), defaultdict(Counter)
        self.asset_n, self.asset_n_raw = Counter(), Counter()
        phase_sets = defaultdict(lambda: defaultdict(set))      # phase -> action -> {shift}
        phase_shifts = defaultdict(set)                         # phase -> {shift with any event in it}
        per_shift = defaultdict(Counter)                        # shift -> Counter(action)
        self.pop = Counter()
        self.text = {}
        prev = {}
        for e in evs:
            a, s = e["action"], e.get("shift_id") or "?"
            wt = DECAY_PER_DAY ** max(0.0, (now - e.get("ts", now)) / 86400)
            p = prev.get(s, START)
            self.seq[p][a] += wt; self.seq_n[p] += wt
            self.seq_raw[p][a] += 1; self.seq_n_raw[p] += 1
            ph = e.get("phase") or "unknown"
            phase_sets[ph][a].add(s); phase_shifts[ph].add(s)
            ca = e.get("ctx_asset") or ""
            if ca:
                self.asset[ca][a] += wt; self.asset_n[ca] += wt
                self.asset_raw[ca][a] += 1; self.asset_n_raw[ca] += 1
            per_shift[s][a] += 1
            self.pop[a] += wt
            if e.get("text"): self.text[a] = e["text"]
            prev[s] = a
        self.phase = {ph: {a: len(ss) for a, ss in acts.items()} for ph, acts in phase_sets.items()}
        self.phase_n = {ph: len(ss) for ph, ss in phase_shifts.items()}
        self.typical = {}
        for a in self.pop:
            counts = [c[a] for c in per_shift.values() if c[a]]
            self.typical[a] = max(1, round(sum(counts) / len(counts)))
        self.actions = list(self.pop)
        self.pop_total = sum(self.pop.values()) or 1

    # ---------------- prediction
    def parts(self, a, prev, phase, ctx_asset):
        out = {"seq": 0.0, "phase": 0.0, "asset": 0.0}
        # each part needs the pattern itself seen MIN_SUPPORT times: a one-off is not a habit
        if self.seq_raw[prev][a] >= MIN_SUPPORT:
            out["seq"] = self.seq[prev][a] / self.seq_n[prev]
        if self.phase.get(phase, {}).get(a, 0) >= MIN_SUPPORT:
            out["phase"] = self.phase[phase][a] / self.phase_n[phase]
        if ctx_asset and self.asset_raw[ctx_asset][a] >= MIN_SUPPORT:
            out["asset"] = self.asset[ctx_asset][a] / self.asset_n[ctx_asset]
        return out

    def predict(self, prev=START, phase="unknown", ctx_asset="", done=None, k=3):
        """-> [{action, label, score, parts, why, why_full}] best first. done: Counter of actions this shift."""
        done = done or Counter()
        prev = prev or START
        res = []
        for a in self.actions:
            pt = self.parts(a, prev, phase, ctx_asset)
            score = sum(self.w[c] * v for c, v in pt.items()) + 0.02 * self.pop[a] / self.pop_total
            damped = done[a] >= self.typical.get(a, 1)
            if damped: score *= DAMP_DONE
            res.append({"action": a, "label": label(a), "score": round(score, 4), "parts": pt, "damped": damped})
        res.sort(key=lambda r: (-r["score"], r["action"]))
        for r in res[:k]:
            r["why"], r["why_full"], r["support"] = self.explain(r["action"], r["parts"], prev, phase, ctx_asset)
        return res[:k]

    def explain(self, a, pt, prev, phase, ctx_asset):
        """Short WHY (<=40 chars, for the HUD), a full sentence, and the raw count behind it."""
        contrib = {c: self.w[c] * v for c, v in pt.items()}
        best = max(contrib, key=contrib.get) if any(contrib.values()) else None
        app, asset, _ = split(a)
        name = APP_NAMES.get(app, app)
        if best == "seq":
            c, n = int(self.seq_raw[prev][a]), int(self.seq_n_raw[prev])
            if prev == START:
                return (f"You open this first ({c}/{n} shifts)", f"You started {c} of {n} shifts with {label(a)}.", c)
            return (f"After {label(prev)}: {c}/{n} times",
                    f"After {label(prev)} you went to {label(a)} {c} of {n} times.", c)
        if best == "phase":
            c, n = self.phase.get(phase, {}).get(a, 0), self.phase_n.get(phase, 0)
            return (f"{PHASE_WORDS[phase].capitalize()}: {c} of {n} shifts",
                    f"You did {label(a)} near {PHASE_WORDS[phase]} in {c} of {n} shifts.", c)
        if best == "asset":
            c, n = int(self.asset_raw[ctx_asset][a]), int(self.asset_n_raw[ctx_asset])
            return (f"At {ctx_asset} you open {name} {c}/{n}",
                    f"When you were at {ctx_asset}, you opened {label(a)} {c} of {n} times.", c)
        c = int(round(self.pop[a]))
        return ("You use this often", f"{label(a)} is one of the things you do most ({c} times).", c)

    # ---------------- verbosity
    @staticmethod
    def verbosity(events):
        sig = []
        evs = sorted(events, key=lambda e: e.get("ts", 0))
        last_ask = {}
        for e in evs:
            s = e.get("signal")
            if s == "shorter": sig.append((-1.0, "shorter"))
            elif s == "more": sig.append((1.0, "more"))
            if e.get("app") == "ask": last_ask[e.get("shift_id")] = e.get("asset")
            elif e.get("intent") == "guide.start" and e.get("shift_id") in last_ask \
                    and last_ask[e.get("shift_id")] == e.get("asset"):
                sig.append((0.5, "guide_after_ask")); last_ask.pop(e.get("shift_id"))
        sig = sig[-20:]
        total = sum(v for v, _ in sig)
        c = Counter(n for _, n in sig)
        if total <= -1.5:
            return "short", f"You asked for shorter answers {c['shorter']} times"
        if total >= 1.5:
            bits = [f"asked for more {c['more']}x" if c["more"] else "",
                    f"opened steps after an answer {c['guide_after_ask']}x" if c["guide_after_ask"] else ""]
            return "steps", "You " + " and ".join(b for b in bits if b)
        return "normal", "Not enough signals yet" if not sig else "Your signals are mixed"

    def summary(self, top=5):
        """What the model has learned, in plain words, for the worker's own 'what do you know' view."""
        pats = []
        for p, cnt in self.seq_raw.items():
            n = self.seq_n_raw[p]
            for a, c in cnt.items():
                if c >= MIN_SUPPORT and c / n >= 0.5:
                    pats.append((c, f"After {label(p)} you usually do {label(a)} ({int(c)}/{int(n)})" if p != START
                                 else f"You usually start shifts with {label(a)} ({int(c)}/{int(n)})"))
        for ph, acts in self.phase.items():
            n = self.phase_n[ph]
            for a, c in acts.items():
                if c >= MIN_SUPPORT and c / n >= 0.6 and ph != "unknown":
                    pats.append((c, f"{label(a)} near {PHASE_WORDS[ph]} ({c}/{n} shifts)"))
        pats.sort(key=lambda x: -x[0])
        return [t for _, t in pats[:top]]


def popularity_baseline(events, k=3):
    """Naive baseline: the k actions you have done most so far, regardless of context."""
    c = Counter(e["action"] for e in events if learnable(e))
    return [a for a, _ in sorted(c.items(), key=lambda x: (-x[1], x[0]))[:k]]
