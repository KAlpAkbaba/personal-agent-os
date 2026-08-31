"""Integration test fixtures. These tests require the local compose stack:

    powershell -File scripts/dev-up.ps1   (from repo root)

Every test in this package is marked `integration`.
"""

import pytest

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items) -> None:
    for item in items:
        item.add_marker(pytest.mark.integration)
