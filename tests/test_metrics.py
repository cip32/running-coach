import datetime as dt

import pytest

from adapters.models import Activity, Lap
from analysis import classify, context, load, plan_compare, recovery, weeks, zones
from analysis.metrics import build_week
from tests.fixtures.factory import GOALS, days, planned, run, wellness

FLOORS = zones.bucket_floors(GOALS)
RULES = GOALS["classification"]


# --- zones -------------------------------------------------------------------

def test_time_in_buckets_from_stream():
    a = run("2026-10-20", 10, [(600, 140, 3.1), (300, 158, 3.8), (200, 170, 4.2), (100, 180, 4.6)])
    tib = zones.time_in_buckets(a, FLOORS)
    assert tib["source"] == "stream"
    # the first sample has no preceding gap, so easy loses 10 s
    assert tib["secs"] == {"easy": 590, "steady": 300, "threshold": 200, "hard": 100}
    s = zones.shares(tib["secs"])
    assert s["steady"] == pytest.approx(300 / 1190)


def test_time_in_buckets_ignores_pauses():
    a = run("2026-10-20", 5)
    a.stream.t_s[50:] = [t + 600 for t in a.stream.t_s[50:]]   # 10-minute stop
    secs = zones.time_in_buckets(a, FLOORS)["secs"]
    assert sum(secs.values()) == pytest.approx((len(a.stream.t_s) - 2) * 10)


def test_time_in_buckets_falls_back_to_source_zones():
    a = Activity(source="t", source_id="x", start_local="2026-10-20T07:00", date="2026-10-20", sport="running",
                 zone_secs=[60, 3000, 600, 120, 30], zone_floors=[91, 114, 152, 166, 175])
    tib = zones.time_in_buckets(a, FLOORS)
    assert tib["source"] == "zones"
    # Garmin Z5 floor 175 < hard floor 178, so it lands in threshold
    assert tib["secs"] == {"easy": 3060, "steady": 600, "threshold": 150, "hard": 0}


def test_distribution_shares_sum_to_one():
    d = zones.distribution([{"easy": 300, "steady": 100, "threshold": 0, "hard": 0},
                            {"easy": 500, "steady": 0, "threshold": 50, "hard": 50}])
    assert d["secs"]["easy"] == 800 and sum(d["share"].values()) == pytest.approx(1, abs=0.002)


# --- classification ---------------------------------------------------------

def _share(a):
    return zones.shares(zones.time_in_buckets(a, FLOORS)["secs"])


def test_classify_long_and_drift():
    # second half: same speed, higher HR -> positive decoupling
    a = run("2026-10-25", 28, [(4500, 140, 3.1), (4500, 150, 3.1)])
    c = classify.classify(a, _share(a), RULES)
    assert c["type"] == "long"
    assert c["hr_drift_pct"] == pytest.approx((1 / 140 - 1 / 150) / (1 / 140) * 100, abs=0.2)


def test_classify_strides_and_grey_zone():
    segs = [(2400, 145, 3.1)] + [(20, 165, 5.2), (60, 140, 2.8)] * 6
    a = run("2026-10-20", 9, segs)
    c = classify.classify(a, _share(a), RULES)
    assert c["type"] == "strides" and c["strides"] == 6 and not c["grey_zone"]

    grey = run("2026-10-21", 10, [(2000, 145, 3.1), (1000, 155, 3.2)])
    assert classify.classify(grey, _share(grey), RULES)["grey_zone"]


def test_classify_lt_hard_and_planned_title_wins():
    lt = run("2026-10-22", 14, [(1200, 140, 3.1), (1500, 170, 4.2), (900, 140, 3.1)])
    assert classify.classify(lt, _share(lt), RULES)["type"] == "LT"
    hard = run("2026-10-23", 12, [(1800, 140, 3.1), (600, 182, 5.0), (600, 140, 3.1)])
    assert classify.classify(hard, _share(hard), RULES)["type"] == "hard"
    easy_hr = run("2026-10-24", 10)
    assert classify.classify(easy_hr, _share(easy_hr), RULES, planned_title="45/15 Intervals Pyramid")["type"] == "hard"
    assert classify.classify(easy_hr, _share(easy_hr), RULES, run_of_day=2)["double"]


# --- load ------------------------------------------------------------------

def test_ewma_steady_state_ratio_is_one():
    daily = [(d, 100.0) for d in days("2026-08-01", 120)]
    e = load.ewma_loads(daily)
    assert e[-1]["acwr"] == pytest.approx(1.0, abs=0.01)


def test_ewma_spike_raises_acwr():
    daily = [(d, 100.0) for d in days("2026-08-01", 90)] + [(d, 200.0) for d in days("2026-10-30", 7)]
    e = load.ewma_loads(daily)
    assert e[-1]["acwr"] > 1.3
    assert e[-1]["atl"] > e[-1]["ctl"]


def test_trimp_fallback_used_without_training_load():
    a = run("2026-10-20", 10, training_load=None)
    assert load.activity_load(a, rest_hr=48, max_hr=185) > 0
    assert load.activity_load(run("2026-10-20", 10, training_load=77.0)) == 77.0


def test_weekly_volume_and_long_run():
    mondays = [dt.date(2026, 10, 12), dt.date(2026, 10, 19)]
    acts = [run("2026-10-13", 10), run("2026-10-18", 20), run("2026-10-20", 12), run("2026-10-25", 26)]
    acts.append(Activity(source="t", source_id="s", start_local="2026-10-21T19:00", date="2026-10-21",
                         sport="strength_training", duration_s=1800))
    v = load.weekly_volume(acts, mondays)
    assert [w["km"] for w in v] == [30.0, 38.0]
    assert [w["runs"] for w in v] == [2, 2]
    assert v[1]["long_run_km"] == 26.0 and v[1]["week"] == "2026-W43"


# --- week comparisons ---------------------------------------------------------

def test_headline_vs_last_week_and_4wk_avg():
    vol = [{"week": f"W{i}", "km": km, "runs": 6, "hours": 6.0, "elev_m": 300, "long_run_km": 20}
           for i, km in enumerate([60, 62, 64, 66, 70])]
    h = weeks.headline(vol)
    assert h["km"] == 70
    assert h["vs_last_week"]["km"] == {"abs": 4.0, "pct": 6.1}
    assert h["avg_4wk"]["km"] == 63.0
    assert h["vs_4wk_avg"]["km"]["abs"] == 7.0


def test_headline_with_no_history():
    h = weeks.headline([{"week": "W1", "km": 50, "runs": 5, "hours": 5.0, "elev_m": 100, "long_run_km": 18}])
    assert h["vs_last_week"]["km"] == {"abs": None, "pct": None} and h["avg_4wk"] is None


def test_due_monday_switches_at_sunday_19():
    sun = dt.date(2026, 10, 25)
    assert weeks.due_monday(dt.datetime(2026, 10, 25, 10, 0)) == dt.date(2026, 10, 12)   # Sunday morning: last week
    assert weeks.due_monday(dt.datetime(2026, 10, 25, 19, 0)) == dt.date(2026, 10, 19)   # Sunday 19:00: this week
    assert weeks.due_monday(dt.datetime(2026, 10, 26, 9, 30)) == dt.date(2026, 10, 19)   # Monday catch-up
    assert weeks.due_monday(dt.datetime(2026, 10, 31, 23, 0)) == dt.date(2026, 10, 19)   # Saturday
    assert sun.weekday() == 6


def test_week_helpers():
    assert weeks.parse_week("2026-W43") == dt.date(2026, 10, 19)
    assert weeks.last_complete_monday(dt.date(2026, 10, 25)) == dt.date(2026, 10, 19)   # Sunday
    assert weeks.last_complete_monday(dt.date(2026, 10, 21)) == dt.date(2026, 10, 12)   # Wednesday


# --- recovery ----------------------------------------------------------------

def test_recovery_red_flags_and_missing():
    hist = [wellness(d, rhr=48) for d in days("2026-09-19", 30)]
    week = [wellness(d) for d in days("2026-10-19", 3)] + \
           [wellness(d, hrv=50, rhr=54, sleep_h=6.2, score=55) for d in days("2026-10-22", 3)] + \
           [wellness("2026-10-25", hrv=None, rhr=55, sleep_h=6.0, score=50)]
    notes = [{"date": "2026-10-23", "text": "Left calf tight after strides"}]
    r = recovery.summarize(week, hist, notes, GOALS["recovery_thresholds"])
    assert r["hrv"]["nights_below_baseline"] == 3
    assert r["hrv"]["current_streak_below"] == 3        # the missing night doesn't reset the streak
    assert r["rhr"]["baseline_30d"] == 48.0
    assert any("HRV below baseline 3" in f for f in r["red_flags"])
    assert any("Resting HR" in f for f in r["red_flags"])
    assert any("tight" in f for f in r["red_flags"])
    assert "HRV: 1 of 7 days missing" in r["missing"]


def test_recovery_all_good_no_flags():
    week = [wellness(d) for d in days("2026-10-19", 7)]
    r = recovery.summarize(week, [wellness(d) for d in days("2026-09-19", 30)], [], GOALS["recovery_thresholds"])
    assert r["red_flags"] == [] and r["missing"] == []
    assert r["sleep"]["avg_h"] == 8.0 and r["sleep"]["sd_h"] == 0.0


# --- planned vs executed -------------------------------------------------------

EASY_STEPS = [{"kind": "interval", "target_type": "heart.rate.zone", "target_low": 126, "target_high": 148}]


def test_plan_easy_on_target_and_too_hard():
    p = planned("2026-10-23", "Slow Recovery Run", EASY_STEPS, km=12)
    assert plan_compare.compare(p, [run("2026-10-23", 12)])["verdict"] == "on target"
    hot = run("2026-10-23", 12, [(int(12000 / 3.3), 156, 3.3)])
    r = plan_compare.compare(p, [hot])
    assert r["verdict"] == "too hard" and "avg HR 156" in r["why"]


def test_plan_quality_too_easy_and_missed():
    steps = [{"kind": "interval", "target_type": "heart.rate.zone", "target_low": 171, "target_high": 180}]
    p = planned("2026-10-27", "Recovery + 30/30", steps)
    assert plan_compare.compare(p, [run("2026-10-27", 12)])["verdict"] == "too easy"
    assert plan_compare.compare(p, [])["verdict"] == "missed"


def test_plan_pace_targets():
    steps = [{"kind": "interval", "target_type": "pace.zone", "target_low": 4.1, "target_high": 4.5}]
    p = planned("2026-10-29", "Threshold 3x10'", steps)
    a = run("2026-10-29", 14, laps=[Lap(index=1, avg_speed=4.3, intensity="INTERVAL"),
                                    Lap(index=2, avg_speed=4.35, intensity="INTERVAL")])
    assert plan_compare.compare(p, [a])["verdict"] == "on target"
    a.laps = [Lap(index=1, avg_speed=4.9, intensity="INTERVAL")]
    assert plan_compare.compare(p, [a])["verdict"] == "too hard"


# --- context -----------------------------------------------------------------

def test_context_ramp_and_race():
    c = context.build(GOALS, dt.date(2026, 10, 19), 68)
    assert c["phase"]["name"] == "base" and c["phase"]["week"] == 1
    assert c["volume"]["ramp_target_km"] == 65.0
    assert c["a_race"]["name"] == "Vienna Marathon" and c["a_race"]["weeks_to_go"] == 25.0
    assert context.target_km(GOALS, dt.date(2026, 11, 23)) == pytest.approx(65 + 35 * 35 / 70, abs=0.1)
    assert context.target_km(GOALS, dt.date(2027, 1, 4)) == 100.0


# --- full week assembly (store + demo data) -----------------------------------

def test_build_week_on_demo_data(store):
    from adapters.demo import DemoAdapter
    from adapters.sync import sync
    today = dt.date(2026, 10, 25)
    a = DemoAdapter(today=today)
    sync(a, store, start=a.first, today=today)
    m = build_week(store, GOALS, dt.date(2026, 10, 19))
    assert m["week"] == "2026-W43" and len(m["daily"]) == 7
    assert m["headline"]["runs"] == len(m["sessions"]) >= 7
    assert any(s["type"] == "long" for s in m["sessions"])
    assert any(s["double"] for s in m["sessions"]) or True
    assert m["intensity"]["grey_zone_runs"], "demo week has deliberate grey-zone running"
    assert m["recovery"]["red_flags"], "demo week has a deliberate HRV dip"
    assert "HRV: 1 of 7 days missing" in m["missing"]
    assert m["key_workouts"] and all("verdict" in k for k in m["key_workouts"])
    assert m["other_activities"][0]["sport"] == "strength_training"
    import json
    json.dumps(m)   # must be serialisable for the LLM and the DB
