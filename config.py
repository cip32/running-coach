"""Settings from .env and goals from goals.yaml.

Secrets are read from the environment only and are never printed or logged.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _path(name: str, default: str) -> Path:
    p = Path(os.path.expanduser(os.getenv(name) or default))
    return p if p.is_absolute() else ROOT / p


# Where the Claude desktop app and the VS Code extension keep their Claude Code
# binary; app updates have moved it before, so every known layout is searched.
_BUNDLED_CLAUDE_CODE = (
    ("APPDATA", "Claude/claude-code/*/*/claude.exe"),
    ("LOCALAPPDATA", "Claude-Data/*/claude-code/*/*/claude.exe"),
    ("USERPROFILE", ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe"),
)


def _path_version(p: Path) -> tuple[int, ...]:
    found = re.findall(r"(\d+)\.(\d+)\.(\d+)", str(p))
    return tuple(int(x) for x in found[-1]) if found else (0,)


def find_claude_code() -> Path | None:
    """Claude Code CLI: CLAUDE_CODE_PATH, then PATH, then the newest bundled copy
    (desktop app or VS Code extension)."""
    explicit = os.getenv("CLAUDE_CODE_PATH")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    on_path = shutil.which("claude")
    if on_path:
        return Path(on_path)
    candidates = [p for env, pattern in _BUNDLED_CLAUDE_CODE if os.getenv(env)
                  for p in Path(os.environ[env]).glob(pattern)]
    return max(candidates, key=_path_version) if candidates else None


@dataclass(frozen=True)
class Settings:
    backend: str                # "claude-code" (your Claude subscription) or "api" (API key + credits)
    model: str
    chat_model: str
    garmin_token_dir: Path
    sync_start: dt.date
    sync_refresh_days: int
    sync_throttle_s: float
    sync_fit: bool
    db_path: Path
    reports_dir: Path
    delivery_email: bool
    delivery_notion: bool

    @property
    def has_api_key(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    @property
    def llm_available(self) -> bool:
        return find_claude_code() is not None if self.backend == "claude-code" else self.has_api_key


def settings() -> Settings:
    model = os.getenv("COACH_MODEL") or "claude-opus-5-5"
    return Settings(
        backend=(os.getenv("COACH_BACKEND") or "claude-code").strip().lower(),
        model=model,
        chat_model=os.getenv("COACH_CHAT_MODEL") or model,
        garmin_token_dir=_path("GARMIN_TOKEN_DIR", "~/.garminconnect"),
        sync_start=dt.date.fromisoformat(os.getenv("SYNC_START_DATE") or "2025-01-01"),
        sync_refresh_days=int(os.getenv("SYNC_REFRESH_DAYS") or 3),
        sync_throttle_s=float(os.getenv("SYNC_THROTTLE_S") or 0.4),
        sync_fit=_bool("SYNC_FIT"),
        db_path=_path("COACH_DB", "data/coach.db"),
        reports_dir=_path("REPORTS_DIR", "reports/out"),
        delivery_email=_bool("DELIVERY_EMAIL"),
        delivery_notion=_bool("DELIVERY_NOTION"),
    )


def load_goals(path: Path | None = None) -> dict[str, Any]:
    goals = yaml.safe_load((path or ROOT / "goals.yaml").read_text(encoding="utf-8"))
    for race in goals.get("races") or []:
        race["date"] = _date(race["date"])
    for phase in goals.get("phases") or []:
        phase["start"], phase["end"] = _date(phase["start"]), _date(phase["end"])
    vol = goals.get("volume") or {}
    if vol.get("target_by"):
        vol["target_by"] = _date(vol["target_by"])
    return goals


def _date(v: Any) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def tz(goals: dict | None = None) -> ZoneInfo:
    name = ((goals or {}).get("athlete") or {}).get("timezone") or "Europe/Berlin"
    return ZoneInfo(name)


def pace_to_s(pace: str) -> int:
    """'3:54' -> 234 seconds per km."""
    m, s = pace.split(":")
    return int(m) * 60 + int(s)


def time_to_s(t: str) -> int:
    """'2:45:00' or '17:50' -> seconds."""
    parts = [int(p) for p in str(t).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


# --- secret redaction for every log handler -------------------------------

_SECRET_RE = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]+|Bearer\s+[A-Za-z0-9._\-]+|"
    r"(?i:(password|token|secret|api[_-]?key)\s*[=:]\s*)\S+)"
)


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # format first, then redact the final text, so %-placeholders and
        # numeric args can't be broken by the substitution
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg, record.args = _SECRET_RE.sub("[REDACTED]", message), None
        return True


def setup_logging(verbose: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactSecrets())
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=[handler], force=True)
    # third-party libraries can log request details; keep them quiet
    for noisy in ("garminconnect", "garth", "urllib3", "httpx", "anthropic", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
