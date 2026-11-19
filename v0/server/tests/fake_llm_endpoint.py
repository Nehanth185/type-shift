"""A stand-in OpenAI-compatible /v1/chat/completions endpoint (no real AI).
Lets the test suite exercise the real 'openai' provider code (request building, image shrinking,
reply parsing) without an API key. It checks the request looks like what NVIDIA NIM expects.
  python tests/fake_llm_endpoint.py 8799
"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        user = msgs[-1]["content"]
        img = [c for c in user if c["type"] == "image_url"][0]["image_url"]["url"]
        problems = []
        if not img.startswith("data:image/jpeg;base64,"): problems.append("image not a jpeg data url")
        if len(img) > 180_100: problems.append(f"image too big for NIM inline ({len(img)} chars)")
        if body["model"].startswith("meta/llama-3.2") and any(m["role"] == "system" for m in msgs):
            problems.append("llama 3.2 vision on NIM rejects system role")
        text = [c for c in user if c["type"] == "text"][0]["text"]
        if "HUD:" not in text and not any(m["role"] == "system" for m in msgs): problems.append("system prompt missing")
        reply = ("HUD: Fake LLM: request OK\nSAY: The request reached the fake endpoint and looked valid."
                 if not problems else "HUD: Request problem\nSAY: " + "; ".join(problems))
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8799), H).serve_forever()
