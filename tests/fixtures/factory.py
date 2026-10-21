"""Small hand-built fixtures for metric tests."""

from __future__ import annotations

import datetime as dt

from adapters.models import Activity, DailyWellness, Lap, PlannedWorkout, Stream

GOALS = {
    "zones": {"max_hr": 185, "hr_buckets": {"easy": 0, "steady": 152, "threshold": 166, "hard": 178}},
    "classification": {"long_run_min_km": 24, "long_run_min_minutes": 105, "strides_max_lap_s": 40,
                       "strides_min_speed_vs_avg": 1.25, "lt_min_share": 0.20, "hard_min_share": 0.08,
                       "grey_zone_max_steady_share": 0.15},
    "recovery_thresholds": {"hrv_days_below_baseline": 3, "sleep_min_hours": 7.0, "sleep_score_poor": 60,
                            "rhr_elevated_bpm": 5, "pain_keywords": ["pain", "sore", "tight"]},
    "volume": {"current_weekly_km": [60, 70], "target_weekly_km": 100, "target_by": dt.date(2026, 12, 28)},
    "phases": [{"name": "base", "start": dt.date(2026, 10, 19), "end": dt.date(2026, 12, 27)},
               {"name": "marathon_block", "start": dt.date(2026, 12, 28), "end": dt.date(2027, 4, 18)}],
    "races": [{"name": "Vienna Marathon", "date": dt.date(2027, 4, 18), "priority": "A", "goal_time": "2:45:00"}],
}


def stream(segments: list[tuple[int, float, float]], step: int = 10) -> Stream:
    """segments: (seconds, hr, speed) blocks laid end to end."""
    st, t = Stream(), 0
    for secs, hr, speed in segments:
        for _ in range(secs // step):
            st.t_s.append(t)
            st.hr.append(hr)
            st.speed.append(speed)
            t += step
    return st


def run(date: str, km: float, segments: list[tuple[int, float, float]] | None = None, n: int = 1,
        **kw) -> Activity:
    segs = segments or [(int(km * 1000 / 3.1), 140, 3.1)]
    st = stream(segs)
    dur = st.t_s[-1] + 10 if st.t_s else 0
    base = dict(source="t", source_id=f"{date}-{n}", start_local=f"{date}T0{6 + n}:00:00", date=date,
                sport="running", name=f"run {date}", distance_m=km * 1000, duration_s=dur, moving_s=dur,
                avg_hr=sum(st.hr) / len(st.hr), max_hr=max(st.hr), avg_speed=km * 1000 / dur,
                training_load=dur / 60, stream=st, laps=[Lap(index=1, distance_m=km * 1000, duration_s=dur)])
    base.update(kw)
    return Activity(**base)


def wellness(date: str, hrv: float | None = 62, low: float = 56, high: float = 74, sleep_h: float = 8,
             score: float = 80, rhr: float = 48, readiness: float = 70) -> DailyWellness:
    return DailyWellness(date=date, sleep_s=sleep_h * 3600, sleep_score=score, hrv_last_night=hrv,
                         hrv_baseline_low=low, hrv_baseline_high=high, rhr=rhr, readiness_score=readiness,
                         bb_high=90, bb_low=20)


def planned(date: str, title: str, steps: list[dict] | None = None, km: float | None = None) -> PlannedWorkout:
    return PlannedWorkout(source="t", source_id=f"p-{date}", date=date, title=title, sport="running",
                          steps=steps or [], est_distance_m=km * 1000 if km else None)


def days(start: str, n: int) -> list[str]:
    d = dt.date.fromisoformat(start)
    return [(d + dt.timedelta(days=i)).isoformat() for i in range(n)]
