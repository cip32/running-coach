"""Long-term context: phase, weeks to the A race, ramp towards target volume."""

from __future__ import annotations

import datetime as dt
from typing import Any


def current_phase(goals: dict, day: dt.date) -> dict[str, Any] | None:
    for p in goals.get("phases") or []:
        if p["start"] <= day <= p["end"]:
            total = (p["end"] - p["start"]).days // 7 + 1
            week_no = (day - p["start"]).days // 7 + 1
            return {"name": p["name"], "start": p["start"].isoformat(), "end": p["end"].isoformat(),
                    "week": week_no, "weeks_total": total, "focus": p.get("focus"),
                    "structure": p.get("structure")}
    return None


def a_race(goals: dict, day: dt.date) -> dict[str, Any] | None:
    races = sorted((r for r in goals.get("races") or [] if r["date"] >= day),
                   key=lambda r: (r.get("priority", "C"), r["date"]))
    if not races:
        return None
    r = races[0]
    return {"name": r["name"], "date": r["date"].isoformat(), "priority": r.get("priority"),
            "goal_time": str(r.get("goal_time")) if r.get("goal_time") else None,
            "days_to_go": (r["date"] - day).days, "weeks_to_go": round((r["date"] - day).days / 7, 1)}


def target_km(goals: dict, monday: dt.date) -> float | None:
    """Linear easy ramp from the middle of current_weekly_km at the first phase
    start to target_weekly_km at target_by; flat afterwards."""
    vol = goals.get("volume") or {}
    target, by = vol.get("target_weekly_km"), vol.get("target_by")
    cur = vol.get("current_weekly_km")
    phases = goals.get("phases") or []
    if not (target and by and cur and phases):
        return None
    start_km = sum(cur) / len(cur) if isinstance(cur, list) else float(cur)
    start = phases[0]["start"]
    if monday <= start:
        return round(start_km, 1)
    if monday >= by:
        return float(target)
    frac = (monday - start).days / (by - start).days
    return round(start_km + frac * (target - start_km), 1)


def build(goals: dict, monday: dt.date, actual_km: float) -> dict[str, Any]:
    sunday = monday + dt.timedelta(days=6)
    tgt = target_km(goals, monday)
    vol = goals.get("volume") or {}
    return {
        "phase": current_phase(goals, sunday),
        "a_race": a_race(goals, sunday),
        "volume": {
            "this_week_km": actual_km, "ramp_target_km": tgt,
            "vs_ramp_km": round(actual_km - tgt, 1) if tgt is not None else None,
            "goal_km": vol.get("target_weekly_km"),
            "goal_by": vol["target_by"].isoformat() if vol.get("target_by") else None,
            "pct_of_goal": round(actual_km / vol["target_weekly_km"] * 100) if vol.get("target_weekly_km") else None,
            "weeks_to_goal_date": round((vol["target_by"] - sunday).days / 7, 1) if vol.get("target_by") else None,
        },
    }
