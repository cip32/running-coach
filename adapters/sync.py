"""Sync engine: pulls from an adapter into the store.

Idempotent and resumable:
- every write is an upsert, so running twice changes nothing;
- per-day data is tracked with a cursor in sync_state, so an interrupted
  backfill continues where it stopped;
- the last `refresh_days` are always re-fetched because sources revise them.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Callable

from adapters.base import SourceAdapter
from adapters.models import Race
from db.store import Store

log = logging.getLogger(__name__)

PLAN_LOOKAHEAD_DAYS = 70


@dataclass
class SyncResult:
    days: int = 0
    activities: int = 0
    planned: int = 0
    races: int = 0
    errors: list[str] = field(default_factory=list)


def _days(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def sync(adapter: SourceAdapter, store: Store, *, start: dt.date, today: dt.date,
         refresh_days: int = 3, progress: Callable[[str], None] | None = None,
         full: bool = False) -> SyncResult:
    res = SyncResult()
    say = progress or (lambda msg: None)

    def first_day(cursor_key: str) -> dt.date:
        cursor = store.state_get(cursor_key)
        if full or not cursor:
            return start
        return max(start, min(dt.date.fromisoformat(cursor) + dt.timedelta(days=1),
                              today - dt.timedelta(days=refresh_days - 1)))

    act_key, day_key = f"{adapter.name}:activity_cursor", f"{adapter.name}:daily_cursor"

    profile = adapter.fetch_profile()
    if profile:
        store.state_set(f"{adapter.name}:profile_max_hr", str(profile.max_hr or ""))
        store.state_set(f"{adapter.name}:profile_lt_hr", str(profile.lt_hr or ""))

    # activities, in monthly chunks so progress is visible on a long backfill
    chunk_start = first_day(act_key)
    while chunk_start <= today:
        chunk_end = min(today, chunk_start + dt.timedelta(days=30))
        say(f"activities {chunk_start} → {chunk_end}")
        try:
            for a in adapter.fetch_activities(chunk_start, chunk_end):
                store.save_activity(a)
                res.activities += 1
        except Exception as e:  # stop here; the cursor stays before the failed chunk
            res.errors.append(f"activities {chunk_start}: {e}")
            log.warning("activities %s failed: %s", chunk_start, e)
            break
        store.state_set(act_key, chunk_end.isoformat())
        chunk_start = chunk_end + dt.timedelta(days=1)

    # per-day wellness, fitness and extras; cursor advances only after a day completes
    for day in _days(first_day(day_key), today):
        try:
            w = adapter.fetch_wellness(day)
            if w:
                store.save_wellness(w)
            f = adapter.fetch_fitness(day)
            if f:
                store.save_fitness(f)
            adapter.fetch_extras(day)
        except Exception as e:
            res.errors.append(f"day {day}: {e}")
            log.warning("day %s failed: %s", day, e)
            break
        res.days += 1
        store.state_set(day_key, day.isoformat())
        if day.day == 1 or day == today:
            say(f"daily data up to {day}")

    try:
        for p in adapter.fetch_planned(today - dt.timedelta(days=14),
                                       today + dt.timedelta(days=PLAN_LOOKAHEAD_DAYS)):
            store.save_planned(p)
            res.planned += 1
        for r in adapter.fetch_races():
            store.save_race(r)
            res.races += 1
    except Exception as e:
        res.errors.append(f"plan: {e}")
        log.warning("plan sync failed: %s", e)

    store.state_set(f"{adapter.name}:last_sync", dt.datetime.now().isoformat(timespec="seconds"))
    return res


def sync_goal_races(goals: dict, store: Store) -> int:
    """Races from goals.yaml always win over races found in a source calendar."""
    n = 0
    for r in goals.get("races") or []:
        store.save_race(Race(name=r["name"], date=r["date"].isoformat(),
                             priority=r.get("priority", "C"), distance_km=r.get("distance_km"),
                             goal_time=str(r["goal_time"]) if r.get("goal_time") else None,
                             source="goals"))
        n += 1
    return n
