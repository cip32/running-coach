from types import SimpleNamespace

import delivery
from delivery.notion import md_to_blocks


def test_nothing_sent_unless_opted_in(monkeypatch):
    sent = []
    monkeypatch.setattr("delivery.email.send", lambda r: sent.append("email"))
    monkeypatch.setattr("delivery.notion.publish", lambda r: sent.append("notion"))
    delivery.deliver({"week": "2026-W43"}, SimpleNamespace(delivery_email=False, delivery_notion=False))
    assert sent == []
    delivery.deliver({"week": "2026-W43"}, SimpleNamespace(delivery_email=True, delivery_notion=False))
    assert sent == ["email"]


def test_defaults_are_off():
    import config
    example = (config.ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DELIVERY_EMAIL=false" in example and "DELIVERY_NOTION=false" in example


def test_markdown_to_notion_blocks():
    blocks = md_to_blocks("# Title\n\n## 1. Week\n| a | b |\n|---|---|\n- bullet\n1. first\nplain **bold**")
    kinds = [b["type"] for b in blocks]
    assert kinds == ["heading_1", "heading_2", "code", "bulleted_list_item", "numbered_list_item", "paragraph"]
    assert blocks[-1]["paragraph"]["rich_text"][0]["text"]["content"] == "plain bold"
