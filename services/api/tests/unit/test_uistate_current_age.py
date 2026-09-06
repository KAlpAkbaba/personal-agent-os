"""GET /v1/ui/state says how old its current event is, and what "no current" means.

Owner M18 eye run, 2026-09-06: right after a release the harness read an EMPTY current
state and could not tell "nothing since startup" from "never anything"; earlier it compared
a server timestamp with its own clock. The route now stamps ``now`` and ``current_age_s``
against the server clock, and the lifespan publishes a truthful ``agent.idle`` at startup
(``app.main``) so the first current event after a restart is a fact, never silence.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.uistate import UiState
from app.uistate.contract import UiStateEvent
from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher
from app.uistate.routes import get_state


def _with_bus(fn):
    previous = get_publisher()
    bus = UiStatePublisher()
    set_publisher(bus)
    try:
        return fn(bus)
    finally:
        set_publisher(previous)


def test_no_current_is_reported_as_null_with_a_null_age() -> None:
    def run(bus):
        return asyncio.run(get_state(after_sequence=0, limit=8))

    doc = _with_bus(run)
    assert doc["current"] is None
    assert doc["current_age_s"] is None
    assert doc["now"].endswith("Z")
    assert doc["sequence"] == 0


def test_current_age_is_measured_against_the_server_clock() -> None:
    def run(bus):
        bus.publish(
            UiStateEvent(
                state=UiState.LISTENING,
                subsystem="voice",
                at=datetime.now(UTC) - timedelta(seconds=90),
                session_id="s-1",
            )
        )
        return asyncio.run(get_state(after_sequence=0, limit=8))

    doc = _with_bus(run)
    assert doc["current"]["state"] == "agent.listening"
    assert doc["current"]["session_id"] == "s-1"
    assert 89 <= doc["current_age_s"] <= 95
    assert doc["now"].endswith("Z")


def test_the_startup_idle_is_a_system_fact_not_a_voice_state() -> None:
    """What app.main publishes first: idle, from the system, with a startup reason -
    never agent.listening before any realtime session exists."""
    from app.uistate import publish

    def run(bus):
        publish(
            UiState.IDLE,
            subsystem="system",
            intensity=0.0,
            status="startup",
            label="api_started",
            metadata={"reason": "api_started"},
        )
        return asyncio.run(get_state(after_sequence=0, limit=8))

    doc = _with_bus(run)
    current = doc["current"]
    assert current["state"] == "agent.idle"
    assert current["subsystem"] == "system"
    assert current["session_id"] is None
    assert current["metadata"] == {"reason": "api_started"}
    assert doc["current_age_s"] is not None and doc["current_age_s"] < 5
