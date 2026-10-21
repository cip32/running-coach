"""Session labelling: easy, strides, LT (incl. LT1), hard, long, plus double.

Rules are deliberately simple and tunable from goals.yaml `classification`.
A planned-workout title on the same day is a strong hint and wins over the
HR-based guess, because the athlete knows what the session was meant to be.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from adapters.models import Activity

HARD_WORDS = re.compile(r"interval|fartlek|burner|vo2|time trial|\btt\b|test|race|hill|track|repeats|30/30|45/15|pyramid", re.I)
LT_WORDS = re.compile(r"threshold|\blt\b|lt2|tempo|cruise", re.I)
LT1_WORDS = re.compile(r"\blt1\b|steady|aerobic threshold", re.I)
LONG_WORDS = re.compile(r"\blong", re.I)


def count_strides(a: Activity, rules: dict) -> int:
    """Short bursts much faster than the run's typical speed."""
    factor = float(rules.get("strides_min_speed_vs_avg", 1.25))
    max_s = float(rules.get("strides_max_lap_s", 40))
    st = a.stream
    if st and st.speed and any(st.speed):
        speeds = [v for v in st.speed if v]
        if len(speeds) < 10:
            return 0
        typical = statistics.median(speeds)
        n, start = 0, None
        for i, v in enumerate(st.speed):
            fast = bool(v) and v >= typical * factor
            if fast and start is None:
                start = st.t_s[i]
            elif not fast and start is not None:
                if 6 <= st.t_s[i] - start <= max_s:
                    n += 1
                start = None
        return n
    if a.laps and a.avg_speed:
        return sum(1 for l in a.laps
                   if l.duration_s and l.duration_s <= max_s and (l.avg_speed or 0) >= a.avg_speed * factor)
    return 0


def hr_drift_pct(a: Activity) -> float | None:
    """Aerobic decoupling: % drop in speed/HR from the first to the second half."""
    st = a.stream
    if not st or not st.hr or not st.speed:
        return None
    pts = [(t, h, v) for t, h, v in zip(st.t_s, st.hr, st.speed) if h and v and v > 1.5]
    if len(pts) < 60:
        return None
    half = (pts[0][0] + pts[-1][0]) / 2
    first = [v / h for t, h, v in pts if t < half]
    second = [v / h for t, h, v in pts if t >= half]
    if not first or not second:
        return None
    ef1, ef2 = statistics.fmean(first), statistics.fmean(second)
    return round((ef1 - ef2) / ef1 * 100, 1)


def classify(a: Activity, bucket_share: dict[str, float], rules: dict, *,
             planned_title: str | None = None, run_of_day: int = 1) -> dict[str, Any]:
    km = (a.distance_m or 0) / 1000
    minutes = (a.moving_s or a.duration_s or 0) / 60
    strides = count_strides(a, rules)
    title = planned_title or ""
    is_long = km >= float(rules.get("long_run_min_km", 24)) or minutes >= float(rules.get("long_run_min_minutes", 105)) \
        or bool(LONG_WORDS.search(title))

    if is_long:
        kind = "long"
    elif HARD_WORDS.search(title) or bucket_share.get("hard", 0) >= float(rules.get("hard_min_share", 0.08)):
        kind = "hard"
    elif LT_WORDS.search(title) or bucket_share.get("threshold", 0) >= float(rules.get("lt_min_share", 0.2)):
        kind = "LT"
    elif LT1_WORDS.search(title) or bucket_share.get("steady", 0) >= 0.35:
        kind = "LT1"
    elif strides >= 3:
        kind = "strides"
    else:
        kind = "easy"

    grey = kind in ("easy", "strides") and \
        bucket_share.get("steady", 0) > float(rules.get("grey_zone_max_steady_share", 0.15))
    return {
        "type": kind,
        "double": run_of_day > 1,
        "strides": strides,
        "grey_zone": grey,
        "hr_drift_pct": hr_drift_pct(a) if kind == "long" else None,
    }
