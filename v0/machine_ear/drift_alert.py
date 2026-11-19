"""Per-stop baseline drift alert (G155). Rules from MACHINE_EAR_WALKAROUND_ROUTE_TEMPLATE.md:
first 4 rounds = baseline only; alert if a stop drifts vs own baseline for 2 rounds in a row.
Feature = any scalar per clip (e.g. band energy dB). Threshold k*std (proposed, tune with pilot data)."""
import statistics as st
def check(history, k=3.0, baseline_n=4, consecutive=2, room_noise=None, noise_limit=None):
    if len(history) < baseline_n + consecutive: return "baseline"
    base = history[:baseline_n]; mu = st.mean(base); sd = max(st.pstdev(base), 1e-6)
    recent = range(len(history)-consecutive, len(history))
    for i in recent:
        if noise_limit is not None and room_noise and room_noise[i] > noise_limit: return "untrusted_noisy"
    return "ALERT" if all(abs(history[i]-mu) > k*sd for i in recent) else "ok"
if __name__ == "__main__":
    assert check([60,61,60,61]) == "baseline"
    assert check([60,61,60,61,60,61]) == "ok"
    assert check([60,61,60,61,68,69]) == "ALERT"
    assert check([60,61,60,61,60,69]) == "ok"
    assert check([60,61,60,61,68,69],room_noise=[0]*4+[90,90],noise_limit=85)=="untrusted_noisy"
    print("all pass")
