"""One routine browser action dispatched end to end on the REAL database (ADR-0213 row 1,
order 2b; owner rule ADR-0214 addendum 4): the execution ledger rows the rule wrote, read
back from PostgreSQL through a fresh session, and the result the routine gets.

Only presence (which device ids hold a connection) and the command client are fakes; the
``devices`` rows and the ledger rows are the dev stack's own tables, and the dispatcher, the
port, the rule and the ledger writer are the application's. The cloud device advertises what
the cloud worker's hello really carries.

Cleanup does not trust the code under test: every ledger write this process makes is noted
by its ``source_ref`` as it is made, and the devices are this test's own ids.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from app import main as main_mod
from app.broker.models import DEVICE_STATUS_ENROLLED, Device
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.devices.commands import CommandSucceeded
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.routines import dispatch as dispatch_mod
from app.routines.dispatch import ActionDispatcher, BriefingDelivery, BrokerDeviceAction
from tests.device_command_support import FakeDeviceCommandClient
from tests.unit.test_execution_call_site_research import CLOUD_CAPS

pytestmark = pytest.mark.integration

URL = "https://example.com/haber"
RULE_VARIABLE = "PAGENTOS_ROUTINES_EXECUTION_RULE_ENABLED"


class FakeBroker:
    def __init__(self) -> None:
        self.online: set[uuid.UUID] = set()

    def is_online(self, device_id: uuid.UUID) -> bool:
        return device_id in self.online


class _Briefing:
    def narrate(self, **_kw) -> BriefingDelivery:  # pragma: no cover - never a briefing here
        return BriefingDelivery(False, "unused")


class Stack:
    def __init__(self, factory) -> None:
        self.factory = factory
        self.broker = FakeBroker()
        self.devices: list[uuid.UUID] = []
        self.tag = uuid.uuid4().hex[:8]
        #: ``source_ref`` of every ledger row this process wrote, noted at write time.
        self.ledger_refs: list[str] = []
        self.commands = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"ok": True}))
        self.dispatcher = ActionDispatcher(
            briefing=_Briefing(),
            device_action=BrokerDeviceAction(session_factory=factory, command_client=self.commands),
        )

    def enroll(self, name: str, *, platform: str, online: bool, seen_s_ago: float) -> uuid.UUID:
        with self.factory() as db:
            device = Device(
                name=f"{name}-{self.tag}",
                platform=platform,
                public_key_spki_b64="integration",
                capabilities_json=list(CLOUD_CAPS) if platform == "cloud" else ["browser.chrome"],
                status=DEVICE_STATUS_ENROLLED,
                last_seen_at=datetime.now(UTC) - timedelta(seconds=seen_s_ago),
                metadata_json={},
            )
            db.add(device)
            db.commit()
            device_id = device.id
        self.devices.append(device_id)
        if online:
            self.broker.online.add(device_id)
        return device_id

    def navigate(self):
        return self.dispatcher.dispatch(
            routine_id=uuid.uuid4(),
            firing_id=uuid.uuid4(),
            action={"kind": "browser_action", "detail": {"action": "navigate", "url": URL}},
        )

    def ledger(self) -> list[ActivityEventRow]:
        with self.factory() as db:  # a FRESH session: what PostgreSQL holds
            return list(
                db.execute(
                    select(ActivityEventRow)
                    .where(ActivityEventRow.source_ref.in_(self.ledger_refs))
                    .order_by(ActivityEventRow.source_ref)
                ).scalars()
            )

    def leftovers(self) -> int:
        with self.factory() as db:
            rows = db.execute(
                select(func.count())
                .select_from(ActivityEventRow)
                .where(ActivityEventRow.source_ref.in_(self.ledger_refs))
            ).scalar_one()
            devices = db.execute(
                select(func.count()).select_from(Device).where(Device.id.in_(self.devices))
            ).scalar_one()
        return rows + devices

    def clear(self) -> None:
        with self.factory() as db:
            db.execute(
                delete(ActivityEventRow).where(ActivityEventRow.source_ref.in_(self.ledger_refs))
            )
            db.execute(delete(Device).where(Device.id.in_(self.devices)))
            db.commit()


def rule(monkeypatch, value: str | None) -> None:
    """What the dispatcher reads for ``routines_execution_rule_enabled`` (``None``: unset,
    the setting's own default), on the dev stack's settings otherwise. ``get_settings`` is
    cached per process, so the dispatcher's reference is replaced."""
    if value is None:
        monkeypatch.delenv(RULE_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(RULE_VARIABLE, value)
    settings = Settings()
    monkeypatch.setattr(dispatch_mod, "get_settings", lambda: settings)


@pytest.fixture()
def stack(monkeypatch) -> Iterator[Stack]:
    engine = build_engine(Settings().database_url)
    assert engine.dialect.name == "postgresql"
    s = Stack(build_session_factory(engine))
    record = ledger_service.record

    def noting(session, event):
        s.ledger_refs.append(event.source_ref)
        return record(session, event)

    monkeypatch.setattr(ledger_service, "record", noting)
    monkeypatch.setattr(dispatch_mod, "get_broker_runtime", lambda: s.broker)
    # The call site is behind a setting that is off by default; these tests are the rule ON
    # unless they say otherwise.
    rule(monkeypatch, "true")
    try:
        yield s
    finally:
        s.clear()
        assert s.leftovers() == 0
        engine.dispose()


def test_a_routines_browser_action_runs_on_the_cloud_device_and_postgres_holds_the_row(
    stack: Stack,
) -> None:
    # The machine was seen most recently: health order (the old choice) picks IT.
    stack.enroll("MAIL", platform="windows", online=True, seen_s_ago=1.0)
    cloud = stack.enroll("bulut", platform="cloud", online=True, seen_s_ago=300.0)

    outcome = stack.navigate()

    assert outcome.ok is True and outcome.status == "succeeded"
    assert [(c.device_id, c.capability) for c in stack.commands.calls] == [
        (cloud, "browser.navigate")
    ]
    ledger = stack.ledger()
    assert [(r.event_type, r.detail_json["target"]) for r in ledger] == [
        ("execution.selected", "cloud")
    ]
    assert ledger[0].subsystem == "routine" and ledger[0].source == "execution"
    assert ledger[0].detail_json["job_kind"] == "scheduled"
    assert ledger[0].detail_json["chain"] == ["cloud"]


def test_with_the_cloud_down_nothing_is_sent_and_postgres_holds_the_refusal(
    stack: Stack,
) -> None:
    stack.enroll("bulut", platform="cloud", online=False, seen_s_ago=300.0)
    stack.enroll("MAIL", platform="windows", online=True, seen_s_ago=1.0)

    outcome = stack.navigate()

    assert stack.commands.calls == []
    assert outcome.ok is False and outcome.detail == {"error_class": "no_capable_device"}
    assert "no_target_available" in outcome.reason
    ledger = stack.ledger()
    assert [r.event_type for r in ledger] == ["execution.fallback", "execution.refused"]
    assert (ledger[0].detail_json["skipped_target"], ledger[0].detail_json["reason"]) == (
        "cloud",
        "cloud_offline",
    )
    assert ledger[1].detail_json["reason"] == "no_target_available"
    assert ledger[1].status == "failed"


# ------------------------------------- the real application object, the setting off and on


@pytest.fixture()
def real_app(stack: Stack, monkeypatch) -> Iterator[ActionDispatcher]:
    """The routine dispatcher ``create_app`` builds, over the port it puts on ``app.state``
    and that port's OWN session factory (the dev stack's PostgreSQL): only the command
    client is this test's."""
    built: list[ActionDispatcher] = []

    class Capturing(ActionDispatcher):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            built.append(self)

    monkeypatch.setattr(main_mod, "ActionDispatcher", Capturing)
    app = main_mod.create_app(Settings())
    port = app.state.device_action
    assert type(port) is BrokerDeviceAction
    mine = [d for d in built if d._device_action is port]
    assert mine, "create_app built no routine dispatcher over app.state.device_action"
    port._command_client = stack.commands
    try:
        yield mine[0]
    finally:
        port._session_factory.kw["bind"].dispose()


def _navigate(dispatcher: ActionDispatcher):
    return dispatcher.dispatch(
        routine_id=uuid.uuid4(),
        firing_id=uuid.uuid4(),
        action={"kind": "browser_action", "detail": {"action": "navigate", "url": URL}},
    )


def test_off_the_real_app_sends_it_where_main_does_and_postgres_holds_no_row(
    stack: Stack, real_app: ActionDispatcher, monkeypatch
) -> None:
    rule(monkeypatch, None)
    mail = stack.enroll("MAIL", platform="windows", online=True, seen_s_ago=1.0)
    stack.enroll("bulut", platform="cloud", online=True, seen_s_ago=300.0)

    outcome = _navigate(real_app)

    assert outcome.ok is True
    assert [(c.device_id, c.capability) for c in stack.commands.calls] == [
        (mail, "browser.navigate")
    ]
    assert stack.ledger_refs == [] and stack.ledger() == []


def test_on_the_real_app_sends_it_to_the_cloud_and_postgres_holds_the_row(
    stack: Stack, real_app: ActionDispatcher
) -> None:
    stack.enroll("MAIL", platform="windows", online=True, seen_s_ago=1.0)
    cloud = stack.enroll("bulut", platform="cloud", online=True, seen_s_ago=300.0)

    outcome = _navigate(real_app)

    assert outcome.ok is True
    assert [(c.device_id, c.capability) for c in stack.commands.calls] == [
        (cloud, "browser.navigate")
    ]
    assert [(r.event_type, r.detail_json["target"]) for r in stack.ledger()] == [
        ("execution.selected", "cloud")
    ]
