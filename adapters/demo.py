"""Demo adapter: a deterministic, realistic fake athlete for `--demo` mode.

Ten weeks of base-phase training ending today, with HR streams, laps, strides,
an LT1 session, long runs, a couple of doubles, a strength session, sleep/HRV,
and planned workouts. The last week deliberately contains an HRV dip, one
night without HRV, and some grey-zone easy runs so every report section has
something to show.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Iterator

from adapters.base import SourceAdapter
from adapters.models import (
    Activity, DailyWellness, FitnessDaily, Lap, PlannedWorkout, Profile, StrengthSet, Stream,
)

WEEKS = 10
EASY_SPEED = 1000 / 320          # 5:20/km
STEADY_SPEED = 1000 / 262        # 4:22/km
STRIDE_SPEED = 1000 / 190        # 3:10/km

# weekday -> (session kind, base km); Monday = 0
TEMPLATE = {
    0: ("easy", 8),
    1: ("strides", 10),
    2: ("easy", 10),
    3: ("lt1", 11),
    4: ("easy", 7),
    5: ("strides", 9),
    6: ("long", 18),
}


class DemoAdapter(SourceAdapter):
    name = "demo"

    def __init__(self, today: dt.date | None = None):
        self.today = today or dt.date.today()
        self.first = self.today - dt.timedelta(days=WEEKS * 7 - 1)

    def _rng(self, day: dt.date, salt: int = 0) -> random.Random:
        return random.Random(day.toordinal() * 31 + salt)

    def _week_scale(self, day: dt.date) -> float:
        week_idx = (day - self.first).days // 7          # 0 .. WEEKS-1
        step_back = week_idx in (3, 7)                    # recovery weeks
        return (0.85 + 0.03 * week_idx) * (0.8 if step_back else 1.0)

    def fetch_profile(self) -> Profile:
        return Profile(name="Demo Runner", max_hr=192, lt_hr=177, hr_zone_floors=[96, 115, 152, 166, 177])

    # --- activities ------------------------------------------------------

    def fetch_activities(self, start: dt.date, end: dt.date) -> Iterator[Activity]:
        day = max(start, self.first)
        while day <= min(end, self.today):
            yield from self._day_activities(day)
            day += dt.timedelta(days=1)

    def _day_activities(self, day: dt.date) -> list[Activity]:
        rng = self._rng(day)
        kind, km = TEMPLATE[day.weekday()]
        if day.weekday() == 4 and rng.random() < 0.35:
            return []                                     # occasional rest day
        last_week = (self.today - day).days < 7
        km = round(km * self._week_scale(day) * rng.uniform(0.95, 1.05), 1)
        out = [self._run(day, 1, kind, km, rng, grey=last_week and kind == "easy")]
        if day.weekday() == 2 and rng.random() < 0.6:     # double on Wednesdays
            out.append(self._run(day, 2, "easy", round(rng.uniform(5, 7), 1), rng))
        if day.weekday() == 0:
            out.append(self._strength(day))
        return out

    def _run(self, day: dt.date, n: int, kind: str, km: float, rng: random.Random,
             grey: bool = False) -> Activity:
        hour = 7 if n == 1 else 18
        dist = km * 1000
        t, hr, speed = [], [], []
        elapsed, covered = 0.0, 0.0
        base_hr = 141 + (6 if grey else 0)
        lt1_from, lt1_to = (0.45, 0.75) if kind == "lt1" else (2, 2)
        stride_from = dist - 1600 if kind == "strides" else dist + 1
        stride_on = False
        while covered < dist:
            frac = covered / dist
            in_strides = covered >= stride_from
            if in_strides:
                # ~20 s strides (100 m) with ~60 s jog (200 m)
                stride_on = (covered - stride_from) % 300 < 100
            if lt1_from <= frac < lt1_to:
                v, h = STEADY_SPEED, 158 + rng.uniform(-2, 2)
            elif in_strides and stride_on:
                v, h = STRIDE_SPEED, 168 + rng.uniform(-3, 4)
            else:
                drift = 8 * frac if kind == "long" else 3 * frac
                v = EASY_SPEED * rng.uniform(0.97, 1.03)
                h = base_hr + drift + rng.uniform(-3, 3) + (rng.random() < 0.15) * (12 if grey else 0)
            t.append(elapsed)
            hr.append(round(h))
            speed.append(round(v, 3))
            elapsed += 10
            covered += v * 10

        laps = self._laps(t, hr, speed)
        duration = elapsed
        z = self._zone_secs(hr)
        avg_hr = sum(hr) / len(hr)
        return Activity(
            source="demo", source_id=f"{day.isoformat()}-{n}",
            start_local=f"{day.isoformat()}T{hour:02d}:00:00", date=day.isoformat(),
            sport="running", name={"long": "Long run", "lt1": "LT1 steady", "strides": "Easy + strides"}
            .get(kind, "Easy run") + (" (double)" if n == 2 else ""),
            distance_m=round(covered), duration_s=duration, moving_s=duration,
            elev_gain_m=round(km * rng.uniform(3, 6)), avg_hr=round(avg_hr), max_hr=max(hr),
            avg_speed=covered / duration, cadence=round(rng.uniform(170, 176)),
            avg_power=round(300 + rng.uniform(-15, 25)),
            training_load=round(duration / 60 * (1.25 + (avg_hr - 135) / 40), 1),
            aerobic_te=round(min(5.0, 2.4 + duration / 3600 + (0.6 if kind == "lt1" else 0)), 1),
            anaerobic_te=0.6 if kind == "strides" else 0.0,
            te_label="TEMPO" if kind == "lt1" else "AEROBIC_BASE", vo2max=55.0,
            zone_secs=z, zone_floors=[96, 115, 152, 166, 177],
            laps=laps, stream=Stream(t_s=t, hr=hr, speed=speed),
            extra={"demo_kind": kind},
        )

    @staticmethod
    def _zone_secs(hr: list[float]) -> list[float]:
        floors = [96, 115, 152, 166, 177]
        secs = [0.0] * 5
        for h in hr:
            idx = max((i for i, f in enumerate(floors) if h >= f), default=0)
            secs[idx] += 10
        return secs

    @staticmethod
    def _laps(t: list[float], hr: list[float], speed: list[float]) -> list[Lap]:
        """Auto-lap every km, plus stride segments as their own laps."""
        laps, start, dist, idx = [], 0, 0.0, 1
        for i, v in enumerate(speed):
            dist += v * 10
            fast = v > 4.5
            boundary = dist >= 1000 or (i + 1 < len(speed) and (speed[i + 1] > 4.5) != fast) or i == len(speed) - 1
            if boundary:
                seg_hr = hr[start:i + 1]
                dur = (i + 1 - start) * 10
                laps.append(Lap(index=idx, distance_m=round(dist), duration_s=dur, avg_speed=dist / dur,
                                avg_hr=round(sum(seg_hr) / len(seg_hr)), max_hr=max(seg_hr),
                                intensity="INTERVAL" if fast else "ACTIVE"))
                idx, start, dist = idx + 1, i + 1, 0.0
        return laps

    def _strength(self, day: dt.date) -> Activity:
        sets = [StrengthSet(index=i, exercise=ex, category="LEGS", reps=reps, weight_kg=w)
                for i, (ex, reps, w) in enumerate([("SQUAT", 8, 60), ("SQUAT", 8, 60),
                                                   ("CALF_RAISE", 15, 20), ("CALF_RAISE", 15, 20),
                                                   ("DEADLIFT", 6, 80)])]
        return Activity(source="demo", source_id=f"{day.isoformat()}-s", start_local=f"{day.isoformat()}T19:30:00",
                        date=day.isoformat(), sport="strength_training", name="Strength",
                        duration_s=1800, avg_hr=105, training_load=15, strength_sets=sets)

    # --- daily data --------------------------------------------------------

    def fetch_wellness(self, day: dt.date) -> DailyWellness | None:
        if not (self.first <= day <= self.today):
            return None
        rng = self._rng(day, 1)
        days_left = (self.today - day).days
        dip = days_left < 4                               # HRV dip + higher RHR at the end
        sleep_h = rng.uniform(6.3, 7.2) if dip else rng.uniform(7.0, 8.6)
        hrv = None if days_left == 2 else round(rng.uniform(47, 53) if dip else rng.uniform(58, 70))
        return DailyWellness(
            date=day.isoformat(), sleep_s=round(sleep_h * 3600), deep_s=round(sleep_h * 3600 * 0.16),
            light_s=round(sleep_h * 3600 * 0.55), rem_s=round(sleep_h * 3600 * 0.22),
            awake_s=round(rng.uniform(10, 35) * 60),
            sleep_score=round(rng.uniform(58, 70) if dip else rng.uniform(72, 90)),
            hrv_last_night=hrv, hrv_weekly_avg=60 if not dip else 56,
            hrv_status="UNBALANCED" if dip else "BALANCED", hrv_baseline_low=56, hrv_baseline_high=74,
            rhr=round(rng.uniform(51, 54) if dip else rng.uniform(46, 49)),
            bb_charged=round(rng.uniform(35, 50) if dip else rng.uniform(50, 75)),
            bb_drained=round(rng.uniform(55, 70)), bb_high=round(rng.uniform(60, 95)),
            bb_low=round(rng.uniform(10, 30)), stress_avg=round(rng.uniform(22, 38)),
            readiness_score=round(rng.uniform(30, 48) if dip else rng.uniform(60, 88)),
            readiness_level="LOW" if dip else "HIGH", steps=round(rng.uniform(14000, 24000)),
            respiration_avg=round(rng.uniform(11.5, 13.5), 1), weight_kg=74.3,
        )

    def fetch_fitness(self, day: dt.date) -> FitnessDaily | None:
        if not (self.first <= day <= self.today):
            return None
        w = (day - self.first).days / 7
        return FitnessDaily(date=day.isoformat(), vo2max=round(54.6 + 0.08 * w, 1),
                            training_status="PRODUCTIVE", acute_load=round(600 + 20 * w),
                            chronic_load=round(560 + 12 * w), acwr=round((600 + 20 * w) / (560 + 12 * w), 2),
                            lt_hr=177, lt_speed=1000 / 233, race_pred_marathon_s=10995 - 20 * w,
                            race_pred_half_s=4949 - 9 * w, race_pred_10k_s=2200, race_pred_5k_s=1070,
                            endurance_score=7900 + 20 * w)

    def fetch_planned(self, start: dt.date, end: dt.date) -> list[PlannedWorkout]:
        out = []
        day = max(start, self.first)
        while day <= end:
            kind, km = TEMPLATE[day.weekday()]
            if kind in ("lt1", "long"):
                steps = ([{"kind": "warmup", "for": "15:00", "target": "HR 130–150 bpm"},
                          {"kind": "interval", "description": "LT1", "for": "25:00", "target": "HR 152–162 bpm"},
                          {"kind": "cooldown", "for": "15:00", "target": "HR 130–150 bpm"}]
                         if kind == "lt1" else
                         [{"kind": "interval", "description": "Easy long", "for": f"{km * 1.1:.0f} km",
                           "target": "HR 130–150 bpm"}])
                out.append(PlannedWorkout(source="demo", source_id=f"plan-{day.isoformat()}",
                                          date=day.isoformat(), sport="running",
                                          title="LT1 steady 25'" if kind == "lt1" else "Easy long run",
                                          steps=steps, est_distance_m=km * 1000 * self._week_scale(day)))
            day += dt.timedelta(days=1)
        return out
