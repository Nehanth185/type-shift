#!/usr/bin/env python3
"""G43 full-system integration test: one maintenance tech's shift, end to end, through the REAL server over HTTP.

Maria (worker token, locked to workspace "plant7") starts her shift, uploads the team's manuals (one is a scanned,
image-only PDF that only OCR can read), asks cited questions, gets refused on something the docs don't cover, walks
the CP-450 seal swap step by step, reads a gauge photo, saves and forgets notes, gets ready cards, hammers the button
until the rate limit kicks in, and uses every client we have against the same server: the v0 glasses loop
(server/fake_device.py: photo, typed and spoken question), the companion web app (headless Chromium), the MentraOS
miniapp adapter (mentra_app/tests/harness_real.ts, fake session) and the Halo bridge (halo_bridge/bridge.py).
Phase 2 swaps the model for a stand-in vision endpoint that invents a gauge number, to drive the photo-reading
confirm flow (needs_confirm -> /confirm, discard, expiry) through the real server and real provider code.

  python3 integration/shift_scenario.py                 # mock LLM (default): no key, no network
  python3 integration/shift_scenario.py --llm nim       # phase 1 on the real NVIDIA NIM model (NVIDIA_API_KEY)
  python3 integration/shift_scenario.py --skip-browser  # no Chromium (companion steps marked SKIP)

Writes integration/results_<llm>.json. Exit code 0 only if every non-skipped check passed.
Every response that reaches a glasses display (answer_hud, ready-card lines, v0 hud_text) is checked for
<= 3 lines and <= 40 characters per line.
"""
import argparse, base64, io, json, os, re, shutil, socket, subprocess, sys, tempfile, threading, time
import urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
V0 = os.path.dirname(HERE)
SERVER = os.path.join(V0, "server")
ROOT = os.path.dirname(os.path.dirname(V0))
GAUGE = os.path.join(ROOT, "simulator", "test_images", "pressure_gauge.jpg")
SAMPLE = os.path.join(SERVER, "sample_workspace")
WS = "plant7"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
HUD_CHARS, HUD_LINES = 40, 3

R = {"steps": [], "hud_checked": 0, "hud_violations": [], "meta": {}}


# ---------------------------------------------------------------- recording
def step(feature, name, ok, ms=None, detail="", model="", skip=False, gap=False):
    """gap=True: a known, documented gap (not a regression) -> status GAP, never fails the run."""
    st = "SKIP" if skip else ("GAP" if gap else ("PASS" if ok else "FAIL"))
    R["steps"].append({"feature": feature, "step": name, "status": st, "ms": None if ms is None else round(ms),
                       "model": model, "detail": "" if st == "PASS" else str(detail)[:500]})
    print(f"{st:4} [{feature}] {name}" + (f"  ({ms:.0f} ms)" if ms is not None else "")
          + ("" if st == "PASS" else f"  -- {str(detail)[:300]}"), flush=True)
    return ok


def hud_audit(where, resp):
    """Every glasses-bound text: <= 3 lines, <= 40 chars each."""
    if not isinstance(resp, dict): return True
    groups = []
    if isinstance(resp.get("answer_hud"), list): groups.append(resp["answer_hud"])
    if isinstance(resp.get("lines"), list): groups.append(resp["lines"])
    if isinstance(resp.get("hud_text"), str): groups.append([resp["hud_text"]])
    ok = True
    for g in groups:
        R["hud_checked"] += 1
        bad = len(g) > HUD_LINES or any(len(l) > HUD_CHARS for l in g)
        if bad:
            ok = False; R["hud_violations"].append({"where": where, "lines": g})
    return ok


# ---------------------------------------------------------------- http
PACE = {"s": 0.0, "last": 0.0}  # min seconds between /ask calls (a person, not a script; see FEATURE_MATRIX.md)


def http(base, method, path, body=None, token=None, headers=None, raw=None, ctype=None, timeout=60):
    if path == "/ask" and method == "POST" and PACE["s"]:
        time.sleep(max(0.0, PACE["last"] + PACE["s"] - time.time())); PACE["last"] = time.time()
    h = dict(headers or {})
    data = None
    if raw is not None:
        data = raw; h["Content-Type"] = ctype
    elif body is not None:
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    if token: h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    t = time.perf_counter()
    try:
        with OPENER.open(req, timeout=timeout) as r:
            txt = r.read(); st, hd = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        txt = e.read(); st, hd = e.code, dict(e.headers)
    ms = (time.perf_counter() - t) * 1000
    try: js = json.loads(txt or b"null")
    except ValueError: js = {"raw": txt[:200].decode("latin1")}
    return st, js, hd, ms


def multipart(fields, files):
    b = "----g43" + os.urandom(6).hex(); out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fn, ct, data) in files.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: {ct}\r\n\r\n'.encode()
                  + data + b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Server:
    def __init__(self, name, env_extra):
        self.tmp = tempfile.mkdtemp(prefix=f"g43-{name}-")
        self.env = {**os.environ, "TTS_ENGINE": "none", "STT_ENGINE": "none", "RETRIEVER": "bm25", "SEED_DEMO": "",
                    "AUTH_MODE": "worker", "DEVICE_TOKEN": "", "GLASSES_DB": os.path.join(self.tmp, "g.db"),
                    "LOG_DIR": os.path.join(self.tmp, "logs"), "REQUEST_LOG": "1", **env_extra}
        if self.env.get("LLM_PROVIDER") == "mock":
            self.env.pop("NVIDIA_API_KEY", None); self.env.pop("LLM_API_KEY", None)
        self.port = free_port(); self.base = f"http://127.0.0.1:{self.port}"
        self.log = os.path.join(self.tmp, "server.log")

    def token(self, worker, ws=None):
        a = [sys.executable, "workers.py", "add", worker] + (["--workspace", ws] if ws else [])
        return subprocess.run(a, cwd=SERVER, env=self.env, capture_output=True, text=True, check=True).stdout.strip()

    def start(self):
        self.p = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--port", str(self.port),
                                   "--log-level", "warning"], cwd=SERVER, env=self.env,
                                  stdout=open(self.log, "w"), stderr=subprocess.STDOUT)
        t = time.time()
        while time.time() - t < 120:
            try:
                OPENER.open(self.base + "/health", timeout=1).read(); return (time.time() - t) * 1000
            except Exception:  # noqa: BLE001
                if self.p.poll() is not None: raise SystemExit("server died:\n" + open(self.log).read()[-2000:])
                time.sleep(0.2)
        raise SystemExit("server did not start")

    def stop(self):
        self.p.terminate()
        try: self.p.wait(5)
        except subprocess.TimeoutExpired: self.p.kill()

    def log_lines(self):
        return open(self.log).read().splitlines()


# ---------------------------------------------------------------- test data
def scanned_pdf():
    """An image-only PDF (no text layer): the Line 3 conveyor card, as if scanned off the wall."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1700, 2200), "white"); d = ImageDraw.Draw(im)
    try:
        fb = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46)
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    except OSError:
        fb = f = ImageFont.load_default()
    y = 140
    lines = [("LINE 3 CONVEYOR CARD (fictional test document)", fb), ("", f),
             ("Gearbox oil is ISO VG 220. Fill to the center of the sight glass.", f),
             ("Belt tension: 12 mm deflection at mid-span.", f), ("", f),
             ("Belt tracking adjustment", fb),
             ("WARNING: Lock out the conveyor drive before touching the belt.", f),
             ("1. Press the E-stop and lock out the drive disconnect.", f),
             ("2. Loosen the take-up bolts on the tail pulley.", f),
             ("3. Turn the tracking screw a quarter turn toward the belt.", f),
             ("4. Retighten the take-up bolts and run the belt empty for 2 minutes.", f)]
    for text, font in lines:
        d.text((110, y), text, fill="black", font=font); y += 86
    b = io.BytesIO(); im.save(b, "PDF", resolution=200.0); return b.getvalue()


def small_jpeg(path, max_px=1024):
    from PIL import Image
    im = Image.open(path).convert("RGB"); im.thumbnail((max_px, max_px))
    b = io.BytesIO(); im.save(b, "JPEG", quality=85); return b.getvalue()


def spoken_question_wav(text):
    """A spoken question made with the server's own offline TTS (Piper) -> WAV bytes, or None."""
    sys.path.insert(0, SERVER)
    try:
        os.environ.setdefault("TTS_ENGINE", "auto")
        from speech import TTS
        t = TTS(); return t.synth(text) if t.engine != "none" else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        sys.path.remove(SERVER)


# ---------------------------------------------------------------- stand-in vision model for phase 2
class FakeVision(BaseHTTPRequestHandler):
    """OpenAI-compatible /chat/completions that 'reads' 88 PSI off any photo (a number the docs don't contain)."""
    delay = 0.0
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        time.sleep(FakeVision.delay)
        reply = ("HUD: Gauge reads 88 PSI | Normal is 55-70 PSI\nFULL: The gauge reads 88 PSI. Normal discharge "
                 "pressure is 55-70 PSI, so stop the pump and check that the discharge valve is fully open.\nCITE: 1")
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass


# ---------------------------------------------------------------- browser (companion)
def launch_browser(pw):
    try:
        return pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        return str(e).splitlines()[0][:200]


def companion_session(browser, base, token, ws):
    pg = browser.new_page(); errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base + "/app/#settings")
    pg.fill("#workspaceId", ws); pg.fill("#workerToken", token); pg.fill("#workerId", "")
    pg.click("#saveSettings"); pg.wait_for_timeout(400)
    pg.click(".tabs button[data-tab=ask]"); pg.wait_for_timeout(300)
    return pg, errors


def companion_ask(pg, question, photo_path=None):
    if photo_path: pg.set_input_files("#photoIn", photo_path); pg.wait_for_timeout(300)
    pg.fill("#question", question)
    t = time.perf_counter(); pg.click("#askBtn")
    pg.wait_for_function("document.querySelector('#hud1').textContent !== '…' && !document.querySelector('#askBtn').disabled",
                         timeout=40000)
    return [pg.inner_text(s) for s in ("#hud1", "#hud2", "#hud3")], (time.perf_counter() - t) * 1000


# ================================================================ phase 1: the shift
def phase_shift(llm, browser):
    M = "nim" if llm == "nim" else "mock"
    PACE["s"] = 1.0  # default limiter: burst 10, +1 every 2 s. Unpaced, this script hit 429 on its 11th question.
    srv = Server("shift", {"LLM_PROVIDER": llm, "SEED_DEMO": "1", "TTS_ENGINE": "auto", "STT_ENGINE": "auto"})
    tok = {"maria": srv.token("maria", WS), "w1": srv.token("w1"), "w2": srv.token("w2")}
    boot = srv.start()
    B = srv.base
    F = "health/logs"
    st, h, hd, ms = http(B, "GET", "/health", headers={"X-Request-ID": "shift-start-001"})
    step(F, "/health ok, worker auth on, request id echoed", st == 200 and h["status"] == "ok" and h["auth"] == "worker"
         and hd.get("x-request-id", hd.get("X-Request-ID")) == "shift-start-001", ms, h)
    R["meta"]["server_boot_ms"] = round(boot); R["meta"]["health"] = h
    R["meta"]["llm"] = h.get("llm_backend"); R["meta"]["model"] = h.get("model")

    F = "auth"
    st, js, _, ms = http(B, "GET", f"/docs?workspace_id={WS}")
    step(F, "no token -> 401", st == 401, ms, js)
    st, js, _, ms = http(B, "GET", "/docs?workspace_id=demo", token=tok["maria"])
    step(F, "maria is locked to plant7: workspace demo -> 403", st == 403, ms, js)
    st, js, _, ms = http(B, "GET", "/whoami", token=tok["maria"])
    step(F, "token decides identity (/whoami = maria @ plant7)", st == 200 and js["worker_id"] == "maria" and js["workspace_id"] == WS, ms, js)

    # ---- start of shift
    F = "ready cards"
    st, js, _, ms = http(B, "POST", "/context", {"trigger": "start_shift"}, tok["maria"])
    step(F, "start_shift card on an empty memory", st == 200 and hud_audit("start_shift", js) and js["lines"][0] == "Start of shift", ms, js)

    # ---- docs
    F = "docs upload"
    up = {}
    for fn, ct in (("cp450_pump_manual_excerpt.pdf", "application/pdf"), ("glasses_v0_bench_notes.md", "text/markdown")):
        body, ctp = multipart({"workspace_id": WS}, {"file": (fn, ct, open(os.path.join(SAMPLE, fn), "rb").read())})
        st, js, _, ms = http(B, "POST", "/docs", token=tok["maria"], raw=body, ctype=ctp)
        up[fn] = js
        step(F, f"upload {fn}", st == 200 and js.get("chunks", 0) > 0, ms, js)
    body, ctp = multipart({"workspace_id": WS}, {"file": ("line3_conveyor_card_scan.pdf", "application/pdf", scanned_pdf())})
    # while the scan is OCR'd, /health must still answer (G43 fix: OCR used to run on the event loop)
    hl = {}
    def _probe():
        time.sleep(0.4); hl["st"], _, _, hl["ms"] = http(B, "GET", "/health")
    th = threading.Thread(target=_probe); th.start()
    st, js, _, ms = http(B, "POST", "/docs", token=tok["maria"], raw=body, ctype=ctp, timeout=120)
    th.join()
    have_ocr = bool(shutil.which("tesseract") and shutil.which("pdftoppm"))
    step(F, "upload scanned (image-only) PDF -> OCR text indexed", st == 200 and js.get("chunks", 0) > 0, ms, js,
         skip=not have_ocr)
    R["meta"]["ocr_upload_ms"] = round(ms)
    step("concurrency", "/health stays fast while a scanned PDF is OCR'd", hl.get("st") == 200 and hl.get("ms", 9e9) < 800,
         hl.get("ms"), hl)
    st, js, _, ms = http(B, "GET", f"/docs?workspace_id={WS}", token=tok["maria"])
    docs = js if isinstance(js, list) else js.get("docs", [])
    step(F, "list docs = 3 in plant7", st == 200 and len(docs) == 3, ms, js)

    # ---- at the CP-450
    F = "ready cards"
    st, js, _, ms = http(B, "POST", "/context", {"trigger": "see_machine", "label": "CP-450"}, tok["maria"])
    step(F, "see_machine CP-450 card", st == 200 and hud_audit("see_machine-1", js) and js["lines"][0].startswith("CP-450"), ms, js)

    F = "cited answer"
    st, a, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "What is the normal discharge pressure on the CP-450?"},
                        tok["maria"], headers={"X-Request-ID": "shift-ask-001"})
    ok = st == 200 and hud_audit("ask-cited", a) and a.get("sources") and str(a["sources"][0]["page"]) == "14" \
        and a["answer_hud"][-1].startswith("Source:") and "55" in (" ".join(a["answer_hud"]) + a["answer_full"])
    step(F, "CP-450 discharge pressure -> 55-70 PSI, cites p.14", ok, ms, a, M)
    R["meta"]["cited_answer"] = a.get("answer_hud") if st == 200 else a

    F = "OCR doc answer"
    st, a, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "What gearbox oil does the line 3 conveyor use?"}, tok["maria"])
    ok = st == 200 and hud_audit("ask-ocr", a) and a.get("sources") and "conveyor" in a["sources"][0]["title"].lower() \
        and "220" in (" ".join(a["answer_hud"]) + a["answer_full"])
    step(F, "answer from the scanned card (ISO VG 220), cites it", ok, ms, a, M, skip=not have_ocr)

    F = "refusal"
    st, a, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "What is the lug nut torque for the forklift wheels?"}, tok["maria"])
    step(F, "forklift lug nut torque -> 'Not in your docs', no sources", st == 200 and hud_audit("refusal", a)
         and a.get("not_in_docs") and not a.get("sources"), ms, a, M)

    # ---- guide: CP-450 seal swap
    F = "guide mode"
    st, g, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "Show me the seal swap step by step", "mode": "guide"}, tok["maria"])
    total = (g.get("guide") or {}).get("total", 0) if st == 200 else 0
    step(F, "start seal-swap guide: step 1/N, warning first", st == 200 and hud_audit("guide-1", g) and total >= 5
         and g["answer_hud"][0].startswith("⚠"), ms, g)
    seen, fin, gms = [1], None, []
    for _ in range(total):
        st, n, _, ms = http(B, "POST", "/guide/next", {}, tok["maria"]); gms.append(ms)
        hud_audit("guide-next", n)
        if n.get("finished"): fin = n; break
        seen.append(n["guide"]["step"])
    step(F, f"next x{total}: steps 1..{total} in order, then finished", seen == list(range(1, total + 1)) and fin is not None,
         sum(gms) / max(1, len(gms)), [seen, fin])
    st, p, _, ms = http(B, "POST", "/guide/prev", {}, tok["maria"])
    step(F, "prev -> step N-1", st == 200 and hud_audit("guide-prev", p) and p["guide"]["step"] == total - 1, ms, p)
    st, d, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "Done, log it"}, tok["maria"])
    step(F, "G46: 'Done, log it' on step N-1 asks first (no job logged mid-procedure)", st == 200 and hud_audit("done-ask", d)
         and d["answer_hud"][0].startswith("Only on step") and http(B, "POST", "/guide/next", {}, tok["maria"])[0] == 200, ms, d)
    st, d, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "Done, log it"}, tok["maria"])
    step(F, "'Done, log it' at the last step logs the job and closes the guide", st == 200 and hud_audit("done", d) and d.get("kind") == "memory"
         and d["answer_hud"][0].startswith("Logged:") and http(B, "POST", "/guide/next", {}, tok["maria"])[0] == 404, ms, d)

    st, g2, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "How do I adjust the belt tracking on the line 3 conveyor step by step", "mode": "guide"}, tok["maria"])
    ok = st == 200 and hud_audit("guide-ocr", g2) and (g2.get("guide") or {}).get("total", 0) >= 3
    step(F, "guide from the scanned card (OCR'd numbered steps)", ok, ms, g2, skip=not have_ocr)
    if ok: http(B, "POST", "/ask", {"workspace_id": WS, "question": "Done, log it anyway"}, tok["maria"])

    # ---- gauge photo
    F = "photo + docs"
    gb64 = base64.b64encode(small_jpeg(GAUGE)).decode()
    st, a, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "Is this pressure normal?", "image_b64": gb64}, tok["maria"])
    ok = st == 200 and hud_audit("gauge", a) and (a.get("sources") or a.get("not_in_docs"))
    if M == "mock":
        step(F, "gauge photo + question -> cited answer (mock never flags readings)", ok and not a.get("needs_confirm"), ms, a, M)
    else:
        step(F, "gauge photo + question (real model) -> answer or confirm prompt", ok, ms, a, M)
        R["meta"]["nim_gauge"] = {k: a.get(k) for k in ("answer_hud", "answer_full", "needs_confirm", "unverified_numbers")}
        step(F, "real model's photo answer never states an unverified number as fact", not any(
            n in " ".join(a.get("answer_hud", [])) + a.get("answer_full", "") for n in a.get("unverified_numbers") or []
        ) if a.get("needs_confirm") else not a.get("unverified_numbers"), None, a, M)
        if a.get("needs_confirm"):
            st, c, _, ms = http(B, "POST", "/confirm", {"pending_id": a["pending_id"]}, tok["maria"])
            step("photo confirm", "real-model reading flagged -> confirm shows it", st == 200 and hud_audit("nim-confirm", c) and c["confirmed"], ms, c, M)
    st, a, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "", "image_b64": gb64}, tok["maria"])
    step(F, "photo only (no question) -> marked 'Not from your docs'", st == 200 and hud_audit("photo-only", a)
         and a.get("ungrounded") and not a.get("sources"), ms, a, M)

    # ---- memory
    F = "memory"
    st, n, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "Remember to order seal kit SK-450 for pump 3"}, tok["maria"])
    step(F, "voice note saved", st == 200 and hud_audit("note", n) and n.get("kind") == "memory" and n["answer_hud"][0].startswith("Saved"), ms, n)
    st, rc, _, ms = http(B, "POST", "/context", {"trigger": "see_machine", "label": "CP-450"}, tok["maria"])
    j = " ".join(rc.get("lines", []))
    step("ready cards", "CP-450 card recalls the note + today's seal swap", st == 200 and hud_audit("see_machine-2", rc)
         and "seal kit" in (j + " ".join(rc.get("unfinished", []))).lower() and "done" in rc["lines"][0], ms, rc)
    st, ab, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "What do you remember about me?"}, tok["maria"])
    step(F, "'what do you remember about me' -> count, no doc sources", st == 200 and hud_audit("about", ab) and not ab["sources"], ms, ab)
    st, mem, _, ms = http(B, "GET", "/memory", token=tok["maria"])
    step(F, "GET /memory lists only maria's items (asks, guide, job, note, context)", st == 200 and mem["worker_id"] == "maria"
         and {"ask", "guide", "job_done", "note", "context"} <= {i["kind"] for i in mem["items"]}, ms, [i["kind"] for i in mem.get("items", [])])
    st, fg, _, ms = http(B, "POST", "/ask", {"workspace_id": WS, "question": "Forget that"}, tok["maria"])
    mem2 = http(B, "GET", "/memory", token=tok["maria"])[1]["items"]
    step(F, "'forget that' deletes the newest item (the note)", st == 200 and hud_audit("forget", fg)
         and not any(i["kind"] == "note" for i in mem2), ms, fg)
    victim = next(i for i in mem2 if i["kind"] == "ask")
    st2, js2, _, _ = http(B, "DELETE", f"/memory/{victim['id']}", token=tok["w2"])
    st3, js3, _, _ = http(B, "GET", "/memory", token=tok["w2"])
    step("privacy", "w2 cannot see or delete maria's memory", st2 == 404 and st3 == 200 and not any(
        i["id"] == victim["id"] for i in js3["items"]), None, [st2, js3])
    st, js, _, ms = http(B, "DELETE", f"/memory/{victim['id']}", token=tok["maria"])
    step(F, "maria deletes one item", st == 200 and js["deleted"] == victim["id"], ms, js)

    st, oj, _, ms = http(B, "POST", "/context", {"trigger": "open_job", "label": "WO-1182 Line 3 belt"}, tok["maria"])
    step("ready cards", "open_job card (long label stays <= 40 chars)", st == 200 and hud_audit("open_job", oj), ms, oj)

    # ---- v0 glasses loop (fake_device.py, multipart photo; worker token as the device token)
    F = "v0 glasses (fake_device)"
    gpath = os.path.join(srv.tmp, "gauge.jpg"); open(gpath, "wb").write(small_jpeg(GAUGE, 800))
    def fd(*args, token=tok["maria"]):
        t = time.perf_counter()
        r = subprocess.run([sys.executable, os.path.join(SERVER, "fake_device.py"), gpath, "--server", B,
                            "--out", os.path.join(srv.tmp, "a.wav"), *(["--token", token] if token else []), *args],
                           capture_output=True, text=True, timeout=180)
        js = None
        m = re.search(r"\n(\{.*?\n\})", r.stdout, re.S)
        if m:
            try: js = json.loads(m.group(1))
            except ValueError: js = None
        return r, js, (time.perf_counter() - t) * 1000
    r, js, ms = fd("-q", "What does the gauge read?")
    tts_on = "piper" in str(h.get("tts")) or "espeak" in str(h.get("tts"))
    step(F, "photo + typed question -> hud_text <= 40 chars" + (" + 16 kHz WAV" if tts_on else ""), r.returncode == 0 and js
         and hud_audit("v0-typed", js) and (not tts_on or "16000 Hz" in r.stdout), ms, r.stdout[-400:] + r.stderr[-300:], M)
    ungrounded = bool(js) and "sources" not in js
    step(F, "v0 photo answers are grounded in the team docs", not ungrounded, None,
         "GAP: the firmware's /ask path never searches the docs (no sources, no memory, no guide)", gap=ungrounded)
    r, js, ms = fd("--pcm")
    step(F, "photo only, inline PCM audio", r.returncode == 0 and js and hud_audit("v0-pcm", js), ms, r.stdout[-300:], M)
    wav = spoken_question_wav("What is the pressure on this gauge?")
    stt_on = "whisper" in str(h.get("stt"))
    if wav and stt_on:
        wp = os.path.join(srv.tmp, "q.wav"); open(wp, "wb").write(wav)
        r, js, ms = fd("--voice", wp)
        heard = (js or {}).get("heard", "")
        step(F, "spoken question (Piper WAV) -> Whisper -> answer", r.returncode == 0 and js and hud_audit("v0-voice", js)
             and "pressure" in heard.lower(), ms, [heard, r.stdout[-300:]], M)
        R["meta"]["whisper_heard"] = heard
    else:
        step(F, "spoken question -> Whisper", True, None, f"tts={h.get('tts')} stt={h.get('stt')}", skip=True)
    r, js, ms = fd("-q", "x", token="")
    step(F, "no token in worker mode -> refused (G43 fix: was open)", r.returncode != 0 and "401" in r.stderr, ms, r.stderr[-200:])
    st, _, _, _ = http(B, "GET", "/last")
    st2, _, _, _ = http(B, "GET", "/last", token=tok["maria"])
    step(F, "/last photo needs a token in worker mode", st == 401 and st2 == 200, None, [st, st2])

    # ---- Halo bridge
    F = "Halo bridge"
    sys.path.insert(0, os.path.join(V0, "halo_bridge"))
    import bridge as halo
    dev = halo.FakeHalo(photo=small_jpeg(GAUGE, 640))
    t = time.perf_counter()
    try:
        b = halo.Bridge(dev, lambda q, j: halo.http_ask(B, tok["maria"], q, j, workspace_id=WS),
                        lambda pid, ok: halo.http_confirm(B, tok["maria"], pid, ok))
        r = b.on_ask("What is the normal discharge pressure on the CP-450?")
        rows = [row for p in b.pages for row in p.split("\n")]
        ok = bool(r.get("sources")) and dev.shown[-1] == b.pages[0] and all(len(x) <= 14 for x in rows) and hud_audit("halo", r)
        step(F, "on_ask -> real /ask -> paged 14-char rows, cited", ok, (time.perf_counter() - t) * 1000, [r, b.pages], M)
        b.on_tap(1); b.on_tap(3)
        step(F, "tap next / tap cancel", dev.shown[-1] is None, None, dev.shown[-3:])
    except Exception as e:  # noqa: BLE001
        step(F, "on_ask -> real /ask", False, (time.perf_counter() - t) * 1000, repr(e))

    # ---- companion web app (served by this server)
    F = "companion app"
    if isinstance(browser, str) or browser is None:
        step(F, "companion flows in Chromium", True, None, browser or "--skip-browser", skip=True)
    else:
        pg, errs = companion_session(browser, B, tok["maria"], WS)
        hud, ms = companion_ask(pg, "What is the normal discharge pressure on the CP-450?")
        last = http(B, "GET", "/memory", token=tok["maria"])[1]["items"][0]
        step(F, "settings (token) + ask -> HUD = server answer, sources listed", last["text"].startswith("Q: What is the normal")
             and all(l in last["text"] for l in hud if l) and "p.14" in pg.inner_text("#sources") and "undefined" not in pg.inner_text("#sources"),
             ms, [hud, last["text"]], M)
        pg.click(".tabs button[data-tab=memory]"); pg.wait_for_timeout(800)
        step(F, "memory view shows maria's items", "seal swap" in pg.inner_text("#memList").lower() or "Q:" in pg.inner_text("#memList"),
             None, pg.inner_text("#memList")[:200])
        step(F, "no page errors", not errs, None, errs)
        pg.close()

    # ---- rate limit (last for maria: it empties her bucket). Refused-at-retrieval questions: no LLM spend.
    F = "rate limit"
    PACE["s"] = 0.0  # now tap as fast as possible
    codes, retry, t0 = [], None, time.perf_counter()
    for i in range(16):
        st, js, hd, _ = http(B, "POST", "/ask", {"workspace_id": WS, "question": "Forklift lug nut torque?"}, tok["maria"])
        codes.append(st)
        if st == 429: retry = hd.get("retry-after", hd.get("Retry-After")); break
    step(F, "rapid taps -> 429 + Retry-After", 429 in codes and retry is not None, (time.perf_counter() - t0) * 1000, codes)
    st, js, _, _ = http(B, "GET", "/memory", token=tok["maria"])
    step(F, "other endpoints still work while limited (GET /memory 200)", st == 200, None, st)

    # ---- Mentra miniapp adapter: its own real-server harness, pointed at THIS server (w1/w2, workspace demo)
    F = "Mentra miniapp"
    node = shutil.which("node")
    if not node:
        step(F, "harness_real.ts", True, None, "node not installed", skip=True)
    else:
        env = {**os.environ, "REAL_URL": B, "TOKEN_W1": tok["w1"], "TOKEN_W2": tok["w2"], "HARNESS_LLM": M,
               "LOG_FILE": os.path.join(srv.env["LOG_DIR"], "asks_v1.jsonl")}
        t = time.perf_counter()
        r = subprocess.run([node, "--experimental-strip-types", "tests/harness_real.ts"], cwd=os.path.join(V0, "mentra_app"),
                           env=env, capture_output=True, text=True, timeout=600)
        summ = re.findall(r"(\d+)/(\d+) passed", r.stdout)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        step(F, f"harness_real.ts vs this server: {summ[-1][0]}/{summ[-1][1] if summ else '?'}" if summ else "harness_real.ts",
             r.returncode == 0, (time.perf_counter() - t) * 1000, fails or r.stderr[-400:], M)

    # ---- request log
    F = "health/logs"
    lines = srv.log_lines()
    recs = []
    for l in lines:
        try: recs.append(json.loads(l))
        except ValueError: pass
    mine = [x for x in recs if x.get("request_id") == "shift-ask-001"]
    step(F, "request log: JSON line per request, client X-Request-ID kept", bool(mine) and mine[0]["path"] == "/ask"
         and mine[0]["status"] == 200 and "latency_ms" in mine[0], None, mine or lines[-3:])
    leak = [l for l in lines if any(t and t in l for t in tok.values())]
    step(F, "no worker token ever written to the server log", not leak, None, leak[:2])
    R["meta"]["requests_logged"] = len(recs)
    srv.stop()


# ================================================================ phase 2: photo reading confirm flow
def phase_confirm(browser):
    vport = free_port()
    vs = ThreadingHTTPServer(("127.0.0.1", vport), FakeVision)
    threading.Thread(target=vs.serve_forever, daemon=True).start()
    srv = Server("confirm", {"LLM_PROVIDER": "openai", "LLM_BASE_URL": f"http://127.0.0.1:{vport}/v1", "LLM_API_KEY": "x",
                             "LLM_MODEL": "fake-vision", "SEED_DEMO": "1", "CONFIRM_TTL": "4"})
    tok = {"maria": srv.token("maria", "demo"), "w2": srv.token("w2", "demo")}
    srv.start(); B = srv.base
    F, M = "photo confirm", "stand-in vision"
    gb64 = base64.b64encode(small_jpeg(GAUGE)).decode()
    ask = lambda: http(B, "POST", "/ask", {"workspace_id": "demo", "question": "Is this pressure normal?", "image_b64": gb64}, tok["maria"])
    st, a, _, ms = ask()
    ok = st == 200 and a.get("needs_confirm") and "88" not in " ".join(a["answer_hud"]) and hud_audit("needs_confirm", a)
    step(F, "invented reading (88 PSI) held back: HUD warns, needs_confirm", ok, ms, a, M)
    R["meta"]["confirm_spoken_full"] = a.get("answer_full")
    step(F, "answer_full (spoken on Mentra / shown in app) also holds 88 back, keeps the doc range (G43 fix)",
         "88" not in a.get("answer_full", "") and "55-70" in a.get("answer_full", ""), None, a.get("answer_full"))
    st, c, _, _ = http(B, "POST", "/confirm", {"pending_id": a["pending_id"]}, tok["w2"])
    step(F, "another worker can't confirm it (G43 fix)", st == 404, None, [st, c])
    st, c, _, ms = http(B, "POST", "/confirm", {"pending_id": a["pending_id"]}, tok["maria"])
    step(F, "owner confirms -> reading shown, sources are objects", st == 200 and c["confirmed"] and hud_audit("confirm", c)
         and "88" in " ".join(c["answer_hud"]) and isinstance(c["sources"][0], dict), ms, c)
    st, a, _, _ = ask()
    st, c, _, ms = http(B, "POST", "/confirm", {"pending_id": a["pending_id"], "ok": False}, tok["maria"])
    step(F, "discard -> 'Reading discarded'", st == 200 and not c["confirmed"] and hud_audit("discard", c), ms, c)
    st, a, _, _ = ask(); time.sleep(4.5)
    st, c, _, ms = http(B, "POST", "/confirm", {"pending_id": a["pending_id"]}, tok["maria"])
    step(F, "confirm after CONFIRM_TTL -> 404 expired", st == 404, ms, c)

    sys.path.insert(0, os.path.join(V0, "halo_bridge"))
    import bridge as halo
    dev = halo.FakeHalo(photo=small_jpeg(GAUGE, 640))
    b = halo.Bridge(dev, lambda q, j: halo.http_ask(B, tok["maria"], q, j), lambda pid, ok: halo.http_confirm(B, tok["maria"], pid, ok))
    try:
        b.on_ask("Is this pressure normal?"); held = dev.shown[-1]
        b.on_voice("confirm")
        step("Halo bridge", "confirm flow vs real server: held, then 'confirm' shows 88", "88" not in held and "88" in dev.shown[-1], None, dev.shown[-2:])
    except Exception as e:  # noqa: BLE001
        step("Halo bridge", "confirm flow vs real server", False, None, repr(e))

    if isinstance(browser, str) or browser is None:
        step("companion app", "confirm buttons", True, None, browser or "--skip-browser", skip=True)
    else:
        pg, errs = companion_session(browser, B, tok["maria"], "demo")
        gp = os.path.join(srv.tmp, "gauge.jpg"); open(gp, "wb").write(small_jpeg(GAUGE))
        hud, ms = companion_ask(pg, "Is this pressure normal?", gp)
        vis = pg.is_visible("#confirmYes")
        pg.click("#confirmYes"); pg.wait_for_timeout(800)
        hud2 = [pg.inner_text(s) for s in ("#hud1", "#hud2", "#hud3")]
        src = pg.inner_text("#sources")
        step("companion app", "photo -> Confirm button -> reading shown with real source (G43 fix)", vis and "88" not in " ".join(hud)
             and "88" in " ".join(hud2) and "undefined" not in src and "p.14" in src and not errs, ms, [hud, hud2, src, errs])
        pg.close()

    # concurrency: a slow model call must not stall other workers / health
    FakeVision.delay = 2.0
    th = threading.Thread(target=ask); th.start(); time.sleep(0.5)
    st, _, _, ms = http(B, "GET", "/health")
    st2, _, _, ms2 = http(B, "GET", "/memory", token=tok["w2"])
    th.join(); FakeVision.delay = 0
    step("concurrency", "2 s model call in flight: /health and another worker's request still fast", st == 200 and ms < 800
         and st2 == 200 and ms2 < 800, max(ms, ms2), [ms, ms2])
    srv.stop(); vs.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", choices=["mock", "nim"], default="mock")
    ap.add_argument("--skip-browser", action="store_true")
    ap.add_argument("--skip-confirm", action="store_true")
    a = ap.parse_args()
    if a.llm == "nim" and not os.environ.get("NVIDIA_API_KEY"):
        raise SystemExit("--llm nim needs NVIDIA_API_KEY")
    t0 = time.time()
    pw = browser = None
    if not a.skip_browser:
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start(); browser = launch_browser(pw)
        except Exception as e:  # noqa: BLE001
            browser = f"playwright unavailable: {e}"[:200]
    try:
        phase_shift(a.llm, browser)
        if not a.skip_confirm: phase_confirm(browser)
    finally:
        if pw:
            if not isinstance(browser, str): browser.close()
            pw.stop()
    s = [x["status"] for x in R["steps"]]
    R["summary"] = {"pass": s.count("PASS"), "fail": s.count("FAIL"), "skip": s.count("SKIP"), "gap": s.count("GAP"), "llm": a.llm,
                    "hud_responses_checked": R["hud_checked"], "hud_violations": len(R["hud_violations"]),
                    "wall_s": round(time.time() - t0, 1), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    if R["hud_violations"]:
        step("HUD", f"{len(R['hud_violations'])} glasses-bound responses broke 3 lines x 40 chars", False, None, R["hud_violations"][:3])
        R["summary"]["fail"] += 1
    else:
        print(f"PASS [HUD] all {R['hud_checked']} glasses-bound responses <= 3 lines x 40 chars")
    out = os.path.join(HERE, f"results_{a.llm}.json")
    json.dump(R, open(out, "w"), indent=1)
    print(f"\n{R['summary']['pass']} pass, {R['summary']['fail']} fail, {R['summary']['gap']} known gap, {R['summary']['skip']} skip "
          f"({a.llm}, {R['summary']['wall_s']} s) -> {out}")
    sys.exit(1 if R["summary"]["fail"] else 0)


if __name__ == "__main__":
    main()
