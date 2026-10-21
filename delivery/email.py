"""Email delivery (SMTP + STARTTLS). Used only when DELIVERY_EMAIL=true."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any


def send(result: dict[str, Any]) -> None:
    host, to, sender = os.getenv("SMTP_HOST"), os.getenv("EMAIL_TO"), os.getenv("EMAIL_FROM")
    if not (host and to and sender):
        raise RuntimeError("set SMTP_HOST, EMAIL_FROM and EMAIL_TO in .env")
    html = Path(result["html"]).read_text(encoding="utf-8")
    md = Path(result["md"]).read_text(encoding="utf-8")
    msg = EmailMessage()
    msg["Subject"] = f"Training report {result['week']}"
    msg["From"], msg["To"] = sender, to
    msg.set_content(md)
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(md.encode("utf-8"), maintype="text", subtype="markdown",
                       filename=f"{result['week']}.md")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT") or 587), timeout=30) as s:
        s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD") or "")
        s.send_message(msg)
