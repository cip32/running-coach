"""Claude calls: weekly narrative (structured JSON) and chat.

Two backends, chosen with COACH_BACKEND in .env:
- "claude-code" (default): runs the Claude Code CLI headless on your Claude
  subscription. No API key or credits needed.
- "api": the Anthropic API with ANTHROPIC_API_KEY (billed per use).

Numbers come from analysis/; the model only interprets them. The stable part
of every request (instructions, coaching principles, goals) is a cached
system prefix, so repeated reports and chat turns are cheap.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterator

import anthropic

import config

log = logging.getLogger(__name__)
PROMPTS = config.ROOT / "prompts"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}
NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": _STR, "sessions": _STR, "key_workouts": _STR, "intensity": _STR,
        "load": _STR, "recovery": _STR, "context": _STR,
        "next_week": {
            "type": "object",
            "properties": {
                "summary": _STR,
                "volume_km": _STR,
                "days": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"day": _STR, "session": _STR, "why": _STR},
                    "required": ["day", "session", "why"], "additionalProperties": False,
                }},
                "protect": _STR_LIST,
                "cut_if_recovery_poor": _STR_LIST,
            },
            "required": ["summary", "volume_km", "days", "protect", "cut_if_recovery_poor"],
            "additionalProperties": False,
        },
        "bullets": _STR_LIST,
        "actions": _STR_LIST,
    },
    "required": ["headline", "sessions", "key_workouts", "intensity", "load", "recovery",
                 "context", "next_week", "bullets", "actions"],
    "additionalProperties": False,
}


class CoachRefusal(RuntimeError):
    pass


# --- Claude Code backend (subscription) ----------------------------------------

CC_TIMEOUT_S = 900
# credentials that would make Claude Code bill the API instead of the subscription
_API_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")


def _cc_workdir() -> Path:
    """Fixed, empty working dir: no project CLAUDE.md or settings leak in, and
    chat sessions can be resumed (Claude Code stores them per working dir)."""
    d = config.settings().db_path.parent / "claude-code"
    d.mkdir(parents=True, exist_ok=True)
    return d


def claude_code(prompt: str, system: str, *, model: str, effort: str = "high",
                schema: dict | None = None, resume: str | None = None,
                persist: bool = False) -> dict[str, Any]:
    """Run `claude -p` once and return its JSON result. The prompt goes over
    stdin (no command-line length limit); no tools, no MCP servers."""
    exe = config.find_claude_code()
    if exe is None:
        raise RuntimeError("Claude Code not found. Install the Claude desktop app or set CLAUDE_CODE_PATH in .env")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(system)
        sys_file = f.name
    cmd = [str(exe), "-p", "--output-format", "json", "--system-prompt-file", sys_file,
           "--tools", "", "--strict-mcp-config", "--model", model, "--effort", effort]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    if resume:
        cmd += ["--resume", resume]
    if not persist and not resume:
        cmd.append("--no-session-persistence")
    env = {k: v for k, v in os.environ.items() if k not in _API_ENV}
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              env=env, cwd=_cc_workdir(), timeout=CC_TIMEOUT_S)
    finally:
        os.unlink(sys_file)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude Code failed (exit {proc.returncode}): "
                           f"{(proc.stderr or proc.stdout).strip()[:300]}") from None
    if out.get("is_error") or out.get("subtype") != "success":
        raise RuntimeError(f"Claude Code error: {str(out.get('result') or out.get('subtype'))[:300]}")
    if out.get("stop_reason") == "refusal":
        raise CoachRefusal("model declined")
    return out


def _system_text(prompt_file: str) -> str:
    return "\n\n".join(b["text"] for b in _system(prompt_file))


# --- API backend ---------------------------------------------------------------

def _client() -> anthropic.Anthropic:
    if not config.settings().has_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    return anthropic.Anthropic()


def _system(prompt_file: str) -> list[dict[str, Any]]:
    """Stable, cacheable prefix: instructions + principles + goals (raw text, as the athlete wrote it)."""
    principles = (config.ROOT / "coaching_principles.md").read_text(encoding="utf-8")
    goals = (config.ROOT / "goals.yaml").read_text(encoding="utf-8")
    return [
        {"type": "text", "text": (PROMPTS / prompt_file).read_text(encoding="utf-8")},
        {"type": "text", "text": f"<coaching_principles>\n{principles}\n</coaching_principles>"},
        {"type": "text", "text": f"<goals_yaml>\n{goals}\n</goals_yaml>",
         "cache_control": {"type": "ephemeral"}},
    ]


def _check(resp: Any) -> None:
    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        raise CoachRefusal(f"model declined ({getattr(details, 'category', None)})")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("model output was cut off (max_tokens)")
    usage = resp.usage
    log.debug("tokens in=%s cached=%s out=%s", usage.input_tokens,
              getattr(usage, "cache_read_input_tokens", None), usage.output_tokens)


def _slim_metrics(m: dict[str, Any]) -> dict[str, Any]:
    """The model needs trends, not every daily EWMA point."""
    out = dict(m)
    load = dict(m["load"])
    load["ewma_weekly"] = load.pop("ewma", [])[6::7]
    out["load"] = load
    return out


def weekly_narrative(metrics: dict[str, Any], goals: dict, previous: list[dict], *,
                     model: str | None = None) -> dict[str, Any]:
    payload = {
        "metrics": _slim_metrics(metrics),
        "athlete_notes_this_week": metrics.get("notes", []),
        "previous_reports": previous,
    }
    user = ("Write this week's coaching narrative from this data.\n\n"
            f"<week_data>\n{json.dumps(payload, default=str)}\n</week_data>")
    s = config.settings()
    if s.backend == "claude-code":
        out = claude_code(user, _system_text("weekly_report.md"), model=model or s.model,
                          effort="high", schema=NARRATIVE_SCHEMA)
        if not isinstance(out.get("structured_output"), dict):
            raise RuntimeError("Claude Code returned no structured output")
        return out["structured_output"]

    resp = _client().beta.messages.create(
        model=model or s.model,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=_system("weekly_report.md"),
        output_config={"effort": "high",
                       "format": {"type": "json_schema", "schema": NARRATIVE_SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    _check(resp)
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def chat_context(digest: dict[str, Any], latest_report: dict | None, notes: list[dict]) -> str:
    report = None
    if latest_report:
        report = {"week": latest_report["week"], "narrative": latest_report.get("narrative"),
                  "headline": (latest_report.get("metrics") or {}).get("headline"),
                  "recovery": (latest_report.get("metrics") or {}).get("recovery"),
                  "key_workouts": (latest_report.get("metrics") or {}).get("key_workouts")}
    return ("<training_digest>\n" + json.dumps(digest, default=str) + "\n</training_digest>\n"
            "<latest_report>\n" + json.dumps(report, default=str) + "\n</latest_report>\n"
            "<recent_notes>\n" + json.dumps(notes, default=str) + "\n</recent_notes>")


class Chat:
    """Multi-turn coach chat. The data context is sent once as the first user
    turn and the history is append-only, so every later turn hits the cache."""

    def __init__(self, context: str, model: str | None = None):
        s = config.settings()
        self.backend = s.backend
        self.client = _client() if self.backend == "api" else None
        self.model = model or s.chat_model
        self.system = _system("chat.md")
        self.messages: list[dict[str, Any]] = []
        self.session_id: str | None = None
        self._context = context

    def ask(self, question: str) -> Iterator[str]:
        if self.backend == "claude-code":
            # first turn carries the data context; later turns resume the same session
            prompt = question if self.session_id else f"{self._context}\n\n{question}"
            out = claude_code(prompt, _system_text("chat.md"), model=self.model, effort="medium",
                              resume=self.session_id, persist=True)
            self.session_id = out.get("session_id") or self.session_id
            yield out.get("result") or ""
            return

        content = question if self.messages else f"{self._context}\n\n{question}"
        self.messages.append({"role": "user", "content": content})
        with self.client.beta.messages.stream(
            model=self.model, max_tokens=16000, betas=[FALLBACK_BETA], fallbacks="default",
            system=self.system, output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"}, messages=self.messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
        _check(final)
        # keep the full content (incl. thinking blocks) so history stays valid
        self.messages.append({"role": "assistant", "content": final.content})
