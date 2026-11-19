#!/usr/bin/env python3
"""
MOCK SERVER - canned data only. NOT the real answer engine.

Implements the companion API contract (same paths and shapes as the real
prototype/v0/server, see ../API.md; checked by tests/test_companion_real.py against the real one) so the companion web app can be run and
tested standalone. Nothing here calls a model, reads a PDF, or does retrieval:
answers are scripted from DEMO_SCRIPT.md (scene A/B/C) and keyword-matched.

Standard library only. Run:
    python3 mock_server.py --port 8765
then open http://localhost:8765/  (the companion is served from this folder).

Contract (all JSON unless noted, CORS open):
  POST   /docs            multipart: file, workspace_id -> {doc_id,title,pages,chunks}
  GET    /docs?workspace_id=                            -> [{doc_id,title,pages,chunks,created_at}]
  DELETE /docs/{doc_id}                                 -> {deleted:doc_id}
  POST   /ask   {workspace_id,worker_id,question,image_b64?,mode}
                -> {answer_hud:[<=3 lines], answer_full, sources:[{doc_id,title,page,snippet}], guide?, latency_ms}
  POST   /guide/next, /guide/prev {worker_id}           -> same shape as /ask (guide filled; finished:true past the last step)
  GET    /memory?worker_id=                             -> {worker_id, items:[{id,text,kind,created_at,label}]}
  DELETE /memory/{id}                                   -> {deleted:id}
  DELETE /memory?worker_id=                             -> {worker_id, deleted:n}
  GET    /health                                        -> {ok, mock}  (no auth: the mock ignores tokens)
  POST   /context {worker_id,trigger,label}             -> {lines,last_answer,usual_next_step,unfinished}
Mock-only helpers (not part of the contract):
  GET    /__mock/last     -> last /ask request body (image_b64 replaced by its length)
  POST   /__mock/reset    -> wipe all mock state
"""
import argparse, base64, email.parser, email.policy, json, mimetypes, os, re, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = threading.Lock()
MAX_UPLOAD = 25 * 1024 * 1024

def now(): return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

# ---------------- canned content (MOCK, from DEMO_SCRIPT.md) ----------------
SEED_DOCS = [
    {"doc_id": "doc_cp450", "title": "CP-450 Pump Manual (MOCK)", "pages": 48, "chunks": 112},
    {"doc_id": "doc_pumpguide", "title": "Unit IV Pump Guide (MOCK)", "pages": 12, "chunks": 30},
    {"doc_id": "doc_wiring", "title": "glasses-v0 WIRING.md (MOCK)", "pages": 3, "chunks": 9},
]
GUIDES = {
    "seal": {"title": "CP-450 seal swap", "doc": "doc_cp450", "short": "pump manual", "steps": [
        ("Lock out pump first. Close both valves", 21),
        ("Drain casing. Fig 7 drain plug", 22),
        ("Remove 4 cover bolts, 13 mm", 23),
        ("Pull old seal. Clean the seat", 24),
        ("Press in new seal, lip inward", 25),
        ("Refit cover. Torque per table 3", 26)]},
    "alarm": {"title": "Pump alarm: air in line", "doc": "doc_pumpguide", "short": "pump guide", "steps": [
        ("Pause pump. Clamp the line", 4),
        ("Open door. Find the air", 4),
        ("Prime per guide. Close door", 5),
        ("Restart. Check rate. Follow policy", 5)]},
}
ANSWERS = [  # (regex, hud lines, full answer, [(doc_id, page, snippet)])
    (r"pressure|psi|normal", ["CP-450 normal: 55-70 PSI", "Now about 62 PSI, OK", "Source: pump manual p.14"],
     "MOCK: The CP-450 runs at 55-70 PSI in normal service. The gauge in your photo reads about 62 PSI, which is inside that band. Source: CP-450 manual, page 14, table 2.",
     [("doc_cp450", 14, "Table 2. Normal discharge pressure: 55-70 PSI at rated flow. Above 75 PSI, stop and check the discharge valve.")]),
    (r"i2c|sda|scl|pins?", ["XIAO S3: SDA = D4 (GPIO5)", "SCL = D5 (GPIO6)", "Source: your WIRING.md"],
     "MOCK: On the XIAO ESP32-S3, I2C SDA is D4 (GPIO5) and SCL is D5 (GPIO6). Source: WIRING.md in your glasses-v0 repo.",
     [("doc_wiring", 1, "OLED SDA -> D4 (GPIO5), OLED SCL -> D5 (GPIO6), 3V3 and GND shared.")]),
    (r"blank|screen|oled", ["Check OLED address 0x3C/0x3D", "Camera shares the I2C bus", "Source: WIRING.md (untested)"],
     "MOCK: A blank OLED is most often the wrong I2C address (0x3C vs 0x3D). The camera shares the bus on this board, which is untested. Treat this as a lead, not a diagnosis.",
     [("doc_wiring", 2, "OLED default address 0x3C; some modules ship at 0x3D. Camera SCCB shares the I2C pins (untested).")]),
    (r"bed 12|night shift|handoff", ["Bed 12: fall risk, high", "Night: up twice, dizzy", "Source: handoff note 06:40"],
     "MOCK (fictional, not clinical data): Night shift flagged bed 12 as high fall risk; the patient was up twice and reported dizziness.",
     [("doc_pumpguide", 1, "MOCK handoff note 06:40 - bed 12 high fall risk, up x2 overnight, c/o dizziness.")]),
    (r"long test", ["This line is deliberately far too long for the one eye display",
                    "Second line ok", "Third line ok", "Fourth line should never show"],
     "MOCK: deliberately over-long HUD reply, used to test that the companion clamps lines to 7 words and 3 lines.",
     []),
]
CONTEXT = {
    "see_machine": {"lines": ["CP-450 · seal done 11/4", "Leak check due today", "▸ start check (2 steps)"],
                    "last_answer": "CP-450 normal: 55-70 PSI (manual p.14)",
                    "usual_next_step": "Leak check around the new seal",
                    "unfinished": ["Leak check 7 days after seal swap (due today)"]},
    "open_job": {"lines": ["glasses-v0 · OLED still open", "Yesterday: try pull-ups", "▸ show wiring diagram"],
                 "last_answer": "Check OLED address 0x3C/0x3D",
                 "usual_next_step": "Open WIRING.md",
                 "unfinished": ["Test I2C pull-ups"]},
    "start_shift": {"lines": ["Your 4 beds: 9, 10, 12, 14", "Bed 12: fall risk carried over", "Pump guide saved ▸"],
                    "last_answer": "Alarm: air in line (unit pump guide)",
                    "usual_next_step": "Read handoff notes first",
                    "unfinished": []},
}

def fresh_state():
    return {"docs": {"default": [dict(d, created_at=now()) for d in SEED_DOCS]},
            "memory": {"w1": [
                {"id": "m_seed1", "kind": "profile", "text": "Prefers short steps, PSI units", "created_at": now()},
                {"id": "m_seed2", "kind": "open_loop", "text": "Seal replaced on CP-450 11/4: leak check in 7 days", "created_at": now()}]},
            "guide": {}, "last": None}
STATE = fresh_state()

def clamp_line(s, n=7):
    w = str(s).split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "")

def doc_title(ws, doc_id):
    for d in STATE["docs"].get(ws, []) + [dict(x) for x in SEED_DOCS]:
        if d["doc_id"] == doc_id: return d["title"]
    return doc_id

def remember(worker, text, kind="habit"):
    STATE["memory"].setdefault(worker, []).insert(0, {"id": "m_" + uuid.uuid4().hex[:8], "kind": kind, "text": text, "created_at": now()})

def guide_payload(worker, ws, t0):
    g = STATE["guide"][worker]; gd = GUIDES[g["key"]]; i = g["i"]; text, page = gd["steps"][i]
    total = len(gd["steps"])
    hud = [f"Step {i+1}/{total}: " + text.split(".")[0], (text.split(".")[1].strip() if "." in text else "") or "▸ swipe for next", f"Source: {gd['short']} p.{page}"]
    return {"answer_hud": [clamp_line(h) for h in hud], "answer_full": f"MOCK guide '{gd['title']}', step {i+1} of {total}: {text}.",
            "sources": [{"doc_id": gd["doc"], "title": doc_title(ws, gd["doc"]), "page": page, "snippet": f"Step {i+1}: {text}."}],
            "guide": {"step": i + 1, "total": total, "text": text}, "latency_ms": int((time.time() - t0) * 1000) + 180, "mock": True}

def ask(body):
    t0 = time.time()
    ws = body.get("workspace_id") or "default"; worker = body.get("worker_id") or "w1"
    q = (body.get("question") or "").strip(); mode = body.get("mode") or "answer"; img = body.get("image_b64")
    if not q and not img: return 400, {"error": "question or image_b64 required"}
    if img:
        try: base64.b64decode(img.split(",")[-1][:4096] + "==", validate=False)
        except Exception: return 400, {"error": "image_b64 is not base64"}
    STATE["last"] = {k: (len(v) if k == "image_b64" and v else v) for k, v in body.items()}
    if mode == "guide":
        key = "alarm" if re.search(r"alarm|air|iv", q, re.I) else "seal"
        STATE["guide"][worker] = {"key": key, "i": 0, "ws": ws}
        remember(worker, f"Started guide: {GUIDES[key]['title']}")
        return 200, guide_payload(worker, ws, t0)
    for rx, hud, full, srcs in ANSWERS:
        if re.search(rx, q, re.I):
            remember(worker, f"Asked: {q[:60]}")
            return 200, {"answer_hud": [clamp_line(h) for h in hud[:3]] if rx != r"long test" else hud,  # long test: send raw, client must clamp
                         "answer_full": full,
                         "sources": [{"doc_id": d, "title": doc_title(ws, d), "page": p, "snippet": s} for d, p, s in srcs],
                         "latency_ms": int((time.time() - t0) * 1000) + 240, "mock": True}
    if img and not q:
        return 200, {"answer_hud": ["Can't read that", "Move closer, more light", "No source used"],
                     "answer_full": "MOCK: photo received but no question; the mock does not look at images.", "sources": [],
                     "latency_ms": 300, "mock": True}
    return 200, {"answer_hud": ["No answer in your docs", "Try different words", "or upload the manual"],
                 "answer_full": f"MOCK: no canned answer matches '{q}'. The real server would search your docs and say 'not found' rather than guess.",
                 "sources": [], "latency_ms": 200, "mock": True}

class H(BaseHTTPRequestHandler):
    server_version = "MockCompanion/0.1"
    def log_message(self, *a): pass
    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.send_header("X-Mock", "true"); self.end_headers(); self.wfile.write(data)
    def do_OPTIONS(self): self._send(204, raw=b"")
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD: raise ValueError("too large")
        return self.rfile.read(n) if n else b""
    def _json(self):
        try: return json.loads(self._body() or b"{}")
        except Exception: return None

    def do_GET(self):
        u = urlparse(self.path); qs = parse_qs(u.query); p = u.path
        with LOCK:
            if p == "/docs":
                ws = (qs.get("workspace_id") or ["default"])[0]
                return self._send(200, STATE["docs"].get(ws, []))
            if p == "/memory":
                w = (qs.get("worker_id") or ["w1"])[0]
                return self._send(200, {"worker_id": w, "items": [dict(i, label=i.get("label")) for i in STATE["memory"].get(w, [])], "mock": True})
            if p == "/__mock/last": return self._send(200, STATE["last"] or {})
            if p == "/health": return self._send(200, {"ok": True, "mock": True})
        # static files (the companion app itself)
        rel = "index.html" if p in ("/", "") else p.lstrip("/")
        fp = os.path.normpath(os.path.join(HERE, rel))
        if fp.startswith(HERE) and os.path.isfile(fp) and not fp.endswith(".py"):
            with open(fp, "rb") as f: data = f.read()
            return self._send(200, raw=data, ctype=mimetypes.guess_type(fp)[0] or "application/octet-stream")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/docs":
            try: raw = self._body()
            except ValueError: return self._send(413, {"error": "file too large"})
            ct = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ct: return self._send(400, {"error": "multipart/form-data required"})
            msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(b"Content-Type: " + ct.encode() + b"\r\n\r\n" + raw)
            fields, fname, fbytes = {}, None, None
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if part.get_filename(): fname, fbytes = part.get_filename(), part.get_payload(decode=True) or b""
                elif name: fields[name] = part.get_content().strip()
            if not fname: return self._send(400, {"error": "file field required"})
            ws = fields.get("workspace_id") or "default"
            pages = max(1, fbytes.count(b"/Type /Page") - fbytes.count(b"/Type /Pages")) if fname.lower().endswith(".pdf") else max(1, len(fbytes) // 3000 + 1)
            d = {"doc_id": "doc_" + uuid.uuid4().hex[:8], "title": os.path.splitext(fname)[0], "pages": pages,
                 "chunks": max(1, len(fbytes) // 800 + 1), "created_at": now()}
            with LOCK: STATE["docs"].setdefault(ws, []).insert(0, d)
            return self._send(200, dict(d, mock=True))
        body = self._json()
        if body is None and p != "/__mock/reset": return self._send(400, {"error": "invalid JSON"})
        with LOCK:
            if p == "/confirm":
                return self._send(200, {"answer_hud":["Confirmed"] if body.get("ok",True) else ["Reading discarded"],"answer_full":"Confirmed by worker." if body.get("ok",True) else "Discarded.","confirmed":bool(body.get("ok",True)),"sources":[],"mock":True})
            if p == "/ask":
                code, obj = ask(body); return self._send(code, obj)
            if p in ("/guide/next", "/guide/prev"):
                w = body.get("worker_id") or "w1"; g = STATE["guide"].get(w)
                if not g: return self._send(404, {"detail": "no active guide: ask with mode 'guide' first"})
                n = len(GUIDES[g["key"]]["steps"])
                if p.endswith("next") and g["i"] == n - 1:  # past the last step, like the real server
                    out = guide_payload(w, g["ws"], time.time())
                    out.update(finished=True, answer_hud=[f"All {n} steps done", "Say 'done, log it'", out["answer_hud"][-1]])
                    return self._send(200, out)
                g["i"] = min(n - 1, g["i"] + 1) if p.endswith("next") else max(0, g["i"] - 1)
                return self._send(200, guide_payload(w, g["ws"], time.time()))
            if p == "/context":
                t = body.get("trigger")
                if t not in CONTEXT: return self._send(400, {"error": "trigger must be see_machine|open_job|start_shift"})
                c = dict(CONTEXT[t]); label = (body.get("label") or "").strip()
                if label and t == "see_machine" and "CP-450" not in label.upper():
                    c = {"lines": [f"{label[:24]}: first time seen", "No history yet", "Ask anything ▸"], "last_answer": None,
                         "usual_next_step": None, "unfinished": []}
                return self._send(200, dict(c, mock=True))
            if p == "/__mock/reset":
                STATE.clear(); STATE.update(fresh_state()); return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        u = urlparse(self.path); qs = parse_qs(u.query); p = u.path
        with LOCK:
            m = re.fullmatch(r"/docs/([\w-]+)", p)
            if m:
                for ws, lst in STATE["docs"].items():
                    for d in lst:
                        if d["doc_id"] == m.group(1): lst.remove(d); return self._send(200, {"deleted": d["doc_id"]})
                return self._send(404, {"error": "doc not found"})
            m = re.fullmatch(r"/memory/([\w-]+)", p)
            if m:
                for w, lst in STATE["memory"].items():
                    for it in lst:
                        if it["id"] == m.group(1): lst.remove(it); return self._send(200, {"deleted": it["id"]})
                return self._send(404, {"error": "memory item not found"})
            if p == "/memory":
                w = (qs.get("worker_id") or [None])[0]
                if not w: return self._send(400, {"error": "worker_id required"})
                n = len(STATE["memory"].get(w, [])); STATE["memory"][w] = []
                return self._send(200, {"worker_id": w, "deleted": n})
        self._send(404, {"error": "not found"})

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8765); ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"MOCK companion server (canned data) on http://{a.host}:{a.port}/", flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()
