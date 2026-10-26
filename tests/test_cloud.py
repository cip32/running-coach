"""Pieces the cloud routine relies on."""

import copy
import datetime as dt
import json

import pytest

import cli
import llm
from db.store import Store
from tests.test_llm import NARRATIVE


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    from adapters.demo import DemoAdapter
    from adapters.sync import sync
    db = tmp_path / "coach.db"
    today = dt.date.today()
    with Store(db) as s:
        a = DemoAdapter(today=today)
        sync(a, s, start=a.first, today=today)
        s.raw_put("garmin", "sleep", "2026-10-20", {"big": "x" * 1000})
    monkeypatch.setattr(cli, "_store", lambda demo=False: Store(db))
    monkeypatch.setenv("COACH_DB", str(db))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "out"))
    return db


def test_validate_narrative():
    assert llm.validate_narrative(copy.deepcopy(NARRATIVE)) == NARRATIVE
    bad = copy.deepcopy(NARRATIVE)
    del bad["next_week"]["days"][0]["why"]
    with pytest.raises(ValueError, match=r"next_week.days\[0\]"):
        llm.validate_narrative(bad)
    with pytest.raises(ValueError, match="bullets"):
        llm.validate_narrative({**NARRATIVE, "bullets": "not a list"})


def test_brief_then_report_with_narrative_file(demo_db, tmp_path, capsys):
    brief = tmp_path / "brief.md"
    assert cli.main(["brief", "--out", str(brief)]) == 0
    text = brief.read_text(encoding="utf-8")
    assert "<coaching_principles>" in text and "<week_data>" in text and '"next_week"' in text
    nfile = tmp_path / "n.json"
    nfile.write_text(json.dumps(NARRATIVE), encoding="utf-8")
    assert cli.main(["report", "--narrative-file", str(nfile)]) == 0
    md = next((tmp_path / "out").glob("*.md")).read_text(encoding="utf-8")
    assert "Solid base week." in md and "Numbers only" not in md
    with Store(demo_db) as s:
        assert s.recent_reports(1)[0]["narrative"]["headline"] == "Solid base week."


def test_slim_db_drops_raw_cache_only(demo_db):
    with Store(demo_db) as s:
        before = s.counts()
    assert before["raw_cache"] == 1
    assert cli.main(["slim-db"]) == 0
    with Store(demo_db) as s:
        after = s.counts()
    assert after["raw_cache"] == 0
    assert {k: v for k, v in after.items() if k != "raw_cache"} == \
           {k: v for k, v in before.items() if k != "raw_cache"}


def test_garmin_logs_in_from_env_tokens(monkeypatch, tmp_path):
    from adapters import garmin as gmod
    seen = []

    class FakeGarmin:
        def login(self, tokenstore=None):
            seen.append(tokenstore)
    monkeypatch.setattr(gmod, "Garmin", FakeGarmin)
    monkeypatch.setenv("GARMIN_TOKENS", "  long-token-string  ")
    a = gmod.GarminAdapter(tmp_path / "missing-dir", Store(":memory:"), today=dt.date(2026, 10, 26))
    a.g
    assert seen == ["long-token-string"]
    monkeypatch.delenv("GARMIN_TOKENS")
    with pytest.raises(RuntimeError, match="No Garmin login"):
        gmod.GarminAdapter(tmp_path / "missing-dir", Store(":memory:"), today=dt.date(2026, 10, 26)).g
