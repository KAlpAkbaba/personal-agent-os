"""Shared pytest fixtures."""

import pytest

from app.config import Settings


@pytest.fixture()
def settings() -> Settings:
    """Settings built from environment/dev-defaults (compose loopback ports)."""
    return Settings()
