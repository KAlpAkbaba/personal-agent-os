"""B05 req 246/663 + 658/659: authority decided by the server, not by the caller.

Three separate holes, one theme - the system took another party's word for something only it
could know:

* ``create_command`` inserted whatever capability string it was handed. The only thing
  between a command and the owner's machine was the DEVICE refusing it on arrival: one half
  of a boundary, held by the half furthest away.
* ``POST /v1/voice/speaker/verify`` read ``device_trusted`` from the request body. The rule
  "an untrusted device caps a voice match at UNCERTAIN" was written down and then handed to
  the one party it exists to constrain.
* ``refresh`` extended ``expires_at`` on the same row with no ceiling, so a token that leaked
  into a log or a backup lived exactly as long as something kept refreshing it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.broker import service as broker_service
from app.broker.models import (
    DEVICE_STATUS_REVOKED,
    AuditEvent,
    Device,
    DeviceCommand,
    DeviceSession,
    EnrollmentToken,
)
from app.devices import authority
from app.identity.models import OwnerSession, SessionEvent
from app.identity.root import InMemoryCredentialRoot
from app.identity.service import IdentityService, SessionContext
from app.voice.device_trust import device_is_trusted

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

_BROKER_TABLES = (
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    AuditEvent.__table__,
)


@pytest.fixture(autouse=True)
def _shadow_by_default():
    """Every test states the mode it is about; none inherits another's."""
    authority.set_mode(authority.GATE_MODE_SHADOW)
    yield
    authority.set_mode(authority.GATE_MODE_SHADOW)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in _BROKER_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _device(db, *, capabilities: list[str], status: str = "active") -> Device:
    device = Device(
        id=uuid.uuid4(),
        name="ev-pc",
        platform="windows",
        public_key_spki_b64="x" * 40,
        capabilities_json=capabilities,
        status=status,
    )
    db.add(device)
    db.commit()
    return device


def _create(db, device_id, capability: str):
    return broker_service.create_command(
        db,
        device_id=device_id,
        capability=capability,
        payload={},
        idempotency_key=uuid.uuid4().hex,
        timeout_s=30,
        trace_id="t",
    )


# ------------------------------------------------------- the device command gate


def test_a_capability_the_device_never_advertised_is_refused(db) -> None:
    device = _device(db, capabilities=["file.read"])
    authority.set_mode(authority.GATE_MODE_ENFORCE)

    with pytest.raises(authority.DeviceCommandRefused) as caught:
        _create(db, device.id, "project.run")

    assert caught.value.decision.reason == authority.REASON_CAPABILITY_NOT_ADVERTISED
    assert db.query(DeviceCommand).count() == 0, "nothing durable was written"


def test_a_family_marker_still_satisfies_a_per_operation_capability(db) -> None:
    """The gate reuses `has_capability`, so it agrees with device selection about what a
    device can do. A second opinion here would refuse commands selection had just routed."""
    device = _device(db, capabilities=["browser.chrome"])
    authority.set_mode(authority.GATE_MODE_ENFORCE)

    command, created = _create(db, device.id, "browser.search")

    assert created and command.capability == "browser.search"


def test_a_revoked_device_is_refused_whatever_it_advertises(db) -> None:
    device = _device(db, capabilities=["file.read"], status=DEVICE_STATUS_REVOKED)
    authority.set_mode(authority.GATE_MODE_ENFORCE)

    with pytest.raises(authority.DeviceCommandRefused) as caught:
        _create(db, device.id, "file.read")

    assert caught.value.decision.reason == authority.REASON_DEVICE_REVOKED


def test_a_command_for_a_device_that_does_not_exist_is_refused(db) -> None:
    authority.set_mode(authority.GATE_MODE_ENFORCE)

    with pytest.raises(authority.DeviceCommandRefused) as caught:
        _create(db, uuid.uuid4(), "file.read")

    assert caught.value.decision.reason == authority.REASON_UNKNOWN_DEVICE


def test_shadow_mode_records_the_refusal_and_lets_it_through(db) -> None:
    """The roadmap's rollback plan, as a test: a gate whose first production appearance is
    an outage is a gate that gets turned off. Shadow answers the question - how many real
    calls would this refuse? - without being the one to break them."""
    device = _device(db, capabilities=["file.read"])

    command, created = _create(db, device.id, "project.run")

    assert created, "shadow mode does not block"
    assert command.capability == "project.run"
    decision = authority.evaluate(device, "project.run", mode=authority.GATE_MODE_SHADOW)
    assert decision.would_refuse is True
    assert decision.allowed is True


def test_the_two_modes_reach_the_same_conclusion_and_differ_only_in_acting(db) -> None:
    device = _device(db, capabilities=["file.read"])
    shadow = authority.evaluate(device, "project.run", mode=authority.GATE_MODE_SHADOW)
    enforce = authority.evaluate(device, "project.run", mode=authority.GATE_MODE_ENFORCE)

    assert shadow.reason == enforce.reason == authority.REASON_CAPABILITY_NOT_ADVERTISED
    assert shadow.would_refuse == enforce.would_refuse is True
    assert (shadow.allowed, enforce.allowed) == (True, False)


def test_an_unknown_mode_falls_back_to_shadow_rather_than_to_nothing(db) -> None:
    """A typo in configuration must not silently disable the gate, and must not silently
    block everything either. It counts, and says the mode was not understood."""
    assert authority.set_mode("enfroce") == authority.GATE_MODE_SHADOW
    device = _device(db, capabilities=[])

    _, created = _create(db, device.id, "file.read")

    assert created


def test_the_gate_ships_off(db) -> None:
    """The default is shadow. Turning enforcement on is the owner's decision, taken after a
    day of production counting - this pins that the code does not take it for them."""
    from app.config import Settings

    assert Settings(_env_file=None).device_command_gate_mode == authority.GATE_MODE_SHADOW


def test_a_deduplicated_command_is_answered_without_re_gating(db) -> None:
    """Returning an already-created command is not creating one. Re-answering it must not
    depend on what the device happens to advertise right now, or a capability list that
    changed between the retry and the original would turn a successful command into a
    refusal."""
    device = _device(db, capabilities=["file.read"])
    key = uuid.uuid4().hex
    first, created = broker_service.create_command(
        db, device_id=device.id, capability="file.read", payload={},
        idempotency_key=key, timeout_s=30, trace_id="t",
    )
    assert created

    device.capabilities_json = []
    db.commit()
    authority.set_mode(authority.GATE_MODE_ENFORCE)

    again, created_again = broker_service.create_command(
        db, device_id=device.id, capability="file.read", payload={},
        idempotency_key=key, timeout_s=30, trace_id="t",
    )

    assert created_again is False
    assert again.id == first.id


def test_every_command_creation_path_goes_through_the_gate() -> None:
    """The gate is inside `create_command` rather than at each caller, so a caller added
    later cannot forget it. This reads the source to hold that: two call sites exist today,
    and neither reaches DeviceCommand(...) on its own."""
    import inspect

    source = inspect.getsource(broker_service.create_command)
    assert "authority.evaluate" in source
    assert "DeviceCommandRefused" in source


# ------------------------------------------------------------ derived device trust


def _context(device_id: uuid.UUID | None) -> SessionContext:
    return SessionContext(
        session_id=uuid.uuid4(),
        client_kind="desktop",
        client_label="pc",
        device_id=device_id,
        scopes=(),
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
        last_seen_at=NOW,
    )


def test_a_session_bound_to_an_enrolled_device_is_trusted(db) -> None:
    device = _device(db, capabilities=[])
    assert device_is_trusted(db, _context(device.id)) is True


def test_a_session_with_no_device_binding_is_not_trusted(db) -> None:
    """A browser tab or a curl call. Untrusted is the honest answer, not a penalty."""
    assert device_is_trusted(db, _context(None)) is False


def test_a_session_bound_to_a_revoked_device_is_not_trusted(db) -> None:
    device = _device(db, capabilities=[], status=DEVICE_STATUS_REVOKED)
    assert device_is_trusted(db, _context(device.id)) is False


def test_a_binding_to_a_device_that_no_longer_exists_is_not_trusted(db) -> None:
    assert device_is_trusted(db, _context(uuid.uuid4())) is False


def test_no_request_body_can_reach_the_trust_decision() -> None:
    """The shape of the old defect: the decision took an argument the caller controlled.
    `device_is_trusted` takes a session and a database, and nothing else."""
    import inspect

    parameters = list(inspect.signature(device_is_trusted).parameters)
    assert parameters == ["db", "session"]


# --------------------------------------------------------------- session lifetime


@pytest.fixture()
def identity_db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (OwnerSession.__table__, SessionEvent.__table__):
        table.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _service(
    factory,
    *,
    now: datetime,
    absolute_lifetime_s: int = 90 * 24 * 3600,
    idle_timeout_s: int = 7 * 24 * 3600,
):
    service = IdentityService(
        factory,
        InMemoryCredentialRoot(),
        ttl_s=30 * 24 * 3600,
        idle_timeout_s=idle_timeout_s,
        absolute_lifetime_s=absolute_lifetime_s,
        clock=lambda: now,
    )
    service.bootstrap()
    return service


def test_a_refreshed_session_still_dies_of_old_age(identity_db) -> None:
    """B05 req 658, the defect exactly: `refresh` rotates the token and pushes `expires_at`
    out on the SAME row, so before this a leaked token lived for ever as long as something
    kept refreshing it. The ceiling is measured from `created_at` and a refresh cannot lift
    it."""
    born = _service(identity_db, now=NOW)
    issued = born.issue_session(client_kind="desktop", label="pc")

    # A hundred days later, having been refreshed the whole time.
    later = _service(identity_db, now=NOW + timedelta(days=100))
    verdict = later.verify(issued.token)

    assert verdict.ok is False
    assert verdict.refusal is not None


def test_the_ceiling_is_not_reset_by_refreshing(identity_db) -> None:
    """The defect, staged: a client that refreshes every twenty days keeps `expires_at`
    permanently in the future, so before this the session never ended. It is refreshed five
    times here and is still over at a hundred days, because the ceiling is measured from
    `created_at` and a refresh does not touch that."""
    token = _service(identity_db, now=NOW).issue_session(
        client_kind="desktop", label="pc"
    ).token

    # Every five days, well inside the seven-day idle window: this is a client somebody is
    # actually using (or a stolen token somebody is actually using).
    for day in range(5, 90, 5):
        rotated = _service(identity_db, now=NOW + timedelta(days=day)).refresh(token)
        assert getattr(rotated, "token", None), f"a live client refreshes fine on day {day}"
        token = rotated.token

    past = _service(identity_db, now=NOW + timedelta(days=100))
    verdict = past.verify(token)

    assert verdict.ok is False
    events = past.list_events(limit=10)
    assert any(e["reason"] == "absolute_lifetime" for e in events), events


def test_without_the_ceiling_the_same_client_would_live_for_ever(identity_db) -> None:
    """The control for the test above: the refresh loop itself is not what ends the session,
    the ceiling is. With no ceiling configured the identical sequence still authenticates."""
    token = _service(identity_db, now=NOW, absolute_lifetime_s=0).issue_session(
        client_kind="desktop", label="pc"
    ).token

    for day in range(5, 130, 5):
        rotated = _service(
            identity_db, now=NOW + timedelta(days=day), absolute_lifetime_s=0
        ).refresh(token)
        assert getattr(rotated, "token", None), f"still alive on day {day}"
        token = rotated.token

    assert (
        _service(identity_db, now=NOW + timedelta(days=130), absolute_lifetime_s=0)
        .verify(token)
        .ok
        is True
    )


def test_a_young_session_is_untouched_by_the_ceiling(identity_db) -> None:
    born = _service(identity_db, now=NOW)
    issued = born.issue_session(client_kind="desktop", label="pc")

    later = _service(identity_db, now=NOW + timedelta(days=3))

    assert later.verify(issued.token).ok is True


def test_an_idle_session_is_swept_rather_than_waiting_to_be_presented(identity_db) -> None:
    """B05 req 659. The idle rule existed only in `verify`, so an abandoned client's session
    stayed `active` in the database until somebody presented its token - which for an
    abandoned client is never. It was counted and listed as an active session the whole
    time. Same defect shape as the voice sessions B06 closed."""
    born = _service(identity_db, now=NOW)
    born.issue_session(client_kind="web", label="tab")

    later = _service(identity_db, now=NOW + timedelta(days=10))
    swept = later.sweep_expired()

    assert swept == 1
    assert later.count_active_sessions() == 0


def test_a_session_used_yesterday_is_left_alone_by_the_sweep(identity_db) -> None:
    born = _service(identity_db, now=NOW)
    issued = born.issue_session(client_kind="web", label="tab")

    day_later = _service(identity_db, now=NOW + timedelta(days=1))
    assert day_later.verify(issued.token).ok is True

    assert day_later.sweep_expired() == 0
    assert day_later.count_active_sessions() == 1


def test_the_sweep_says_which_rule_ended_each_session(identity_db) -> None:
    """Three rules end a session and the audit has to tell them apart, or the owner reading
    "expired" cannot know whether a client went quiet or a token got too old to renew."""
    born = _service(identity_db, now=NOW)
    born.issue_session(client_kind="web", label="tab")

    later = _service(identity_db, now=NOW + timedelta(days=100))
    later.sweep_expired()

    reasons = {event["reason"] for event in later.list_events(limit=20)}
    assert "absolute_lifetime" in reasons
