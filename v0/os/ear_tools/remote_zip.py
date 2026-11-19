"""Read a few members out of a huge remote zip (Zenodo) with HTTP range requests, without downloading the whole zip.
Usage: python remote_zip.py URL list | python remote_zip.py URL get OUTDIR PATTERN... (see fetch_mimii.py)"""
import io, time, struct, subprocess, sys, zlib

def rng(url, a, b, tries=4):
    for _ in range(tries):
        p = subprocess.run(["curl", "-sfL", "--max-time", "120", "-r", f"{a}-{b}", url], capture_output=True)
        if p.returncode == 0 and len(p.stdout) == b - a + 1:
            return p.stdout
        time.sleep(15)   # usually HTTP 429 (rate limit); wait and retry
    raise IOError(f"range {a}-{b} failed")

def size(url):
    out = subprocess.run(["curl", "-sIL", url], capture_output=True, text=True).stdout
    return int([l for l in out.lower().splitlines() if l.startswith("content-length")][-1].split(":")[1])

def central_dir(url):
    n = size(url)
    tail = rng(url, n - 65536, n - 1)
    i = tail.rfind(b"PK\x05\x06")
    cd_size, cd_off = struct.unpack("<II", tail[i + 12:i + 20])
    j = tail.rfind(b"PK\x06\x06")          # zip64 end record
    if j >= 0:
        cd_size, cd_off = struct.unpack("<QQ", tail[j + 40:j + 56])
    cd = rng(url, cd_off, cd_off + cd_size - 1)
    out, p = [], 0
    while cd[p:p + 4] == b"PK\x01\x02":
        meth, = struct.unpack("<H", cd[p + 10:p + 12])
        csz, usz = struct.unpack("<II", cd[p + 20:p + 28])
        fl, el, cl = struct.unpack("<HHH", cd[p + 28:p + 34])
        off, = struct.unpack("<I", cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + fl].decode()
        ex = cd[p + 46 + fl:p + 46 + fl + el]
        q = 0
        while q < len(ex):                   # zip64 extra field
            hid, hl = struct.unpack("<HH", ex[q:q + 4]); d = ex[q + 4:q + 4 + hl]; k = 0
            if hid == 1:
                if usz == 0xFFFFFFFF: usz, = struct.unpack("<Q", d[k:k + 8]); k += 8
                if csz == 0xFFFFFFFF: csz, = struct.unpack("<Q", d[k:k + 8]); k += 8
                if off == 0xFFFFFFFF: off, = struct.unpack("<Q", d[k:k + 8]); k += 8
            q += 4 + hl
        out.append({"name": name, "method": meth, "csize": csz, "usize": usz, "offset": off})
        p += 46 + fl + el + cl
    return out

def member(url, m, slack=512):
    """One request per member (Zenodo allows ~130 requests/min): local header + data + slack in a single range."""
    blob = rng(url, m["offset"], m["offset"] + 30 + slack + m["csize"] - 1)
    fl, el = struct.unpack("<HH", blob[26:30])
    raw = blob[30 + fl + el:30 + fl + el + m["csize"]]
    return raw if m["method"] == 0 else zlib.decompress(raw, -15)
