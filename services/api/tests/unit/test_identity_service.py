"""M9 unit tests: the identity service lifecycle on an in-memory SQLite engine.

Covers the whole decision surface — bootstrap once, exchange, verify with each
typed refusal, refresh-with-rotation, revocation (session / device / all),
expiry sweep — plus the two invariants the audit exists to guarantee: every
decision writes an event, and no event ever contains a token.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.identity import tokens
from app.identity.errors import (
    AlreadyBootstrapped,
    InvalidOwnerCredential,
    NotBootstrapped,
    Refusal,
    Throttled,
)
from app.identity.models import (
    EVENT_EXPIRED,
    EVENT_ISSUED,
    EVENT_REFRESHED,
    EVENT_REJECTED,
    EVENT_REVOKED,
    SESSION_STATUS_EXPIRED,
    SESSION_STATUS_REVOKED,
    OwnerSession,
)
from app.identity.root import InMemoryCredentialRoot
from app.identity.service import AttemptLimiter, IdentityService, IssuedSession
from tests.identity_support import make_identity_engine

TTL_S = 3600
IDLE_S = 600


class Clock:
    """Deterministic, movable clock — no sleeps anywhere in this suite."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def service(clock: Clock) -> IdentityService:
    engine = make_identity_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return IdentityService(
        session_scope,
        InMemoryCredentialRoot(),
        ttl_s=TTL_S,
        idle_timeout_s=IDLE_S,
        clock=clock,
        limiter=AttemptLimiter(max_failures=5, window_s=60.0, clock=lambda: clock.now.timestamp()),
    )


def bootstrapped(service: IdentityService) -> str:
    return service.bootstrap()


def issue(service: IdentityService, **kw) -> IssuedSession:
    return service.issue_session(client_kind=kw.pop("client_kind", "cli"), **kw)


def actions(service: IdentityService) -> list[str]:
    return [e["action"] for e in service.list_events(limit=200)]


# ------------------------------------------------------------------ bootstrap


def test_bootstrap_mints_once_and_stores_only_a_hash(service: IdentityService) -> None:
    credential = service.bootstrap()
    assert credential.startswith(tokens.OWNER_CREDENTIAL_PREFIX)
    record = service.root.load()
    assert record is not None
    assert record.credential_hash == tokens.hash_token(credential)
    # The plaintext is nowhere in the stored record.
    assert credential not in str(record.to_json())


def test_second_bootstrap_is_refused(service: IdentityService) -> None:
    service.bootstrap()
    with pytest.raises(AlreadyBootstrapped):
        service.bootstrap()


def test_forced_bootstrap_is_a_rotation_not_a_reset(service: IdentityService) -> None:
    first = service.bootstrap()
    second = service.bootstrap(force=True)
    assert first != second
    record = service.root.load()
    assert record is not None and record.rotations == 1 and record.rotated_at is not None
    assert not service.verify_owner_credential(first)
    assert service.verify_owner_credential(second)


def test_verify_owner_credential_before_bootstrap_raises(service: IdentityService) -> None:
    with pytest.raises(NotBootstrapped):
        service.verify_owner_credential("pagentos_ok_" + "x" * 43)


# ------------------------------------------------------------------- issuing


def test_exchange_credential_issues_a_session(service: IdentityService) -> None:
    credential = bootstrapped(service)
    issued = service.exchange_credential(credential, client_kind="mobile", label="phone")
    assert issued.token.startswith(tokens.SESSION_TOKEN_PREFIX)
    assert issued.context.client_kind == "mobile"
    assert issued.context.client_label == "phone"
    assert issued.context.unrestricted
    assert EVENT_ISSUED in actions(service)


def test_wrong_credential_is_refused_and_audited(service: IdentityService) -> None:
    bootstrapped(service)
    with pytest.raises(InvalidOwnerCredential):
        service.exchange_credential(
            "pagentos_ok_" + "z" * 43, client_kind="cli"
        )
    assert EVENT_REJECTED in actions(service)


def test_exchange_before_bootstrap_fails_closed(service: IdentityService) -> None:
    with pytest.raises(NotBootstrapped):
        service.exchange_credential("pagentos_ok_" + "z" * 43, client_kind="cli")


def test_credential_attempts_are_throttled(service: IdentityService) -> None:
    bootstrapped(service)
    for _ in range(5):
        with pytest.raises(InvalidOwnerCredential):
            service.exchange_credential("pagentos_ok_" + "z" * 43, client_kind="cli")
    with pytest.raises(Throttled) as exc:
        service.exchange_credential("pagentos_ok_" + "z" * 43, client_kind="cli")
    assert exc.value.retry_after_s > 0


def test_unknown_client_kind_and_bad_ttl_are_refused(service: IdentityService) -> None:
    bootstrapped(service)
    with pytest.raises(ValueError):
        service.issue_session(client_kind="server")
    with pytest.raises(ValueError):
        service.issue_session(client_kind="cli", ttl_s=1)
    with pytest.raises(ValueError):
        service.issue_session(client_kind="cli", ttl_s=TTL_S * 10)


def test_scopes_are_bounded_and_deduplicated(service: IdentityService) -> None:
    bootstrapped(service)
    issued = service.issue_session(client_kind="cli", scopes=["a", "a", "b"])
    assert issued.context.scopes == ("a", "b")
    assert not issued.context.unrestricted
    assert issued.context.has_scope("a")
    assert not issued.context.has_scope("c")
    with pytest.raises(ValueError):
        service.issue_session(client_kind="cli", scopes=[f"s{i}" for i in range(17)])
    with pytest.raises(ValueError):
        service.issue_session(client_kind="cli", scopes=["x" * 65])


# --------------------------------------------------------------- verification


def test_verify_accepts_a_live_token_and_updates_last_seen(
    service: IdentityService, clock: Clock
) -> None:
    bootstrapped(service)
    issued = issue(service)
    clock.advance(30)
    verdict = service.verify(issued.token)
    assert verdict.ok
    assert verdict.session is not None
    assert verdict.session.session_id == issued.context.session_id
    assert verdict.session.last_seen_at == clock.now


def test_verify_before_bootstrap_refuses_everything(service: IdentityService) -> None:
    verdict = service.verify("pagentos_st_" + "x" * 43)
    assert verdict.refusal is Refusal.NOT_BOOTSTRAPPED
    assert not verdict.ok


@pytest.mark.parametrize("bad", [None, "", "garbage", "Bearer x", "pagentos_ok_" + "x" * 43])
def test_verify_refuses_malformed(service: IdentityService, bad: str | None) -> None:
    bootstrapped(service)
    assert service.verify(bad).refusal is Refusal.MALFORMED


def test_verify_refuses_unknown_token(service: IdentityService) -> None:
    bootstrapped(service)
    assert service.verify(tokens.new_session_token()).refusal is Refusal.UNKNOWN


def test_verify_refuses_after_absolute_ttl(service: IdentityService, clock: Clock) -> None:
    bootstrapped(service)
    issued = issue(service)
    clock.advance(TTL_S + 1)
    assert service.verify(issued.token).refusal is Refusal.EXPIRED
    # ...and the row is flipped so the refusal is recorded once, not recomputed.
    with service._sessions() as db:  # noqa: SLF001 - white-box assertion on state
        row = db.get(OwnerSession, issued.context.session_id)
        assert row is not None and row.status == SESSION_STATUS_EXPIRED
    assert EVENT_EXPIRED in actions(service)


def test_verify_refuses_after_idle_timeout_even_within_ttl(
    service: IdentityService, clock: Clock
) -> None:
    bootstrapped(service)
    issued = issue(service)
    clock.advance(IDLE_S + 1)  # well inside TTL_S
    verdict = service.verify(issued.token)
    assert verdict.refusal is Refusal.IDLE_TIMEOUT


def test_activity_keeps_a_session_alive_within_the_idle_window(
    service: IdentityService, clock: Clock
) -> None:
    bootstrapped(service)
    issued = issue(service)
    for _ in range(5):
        clock.advance(IDLE_S - 1)
        assert service.verify(issued.token).ok


def test_verify_refuses_a_revoked_session(service: IdentityService) -> None:
    bootstrapped(service)
    issued = issue(service)
    assert service.revoke_session(issued.context.session_id, reason="owner_revoked")
    assert service.verify(issued.token).refusal is Refusal.REVOKED
    # Revoking twice is not an error, it is a no-op.
    assert not service.revoke_session(issued.context.session_id)


# ------------------------------------------------------------------- refresh


def test_refresh_rotates_the_token_and_keeps_the_session_identity(
    service: IdentityService, clock: Clock
) -> None:
    bootstrapped(service)
    issued = issue(service, label="phone", device_id=None)
    clock.advance(60)
    refreshed = service.refresh(issued.token)
    assert isinstance(refreshed, IssuedSession)
    assert refreshed.token != issued.token
    assert refreshed.context.session_id == issued.context.session_id
    assert refreshed.context.expires_at == clock.now + timedelta(seconds=TTL_S)
    # The old token is dead immediately: rotation, not extension.
    assert service.verify(issued.token).refusal is Refusal.UNKNOWN
    assert service.verify(refreshed.token).ok
    assert EVENT_REFRESHED in actions(service)


def test_refresh_of_a_dead_token_returns_the_refusal(service: IdentityService) -> None:
    bootstrapped(service)
    issued = issue(service)
    service.revoke_session(issued.context.session_id)
    result = service.refresh(issued.token)
    assert not isinstance(result, IssuedSession)
    assert result.refusal is Refusal.REVOKED


# ------------------------------------------------------------------ revoking


def test_revoking_a_device_revokes_exactly_its_sessions(service: IdentityService) -> None:
    bootstrapped(service)
    device = uuid.uuid4()
    other_device = uuid.uuid4()
    phone = issue(service, client_kind="mobile", device_id=device)
    tablet = issue(service, client_kind="mobile", device_id=device)
    desktop = issue(service, client_kind="desktop", device_id=other_device)
    unbound = issue(service, client_kind="web")

    assert service.revoke_sessions_for_device(device) == 2
    assert service.verify(phone.token).refusal is Refusal.REVOKED
    assert service.verify(tablet.token).refusal is Refusal.REVOKED
    assert service.verify(desktop.token).ok
    assert service.verify(unbound.token).ok
    # Idempotent: a second revocation finds nothing left to revoke.
    assert service.revoke_sessions_for_device(device) == 0


def test_device_revocation_reason_is_recorded_on_the_row(service: IdentityService) -> None:
    bootstrapped(service)
    device = uuid.uuid4()
    phone = issue(service, client_kind="mobile", device_id=device)
    service.revoke_sessions_for_device(device)
    with service._sessions() as db:  # noqa: SLF001 - white-box assertion on state
        row = db.get(OwnerSession, phone.context.session_id)
        assert row is not None
        assert row.status == SESSION_STATUS_REVOKED
        assert row.revoked_reason == "device_revoked"
        assert row.revoked_at is not None
    assert EVENT_REVOKED in actions(service)


def test_panic_revokes_every_session(service: IdentityService) -> None:
    bootstrapped(service)
    live = [issue(service) for _ in range(4)]
    assert service.revoke_all() == 4
    for issued in live:
        assert service.verify(issued.token).refusal is Refusal.REVOKED
    assert service.count_active_sessions() == 0
    # The owner credential still works: panic kills sessions, not ownership.
    assert service.is_bootstrapped()


# --------------------------------------------------------------------- sweep


def test_sweep_expires_stale_sessions_once(service: IdentityService, clock: Clock) -> None:
    bootstrapped(service)
    [issue(service) for _ in range(3)]
    fresh_clock_ttl = TTL_S + 5
    clock.advance(fresh_clock_ttl)
    assert service.sweep_expired() == 3
    assert service.sweep_expired() == 0
    assert service.count_active_sessions() == 0


# --------------------------------------------------------------------- audit


def test_no_event_ever_contains_a_token_or_credential(service: IdentityService) -> None:
    credential = bootstrapped(service)
    issued = issue(service)
    service.refresh(issued.token)
    bad = tokens.new_session_token()
    service.verify(bad)
    service.revoke_all()
    blob = repr(service.list_events(limit=500))
    assert credential not in blob
    assert issued.token not in blob
    assert bad not in blob
    # ...but the rejection is still correlatable by one-way fingerprint.
    assert tokens.fingerprint(bad) in blob


def test_every_decision_kind_is_represented_in_the_audit(
    service: IdentityService, clock: Clock
) -> None:
    bootstrapped(service)
    issued = issue(service)
    service.refresh(issued.token)
    service.verify("garbage")
    live = issue(service)
    service.revoke_session(live.context.session_id)
    stale = issue(service)
    clock.advance(TTL_S + 1)
    service.verify(stale.token)
    recorded = set(actions(service))
    assert {EVENT_ISSUED, EVENT_REFRESHED, EVENT_REJECTED, EVENT_REVOKED, EVENT_EXPIRED} <= recorded


def test_rejection_audit_is_bounded_so_a_scanner_cannot_flood_it(
    service: IdentityService,
) -> None:
    bootstrapped(service)
    for _ in range(50):
        service.verify(tokens.new_session_token())
    rejected = [e for e in service.list_events(limit=500) if e["action"] == EVENT_REJECTED]
    # 5 detailed rejections + exactly one "suppressed" marker.
    assert len(rejected) == 6
    assert rejected[0]["reason"] == "rejection_audit_suppressed"


def test_events_can_be_filtered_by_action(service: IdentityService) -> None:
    bootstrapped(service)
    issue(service)
    only_issued = service.list_events(action=EVENT_ISSUED)
    assert only_issued and all(e["action"] == EVENT_ISSUED for e in only_issued)
