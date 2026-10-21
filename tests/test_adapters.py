import datetime as dt

from adapters.base import SourceAdapter
from adapters.demo import DemoAdapter
from adapters.sync import sync

TODAY = dt.date(2026, 10, 25)  # a Sunday


def test_demo_adapter_contract():
    a = DemoAdapter(today=TODAY)
    assert isinstance(a, SourceAdapter)
    acts = list(a.fetch_activities(TODAY - dt.timedelta(days=6), TODAY))
    runs = [x for x in acts if x.is_run]
    assert len(runs) >= 6
    assert all(r.stream and len(r.stream.hr) == len(r.stream.t_s) for r in runs)
    assert all(r.laps for r in runs)
    assert any(x.sport == "strength_training" and x.strength_sets for x in acts)
    assert a.fetch_wellness(TODAY - dt.timedelta(days=2)).hrv_last_night is None  # deliberate gap
    assert a.fetch_wellness(TODAY - dt.timedelta(days=200)) is None


def test_demo_is_deterministic():
    one = list(DemoAdapter(today=TODAY).fetch_activities(TODAY - dt.timedelta(days=6), TODAY))
    two = list(DemoAdapter(today=TODAY).fetch_activities(TODAY - dt.timedelta(days=6), TODAY))
    assert [(x.source_id, x.distance_m) for x in one] == [(x.source_id, x.distance_m) for x in two]


def test_sync_is_idempotent(store):
    adapter = DemoAdapter(today=TODAY)
    start = adapter.first
    sync(adapter, store, start=start, today=TODAY)
    first = store.counts()
    res = sync(adapter, store, start=start, today=TODAY)
    assert store.counts() == first
    assert res.days == 3                      # only the refresh window re-fetched
    assert first["activities"] > 60 and first["daily_wellness"] == 70


class _FlakyAdapter(DemoAdapter):
    """Fails on one day to check the cursor stops before it and resumes later."""
    fail_on: dt.date | None = None

    def fetch_wellness(self, day):
        if day == self.fail_on:
            raise RuntimeError("429 rate limited")
        return super().fetch_wellness(day)


def test_sync_resumes_after_failure(store):
    a = _FlakyAdapter(today=TODAY)
    a.fail_on = a.first + dt.timedelta(days=20)
    res = sync(a, store, start=a.first, today=TODAY)
    assert res.errors and store.state_get("demo:daily_cursor") == (a.fail_on - dt.timedelta(days=1)).isoformat()
    a.fail_on = None
    res = sync(a, store, start=a.first, today=TODAY)
    assert not res.errors and store.counts()["daily_wellness"] == 70
