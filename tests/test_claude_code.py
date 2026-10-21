import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import llm
from tests.test_llm import NARRATIVE, _metrics
from tests.fixtures.factory import GOALS


@pytest.fixture
def cc(monkeypatch, tmp_path):
    """Claude Code backend with subprocess.run faked."""
    monkeypatch.setenv("COACH_BACKEND", "claude-code")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-reach-claude-code")
    monkeypatch.setenv("COACH_DB", str(tmp_path / "coach.db"))
    monkeypatch.setattr(config, "find_claude_code", lambda: Path("C:/fake/claude.exe"))
    calls, replies = [], []

    def run(cmd, **kw):
        calls.append({"cmd": cmd, **kw})
        out = replies.pop(0) if replies else {}
        base = {"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn",
                "session_id": "sess-1", "result": "ok"}
        return SimpleNamespace(returncode=0, stdout=json.dumps({**base, **out}), stderr="")
    monkeypatch.setattr(llm.subprocess, "run", run)
    return SimpleNamespace(calls=calls, replies=replies)


def test_report_via_claude_code(cc, store):
    cc.replies.append({"structured_output": NARRATIVE})
    assert config.settings().llm_available
    assert llm.weekly_narrative(_metrics(store), GOALS, []) == NARRATIVE
    call = cc.calls[0]
    cmd = call["cmd"]
    assert cmd[1] == "-p"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == llm.NARRATIVE_SCHEMA
    assert cmd[cmd.index("--tools") + 1] == "" and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "ANTHROPIC_API_KEY" not in call["env"], "must bill the subscription, never the API key"
    assert "<week_data>" in call["input"]          # data goes over stdin, not the command line


def test_claude_code_error_is_raised(cc, store):
    cc.replies.append({"is_error": True, "subtype": "error_during_execution", "result": "usage limit reached"})
    with pytest.raises(RuntimeError, match="usage limit"):
        llm.weekly_narrative(_metrics(store), GOALS, [])


def test_chat_resumes_session(cc):
    cc.replies += [{"result": "Easy week.", "session_id": "abc"}, {"result": "Yes.", "session_id": "abc"}]
    chat = llm.Chat("<training_digest>{}</training_digest>")
    assert "".join(chat.ask("How was my week?")) == "Easy week."
    assert "".join(chat.ask("Can I race Sunday?")) == "Yes."
    first, second = cc.calls
    assert first["input"].startswith("<training_digest>") and "--resume" not in first["cmd"]
    assert "--no-session-persistence" not in first["cmd"]
    assert second["cmd"][second["cmd"].index("--resume") + 1] == "abc"
    assert second["input"] == "Can I race Sunday?"


def test_find_claude_code_prefers_newest_bundled(monkeypatch, tmp_path):
    for v in ("2.1.99", "2.1.304", "2.1.300"):
        exe = tmp_path / "Claude" / "claude-code" / v / "x" / "claude.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
    monkeypatch.delenv("CLAUDE_CODE_PATH", raising=False)
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config.find_claude_code().parent.parent.name == "2.1.304"
