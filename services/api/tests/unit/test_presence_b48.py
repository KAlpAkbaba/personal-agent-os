"""B48 - presence that reasons without a camera where it honestly can (ADR-0155).

* 310: an observation's weight fades across its trust lifespan;
* 312/313: the sleep threshold follows the owner's own quiet hours;
* 330: the presence history route;
* 301: a closed camera tab is recorded as evidence, never as a disable.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.config import Settings
from app.presence.engine import (
    DEFAULT_POLICY,
    PresenceFusionEngine,
    _base_confidence,
    _freshness_weight,
    _inside_quiet_hours,
    _time_of_day_policy,
    classify_window,
)
from app.presence.observations import parse_observation
from app.presence.states import PresenceState

#: 23:30 Europe/Istanbul is 20:30 UTC; 15:00 Istanbul is 12:00 UTC.
NIGHT = datetime(2026, 9, 14, 20, 30, tzinfo=UTC)
AFTERNOON = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
QUIET = {"start": "23:00", "end": "07:30", "timezone": "Europe/Istanbul"}


def _obs(
    at: datetime,
    *,
    source: str = "camera",
    posture: str = "resting",
    awake: str = "resting",
    activity: str = "none",
    confidence: float = 0.9,
    present: bool = True,
):
    return parse_observation(
        {
            "source": source,
            "observed_at": at.isoformat(),
            "person_present": present,
            "presence_confidence": confidence,
            "activity_level": activity,
            "posture": posture,
            "awake_state": awake,
        }
    )


# ------------------------------------------------------------------------------ 310


def test_a_fresh_observation_counts_more_than_an_old_one() -> None:
    now = AFTERNOON
    fresh = _obs(now, source="input", confidence=0.8)
    old = _obs(now - timedelta(minutes=4), source="input", confidence=0.8)
    assert _freshness_weight(fresh, now=now, policy=DEFAULT_POLICY) == 1.0
    assert 0.5 <= _freshness_weight(old, now=now, policy=DEFAULT_POLICY) < 0.7
    sure_now = _obs(now, source="input", confidence=0.9)
    unsure_now = _obs(now, source="input", confidence=0.5)
    sure_before = _obs(now - timedelta(minutes=4), source="input", confidence=0.9)
    unsure_before = _obs(now - timedelta(minutes=4), source="input", confidence=0.5)
    fresh_leads = _base_confidence([sure_now, unsure_before], 1.0, now=now, policy=DEFAULT_POLICY)
    old_leads = _base_confidence([unsure_now, sure_before], 1.0, now=now, policy=DEFAULT_POLICY)
    assert fresh_leads > old_leads
    # An evenly aged window is not less sure of itself: decay only shares the weight out.
    evenly_old = _base_confidence([old, old], 1.0, now=now, policy=DEFAULT_POLICY)
    assert evenly_old == _base_confidence([old, old], 1.0)


def test_no_decay_is_exactly_the_old_confidence() -> None:
    now = AFTERNOON
    old = _obs(now - timedelta(minutes=4), source="input", confidence=0.8)
    policy = replace(DEFAULT_POLICY, freshness_decay=0.0)
    assert _base_confidence([old, old], 1.0, now=now, policy=policy) == _base_confidence(
        [old, old], 1.0
    )


# ------------------------------------------------------------------------------ 312/313


def test_quiet_hours_are_read_in_their_own_timezone_across_midnight() -> None:
    assert _inside_quiet_hours(QUIET, NIGHT) is True
    assert _inside_quiet_hours(QUIET, AFTERNOON) is False
    assert _inside_quiet_hours(None, NIGHT) is None
    assert _inside_quiet_hours({"start": "25:00", "end": "07:00"}, NIGHT) is None


def test_outside_quiet_hours_a_rest_must_hold_longer_before_it_is_sleep() -> None:
    policy = replace(DEFAULT_POLICY, quiet_hours=QUIET)
    assert (
        _time_of_day_policy(policy, NIGHT).likely_asleep_after_s
        == DEFAULT_POLICY.likely_asleep_after_s
    )
    assert _time_of_day_policy(policy, AFTERNOON).likely_asleep_after_s == max(
        DEFAULT_POLICY.likely_asleep_after_s, DEFAULT_POLICY.likely_asleep_after_outside_quiet_s
    )
    # No window: the threshold is never changed by a missing preference.
    assert _time_of_day_policy(DEFAULT_POLICY, AFTERNOON) == DEFAULT_POLICY


def _rest_run(start: datetime, minutes: int, policy) -> PresenceState:
    engine = PresenceFusionEngine(policy=policy)
    state = PresenceState.UNKNOWN
    for step in range(0, minutes * 60 + 1, 30):
        at = start + timedelta(seconds=step)
        assertion, _ = engine.add_observation(_obs(at), now=at)
        state = assertion.state
    return state


def test_thirty_minutes_of_rest_is_sleep_at_night_and_only_rest_in_the_afternoon() -> None:
    policy = replace(DEFAULT_POLICY, quiet_hours=QUIET)
    assert _rest_run(NIGHT, 30, policy) is PresenceState.LIKELY_ASLEEP
    assert _rest_run(AFTERNOON, 30, policy) is PresenceState.RESTING


def test_the_owners_quiet_hours_reach_the_engine_on_ingest() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.alarms.models import AmbientPolicyRow
    from app.ambient import service as ambient_service
    from app.ledger.models import ActivityEventRow
    from app.presence import service as presence_service

    engine_db = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (AmbientPolicyRow.__table__, ActivityEventRow.__table__):
        table.create(engine_db)
    session = sessionmaker(bind=engine_db, expire_on_commit=False)()
    try:
        ambient_service.set_policy(session, {"quiet_hours": QUIET}, source="test")
        fusion = PresenceFusionEngine()
        presence_service.ingest_observation(
            session,
            {
                "source": "input",
                "observed_at": AFTERNOON.isoformat(),
                "person_present": True,
                "presence_confidence": 0.85,
                "activity_level": "high",
                "posture": "unknown",
                "awake_state": "awake",
            },
            engine=fusion,
            now=AFTERNOON,
        )
        assert fusion.policy.quiet_hours is not None
        assert fusion.policy.quiet_hours["start"] == "23:00"
    finally:
        session.close()
        engine_db.dispose()


def test_classify_window_uses_the_time_of_day_threshold() -> None:
    policy = replace(DEFAULT_POLICY, quiet_hours=QUIET)
    previous = None
    obs = []
    for step in range(0, 25 * 60 + 1, 60):
        at = AFTERNOON + timedelta(seconds=step)
        obs.append(_obs(at))
        previous = classify_window(obs[-20:], previous=previous, now=at, policy=policy)
    assert previous is not None and previous.state is PresenceState.RESTING


# ------------------------------------------------------------------------------ 330 + 301


def _client():
    """The presence routes' own fixture shape (tests/unit/test_presence_routes.py): identity
    and the ledger on in-memory SQLite, never create_app()'s real database."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.artifacts.runtime import ArtifactRuntime
    from app.ledger.models import ActivityEventRow
    from app.main import create_app
    from app.presence.engine import set_engine
    from tests.identity_support import authenticate

    set_engine(PresenceFusionEngine())
    settings = Settings(_env_file=None)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    runtime = ArtifactRuntime(settings)
    runtime._engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings)
    app.state.artifacts = runtime
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    return app, client


def test_the_history_route_lists_recorded_transitions_and_recent_episodes() -> None:
    _app, client = _client()
    body = client.get("/v1/presence/history", params={"limit": 5})
    assert body.status_code == 200, body.text
    payload = body.json()
    assert isinstance(payload["transitions"], list) and isinstance(payload["episodes"], list)
    assert client.get("/v1/presence/history", params={"limit": 0}).status_code == 422


def test_a_closed_camera_tab_is_evidence_and_never_withdraws_consent() -> None:
    _app, client = _client()
    client.post("/v1/presence/eye/enable", json={"reason": "owner_start"})
    assert client.get("/v1/presence/state").json()["eye_enabled"] is True
    stopped = client.post("/v1/presence/eye/stream-stopped", json={"reason": "tab_closed"})
    assert stopped.status_code == 200 and stopped.json() == {"recorded": True}
    assert client.get("/v1/presence/state").json()["eye_enabled"] is True


def test_the_history_route_is_owner_gated() -> None:
    from app.main import create_app

    client = TestClient(create_app(Settings(_env_file=None)))
    assert client.get("/v1/presence/history").status_code in (401, 403)
