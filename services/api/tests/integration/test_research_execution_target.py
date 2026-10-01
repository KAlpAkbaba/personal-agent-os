"""A research start on the REAL database: the run row and the execution ledger rows the
execution_target rule wrote (ADR-0213, order 2b), read back from PostgreSQL through a fresh
session - not from the session that wrote them, and not from SQLite.

Only presence is faked (which device ids hold a connection); the ``devices`` rows, the task,
the run row and the ledger rows are the dev stack's own tables. The cloud device advertises
what the cloud worker's hello really carries (read from ``browser_agent/policy.py``).

Cleanup does not trust the code under test to have stamped its rows: every ledger write this
process makes is noted by its ``source_ref`` as it is made, and the tasks are found by this
test's own intent text - so a start that forgets ``research_job_id``, or raises half way,
leaves nothing behind in the shared dev database.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, or_, select

from app.artifacts.models import Task, TaskRun
from app.broker.models import DEVICE_STATUS_ENROLLED, Device
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.execution import wiring
from app.execution.rule import JobKind
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.notifications.models import NotificationRow
from app.research import service as research_service
from app.research.models import STAGE_FAILED, STAGE_PLANNED, ResearchRunRow
from tests.unit.test_execution_call_site_research import CLOUD_CAPS

pytestmark = pytest.mark.integration


class FakeBroker:
    def __init__(self) -> None:
        self.online: set[uuid.UUID] = set()

    def is_online(self, device_id: uuid.UUID) -> bool:
        return device_id in self.online


class Stack:
    def __init__(self, factory) -> None:
        self.factory = factory
        self.broker = FakeBroker()
        self.devices: list[uuid.UUID] = []
        self.tag = uuid.uuid4().hex[:8]
        #: This test's tasks are the ones with this intent - found even when a start raised.
        self.intent = f"yapay zeka ajanları {self.tag}"
        #: ``source_ref`` of every ledger row this process wrote, noted at write time.
        self.ledger_refs: list[str] = []

    def enroll(
        self,
        name: str,
        *,
        platform: str,
        online: bool,
        seen_s_ago: float,
        capabilities: list[str] | None = None,
    ) -> uuid.UUID:
        if capabilities is None:
            capabilities = list(CLOUD_CAPS) if platform == "cloud" else ["browser.chrome"]
        with self.factory() as db:
            device = Device(
                name=f"{name}-{self.tag}",
                platform=platform,
                public_key_spki_b64="integration",
                capabilities_json=capabilities,
                status=DEVICE_STATUS_ENROLLED,
                last_seen_at=datetime.now(UTC) - timedelta(seconds=seen_s_ago),
                metadata_json={"aliases": ["bulut"] if platform == "cloud" else []},
            )
            db.add(device)
            db.commit()
            device_id = device.id
        self.devices.append(device_id)
        if online:
            self.broker.online.add(device_id)
        return device_id

    def start(self, **kw) -> research_service.StartedResearch:
        with self.factory() as db:
            return research_service.start_browser_research(db, self.broker, input=self.intent, **kw)

    def read_back(self, task_id: uuid.UUID) -> tuple[ResearchRunRow, list[ActivityEventRow]]:
        with self.factory() as db:  # a FRESH session: what PostgreSQL holds
            run = db.execute(
                select(ResearchRunRow).where(ResearchRunRow.task_id == task_id)
            ).scalar_one()
            ledger = list(
                db.execute(
                    select(ActivityEventRow)
                    .where(ActivityEventRow.research_job_id == task_id)
                    .order_by(ActivityEventRow.source_ref)
                ).scalars()
            )
        return run, ledger

    def _mine(self, tasks: list[uuid.UUID]):
        return or_(
            ActivityEventRow.source_ref.in_(self.ledger_refs),
            ActivityEventRow.research_job_id.in_(tasks),
        )

    def leftovers(self) -> int:
        """Rows of this test still in PostgreSQL (0 after ``clear``)."""
        with self.factory() as db:
            tasks = list(db.execute(select(Task.id).where(Task.intent == self.intent)).scalars())
            ledger = db.execute(
                select(func.count()).select_from(ActivityEventRow).where(self._mine(tasks))
            ).scalar_one()
            devices = db.execute(
                select(func.count()).select_from(Device).where(Device.id.in_(self.devices))
            ).scalar_one()
        return len(tasks) + ledger + devices

    def clear(self) -> None:
        with self.factory() as db:
            tasks = list(db.execute(select(Task.id).where(Task.intent == self.intent)).scalars())
            db.execute(delete(ActivityEventRow).where(self._mine(tasks)))
            # A failed start notifies the owner ("task.failed"): this test's own rows only.
            db.execute(
                delete(NotificationRow).where(
                    NotificationRow.group_key.in_([f"task:{t}" for t in tasks])
                )
            )
            db.execute(delete(ResearchRunRow).where(ResearchRunRow.task_id.in_(tasks)))
            db.execute(delete(TaskRun).where(TaskRun.task_id.in_(tasks)))
            db.execute(delete(Task).where(Task.id.in_(tasks)))
            db.execute(delete(Device).where(Device.id.in_(self.devices)))
            db.commit()


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
    try:
        yield s
    finally:
        s.clear()
        engine.dispose()


def test_a_research_start_is_planned_on_the_cloud_device_in_postgres(stack: Stack) -> None:
    # The machine is enrolled first and was seen most recently: health order (the old
    # rule) and registry order both pick IT. The cloud advertises the worker's real hello.
    stack.enroll("MAIL", platform="windows", online=True, seen_s_ago=1.0)
    cloud = stack.enroll("bulut", platform="cloud", online=True, seen_s_ago=300.0)

    started = stack.start()

    assert started.error is None
    run, ledger = stack.read_back(started.task_id)
    assert run.device_id == cloud
    assert run.stage == STAGE_PLANNED
    planned = [e for e in run.events_json if e.get("stage") == STAGE_PLANNED][-1]
    assert planned["execution_target"] == "cloud"
    assert planned["execution_chain"] == ["cloud", "owner_chrome", "device"]
    assert planned["execution_skipped"] == []
    assert [(r.event_type, r.detail_json["target"]) for r in ledger] == [
        ("execution.selected", "cloud")
    ]
    assert ledger[0].source_ref == f"execution:{started.task_id}:0"


def test_the_fallback_rows_and_the_machine_are_in_postgres_when_the_cloud_is_down(
    stack: Stack,
) -> None:
    stack.enroll("bulut", platform="cloud", online=False, seen_s_ago=300.0)
    mail = stack.enroll("MAIL", platform="windows", online=True, seen_s_ago=1.0)

    started = stack.start()

    run, ledger = stack.read_back(started.task_id)
    assert run.device_id == mail
    assert [r.event_type for r in ledger] == [
        "execution.fallback",
        "execution.fallback",
        "execution.selected",
    ]
    assert (ledger[0].detail_json["skipped_target"], ledger[0].detail_json["reason"]) == (
        "cloud",
        "cloud_offline",
    )
    assert ledger[-1].detail_json["target"] == "device"


def test_a_cloud_that_cannot_serve_research_falls_to_the_machine_in_postgres(
    stack: Stack,
) -> None:
    """The inspector's blocker, on the real tables: an online cloud device that does not
    advertise what research sends is skipped with its own reason - the run is PLANNED on
    the machine and no ``execution.selected target=cloud`` row exists."""
    stack.enroll(
        "bulut",
        platform="cloud",
        online=True,
        seen_s_ago=300.0,
        capabilities=[c for c in CLOUD_CAPS if c != "browser.fetch_evidence"],
    )
    mail = stack.enroll("MAIL", platform="windows", online=True, seen_s_ago=1.0)

    started = stack.start()

    assert started.error is None
    run, ledger = stack.read_back(started.task_id)
    assert run.stage == STAGE_PLANNED and run.device_id == mail
    assert (ledger[0].detail_json["skipped_target"], ledger[0].detail_json["reason"]) == (
        "cloud",
        "cloud_capability_missing",
    )
    selected = [r.detail_json["target"] for r in ledger if r.event_type == "execution.selected"]
    assert selected == ["device"]


def test_bulutta_with_the_cloud_down_is_a_failed_run_with_no_device_in_postgres(
    stack: Stack,
) -> None:
    stack.enroll("bulut", platform="cloud", online=False, seen_s_ago=300.0)
    stack.enroll("MAIL", platform="windows", online=True, seen_s_ago=1.0)

    started = stack.start(named_devices=("bulutta",))

    assert started.device is None and started.error
    run, ledger = stack.read_back(started.task_id)
    assert run.stage == STAGE_FAILED and run.device_id is None
    assert run.events_json[-1]["execution_reason"] == "forced_target_unavailable"
    assert [r.event_type for r in ledger] == ["execution.fallback", "execution.refused"]
    assert ledger[-1].detail_json["reason"] == "forced_target_unavailable"


def test_the_cleanup_removes_ledger_rows_that_carry_no_research_job_id(stack: Stack) -> None:
    """The cleanup itself, proven: a decision written WITHOUT a research task (what a start
    that forgot to pass it writes) and a start's own rows are all gone after ``clear``."""
    stack.enroll("bulut", platform="cloud", online=True, seen_s_ago=300.0)
    stack.start()
    with stack.factory() as db:
        wiring.choose(
            JobKind.RESEARCH,
            spoken_target=None,
            url=None,
            needs_signed_in_session=False,
            acting=False,
            scheduled=False,
            db=db,
            runtime=stack.broker,
        )
        db.commit()
    with stack.factory() as db:
        orphans = db.execute(
            select(ActivityEventRow).where(
                ActivityEventRow.source_ref.in_(stack.ledger_refs),
                ActivityEventRow.research_job_id.is_(None),
            )
        ).scalars()
        assert len(list(orphans)) == 1
    assert stack.leftovers() > 0

    stack.clear()

    assert stack.leftovers() == 0
