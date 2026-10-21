"""metrics -> (optional) LLM narrative -> Markdown/HTML -> DB record."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from analysis.metrics import build_week
from db.store import Store
from reports.render import render

log = logging.getLogger(__name__)


def generate(store: Store, goals: dict, monday: dt.date, today: dt.date, out_dir: Path, *,
             use_llm: bool, model: str | None = None) -> dict[str, Any]:
    m = build_week(store, goals, monday, today=today)
    narrative = None
    if use_llm:
        from llm import weekly_narrative
        previous = [{"week": r["week"], "bullets": (r["narrative"] or {}).get("bullets"),
                     "next_week": (r["narrative"] or {}).get("next_week")}
                    for r in store.recent_reports(4, before_week=m["week"])]
        try:
            narrative = weekly_narrative(m, goals, previous, model=model)
        except Exception as e:   # never lose the numbers because the LLM step failed
            log.warning("LLM step failed, rendering numbers only: %s", e)
            m["missing"].append(f"coaching commentary unavailable ({type(e).__name__})")
    md, html = render(m, narrative, goals, out_dir)
    store.save_report(m["week"], str(md), str(html), m, narrative)
    return {"week": m["week"], "md": md, "html": html, "metrics": m, "narrative": narrative}
