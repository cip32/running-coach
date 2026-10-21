"""Notion delivery: creates a page per report under NOTION_PARENT_PAGE_ID.
Used only when DELIVERY_NOTION=true. Needs an internal integration token
shared with the parent page."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.notion.com/v1/pages"
VERSION = "2022-06-28"


def _rt(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text[i:i + 1900]}} for i in range(0, len(text), 1900)] or \
        [{"type": "text", "text": {"content": ""}}]


def md_to_blocks(md: str) -> list[dict]:
    """Small Markdown -> Notion blocks mapping (headings, bullets, numbers, paragraphs; tables as code)."""
    blocks, table = [], []

    def flush_table() -> None:
        if table:
            blocks.append({"type": "code", "code": {"language": "markdown", "rich_text": _rt("\n".join(table))}})
            table.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush_table()
        s = line.strip()
        if not s or s == "---":
            continue
        if s.startswith("## "):
            blocks.append({"type": "heading_2", "heading_2": {"rich_text": _rt(s[3:])}})
        elif s.startswith("# "):
            blocks.append({"type": "heading_1", "heading_1": {"rich_text": _rt(s[2:])}})
        elif s.startswith("- "):
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rt(s[2:])}})
        elif s[:2].rstrip(".").isdigit() and ". " in s[:4]:
            blocks.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": _rt(s.split(". ", 1)[1])}})
        else:
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": _rt(s.replace("**", ""))}})
    flush_table()
    return blocks


def publish(result: dict[str, Any]) -> str:
    token, parent = os.getenv("NOTION_TOKEN"), os.getenv("NOTION_PARENT_PAGE_ID")
    if not (token and parent):
        raise RuntimeError("set NOTION_TOKEN and NOTION_PARENT_PAGE_ID in .env")
    blocks = md_to_blocks(Path(result["md"]).read_text(encoding="utf-8"))
    body = {"parent": {"page_id": parent},
            "properties": {"title": {"title": _rt(f"Training report {result['week']}")}},
            "children": blocks[:100]}            # Notion caps children per request at 100
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("url", "")
