#!/usr/bin/env python3
"""G23: NIM embeddings on big_cases; sweep MIN_COSINE. Usage: python3 tests/big_embed_eval.py (needs NVIDIA_API_KEY)."""
import json, os, sys, pathlib, importlib.util, urllib.request, math
H = pathlib.Path(__file__).resolve().parent
src = (H/"build_big_corpus.py").read_text().replace("\nmain()", "\n")
ns = {"__file__": str(H/"build_big_corpus.py")}; exec(compile(src, "b", "exec"), ns)
DOCS, cases = ns["DOCS"], json.loads((H/"big_cases.json").read_text())
URL = os.environ.get("EMBED_BASE_URL", "https://integrate.api.nvidia.com/v1"); KEY = os.environ["NVIDIA_API_KEY"]
MODEL = os.environ.get("EMBED_MODEL", "nvidia/nemotron-3.5-embed-1b")
def emb(texts, kind):
    out = []
    for i in range(0, len(texts), 32):
        req = urllib.request.Request(URL+"/embeddings", method="POST", data=json.dumps({"model": MODEL, "input": texts[i:i+32], "input_type": kind, "encoding_format": "float", "truncate": "END"}).encode(), headers={"Authorization": "Bearer "+KEY, "Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(req, timeout=60))["data"]
        for x in sorted(d, key=lambda x: x["index"]):
            v = x["embedding"]; n = math.sqrt(sum(a*a for a in v)) or 1; out.append([a/n for a in v])
    return out
chunks = [(k, str(i+1), h+". "+b) for k, (t, s) in DOCS.items() for i, (h, b) in enumerate(s)]
CE = emb([c[2] for c in chunks], "passage"); QE = emb([c["q"] for c in cases], "query")
rows = []
for c, q in zip(cases, QE):
    sc = sorted(((sum(a*b for a, b in zip(q, e)), (ch[0], ch[1])) for e, ch in zip(CE, chunks)), reverse=True)
    rows.append((c, sc))
pos = [r for r in rows if r[0]["doc"]]; neg = [r for r in rows if not r[0]["doc"]]
h1 = sum(r[1][0][1] == (r[0]["doc"], r[0]["page"]) for r in pos); h3 = sum((r[0]["doc"], r[0]["page"]) in [x[1] for x in r[1][:3]] for r in pos)
print(f"model {MODEL}: hit@1 {h1}/{len(pos)} hit@3 {h3}/{len(pos)} (ungated)")
print("top1 cos pos: min %.3f median %.3f | neg: max %.3f median %.3f" % (min(r[1][0][0] for r in pos), sorted(r[1][0][0] for r in pos)[len(pos)//2], max(r[1][0][0] for r in neg), sorted(r[1][0][0] for r in neg)[len(neg)//2]))
for t in [0.15,0.2,0.25,0.3,0.35,0.4,0.45]:
    p = sum((r[0]["doc"], r[0]["page"]) in [x[1] for x in r[1][:3] if x[0] >= t] for r in pos)
    n = sum(r[1][0][0] < t for r in neg)
    print(f"MIN_COSINE {t}: answerable hit@3 {p}/{len(pos)}  negatives refused {n}/{len(neg)}")
