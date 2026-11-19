"""Fetch a subset of MIMII (CC BY-SA 4.0, Zenodo 3384388) via range requests; keep channel 0 only, 16 kHz mono 16-bit.
Re-runnable: skips clips already on disk. Stops after --budget seconds so it fits short shells.
python fetch_mimii.py --snr 6_dB --machine pump --id id_00 --normal 250 --abnormal 100 --budget 100"""
import argparse, io, json, os, sys, time, wave, random
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote_zip import central_dir, member
import numpy as np

a = argparse.ArgumentParser()
a.add_argument("--snr", default="6_dB"); a.add_argument("--machine", default="pump"); a.add_argument("--id", default="id_00")
a.add_argument("--normal", type=int, default=250); a.add_argument("--abnormal", type=int, default=100)
a.add_argument("--names", default="", help="json split file: fetch exactly test_normal + test_abnormal names")
a.add_argument("--with_train", action="store_true")
a.add_argument("--budget", type=float, default=100); a.add_argument("--workers", type=int, default=12)
o = a.parse_args()
url = f"https://zenodo.org/api/records/3384388/files/{o.snr}_{o.machine}.zip/content"
here = os.environ.get("MIMII_DIR") or os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "web", "downloads", "mimii"))
cdp = os.path.join(here, f"cd_{o.snr}_{o.machine}.json")
cd = json.load(open(cdp)) if os.path.exists(cdp) else central_dir(url)
json.dump(cd, open(cdp, "w"))
out = os.path.join(here, f"{o.snr}_{o.machine}_{o.id}")
want = []
for lab, n in (("normal", o.normal), ("abnormal", o.abnormal)):
    ms = sorted([m for m in cd if m["name"].startswith(f"{o.machine}/{o.id}/{lab}/") and m["name"].endswith(".wav")], key=lambda m: m["name"])
    random.Random(0).shuffle(ms)
    if o.names:
        sp = json.load(open(o.names)); keep = set(sp["test_" + lab]) | (set(sp["train_normal"]) if lab == "normal" and o.with_train else set())
        ms, n = [m for m in ms if os.path.basename(m["name"]) in keep], len(keep)
    want += [(lab, m) for m in ms[:n]]
t0 = time.time()

def get(item):
    lab, m = item
    dst = os.path.join(out, lab, os.path.basename(m["name"]))
    if os.path.exists(dst) or time.time() - t0 > o.budget: return 0
    try:
        raw = member(url, m)
    except Exception as e:  # noqa: BLE001  flaky range: skip, the next run retries
        print("skip", m["name"], e); return 0
    w = wave.open(io.BytesIO(raw)); ch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2" if sw == 2 else "<i4").reshape(-1, ch)[:, 0]
    if sw == 4: x = (x >> 16).astype("<i2")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    with wave.open(tmp, "wb") as z:
        z.setnchannels(1); z.setsampwidth(2); z.setframerate(sr); z.writeframes(x.astype("<i2").tobytes())
    os.replace(tmp, dst)
    return 1

with ThreadPoolExecutor(o.workers) as ex:
    got = sum(ex.map(get, want))
have = sum(len(os.listdir(os.path.join(out, l))) for l in ("normal", "abnormal") if os.path.isdir(os.path.join(out, l)))
print(f"fetched {got} this run, {have}/{len(want)} on disk, {time.time()-t0:.0f}s")
