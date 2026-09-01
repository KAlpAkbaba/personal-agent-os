"""Shared pytest fixtures."""

from collections.abc import Callable

import pytest

from app.config import Settings
from tests.identity_support import authenticate


@pytest.fixture()
def settings() -> Settings:
    """Settings built from environment/dev-defaults (compose loopback ports)."""
    return Settings()


@pytest.fixture()
def owner_auth() -> Callable[..., object]:
    """M9: `owner_auth(app, client)` bootstraps identity and authenticates.

    Every endpoint except health now requires an owner bearer session, so this
    is the one shared way a test becomes the owner. It issues a *real* session
    through the *real* service — nothing about the authentication path is
    stubbed or overridden — so a test passing is evidence that the endpoint is
    protected and that a valid session gets through, not that auth was skipped.
    """
    return authenticate
