"""Time in intensity buckets (easy / steady / threshold / hard).

Bucket floors come from goals.yaml (zones.hr_buckets). The HR stream is used
when present; otherwise the source's own zone times are mapped onto buckets
by each zone's lower bound, and the result is marked as approximate.
"""

from __future__ import annotations

from typing import Any

from adapters.models import Activity

BUCKETS = ("easy", "steady", "threshold", "hard")
MAX_SAMPLE_GAP_S = 30          # longer gaps are pauses, not running


def bucket_floors(goals: dict) -> list[tuple[str, float]]:
    raw = ((goals.get("zones") or {}).get("hr_buckets") or {})
    floors = [(b, float(raw.get(b, 0) or 0)) for b in BUCKETS]
    return sorted(floors, key=lambda x: x[1])


def _bucket(hr: float, floors: list[tuple[str, float]]) -> str:
    name = floors[0][0]
    for b, f in floors:
        if hr >= f:
            name = b
    return name


def time_in_buckets(a: Activity, floors: list[tuple[str, float]]) -> dict[str, Any]:
    """{'secs': {bucket: s}, 'source': 'stream'|'zones'|None}"""
    secs = {b: 0.0 for b in BUCKETS}
    st = a.stream
    if st and st.hr and any(h for h in st.hr):
        for i in range(1, len(st.t_s)):
            gap = st.t_s[i] - st.t_s[i - 1]
            h = st.hr[i]
            if h is None or gap <= 0 or gap > MAX_SAMPLE_GAP_S:
                continue
            secs[_bucket(h, floors)] += gap
        return {"secs": secs, "source": "stream"}
    if a.zone_secs and a.zone_floors and len(a.zone_secs) == len(a.zone_floors):
        for s, f in zip(a.zone_secs, a.zone_floors):
            if f is not None:
                secs[_bucket(f, floors)] += s or 0
        return {"secs": secs, "source": "zones"}
    return {"secs": secs, "source": None}


def shares(secs: dict[str, float]) -> dict[str, float]:
    total = sum(secs.values())
    return {b: (secs[b] / total if total else 0.0) for b in BUCKETS}


def distribution(per_activity: list[dict[str, float]]) -> dict[str, Any]:
    total = {b: sum(p.get(b, 0) for p in per_activity) for b in BUCKETS}
    return {"secs": {b: round(v) for b, v in total.items()},
            "share": {b: round(v, 3) for b, v in shares(total).items()}}
