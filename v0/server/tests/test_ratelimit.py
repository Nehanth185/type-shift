import workapi


def test_bucket_burst_then_refill():
    t = [0.0]
    b = workapi.TokenBucket(2, 60, clock=lambda: t[0])
    assert b.take("a")[0] and b.take("a")[0]
    ok, wait = b.take("a")
    assert not ok and wait >= 1
    assert b.take("b")[0]          # other worker unaffected
    t[0] += 1.5
    assert b.take("a")[0]
