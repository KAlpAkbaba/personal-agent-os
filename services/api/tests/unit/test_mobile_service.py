"""M9 unit tests: the mobile service (registrations + artifact-ready delivery).

The identity tables and `push_registrations` share one SQLite engine here, so
the live-target *join* is exercised for real: these tests can revoke a session
through the real `IdentityService` and watch the push target disappear, which
is the acceptance chain "device revocation invalidates session" ends in.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.mobile.errors import MobileError, MobileErrorClass
from app.mobile.models import (
    PUSH_STATUS_ACTIVE,
    PUSH_STATUS_INVALID,
    PUSH_STATUS_REVOKED,
    PushRegistration,
)
from app.mobile.providers import FakePushProvider, PushMessage, build_registry, hash_token
from app.mobile.runtime import MobileRuntime
from tests.identity_support import IDENTITY_TABLES

ARTIFACT_ID = uuid.uuid4()
TITLE = "Araştırma Raporu: yapay zekâ ajanları"


@pytest.fixture()
def engine() -> Engine:
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in IDENTITY_TABLES:
        table.create(eng)
    PushRegistration.__table__.create(eng)
    return eng


@pytest.fixture()
def identity(engine: Engine) -> IdentityRuntime:
    runtime = IdentityRuntime(
        Settings(_env_file=None), engine=engine, root=InMemoryCredentialRoot()
    )
    runtime.service.bootstrap()
    return runtime


@pytest.fixture()
def fake() -> FakePushProvider:
    return FakePushProvider()


@pytest.fixture()
def mobile(engine: Engine, fake: FakePushProvider) -> MobileRuntime:
    return MobileRuntime(
        Settings(_env_file=None), engine=engine, providers=build_registry(fake=fake)
    )


def a_session(identity: IdentityRuntime, **kwargs) -> uuid.UUID:
    kwargs.setdefault("client_kind", "mobile")
    return identity.service.issue_session(**kwargs).context.session_id


# ------------------------------------------------------------- registration


def test_registration_stores_only_a_hash(mobile, identity, fake) -> None:
    session_id = a_session(identity)
    view = mobile.service.register(
        session_id=session_id, provider_name="fake", token="phone-token-1", platform="ios"
    )
    assert view.status == PUSH_STATUS_ACTIVE
    assert view.session_id == session_id
    with mobile.session() as db:
        row = db.get(PushRegistration, view.registration_id)
        assert row.token_hash == hash_token("phone-token-1")
    # The plaintext lives only in the provider's memory, keyed by registration.
    assert fake.has_live_token(str(view.registration_id))


def test_re_registering_updates_the_same_row(mobile, identity) -> None:
    """An OS token rotation must not leave a dead target behind."""
    session_id = a_session(identity)
    first = mobile.service.register(
        session_id=session_id, provider_name="fake", token="token-one"
    )
    second = mobile.service.register(
        session_id=session_id, provider_name="fake", token="token-two"
    )
    assert first.registration_id == second.registration_id
    assert len(mobile.service.list_registrations(session_id=session_id)) == 1


def test_two_providers_on_one_session_are_two_registrations(mobile, identity) -> None:
    session_id = a_session(identity)
    mobile.service.register(session_id=session_id, provider_name="fake", token="token-one")
    mobile.service.register(
        session_id=session_id, provider_name="fcm", token="token-two", platform="android"
    )
    assert len(mobile.service.list_registrations(session_id=session_id)) == 2


def test_registration_refuses_an_inactive_session(mobile, identity) -> None:
    session_id = a_session(identity)
    identity.service.revoke_session(session_id)
    with pytest.raises(MobileError) as exc:
        mobile.service.register(
            session_id=session_id, provider_name="fake", token="phone-token-1"
        )
    assert exc.value.error_class == MobileErrorClass.SESSION_REVOKED


def test_registration_refuses_an_unknown_provider(mobile, identity) -> None:
    with pytest.raises(MobileError) as exc:
        mobile.service.register(
            session_id=a_session(identity), provider_name="carrier-pigeon", token="tok-12345"
        )
    assert exc.value.error_class == MobileErrorClass.CAPABILITY_MISSING


def test_registration_refuses_a_platform_the_transport_cannot_reach(mobile, identity) -> None:
    with pytest.raises(MobileError) as exc:
        mobile.service.register(
            session_id=a_session(identity),
            provider_name="apns",
            token="a-device-token",
            platform="android",
        )
    assert exc.value.error_class == MobileErrorClass.VALIDATION_ERROR


def test_unregister_retires_the_row_and_the_live_token(mobile, identity, fake) -> None:
    session_id = a_session(identity)
    view = mobile.service.register(
        session_id=session_id, provider_name="fake", token="phone-token-1"
    )
    assert mobile.service.unregister(registration_id=view.registration_id) is True
    assert mobile.service.unregister(registration_id=view.registration_id) is False
    assert not fake.has_live_token(str(view.registration_id))
    [row] = mobile.service.list_registrations(session_id=session_id)
    assert row.status == PUSH_STATUS_REVOKED
    assert row.revoked_at is not None


def test_unregister_is_scoped_to_the_calling_session(mobile, identity) -> None:
    """One owner, but a session still cannot silently retire another's target."""
    mine = a_session(identity)
    theirs = a_session(identity)
    view = mobile.service.register(
        session_id=theirs, provider_name="fake", token="their-token"
    )
    assert (
        mobile.service.unregister(registration_id=view.registration_id, session_id=mine)
        is False
    )
    assert (
        mobile.service.unregister(registration_id=view.registration_id, session_id=theirs)
        is True
    )


# --------------------------------------------------------------- live targets


def test_a_revoked_session_is_not_a_live_push_target(mobile, identity) -> None:
    keep = a_session(identity, label="tablet")
    doomed = a_session(identity, label="lost phone")
    mobile.service.register(session_id=keep, provider_name="fake", token="keep-token")
    mobile.service.register(session_id=doomed, provider_name="fake", token="doomed-token")
    assert len(mobile.service.live_registrations()) == 2

    identity.service.revoke_session(doomed, reason="owner_revoked")

    live = mobile.service.live_registrations()
    assert [r.session_id for r in live] == [keep]
    # The registration row itself is untouched — the join is the enforcement,
    # so no future revocation path can forget to mirror a flag here.
    assert (
        mobile.service.list_registrations(session_id=doomed)[0].status == PUSH_STATUS_ACTIVE
    )


def test_device_revocation_removes_every_push_target_of_that_device(
    mobile, identity
) -> None:
    """The M9 chain, end to end at the service layer."""
    device_id = uuid.uuid4()
    phone = a_session(identity, label="phone", device_id=device_id)
    laptop = a_session(identity, client_kind="desktop", label="laptop")
    mobile.service.register(session_id=phone, provider_name="fake", token="phone-token")
    mobile.service.register(session_id=laptop, provider_name="fake", token="laptop-token")

    assert identity.service.revoke_sessions_for_device(device_id) == 1

    assert [r.session_id for r in mobile.service.live_registrations()] == [laptop]


def test_panic_removes_every_push_target(mobile, identity) -> None:
    for i in range(3):
        mobile.service.register(
            session_id=a_session(identity), provider_name="fake", token=f"phone-token-{i}"
        )
    identity.service.revoke_all(reason="owner_panic")
    assert mobile.service.live_registrations() == []


# --------------------------------------------------------------- notification


def test_artifact_ready_is_short_turkish_and_carries_no_report(mobile) -> None:
    message = mobile.service.build_artifact_ready_message(
        artifact_id=ARTIFACT_ID, title=TITLE
    )
    assert message.title == "Araştırma tamamlandı"
    assert TITLE in message.body
    assert message.data == {"kind": "artifact_ready", "artifact_id": str(ARTIFACT_ID)}
    assert message.collapse_key == f"artifact:{ARTIFACT_ID}"
    assert isinstance(message, PushMessage)


def test_artifact_ready_falls_back_to_english_for_other_locales(mobile) -> None:
    message = mobile.service.build_artifact_ready_message(
        artifact_id=ARTIFACT_ID, title="Report", locale="en-GB"
    )
    assert message.title == "Research complete"


def test_a_very_long_title_is_truncated_not_rejected(mobile) -> None:
    message = mobile.service.build_artifact_ready_message(
        artifact_id=ARTIFACT_ID, title="x" * 4000
    )
    assert message.body.endswith("…")


def test_notify_delivers_to_every_live_registration(mobile, identity, fake) -> None:
    phone = a_session(identity, label="phone")
    tablet = a_session(identity, label="tablet")
    a = mobile.service.register(session_id=phone, provider_name="fake", token="phone-token")
    b = mobile.service.register(session_id=tablet, provider_name="fake", token="tablet-token")

    result = mobile.service.notify_artifact_ready(artifact_id=ARTIFACT_ID, title=TITLE)

    assert result.delivered == 2
    assert result.failed == 0
    assert len(fake.deliveries_for(str(a.registration_id))) == 1
    assert len(fake.deliveries_for(str(b.registration_id))) == 1
    # ...and the delivery is recorded on the row.
    assert mobile.service.list_registrations(session_id=phone)[0].last_delivery_at is not None


def test_notify_skips_revoked_sessions(mobile, identity, fake) -> None:
    doomed = a_session(identity)
    view = mobile.service.register(
        session_id=doomed, provider_name="fake", token="doomed-token"
    )
    identity.service.revoke_session(doomed)
    result = mobile.service.notify_artifact_ready(artifact_id=ARTIFACT_ID, title=TITLE)
    assert result.delivered == 0
    assert fake.deliveries_for(str(view.registration_id)) == []


def test_notify_uses_each_registrations_locale(mobile, identity, fake) -> None:
    tr = mobile.service.register(
        session_id=a_session(identity), provider_name="fake", token="tr-token", locale="tr-TR"
    )
    en = mobile.service.register(
        session_id=a_session(identity), provider_name="fake", token="en-token", locale="en-US"
    )
    mobile.service.notify_artifact_ready(artifact_id=ARTIFACT_ID, title=TITLE)
    assert fake.deliveries_for(str(tr.registration_id))[0].message.title == (
        "Araştırma tamamlandı"
    )
    assert fake.deliveries_for(str(en.registration_id))[0].message.title == (
        "Research complete"
    )


def test_a_dead_token_retires_the_registration_instead_of_retrying(
    mobile, identity, fake
) -> None:
    session_id = a_session(identity)
    view = mobile.service.register(
        session_id=session_id, provider_name="fake", token="phone-token"
    )
    # The provider forgot the token (API restart, or the OS rotated it).
    fake.invalidate(registration_key=str(view.registration_id))

    result = mobile.service.notify_artifact_ready(artifact_id=ARTIFACT_ID, title=TITLE)

    assert result.failed == 1
    assert result.outcomes[0].error_class == str(MobileErrorClass.PUSH_TOKEN_INVALID)
    assert (
        mobile.service.list_registrations(session_id=session_id)[0].status
        == PUSH_STATUS_INVALID
    )
    # ...so the next announcement does not shout into the void again.
    assert mobile.service.live_registrations() == []


def test_notify_with_no_registrations_is_a_no_op(mobile) -> None:
    result = mobile.service.notify_artifact_ready(artifact_id=ARTIFACT_ID, title=TITLE)
    assert result.delivered == 0 and result.outcomes == []
    assert result.to_dict()["notification"]["title"] == "Araştırma tamamlandı"


# ------------------------------------------------------------------- inbox


def test_deliveries_are_readable_only_for_the_owning_session(mobile, identity) -> None:
    mine = a_session(identity)
    theirs = a_session(identity)
    mobile.service.register(session_id=mine, provider_name="fake", token="my-token")
    mobile.service.register(session_id=theirs, provider_name="fake", token="their-token")
    mobile.service.notify_artifact_ready(artifact_id=ARTIFACT_ID, title=TITLE)

    inbox = mobile.service.deliveries_for_session(mine)
    assert len(inbox) == 1
    assert inbox[0]["data"]["artifact_id"] == str(ARTIFACT_ID)
    assert "token" not in inbox[0]


def test_capabilities_report_which_real_providers_a_credential_would_activate(
    mobile,
) -> None:
    by_name = {c["name"]: c for c in mobile.service.capabilities()}
    assert by_name["fake"]["activated"] is True
    # No credentials in the environment: every real transport is inert.
    assert by_name["fcm"]["activated"] is False
    assert by_name["apns"]["requires_credentials"] is True
    health = mobile.health_check()
    assert health["status"] == "ok"
    assert health["real_providers_activated"] == {
        "apns": False,
        "fcm": False,
        "webpush": False,
    }
