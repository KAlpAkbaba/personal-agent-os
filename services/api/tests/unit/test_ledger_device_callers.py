"""The operator tool handlers stamp the BOUND device on the ledger rows they cause (ADR-0228).

The writers accept a device (``start_task(device=)``, ``record_receipt(device=)``) but no caller
passed it, so real operator rows stayed unstamped and "ofiste ne yaptın" counted only missions
and research. These run the real handlers with a fake device port bound to the office PC and
read the rows back - and the narrative collector's 'ofis' filter over them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.broker.models import Device
from app.ledger.models import ActivityEventRow
from app.narrative.collector import collect
from app.operator.mission_models import OperatorMissionRow
from app.operator.models import ObjectFocusRow
from app.operator.service import OperatorService
from app.voice.realtime_sessions.tools import ToolContext
from app.voice.realtime_sessions.tools_operator import (
    operator_app_open,
    operator_window_control,
)
from tests.alarms_support import FakeDeviceAction, happy_operator_device_results, window_id

OFFICE_ID = uuid.uuid4()


class _Port(FakeDeviceAction):
    """The scripted happy operator device, bound the way the real view
    (``_SessionBoundDeviceAction``) is: ``targets`` / ``session_device_ids``."""

    def __init__(self, *, targets: tuple[str, ...] = (), ids: tuple[Any, ...] = ()) -> None:
        super().__init__(results=happy_operator_device_results())
        self.targets = targets
        self.session_device_ids = ids

    def can_run(self, capability: str) -> bool:
        return True

    def selection_for(self, capability: str) -> Any:
        return None


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        ActivityEventRow.__table__,
        OperatorMissionRow.__table__,
        ObjectFocusRow.__table__,
        Device.__table__,
    ):
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(
        Device(
            id=OFFICE_ID,
            name="GMKADIRAKBABA",
            platform="windows",
            public_key_spki_b64="k",
            capabilities_json=[],
            metadata_json={"aliases": ["ofis", "iş"]},
        )
    )
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _ctx(db, port: Any, turn: dict[str, Any] | None = None) -> ToolContext:
    now = datetime.now(UTC)
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=OFFICE_ID,
        client_kind="web",
        context={"last_utterance": {"at": now.isoformat(), **(turn or {})}},
        db=db,
        now=now,
        call_id="call-1",
        live={"device_action": port, "operator": OperatorService()},
    )


def _operator_rows(db) -> list[ActivityEventRow]:
    return list(
        db.execute(
            select(ActivityEventRow).where(ActivityEventRow.subsystem == "operator")
        ).scalars()
    )


def _devices(db) -> set[Any]:
    return {(r.detail_json or {}).get("device", "<none>") for r in _operator_rows(db)}


def _bound_port() -> _Port:
    return _Port(ids=(OFFICE_ID,))


def test_app_open_rows_carry_the_bound_device(db) -> None:
    out = operator_app_open(_ctx(db, _bound_port()), {"application": "Hesap Makinesi"})
    assert out["terminal_status"] != "failed", out
    assert _operator_rows(db)
    assert _devices(db) == {"ofis"}


def test_named_target_is_the_device_word_when_no_session_device(db) -> None:
    operator_app_open(_ctx(db, _Port(targets=("ofis",))), {"application": "Hesap Makinesi"})
    assert _devices(db) == {"ofis"}


def test_window_action_rows_carry_the_bound_device(db) -> None:
    ctx = _ctx(db, _bound_port(), {"intent": "window_maximize", "window_ref": window_id(1)})
    out = operator_window_control(ctx, {"action": "maximize", "window": window_id(1)})
    assert out.get("execution_status") is not None, out
    rows = _operator_rows(db)
    assert rows
    assert _devices(db) == {"ofis"}


def test_action_receipt_row_carries_the_bound_device(db) -> None:
    from app.voice.realtime_sessions import tools_operator as t

    out = t._capability_missing(
        _ctx(db, _bound_port()), capability="operator.app_open", requested_state="opened"
    )
    assert out["terminal_status"] == "failed"
    receipts = [r for r in _operator_rows(db) if r.event_type == "action.receipt"]
    assert len(receipts) == 1
    assert receipts[0].detail_json["device"] == "ofis"


def test_no_bound_device_leaves_no_stamp_and_the_tool_succeeds(db) -> None:
    out = operator_app_open(_ctx(db, _Port()), {"application": "Hesap Makinesi"})
    assert out["terminal_status"] != "failed", out
    assert _operator_rows(db)
    assert _devices(db) == {"<none>"}


def test_unreadable_device_leaves_no_stamp(db) -> None:
    out = operator_app_open(_ctx(db, _Port(ids=(uuid.uuid4(),))), {"application": "Hesap Makinesi"})
    assert out["terminal_status"] != "failed", out
    assert _devices(db) == {"<none>"}


def test_collector_office_filter_counts_these_rows(db) -> None:
    operator_app_open(_ctx(db, _bound_port()), {"application": "Hesap Makinesi"})
    operator_app_open(_ctx(db, _Port()), {"application": "Hesap Makinesi"})
    now = datetime.now(UTC) + timedelta(seconds=1)
    office = collect(db, "bugün", "ofiste", now=now)
    assert office.total >= 1
    assert set(dict(office.counts_by_device)) == {"ofis"}
