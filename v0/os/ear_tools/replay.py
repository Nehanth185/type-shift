"""Machine Ear replay harness (D5): play a recording through the walk-by pipeline without glasses.
Cuts a WAV into 3-second passes, runs each pass through the quality gate (ear_gate.py, D4), sends the good passes to a
running server (POST /os/ear/pass) and prints what happened. A rejected pass is never sent, so it can never change the
machine's healthy baseline. Standard library + numpy only.

  cd v0/os
  python ear_tools/replay.py healthy.wav --asset desk-fan --expect healthy
  python ear_tools/replay.py faulty.wav  --asset desk-fan --expect faulty

--expect healthy  PASS = the healthy baseline is complete (10 passes learned) and the machine never reached ALERT
--expect faulty   PASS = the machine reached ALERT (3 odd passes in the last 4)
Exit code: 0 = PASS (or no --expect), 1 = FAIL, 2 = could not run (bad file, server down).

The level check needs this machine's healthy level. The harness keeps the levels of the passes the server learned from
in a small JSON file (--levels, default ./ear_levels.json), one entry per server + workspace + asset.
UNTESTED on a real microphone and a real machine: see DESK_FAN_TEST.md.
"""
import argparse, base64, json, os, sys, urllib.error, urllib.parse, urllib.request
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__)); OS = os.path.dirname(HERE)
try:                                    # inside the server and the tests this folder is the package `worker_os`
    from worker_os import ear_engine as E, ear_gate as G
except ImportError:                     # run as a script
    sys.path.insert(0, OS)
    import ear_engine as E, ear_gate as G

PASS_SECONDS = 3.0
SENT, REJECTED, ERROR = "SENT", "REJECTED", "ERROR"


class ServerDown(Exception):
    pass


def cut(x, seconds=PASS_SECONDS, sr=E.SR):
    """Back-to-back passes of exactly `seconds`. Returns (passes, seconds left over at the end and not used)."""
    n = int(round(seconds * sr))
    k = len(x) // n
    return [x[i * n:(i + 1) * n] for i in range(k)], (len(x) - k * n) / sr


def _call(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try: return e.code, json.loads(raw)
        except ValueError: return e.code, {"detail": raw[:200]}
    except (urllib.error.URLError, OSError) as e:
        raise ServerDown(f"cannot reach {req.full_url}: {getattr(e, 'reason', e)}") from None


def _headers(token):
    h = {"Content-Type": "application/json"}
    if token: h.update({"Authorization": f"Bearer {token}", "X-Device-Token": token})
    return h


def http_sender(server, token="", timeout=30.0):
    """send(body) -> (status, json). Raises ServerDown when the server cannot be reached."""
    url = server.rstrip("/") + "/os/ear/pass"
    return lambda body: _call(urllib.request.Request(url, json.dumps(body).encode(), _headers(token)), timeout)


def http_machine(server, workspace, asset, token="", timeout=30.0):
    """What the server knows about this machine (GET /os/ear/status), or None when it has never heard it."""
    q = urllib.parse.urlencode({"workspace_id": workspace, "asset": asset})
    code, j = _call(urllib.request.Request(f"{server.rstrip('/')}/os/ear/status?{q}", headers=_headers(token)), timeout)
    if code != 200: raise ServerDown(f"server answered {code} to the status check: {str(j.get('detail', j))[:160]}")
    return (j.get("machines") or [None])[0]


def load_levels(path, key):
    try:
        with open(path) as f: return G.LevelRef(json.load(f).get(key) or [])
    except (OSError, ValueError, AttributeError, TypeError):
        return G.LevelRef()


def save_levels(path, key, ref):
    try:
        with open(path) as f: d = json.load(f)
        if not isinstance(d, dict): d = {}
    except (OSError, ValueError):
        d = {}
    d[key] = ref.levels
    with open(path, "w") as f: json.dump(d, f, indent=1)


def _t(s):
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def score_text(r):
    if r["status"] == REJECTED: return "no score (rejected)"
    if r["status"] == ERROR: return "no score (not accepted by the server)"
    if r["score"] is None: return f"no score (learning healthy sound, {r['baseline_passes']}/{E.MIN_BASELINE})"
    return f"score {r['score']:.3f}  healthy-percentile {r['pct']:.2f}  {'ODD' if r['odd'] else 'normal'}"


def replay(x, send, *, asset, model="", workspace="demo", worker="replay", ref=None, seconds=PASS_SECONDS, out=print):
    """Cut -> gate -> send. `send(body) -> (status, json)`. `ref` (ear_gate.LevelRef) is updated only by passes the
    gate accepted AND the server learned from. Returns the summary dict (see summarize())."""
    ref = ref if ref is not None else G.LevelRef()
    passes, left = cut(x, seconds)
    rows, state, alerted = [], None, False
    for i, p in enumerate(passes, 1):
        when = f"{_t((i - 1) * seconds)}-{_t(i * seconds)}"
        row = {"n": i, "at": when, "status": SENT, "reasons": [], "why": "", "level_db": None, "score": None,
               "pct": None, "odd": None, "state": state, "baseline_passes": None}
        v = G.check(p, ref.value)
        row["level_db"] = None if v.level_db == float("-inf") else round(v.level_db, 1)
        if not v.ok:
            row.update(status=REJECTED, reasons=list(v.reasons), why=v.why)
            out(f"pass {i:>3}  {when}  REJECTED  {v.why}")
            rows.append(row); continue
        body = {"worker_id": worker, "workspace_id": workspace, "asset": asset, "model": model,
                "audio_b64": base64.b64encode(E.to_wav(p)).decode()}
        code, j = send(body)
        if code != 200:
            row.update(status=ERROR, why=f"server answered {code}: {str(j.get('detail', j))[:160]}")
            out(f"pass {i:>3}  {when}  ERROR     {row['why']}")
            rows.append(row); continue
        info, state = j.get("pass") or {}, j.get("state")
        row.update(score=info.get("score"), pct=info.get("pct"), odd=info.get("odd"), state=state,
                   baseline_passes=(j.get("machine") or {}).get("baseline_passes"))
        alerted = alerted or state == "ALERT"
        if j.get("prev_state") == "LEARNING":               # the server learned from this pass: it defines the level
            if row["baseline_passes"] == 1: ref.levels.clear()      # a fresh baseline started
            ref.add(v.level_db)
        out(f"pass {i:>3}  {when}  SENT      {score_text(row)}  -> {state}")
        rows.append(row)
    return summarize(rows, left, state, alerted)


def summarize(rows, left=0.0, state=None, alerted=False):
    why = OrderedDict()
    for r in rows:
        for c in r["reasons"]: why.setdefault(c, []).append(r["n"])
    return {"passes": len(rows), "sent": sum(r["status"] == SENT for r in rows),
            "rejected": sum(r["status"] == REJECTED for r in rows), "errors": sum(r["status"] == ERROR for r in rows),
            "rejected_why": dict(why), "leftover_s": round(left, 2), "state": state, "alerted": alerted, "rows": rows}


def verdict(s, expect):
    """(passed, sentence) for --expect healthy | faulty."""
    if not s["sent"]: return False, "no pass was sent, nothing was tested"
    if expect == "healthy":
        if s["alerted"]: return False, "healthy recording raised an ALERT (false alarm)"
        if s["state"] == "LEARNING":
            n = max([r["baseline_passes"] or 0 for r in s["rows"]] or [0])
            return False, f"only {n} of {E.MIN_BASELINE} healthy passes learned, record more healthy sound"
        return True, "healthy baseline learned, no ALERT on the healthy recording"
    scored = [r for r in s["rows"] if r["score"] is not None]
    if not scored: return False, "the machine is still learning, no pass was scored (send 10 healthy passes first)"
    return (s["alerted"], "the fault raised an ALERT" if s["alerted"]
            else f"no ALERT on the faulty recording ({sum(bool(r['odd']) for r in scored)} of {len(scored)} passes odd)")


def print_summary(s, name, asset, expect=None, out=print):
    out("")
    out(f"SUMMARY  {name} -> machine '{asset}'")
    out(f"  passes cut:      {s['passes']}" + (f"  (last {s['leftover_s']:.1f} s too short for a pass, not used)"
                                               if s["leftover_s"] >= 0.05 else ""))
    out(f"  passes sent:     {s['sent']}")
    out(f"  passes rejected: {s['rejected']}")
    for c, ns in s["rejected_why"].items():
        out(f"    {c}: {len(ns)}  (pass {', '.join(map(str, ns))})")
    if s["errors"]: out(f"  not accepted by the server: {s['errors']}")
    out("  anomaly score per pass (distance from this machine's healthy sound; not a fault probability):")
    for r in s["rows"]:
        extra = f"  [{r['why']}]" if r["status"] != SENT else ""
        out(f"    pass {r['n']:>3}  {r['at']}  {score_text(r)}{extra}")
    out(f"  machine state now: {s['state'] or 'unchanged (nothing sent)'}")
    if expect:
        ok, line = verdict(s, expect)
        out(f"RESULT: {'PASS' if ok else 'FAIL'} ({line})")
        return ok
    return True


def main(argv=None, out=print):
    ap = argparse.ArgumentParser(description="Replay a recording through the Machine Ear quality gate and server.")
    ap.add_argument("wav", help="recording: PCM WAV (8/16/32-bit, any sample rate, first channel is used)")
    ap.add_argument("--asset", required=True, help="machine name on the server, e.g. desk-fan")
    ap.add_argument("--model", default="")
    ap.add_argument("--server", default=os.environ.get("EAR_SERVER", "http://127.0.0.1:8000"))
    ap.add_argument("--workspace", default="demo")
    ap.add_argument("--worker", default="replay")
    ap.add_argument("--token", default=os.environ.get("DEVICE_TOKEN", ""), help="device or worker token, if the server needs one")
    ap.add_argument("--levels", default="ear_levels.json", help="file that keeps each machine's healthy level")
    ap.add_argument("--expect", choices=["healthy", "faulty"], help="print PASS/FAIL and set the exit code")
    a = ap.parse_args(argv)
    try:
        x = E.read_wav(a.wav)
    except Exception as e:  # noqa: BLE001  (wave.Error, EOFError, OSError, ValueError)
        out(f"cannot read {a.wav}: {e}. The file must be a PCM WAV (see DESK_FAN_TEST.md for how to convert).")
        return 2
    key = f"{a.server.rstrip('/')}|{a.workspace}|{a.asset.strip().lower()}"
    ref = load_levels(a.levels, key)
    out(f"{a.wav}: {len(x) / E.SR:.1f} s -> {PASS_SECONDS:.0f}-second passes, machine '{a.asset}' on {a.server}")
    try:
        if http_machine(a.server, a.workspace, a.asset, a.token) is None and ref.levels:
            out("the server has never heard this machine: old level reference dropped")
            ref.levels.clear()
        out("healthy level reference: " + (f"{ref.value:.0f} dBFS ({len(ref.levels)} learned passes)"
                                           if ref.value is not None else "none yet (set by the first accepted learning passes)"))
        s = replay(x, http_sender(a.server, a.token), asset=a.asset, model=a.model, workspace=a.workspace,
                   worker=a.worker, ref=ref, out=out)
    except ServerDown as e:
        out(f"STOPPED: {e}")
        return 2
    finally:
        if ref.levels or os.path.exists(a.levels): save_levels(a.levels, key, ref)
    return 0 if print_summary(s, os.path.basename(a.wav), a.asset, a.expect, out) else 1


if __name__ == "__main__":
    sys.exit(main())
