"""Planned vs executed: match each planned workout to the run on that day and
give a plain verdict: on target, too hard, too easy, missed."""

from __future__ import annotations

import re
from typing import Any

from adapters.models import Activity, PlannedWorkout

EASY_WORDS = re.compile(r"easy|recovery|slow|jog|alergare|shake", re.I)
DIST_TOLERANCE = 0.20
HR_MARGIN = 4


def _flatten(steps: list[dict]) -> list[dict]:
    out = []
    for s in steps:
        if "repeat" in s:
            out.extend(_flatten(s.get("steps") or []))
        else:
            out.append(s)
    return out


def _hr_target(steps: list[dict], kinds: tuple[str, ...]) -> tuple[float, float] | None:
    ranges = [(s["target_low"], s["target_high"]) for s in _flatten(steps)
              if s.get("target_type") == "heart.rate.zone" and s.get("kind") in kinds
              and s.get("target_low") and s.get("target_high")]
    if not ranges:
        return None
    return min(r[0] for r in ranges), max(r[1] for r in ranges)


def _pace_target(steps: list[dict]) -> tuple[float, float] | None:
    """Fastest 'interval' pace target as a speed range (m/s)."""
    ranges = [(s["target_low"], s["target_high"]) for s in _flatten(steps)
              if s.get("target_type") == "pace.zone" and s.get("kind") == "interval"
              and s.get("target_low") and s.get("target_high")]
    return max(ranges, key=lambda r: r[1]) if ranges else None


def compare(planned: PlannedWorkout, runs: list[Activity], today: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"date": planned.date, "planned": planned.title,
                           "planned_km": round(planned.est_distance_m / 1000, 1) if planned.est_distance_m else None}
    if not runs:
        if today and planned.date >= today:
            return {**out, "verdict": "upcoming", "why": "not done yet"}
        return {**out, "verdict": "missed", "why": "no run recorded that day"}
    run = max(runs, key=lambda a: a.distance_m or 0)
    km = (run.distance_m or 0) / 1000
    out.update({"executed": run.name, "activity_id": run.source_id, "km": round(km, 1),
                "avg_hr": run.avg_hr, "max_hr": run.max_hr})
    reasons, verdict = [], "on target"

    easy = bool(EASY_WORDS.search(planned.title or "")) and not re.search(r"\d+/\d+|interval|fartlek", planned.title or "", re.I)
    hr_t = _hr_target(planned.steps, ("interval", "active", "run", "other") if easy else ("interval",))
    if easy:
        hr_t = hr_t or _hr_target(planned.steps, ("interval", "active", "warmup", "cooldown", "run"))
        if hr_t and run.avg_hr and run.avg_hr > hr_t[1] + HR_MARGIN:
            verdict = "too hard"
            reasons.append(f"avg HR {run.avg_hr:.0f} above the {hr_t[0]:.0f}–{hr_t[1]:.0f} target")
    else:
        work = [l for l in run.laps if (l.intensity or "").upper() in ("INTERVAL", "ACTIVE") and l.avg_speed]
        pace_t = _pace_target(planned.steps)
        if pace_t and work:
            fastest = sorted(work, key=lambda l: l.avg_speed or 0, reverse=True)[: max(1, len(work) // 2)]
            v = sum(l.avg_speed for l in fastest) / len(fastest)
            if v > pace_t[1] * 1.03:
                verdict = "too hard"
                reasons.append("work laps faster than the target pace range")
            elif v < pace_t[0] * 0.97:
                verdict = "too easy"
                reasons.append("work laps slower than the target pace range")
        elif hr_t and run.max_hr:
            if run.max_hr < hr_t[0] - HR_MARGIN:
                verdict = "too easy"
                reasons.append(f"max HR {run.max_hr:.0f} never reached the {hr_t[0]:.0f}+ target")
            elif run.avg_hr and run.avg_hr > hr_t[1]:
                verdict = "too hard"
                reasons.append(f"avg HR {run.avg_hr:.0f} above the whole-session target")

    if planned.est_distance_m:
        dev = (km * 1000 - planned.est_distance_m) / planned.est_distance_m
        if abs(dev) > DIST_TOLERANCE:
            reasons.append(f"{'longer' if dev > 0 else 'shorter'} than planned ({dev:+.0%})")
            if verdict == "on target" and dev > DIST_TOLERANCE * 1.5:
                verdict = "too hard"
            elif verdict == "on target" and dev < -DIST_TOLERANCE * 1.5:
                verdict = "too easy"
    out.update({"verdict": verdict, "why": "; ".join(reasons) or "within targets"})
    return out
