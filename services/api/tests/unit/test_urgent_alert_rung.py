"""urgent-alert-rung: the Pushover emergency-priority rung, in its own package.

Nothing here reaches the network: every provider call goes through a real ``httpx.Client``
on ``httpx.MockTransport``, so the form that would leave the machine and the log lines httpx
itself writes are both the real ones. The ladder is not wired to this rung (a separate card);
these tests prove the parts the wiring card will plug in.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from app.notifications import ladder
from app.notifications.models import NotificationRow
from app.urgent_alert import text
from app.urgent_alert.provider import AlarmProvider, AlarmReceipt, ReceiptStatus
from app.urgent_alert.pushover import PushoverProvider
from app.urgent_alert.receipts import InMemoryReceiptStore, poll_open_receipts
from app.urgent_alert.rung import AlarmRung, marked_important

APP_TOKEN = "aAppTokenSecretValue000000000x"
USER_KEY = "uUserKeySecretValue0000000000y"
RECEIPT = "rReceiptId00000000000000000001"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
LINK = "https://home-pc.tail1234.ts.net/notifications"
NOTE_ID = "3f2b8c1e-4d5a-4b6c-8e9f-0a1b2c3d4e5f"


class FakeServer:
    """Pushover's three endpoints, answering from a script the test sets."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.send_reply: httpx.Response | Exception = httpx.Response(
            200, json={"status": 1, "request": "req1", "receipt": RECEIPT}
        )
        self.poll_reply: httpx.Response | Exception = httpx.Response(
            200, json=_receipt_json(acknowledged=0, expired=0)
        )
        self.cancel_reply: httpx.Response | Exception = httpx.Response(
            200, json={"status": 1, "request": "req2"}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/messages.json"):
            reply = self.send_reply
        elif path.endswith("/cancel.json"):
            reply = self.cancel_reply
        else:
            reply = self.poll_reply
        if isinstance(reply, Exception):
            raise reply
        return reply


def _receipt_json(*, acknowledged: int, expired: int, ack_at: int = 0) -> dict:
    return {
        "status": 1,
        "acknowledged": acknowledged,
        "acknowledged_at": ack_at,
        "acknowledged_by": USER_KEY if acknowledged else "",
        "acknowledged_by_device": "iphone" if acknowledged else "",
        "last_delivered_at": int(NOW.timestamp()) + 60,
        "expired": expired,
        "expires_at": int(NOW.timestamp()) + 10800,
        "called_back": 0,
        "called_back_at": 0,
        "request": "req3",
    }


def _provider(server: FakeServer, **kwargs) -> PushoverProvider:
    client = httpx.Client(transport=httpx.MockTransport(server))
    return PushoverProvider(APP_TOKEN, USER_KEY, client=client, clock=lambda: NOW, **kwargs)


def _row(*, important: bool, category: str = "Aktivra") -> NotificationRow:
    return NotificationRow(
        id=uuid.uuid4(),
        kind="test",
        title="Ahmet Yılmaz 1.250 TL ödedi",
        body="kişisel içerik asla gitmez",
        data_json={"important": important, "alert_category": category},
    )


# --- provider -------------------------------------------------------------------------------


def test_send_posts_an_emergency_priority_form() -> None:
    server = FakeServer()
    receipt = _provider(server).send(text.TITLE, "Aktivra", LINK)

    assert receipt == AlarmReceipt(
        receipt_id=RECEIPT, sent_at=NOW, expires_at=NOW + timedelta(seconds=10800)
    )
    (request,) = server.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.pushover.net/1/messages.json"
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    assert form["priority"] == "2"
    assert int(form["retry"]) >= 30
    assert int(form["expire"]) <= 10800
    assert form["title"] == "JARVIS: önemli"
    assert form["message"] == "Aktivra"
    assert form["url"] == LINK
    assert form["token"] == APP_TOKEN
    assert form["user"] == USER_KEY
    assert "callback" not in form  # Cloud Core opens no inbound port
    assert APP_TOKEN not in str(request.url) and USER_KEY not in str(request.url)


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(400, json={"status": 0, "errors": ["user identifier is invalid"]}),
        httpx.Response(429, json={"status": 0, "errors": ["quota"]}),
        httpx.Response(500, text="oops"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"status": 0, "errors": ["x"]}),
        httpx.Response(200, json={"status": 1, "request": "r"}),  # no receipt
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("down"),
    ],
)
def test_send_failures_are_none_never_an_exception(reply) -> None:
    server = FakeServer()
    server.send_reply = reply
    assert _provider(server).send(text.TITLE, "ev", LINK) is None


def test_poll_parses_the_receipt_without_keeping_the_user_key() -> None:
    server = FakeServer()
    ack = int(NOW.timestamp()) + 120
    server.poll_reply = httpx.Response(
        200, json=_receipt_json(acknowledged=1, expired=0, ack_at=ack)
    )

    status = _provider(server).poll(RECEIPT)

    assert status == ReceiptStatus(
        acknowledged_at=datetime.fromtimestamp(ack, UTC),
        expired=False,
        last_delivered_at=NOW + timedelta(seconds=60),
        expires_at=NOW + timedelta(seconds=10800),
    )
    assert USER_KEY not in repr(status)
    (request,) = server.requests
    assert request.method == "GET"
    assert request.url.path == f"/1/receipts/{RECEIPT}.json"
    assert request.url.params["token"] == APP_TOKEN


@pytest.mark.parametrize(
    "reply",
    [httpx.Response(404, json={"status": 0}), httpx.Response(503), httpx.ReadTimeout("slow")],
)
def test_poll_and_cancel_failures_are_quiet(reply) -> None:
    server = FakeServer()
    server.poll_reply = reply
    server.cancel_reply = reply
    provider = _provider(server)
    assert provider.poll(RECEIPT) is None
    assert provider.cancel(RECEIPT) is False


def test_a_receipt_id_that_is_not_a_receipt_never_becomes_a_path() -> None:
    server = FakeServer()
    provider = _provider(server)
    assert provider.poll("../messages") is None
    assert provider.cancel("a/b") is False
    assert server.requests == []


def test_cancel_posts_the_app_token_in_the_form() -> None:
    server = FakeServer()
    assert _provider(server).cancel(RECEIPT) is True
    (request,) = server.requests
    assert request.url.path == f"/1/receipts/{RECEIPT}/cancel.json"
    assert parse_qs(request.content.decode())["token"] == [APP_TOKEN]


@pytest.mark.parametrize(
    "kwargs", [{"retry_s": 29}, {"expire_s": 10801}, {"retry_s": 120, "expire_s": 60}]
)
def test_a_misconfigured_provider_is_loud(kwargs) -> None:
    with pytest.raises(ValueError):
        PushoverProvider(APP_TOKEN, USER_KEY, client=httpx.Client(), **kwargs)


def test_no_key_value_reaches_any_log_record(caplog, capsys) -> None:
    """The receipt query carries the app token in its URL (Pushover offers no other way)
    and httpx logs every request URL at INFO. The masking filter is what stands between
    that line and the log; a 4xx error body is not logged either."""
    caplog.set_level(logging.DEBUG)
    server = FakeServer()
    provider = _provider(server)
    provider.send(text.TITLE, "ev", LINK)
    server.poll_reply = httpx.Response(200, json=_receipt_json(acknowledged=1, expired=0, ack_at=1))
    provider.poll(RECEIPT)
    provider.cancel(RECEIPT)
    server.send_reply = httpx.Response(400, json={"status": 0, "errors": [f"bad {USER_KEY}"]})
    provider.send(text.TITLE, "ev", LINK)

    assert any("receipts" in r.getMessage() for r in caplog.records), "httpx logged nothing"
    logged = "\n".join(f"{r.getMessage()} {r.args!r}" for r in caplog.records)
    logged += capsys.readouterr().out
    assert APP_TOKEN not in logged
    assert USER_KEY not in logged


def test_configured_needs_both_keys() -> None:
    assert PushoverProvider(APP_TOKEN, USER_KEY, client=httpx.Client()).configured()
    assert not PushoverProvider("", USER_KEY, client=httpx.Client()).configured()
    assert not PushoverProvider(APP_TOKEN, "", client=httpx.Client()).configured()


def test_pushover_is_an_alarm_provider() -> None:
    provider: AlarmProvider = _provider(FakeServer())
    assert isinstance(provider, AlarmProvider)


# --- rung -----------------------------------------------------------------------------------


def _rung(server: FakeServer, store: InMemoryReceiptStore, provider=None) -> AlarmRung:
    return AlarmRung(
        provider if provider is not None else _provider(server),
        store,
        link_for=lambda row: LINK,
    )


def test_alarm_rung_matches_the_ladders_rung_protocol() -> None:
    rung: ladder.Rung = _rung(FakeServer(), InMemoryReceiptStore())
    for member in ("available", "deliver"):
        expected = inspect.signature(getattr(ladder.Rung, member))
        actual = inspect.signature(getattr(type(rung), member))
        assert list(actual.parameters) == list(expected.parameters), member
    assert rung.name == "alarm"
    assert isinstance(rung.name, str) and len(rung.name) <= 16  # delivered_via is String(16)


def test_without_keys_the_rung_is_skipped() -> None:
    store = InMemoryReceiptStore()
    assert not _rung(
        FakeServer(), store, PushoverProvider("", "", client=httpx.Client())
    ).available()
    assert not AlarmRung(None, store, link_for=lambda row: LINK).available()
    assert _rung(FakeServer(), store).available()


def test_an_unimportant_row_sends_nothing() -> None:
    server = FakeServer()
    store = InMemoryReceiptStore()
    assert _rung(server, store).deliver(_row(important=False)) is False
    assert server.requests == []
    assert store.list_open() == []


def test_an_important_row_rings_and_keeps_one_open_receipt() -> None:
    server = FakeServer()
    store = InMemoryReceiptStore()
    row = _row(important=True)

    assert _rung(server, store).deliver(row) is True

    (opened,) = store.list_open()
    assert opened.notification_id == row.id
    assert opened.receipt.receipt_id == RECEIPT
    form = parse_qs(server.requests[0].content.decode())
    assert form["title"] == ["JARVIS: önemli"]
    assert form["message"] == ["Aktivra"]  # the row's title and body never leave
    assert "Ahmet" not in server.requests[0].content.decode()


def test_a_refused_send_or_category_is_not_reached() -> None:
    server = FakeServer()
    server.send_reply = httpx.Response(500)
    store = InMemoryReceiptStore()
    assert _rung(server, store).deliver(_row(important=True)) is False
    assert store.list_open() == []

    server = FakeServer()
    assert _rung(server, store).deliver(_row(important=True, category="Ahmet")) is False
    assert server.requests == []


def test_a_receipt_that_cannot_be_kept_is_cancelled() -> None:
    class BrokenStore(InMemoryReceiptStore):
        def open(self, notification_id, receipt) -> None:
            raise RuntimeError("db down")

    server = FakeServer()
    assert _rung(server, BrokenStore()).deliver(_row(important=True)) is False
    assert [r.url.path for r in server.requests][-1].endswith("/cancel.json")


def test_importance_is_read_in_one_place() -> None:
    assert marked_important(_row(important=True))
    assert not marked_important(_row(important=False))
    assert not marked_important(NotificationRow(id=uuid.uuid4(), kind="x", data_json={}))
    assert not marked_important(
        NotificationRow(id=uuid.uuid4(), kind="x", data_json={"important": "yes"})
    )


# --- receipts -------------------------------------------------------------------------------


def _store_with_one_open() -> tuple[InMemoryReceiptStore, uuid.UUID]:
    store = InMemoryReceiptStore()
    nid = uuid.uuid4()
    store.open(nid, AlarmReceipt(RECEIPT, NOW, NOW + timedelta(seconds=10800)))
    return store, nid


def test_acknowledged_is_seen_once() -> None:
    server = FakeServer()
    ack = int(NOW.timestamp()) + 300
    server.poll_reply = httpx.Response(
        200, json=_receipt_json(acknowledged=1, expired=0, ack_at=ack)
    )
    store, nid = _store_with_one_open()
    provider = _provider(server)

    (event,) = poll_open_receipts(store, provider, NOW + timedelta(minutes=6))

    assert (event.kind, event.notification_id, event.receipt_id) == ("seen", nid, RECEIPT)
    assert event.at == datetime.fromtimestamp(ack, UTC)
    assert store.list_open() == []
    assert poll_open_receipts(store, provider, NOW + timedelta(minutes=7)) == []


def test_expired_and_unseen_is_unseen_once() -> None:
    server = FakeServer()
    server.poll_reply = httpx.Response(200, json=_receipt_json(acknowledged=0, expired=1))
    store, nid = _store_with_one_open()
    provider = _provider(server)

    (event,) = poll_open_receipts(store, provider, NOW + timedelta(hours=3, minutes=1))

    assert (event.kind, event.notification_id) == ("unseen", nid)
    assert event.at == NOW + timedelta(seconds=10800)
    assert store.list_open() == []
    assert poll_open_receipts(store, provider, NOW + timedelta(hours=4)) == []


def test_still_ringing_or_unreadable_stays_open() -> None:
    server = FakeServer()
    store, _ = _store_with_one_open()
    provider = _provider(server)
    assert poll_open_receipts(store, provider, NOW + timedelta(minutes=1)) == []
    server.poll_reply = httpx.Response(503)
    assert poll_open_receipts(store, provider, NOW + timedelta(minutes=2)) == []
    assert len(store.list_open()) == 1


def test_a_receipt_pushover_has_forgotten_is_closed_unseen() -> None:
    """Pushover keeps a receipt for a week; an open record must not outlive it."""
    server = FakeServer()
    server.poll_reply = httpx.Response(404, json={"status": 0})
    store, _ = _store_with_one_open()
    (event,) = poll_open_receipts(store, _provider(server), NOW + timedelta(days=7, minutes=1))
    assert event.kind == "unseen"
    assert store.list_open() == []


def test_no_open_receipt_means_no_request() -> None:
    server = FakeServer()
    assert poll_open_receipts(InMemoryReceiptStore(), _provider(server), NOW) == []
    assert server.requests == []


# --- text guard -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "reason"),
    [
        ("Ahmet Yılmaz", "more_than_one_word"),
        ("1.250 TL", "amount"),
        ("₺300", "amount"),
        ("a@b.com", "email"),
        ("+90 532 123 45 67", "phone"),
        ("banka", "not_a_category"),
        ("x" * 1025, "too_long"),
    ],
)
def test_the_text_guard_refuses_anything_personal(category: str, reason: str) -> None:
    with pytest.raises(text.TextRefused) as refused:
        text.compose(category, LINK)
    assert refused.value.reason == reason


@pytest.mark.parametrize("category", ["Aktivra", "ev", "haber", "sistem"])
def test_a_category_and_a_tailnet_link_pass(category: str) -> None:
    assert text.compose(category, LINK) == ("JARVIS: önemli", category, LINK)


@pytest.mark.parametrize(
    "link",
    [
        "http://home-pc.tail1234.ts.net/inbox",
        "https://example.com/inbox",
        "https://user:pw@home-pc.tail1234.ts.net/",
        "https://home-pc.tail1234.ts.net/" + "a" * 600,
    ],
)
def test_only_a_tailnet_https_link_passes(link: str) -> None:
    with pytest.raises(text.TextRefused):
        text.compose("ev", link)


@pytest.mark.parametrize(
    "link",
    [LINK, f"https://home-pc.tail1234.ts.net/notifications/{NOTE_ID}"],
)
def test_the_inbox_and_one_opaque_notification_id_pass(link: str) -> None:
    assert text.link_refusal(link) is None


# The link leaves the country with the body (Pushover, US): a name, an amount, an e-mail or a
# phone number must not ride out in its path, query or fragment either (inspector, 2026-10-05).
@pytest.mark.parametrize(
    ("link", "reason"),
    [
        (
            "https://pc.tail1.ts.net/notifications?name=Ahmet%20Yilmaz&tutar=1.250%20TL",
            "link_has_query",
        ),
        ("https://pc.tail1.ts.net/inbox?name=Ahmet%20Yilmaz&mail=a@b.com", "link_has_query"),
        ("https://pc.tail1.ts.net/notifications?", "link_has_query"),
        ("https://pc.tail1.ts.net#a@b.com", "link_has_fragment"),
        ("https://pc.tail1.ts.net/notifications#", "link_has_fragment"),
        ("https://pc.tail1.ts.net/Ahmet-Yilmaz/+905321234567", "link_path_not_allowed"),
        ("https://pc.tail1.ts.net/notifications/1.250-TL", "link_path_not_allowed"),
        ("https://pc.tail1.ts.net/notifications/a@b.com", "link_path_not_allowed"),
        ("https://pc.tail1.ts.net/notifications/%41hmet", "link_path_not_allowed"),
        ("https://pc.tail1.ts.net/notifications/" + NOTE_ID.upper(), "link_path_not_allowed"),
        ("https://pc.tail1.ts.net/notifications/" + NOTE_ID + "/x", "link_path_not_allowed"),
        ("https://pc.tail1.ts.net/inbox", "link_path_not_allowed"),
        ("https://pc.tail1.ts.net/", "link_path_not_allowed"),
        ("https://pc.tail1.ts.net/notifi\tcations", "link_not_plain"),
        ("https://pc.tail1.ts.net/notifications;Ahmet", "link_path_not_allowed"),
    ],
)
def test_a_link_carrying_anything_personal_is_refused(link: str, reason: str) -> None:
    with pytest.raises(text.TextRefused) as refused:
        text.compose("ev", link)
    assert refused.value.reason == reason
