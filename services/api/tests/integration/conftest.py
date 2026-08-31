"""Integration test fixtures. These tests require the local compose stack:

    powershell -File scripts/dev-up.ps1   (from repo root)

Every test in this package is marked `integration`.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items) -> None:
    for item in items:
        item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Ensure the schema is at head before any integration test runs."""
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")
