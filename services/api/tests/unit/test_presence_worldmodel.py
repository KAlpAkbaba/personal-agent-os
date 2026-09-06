"""Unit tests: the M18 Presence Engine feeding the World Model
(M18_HOLOGRAPHIC_CORE_SPEC.md's presence integration section;
ADR-0053's four truth kinds).

Only ``app.ledger.models.ActivityEventRow`` is created here: every other
World Model section degrades to an ``Uncertainty`` on a missing table
(``app.worldmodel.state._Collector.section`` catches exactly this), which is
precisely the behaviour these tests rely on to stay focused on presence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.presence.engine import PresenceFusionEngine
from app.presence.eye import disable_eye, enable_eye
from app.presence.observations import Observation
from app.worldmodel.state import TruthKind, assemble_snapshot

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


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


def _observation(*, t: float, **overrides) -> Observation:
    defaults = dict(
        person_present=True,
        presence_confidence=0.9,
        activity_level="medium",
        posture="upright",
        awake_state="awake",
        observed_at=NOW + timedelta(seconds=t),
        source="camera",
    )
    defaults.update(overrides)
    return Observation(**defaults)


def test_no_presence_runtime_supplied_reports_no_presence_facts(session) -> None:
    snapshot = assemble_snapshot(session, now=NOW, presence_runtime=None)
    assert not [f for f in snapshot.facts if f.key.startswith("owner.")]
    assert any(u.subject == "owner.presence" for u in snapshot.uncertainties)


def test_camera_state_is_evidence_truth_and_enabled_by_default(session) -> None:
    snapshot = assemble_snapshot(session, now=NOW, presence_runtime=None)
    camera_fact = next(f for f in snapshot.facts if f.key == "device.camera_state")
    assert camera_fact.truth_kind == TruthKind.EVIDENCE
    assert camera_fact.value == "enabled"


def test_camera_state_reflects_a_durable_disable(session) -> None:
    disable_eye(session, reason="owner asked")
    snapshot = assemble_snapshot(session, now=NOW, presence_runtime=None)
    camera_fact = next(f for f in snapshot.facts if f.key == "device.camera_state")
    assert camera_fact.value == "disabled"

    enable_eye(session, reason="owner asked again")
    snapshot2 = assemble_snapshot(session, now=NOW, presence_runtime=None)
    camera_fact2 = next(f for f in snapshot2.facts if f.key == "device.camera_state")
    assert camera_fact2.value == "enabled"


def test_a_live_presence_engine_reports_runtime_truth_facts(session) -> None:
    engine = PresenceFusionEngine()
    engine.add_observation(_observation(t=0), now=NOW)
    engine.add_observation(_observation(t=25), now=NOW + timedelta(seconds=25))

    snapshot = assemble_snapshot(session, now=NOW + timedelta(seconds=25), presence_runtime=engine)

    presence_fact = next(f for f in snapshot.facts if f.key == "owner.presence")
    assert presence_fact.truth_kind == TruthKind.RUNTIME
    assert presence_fact.value == "present"
    assert presence_fact.confidence > 0.0
    assert presence_fact.stale is False

    awake_state_fact = next(f for f in snapshot.facts if f.key == "owner.awake_state")
    assert awake_state_fact.value == "awake"

    activity_fact = next(f for f in snapshot.facts if f.key == "owner.activity_level")
    assert activity_fact.value == "medium"

    last_seen_fact = next(f for f in snapshot.facts if f.key == "owner.last_seen")
    assert last_seen_fact.value is not None


def test_a_stale_presence_assertion_is_reported_stale_using_its_own_ttl(session) -> None:
    """Presence uses its OWN per-source TTL (app.presence.engine.FusionPolicy
    .ttl_s), which can differ from the World Model's generic 15-minute
    RUNTIME_TRUTH default (app.worldmodel.state.STALE_AFTER) - the assertion
    itself is the authority on whether it is stale, passed through as an
    explicit floor rather than re-derived."""
    engine = PresenceFusionEngine()
    engine.add_observation(_observation(t=0, source="camera"), now=NOW)
    engine.add_observation(_observation(t=25, source="camera"), now=NOW + timedelta(seconds=25))

    # Camera's default TTL is 90s; ask for a snapshot well past it.
    far_future = NOW + timedelta(seconds=25 + 400)
    snapshot = assemble_snapshot(session, now=far_future, presence_runtime=engine)
    presence_fact = next(f for f in snapshot.facts if f.key == "owner.presence")
    assert presence_fact.stale is True


def test_no_observations_yet_is_an_honest_uncertainty_not_a_guess(session) -> None:
    engine = PresenceFusionEngine()
    snapshot = assemble_snapshot(session, now=NOW, presence_runtime=engine)
    assert not [f for f in snapshot.facts if f.key == "owner.presence"]
    assert any(u.subject == "owner.presence" for u in snapshot.uncertainties)


def test_an_open_camera_is_not_evidence_of_presence(session) -> None:
    """The first real M18 run, 2026-09-06: the eye was on and the Core said
    "Sahip durumu bilinmiyor" - and that half was CORRECT. "camera enabled" is
    an evidence fact about a device; "the owner is present" is a runtime
    inference that needs observations. With the eye on and nothing observed the
    World Model must carry the first and refuse the second, by name."""
    enable_eye(session, reason="owner_start")
    engine = PresenceFusionEngine()
    snapshot = assemble_snapshot(session, now=NOW, presence_runtime=engine)

    camera = next(f for f in snapshot.facts if f.key == "device.camera_state")
    assert camera.value == "enabled"
    assert camera.truth_kind is TruthKind.EVIDENCE

    assert not [f for f in snapshot.facts if f.key == "owner.presence"]
    reasons = {u.reason for u in snapshot.uncertainties if u.subject == "owner.presence"}
    assert reasons == {"no_observations_yet"}
