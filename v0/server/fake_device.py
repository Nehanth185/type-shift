"""Pretend to be the glasses: send a photo (and optionally a question or a spoken-question WAV)
to the server exactly like the firmware does, print the reply, save the spoken answer.

  python fake_device.py                                   # first image in ../../../simulator/test_images
  python fake_device.py photo.jpg -q "What is the voltage?"
  python fake_device.py photo.jpg --voice question.wav
  python fake_device.py photo.jpg --server http://192.168.1.50:8000 --pcm
Standard library only (no pip install needed).
"""
import argparse, base64, glob, io, json, os, time, urllib.request, uuid, wave

HERE = os.path.dirname(os.path.abspath(__file__))
# The glasses talk straight to the laptop on the LAN, so ignore any HTTP proxy settings.
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def multipart(fields, files, boundary):
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, ctype, data) in files.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                  f'Content-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue()


def hud_preview(text, width=12, lines=3):
    words, rows, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            rows.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    rows.append(cur)
    rows = rows[:lines]
    bar = "+" + "-" * (width + 2) + "+"
    return "\n".join([bar] + [f"| {r.center(width)} |" for r in rows] + [bar])


def main():
    ap = argparse.ArgumentParser()
    default_img = next(iter(sorted(glob.glob(os.path.join(HERE, "..", "..", "..", "simulator", "test_images", "*.jpg")))), None)
    ap.add_argument("image", nargs="?", default=default_img)
    ap.add_argument("-q", "--question", default="")
    ap.add_argument("--voice", help="WAV file with a spoken question")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--pcm", action="store_true", help="ask for audio inline (base64) instead of a URL")
    ap.add_argument("--token", default=os.environ.get("DEVICE_TOKEN", ""))
    ap.add_argument("--out", default="answer.wav")
    a = ap.parse_args()
    if not a.image: raise SystemExit("give an image path")

    jpeg = open(a.image, "rb").read()
    b = "----glassesv0" + uuid.uuid4().hex
    files = {"image": ("frame.jpg", "image/jpeg", jpeg)}
    if a.voice: files["audio"] = ("q.wav", "audio/wav", open(a.voice, "rb").read())
    fields = {"device_id": "fake-device"}
    if a.question: fields["question"] = a.question
    body = multipart(fields, files, b)
    url = a.server.rstrip("/") + "/ask" + ("?audio=pcm" if a.pcm else "")
    hdr = {"Content-Type": f"multipart/form-data; boundary={b}"}
    if a.token: hdr["X-Device-Token"] = a.token
    t0 = time.time()
    reply = json.load(urllib.request.urlopen(urllib.request.Request(url, body, hdr, method="POST"), timeout=120))
    rtt = time.time() - t0

    print(f"image: {os.path.basename(a.image)} ({len(jpeg)//1024} KB)   round trip: {rtt*1000:.0f} ms")
    print(json.dumps({k: (v[:60] + "...") if k == "pcm_b64" else v for k, v in reply.items()}, indent=2))
    print("\nOLED preview:\n" + hud_preview(reply.get("hud_text", "")))

    wav = None
    if reply.get("pcm_b64"):
        wav = base64.b64decode(reply["pcm_b64"])
    elif reply.get("audio_url"):
        wav = urllib.request.urlopen(a.server.rstrip("/") + reply["audio_url"], timeout=30).read()
    if wav:
        open(a.out, "wb").write(wav)
        with wave.open(io.BytesIO(wav)) as w:
            print(f"\nspoken answer: {a.out}  {w.getframerate()} Hz, {w.getnchannels()} ch, "
                  f"{w.getsampwidth()*8}-bit, {w.getnframes()/w.getframerate():.1f} s, {len(wav)//1024} KB")
    else:
        print("\n(no audio: server has no TTS engine)")


if __name__ == "__main__":
    main()
