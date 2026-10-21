import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.store import Store  # noqa: E402


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()
