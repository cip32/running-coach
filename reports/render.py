"""Render WeekMetrics (+ optional LLM narrative) into Markdown and HTML."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from analysis import context as ctx
from analysis.weeks import parse_week
from reports import charts

TEMPLATES = Path(__file__).with_name("templates")


def _hms(secs: float | None) -> str:
    if secs is None:
        return "—"
    secs = round(secs)
    h, rem = divmod(secs, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


def _hours(h: float | None) -> str:
    if h is None:
        return "—"
    return f"{int(h)}h{round((h % 1) * 60):02d}"


def _signed(v: float | None, unit: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:+g}{unit}"


def _num(v: Any, nd: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else str(v)


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined,
                      trim_blocks=True, lstrip_blocks=True, autoescape=False)
    env.filters.update(hms=_hms, hours=_hours, signed=_signed, num=_num)
    return env


def fallback_narrative(m: dict[str, Any]) -> dict[str, Any]:
    """Facts-only summary used when no LLM is configured (or with --no-llm)."""
    h, rec, c = m["headline"], m["recovery"], m["context"]
    vol = c["volume"]
    share = m["intensity"]["share"]
    bullets = [
        f"{h['km']} km in {h['runs']} runs ({_signed(h['vs_last_week']['km']['abs'], ' km')} vs last week).",
        f"Ramp target this week was {_num(vol['ramp_target_km'], 1)} km ({_signed(vol['vs_ramp_km'], ' km')}); "
        f"{_num(vol['pct_of_goal'])}% of the 100 km goal.",
        f"Intensity: {share['easy'] * 100:.0f}% easy, {share['steady'] * 100:.0f}% steady, "
        f"{(share['threshold'] + share['hard']) * 100:.0f}% threshold or harder.",
        f"Sleep {_num(rec['sleep']['avg_h'], 1)} h/night (score {_num(rec['sleep']['avg_score'])}), "
        f"HRV {_num(rec['hrv']['avg'])} ms vs baseline {_num(rec['hrv']['baseline_low'])}–{_num(rec['hrv']['baseline_high'])}.",
        (f"Red flags: {len(rec['red_flags'])}." if rec["red_flags"] else "No recovery red flags."),
    ]
    actions = []
    if rec["red_flags"]:
        actions.append("Read the red flags in the Recovery section before planning next week.")
    if m["intensity"]["grey_zone_runs"]:
        actions.append("Cap easy runs below the steady HR floor (watch alert).")
    actions.append("Log how the week felt: `coach note \"...\"`.")
    while len(actions) < 3:
        actions.append("Add an API key to .env to get coaching commentary and a next-week plan.")
    return {"headline": None, "sessions": None, "key_workouts": None, "intensity": None, "load": None,
            "recovery": None, "context": None, "next_week": None,
            "bullets": bullets, "actions": actions[:3], "_fallback": True}


def ramp_by_week(goals: dict, weekly: list[dict]) -> dict[str, float | None]:
    return {w["week"]: ctx.target_km(goals, parse_week(w["week"])) for w in weekly}


def render(m: dict[str, Any], narrative: dict[str, Any] | None, goals: dict,
           out_dir: Path) -> tuple[Path, Path]:
    n = narrative or fallback_narrative(m)
    env = _env()
    data = {"m": m, "n": n, "goals": goals, "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "athlete": (goals.get("athlete") or {}).get("name") or "Athlete"}
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{m['week']}.md"
    html_path = out_dir / f"{m['week']}.html"
    md = env.get_template("report.md.j2").render(**data)
    # Jinja's trim_blocks eats the newline after inline tags; keep headings separated
    md = re.sub(r"\n{3,}", "\n\n", re.sub(r"(?<!\n)\n(#{1,3} |---\n)", r"\n\n\1", md))
    md_path.write_text(md, encoding="utf-8")
    imgs = charts.all_charts(m, ramp_by_week(goals, m["load"]["weekly_volume"]))
    html_path.write_text(env.get_template("report.html.j2").render(**data, charts=imgs), encoding="utf-8")
    return md_path, html_path
