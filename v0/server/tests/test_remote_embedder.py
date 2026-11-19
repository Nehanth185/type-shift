import math
from retrieval import RemoteEmbedder

def test_remote_embedder_batches_and_normalizes():
    calls = []
    def post(texts):
        calls.append(len(texts))
        return [[3.0, 4.0] for _ in texts]
    e = RemoteEmbedder("http://x", "k", "m", post=post)
    out = e.encode(["a"] * 70)
    assert calls == [32, 32, 6] and len(out) == 70
    assert math.isclose(out[0][0], 0.6) and math.isclose(out[0][1], 0.8)
    assert e.name == "remote:m"
