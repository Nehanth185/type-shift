"""Worker context: the asset/machine in front of them, the active work order, a location tag, and time in shift.
Sent by the glasses/phone with every /os/input (all optional). Missing asset falls back to the last /context label."""
import time
from dataclasses import asdict, dataclass

import memory
from .learn import phase_of


@dataclass
class Ctx:
    asset: str = ""          # e.g. "P-12" (from a QR / nameplate read or picked in the app)
    model: str = ""          # the asset's model as the docs name it, e.g. "CP-450"; used to search docs
    work_order: str = ""     # e.g. "WO-881"
    location: str = ""       # e.g. "Line 2"
    shift_minute: int = None  # minutes since the shift started
    shift_len: int = 480
    shift_id: str = ""
    phase: str = "unknown"

    @property
    def doc_label(self):
        return self.model or self.asset

    def public(self):
        return asdict(self)


def build(raw, worker_id, store):
    raw = raw or {}
    def s(k, n=60): return str(raw.get(k) or "").strip()[:n]
    c = Ctx(asset=s("asset"), model=s("model"), work_order=s("work_order"), location=s("location"),
            shift_id=s("shift_id", 80))
    try:
        c.shift_len = max(60, min(24 * 60, int(raw.get("shift_len") or 480)))
    except (TypeError, ValueError):
        pass
    m = raw.get("shift_minute")
    if m is None and raw.get("shift_start_ts"):
        try: m = (time.time() - float(raw["shift_start_ts"])) / 60
        except (TypeError, ValueError): m = None
    try:
        c.shift_minute = None if m is None else max(0, int(float(m)))
    except (TypeError, ValueError):
        c.shift_minute = None
    if not c.asset:
        c.asset = memory.current_label(store, worker_id)
    if c.asset:  # remember which docs model this asset tag is (P-12 -> CP-450), so later requests can skip it
        from . import osdb
        models = osdb.state_get(worker_id, "asset_models", {}) or {}
        if c.model and models.get(c.asset) != c.model:
            models[c.asset] = c.model
            osdb.state_set(worker_id, "asset_models", dict(list(models.items())[-50:]))
        elif not c.model:
            c.model = models.get(c.asset, "")
    if not c.shift_id:
        c.shift_id = time.strftime("%Y-%m-%d")
    c.phase = phase_of(c.shift_minute, c.shift_len)
    return c
