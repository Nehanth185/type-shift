"""Halo <-> answer server glue. Device is injected: FakeHalo for tests, a real brilliant-ble wrapper later (untested on hardware)."""
import base64, json, os, urllib.request
from hud import paginate

WORKSPACE = os.environ.get("HALO_WORKSPACE", "demo")

def hud_text(r):
    """Server answer -> one string for paginate(). answer_hud is a list of HUD lines (API.md); a plain string is
    accepted too (older stubs). G43: on_ask used to call paginate() on the list itself and crashed."""
    h = r.get("answer_hud") or r.get("answer") or "No answer"
    return " ".join(h) if isinstance(h, list) else str(h)

class FakeHalo:
    def __init__(self, photo=b"\xff\xd8fake\xff\xd9"):
        self.photo, self.shown, self.events = photo, [], []
    def capture_jpeg(self): return self.photo
    def show(self, text): self.shown.append(text)
    def clear(self): self.shown.append(None)

def http_ask(base, token, question, jpeg, workspace_id=None, mode="answer"):
    # G43: the server needs workspace_id and mode "answer"|"guide" (it was sending mode "ask" and no workspace -> 422)
    b = {"workspace_id": workspace_id or WORKSPACE, "question": question, "mode": mode}
    if jpeg: b["image_b64"] = base64.b64encode(jpeg).decode()
    body = json.dumps(b).encode()
    req = urllib.request.Request(base + "/ask", body, {"Content-Type": "application/json", "Authorization": "Bearer " + token})
    return json.load(urllib.request.urlopen(req, timeout=30))

class Bridge:
    def __init__(self, dev, ask, confirm=None):
        self.dev, self.ask, self.confirm, self.pages, self.i, self.pending = dev, ask, confirm, [], 0, None
    def on_ask(self, question):
        r = self.ask(question, self.dev.capture_jpeg())
        if r.get("needs_confirm") and r.get("pending_id"):  # photo reading the docs can't back: hold it until the worker agrees
            self.pending, self.pages = r["pending_id"], []
            self.dev.show("Check reading? Tap1=yes Tap3=no"); return r
        self.pages = paginate(hud_text(r))
        self.i = 0; self.dev.show(self.pages[0]); return r
    def _resolve(self, ok):
        pid, self.pending = self.pending, None
        r = self.confirm(pid, ok)
        self.pages = paginate(hud_text(r)); self.i = 0
        self.dev.show(self.pages[0])
    def on_voice(self, word):
        if self.pending and word.strip().lower() in ("confirm", "yes"): self._resolve(True)
        elif self.pending and word.strip().lower() in ("discard", "no", "cancel"): self._resolve(False)
    def on_tap(self, n):  # 1 next page, 2 prev, 3 cancel; while a reading waits: 1 confirm, 3 discard
        if self.pending: return self._resolve(n == 1) if n in (1, 3) else None
        if n == 3: self.pages = []; self.dev.clear(); return
        if not self.pages: return
        self.i = min(len(self.pages)-1, self.i+1) if n == 1 else max(0, self.i-1)
        self.dev.show(self.pages[self.i])

def http_confirm(base, token, pid, ok):
    body = json.dumps({"pending_id": pid, "ok": ok}).encode()
    req = urllib.request.Request(base + "/confirm", body, {"Content-Type": "application/json", "Authorization": "Bearer " + token})
    return json.load(urllib.request.urlopen(req, timeout=30))
