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


@pytest.fixture(autouse=True)
def _reset_operator_service_registry():
    """``app.operator.service``'s module-wide registry (mirrors
    ``app.devices.commands.register_broker_runtime``) must not leak a "task running"
    state across tests that never touch it (docs/M19_DIGITAL_OPERATOR_SPEC.md §4): a test
    file with its own hand-rolled realtime runtime and no operator of its own must see
    ``operator_running=False`` at the router, never whatever a PRECEDING test's operator
    happened to be doing. A fixture that wants the real thing (the corpus harness,
    ``test_operator_wiring.py``) registers its own after this reset runs."""
    from app.operator.service import register_operator_service

    register_operator_service(None)
    yield
    register_operator_service(None)


@pytest.fixture(autouse=True)
def _reset_route_telemetry():
    """B26 req 749: the router's telemetry ring is module-wide for the same reason the
    operator registry is — `record_client_events` is a function, not a service object. A
    ring that survived a test would let one test's resolutions pair with the next test's
    "dur" and report a misroute nobody caused."""
    from app.voice.route_telemetry import reset_telemetry

    reset_telemetry()
    yield
    reset_telemetry()


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
