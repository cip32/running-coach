"""Common data model. Every adapter converts its source into these types.

Units are SI throughout: metres, seconds, m/s, bpm. Dates are ISO strings
(YYYY-MM-DD) and local start times are ISO datetimes in the athlete's timezone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Lap:
    index: int
    distance_m: float | None = None
    duration_s: float | None = None
    avg_speed: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    cadence: float | None = None
    avg_power: float | None = None
    elev_gain_m: float | None = None
    intensity: str | None = None        # ACTIVE / REST / WARMUP / COOLDOWN / INTERVAL ...


@dataclass
class StrengthSet:
    index: int
    exercise: str | None = None
    category: str | None = None
    reps: int | None = None
    weight_kg: float | None = None
    duration_s: float | None = None


@dataclass
class Stream:
    """Per-sample series for an activity (seconds from start)."""
    t_s: list[float] = field(default_factory=list)
    hr: list[float | None] = field(default_factory=list)
    speed: list[float | None] = field(default_factory=list)


@dataclass
class Activity:
    source: str
    source_id: str
    start_local: str                     # ISO datetime, athlete timezone
    date: str                            # YYYY-MM-DD local
    sport: str                           # running, trail_running, strength_training, cycling ...
    name: str | None = None
    distance_m: float | None = None
    duration_s: float | None = None
    moving_s: float | None = None
    elev_gain_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_speed: float | None = None
    cadence: float | None = None
    avg_power: float | None = None
    training_load: float | None = None
    aerobic_te: float | None = None
    anaerobic_te: float | None = None
    te_label: str | None = None
    vo2max: float | None = None
    calories: float | None = None
    zone_secs: list[float] | None = None  # source's own HR zones (Garmin: 5 zones)
    zone_floors: list[float] | None = None
    laps: list[Lap] = field(default_factory=list)
    strength_sets: list[StrengthSet] = field(default_factory=list)
    stream: Stream | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # running dynamics, weather...

    @property
    def is_run(self) -> bool:
        return "running" in (self.sport or "") or self.sport in ("treadmill", "track")


@dataclass
class DailyWellness:
    date: str
    sleep_s: float | None = None
    deep_s: float | None = None
    light_s: float | None = None
    rem_s: float | None = None
    awake_s: float | None = None
    sleep_score: float | None = None
    hrv_last_night: float | None = None
    hrv_weekly_avg: float | None = None
    hrv_status: str | None = None
    hrv_baseline_low: float | None = None
    hrv_baseline_high: float | None = None
    rhr: float | None = None
    bb_charged: float | None = None
    bb_drained: float | None = None
    bb_high: float | None = None
    bb_low: float | None = None
    stress_avg: float | None = None
    readiness_score: float | None = None
    readiness_level: str | None = None
    steps: int | None = None
    respiration_avg: float | None = None
    weight_kg: float | None = None


@dataclass
class FitnessDaily:
    date: str
    vo2max: float | None = None
    training_status: str | None = None
    acute_load: float | None = None
    chronic_load: float | None = None
    acwr: float | None = None
    lt_hr: float | None = None
    lt_speed: float | None = None        # m/s
    race_pred_5k_s: float | None = None
    race_pred_10k_s: float | None = None
    race_pred_half_s: float | None = None
    race_pred_marathon_s: float | None = None
    endurance_score: float | None = None


@dataclass
class PlannedWorkout:
    source: str
    source_id: str
    date: str
    title: str | None = None
    sport: str | None = None
    workout_id: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    est_duration_s: float | None = None
    est_distance_m: float | None = None


@dataclass
class Race:
    name: str
    date: str
    priority: str = "C"
    distance_km: float | None = None
    goal_time: str | None = None
    source: str = "goals"


@dataclass
class Profile:
    """Athlete facts a source knows about (zones, thresholds, devices)."""
    name: str | None = None
    max_hr: float | None = None
    lt_hr: float | None = None
    hr_zone_floors: list[float] | None = None
    raw: dict[str, Any] = field(default_factory=dict)
