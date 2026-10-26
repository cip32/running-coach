import datetime as dt
from types import SimpleNamespace

import cli
import config
import llm
from db.store import Store


def test_note_is_stored_and_flags_pain(tmp_path, monkeypatch, capsys):
    db = tmp_path / "coach.db"
    monkeypatch.setattr(cli, "_store", lambda demo=False: Store(db))
    assert cli.main(["note", "left", "calf", "tight", "after", "strides"]) == 0
    out = capsys.readouterr().out
    assert "physio" in out
    with Store(db) as s:
        today = dt.datetime.now(config.tz()).date().isoformat()
        assert s.notes(today, today)[0]["text"] == "left calf tight after strides"


def test_ask_streams_with_context(tmp_path, monkeypatch, capsys):
    db = tmp_path / "coach.db"
    monkeypatch.setattr(cli, "_store", lambda demo=False: Store(db))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    calls = []

    class FakeStream:
        def __init__(self, **kw):
            calls.append(kw)
            self.text_stream = iter(["Keep ", "it easy."])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Keep it easy.")],
                                   usage=SimpleNamespace(input_tokens=1, cache_read_input_tokens=0, output_tokens=1))

    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: FakeStream(**kw)))))
    assert cli.main(["note", "slept badly, work stress"]) == 0
    assert cli.main(["ask", "Should", "I", "run", "doubles?"]) == 0
    assert "Keep it easy." in capsys.readouterr().out
    first = calls[0]["messages"][0]["content"]
    assert "<training_digest>" in first and "slept badly" in first and first.endswith("Should I run doubles?")
    assert calls[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_weekly_skips_done_week_and_runs_missing_one(tmp_path, monkeypatch, capsys):
    from analysis.load import iso_week
    from analysis.weeks import due_monday
    db = tmp_path / "coach.db"
    monkeypatch.setattr(cli, "_store", lambda demo=False: Store(db))
    ran = []
    monkeypatch.setattr(cli, "cmd_sync", lambda a: ran.append("sync") or 0)
    monkeypatch.setattr(cli, "cmd_report", lambda a: ran.append(("report", a.week)) or 0)
    week = iso_week(due_monday(dt.datetime.now(config.tz()).replace(tzinfo=None)))

    assert cli.main(["weekly"]) == 0                      # nothing yet -> sync + report
    assert ran == ["sync", ("report", week)]

    ran.clear()
    with Store(db) as s:
        s.save_report(week, "a.md", "a.html", {}, {"bullets": []})
    assert cli.main(["weekly"]) == 0                      # done -> no Garmin calls at all
    assert ran == [] and "nothing to do" in capsys.readouterr().out

    with Store(db) as s:                                  # numbers-only, just generated -> don't hammer
        s.save_report(week, "a.md", "a.html", {}, None)
    assert cli.main(["weekly"]) == 0 and ran == []
    assert cli.main(["weekly", "--force"]) == 0 and ran == ["sync", ("report", week)]


def test_ask_without_key_is_friendly(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["ask", "hi"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out
