import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.store import Store  # noqa: E402


@pytest.fixture(autouse=True)
def api_backend(monkeypatch):
    """Tests default to the API backend (with fake clients); Claude Code tests opt in."""
    monkeypatch.setenv("COACH_BACKEND", "api")


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()
