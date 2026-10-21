"""Adapter interface. One class per data source; each returns the common model
in adapters/models.py. To add TrainingPeaks, Notion or Strava later, subclass
SourceAdapter and implement the methods that source can provide; the defaults
return nothing, so partial sources are fine."""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from typing import Iterator

from adapters.models import Activity, DailyWellness, FitnessDaily, PlannedWorkout, Profile, Race


class SourceAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def fetch_activities(self, start: dt.date, end: dt.date) -> Iterator[Activity]:
        """All activities (any sport) that started between start and end, inclusive."""

    def fetch_profile(self) -> Profile | None:
        return None

    def fetch_wellness(self, day: dt.date) -> DailyWellness | None:
        return None

    def fetch_fitness(self, day: dt.date) -> FitnessDaily | None:
        return None

    def fetch_planned(self, start: dt.date, end: dt.date) -> list[PlannedWorkout]:
        return []

    def fetch_races(self) -> list[Race]:
        return []

    def fetch_extras(self, day: dt.date) -> None:
        """Optional: pull any other per-day data into the raw cache only."""
        return None
