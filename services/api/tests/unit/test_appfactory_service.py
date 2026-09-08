"""``AppFactoryService`` (docs/M23_APP_FACTORY_SPEC.md §1-§4, ADR-0086): create -> scaffold
-> run -> exercise -> test -> stop, against SQLite + the fake device (``tests.alarms_
support.FakeDeviceAction``) — the same harness discipline ``test_artifact_factory.py``
and ``test_documents_*`` already establish for their own device-calling services.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.appfactory.models import (
    STATE_FAILED,
    STATE_RUNNING,
    STATE_SCAFFOLDED,
    STATE_STOPPED,
    STATE_TESTED,
    AppProjectRow,
)
from app.appfactory.service import AppFactoryService
from app.ledger.models import ActivityEventRow
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_PROJECT, ObjectFocusRow
from app.research.browser_gateway import FakeBrowserGateway
from tests.alarms_support import FakeDeviceAction, failed
from tests.appfactory_support import (
    appfactory_capability_results,
    project_test_failing,
)

TABLES = [AppProjectRow.__table__, ObjectFocusRow.__table__, ActivityEventRow.__table__]


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def device() -> FakeDeviceAction:
    return FakeDeviceAction(results=dict(appfactory_capability_results()))


@pytest.fixture()
def service() -> AppFactoryService:
    return AppFactoryService()


TASK_TRACKER_SPEC = {"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"}


def test_create_scaffolds_a_project_and_sets_focus(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    result = service.create(db, device, spec=TASK_TRACKER_SPEC, session_id="s-1")
    assert result["execution_status"] == "executed", result
    assert result["state"] == STATE_SCAFFOLDED
    assert result["root_path"]
    assert device.capabilities_called() == ["project.scaffold"]

    project_id = result["project_id"]
    row = db.get(AppProjectRow, __import__("uuid").UUID(project_id))
    assert row is not None
    assert row.state == STATE_SCAFFOLDED
    assert row.root_path == result["root_path"]

    current = focus_module.current(db, FOCUS_KIND_PROJECT)
    assert current is not None
    assert current.object_id == project_id


def test_create_with_an_invalid_spec_never_reaches_the_device(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    result = service.create(db, device, spec={"name": "x", "kind": "cli", "template": "cli-tool"})
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "validation_error"
    assert device.calls == []
    assert db.query(AppProjectRow).count() == 0


def test_create_with_a_secret_bearing_page_body_is_refused_before_scaffold(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    spec = {
        "name": "Notlar",
        "kind": "web_static",
        "template": "static-page",
        "page_title": "Notlar",
        "page_heading": "Notlar",
        "page_body": "api_key: sk-abcdefghijklmnopqrstuvwx",
    }
    result = service.create(db, device, spec=spec)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "validation_error"
    assert device.calls == []


def test_create_when_the_device_refuses_scaffold_marks_the_project_failed(
    db: Session, service: AppFactoryService
) -> None:
    device = FakeDeviceAction(results={"project.scaffold": failed("permission_denied")})
    result = service.create(db, device, spec=TASK_TRACKER_SPEC)
    assert result["execution_status"] == "refused"
    project_id = result["project_id"]
    row = db.get(AppProjectRow, __import__("uuid").UUID(project_id))
    assert row.state == STATE_FAILED


def test_create_with_no_device_is_capability_missing(
    db: Session, service: AppFactoryService
) -> None:
    result = service.create(db, None, spec=TASK_TRACKER_SPEC)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "capability_missing"


def _scaffold(db: Session, device: FakeDeviceAction, service: AppFactoryService) -> str:
    created = service.create(db, device, spec=TASK_TRACKER_SPEC)
    assert created["execution_status"] == "executed", created
    return created["project_id"]


def test_run_starts_the_scaffolded_project(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    _scaffold(db, device, service)
    device.reset()
    result = service.run(db, device, target="current")
    assert result["execution_status"] == "executed", result
    assert result["state"] == STATE_RUNNING
    assert result["port"]
    assert device.capabilities_called() == ["project.run"]


def test_run_with_nothing_scaffolded_is_a_clarification(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    result = service.run(db, device, target="current")
    assert result["status"] == "needs_clarification"
    assert result["speech"]


def test_run_refuses_a_cli_tool_project(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    created = service.create(
        db,
        device,
        spec={
            "name": "Araç",
            "kind": "cli",
            "template": "cli-tool",
            "commands": [{"name": "run"}],
        },
    )
    assert created["execution_status"] == "executed", created
    device.reset()
    result = service.run(db, device, target="current")
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "invalid_argument"
    assert device.calls == []


def test_exercise_opens_the_running_project_through_the_fake_browser_gateway(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    _scaffold(db, device, service)
    service.run(db, device, target="current")
    result = service.exercise(db, FakeBrowserGateway(), target="current")
    assert result["execution_status"] == "executed", result
    assert result["oracle"]["template"] == "task-tracker"
    assert result["url"].startswith("http://127.0.0.1:")


def test_exercise_without_running_first_is_refused(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    _scaffold(db, device, service)
    result = service.exercise(db, FakeBrowserGateway(), target="current")
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "invalid_argument"


def test_test_marks_the_project_tested_on_a_passing_run(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    project_id = _scaffold(db, device, service)
    device.reset()
    result = service.test(db, device, target="current")
    assert result["execution_status"] == "executed", result
    assert result["state"] == STATE_TESTED
    assert result["passed"] == 5
    assert result["failed"] == 0
    row = db.get(AppProjectRow, __import__("uuid").UUID(project_id))
    assert row.state == STATE_TESTED
    assert row.test_report_json["passed"] == 5


def test_test_marks_the_project_failed_on_a_failing_run(
    db: Session, service: AppFactoryService
) -> None:
    device = FakeDeviceAction(
        results={**appfactory_capability_results(), "project.test": project_test_failing}
    )
    project_id = _scaffold(db, device, service)
    device.reset()
    result = service.test(db, device, target="current")
    assert result["execution_status"] == "executed", result
    assert result["state"] == STATE_FAILED
    assert result["failed"] == 2
    row = db.get(AppProjectRow, __import__("uuid").UUID(project_id))
    assert row.state == STATE_FAILED


def test_stop_stops_a_running_project(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    project_id = _scaffold(db, device, service)
    service.run(db, device, target="current")
    device.reset()
    result = service.stop(db, device, target="current")
    assert result["execution_status"] == "executed", result
    assert result["state"] == STATE_STOPPED
    assert device.capabilities_called() == ["project.stop"]
    row = db.get(AppProjectRow, __import__("uuid").UUID(project_id))
    assert row.run_port is None


def test_stop_still_works_after_test_moved_state_to_tested(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    """Regression: ``test()`` moves ``state`` to "tested"/"failed" as a lifecycle
    milestone, but the process ``run()`` started is still up — a real bug this test
    caught (2026-09-08): ``stop()`` used to check ``state == "running"`` and silently
    no-op'd instead of stopping a project that had since been tested. ``run_port``
    (never ``state``) is the truth of whether a process is actually up."""
    _scaffold(db, device, service)
    service.run(db, device, target="current")
    tested = service.test(db, device, target="current")
    assert tested["state"] == STATE_TESTED
    device.reset()
    result = service.stop(db, device, target="current")
    assert result["execution_status"] == "executed", result
    assert result["state"] == STATE_STOPPED
    assert device.capabilities_called() == ["project.stop"]


def test_status_reports_running_even_after_test_moved_state_to_tested(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    _scaffold(db, device, service)
    service.run(db, device, target="current")
    service.test(db, device, target="current")
    device.reset()
    result = service.status(db, device, target="current")
    assert result["state"] == "running"
    assert result["port"]


def test_stop_a_project_that_is_not_running_is_a_noop(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    _scaffold(db, device, service)
    device.reset()
    result = service.stop(db, device, target="current")
    assert result["execution_status"] == "noop"
    assert result["terminal_status"] == "already"
    assert device.calls == []


def test_status_reports_running_state_and_port(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    _scaffold(db, device, service)
    service.run(db, device, target="current")
    device.reset()
    result = service.status(db, device, target="current")
    assert result["state"] == "running"
    assert result["port"]
    assert device.capabilities_called() == ["project.status"]


def test_list_reports_every_project(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    _scaffold(db, device, service)
    result = service.list(db)
    assert result["projects"]
    assert result["projects"][0]["name"] == "Yapılacaklar"


def test_list_with_nothing_made_is_honest() -> None:
    engine = create_engine("sqlite://")
    for table in TABLES:
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    service = AppFactoryService()
    result = service.list(session)
    assert result["projects"] == []
    assert "Henüz" in result["speech"]


def test_open_reveals_the_folder_and_the_browser_for_a_running_web_project(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    _scaffold(db, device, service)
    service.run(db, device, target="current")
    device.reset()
    result = service.open(db, device, FakeBrowserGateway(), target="current")
    assert result["execution_status"] == "executed", result
    assert "file.reveal" in device.capabilities_called()
    assert result["browser_opened"] is True


def test_resolve_project_falls_back_to_the_most_recent_when_no_focus(
    db: Session, device: FakeDeviceAction, service: AppFactoryService
) -> None:
    project_id = _scaffold(db, device, service)
    # Clear the focus row created by ``create`` to prove the fallback path.
    db.query(ObjectFocusRow).delete()
    db.commit()
    row = service.resolve_project(db, "current")
    assert row is not None
    assert str(row.id) == project_id
