"""app.webpush.service: subscription storage and ``send_to_all``.

Uses ``FakePushProvider`` (never the real HTTP transport — that is
``test_webpush_provider.py``'s job) so these tests are about the ORCHESTRATION: which
subscription gets which headers, what happens to the row on success/failure/expiry, and
that "accepted" is computed from what the provider actually did rather than assumed.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.notifications.models import PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_URGENT
from app.webpush import ece, service
from app.webpush.models import PushSubscriptionRow
from app.webpush.provider import FakePushProvider, PushError
from app.webpush.vapid import generate_key_pair

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    PushSubscriptionRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _fake_subscription_keys() -> tuple[str, str]:
    private = ec.generate_private_key(ece.CURVE)
    p256dh = _b64url(ece.raw_public_key(private.public_key()))
    auth = _b64url(b"\x01" * 16)
    return p256dh, auth


def _subscribe(
    db, *, endpoint: str = "https://fcm.googleapis.com/fcm/send/aaa"
) -> PushSubscriptionRow:
    p256dh, auth = _fake_subscription_keys()
    return service.subscribe(
        db, endpoint=endpoint, p256dh=p256dh, auth=auth, user_agent="pytest-agent"
    )


# ------------------------------------------------------------- subscribe/list/unsubscribe


def test_subscribe_stores_a_row(db) -> None:
    row = _subscribe(db)
    assert row.id is not None
    assert service.list_subscriptions(db) == [row]


def test_subscribing_the_same_endpoint_twice_updates_rather_than_duplicates(db) -> None:
    p256dh1, auth1 = _fake_subscription_keys()
    p256dh2, auth2 = _fake_subscription_keys()
    endpoint = "https://fcm.googleapis.com/fcm/send/same"
    first = service.subscribe(db, endpoint=endpoint, p256dh=p256dh1, auth=auth1)
    second = service.subscribe(db, endpoint=endpoint, p256dh=p256dh2, auth=auth2)
    assert first.id == second.id
    assert len(service.list_subscriptions(db)) == 1
    assert service.list_subscriptions(db)[0].p256dh == p256dh2


def test_subscribe_refuses_a_non_allowlisted_endpoint(db) -> None:
    p256dh, auth = _fake_subscription_keys()
    with pytest.raises(PushError):
        service.subscribe(db, endpoint="https://evil.example.com/x", p256dh=p256dh, auth=auth)
    assert service.list_subscriptions(db) == []


def test_subscribe_refuses_malformed_key_material(db) -> None:
    _, auth = _fake_subscription_keys()
    with pytest.raises(service.SubscriptionError):
        service.subscribe(
            db,
            endpoint="https://fcm.googleapis.com/fcm/send/x",
            p256dh="not-a-valid-point",
            auth=auth,
        )


def test_unsubscribe_removes_the_row(db) -> None:
    row = _subscribe(db)
    assert service.unsubscribe(db, row.id) is True
    assert service.list_subscriptions(db) == []


def test_unsubscribe_unknown_id_returns_false(db) -> None:
    assert service.unsubscribe(db, uuid.uuid4()) is False


# ------------------------------------------------------------- build_payload


def test_build_payload_contains_the_expected_fields() -> None:
    import json

    notification_id = uuid.uuid4()
    payload = service.build_payload(
        notification_id=notification_id, title="Başlık", body="Gövde metni", group_key="task:1"
    )
    decoded = json.loads(payload)
    assert decoded["notification_id"] == str(notification_id)
    assert decoded["title"] == "Başlık"
    assert decoded["body"] == "Gövde metni"
    assert decoded["tag"] == "task:1"


def test_build_payload_trims_oversized_title_and_body() -> None:
    import json

    payload = service.build_payload(
        notification_id=uuid.uuid4(), title="x" * 1000, body="y" * 1000, group_key=""
    )
    decoded = json.loads(payload)
    assert len(decoded["title"]) == service.MAX_TITLE_CHARS
    assert len(decoded["body"]) == service.MAX_BODY_CHARS
    assert len(payload) <= ece.MAX_PLAINTEXT_BYTES


def test_build_payload_falls_back_to_notification_id_as_tag_when_no_group_key() -> None:
    import json

    notification_id = uuid.uuid4()
    payload = service.build_payload(
        notification_id=notification_id, title="t", body="b", group_key=""
    )
    assert json.loads(payload)["tag"] == str(notification_id)


# ------------------------------------------------------------- send_to_all


def test_send_to_all_with_no_subscriptions_attempts_nothing(db) -> None:
    outcome = service.send_to_all(
        db,
        provider=FakePushProvider(),
        vapid_private_key=generate_key_pair().private_key,
        vapid_subject="mailto:owner@example.com",
        notification_id=uuid.uuid4(),
        title="t",
        body="b",
        group_key="",
        priority=PRIORITY_NORMAL,
        now=NOW,
    )
    assert outcome.attempted == 0
    assert outcome.accepted == 0


def test_send_to_all_accepts_and_marks_success(db) -> None:
    row = _subscribe(db)
    provider = FakePushProvider()
    outcome = service.send_to_all(
        db,
        provider=provider,
        vapid_private_key=generate_key_pair().private_key,
        vapid_subject="mailto:owner@example.com",
        notification_id=uuid.uuid4(),
        title="Rapor hazır",
        body="Üç klasör karşılaştırması bitti efendim.",
        group_key="task:1",
        priority=PRIORITY_NORMAL,
        now=NOW,
    )
    assert outcome.attempted == 1
    assert outcome.accepted == 1
    assert len(provider.calls) == 1

    db.refresh(row)
    assert row.last_success_at.replace(tzinfo=UTC) == NOW
    assert row.last_error_reason is None
    assert row.failure_count == 0


def test_send_to_all_sends_aes128gcm_content_encoding_and_vapid_authorization(db) -> None:
    _subscribe(db)
    provider = FakePushProvider()
    key_pair = generate_key_pair()
    service.send_to_all(
        db,
        provider=provider,
        vapid_private_key=key_pair.private_key,
        vapid_subject="mailto:owner@example.com",
        notification_id=uuid.uuid4(),
        title="t",
        body="b",
        group_key="",
        priority=PRIORITY_NORMAL,
        now=NOW,
    )
    headers = provider.calls[0]["headers"]
    assert headers["Content-Encoding"] == "aes128gcm"
    assert headers["Authorization"].startswith("vapid t=")
    assert f"k={key_pair.public_key_b64url}" in headers["Authorization"]


@pytest.mark.parametrize(
    ("priority", "expected_urgency"),
    [(PRIORITY_URGENT, "high"), (PRIORITY_NORMAL, "normal"), (PRIORITY_LOW, "low")],
)
def test_urgency_header_follows_priority(db, priority: str, expected_urgency: str) -> None:
    _subscribe(db)
    provider = FakePushProvider()
    service.send_to_all(
        db,
        provider=provider,
        vapid_private_key=generate_key_pair().private_key,
        vapid_subject="mailto:owner@example.com",
        notification_id=uuid.uuid4(),
        title="t",
        body="b",
        group_key="",
        priority=priority,
        now=NOW,
    )
    assert provider.calls[0]["headers"]["Urgency"] == expected_urgency


def test_urgent_ttl_is_shorter_than_normal_which_is_shorter_than_low(db) -> None:
    ttls: dict[str, int] = {}
    for priority in (PRIORITY_URGENT, PRIORITY_NORMAL, PRIORITY_LOW):
        endpoint = f"https://fcm.googleapis.com/fcm/send/{priority}"
        service.subscribe(
            db,
            endpoint=endpoint,
            p256dh=_fake_subscription_keys()[0],
            auth=_fake_subscription_keys()[1],
        )
        provider = FakePushProvider()
        service.send_to_all(
            db,
            provider=provider,
            vapid_private_key=generate_key_pair().private_key,
            vapid_subject="mailto:owner@example.com",
            notification_id=uuid.uuid4(),
            title="t",
            body="b",
            group_key="",
            priority=priority,
            now=NOW,
        )
        ttls[priority] = int(
            next(c["headers"]["TTL"] for c in provider.calls if c["endpoint"] == endpoint)
        )
    assert ttls[PRIORITY_URGENT] < ttls[PRIORITY_NORMAL] < ttls[PRIORITY_LOW]


@pytest.mark.parametrize("status_reason", [PushError.REASON_EXPIRED])
def test_expired_subscription_is_deleted(db, status_reason: str) -> None:
    row = _subscribe(db)
    provider = FakePushProvider(responses={row.endpoint: PushError(status_reason, status_code=410)})
    outcome = service.send_to_all(
        db,
        provider=provider,
        vapid_private_key=generate_key_pair().private_key,
        vapid_subject="mailto:owner@example.com",
        notification_id=uuid.uuid4(),
        title="t",
        body="b",
        group_key="",
        priority=PRIORITY_NORMAL,
        now=NOW,
    )
    assert outcome.attempted == 1
    assert outcome.accepted == 0
    assert outcome.expired_subscription_ids == (row.id,)
    assert service.list_subscriptions(db) == []


def test_rate_limited_subscription_is_kept_and_marked_failed(db) -> None:
    row = _subscribe(db)
    provider = FakePushProvider(
        responses={
            row.endpoint: PushError(
                PushError.REASON_RATE_LIMITED, status_code=429, retry_after_s=30
            )
        }
    )
    outcome = service.send_to_all(
        db,
        provider=provider,
        vapid_private_key=generate_key_pair().private_key,
        vapid_subject="mailto:owner@example.com",
        notification_id=uuid.uuid4(),
        title="t",
        body="b",
        group_key="",
        priority=PRIORITY_NORMAL,
        now=NOW,
    )
    assert outcome.accepted == 0
    remaining = service.list_subscriptions(db)
    assert len(remaining) == 1
    assert remaining[0].last_error_reason == PushError.REASON_RATE_LIMITED
    assert remaining[0].failure_count == 1


def test_one_failing_subscription_does_not_stop_the_others(db) -> None:
    good = _subscribe(db, endpoint="https://fcm.googleapis.com/fcm/send/good")
    bad = _subscribe(db, endpoint="https://fcm.googleapis.com/fcm/send/bad")
    provider = FakePushProvider(
        responses={bad.endpoint: PushError(PushError.REASON_SERVER_ERROR, status_code=503)}
    )
    outcome = service.send_to_all(
        db,
        provider=provider,
        vapid_private_key=generate_key_pair().private_key,
        vapid_subject="mailto:owner@example.com",
        notification_id=uuid.uuid4(),
        title="t",
        body="b",
        group_key="",
        priority=PRIORITY_NORMAL,
        now=NOW,
    )
    assert outcome.attempted == 2
    assert outcome.accepted == 1
    db.refresh(good)
    db.refresh(bad)
    assert good.last_success_at.replace(tzinfo=UTC) == NOW
    assert bad.last_error_reason == PushError.REASON_SERVER_ERROR


def test_never_logs_or_sends_the_full_endpoint_in_the_encrypted_payload(db) -> None:
    """Task brief: never put secrets or tokens into the payload or URL. The endpoint
    itself behaves like a bearer credential and must never appear inside the encrypted
    plaintext this system builds."""
    row = _subscribe(db, endpoint="https://fcm.googleapis.com/fcm/send/super-secret-token-abc")
    provider = FakePushProvider()
    service.send_to_all(
        db,
        provider=provider,
        vapid_private_key=generate_key_pair().private_key,
        vapid_subject="mailto:owner@example.com",
        notification_id=uuid.uuid4(),
        title="t",
        body="b",
        group_key="",
        priority=PRIORITY_NORMAL,
        now=NOW,
    )
    payload = service.build_payload(notification_id=uuid.uuid4(), title="t", body="b", group_key="")
    assert b"super-secret-token-abc" not in payload
    assert row.endpoint not in payload.decode()
