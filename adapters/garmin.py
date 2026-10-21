"""Garmin Connect adapter (unofficial API via python-garminconnect).

Pulls everything the API exposes into the raw cache, and maps the parts the
coach uses onto the common model. Uses the OAuth tokens written by
`coach login`; the password is never stored.

Caching: every response goes to raw_cache. Activity detail endpoints are
cached forever (activities don't change); per-day and per-month data is
re-fetched only when it falls inside the refresh window.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from garminconnect import Garmin

from adapters.base import SourceAdapter
from adapters.models import (
    Activity, DailyWellness, FitnessDaily, Lap, PlannedWorkout, Profile, Race, StrengthSet, Stream,
)
from db.store import Store

log = logging.getLogger(__name__)
SRC = "garmin"

# activity summary fields kept in Activity.extra (running dynamics etc.)
EXTRA_KEYS = (
    "avgGroundContactTime", "avgStrideLength", "avgVerticalOscillation", "avgVerticalRatio",
    "normPower", "maxPower", "avgGradeAdjustedSpeed", "maxSpeed", "elevationLoss",
    "minTemperature", "maxTemperature", "averageTemperature", "directWorkoutFeel",
    "directWorkoutRpe", "directWorkoutComplianceScore", "beginPotentialStamina",
    "endPotentialStamina", "minAvailableStamina", "differenceBodyBattery", "impactLoad",
    "workoutId", "lapCount", "maxRunningCadenceInStepsPerMinute", "moderateIntensityMinutes",
    "vigorousIntensityMinutes", "waterEstimated", "locationName", "manufacturer",
)

# per-day endpoints stored in the raw cache only ("pull everything")
DAILY_EXTRAS: dict[str, str] = {
    "respiration": "get_respiration_data",
    "spo2": "get_spo2_data",
    "stress": "get_stress_data",
    "intensity_minutes": "get_intensity_minutes_data",
    "hydration": "get_hydration_data",
    "body_battery_events": "get_body_battery_events",
    "rhr": "get_rhr_day",
    "heart_rates": "get_heart_rates",
    "steps": "get_steps_data",
    "morning_readiness": "get_morning_training_readiness",
}

# per-month range endpoints stored in the raw cache only
MONTHLY_EXTRAS: dict[str, Callable[[Garmin, str, str], Any]] = {
    "hill_score": lambda g, s, e: g.get_hill_score(s, e),
    "running_tolerance": lambda g, s, e: g.get_running_tolerance(s, e, "daily"),
    "body_composition": lambda g, s, e: g.get_body_composition(s, e),
    "fitness_age": lambda g, s, e: g.get_fitness_age_daily(s, e),
}


def _iso_local(s: str | None) -> str:
    return (s or "").replace(" ", "T")


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def pace(speed_mps: float | None) -> str | None:
    if not speed_mps:
        return None
    secs = 1000 / speed_mps
    return f"{int(secs // 60)}:{int(secs % 60):02d}/km"


def hms(secs: float | None) -> str | None:
    if secs is None:
        return None
    secs = round(secs)
    h, rem = divmod(secs, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


def _month_bounds(day: dt.date) -> tuple[dt.date, dt.date]:
    first = day.replace(day=1)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    return first, nxt - dt.timedelta(days=1)


def _first_device(dto_map: dict | None) -> dict:
    entries = list((dto_map or {}).values())
    primary = [e for e in entries if isinstance(e, dict) and e.get("primaryTrainingDevice")]
    return (primary or entries or [{}])[0]


def workout_step(s: dict) -> dict:
    """Garmin workout step -> compact dict (shared format with the MCP server)."""
    if s.get("type") == "RepeatGroupDTO":
        return {"repeat": s.get("numberOfIterations"),
                "steps": [workout_step(c) for c in s.get("workoutSteps") or []]}
    end = (s.get("endCondition") or {}).get("conditionTypeKey")
    val = s.get("endConditionValue")
    target = (s.get("targetType") or {}).get("workoutTargetTypeKey")
    lo, hi = s.get("targetValueOne"), s.get("targetValueTwo")
    if target == "pace.zone" and lo and hi:          # Garmin stores pace targets as m/s
        target_str = f"pace {pace(hi)}–{pace(lo)}"
    elif target == "heart.rate.zone" and lo and hi:
        target_str = f"HR {lo:.0f}–{hi:.0f} bpm"
    elif target and target != "no.target":
        target_str = f"{target} {lo}–{hi}"
    else:
        target_str = None
    out = {
        "kind": (s.get("stepType") or {}).get("stepTypeKey"),
        "description": s.get("description"),
        "for": hms(val) if end == "time" else f"{val / 1000:g} km" if end == "distance" and val else end,
        "target": target_str,
        "end_condition": end, "end_value": val,
        "target_type": target, "target_low": lo, "target_high": hi,
    }
    return {k: v for k, v in out.items() if v is not None}


class GarminAdapter(SourceAdapter):
    name = SRC

    def __init__(self, token_dir: Path, store: Store, *, today: dt.date,
                 refresh_days: int = 3, throttle_s: float = 0.4, extras: bool = True,
                 fit_dir: Path | None = None):
        self.token_dir = token_dir
        self.store = store
        self.today = today
        self.refresh_from = today - dt.timedelta(days=refresh_days - 1)
        self.throttle_s = throttle_s
        self.extras = extras
        self.fit_dir = fit_dir
        self._g: Garmin | None = None
        self._races: dict[tuple[str, str], Race] = {}
        self.calls = 0

    # --- plumbing -------------------------------------------------------

    @property
    def g(self) -> Garmin:
        if self._g is None:
            if not self.token_dir.exists():
                raise RuntimeError("No Garmin login found. Run `coach login` first.")
            g = Garmin()
            g.login(str(self.token_dir))
            self._g = g
        return self._g

    def _fetch(self, endpoint: str, key: str, fn: Callable[[], Any], *, fresh: bool = False) -> Any:
        """Cached call. `fresh` forces a re-fetch (recent days)."""
        if not fresh:
            cached = self.store.raw_get(SRC, endpoint, key)
            if cached is not None:
                return cached
        try:
            data = fn()
        except Exception as e:
            # one missing metric must not stop a sync; 429/auth errors still surface
            msg = str(e)
            if "429" in msg or "auth" in msg.lower() or "401" in msg:
                raise
            log.debug("%s %s unavailable: %s", endpoint, key, e)
            data = {"_unavailable": msg[:200]}
        self.calls += 1
        time.sleep(self.throttle_s)
        self.store.raw_put(SRC, endpoint, key, data)
        return data

    def _is_fresh(self, day: dt.date) -> bool:
        return day >= self.refresh_from

    def _monthly(self, endpoint: str, day: dt.date, fn: Callable[[str, str], Any]) -> Any:
        first, last = _month_bounds(day)
        last = min(last, self.today)
        return self._fetch(endpoint, first.strftime("%Y-%m"),
                           lambda: fn(first.isoformat(), last.isoformat()),
                           fresh=last >= self.refresh_from)

    # --- profile & one-offs ---------------------------------------------------

    def fetch_profile(self) -> Profile:
        today = self.today.isoformat()
        zones = self._fetch("hr_zones", today, self.g.get_heart_rate_zones, fresh=True)
        run = next((z for z in zones if isinstance(z, dict) and z.get("sport") == "RUNNING"),
                   zones[0] if isinstance(zones, list) and zones else {})
        for endpoint, fn in {
            "user_profile": self.g.get_user_profile,
            "userprofile_settings": self.g.get_userprofile_settings,
            "personal_information": self.g.get_personal_information,
            "devices": self.g.get_devices,
            "personal_records": self.g.get_personal_record,
            "gear": lambda: self.g.get_gear(self.g.get_user_profile_base().get("userProfileNumber")
                                            if hasattr(self.g, "get_user_profile_base") else None),
            "goals": self.g.get_goals,
            "training_plans": self.g.get_training_plans,
            "power_zones": self.g.get_power_zones,
            "primary_event": self.g.get_primary_event,
            "upcoming_events": self.g.get_upcoming_events,
            "lifetime_totals": self.g.get_lifetime_totals,
        }.items():
            self._fetch(endpoint, today, fn, fresh=True)
        return Profile(
            name=self.g.get_full_name(),
            max_hr=_num(run.get("maxHeartRateUsed")),
            lt_hr=_num(run.get("lactateThresholdHeartRateUsed")),
            hr_zone_floors=[run.get(f"zone{i}Floor") for i in range(1, 6)] if run else None,
            raw={"hr_zones": zones},
        )

    # --- activities -------------------------------------------------------

    def fetch_activities(self, start: dt.date, end: dt.date) -> Iterator[Activity]:
        summaries = self._fetch("activities_by_date", f"{start}_{end}",
                                lambda: self.g.get_activities_by_date(start.isoformat(), end.isoformat()),
                                fresh=True)
        for s in summaries or []:
            yield self._activity(s)

    def _activity(self, s: dict) -> Activity:
        aid = str(s["activityId"])
        sport = (s.get("activityType") or {}).get("typeKey") or "unknown"
        g = self.g
        details = self._fetch("activity_details", aid, lambda: g.get_activity_details(aid, maxchart=4000))
        splits = self._fetch("activity_splits", aid, lambda: g.get_activity_splits(aid))
        zones = self._fetch("activity_hr_zones", aid, lambda: g.get_activity_hr_in_timezones(aid))
        weather = self._fetch("activity_weather", aid, lambda: g.get_activity_weather(aid))
        if self.extras:
            for endpoint, fn in {
                "activity": lambda: g.get_activity(aid),
                "activity_typed_splits": lambda: g.get_activity_typed_splits(aid),
                "activity_split_summaries": lambda: g.get_activity_split_summaries(aid),
                "activity_power_zones": lambda: g.get_activity_power_in_timezones(aid),
                "activity_gear": lambda: g.get_activity_gear(aid),
            }.items():
                self._fetch(endpoint, aid, fn)
        sets: list[StrengthSet] = []
        if "strength" in sport:
            ex = self._fetch("activity_exercise_sets", aid, lambda: g.get_activity_exercise_sets(aid))
            sets = self._strength_sets(ex)
        if self.fit_dir is not None:
            self._download_fit(aid)

        zone_list = zones if isinstance(zones, list) else []
        weather_extra = {}
        if isinstance(weather, dict) and "_unavailable" not in weather:
            weather_extra = {"weather_temp_f": weather.get("temp"), "weather_apparent_f": weather.get("apparentTemp"),
                             "weather_humidity": weather.get("relativeHumidity"),
                             "weather_wind_mph": weather.get("windSpeed"),
                             "weather": (weather.get("weatherTypeDTO") or {}).get("desc")}
        return Activity(
            source=SRC, source_id=aid,
            start_local=_iso_local(s.get("startTimeLocal")),
            date=(s.get("startTimeLocal") or "")[:10],
            sport=sport, name=s.get("activityName"),
            distance_m=_num(s.get("distance")), duration_s=_num(s.get("duration")),
            moving_s=_num(s.get("movingDuration")), elev_gain_m=_num(s.get("elevationGain")),
            avg_hr=_num(s.get("averageHR")), max_hr=_num(s.get("maxHR")),
            avg_speed=_num(s.get("averageSpeed")),
            cadence=_num(s.get("averageRunningCadenceInStepsPerMinute")),
            avg_power=_num(s.get("avgPower")), training_load=_num(s.get("activityTrainingLoad")),
            aerobic_te=_num(s.get("aerobicTrainingEffect")),
            anaerobic_te=_num(s.get("anaerobicTrainingEffect")),
            te_label=s.get("trainingEffectLabel"), vo2max=_num(s.get("vO2MaxValue")),
            calories=_num(s.get("calories")),
            zone_secs=[_num(z.get("secsInZone")) or 0.0 for z in zone_list] or
                      [_num(s.get(f"hrTimeInZone_{i}")) or 0.0 for i in range(1, 6)],
            zone_floors=[_num(z.get("zoneLowBoundary")) for z in zone_list] or None,
            laps=self._laps(splits),
            strength_sets=sets,
            stream=self._stream(details),
            extra={**{k: s[k] for k in EXTRA_KEYS if s.get(k) is not None}, **weather_extra},
        )

    @staticmethod
    def _laps(splits: Any) -> list[Lap]:
        laps = []
        for lap in (splits or {}).get("lapDTOs") or [] if isinstance(splits, dict) else []:
            laps.append(Lap(
                index=lap.get("lapIndex") or len(laps) + 1,
                distance_m=_num(lap.get("distance")), duration_s=_num(lap.get("duration")),
                avg_speed=_num(lap.get("averageSpeed")), avg_hr=_num(lap.get("averageHR")),
                max_hr=_num(lap.get("maxHR")), cadence=_num(lap.get("averageRunCadence")),
                avg_power=_num(lap.get("averagePower")), elev_gain_m=_num(lap.get("elevationGain")),
                intensity=lap.get("intensityType"),
            ))
        return laps

    @staticmethod
    def _stream(details: Any) -> Stream | None:
        if not isinstance(details, dict):
            return None
        idx = {m["key"]: m["metricsIndex"] for m in details.get("metricDescriptors") or []}
        rows = details.get("activityDetailMetrics") or []
        if not rows or "directHeartRate" not in idx:
            return None
        t_key = "sumDuration" if "sumDuration" in idx else "sumElapsedDuration"
        st = Stream()
        for r in rows:
            m = r.get("metrics") or []

            def get(key: str) -> float | None:
                i = idx.get(key)
                return m[i] if i is not None and i < len(m) else None

            t = get(t_key)
            if t is None:
                continue
            st.t_s.append(round(t, 1))
            st.hr.append(get("directHeartRate"))
            st.speed.append(get("directSpeed"))
        return st if st.t_s else None

    @staticmethod
    def _strength_sets(ex: Any) -> list[StrengthSet]:
        out = []
        for s in (ex or {}).get("exerciseSets") or [] if isinstance(ex, dict) else []:
            if s.get("setType") != "ACTIVE":
                continue
            first = (s.get("exercises") or [{}])[0]
            w = _num(s.get("weight"))
            out.append(StrengthSet(
                index=len(out), exercise=first.get("name") or first.get("category"),
                category=first.get("category"), reps=s.get("repetitionCount"),
                weight_kg=(w / 1000 if w and w > 1000 else w),   # Garmin reports grams
                duration_s=_num(s.get("duration")),
            ))
        return out

    def _download_fit(self, aid: str) -> None:
        assert self.fit_dir is not None
        path = self.fit_dir / f"{aid}.zip"
        if path.exists():
            return
        self.fit_dir.mkdir(parents=True, exist_ok=True)
        try:
            data = self.g.download_activity(aid, dl_fmt=self.g.ActivityDownloadFormat.ORIGINAL)
            path.write_bytes(data)
            time.sleep(self.throttle_s)
        except Exception as e:
            log.debug("FIT download %s failed: %s", aid, e)

    # --- daily data -----------------------------------------------------

    def fetch_wellness(self, day: dt.date) -> DailyWellness:
        d, fresh, g = day.isoformat(), self._is_fresh(day), self.g
        sleep = self._fetch("sleep", d, lambda: g.get_sleep_data(d), fresh=fresh)
        stats = self._fetch("stats", d, lambda: g.get_stats(d), fresh=fresh)
        hrv_month = self._monthly("hrv_range", day, g.get_hrv_data_range)
        ready_month = self._monthly("readiness_range", day, g.get_training_readiness_range)

        dto = (sleep or {}).get("dailySleepDTO") or {} if isinstance(sleep, dict) else {}
        hrv = next((h for h in (hrv_month or {}).get("hrvSummaries") or []
                    if h.get("calendarDate") == d), {}) if isinstance(hrv_month, dict) else {}
        base = hrv.get("baseline") or {}
        readings = [r for r in ready_month or [] if isinstance(r, dict) and r.get("calendarDate") == d] \
            if isinstance(ready_month, list) else []
        # the after-wake-up reading is the one that reflects the night's recovery
        ready = next((r for r in readings if r.get("inputContext") == "AFTER_WAKEUP_RESET"),
                     min(readings, key=lambda r: r.get("timestamp") or "", default={}))
        st = stats if isinstance(stats, dict) else {}
        return DailyWellness(
            date=d,
            sleep_s=_num(dto.get("sleepTimeSeconds")), deep_s=_num(dto.get("deepSleepSeconds")),
            light_s=_num(dto.get("lightSleepSeconds")), rem_s=_num(dto.get("remSleepSeconds")),
            awake_s=_num(dto.get("awakeSleepSeconds")),
            sleep_score=_num(((dto.get("sleepScores") or {}).get("overall") or {}).get("value")),
            hrv_last_night=_num(hrv.get("lastNightAvg")), hrv_weekly_avg=_num(hrv.get("weeklyAvg")),
            hrv_status=hrv.get("status"),
            hrv_baseline_low=_num(base.get("balancedLow")), hrv_baseline_high=_num(base.get("balancedUpper")),
            rhr=_num(st.get("restingHeartRate")),
            bb_charged=_num(st.get("bodyBatteryChargedValue")), bb_drained=_num(st.get("bodyBatteryDrainedValue")),
            bb_high=_num(st.get("bodyBatteryHighestValue")), bb_low=_num(st.get("bodyBatteryLowestValue")),
            stress_avg=_num(st.get("averageStressLevel")) if (st.get("averageStressLevel") or 0) >= 0 else None,
            readiness_score=_num(ready.get("score")), readiness_level=ready.get("level"),
            steps=st.get("totalSteps"), respiration_avg=_num(dto.get("averageRespirationValue")),
        )

    def fetch_fitness(self, day: dt.date) -> FitnessDaily:
        d, fresh, g = day.isoformat(), self._is_fresh(day), self.g
        ts = self._fetch("training_status", d, lambda: g.get_training_status(d), fresh=fresh)
        preds = self._monthly("race_predictions_range", day,
                              lambda s, e: g.get_race_predictions(s, e, "daily"))
        lt = self._monthly("lactate_threshold_range", day,
                           lambda s, e: g.get_lactate_threshold(latest=False, start_date=s, end_date=e))
        es = self._monthly("endurance_score_range", day, g.get_endurance_score)

        status, vo2 = {}, {}
        if isinstance(ts, dict):
            status = _first_device((ts.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData"))
            vo2 = (ts.get("mostRecentVO2Max") or {}).get("generic") or {}
            if status.get("calendarDate") not in (None, d):
                status = {}     # Garmin returns the last known status; only keep same-day values
        load = status.get("acuteTrainingLoadDTO") or {}
        pred = next((p for p in preds or [] if isinstance(p, dict) and p.get("calendarDate") == d), {}) \
            if isinstance(preds, list) else {}

        def latest_series(series: list | None) -> float | None:
            vals = [x for x in series or [] if isinstance(x, dict) and (x.get("from") or "") <= d]
            return _num(vals[-1].get("value")) if vals else None

        lt_speed = latest_series(lt.get("speed")) if isinstance(lt, dict) else None
        lt_hr = latest_series(lt.get("heart_rate") or lt.get("heartRate")) if isinstance(lt, dict) else None
        es_day = None
        if isinstance(es, dict):
            for wk, grp in sorted((es.get("groupMap") or {}).items()):
                if wk <= d:
                    es_day = _num(grp.get("groupAverage"))
        return FitnessDaily(
            date=d,
            vo2max=_num(vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue")) if vo2.get("calendarDate") == d else None,
            training_status=status.get("trainingStatusFeedbackPhrase"),
            acute_load=_num(load.get("dailyTrainingLoadAcute")),
            chronic_load=_num(load.get("dailyTrainingLoadChronic")),
            acwr=_num(load.get("dailyAcuteChronicWorkloadRatio")),
            lt_hr=lt_hr,
            lt_speed=lt_speed * 10 if lt_speed else None,     # Garmin reports LT speed in 10 m/s units
            race_pred_5k_s=_num(pred.get("time5K")), race_pred_10k_s=_num(pred.get("time10K")),
            race_pred_half_s=_num(pred.get("timeHalfMarathon")),
            race_pred_marathon_s=_num(pred.get("timeMarathon")),
            endurance_score=es_day,
        )

    def fetch_extras(self, day: dt.date) -> None:
        if not self.extras:
            return
        d, fresh, g = day.isoformat(), self._is_fresh(day), self.g
        for endpoint, method in DAILY_EXTRAS.items():
            fn = getattr(g, method, None)
            if fn is not None:
                self._fetch(endpoint, d, lambda fn=fn: fn(d), fresh=fresh)
        if day.day == 1 or day == self.today:
            for endpoint, fn in MONTHLY_EXTRAS.items():
                self._monthly(endpoint, day, lambda s, e, fn=fn: fn(g, s, e))

    # --- plan & races ---------------------------------------------------

    def fetch_planned(self, start: dt.date, end: dt.date) -> list[PlannedWorkout]:
        g, out, seen = self.g, [], set()
        months, cur = [], start.replace(day=1)
        while cur <= end:
            months.append(cur)
            cur = (cur + dt.timedelta(days=32)).replace(day=1)
        for m in months:
            cal = self._fetch("calendar", m.strftime("%Y-%m"),
                              lambda m=m: g.get_scheduled_workouts(m.year, m.month), fresh=True)
            for item in (cal or {}).get("calendarItems") or [] if isinstance(cal, dict) else []:
                date = item.get("date") or ""
                kind = item.get("itemType")
                if item.get("isRace") or kind in ("race", "event"):
                    self._races[(item.get("title") or "Race", date)] = Race(
                        name=item.get("title") or "Race", date=date, priority="B", source=SRC)
                if kind != "workout" or not (start.isoformat() <= date <= end.isoformat()):
                    continue
                if item.get("id") in seen:
                    continue
                seen.add(item.get("id"))
                wid = item.get("workoutId")
                steps, est_d, est_s = [], None, None
                if wid:
                    w = self._fetch("workout", str(wid), lambda wid=wid: g.get_workout_by_id(wid),
                                    fresh=date >= self.today.isoformat())
                    if isinstance(w, dict):
                        steps = [workout_step(s) for seg in w.get("workoutSegments") or []
                                 for s in seg.get("workoutSteps") or []]
                        est_d, est_s = _num(w.get("estimatedDistanceInMeters")), _num(w.get("estimatedDurationInSecs"))
                out.append(PlannedWorkout(
                    source=SRC, source_id=str(item.get("id")), date=date, title=item.get("title"),
                    sport=item.get("sportTypeKey"), workout_id=str(wid) if wid else None,
                    steps=steps, est_duration_s=est_s, est_distance_m=est_d,
                ))
        return out

    def fetch_races(self) -> list[Race]:
        primary = self._fetch("primary_event", self.today.isoformat(), self.g.get_primary_event)
        if isinstance(primary, dict) and primary.get("date") and primary.get("eventName"):
            self._races[(primary["eventName"], primary["date"])] = Race(
                name=primary["eventName"], date=primary["date"][:10], priority="A", source=SRC)
        return list(self._races.values())
