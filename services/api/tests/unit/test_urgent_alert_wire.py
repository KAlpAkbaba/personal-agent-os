"""urgent-alert-wire: the alarm rung on the ladder, its receipts kept and asked about.

The package (``app.urgent_alert``) was built by urgent-alert-rung; this file proves the
wiring: the ladder's order (toast -> alarm -> sound -> push -> inbox), the night trap (a toast
shown to an empty desk does not end an important row's ladder), the one source of
"important" (``app.telephony.policy``), the receipt table and its loop, "seen another way"
(the owner read it in the inbox -> the phone stops ringing), the two routes and their keys.

Nothing reaches the network: Pushover is ``httpx.MockTransport`` behind the real provider.
The database is SQLite here; ``tests/integration/test_urgent_alert_receipts_pg.py`` runs the
receipt table on PostgreSQL through the migration.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.devices.status import DeviceStatus, DeviceStatusRegistry
from app.ledger import vocabulary as v
from app.ledger.models import ActivityEventRow
from app.notifications import ladder
from app.notifications import service as notifications
from app.notifications.models import (
    CHANNEL_ALARM,
    CHANNEL_INBOX,
    CHANNEL_PUSH,
    CHANNEL_SOUND,
    CHANNEL_TOAST,
    LADDER,
    PRIORITY_URGENT,
    NotificationRow,
)
from app.routines.dispatch import DeviceRunResult
from app.telephony import policy
from app.urgent_alert import presence, text, wiring
from app.urgent_alert.loop import HEALTH_NAME, UrgentAlertLoop
from app.urgent_alert.models import UrgentAlertReceiptRow
from app.urgent_alert.rung import AlarmRung

APP_TOKEN = "aAppTokenSecretValue000000000x"
USER_KEY = "uUserKeySecretValue0000000000y"
RECEIPT = "rReceiptId00000000000000000001"
LINK_BASE = "https://home-pc.tail1234.ts.net"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)  # 15:00 Istanbul, outside quiet hours


# ------------------------------------------------------------------ doubles


class FakePushover:
    """Pushover's three endpoints, counting what reached them."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.receipt_json: dict = _receipt(acknowledged=0, expired=0)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/messages.json"):
            return httpx.Response(200, json={"status": 1, "request": "q1", "receipt": RECEIPT})
        if path.endswith("/cancel.json"):
            return httpx.Response(200, json={"status": 1, "request": "q2"})
        return httpx.Response(200, json=self.receipt_json)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def _receipt(*, acknowledged: int, expired: int, ack_at: datetime | None = None) -> dict:
    return {
        "status": 1,
        "acknowledged": acknowledged,
        "acknowledged_at": int(ack_at.timestamp()) if ack_at else 0,
        "acknowledged_by": USER_KEY if acknowledged else "",
        "last_delivered_at": int(NOW.timestamp()) + 60,
        "expired": expired,
        "expires_at": int(NOW.timestamp()) + 10800,
        "request": "q3",
    }


def _settings(**overrides) -> Settings:
    values = {
        "urgent_alert_pushover_app_token": SecretStr(APP_TOKEN),
        "urgent_alert_pushover_user_key": SecretStr(USER_KEY),
        "urgent_alert_link_base": LINK_BASE,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for model in (NotificationRow, UrgentAlertReceiptRow, ActivityEventRow):
        model.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def server() -> FakePushover:
    return FakePushover()


def _rung(factory, server: FakePushover, **settings) -> AlarmRung:
    rung = wiring.build_alarm_rung(
        _settings(**settings),
        factory,
        client=httpx.Client(transport=httpx.MockTransport(server)),
        clock=lambda: NOW,
    )
    assert rung is not None
    return rung


def _record(factory, kind: str = policy.KIND_SECURITY_CRITICAL, **extra) -> NotificationRow:
    with factory() as db:
        return notifications.record(
            db, kind=kind, title="Başlık", body="Gövde", priority=PRIORITY_URGENT, now=NOW, **extra
        )


class _Rung:
    def __init__(self, name: str, *, reaches: bool) -> None:
        self.name = name
        self.reaches = reaches
        self.calls = 0

    def available(self) -> bool:
        return True

    def deliver(self, row: NotificationRow) -> bool:  # noqa: ARG002
        self.calls += 1
        return self.reaches


def _shown_toast_port():
    from unittest.mock import create_autospec

    from app.routines.dispatch import DeviceActionPort

    port = create_autospec(DeviceActionPort, instance=True)
    port.run.return_value = DeviceRunResult(True, result={"shown": True})
    return port


def _ledger(factory, event_type: str) -> list[ActivityEventRow]:
    with factory() as db:
        return list(
            db.execute(select(ActivityEventRow).where(ActivityEventRow.event_type == event_type))
            .scalars()
            .all()
        )


def _receipt_row(factory) -> UrgentAlertReceiptRow:
    with factory() as db:
        return db.execute(select(UrgentAlertReceiptRow)).scalar_one()


# ----------------------------------------------------- acceptance 1: the order


def test_the_ladder_rings_the_phone_after_the_desk_and_before_everything_that_buzzes() -> None:
    assert LADDER == (CHANNEL_TOAST, CHANNEL_ALARM, CHANNEL_SOUND, CHANNEL_PUSH, CHANNEL_INBOX)


def test_an_important_row_whose_toast_failed_rings_the_phone(factory, server) -> None:
    alarm = _rung(factory, server)
    push = _Rung(CHANNEL_PUSH, reaches=True)
    row = _record(factory)
    rungs = {
        CHANNEL_TOAST: _Rung(CHANNEL_TOAST, reaches=False),
        CHANNEL_ALARM: alarm,
        CHANNEL_PUSH: push,
        CHANNEL_INBOX: ladder.InboxRung(),
    }
    with factory() as db:
        row = db.get(NotificationRow, row.id)
        channel = ladder.deliver_one(db, row, rungs=rungs, now=NOW)
        assert channel == CHANNEL_ALARM
        assert row.delivered_via == CHANNEL_ALARM
    assert push.calls == 0
    assert server.paths() == ["/1/messages.json"]
    sent = _ledger(factory, v.EVENT_TYPE_ALERT_SENT)
    assert [e.source_ref for e in sent] == [f"notification:{row.id}"]
    assert _receipt_row(factory).receipt_id == RECEIPT


def test_a_row_that_is_not_important_never_reaches_pushover_and_steps_to_push(
    factory, server
) -> None:
    alarm = _rung(factory, server)
    push = _Rung(CHANNEL_PUSH, reaches=True)
    row = _record(factory, kind="artifact_ready")
    rungs = {
        CHANNEL_TOAST: _Rung(CHANNEL_TOAST, reaches=False),
        CHANNEL_ALARM: alarm,
        CHANNEL_PUSH: push,
        CHANNEL_INBOX: ladder.InboxRung(),
    }
    with factory() as db:
        row = db.get(NotificationRow, row.id)
        assert ladder.deliver_one(db, row, rungs=rungs, now=NOW) == CHANNEL_PUSH
    assert server.requests == []
    assert push.calls == 1


# ------------------------------------------------- acceptance 2: the night trap


def _toast_then_alarm(factory, server, *, owner_present):
    alarm = _rung(factory, server)
    rungs = ladder.default_rungs(
        device_action=_shown_toast_port(), alarm_rung=alarm, owner_present=owner_present
    )
    return rungs


def test_a_toast_shown_to_nobody_does_not_end_an_important_row(factory, server) -> None:
    """The home PC is on at 03:00 and nobody is at it: Windows shows the toast, and before
    this rule the ladder stopped there and the phone never rang."""
    rungs = _toast_then_alarm(factory, server, owner_present=lambda: False)
    row = _record(factory)
    with factory() as db:
        row = db.get(NotificationRow, row.id)
        assert ladder.deliver_one(db, row, rungs=rungs, now=NOW) == CHANNEL_ALARM
        assert CHANNEL_TOAST in row.attempted_json
    assert server.paths() == ["/1/messages.json"]


def test_a_toast_shown_to_the_owner_at_the_desk_ends_the_ladder(factory, server) -> None:
    rungs = _toast_then_alarm(factory, server, owner_present=lambda: True)
    row = _record(factory)
    with factory() as db:
        row = db.get(NotificationRow, row.id)
        assert ladder.deliver_one(db, row, rungs=rungs, now=NOW) == CHANNEL_TOAST
    assert server.requests == []


def test_without_a_presence_reading_an_important_toast_never_ends_the_ladder(
    factory, server
) -> None:
    """The first version's rule when the companion reports no idle time: loud beats silent."""
    alarm = _rung(factory, server)
    toast = ladder.ToastRung(device_action=_shown_toast_port(), must_reach_owner=alarm.rings_for)
    row = _record(factory)
    assert toast.deliver(row) is False


def test_an_ordinary_row_still_ends_at_a_shown_toast(factory, server) -> None:
    rungs = _toast_then_alarm(factory, server, owner_present=lambda: False)
    row = _record(factory, kind="artifact_ready")
    with factory() as db:
        row = db.get(NotificationRow, row.id)
        assert ladder.deliver_one(db, row, rungs=rungs, now=NOW) == CHANNEL_TOAST


def _status(idle_s: float | None, observed: datetime) -> DeviceStatus:
    return DeviceStatus(device_id=uuid.uuid4(), observed_at=observed, input_idle_s=idle_s)


@pytest.mark.parametrize(
    ("idle_s", "age_s", "present"),
    [
        (30.0, 5, True),  # typed half a minute ago, heartbeat fresh
        (110.0, 5, True),  # 115 s in all: still under two minutes
        (100.0, 30, False),  # 130 s in all: the heartbeat's age counts as idle too
        (300.0, 5, False),  # away
        (10.0, 600, False),  # a stale heartbeat says nothing about now
        (None, 5, False),  # a companion that does not report idle time
    ],
)
def test_presence_is_under_two_minutes_of_idle_on_a_fresh_report(idle_s, age_s, present) -> None:
    class _Registry(DeviceStatusRegistry):
        def __init__(self, status):
            super().__init__()
            self._one = status

        def all(self):
            return {self._one.device_id: self._one}

    registry = _Registry(_status(idle_s, NOW - timedelta(seconds=age_s)))
    assert presence.owner_present(now=NOW, registry=registry) is present


def test_no_device_at_all_is_not_presence() -> None:
    assert presence.owner_present(now=NOW, registry=DeviceStatusRegistry()) is False


# --------------------------------------------- acceptance 3: no keys, no alarm


@pytest.mark.parametrize(
    "missing", ["urgent_alert_pushover_app_token", "urgent_alert_pushover_user_key"]
)
def test_without_both_keys_there_is_no_alarm_rung(factory, missing) -> None:
    settings = _settings(**{missing: SecretStr("")})
    assert wiring.build_alarm_rung(settings, factory) is None
    rungs = ladder.default_rungs(device_action=_shown_toast_port(), alarm_rung=None)
    assert CHANNEL_ALARM not in rungs


def test_without_a_link_base_there_is_no_alarm_rung(factory) -> None:
    """Every alarm would be refused at the link gate - a rung that can only say no is absent."""
    assert wiring.build_alarm_rung(_settings(urgent_alert_link_base=""), factory) is None


def test_with_no_alarm_rung_an_important_toast_ends_the_ladder_as_before() -> None:
    toast = ladder.default_rungs(device_action=_shown_toast_port())[CHANNEL_TOAST]
    row = NotificationRow(
        id=uuid.uuid4(),
        kind=policy.KIND_SECURITY_CRITICAL,
        title="Güvenlik",
        body="Acil durum.",
        priority=PRIORITY_URGENT,
        group_key="",
        data_json={},
    )
    assert toast.deliver(row) is True


# ---------------------------------------- acceptance 7: one source of important


def test_important_is_the_telephony_policy_list_and_nothing_else() -> None:
    assert policy.important_notification_kinds() == frozenset(
        {*policy._NOTIFICATION_KINDS, *policy.ALERT_ONLY_KINDS}
    )
    for kind in policy.important_notification_kinds():
        row = NotificationRow(id=uuid.uuid4(), kind=kind, data_json={})
        assert wiring.is_important(row), kind
        assert wiring.category_for(row) in text.CATEGORIES, kind
    assert not wiring.is_important(NotificationRow(id=uuid.uuid4(), kind="artifact_ready"))


def test_every_reason_to_call_has_an_alarm_category() -> None:
    """A kind added to the call list without a category would ring with a refused body."""
    called = {k for k in policy.RULES if k != policy.KIND_TEST_CALL}
    assert called | set(policy.ALERT_ONLY_KINDS) == set(policy.ALERT_CATEGORIES)
    assert set(policy._NOTIFICATION_KINDS.values()) <= set(policy.ALERT_CATEGORIES)


def test_a_kind_added_to_the_call_list_is_important_at_once(monkeypatch) -> None:
    monkeypatch.setitem(policy._NOTIFICATION_KINDS, "home.pulse_lost", policy.KIND_ALARM_CALL_ME)
    row = NotificationRow(id=uuid.uuid4(), kind="home.pulse_lost", data_json={})
    assert wiring.is_important(row)
    assert wiring.category_for(row) == "ev"


def test_the_alarm_test_is_important_but_never_a_phone_call() -> None:
    assert policy.call_kind_for_notification(policy.KIND_URGENT_ALERT_TEST) is None
    assert policy.is_important_notification(policy.KIND_URGENT_ALERT_TEST)


def test_categories_follow_the_card() -> None:
    c = policy.ALERT_CATEGORIES
    assert c[policy.KIND_SECURITY_CRITICAL] == "sistem"
    assert c[policy.KIND_RELEASE_FAILED] == "sistem"
    assert c[policy.KIND_SPEND_UNANSWERED] == "sistem"
    assert c[policy.KIND_AKTIVRA_IMPORTANT] == "Aktivra"
    assert c[policy.KIND_ALARM_CALL_ME] == "ev"


# ----------------------------------------------------------- the link root


def test_the_link_is_the_configured_root_exactly(factory, server) -> None:
    rung = _rung(factory, server)
    row = _record(factory)
    assert wiring.link_for_base(LINK_BASE)(row) == f"{LINK_BASE}/notifications/{row.id}"
    assert text.link_refusal(f"{LINK_BASE}/notifications/{row.id}", root=LINK_BASE) is None
    for other in (
        "https://evil.ts.net/notifications",
        "https://home-pc.tail1234.ts.net.evil.ts.net/notifications",
        "https://x.home-pc.tail1234.ts.net/notifications",
        "https://home-pc.tail1234.ts.net:8443/notifications",
    ):
        assert text.link_refusal(other, root=LINK_BASE) == "link_not_configured_root", other
    assert rung.available()


def test_a_refused_link_writes_alert_refused_and_sends_nothing(factory, server) -> None:
    rung = wiring.build_alarm_rung(
        _settings(),
        factory,
        client=httpx.Client(transport=httpx.MockTransport(server)),
        clock=lambda: NOW,
        link_for=lambda row: "https://evil.ts.net/notifications",
    )
    row = _record(factory)
    assert rung.deliver(row) is False
    assert server.requests == []
    refused = _ledger(factory, v.EVENT_TYPE_ALERT_REFUSED)
    assert [e.detail_json.get("reason") for e in refused] == ["link_not_configured_root"]
    assert "evil" not in repr([e.detail_json for e in refused])


# ------------------------------------------------- acceptance 4/5: the loop


def _loop(factory, server, clock=lambda: NOW) -> UrgentAlertLoop:
    return wiring.build_receipt_loop(_settings(), factory, _rung(factory, server), clock=clock)


def _ring(factory, server) -> NotificationRow:
    rung = _rung(factory, server)
    row = _record(factory)
    assert rung.deliver(row) is True
    server.requests.clear()
    return row


def test_no_open_receipt_means_no_request(factory, server) -> None:
    loop = _loop(factory, server)
    assert loop.run_once(NOW) == {"seen": 0, "unseen": 0, "cancelled": 0}
    assert server.requests == []
    assert loop.health_check()["status"] in {"ok", "skipped"}


def test_an_acknowledged_alarm_is_seen_in_the_ledger_and_the_table(factory, server) -> None:
    row = _ring(factory, server)
    ack = NOW + timedelta(minutes=3)
    server.receipt_json = _receipt(acknowledged=1, expired=0, ack_at=ack)
    loop = _loop(factory, server)

    assert loop.run_once(NOW + timedelta(minutes=4))["seen"] == 1
    receipt = _receipt_row(factory)
    assert receipt.outcome == "seen" and receipt.source == "pushover"
    assert receipt.seen_at.replace(tzinfo=UTC) == ack
    seen = _ledger(factory, v.EVENT_TYPE_ALERT_SEEN)
    assert [e.source_ref for e in seen] == [f"notification:{row.id}"]
    assert USER_KEY not in repr([e.detail_json for e in seen])
    server.requests.clear()
    assert loop.run_once(NOW + timedelta(minutes=5))["seen"] == 0
    assert server.requests == []


def test_an_expired_alarm_is_unseen(factory, server) -> None:
    _ring(factory, server)
    server.receipt_json = _receipt(acknowledged=0, expired=1)
    assert _loop(factory, server).run_once(NOW + timedelta(hours=3))["unseen"] == 1
    assert _receipt_row(factory).outcome == "unseen"
    assert len(_ledger(factory, v.EVENT_TYPE_ALERT_UNSEEN)) == 1


def test_a_restart_keeps_asking_about_open_receipts(factory, server) -> None:
    _ring(factory, server)
    fresh = _loop(factory, server)  # a new process: nothing in memory
    fresh.run_once(NOW + timedelta(minutes=1))
    assert server.paths() == [f"/1/receipts/{RECEIPT}.json"]


def test_read_in_the_inbox_stops_the_ringing(factory, server) -> None:
    """Acceptance 5: the owner read it on the web - the phone must not ring for three hours."""
    row = _ring(factory, server)
    read_at = NOW + timedelta(minutes=2)
    with factory() as db:
        notifications.mark_read(db, row.id, now=read_at)

    counts = _loop(factory, server).run_once(NOW + timedelta(minutes=3))

    assert counts["cancelled"] == 1
    assert server.paths() == [f"/1/receipts/{RECEIPT}/cancel.json"]
    receipt = _receipt_row(factory)
    assert receipt.outcome == "cancelled" and receipt.source == "inbox"
    assert receipt.seen_at.replace(tzinfo=UTC) == read_at
    seen = _ledger(factory, v.EVENT_TYPE_ALERT_SEEN)
    assert [e.detail_json.get("source") for e in seen] == ["inbox"]


def test_the_loop_reports_under_its_health_name() -> None:
    assert HEALTH_NAME == "urgent_alert_receipts"


def test_an_unconfigured_loop_passes_and_asks_nobody(factory) -> None:
    loop = wiring.build_receipt_loop(Settings(_env_file=None), factory, None)
    assert loop.configured is False
    assert loop.run_once(NOW) == {"seen": 0, "unseen": 0, "cancelled": 0}


# ------------------------------------------------- acceptance 6: the routes


@pytest.fixture()
def api(factory, server):
    from app.identity.root import InMemoryCredentialRoot
    from app.identity.runtime import IdentityRuntime
    from app.urgent_alert.routes import router
    from tests.identity_support import bearer, make_identity_engine

    clock = {"now": NOW}
    app = FastAPI()
    runtime = IdentityRuntime(
        Settings(_env_file=None), engine=make_identity_engine(), root=InMemoryCredentialRoot()
    )
    runtime.service.bootstrap()
    app.state.identity = runtime
    app.state.urgent_alert_loop = wiring.build_receipt_loop(
        _settings(), factory, _rung(factory, server), clock=lambda: clock["now"]
    )
    app.include_router(router)
    token = runtime.service.issue_session(client_kind="cli", label="t").token
    client = TestClient(app)
    client.clock = clock  # type: ignore[attr-defined]
    client.owner = bearer(token)  # type: ignore[attr-defined]
    yield client
    client.close()


def test_the_routes_refuse_without_a_session(api) -> None:
    assert api.post("/v1/urgent-alert/test").status_code == 401
    assert api.get("/v1/urgent-alert/status").status_code == 401


def test_the_test_button_records_an_important_test_notification(api, factory) -> None:
    response = api.post("/v1/urgent-alert/test", headers=api.owner)
    assert response.status_code == 200
    with factory() as db:
        row = db.get(NotificationRow, uuid.UUID(response.json()["id"]))
    assert row.kind == policy.KIND_URGENT_ALERT_TEST == "urgent_alert.test"
    assert row.priority == PRIORITY_URGENT
    assert wiring.is_important(row)


def test_the_fourth_test_in_an_hour_is_429(api) -> None:
    for _ in range(3):
        assert api.post("/v1/urgent-alert/test", headers=api.owner).status_code == 200
    assert api.post("/v1/urgent-alert/test", headers=api.owner).status_code == 429
    api.clock["now"] = NOW + timedelta(minutes=61)
    assert api.post("/v1/urgent-alert/test", headers=api.owner).status_code == 200


def test_status_says_connected_and_the_last_seen_and_never_a_key(
    api, factory, server, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    row = _ring(factory, server)
    server.receipt_json = _receipt(acknowledged=1, expired=0, ack_at=NOW + timedelta(minutes=1))
    api.app.state.urgent_alert_loop.run_once(NOW + timedelta(minutes=2))

    body = api.get("/v1/urgent-alert/status", headers=api.owner).json()

    assert body["configured"] is True
    assert body["open_receipts"] == 0
    assert body["last_outcome"] == "seen"
    assert body["last_seen_at"] == "2026-10-07T12:01:00Z"
    assert row.id is not None
    everything = repr(body) + "\n".join(r.getMessage() for r in caplog.records)
    assert APP_TOKEN not in everything and USER_KEY not in everything
    assert any("receipts/" in r.getMessage() for r in caplog.records if r.name == "httpx")


def test_status_when_not_configured(factory) -> None:
    from app.urgent_alert import routes

    loop = wiring.build_receipt_loop(Settings(_env_file=None), factory, None)
    assert routes.status_of(loop) == {
        "configured": False,
        "open_receipts": 0,
        "last_seen_at": None,
        "last_outcome": None,
    }


def test_the_test_route_is_409_when_not_configured(factory) -> None:
    from app.identity.root import InMemoryCredentialRoot
    from app.identity.runtime import IdentityRuntime
    from app.urgent_alert.routes import router
    from tests.identity_support import bearer, make_identity_engine

    app = FastAPI()
    runtime = IdentityRuntime(
        Settings(_env_file=None), engine=make_identity_engine(), root=InMemoryCredentialRoot()
    )
    runtime.service.bootstrap()
    app.state.identity = runtime
    app.state.urgent_alert_loop = wiring.build_receipt_loop(Settings(_env_file=None), factory, None)
    app.include_router(router)
    token = runtime.service.issue_session(client_kind="cli", label="t").token
    with TestClient(app) as client:
        assert client.post("/v1/urgent-alert/test", headers=bearer(token)).status_code == 409


# --------------------------------------------------------- vocabulary, config


def test_the_ledger_knows_the_alert_events() -> None:
    assert v.SUBSYSTEM_URGENT_ALERT == "urgent_alert"
    assert v.SUBSYSTEM_URGENT_ALERT in v.SUBSYSTEMS
    for name, value in (
        ("EVENT_TYPE_ALERT_SENT", "alert.sent"),
        ("EVENT_TYPE_ALERT_SEEN", "alert.seen"),
        ("EVENT_TYPE_ALERT_UNSEEN", "alert.unseen"),
        ("EVENT_TYPE_ALERT_REFUSED", "alert.refused"),
    ):
        assert getattr(v, name) == value
        assert value in v.EVENT_TYPES
        assert name in v.__all__
    assert "SUBSYSTEM_URGENT_ALERT" in v.__all__


def test_the_keys_are_secrets_and_the_defaults_are_off() -> None:
    settings = Settings(_env_file=None)
    assert isinstance(settings.urgent_alert_pushover_app_token, SecretStr)
    assert isinstance(settings.urgent_alert_pushover_user_key, SecretStr)
    assert settings.urgent_alert_poll_interval_s == 60.0
    assert settings.urgent_alert_link_base == ""
    assert APP_TOKEN not in repr(_settings())


# ------------------------------------------ the real application object (main.py)


def test_the_alarm_rung_is_in_the_ladder_of_the_real_app() -> None:
    """RED until main.py carries the registration lines (ALAN_ISTEGI: main.py)."""
    from app.main import create_app

    app = create_app(_settings())
    loop = app.state.urgent_alert_loop
    assert isinstance(loop, UrgentAlertLoop)
    assert loop.configured is True
    assert isinstance(app.state.urgent_alert_alarm_rung, AlarmRung)

    def _walk(routes):  # included routers nest (test_identity_enforcement._walk)
        for route in routes:
            inner = getattr(getattr(route, "original_router", None), "routes", None)
            inner = inner if inner is not None else getattr(route, "routes", None)
            if inner is not None:
                yield from _walk(inner)
            else:
                yield getattr(route, "path", "")

    assert {"/v1/urgent-alert/test", "/v1/urgent-alert/status"} <= set(_walk(app.routes))


def test_the_real_ladder_sweep_hands_the_alarm_rung_on(monkeypatch) -> None:
    """The retention sweeper's 'notification_ladder' pass is what delivers a row: the rung
    built in main.py must reach default_rungs there, not only sit on app.state."""
    from app.main import create_app

    app = create_app(_settings())
    seen: list[dict] = []
    monkeypatch.setattr(ladder, "sweep", lambda db, *, rungs, **_: seen.append(rungs) or {})
    app.state.retention_sweeper._sweeps["notification_ladder"]()
    assert seen and seen[0].get("alarm") is app.state.urgent_alert_alarm_rung
