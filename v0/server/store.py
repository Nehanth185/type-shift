"""SQLite storage for team docs (per workspace) and personal memory (per worker).

One file (GLASSES_DB, default data/glasses.db). Tables:
  docs, pages, chunks         team knowledge, scoped by workspace_id
  memory                      a worker's personal memory: text facts only, never photos or audio
  guides                      the one active step-by-step guide per worker (points at a memory row)
  workers                     per-worker access tokens (AUTH_MODE=worker); only a SHA-256 of each token is kept
"""
import hashlib, json, os, secrets, sqlite3, threading, time, uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  doc_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL, filename TEXT,
  kind TEXT, pages INTEGER, chunks INTEGER, created_at TEXT);
CREATE INDEX IF NOT EXISTS docs_ws ON docs(workspace_id);
CREATE TABLE IF NOT EXISTS pages (
  doc_id TEXT NOT NULL, seq INTEGER NOT NULL, page TEXT NOT NULL, text TEXT NOT NULL,
  PRIMARY KEY (doc_id, seq));
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
  seq INTEGER NOT NULL, page TEXT NOT NULL, text TEXT NOT NULL, emb BLOB);
CREATE INDEX IF NOT EXISTS chunks_ws ON chunks(workspace_id);
CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS memory_worker ON memory(worker_id, ts);
CREATE TABLE IF NOT EXISTS guides (
  worker_id TEXT PRIMARY KEY, mem_id TEXT, workspace_id TEXT, doc_id TEXT, title TEXT,
  steps TEXT NOT NULL, warning TEXT, idx INTEGER NOT NULL DEFAULT 0, sources TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS workers (
  worker_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, workspace_id TEXT, created_at TEXT NOT NULL);
"""


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def now_iso(ts=None):
    g = time.gmtime(ts if ts is not None else time.time())
    # formatted by hand: ISO 8601 UTC, e.g. 2026-11-11T15:42:00Z
    return f"{g.tm_year:04d}-{g.tm_mon:02d}-{g.tm_mday:02d}T{g.tm_hour:02d}:{g.tm_min:02d}:{g.tm_sec:02d}Z"


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.execute("CREATE TABLE IF NOT EXISTS pending(id TEXT PRIMARY KEY, exp REAL, body TEXT)")
            self.db.commit()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.rowcount

    # ---------------- pending confirms (expire after ttl seconds)
    def pending_put(self, pid, data, ttl=60):
        self.x("DELETE FROM pending WHERE exp < ?", (time.time(),))
        self.x("INSERT OR REPLACE INTO pending VALUES(?,?,?)", (pid, time.time() + ttl, json.dumps(data)))

    def pending_get(self, pid):
        rows = self.q("SELECT exp, body FROM pending WHERE id=?", (pid,))
        if not rows or rows[0]["exp"] < time.time():
            return None
        return json.loads(rows[0]["body"])

    def pending_pop(self, pid):
        rows = self.q("SELECT exp, body FROM pending WHERE id=?", (pid,))
        self.x("DELETE FROM pending WHERE id=?", (pid,))
        if not rows or rows[0]["exp"] < time.time():
            return None
        return json.loads(rows[0]["body"])

    # ---------------- docs
    def add_doc(self, workspace_id, title, filename, kind, pages, chunks, embs=None):
        """pages: [(page_label, text)], chunks: [(page_label, text)], embs: optional list of bytes."""
        doc_id = new_id("doc")
        with self.lock:
            self.db.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?)",
                            (doc_id, workspace_id, title, filename, kind, len(pages), len(chunks), now_iso()))
            self.db.executemany("INSERT INTO pages VALUES (?,?,?,?)",
                                [(doc_id, i, str(p), t) for i, (p, t) in enumerate(pages)])
            self.db.executemany(
                "INSERT INTO chunks (doc_id, workspace_id, seq, page, text, emb) VALUES (?,?,?,?,?,?)",
                [(doc_id, workspace_id, i, str(p), t, (embs[i] if embs else None)) for i, (p, t) in enumerate(chunks)])
            self.db.commit()
        return doc_id

    def list_docs(self, workspace_id):
        return self.q("SELECT doc_id, title, filename, kind, pages, chunks, created_at FROM docs "
                      "WHERE workspace_id=? ORDER BY created_at, rowid", (workspace_id,))

    def get_doc(self, doc_id):
        r = self.q("SELECT * FROM docs WHERE doc_id=?", (doc_id,))
        return r[0] if r else None

    def delete_doc(self, doc_id):
        with self.lock:
            n = self.db.execute("DELETE FROM docs WHERE doc_id=?", (doc_id,)).rowcount
            self.db.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
            self.db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            self.db.commit()
        return n

    def chunks_for(self, workspace_id):
        return self.q("SELECT c.chunk_id, c.doc_id, c.seq, c.page, c.text, c.emb, d.title FROM chunks c "
                      "JOIN docs d ON d.doc_id=c.doc_id WHERE c.workspace_id=? ORDER BY c.chunk_id", (workspace_id,))

    def pages_for(self, doc_id):
        return self.q("SELECT seq, page, text FROM pages WHERE doc_id=? ORDER BY seq", (doc_id,))

    # ---------------- memory (personal, per worker)
    def add_memory(self, worker_id, kind, text, meta=None, ts=None):
        ts = ts if ts is not None else time.time()
        mid = new_id("mem")
        self.x("INSERT INTO memory VALUES (?,?,?,?,?,?,?)",
               (mid, worker_id, kind, text, json.dumps(meta or {}), now_iso(ts), ts))
        return mid

    def update_memory(self, mid, text=None, meta=None):
        if text is not None:
            self.x("UPDATE memory SET text=? WHERE id=?", (text, mid))
        if meta is not None:
            self.x("UPDATE memory SET meta=? WHERE id=?", (json.dumps(meta), mid))

    def memory(self, worker_id, kinds=None):
        rows = self.q("SELECT * FROM memory WHERE worker_id=? ORDER BY ts DESC, rowid DESC", (worker_id,))
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return [r for r in rows if not kinds or r["kind"] in kinds]

    def get_memory(self, mid):
        r = self.q("SELECT * FROM memory WHERE id=?", (mid,))
        if not r: return None
        r[0]["meta"] = json.loads(r[0]["meta"] or "{}")
        return r[0]

    def delete_memory(self, mid):
        with self.lock:
            n = self.db.execute("DELETE FROM memory WHERE id=?", (mid,)).rowcount
            self.db.execute("DELETE FROM guides WHERE mem_id=?", (mid,))
            self.db.commit()
        return n

    def forget_worker(self, worker_id):
        with self.lock:
            n = self.db.execute("DELETE FROM memory WHERE worker_id=?", (worker_id,)).rowcount
            self.db.execute("DELETE FROM guides WHERE worker_id=?", (worker_id,))
            self.db.commit()
        return n

    # ---------------- active guide
    def set_guide(self, worker_id, mem_id, workspace_id, doc_id, title, steps, warning, sources):
        self.x("INSERT OR REPLACE INTO guides VALUES (?,?,?,?,?,?,?,?,?)",
               (worker_id, mem_id, workspace_id, doc_id, title, json.dumps(steps), warning, 0, json.dumps(sources)))

    def get_guide(self, worker_id):
        r = self.q("SELECT * FROM guides WHERE worker_id=?", (worker_id,))
        if not r: return None
        g = r[0]
        g["steps"], g["sources"] = json.loads(g["steps"]), json.loads(g["sources"])
        return g

    def set_guide_idx(self, worker_id, idx):
        self.x("UPDATE guides SET idx=? WHERE worker_id=?", (idx, worker_id))

    def end_guide(self, worker_id):
        self.x("DELETE FROM guides WHERE worker_id=?", (worker_id,))

    # ---------------- worker tokens (AUTH_MODE=worker)
    def add_worker(self, worker_id, workspace_id=None):
        """Create the worker, or rotate its token. Returns the new token; it is shown once and never stored."""
        token = "wt_" + secrets.token_urlsafe(24)
        self.x("INSERT OR REPLACE INTO workers VALUES (?,?,?,?)",
               (worker_id, token_hash(token), workspace_id or None, now_iso()))
        return token

    def worker_for_token(self, token):
        if not token: return None
        r = self.q("SELECT worker_id, workspace_id, created_at FROM workers WHERE token_hash=?", (token_hash(token),))
        return r[0] if r else None

    def list_workers(self):
        return self.q("SELECT worker_id, workspace_id, created_at FROM workers ORDER BY worker_id")

    def revoke_worker(self, worker_id):
        return self.x("DELETE FROM workers WHERE worker_id=?", (worker_id,))
