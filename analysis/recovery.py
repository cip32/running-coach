"""Recovery summary and red flags (sleep, HRV vs baseline, resting HR, body
battery, readiness, athlete notes). Missing nights are counted and reported,
never filled in."""

from __future__ import annotations

import statistics
from typing import Any

from adapters.models import DailyWellness


def _vals(ws: list[DailyWellness], attr: str) -> list[float]:
    return [getattr(w, attr) for w in ws if getattr(w, attr) is not None]


def _mean(xs: list[float], nd: int = 1) -> float | None:
    return round(statistics.fmean(xs), nd) if xs else None


def below_baseline_streak(ws: list[DailyWellness]) -> tuple[int, int]:
    """(longest run, run ending on the last day) of nights with HRV below the
    baseline band. A night without HRV breaks neither run nor extends it."""
    longest = current = 0
    for w in ws:
        if w.hrv_last_night is None or w.hrv_baseline_low is None:
            continue
        if w.hrv_last_night < w.hrv_baseline_low:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest, current


def summarize(week: list[DailyWellness], history: list[DailyWellness], notes: list[dict],
              thresholds: dict) -> dict[str, Any]:
    """`week`: the 7 days being reported. `history`: the ~30 days before it."""
    n_days = 7
    sleep_h = [s / 3600 for s in _vals(week, "sleep_s")]
    scores = _vals(week, "sleep_score")
    hrv = _vals(week, "hrv_last_night")
    rhr = _vals(week, "rhr")
    rhr_hist = _vals(history, "rhr")
    longest, trailing = below_baseline_streak(history[-7:] + week)
    last = next((w for w in reversed(week) if w.hrv_baseline_low is not None), None)

    missing = []
    for label, xs in (("sleep", sleep_h), ("HRV", hrv), ("resting HR", rhr),
                      ("readiness", _vals(week, "readiness_score"))):
        if len(xs) < n_days:
            missing.append(f"{label}: {n_days - len(xs)} of {n_days} days missing")

    out: dict[str, Any] = {
        "sleep": {
            "avg_h": _mean(sleep_h, 2), "sd_h": round(statistics.pstdev(sleep_h), 2) if len(sleep_h) > 1 else None,
            "min_h": round(min(sleep_h), 2) if sleep_h else None,
            "avg_score": _mean(scores), "nights": len(sleep_h),
            "short_nights": sum(1 for h in sleep_h if h < float(thresholds.get("sleep_min_hours", 7))),
        },
        "hrv": {
            "avg": _mean(hrv), "nights": len(hrv),
            "baseline_low": last.hrv_baseline_low if last else None,
            "baseline_high": last.hrv_baseline_high if last else None,
            "weekly_avg_garmin": last.hrv_weekly_avg if last else None,
            "status_last": next((w.hrv_status for w in reversed(week) if w.hrv_status), None),
            "nights_below_baseline": sum(1 for w in week if w.hrv_last_night is not None
                                         and w.hrv_baseline_low is not None and w.hrv_last_night < w.hrv_baseline_low),
            "longest_streak_below": longest, "current_streak_below": trailing,
        },
        "rhr": {
            "avg": _mean(rhr), "baseline_30d": _mean(rhr_hist),
            "delta": round(_mean(rhr) - _mean(rhr_hist), 1) if rhr and rhr_hist else None,
            # a late-week rise is diluted in the weekly mean, so flag on the last 3 days
            "last_3d": _mean(rhr[-3:]),
            "last_3d_delta": round(_mean(rhr[-3:]) - _mean(rhr_hist), 1) if rhr and rhr_hist else None,
        },
        "body_battery": {"avg_high": _mean(_vals(week, "bb_high")), "avg_low": _mean(_vals(week, "bb_low")),
                         "avg_charged": _mean(_vals(week, "bb_charged"))},
        "readiness": {"avg": _mean(_vals(week, "readiness_score")),
                      "low_days": sum(1 for w in week if (w.readiness_score or 100) < 40 and w.readiness_score is not None)},
        "stress_avg": _mean(_vals(week, "stress_avg")),
        "missing": missing,
    }
    out["red_flags"] = red_flags(out, notes, thresholds)
    return out


def red_flags(s: dict[str, Any], notes: list[dict], t: dict) -> list[str]:
    flags = []
    days = int(t.get("hrv_days_below_baseline", 3))
    if s["hrv"]["current_streak_below"] >= days:
        flags.append(f"HRV below baseline {s['hrv']['current_streak_below']} nights in a row (rule: {days}+)")
    elif s["hrv"]["longest_streak_below"] >= days:
        flags.append(f"HRV was below baseline {s['hrv']['longest_streak_below']} nights in a row this week")
    sl = s["sleep"]
    if sl["avg_h"] is not None and sl["avg_h"] < float(t.get("sleep_min_hours", 7)):
        flags.append(f"Average sleep {sl['avg_h']} h is below {t.get('sleep_min_hours', 7)} h")
    if sl["avg_score"] is not None and sl["avg_score"] < float(t.get("sleep_score_poor", 60)):
        flags.append(f"Average sleep score {sl['avg_score']} is poor")
    if (s["rhr"]["last_3d_delta"] or 0) >= float(t.get("rhr_elevated_bpm", 5)):
        flags.append(f"Resting HR up {s['rhr']['last_3d_delta']} bpm over the last 3 days vs the 30-day norm")
    words = [w.lower() for w in t.get("pain_keywords", [])]
    for n in notes:
        hit = next((w for w in words if w in n["text"].lower()), None)
        if hit:
            flags.append(f"Note on {n['date']} mentions '{hit}': \"{n['text']}\"")
    return flags
