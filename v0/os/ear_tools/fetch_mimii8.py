"""Fetch ALL 8 channels of the FIRST few seconds of MIMII clips we already hold as channel-0 mono (CC BY-SA 4.0,
Zenodo 3384388). Used by the multi-mic study (ear_multimic_eval.py). Only the start of each zip member is downloaded
(one HTTP range covering ~SECS of compressed audio, inflated with a streaming zlib decoder), so a 10 s 8-ch clip
(2.56 MB) costs ~0.6 MB. Saved as int16 .npy, shape (SECS*16000, 8), in web/downloads/mimii8/<same folder>/<label>/.
Mirrors the clip names already on disk in web/downloads/mimii/<snr>_<machine>_<id>/ so every split stays identical.
Re-runnable; stops after --budget seconds.
  python ear_tools/fetch_mimii8.py --snr 0_dB --machine pump --id id_04 --budget 100"""
import argparse, json, os, struct, sys, time, zlib
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote_zip import rng
import numpy as np

a = argparse.ArgumentParser()
a.add_argument("--snr", default="0_dB"); a.add_argument("--machine", default="pump"); a.add_argument("--id", default="id_04")
a.add_argument("--secs", type=float, default=3.0)
a.add_argument("--budget", type=float, default=100); a.add_argument("--workers", type=int, default=12)
o = a.parse_args()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "web", "downloads"))
url = f"https://zenodo.org/api/records/3384388/files/{o.snr}_{o.machine}.zip/content"
cd = {m["name"]: m for m in json.load(open(os.path.join(ROOT, "mimii", f"cd_{o.snr}_{o.machine}.json")))}
folder = f"{o.snr}_{o.machine}_{o.id}"
NEED = int(o.secs * 16000) * 8 * 2
want = []
for lab in ("normal", "abnormal"):
    d = os.path.join(ROOT, "mimii", folder, lab)
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if n.endswith(".wav"): want.append((lab, cd[f"{o.machine}/{o.id}/{lab}/{n}"]))
t0 = time.time(); stats = {"bytes": 0}


def head(m, nbytes):
    blob = rng(url, m["offset"], m["offset"] + min(30 + 600 + nbytes, 30 + 600 + m["csize"]) - 1)
    fl, el = struct.unpack("<HH", blob[26:30])
    stats["bytes"] += len(blob)
    return blob[30 + fl + el:]


def get(item):
    lab, m = item
    dst = os.path.join(ROOT, "mimii8", folder, lab, os.path.basename(m["name"])[:-4] + ".npy")
    if os.path.exists(dst) or time.time() - t0 > o.budget: return 0
    ratio = m["csize"] / m["usize"]
    try:
        nb = int(ratio * (NEED + 200) * 1.25) + 30000
        for _ in range(3):
            raw = head(m, nb)
            pcm = zlib.decompressobj(-15).decompress(raw) if m["method"] == 8 else raw
            i = pcm.find(b"data")
            if i >= 0 and len(pcm) >= i + 8 + NEED: break
            nb *= 2
        else:
            raise IOError("short")
        ch, = struct.unpack("<H", pcm[22:24]); sw = struct.unpack("<H", pcm[34:36])[0] // 8
        assert ch == 8 and sw == 2, (ch, sw)
        x = np.frombuffer(pcm[i + 8:i + 8 + NEED], "<i2").reshape(-1, 8)
    except Exception as e:  # noqa: BLE001
        print("skip", m["name"], e); return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    np.save(dst + ".part.npy", x); os.replace(dst + ".part.npy", dst)
    return 1


with ThreadPoolExecutor(o.workers) as ex:
    got = sum(ex.map(get, want))
d8 = os.path.join(ROOT, "mimii8", folder)
have = sum(len(os.listdir(os.path.join(d8, l))) for l in ("normal", "abnormal") if os.path.isdir(os.path.join(d8, l)))
print(f"{folder}: fetched {got} ({stats['bytes']/1e6:.0f} MB) this run, {have}/{len(want)} on disk, {time.time()-t0:.0f}s", flush=True)
