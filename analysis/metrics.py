"""Assemble everything the weekly report needs into one JSON-serialisable dict.

All numbers are computed here in plain Python; the LLM only interprets them.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from typing import Any

from adapters.models import Activity
from analysis import classify as cls
from analysis import context, load, plan_compare, recovery, weeks, zones
from db.store import Store

HISTORY_WEEKS = 8


def pace_str(speed: float | None) -> str | None:
    if not speed:
        return None
    s = 1000 / speed
    return f"{int(s // 60)}:{int(round(s % 60)):02d}"


def _r(x: float | None, nd: int = 1) -> float | None:
    return round(x, nd) if x is not None else None


def session_rows(acts: list[Activity], planned_by_day: dict[str, list], goals: dict) -> list[dict[str, Any]]:
    floors = zones.bucket_floors(goals)
    rules = goals.get("classification") or {}
    runs = [a for a in acts if a.is_run]
    rows, nth = [], defaultdict(int)
    longest_by_day: dict[str, Activity] = {}
    for a in runs:
        if a.date not in longest_by_day or (a.distance_m or 0) > (longest_by_day[a.date].distance_m or 0):
            longest_by_day[a.date] = a
    for a in runs:
        nth[a.date] += 1
        tib = zones.time_in_buckets(a, floors)
        share = zones.shares(tib["secs"])
        plan = planned_by_day.get(a.date) or []
        title = plan[0].title if plan and longest_by_day[a.date] is a else None
        c = cls.classify(a, share, rules, planned_title=title, run_of_day=nth[a.date])
        rows.append({
            "date": a.date, "start": a.start_local[11:16], "name": a.name, "sport": a.sport,
            "id": a.source_id, "planned": title,
            "km": _r((a.distance_m or 0) / 1000, 2), "minutes": _r((a.moving_s or a.duration_s or 0) / 60),
            "pace": pace_str(a.avg_speed), "avg_hr": a.avg_hr, "max_hr": a.max_hr,
            "elev_m": a.elev_gain_m, "cadence": _r(a.cadence, 0), "power": a.avg_power,
            "load": _r(a.training_load, 0), "aerobic_te": a.aerobic_te, "te_label": a.te_label,
            **c,
            "zone_minutes": {b: round(s / 60, 1) for b, s in tib["secs"].items()},
            "zone_share": {b: round(v, 3) for b, v in share.items()},
            "zone_source": tib["source"],
            "_secs": tib["secs"],
        })
    return rows


def build_week(store: Store, goals: dict, monday: dt.date, today: dt.date | None = None) -> dict[str, Any]:
    sunday = monday + dt.timedelta(days=6)
    today_s = (today or dt.date.today()).isoformat()
    m0 = monday - dt.timedelta(weeks=HISTORY_WEEKS - 1)
    hist_start = monday - dt.timedelta(days=60)
    acts_hist = store.activities(hist_start.isoformat(), sunday.isoformat())
    week_acts = [a for a in store.activities(monday.isoformat(), sunday.isoformat(), with_streams=True)]
    missing: list[str] = []

    planned = store.planned(monday.isoformat(), sunday.isoformat())
    planned_by_day: dict[str, list] = defaultdict(list)
    for p in planned:
        if (p.sport or "running") == "running":
            planned_by_day[p.date].append(p)

    sessions = session_rows(week_acts, planned_by_day, goals)
    if any(s["zone_source"] == "zones" for s in sessions):
        missing.append("some runs had no HR stream; zone times use Garmin's zones (approximate)")
    if any(s["zone_source"] is None for s in sessions):
        missing.append("some runs had no HR data at all")

    other = [{"date": a.date, "sport": a.sport, "name": a.name,
              "minutes": _r((a.duration_s or 0) / 60), "sets": len(a.strength_sets),
              "exercises": sorted({s.exercise for s in a.strength_sets if s.exercise and s.exercise != "UNKNOWN"})}
             for a in week_acts if not a.is_run]

    # --- intensity distribution
    dist = zones.distribution([s["_secs"] for s in sessions])
    grey = [{"date": s["date"], "name": s["name"], "steady_share": s["zone_share"]["steady"]}
            for s in sessions if s["grey_zone"]]
    by_type: dict[str, float] = defaultdict(float)
    for s in sessions:
        by_type[s["type"]] += s["km"] or 0
    for s in sessions:
        s.pop("_secs")

    # --- key workouts: every planned non-easy session, plus long runs
    key = []
    runs_by_day: dict[str, list[Activity]] = defaultdict(list)
    for a in week_acts:
        if a.is_run:
            runs_by_day[a.date].append(a)
    for day, plans in sorted(planned_by_day.items()):
        for p in plans:
            res = plan_compare.compare(p, runs_by_day.get(day, []), today=today_s)
            res["key"] = not plan_compare.EASY_WORDS.search(p.title or "") or bool(cls.HARD_WORDS.search(p.title or ""))
            key.append(res)
    unplanned_quality = [s for s in sessions if s["type"] in ("hard", "LT", "LT1", "long") and not s["planned"]]

    # --- load
    mondays = [m0 + dt.timedelta(weeks=i) for i in range(HISTORY_WEEKS)]
    volume = load.weekly_volume(acts_hist, mondays)
    wellness_hist = store.wellness((monday - dt.timedelta(days=37)).isoformat(), (monday - dt.timedelta(days=1)).isoformat())
    week_wellness = store.wellness(monday.isoformat(), sunday.isoformat())
    rest_hr = statistics.fmean([w.rhr for w in wellness_hist + week_wellness if w.rhr]) \
        if any(w.rhr for w in wellness_hist + week_wellness) else 48.0
    max_hr = float((goals.get("zones") or {}).get("max_hr") or 185)
    daily = load.daily_loads(acts_hist, hist_start, sunday, rest_hr=rest_hr, max_hr=max_hr)
    ewma = load.ewma_loads(daily)
    first_act = min((a.date for a in acts_hist), default=None)
    if first_act is None or first_act > (sunday - dt.timedelta(days=load.CTL_DAYS)).isoformat():
        missing.append("less than 6 weeks of activity history: the computed load ratio (ACWR) is unreliable; "
                       "use Garmin's acute/chronic values instead")
    fitness = store.fitness(monday.isoformat(), sunday.isoformat())
    latest_fit = next((f for f in reversed(fitness) if f.acute_load is not None), None)
    latest_pred = next((f for f in reversed(fitness) if f.race_pred_marathon_s), None)
    latest_vo2 = next((f.vo2max for f in reversed(fitness) if f.vo2max), None)
    if not fitness:
        missing.append("no Garmin fitness/training-status data this week")

    long_runs = [{"week": w["week"], "km": w["long_run_km"], "date": w["long_run_date"]} for w in volume]
    this_long = next((s for s in sessions if s["type"] == "long"), None)

    # --- recovery
    notes = store.notes(monday.isoformat(), sunday.isoformat())
    rec = recovery.summarize(week_wellness, wellness_hist, notes, goals.get("recovery_thresholds") or {})
    missing += rec["missing"]

    km_by_day: dict[str, float] = defaultdict(float)
    for s in sessions:
        km_by_day[s["date"]] += s["km"] or 0
    wmap = {w.date: w for w in week_wellness}
    daily_rows = []
    for i in range(7):
        d = (monday + dt.timedelta(days=i)).isoformat()
        w = wmap.get(d)
        daily_rows.append({
            "date": d, "weekday": (monday + dt.timedelta(days=i)).strftime("%a"),
            "km": round(km_by_day.get(d, 0), 1),
            "sleep_h": _r(w.sleep_s / 3600, 2) if w and w.sleep_s else None,
            "sleep_score": w.sleep_score if w else None,
            "readiness": w.readiness_score if w else None,
            "hrv": w.hrv_last_night if w else None,
            "hrv_low": w.hrv_baseline_low if w else None, "hrv_high": w.hrv_baseline_high if w else None,
            "rhr": w.rhr if w else None, "bb_high": w.bb_high if w else None,
        })

    head = weeks.headline(volume)
    next_monday = sunday + dt.timedelta(days=1)
    next_plan = [{"date": p.date, "title": p.title, "km": _r((p.est_distance_m or 0) / 1000),
                  "steps": p.steps} for p in store.planned(next_monday.isoformat(),
                                                           (next_monday + dt.timedelta(days=6)).isoformat())]
    return {
        "week": load.iso_week(monday), "monday": monday.isoformat(), "sunday": sunday.isoformat(),
        "headline": head,
        "daily": daily_rows,
        "sessions": sessions,
        "other_activities": other,
        "key_workouts": key,
        "unplanned_quality": [{"date": s["date"], "name": s["name"], "type": s["type"]} for s in unplanned_quality],
        "intensity": {**dist, "grey_zone_runs": grey, "km_by_type": {k: round(v, 1) for k, v in by_type.items()}},
        "load": {
            "weekly_volume": volume,
            "ewma": [e for e in ewma if e["date"] >= (sunday - dt.timedelta(days=55)).isoformat()],
            "acwr_now": ewma[-1]["acwr"] if ewma else None,
            "atl_now": ewma[-1]["atl"] if ewma else None, "ctl_now": ewma[-1]["ctl"] if ewma else None,
            "garmin": {
                "training_status": latest_fit.training_status if latest_fit else None,
                "acute": latest_fit.acute_load if latest_fit else None,
                "chronic": latest_fit.chronic_load if latest_fit else None,
                "acwr": latest_fit.acwr if latest_fit else None,
                "vo2max": latest_vo2,
                "lt_hr": next((f.lt_hr for f in reversed(fitness) if f.lt_hr), None),
                "lt_pace": pace_str(next((f.lt_speed for f in reversed(fitness) if f.lt_speed), None)),
                "pred_marathon_s": latest_pred.race_pred_marathon_s if latest_pred else None,
                "pred_half_s": latest_pred.race_pred_half_s if latest_pred else None,
            },
            "long_runs": long_runs,
            "this_week_long_run": {"km": this_long["km"], "hr_drift_pct": this_long["hr_drift_pct"],
                                   "pace": this_long["pace"]} if this_long else None,
        },
        "recovery": rec,
        "context": context.build(goals, monday, head["km"]),
        "next_week_planned": next_plan,
        "notes": notes,
        "missing": missing,
    }


def digest(store: Store, goals: dict, today: dt.date, weeks_back: int = HISTORY_WEEKS) -> dict[str, Any]:
    """Compact 8-week summary for chat mode."""
    monday = weeks.monday_of(today)
    mondays = [monday - dt.timedelta(weeks=i) for i in reversed(range(weeks_back))]
    acts = store.activities(mondays[0].isoformat(), today.isoformat())
    vol = load.weekly_volume(acts, mondays)
    well = store.wellness((today - dt.timedelta(days=13)).isoformat(), today.isoformat())
    recent_runs = [{"date": a.date, "name": a.name, "km": _r((a.distance_m or 0) / 1000), "pace": pace_str(a.avg_speed),
                    "avg_hr": a.avg_hr, "load": _r(a.training_load, 0)}
                   for a in acts if a.is_run and a.date >= (today - dt.timedelta(days=13)).isoformat()]
    return {
        "today": today.isoformat(),
        "weekly_volume": vol,
        "last_14_days_runs": recent_runs,
        "last_14_days_wellness": [{"date": w.date, "sleep_h": _r((w.sleep_s or 0) / 3600, 1) if w.sleep_s else None,
                                   "sleep_score": w.sleep_score, "hrv": w.hrv_last_night,
                                   "hrv_band": [w.hrv_baseline_low, w.hrv_baseline_high], "rhr": w.rhr,
                                   "readiness": w.readiness_score} for w in well],
        "planned_next_14_days": [{"date": p.date, "title": p.title} for p in
                                 store.planned(today.isoformat(), (today + dt.timedelta(days=14)).isoformat())],
        "context": context.build(goals, monday, vol[-1]["km"] if vol else 0),
    }
