"""Optional delivery. Every channel is OFF unless explicitly enabled in .env;
the report is always written locally first."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def deliver(result: dict[str, Any], settings: Any) -> None:
    if settings.delivery_email:
        from delivery.email import send
        try:
            send(result)
            print("  emailed (DELIVERY_EMAIL=true)")
        except Exception as e:
            print(f"  email delivery failed: {e}")
    if settings.delivery_notion:
        from delivery.notion import publish
        try:
            url = publish(result)
            print(f"  published to Notion (DELIVERY_NOTION=true): {url}")
        except Exception as e:
            print(f"  Notion delivery failed: {e}")
