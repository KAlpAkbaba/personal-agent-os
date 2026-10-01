"""The ledger writers that know which machine acted stamp it (ADR-0221).

The narrative collector reads ``detail_json["device"]`` and treats a row that names none as a
cloud row. So "ofiste ne yaptın" found nothing in real data until the writers named the
device. Per writer: the home device is 'ev', the office device 'ofis', no device leaves NO
stamp (a cloud event stays 'bulut'), and a stamp already on the detail is never overwritten.
The last test asks the collector itself, over rows these writers produced.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.receipt import ActionReceipt, record_receipt
from app.broker.models import Device
from app.ledger.models import ActivityEventRow
from app.narrative.collector import collect
from app.operator import mission_service
from app.operator.mission_models import OperatorMissionRow
from app.operator.models import ObjectFocusRow
from app.operator.plans import open_application
from app.operator.service import OperatorService, Plan
from app.research import browser_activities
from app.research.models import ResearchRunRow
from tests.alarms_support import FakeDeviceAction, happy_operator_device_results

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        ActivityEventRow.__table__,
        OperatorMissionRow.__table__,
        ObjectFocusRow.__table__,
        ResearchRunRow.__table__,
        Device.__table__,
    ):
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _rows(db, **where: Any) -> list[ActivityEventRow]:
    stmt = select(ActivityEventRow)
    for key, value in where.items():
        stmt = stmt.where(getattr(ActivityEventRow, key) == value)
    return list(db.execute(stmt).scalars())


def _device_of(row: ActivityEventRow) -> Any:
    return (row.detail_json or {}).get("device", "<none>")


# ------------------------------------------------------------------ action receipts


def _receipt(action_id: str = "a1") -> ActionReceipt:
    return ActionReceipt(
        action_id=action_id,
        capability="eye.disable",
        requested_state="disabled",
        execution_status="executed",
        terminal_status="verified",
        observed_after={"server": {}, "local": {}},
        completed_at=NOW,
    )


def test_receipt_row_carries_the_device_that_acted(db) -> None:
    home = record_receipt(db, _receipt("h"), "presence", device="Evde")
    office = record_receipt(db, _receipt("o"), "presence", device="Ofiste")
    assert _device_of(home) == "ev"
    assert _device_of(office) == "ofis"


def test_receipt_row_without_a_device_carries_no_stamp(db) -> None:
    row = record_receipt(db, _receipt(), "presence")
    assert "device" not in row.detail_json
    blank = record_receipt(db, _receipt("b"), "presence", device="  ")
    assert "device" not in blank.detail_json


def test_receipt_does_not_overwrite_a_stamp_already_in_the_detail(db, monkeypatch) -> None:
    original = ActionReceipt.as_dict

    def with_stamp(self, **kw):
        return {**original(self, **kw), "device": "ev"}

    monkeypatch.setattr(ActionReceipt, "as_dict", with_stamp)
    row = record_receipt(db, _receipt(), "presence", device="Ofiste")
    assert _device_of(row) == "ev"


# ------------------------------------------------------------------ operator tasks


def _run_task(db, **kw):
    plan = Plan(name="open_application", goal="open", steps=open_application("notepad"))
    device = FakeDeviceAction(results=happy_operator_device_results())
    return OperatorService().start_task(db, plan, device, **kw)


def _operator_rows(db) -> list[ActivityEventRow]:
    return _rows(db, subsystem="operator")


def test_operator_task_rows_and_receipt_carry_the_device(db) -> None:
    _run_task(db, device="Ofiste")
    rows = _operator_rows(db)
    assert len(rows) >= 3, "started + finished + receipt"
    assert {_device_of(r) for r in rows} == {"ofis"}


def test_operator_task_without_a_device_carries_no_stamp(db) -> None:
    _run_task(db)
    rows = _operator_rows(db)
    assert rows
    assert {_device_of(r) for r in rows} == {"<none>"}


def test_operator_task_home_device_is_ev(db) -> None:
    _run_task(db, device="ev")
    assert {_device_of(r) for r in _operator_rows(db)} == {"ev"}


# ------------------------------------------------------------------ operator missions


def _mission_rows(db) -> list[ActivityEventRow]:
    return _rows(db, subsystem="operator", event_type="operator.mission.started")


def test_mission_row_carries_the_device_the_sentence_named(db) -> None:
    mission_service.start_mission_db(
        db, text="Ofis bilgisayarımda hesap makinesini aç.", device_targets=("ofis",)
    )
    rows = _mission_rows(db)
    assert len(rows) == 1
    assert _device_of(rows[0]) == "ofis"


def test_mission_home_device_is_ev(db) -> None:
    mission_service.start_mission_db(
        db, text="Ev bilgisayarımda hesap makinesini aç.", device_targets=("ev",)
    )
    assert _device_of(_mission_rows(db)[0]) == "ev"


def test_mission_naming_no_device_carries_no_stamp(db) -> None:
    mission_service.start_mission_db(db, text="Hesap makinesini aç.")
    assert _device_of(_mission_rows(db)[0]) == "<none>"


def test_mission_does_not_overwrite_a_stamp_already_in_the_detail(db) -> None:
    mission = mission_service.plan_mission("Hesap makinesini aç.")
    mission.device_targets = ["ofis"]
    mission_service._ledger(db, mission, "operator.mission.started", "x", {"device": "ev"})
    assert _device_of(_rows(db, source="live")[0]) == "ev"


# ------------------------------------------------------------------ research runs


def _run_on(db, device_name: str | None, alias: str | None = None) -> uuid.UUID:
    tid = uuid.uuid4()
    device_id = None
    if device_name is not None:
        device_id = uuid.uuid4()
        db.add(
            Device(
                id=device_id,
                name=device_name,
                platform="windows",
                public_key_spki_b64="k",
                capabilities_json=[],
                metadata_json={"aliases": [alias]} if alias else {},
            )
        )
    db.add(ResearchRunRow(task_id=tid, device_id=device_id, stage="ranking"))
    db.commit()
    return tid


_FALLBACK = {"requested": "a", "used": "b", "attempts": 2, "reason": "r"}


def _fallback_row(db, tid) -> ActivityEventRow:
    browser_activities._record_provider_fallback(db, tid, dict(_FALLBACK))
    rows = _rows(db, research_job_id=tid)
    assert len(rows) == 1
    return rows[0]


def test_research_row_carries_the_device_the_run_used(db) -> None:
    assert _device_of(_fallback_row(db, _run_on(db, "GMKADIRAKBABA", "ofis"))) == "ofis"
    assert _device_of(_fallback_row(db, _run_on(db, "MAIL", "ev"))) == "ev"


def test_research_row_of_a_device_with_no_alias_carries_its_name(db) -> None:
    assert _device_of(_fallback_row(db, _run_on(db, "MAIL"))) == "mail"


def test_research_row_of_a_run_with_no_device_carries_no_stamp(db) -> None:
    row = _fallback_row(db, _run_on(db, None))
    assert _device_of(row) == "<none>"
    assert row.detail_json["requested"] == "a", "what is recorded is otherwise unchanged"


def test_research_stamp_does_not_overwrite_an_existing_stamp(db) -> None:
    tid = _run_on(db, "GMKADIRAKBABA", "ofis")
    event = browser_activities.ledger_service.ActivityEvent(
        event_type="research.provider_fallback",
        subsystem="research",
        action="x",
        factual_summary="x",
        source="live",
        source_ref="t",
        detail_json={"device": "ev"},
    )
    assert browser_activities._stamp_run_device(db, tid, event).detail_json["device"] == "ev"


def test_research_stamp_survives_a_missing_run(db) -> None:
    event = browser_activities.ledger_service.ActivityEvent(
        event_type="research.provider_fallback",
        subsystem="research",
        action="x",
        factual_summary="x",
        source="live",
        source_ref="t",
        detail_json={},
    )
    assert "device" not in browser_activities._stamp_run_device(db, uuid.uuid4(), event).detail_json


# ------------------------------------------------------------------ the collector


def _completed_research(db, tid) -> None:
    """The row ``persist_artifact_activity`` writes: the shared builder, then the run's stamp."""
    ledger = browser_activities.ledger_service
    event = ledger.build_research_completed_event(
        task_id=tid,
        occurred_at=datetime.now(UTC),
        report_json={"topic": "x", "findings": [1], "sources": [1]},
        source="live",
        source_ref=f"research_runs:{tid}:ready",
    )
    ledger.record(db, browser_activities._stamp_run_device(db, tid, event))


def test_collector_selects_the_office_rows_from_rows_the_writers_produced(db) -> None:
    # 'failed' and 'completed' rows are what the collector reads; a receipt's status is its
    # terminal status, so a failed one is a row it sees.
    def failed_receipt(action_id: str) -> ActionReceipt:
        return ActionReceipt(
            action_id=action_id,
            capability="eye.disable",
            requested_state="disabled",
            execution_status="failed",
            terminal_status="failed",
            observed_after={"server": {}, "local": {}},
            completed_at=datetime.now(UTC),
        )

    record_receipt(db, failed_receipt("o"), "presence", device="Ofiste")
    record_receipt(db, failed_receipt("h"), "presence", device="ev")
    record_receipt(db, failed_receipt("c"), "presence")  # a cloud-side event, unstamped
    _completed_research(db, _run_on(db, "GMKADIRAKBABA", "ofis"))
    mission_service.start_mission_db(
        db, text="Ofis bilgisayarimda hesap makinesini ac.", device_targets=("ofis",)
    )
    now = datetime.now(UTC) + timedelta(seconds=1)  # the collector reads rows strictly before it
    office = collect(db, "bugün", "ofiste", now=now)
    home = collect(db, "bugün", "evde", now=now)
    cloud = collect(db, "bugün", "bulut", now=now)
    assert dict(office.counts_by_device) == {"ofis": 3}  # receipt, research, mission
    assert home.total == 1
    assert cloud.total == 1
