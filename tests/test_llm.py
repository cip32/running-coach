import datetime as dt
import json
from types import SimpleNamespace

import pytest

import llm
from tests.fixtures.factory import GOALS

NARRATIVE = {
    "headline": "Solid base week.", "sessions": "All easy.", "key_workouts": "Long run on target.",
    "intensity": "Mostly easy.", "load": "Ratio steady.", "recovery": "HRV dipped late.",
    "context": "Week 1 of base.",
    "next_week": {"summary": "Hold volume.", "volume_km": "68-72",
                  "days": [{"day": d, "session": "Easy 10 km", "why": "volume"}
                           for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")],
                  "protect": ["Sunday long run"], "cut_if_recovery_poor": ["Wednesday double"]},
    "bullets": ["a", "b", "c", "d", "e"], "actions": ["x", "y", "z"],
}


class FakeMessages:
    def __init__(self, stop_reason="end_turn", text=json.dumps(NARRATIVE)):
        self.calls, self.stop_reason, self.text = [], stop_reason, text

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            stop_reason=self.stop_reason, stop_details=SimpleNamespace(category="bio"),
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=self.text)],
            usage=SimpleNamespace(input_tokens=10, cache_read_input_tokens=0, output_tokens=5))


@pytest.fixture
def fake(monkeypatch):
    msgs = FakeMessages()
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=msgs)))
    return msgs


def _metrics(store):
    from adapters.demo import DemoAdapter
    from adapters.sync import sync
    from analysis.metrics import build_week
    today = dt.date(2026, 10, 25)
    a = DemoAdapter(today=today)
    sync(a, store, start=a.first, today=today)
    return build_week(store, GOALS, dt.date(2026, 10, 19), today=today)


def test_weekly_narrative_request_shape(fake, store):
    out = llm.weekly_narrative(_metrics(store), GOALS, [], model="claude-opus-5-5")
    assert out == NARRATIVE
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == [llm.FALLBACK_BETA]
    assert kw["output_config"]["format"]["schema"] is llm.NARRATIVE_SCHEMA
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "coaching_principles" in kw["system"][1]["text"] and "goals_yaml" in kw["system"][2]["text"]
    # volatile data stays out of the cached prefix
    assert "week_data" in kw["messages"][0]["content"]
    assert "ewma" not in json.loads(kw["messages"][0]["content"].split("<week_data>\n")[1].split("\n</week_data>")[0])["metrics"]["load"]


def test_refusal_raises(fake, store):
    fake.stop_reason = "refusal"
    with pytest.raises(llm.CoachRefusal):
        llm.weekly_narrative(_metrics(store), GOALS, [])


def test_pipeline_survives_llm_failure(monkeypatch, store, tmp_path):
    from reports import pipeline
    _metrics(store)

    def boom(*a, **k):
        raise llm.CoachRefusal("declined")
    monkeypatch.setattr(llm, "weekly_narrative", boom)
    res = pipeline.generate(store, GOALS | {"athlete": {"name": "T"}}, dt.date(2026, 10, 19),
                            dt.date(2026, 10, 25), tmp_path, use_llm=True)
    assert res["narrative"] is None
    assert any("coaching commentary unavailable" in x for x in res["metrics"]["missing"])
    assert res["html"].exists() and "Numbers only" in res["html"].read_text(encoding="utf-8")


def test_full_narrative_renders(store, tmp_path):
    from reports.render import render
    m = _metrics(store)
    md, html = render(m, NARRATIVE, GOALS | {"athlete": {"name": "T"}}, tmp_path)
    text = md.read_text(encoding="utf-8")
    assert "Solid base week." in text and "68-72" in text and "Sunday long run" in text
    assert "Numbers only" not in text
    assert "Hold volume." in html.read_text(encoding="utf-8")


def test_schema_is_strict():
    def walk(s):
        if s.get("type") == "object":
            assert s["additionalProperties"] is False
            assert set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if s.get("type") == "array":
            walk(s["items"])
    walk(llm.NARRATIVE_SCHEMA)
