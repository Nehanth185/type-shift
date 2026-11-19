"""The OS's own tables, in the same SQLite file as the work API (workapi.store()).

  os_events    the worker's PRIVATE event log (what app, which asset, when in the shift). Text only, no photos/audio.
               Written only while the worker's learning flag is on. Readable and deletable only by that worker.
  os_settings  per worker: learning on/off (opt-in), verbosity override
  os_state     small per-worker app state (active checklist, a gauge reading waiting for confirmation, ...)
There is deliberately no function here that reads events across workers or by workspace.
"""
import json, os, time

import workapi
from store import new_id

LEARNING_DEFAULT = os.environ.get("OS_LEARNING_DEFAULT", "0") in ("1", "true", "on")  # off = opt-in
SCHEMA = """
CREATE TABLE IF NOT EXISTS os_events (
  id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, ts REAL NOT NULL, shift_id TEXT, shift_minute INTEGER, phase TEXT,
  app TEXT, intent TEXT, asset TEXT, ctx_asset TEXT, work_order TEXT, location TEXT, action TEXT, detail TEXT,
  text TEXT, signal TEXT);
CREATE INDEX IF NOT EXISTS os_events_worker ON os_events(worker_id, ts);
CREATE TABLE IF NOT EXISTS os_settings (worker_id TEXT PRIMARY KEY, learning INTEGER, verbosity TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS os_state (worker_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY (worker_id, key));
"""
FIELDS = ["shift_id", "shift_minute", "phase", "app", "intent", "asset", "ctx_asset", "work_order", "location",
          "action", "detail", "text", "signal"]


def st():
    s = workapi.store()
    if not getattr(s, "_os_ready", False):
        with s.lock:
            s.db.executescript(SCHEMA); s.db.commit()
        s._os_ready = True
    return s


# ---------------- settings
def settings(wid):
    r = st().q("SELECT learning, verbosity, updated FROM os_settings WHERE worker_id=?", (wid,))
    if not r:
        return {"learning": LEARNING_DEFAULT, "verbosity": "auto", "learning_set_by_worker": False}
    return {"learning": bool(r[0]["learning"]), "verbosity": r[0]["verbosity"] or "auto", "learning_set_by_worker": True}


def set_settings(wid, learning=None, verbosity=None):
    cur = settings(wid)
    learning = cur["learning"] if learning is None else bool(learning)
    verbosity = verbosity or cur["verbosity"]
    st().x("INSERT OR REPLACE INTO os_settings VALUES (?,?,?,?)", (wid, int(learning), verbosity, time.time()))
    return settings(wid)


# ---------------- events (private)
def log_event(wid, ev, ts=None):
    if not settings(wid)["learning"]:
        return None
    eid = new_id("ev")
    vals = [ev.get(f) for f in FIELDS]
    vals[FIELDS.index("text")] = (vals[FIELDS.index("text")] or "")[:200]
    st().x(f"INSERT INTO os_events (id, worker_id, ts, {', '.join(FIELDS)}) VALUES (?,?,?{',?' * len(FIELDS)})",
           (eid, wid, ts if ts is not None else time.time(), *vals))
    return eid


def events(wid):
    return st().q("SELECT * FROM os_events WHERE worker_id=? ORDER BY ts, rowid", (wid,))


def delete_event(wid, eid):
    return st().x("DELETE FROM os_events WHERE id=? AND worker_id=?", (eid, wid))


def delete_latest_event(wid):
    r = st().q("SELECT id FROM os_events WHERE worker_id=? ORDER BY ts DESC, rowid DESC LIMIT 1", (wid,))
    return delete_event(wid, r[0]["id"]) if r else 0


def forget(wid):
    n = st().x("DELETE FROM os_events WHERE worker_id=?", (wid,))
    st().x("DELETE FROM os_state WHERE worker_id=?", (wid,))
    return n


# ---------------- small app state
def state_get(wid, key, default=None):
    r = st().q("SELECT value FROM os_state WHERE worker_id=? AND key=?", (wid, key))
    return json.loads(r[0]["value"]) if r else default


def state_set(wid, key, value):
    if value is None:
        st().x("DELETE FROM os_state WHERE worker_id=? AND key=?", (wid, key))
    else:
        st().x("INSERT OR REPLACE INTO os_state VALUES (?,?,?)", (wid, key, json.dumps(value)))
