"""Shared pytest fixtures."""

from collections.abc import Callable

import pytest

from app.config import Settings
from tests.identity_support import authenticate


@pytest.fixture()
def settings() -> Settings:
    """Settings built from environment/dev-defaults (compose loopback ports)."""
    return Settings()


@pytest.fixture(autouse=True)
def _reset_browser_gateway_session_registry():
    """``DeviceBrowserGateway``'s known-open session registry (spec §5a) is
    process-wide by design (M13_RESEARCH_SPEC.md §5a) so it survives across
    the many gateway instances one research job constructs. Tests reuse the
    same device/task ids across cases, so without a reset the SECOND test to
    touch a given (device_id, session_id) would see it as "already open" and
    skip dispatching ``browser.session_open`` — reset it before (and after)
    every test so each test starts from a clean registry."""
    from app.research.browser_gateway import reset_known_open_sessions

    reset_known_open_sessions()
    yield
    reset_known_open_sessions()


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
