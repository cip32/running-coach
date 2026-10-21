"""Claude API calls: weekly narrative (structured JSON) and chat.

Numbers come from analysis/; the model only interprets them. The stable part
of every request (instructions, coaching principles, goals) is a cached
system prefix, so repeated reports and chat turns are cheap.
"""

from __future__ import annotations

import json
import logging
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
    resp = _client().beta.messages.create(
        model=model or config.settings().model,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=_system("weekly_report.md"),
        output_config={"effort": "high",
                       "format": {"type": "json_schema", "schema": NARRATIVE_SCHEMA}},
        messages=[{"role": "user", "content":
                   "Write this week's coaching narrative from this data.\n\n"
                   f"<week_data>\n{json.dumps(payload, default=str)}\n</week_data>"}],
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
        self.client = _client()
        self.model = model or config.settings().chat_model
        self.system = _system("chat.md")
        self.messages: list[dict[str, Any]] = []
        self._context = context

    def ask(self, question: str) -> Iterator[str]:
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
