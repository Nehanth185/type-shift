"""G157: synthetic walk-around sim. Measures false-alert rate and rounds-to-detect for drift_alert.check.
SYNTHETIC data only (not real machines); shows how k and noise affect alerts. Tune with pilot data."""
import random, drift_alert as d
def run(k, noise_sd, drift_start=None, drift=6.0, rounds=20, seed=0):
    r = random.Random(seed); h = []
    for i in range(rounds):
        v = 60 + r.gauss(0, noise_sd) + (drift if drift_start is not None and i >= drift_start else 0)
        h.append(v)
        if d.check(h, k=k) == "ALERT": return i
    return None
if __name__ == "__main__":
    print("k  noise  false_alert%  mean_rounds_to_detect(drift 6dB at round 8)")
    for k in (2, 3, 4):
        for ns in (0.5, 1.0, 2.0):
            fa = sum(run(k, ns, None, seed=s) is not None for s in range(500)) / 5
            det = [run(k, ns, 8, seed=s) for s in range(500)]
            ok = [x - 8 for x in det if x is not None and x >= 8]
            print(k, ns, f"{fa:.1f}", f"{sum(ok)/len(ok):.1f}" if ok else "none", f"missed={sum(x is None for x in det)}")
