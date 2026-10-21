import datetime as dt

import config
from adapters.models import (
    Activity, DailyWellness, FitnessDaily, Lap, PlannedWorkout, Race, StrengthSet, Stream,
)


def _activity(**kw):
    base = dict(
        source="test", source_id="1", start_local="2026-10-20T12:07:32", date="2026-10-20",
        sport="running", distance_m=13461.0, duration_s=4274.0, avg_hr=144.0,
        zone_secs=[26.0, 3656.8, 560.2, 0.0, 0.0], extra={"weather_temp_c": 9},
        laps=[Lap(index=1, distance_m=11280.0, duration_s=3600.0, avg_hr=143.0),
              Lap(index=2, distance_m=2181.0, duration_s=674.0, avg_hr=150.0)],
        stream=Stream(t_s=[0, 1, 2], hr=[120, 121, None], speed=[3.1, 3.2, 3.2]),
    )
    base.update(kw)
    return Activity(**base)


def test_goals_parse():
    goals = config.load_goals()
    vienna = goals["races"][0]
    assert vienna["date"] == dt.date(2027, 4, 18)
    assert config.time_to_s(vienna["goal_time"]) == 2 * 3600 + 45 * 60
    assert [p["name"] for p in goals["phases"]] == ["base", "marathon_block"]
    assert goals["phases"][0]["end"] < goals["phases"][1]["start"]


def test_activity_roundtrip(store):
    store.save_activity(_activity())
    [a] = store.activities("2026-10-20", "2026-10-20", with_streams=True)
    assert a.distance_m == 13461.0
    assert a.zone_secs == [26.0, 3656.8, 560.2, 0.0, 0.0]
    assert a.extra == {"weather_temp_c": 9}
    assert [l.index for l in a.laps] == [1, 2]
    assert a.stream.hr == [120, 121, None]


def test_activity_upsert_is_idempotent(store):
    for _ in range(3):
        store.save_activity(_activity())
    # a re-sync with fewer laps must not leave the old lap behind
    store.save_activity(_activity(name="renamed", laps=[Lap(index=1, distance_m=13461.0)]))
    c = store.counts()
    assert c["activities"] == 1 and c["laps"] == 1 and c["activity_streams"] == 1
    assert store.activities("2026-10-20", "2026-10-20")[0].name == "renamed"


def test_strength_sets(store):
    store.save_activity(_activity(source_id="2", sport="strength_training", laps=[], stream=None,
                                  strength_sets=[StrengthSet(index=0, exercise="SQUAT", reps=8, weight_kg=60)]))
    [a] = store.activities("2026-10-20", "2026-10-20")
    assert a.strength_sets[0].exercise == "SQUAT" and not a.is_run


def test_daily_tables_idempotent(store):
    for _ in range(2):
        store.save_wellness(DailyWellness(date="2026-10-20", sleep_score=85, hrv_last_night=55))
        store.save_fitness(FitnessDaily(date="2026-10-20", vo2max=55.4))
        store.save_planned(PlannedWorkout(source="test", source_id="w1", date="2026-10-25",
                                          title="Slow Longish", steps=[{"kind": "interval"}]))
        store.save_race(Race(name="Vienna Marathon", date="2027-04-18", priority="A"))
    store.save_wellness(DailyWellness(date="2026-10-20", sleep_score=80))
    c = store.counts()
    assert (c["daily_wellness"], c["fitness_daily"], c["planned_workouts"], c["races"]) == (1, 1, 1, 1)
    assert store.wellness("2026-10-01", "2026-10-31")[0].sleep_score == 80
    assert store.planned("2026-10-25", "2026-10-25")[0].steps == [{"kind": "interval"}]


def test_raw_cache_and_state(store):
    store.raw_put("garmin", "sleep", "2026-10-20", {"a": [1, 2]})
    store.raw_put("garmin", "sleep", "2026-10-20", {"a": [3]})
    assert store.raw_get("garmin", "sleep", "2026-10-20") == {"a": [3]}
    assert store.counts()["raw_cache"] == 1
    store.state_set("backfill_cursor", "2025-03-01")
    assert store.state_get("backfill_cursor") == "2025-03-01"


def test_notes_and_reports(store):
    store.add_note("calf tight", dt.datetime(2026, 10, 21, 20, 0))
    assert store.notes("2026-10-21", "2026-10-21")[0]["text"] == "calf tight"
    store.save_report("2026-W43", "a.md", "a.html", {"km": 70}, None)
    store.save_report("2026-W43", "a.md", "a.html", {"km": 71}, {"bullets": []})
    assert store.report("2026-W43")["metrics"] == {"km": 71}
    assert store.counts()["reports"] == 1


def test_secret_redaction():
    import logging
    rec = logging.LogRecord("x", logging.INFO, "", 0, "key sk-ant-abc123 and password=hunter2", None, None)
    config.RedactSecrets().filter(rec)
    assert "sk-ant" not in rec.msg and "hunter2" not in rec.msg
    rec = logging.LogRecord("x", logging.INFO, "", 0, "%s %d token=%s", ("POST", 400, "abc"), None)
    config.RedactSecrets().filter(rec)
    assert rec.getMessage() == "POST 400 [REDACTED]"
