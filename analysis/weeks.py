"""Week boundaries and the week headline (vs last week, vs 4-week average)."""

from __future__ import annotations

import datetime as dt
from typing import Any


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def parse_week(label: str) -> dt.date:
    """'2026-W43' -> Monday of that ISO week."""
    year, week = label.upper().split("-W")
    return dt.date.fromisocalendar(int(year), int(week), 1)


def last_complete_monday(today: dt.date) -> dt.date:
    """On Sunday the current week counts as complete (report runs Sunday evening)."""
    this = monday_of(today)
    return this if today.weekday() == 6 else this - dt.timedelta(days=7)


REPORT_HOUR = 19   # the weekly report is due Sunday 19:00 local time


def due_monday(now: dt.datetime) -> dt.date:
    """Monday of the most recent week whose report is due. A week becomes due on
    its Sunday at REPORT_HOUR, so Sunday-morning catch-ups still pick last week."""
    this = monday_of(now.date())
    if now.weekday() == 6 and now.hour >= REPORT_HOUR:
        return this
    return this - dt.timedelta(days=7)


def _delta(cur: float, ref: float | None) -> dict[str, float | None]:
    if ref is None:
        return {"abs": None, "pct": None}
    return {"abs": round(cur - ref, 1), "pct": round((cur - ref) / ref * 100, 1) if ref else None}


def headline(volume: list[dict[str, Any]]) -> dict[str, Any]:
    """`volume` is weekly_volume() output, oldest first; the last entry is this week."""
    this = volume[-1]
    last = volume[-2] if len(volume) >= 2 else None
    prev4 = volume[-5:-1]
    avg4 = {k: round(sum(w[k] for w in prev4) / len(prev4), 1) for k in ("km", "runs", "hours", "elev_m")} \
        if prev4 else None
    out = {k: this[k] for k in ("week", "km", "runs", "hours", "elev_m", "long_run_km")}
    out["vs_last_week"] = {k: _delta(this[k], last[k] if last else None) for k in ("km", "runs", "hours", "elev_m")}
    out["vs_4wk_avg"] = {k: _delta(this[k], avg4[k] if avg4 else None) for k in ("km", "runs", "hours", "elev_m")}
    out["avg_4wk"] = avg4
    out["weeks_in_avg"] = len(prev4)
    return out
