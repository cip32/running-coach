"""Training load: daily load, ATL/CTL (EWMA), ACWR, weekly volume."""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Any

from adapters.models import Activity

ATL_DAYS, CTL_DAYS = 7, 42


def trimp(a: Activity, rest_hr: float, max_hr: float) -> float | None:
    """Banister TRIMP; fallback when the source has no training load."""
    if not a.avg_hr or not (a.moving_s or a.duration_s) or max_hr <= rest_hr:
        return None
    ratio = max(0.0, min(1.0, (a.avg_hr - rest_hr) / (max_hr - rest_hr)))
    minutes = (a.moving_s or a.duration_s) / 60
    return minutes * ratio * 0.64 * math.exp(1.92 * ratio)


def activity_load(a: Activity, rest_hr: float = 48, max_hr: float = 185) -> float:
    if a.training_load is not None:
        return a.training_load
    return trimp(a, rest_hr, max_hr) or 0.0


def daily_loads(acts: list[Activity], start: dt.date, end: dt.date, **kw: float) -> list[tuple[str, float]]:
    by_day: dict[str, float] = defaultdict(float)
    for a in acts:
        by_day[a.date] += activity_load(a, **kw)
    days = (end - start).days + 1
    return [((start + dt.timedelta(days=i)).isoformat(), round(by_day.get((start + dt.timedelta(days=i)).isoformat(), 0.0), 1))
            for i in range(days)]


def ewma_loads(daily: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """Exponentially weighted acute (7 d) and chronic (42 d) load, and their ratio."""
    atl = ctl = 0.0
    out = []
    for i, (day, load) in enumerate(daily):
        if i == 0:
            atl = ctl = load
        atl += (load - atl) / ATL_DAYS
        ctl += (load - ctl) / CTL_DAYS
        out.append({"date": day, "load": load, "atl": round(atl, 1), "ctl": round(ctl, 1),
                    "acwr": round(atl / ctl, 2) if ctl > 0 else None})
    return out


def weekly_volume(acts: list[Activity], mondays: list[dt.date]) -> list[dict[str, Any]]:
    out = []
    for monday in mondays:
        sunday = monday + dt.timedelta(days=6)
        runs = [a for a in acts if a.is_run and monday.isoformat() <= a.date <= sunday.isoformat()]
        longest = max(runs, key=lambda a: a.distance_m or 0, default=None)
        out.append({
            "week": iso_week(monday), "monday": monday.isoformat(),
            "km": round(sum((a.distance_m or 0) for a in runs) / 1000, 1),
            "runs": len(runs),
            "hours": round(sum((a.moving_s or a.duration_s or 0) for a in runs) / 3600, 2),
            "elev_m": round(sum((a.elev_gain_m or 0) for a in runs)),
            "long_run_km": round((longest.distance_m or 0) / 1000, 1) if longest else 0.0,
            "long_run_date": longest.date if longest else None,
        })
    return out


def iso_week(day: dt.date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"
