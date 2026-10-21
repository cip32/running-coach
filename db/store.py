"""SQLite store. Every write is an upsert on a natural key, so re-syncing the
same data any number of times leaves the database unchanged."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
import zlib
from pathlib import Path
from typing import Any, Iterable, TypeVar

from adapters.models import (
    Activity, DailyWellness, FitnessDaily, Lap, PlannedWorkout, Race, StrengthSet, Stream,
)

SCHEMA = Path(__file__).with_name("schema.sql")
T = TypeVar("T")

_JSON_FIELDS: dict[type, set[str]] = {
    Activity: {"zone_secs", "zone_floors", "extra"},
    PlannedWorkout: {"steps"},
}


def _pack(obj: Any) -> bytes:
    return zlib.compress(json.dumps(obj, default=str, separators=(",", ":")).encode())


def _unpack(blob: bytes) -> Any:
    return json.loads(zlib.decompress(blob))


def _row_from(obj: Any, skip: Iterable[str] = ()) -> dict[str, Any]:
    row = {}
    json_fields = _JSON_FIELDS.get(type(obj), set())
    for f in dataclasses.fields(obj):
        if f.name in skip:
            continue
        v = getattr(obj, f.name)
        row[f.name] = json.dumps(v) if f.name in json_fields and v is not None else v
    return row


def _obj_from(cls: type[T], row: sqlite3.Row, **extra: Any) -> T:
    names = {f.name for f in dataclasses.fields(cls)}
    json_fields = _JSON_FIELDS.get(cls, set())
    kw = {}
    for k in row.keys():
        if k in names:
            v = row[k]
            kw[k] = json.loads(v) if k in json_fields and v is not None else v
    kw.update(extra)
    return cls(**kw)


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- generic -------------------------------------------------------

    def _upsert(self, table: str, row: dict[str, Any], keys: tuple[str, ...]) -> None:
        cols = list(row)
        updates = [c for c in cols if c not in keys]
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            f"ON CONFLICT ({', '.join(keys)}) DO "
            + (f"UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in updates)}" if updates else "NOTHING")
        )
        self.db.execute(sql, [row[c] for c in cols])

    # --- raw cache -----------------------------------------------------

    def raw_put(self, source: str, endpoint: str, key: str, payload: Any) -> None:
        with self.db:
            self._upsert("raw_cache", {
                "source": source, "endpoint": endpoint, "key": key,
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "payload": _pack(payload),
            }, ("source", "endpoint", "key"))

    def raw_get(self, source: str, endpoint: str, key: str) -> Any | None:
        r = self.db.execute(
            "SELECT payload FROM raw_cache WHERE source=? AND endpoint=? AND key=?",
            (source, endpoint, key)).fetchone()
        return _unpack(r["payload"]) if r else None

    def raw_has(self, source: str, endpoint: str, key: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM raw_cache WHERE source=? AND endpoint=? AND key=?",
            (source, endpoint, key)).fetchone() is not None

    # --- writes --------------------------------------------------------

    def save_activity(self, a: Activity) -> None:
        with self.db:
            self._upsert("activities", _row_from(a, skip=("laps", "strength_sets", "stream")),
                         ("source", "source_id"))
            # child rows are replaced wholesale so a re-sync can't leave stale laps
            self.db.execute("DELETE FROM laps WHERE source=? AND activity_id=?", (a.source, a.source_id))
            self.db.execute("DELETE FROM strength_sets WHERE source=? AND activity_id=?", (a.source, a.source_id))
            for lap in a.laps:
                row = _row_from(lap)
                row["idx"] = row.pop("index")
                self._upsert("laps", {"source": a.source, "activity_id": a.source_id, **row},
                             ("source", "activity_id", "idx"))
            for s in a.strength_sets:
                row = _row_from(s)
                row["idx"] = row.pop("index")
                self._upsert("strength_sets", {"source": a.source, "activity_id": a.source_id, **row},
                             ("source", "activity_id", "idx"))
            if a.stream is not None:
                self._upsert("activity_streams", {
                    "source": a.source, "activity_id": a.source_id,
                    "payload": _pack(dataclasses.asdict(a.stream)),
                }, ("source", "activity_id"))

    def save_wellness(self, w: DailyWellness) -> None:
        with self.db:
            self._upsert("daily_wellness", _row_from(w), ("date",))

    def save_fitness(self, f: FitnessDaily) -> None:
        with self.db:
            self._upsert("fitness_daily", _row_from(f), ("date",))

    def save_planned(self, p: PlannedWorkout) -> None:
        with self.db:
            self._upsert("planned_workouts", _row_from(p), ("source", "source_id"))

    def save_race(self, r: Race) -> None:
        with self.db:
            self._upsert("races", _row_from(r), ("name", "date"))

    def add_note(self, text: str, ts: dt.datetime) -> int:
        with self.db:
            cur = self.db.execute("INSERT INTO notes (ts, date, text) VALUES (?, ?, ?)",
                                  (ts.isoformat(timespec="seconds"), ts.date().isoformat(), text))
            return int(cur.lastrowid)

    def save_report(self, week: str, md_path: str, html_path: str,
                    metrics: dict, narrative: dict | None) -> None:
        with self.db:
            self._upsert("reports", {
                "week": week,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "md_path": md_path, "html_path": html_path,
                "metrics": json.dumps(metrics, default=str),
                "narrative": json.dumps(narrative) if narrative is not None else None,
            }, ("week",))

    def state_get(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def state_set(self, key: str, value: str) -> None:
        with self.db:
            self._upsert("sync_state", {"key": key, "value": value}, ("key",))

    # --- reads ---------------------------------------------------------

    def activities(self, start: str, end: str, *, with_streams: bool = False) -> list[Activity]:
        """Activities with start <= date <= end, oldest first, laps and sets attached."""
        rows = self.db.execute(
            "SELECT * FROM activities WHERE date BETWEEN ? AND ? ORDER BY start_local", (start, end)
        ).fetchall()
        out = []
        for r in rows:
            key = (r["source"], r["source_id"])
            laps = [Lap(index=l["idx"], **{k: l[k] for k in l.keys()
                                          if k not in ("source", "activity_id", "idx")})
                    for l in self.db.execute(
                        "SELECT * FROM laps WHERE source=? AND activity_id=? ORDER BY idx", key)]
            sets = [StrengthSet(index=s["idx"], **{k: s[k] for k in s.keys()
                                                   if k not in ("source", "activity_id", "idx")})
                    for s in self.db.execute(
                        "SELECT * FROM strength_sets WHERE source=? AND activity_id=? ORDER BY idx", key)]
            stream = None
            if with_streams:
                srow = self.db.execute(
                    "SELECT payload FROM activity_streams WHERE source=? AND activity_id=?", key).fetchone()
                if srow:
                    stream = Stream(**_unpack(srow["payload"]))
            out.append(_obj_from(Activity, r, laps=laps, strength_sets=sets, stream=stream))
        return out

    def wellness(self, start: str, end: str) -> list[DailyWellness]:
        return [_obj_from(DailyWellness, r) for r in self.db.execute(
            "SELECT * FROM daily_wellness WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))]

    def fitness(self, start: str, end: str) -> list[FitnessDaily]:
        return [_obj_from(FitnessDaily, r) for r in self.db.execute(
            "SELECT * FROM fitness_daily WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))]

    def planned(self, start: str, end: str) -> list[PlannedWorkout]:
        return [_obj_from(PlannedWorkout, r) for r in self.db.execute(
            "SELECT * FROM planned_workouts WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))]

    def races(self) -> list[Race]:
        return [_obj_from(Race, r) for r in self.db.execute("SELECT * FROM races ORDER BY date")]

    def notes(self, start: str, end: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT ts, date, text FROM notes WHERE date BETWEEN ? AND ? ORDER BY ts", (start, end))]

    def report(self, week: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM reports WHERE week=?", (week,)).fetchone()
        return self._report_dict(r) if r else None

    def recent_reports(self, n: int, before_week: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM reports", []
        if before_week:
            sql, args = sql + " WHERE week < ?", [before_week]
        rows = self.db.execute(sql + " ORDER BY week DESC LIMIT ?", (*args, n)).fetchall()
        return [self._report_dict(r) for r in rows]

    @staticmethod
    def _report_dict(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["metrics"] = json.loads(d["metrics"]) if d["metrics"] else None
        d["narrative"] = json.loads(d["narrative"]) if d["narrative"] else None
        return d

    def counts(self) -> dict[str, int]:
        tables = ["activities", "laps", "strength_sets", "activity_streams", "daily_wellness",
                  "fitness_daily", "planned_workouts", "races", "notes", "reports", "raw_cache"]
        return {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
