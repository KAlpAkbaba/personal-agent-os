"""B11 req 372/389: the push rung wired into the notification ladder, between (the
still-unimplemented) sound and the inbox floor.

Uses ``FakePushProvider`` — this file is about LADDER behaviour (ordering, what gets
recorded, honest skip when unconfigured), not about RFC 8291/8292 correctness, which
``test_webpush_ece.py``/``test_webpush_vapid.py`` already prove independently.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.notifications import ladder as ladder_module
from app.notifications import service as notifications
from app.notifications.models import CHANNEL_INBOX, CHANNEL_PUSH, NotificationRow
from app.webpush import ece
from app.webpush.models import PushSubscriptionRow
from app.webpush.provider import FakePushProvider, PushError
from app.webpush.vapid import generate_key_pair

NOON = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)  # outside quiet hours


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    NotificationRow.__table__.create(eng)
    PushSubscriptionRow.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(session_factory):
    with session_factory() as session:
        yield session


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _add_subscription(
    session_factory, *, endpoint: str = "https://fcm.googleapis.com/fcm/send/x"
) -> None:
    private = ec.generate_private_key(ece.CURVE)
    with session_factory() as db:
        db.add(
            PushSubscriptionRow(
                endpoint=endpoint,
                p256dh=_b64url(ece.raw_public_key(private.public_key())),
                auth=_b64url(b"\x03" * 16),
                user_agent="pytest",
            )
        )
        db.commit()


def _record(db, **overrides) -> NotificationRow:
    base = {"kind": "artifact_ready", "title": "Rapor hazır", "body": "Bitti efendim.", "now": NOON}
    base.update(overrides)
    return notifications.record(db, **base)


def _push_rung(
    session_factory, *, provider=None, vapid_private_key="unset"
) -> ladder_module.PushRung:
    key = generate_key_pair().private_key if vapid_private_key == "unset" else vapid_private_key
    return ladder_module.PushRung(
        session_factory=session_factory,
        provider=provider or FakePushProvider(),
        vapid_private_key=key,
        vapid_subject="mailto:owner@example.com",
    )


# ------------------------------------------------------------- honest skip when unconfigured


def test_no_vapid_key_skips_push_honestly(db, session_factory) -> None:
    """Task brief: 'when no key is configured the push rung is skipped honestly ... and
    the rest of the ladder is unchanged'."""
    row = _record(db)
    rungs = ladder_module.default_rungs(push_rung=None)
    assert CHANNEL_PUSH not in rungs

    channel = ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)
    assert channel == CHANNEL_INBOX
    assert row.attempted_json == [CHANNEL_INBOX]


def test_push_rung_available_only_with_a_vapid_key(session_factory) -> None:
    assert _push_rung(session_factory, vapid_private_key=None).available() is False
    assert _push_rung(session_factory, vapid_private_key="present").available() is True


# ------------------------------------------------------------- success


def test_push_attempted_between_sound_and_inbox_and_reaches_the_owner(db, session_factory) -> None:
    _add_subscription(session_factory)
    row = _record(db)
    rungs = ladder_module.default_rungs(push_rung=_push_rung(session_factory))

    channel = ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert channel == CHANNEL_PUSH
    assert row.delivered_via == CHANNEL_PUSH
    assert row.attempted_json == [CHANNEL_PUSH]  # sound was never registered; toast wasn't either


def test_ladder_order_is_toast_then_push_then_inbox_regardless_of_dict_construction_order(
    db, session_factory
) -> None:
    """``rungs`` is an ordinary dict; the ladder's ORDER must come from
    ``app.notifications.models.LADDER``, never from insertion order — this constructs
    the dict with push registered before toast to prove that."""
    _add_subscription(session_factory)
    row = _record(db)

    class _FailingToast:
        name = "toast"

        def available(self) -> bool:
            return True

        def deliver(self, row: NotificationRow) -> bool:
            return False

    rungs = {
        "push": _push_rung(session_factory),
        "toast": _FailingToast(),
        "inbox": ladder_module.InboxRung(),
    }
    channel = ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert channel == CHANNEL_PUSH
    # attempted_json is stored SORTED (app.notifications.service), not in try order -
    # "toast" was tried and failed FIRST, which the ladder's own ordering guarantees
    # (LADDER puts toast before push), even though the stored set does not preserve it.
    assert row.attempted_json == sorted(["toast", CHANNEL_PUSH])


# ------------------------------------------------------------- no subscriptions / all fail


def test_no_subscriptions_falls_through_to_inbox(db, session_factory) -> None:
    row = _record(db)
    rungs = ladder_module.default_rungs(push_rung=_push_rung(session_factory))

    channel = ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert channel == CHANNEL_INBOX
    assert row.attempted_json == sorted([CHANNEL_PUSH, CHANNEL_INBOX])
    assert row.delivered_via == CHANNEL_INBOX


def test_all_subscriptions_failing_falls_through_to_inbox(db, session_factory) -> None:
    _add_subscription(session_factory, endpoint="https://fcm.googleapis.com/fcm/send/gone")
    provider = FakePushProvider(
        responses={
            "https://fcm.googleapis.com/fcm/send/gone": PushError(
                PushError.REASON_SERVER_ERROR, status_code=503
            )
        }
    )
    row = _record(db)
    rungs = ladder_module.default_rungs(push_rung=_push_rung(session_factory, provider=provider))

    channel = ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert channel == CHANNEL_INBOX
    assert row.delivered_via == CHANNEL_INBOX


def test_expired_subscription_is_removed_and_ladder_falls_through(db, session_factory) -> None:
    """RFC 8030's 404/410 must expire the subscription (task brief) AND still let the
    ladder honestly fall to inbox — an expired subscription is not a delivery."""
    endpoint = "https://fcm.googleapis.com/fcm/send/expired"
    _add_subscription(session_factory, endpoint=endpoint)
    provider = FakePushProvider(
        responses={endpoint: PushError(PushError.REASON_EXPIRED, status_code=410)}
    )
    row = _record(db)
    rungs = ladder_module.default_rungs(push_rung=_push_rung(session_factory, provider=provider))

    channel = ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert channel == CHANNEL_INBOX
    with session_factory() as check:
        from sqlalchemy import select

        remaining = check.execute(select(PushSubscriptionRow)).scalars().all()
        assert remaining == []


# ------------------------------------------------------------- no over-claiming delivery


def test_push_acceptance_does_not_set_read_at_or_claim_the_owner_saw_it(
    db, session_factory
) -> None:
    """'a queue is not a delivery': a push rung success sets delivered_via/delivered_at
    (the ladder's own 'a channel accepted responsibility' bookkeeping) but must NEVER
    touch read_at — that field means the OWNER opened it, which push acceptance from the
    push service can never establish."""
    _add_subscription(session_factory)
    row = _record(db)
    rungs = ladder_module.default_rungs(push_rung=_push_rung(session_factory))

    ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert row.delivered_via == CHANNEL_PUSH
    assert row.read_at is None
