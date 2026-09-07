"""Disabling the Active Eye invalidates the camera evidence NOW
(docs/M18_ACTION_CONTRACT.md §5.4; tests §8).

A presence claim fused from frames the owner just forbade must not outlive the command
by its TTL: the camera observations leave the window, the assertion is UNKNOWN with reason
``eye_disabled``, the World Model says so by name, the bus hears it once, the heartbeat
forgets. Enabling the eye again asserts nothing - only observations can.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_EYE_DISABLED, EVENT_TYPE_PRESENCE_STATE_CHANGED
from app.presence import service as presence_service
from app.presence.engine import PresenceFusionEngine, get_engine, set_engine
from app.presence.eye import disable_eye, enable_eye, is_eye_enabled
from app.presence.observations import Observation
from app.presence.states import PresenceState
from app.uistate.publisher import UiStatePublisher, get_publisher, set_publisher
from app.worldmodel.state import assemble_snapshot

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture()
def engine():
    previous = get_engine()
    eng = PresenceFusionEngine()
    set_engine(eng)
    presence_service.reset_heartbeat()
    try:
        yield eng
    finally:
        set_engine(previous)
        presence_service.reset_heartbeat()


@pytest.fixture()
def bus():
    previous = get_publisher()
    publisher = UiStatePublisher()
    set_publisher(publisher)
    try:
        yield publisher
    finally:
        set_publisher(previous)


def _observation(*, t: float, source: str = "camera", **overrides) -> Observation:
    defaults = dict(
        person_present=True,
        presence_confidence=0.9,
        activity_level="medium",
        posture="upright",
        awake_state="awake",
        observed_at=NOW + timedelta(seconds=t),
        source=source,
    )
    defaults.update(overrides)
    return Observation(**defaults)


def _see_owner(engine: PresenceFusionEngine) -> None:
    engine.add_observation(_observation(t=0), now=NOW)
    engine.add_observation(_observation(t=25), now=NOW + timedelta(seconds=25))
    assert engine.current().state is PresenceState.PRESENT


def test_disable_drops_camera_evidence_and_degrades_to_unknown(session, engine, bus) -> None:
    _see_owner(engine)
    assert engine.last_observation_at(source="camera") is not None

    changed = disable_eye(session, reason="voice:gözünü kapat")
    assert changed is True

    current = engine.current()
    assert current.state is PresenceState.UNKNOWN
    assert current.reason == "eye_disabled"
    assert current.signals == ()
    assert engine.last_observation_at(source="camera") is None
    assert engine.episodes()[-1].state is PresenceState.UNKNOWN

    # the World Model: camera disabled, and the presence uncertainty says why
    snapshot = assemble_snapshot(session, now=NOW + timedelta(seconds=30), presence_runtime=engine)
    camera = next(f for f in snapshot.facts if f.key == "device.camera_state")
    assert camera.value == "disabled"
    assert not [f for f in snapshot.facts if f.key == "owner.presence"]
    reasons = {u.reason for u in snapshot.uncertainties if u.subject == "owner.presence"}
    assert reasons == {"eye_disabled"}

    # the bus: eye.disabled (the flag) and the degraded presence, published once
    events = [e for e in bus.tail() if e.state.value == "eye.disabled"]
    assert len(events) == 2
    invalidated = [e for e in events if e.status == "presence_invalidated"]
    assert len(invalidated) == 1
    assert invalidated[0].metadata["presence"] == "unknown"
    assert invalidated[0].metadata["reason"] == "eye_disabled"
    assert not [e for e in bus.tail() if e.state.value.startswith("owner.")][1:]


def test_disable_is_idempotent_and_invalidates_only_on_a_real_change(session, engine, bus) -> None:
    assert disable_eye(session, reason="first") is True
    assert disable_eye(session, reason="second") is False
    assert enable_eye(session, reason="third") is True
    assert enable_eye(session, reason="fourth") is False
    rows = ledger_service.query(session, event_types=[EVENT_TYPE_EYE_DISABLED])
    assert len(rows) == 1
    assert rows[0].detail_json["reason"] == "first"
    invalidations = [e for e in bus.tail() if e.status == "presence_invalidated"]
    assert len(invalidations) == 1


def test_other_sources_survive_and_the_next_observation_reclassifies(session, engine) -> None:
    engine.add_observation(_observation(t=0, source="input"), now=NOW)
    engine.add_observation(_observation(t=25, source="camera"), now=NOW + timedelta(seconds=25))
    disable_eye(session, reason="owner")
    assert engine.last_observation_at(source="input") is not None
    assert engine.last_observation_at(source="camera") is None
    assert engine.current().state is PresenceState.UNKNOWN
    # a later input observation forms a fresh opinion over what remains
    assertion, changed = engine.add_observation(
        _observation(t=60, source="input"), now=NOW + timedelta(seconds=60)
    )
    assert assertion.state is PresenceState.PRESENT
    assert changed is True


def test_heartbeat_forgets_the_held_state(session, engine) -> None:
    _see_owner(engine)
    held = engine.current()
    presence_service._note_published(held)
    assert presence_service.heartbeat_due(held, now=NOW + timedelta(seconds=25)) is False
    disable_eye(session, reason="owner")
    # the next real state is published at once rather than waiting out half a TTL
    assert presence_service.heartbeat_due(held, now=NOW + timedelta(seconds=26)) is True


def test_enable_asserts_no_presence(session, engine) -> None:
    disable_eye(session, reason="owner")
    assert enable_eye(session, reason="voice:gözünü aç") is True
    assert is_eye_enabled(session) is True
    assert engine.current().state is PresenceState.UNKNOWN
    snapshot = assemble_snapshot(session, now=NOW + timedelta(seconds=30), presence_runtime=engine)
    assert not [f for f in snapshot.facts if f.key == "owner.presence"]
    reasons = {u.reason for u in snapshot.uncertainties if u.subject == "owner.presence"}
    assert reasons == {"eye_disabled"}  # the assertion's own reason: nothing new was seen
    assert not ledger_service.query(session, event_types=[EVENT_TYPE_PRESENCE_STATE_CHANGED])


def test_an_unknown_assertion_is_an_uncertainty_not_a_fact(session) -> None:
    """UNKNOWN is the absence of a claim; the World Model must not carry it as a value."""
    engine = PresenceFusionEngine()
    engine.add_observation(_observation(t=0), now=NOW)  # one reading: not classifiable
    assert engine.current().state is PresenceState.UNKNOWN
    snapshot = assemble_snapshot(session, now=NOW, presence_runtime=engine)
    assert not [f for f in snapshot.facts if f.key == "owner.presence"]
    reasons = {u.reason for u in snapshot.uncertainties if u.subject == "owner.presence"}
    assert reasons == {"insufficient_observations"}


def test_two_eye_writes_inside_one_clock_tick_still_order_by_write(
    session, engine, bus, monkeypatch
) -> None:
    """Windows resolves the clock in ~1-15 ms; two commands inside one tick used to tie on
    the ledger's ordering and the flag read the OLDER row (a real flake, 2026-09-07). The
    write clock is strictly increasing: the later write is the later state."""
    from datetime import UTC as _utc
    from datetime import datetime as _dt

    from app.presence import eye as eye_module

    frozen = _dt(2026, 9, 7, 22, 0, 0, tzinfo=_utc)

    class FrozenDateTime(_dt):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001 - the datetime signature
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(eye_module, "datetime", FrozenDateTime)
    assert disable_eye(session, reason="first") is True
    assert disable_eye(session, reason="second") is False
    assert enable_eye(session, reason="third") is True
    assert enable_eye(session, reason="fourth") is False
    assert disable_eye(session, reason="fifth") is True
    latest = eye_module.latest_eye_event(session)
    assert latest is not None and latest.event_type == "eye.disabled"
    assert latest.occurred_at.replace(tzinfo=_utc) > frozen

