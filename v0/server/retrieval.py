"""Search a workspace's chunks. BM25 by default (pure Python, no downloads).

Optional dense embeddings: RETRIEVER=embed uses sentence-transformers (EMBED_MODEL, default
sentence-transformers/all-MiniLM-L6-v2) if installed, mixed 50/50 with BM25. If the package is missing the
server falls back to BM25 and says so in /health.

The relevance gate is what makes "not in your docs" work: a hit only counts if the docs contain enough of the
question's rare words (idf-weighted coverage >= MIN_COVERAGE), or, with embeddings, cosine >= MIN_COSINE.
"""
import math, os, re
from collections import Counter

STOP = set("""a an the is are was were be been being am do does did done doing have has had having i me my we our you
your he she it its they them their this that these those there here what which who whom whose when where why how
of in on at to for from by with about into over under as and or but if then so than too very can could should would
will shall may might must just also not no yes please tell show give get make use using need want know see look
go going any some all each per vs okay ok hey glasses one thing things like right now
step steps procedure procedures guide walk through instructions help
long often usually typically""".split())  # meta words: say what kind
# of answer is wanted, not what it is about; "how long" / "how often" / "usually" are question form, not topic
TOKEN = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*")
MIN_COVERAGE = float(os.environ.get("RETRIEVAL_MIN_COVERAGE", "0.5"))
MIN_COSINE = float(os.environ.get("RETRIEVAL_MIN_COSINE", "0.25"))


def stem(w):
    """Crude suffix stripping, applied the same way to docs and questions. Also folds a final 'e' and a doubled last
    consonant so reassembling/reassemble and running/run meet (both missed the gate before 2026-11-12)."""
    if any(ch.isdigit() for ch in w): return w
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            w = w[:-len(suf)]
            break
    if len(w) > 4 and w.endswith("e"): w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls": w = w[:-1]
    return w


def tokens(text, keep_stop=False):
    out = []
    for t in TOKEN.findall((text or "").lower()):
        parts = [t] + ([p for p in re.split(r"[-./]", t) if p] if re.search(r"[-./]", t) else [])
        for p in parts:
            if keep_stop or (p not in STOP and len(p) > 1):
                out.append(stem(p))
    return out


# Small hand list so everyday words find manual words. Embeddings (RETRIEVER=embed) do this properly.
_SYN_GROUPS = ["swap replace replacement change install", "fix repair troubleshoot", "broken fault fail failure",
               "alarm alert alarming", "blank dark off", "screen display oled", "normal range spec rated operating",
               "drain empty", "leak drip", "pin pinout gpio", "start begin", "stop shut shutdown",
               "lock lockout loto", "pressure psi", "wire wiring connect"]
SYN = {}
for _g in _SYN_GROUPS:
    _ws = [stem(w) for w in _g.split()]
    for _w in _ws: SYN.setdefault(_w, set()).update(_ws)

class Embedder:
    """Wraps sentence-transformers. Any object with .name and .encode(list[str]) -> list[list[float]] works."""
    def __init__(self, model_name):
        from sentence_transformers import SentenceTransformer  # noqa: import inside: optional dependency
        self.name, self.m = model_name, SentenceTransformer(model_name)

    def encode(self, texts):
        return [list(map(float, v)) for v in self.m.encode(list(texts), normalize_embeddings=True)]


class RemoteEmbedder:
    """OpenAI-compatible /embeddings endpoint (e.g. NVIDIA NIM). No heavy local dependency.
    Env: EMBED_BASE_URL, EMBED_API_KEY, EMBED_MODEL. Batches of 32."""
    def __init__(self, base_url, api_key, model, post=None):
        self.name, self.base, self.key, self.model = "remote:" + model, base_url.rstrip("/"), api_key, model
        self._post = post

    def _call(self, texts):
        if self._post: return self._post(texts)
        import json, urllib.request
        req = urllib.request.Request(self.base + "/embeddings", method="POST",
            data=json.dumps({"model": self.model, "input": texts, "input_type": "passage",
                             "encoding_format": "float", "truncate": "END"}).encode(),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)["data"]
        return [x["embedding"] for x in sorted(d, key=lambda x: x["index"])]

    def encode(self, texts):
        texts, out = list(texts), []
        for i in range(0, len(texts), 32):
            for v in self._call(texts[i:i + 32]):
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                out.append([x / n for x in v])
        return out


def make_embedder():
    if os.environ.get("RETRIEVER", "bm25").lower() != "embed":
        return None
    if os.environ.get("EMBED_BASE_URL"):
        return RemoteEmbedder(os.environ["EMBED_BASE_URL"], os.environ.get("EMBED_API_KEY", ""),
                              os.environ.get("EMBED_MODEL", "nvidia/nemotron-3.5-embed-1b"))
    try:
        return Embedder(os.environ.get("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    except Exception as e:  # noqa: BLE001
        print(f"[retrieval] embeddings unavailable ({type(e).__name__}: {e}); using BM25 only")
        return None


def pack(vec):
    import struct
    return struct.pack(f"{len(vec)}f", *vec)


def unpack(blob):
    import struct
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _cos(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def search(chunks, question, context_label="", k=4, embedder=None):
    """chunks: rows with text, page, doc_id, title, emb. Returns hits [{..row, score, coverage}] best first,
    [] when nothing passes the relevance gate."""
    if not chunks: return []
    docs = [Counter(tokens(c["title"] + " " + c["text"])) for c in chunks]
    lens = [sum(d.values()) for d in docs]
    avg = sum(lens) / len(lens) or 1
    n = len(chunks)
    df = Counter()
    for d in docs: df.update(d.keys())
    idf = lambda t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))  # noqa: E731
    q_terms = list(dict.fromkeys(tokens(question)))
    ctx_terms = [t for t in dict.fromkeys(tokens(context_label)) if t not in q_terms]
    if not q_terms: return []
    unseen_idf = math.log(1 + (n + 0.5) / 0.5)
    q_weight_total = sum(idf(t) if df[t] else unseen_idf for t in q_terms)

    qv = None
    if embedder is not None and all(c.get("emb") for c in chunks):
        qv = embedder.encode([question + (" " + context_label if context_label else "")])[0]

    k1, b = 1.5, 0.75
    scored = []
    for c, d, L in zip(chunks, docs, lens):
        def bm(terms, w=1.0):
            s = 0.0
            for t in terms:
                f = d.get(t, 0) or max((d.get(v, 0) for v in SYN.get(t, ())), default=0)
                if f: s += w * idf(t) * f * (k1 + 1) / (f + k1 * (1 - b + b * L / avg))
            return s
        q_score = bm(q_terms)
        matched = sum(idf(t) if df[t] else unseen_idf for t in q_terms
                      if d.get(t) or any(d.get(v) for v in SYN.get(t, ())))
        coverage = matched / q_weight_total if q_weight_total else 0.0
        score = q_score + bm(ctx_terms, 0.5) if q_score else 0.0
        cos = _cos(qv, unpack(c["emb"])) if qv is not None else None
        ok = coverage >= MIN_COVERAGE or (cos is not None and cos >= MIN_COSINE)
        if not ok: continue
        scored.append({**{kk: vv for kk, vv in c.items() if kk != "emb"}, "bm25": score, "coverage": round(coverage, 3),
                       "cosine": None if cos is None else round(cos, 3)})
    if not scored: return []
    top_bm = max(s["bm25"] for s in scored) or 1.0
    for s in scored:
        s["score"] = s["bm25"] / top_bm if s["cosine"] is None else 0.5 * s["bm25"] / top_bm + 0.5 * s["cosine"]
    scored.sort(key=lambda s: (-s["score"], s["chunk_id"]))
    return scored[:k]
