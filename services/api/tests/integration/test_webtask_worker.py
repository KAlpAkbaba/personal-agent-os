"""A browser task through the REAL round activity under a REAL Temporal worker (ADR-0207 PR-B).

The unit suites drive the loop and ``run_round_db`` directly. The layer they do not
cover is the activity as the worker runs it: its thread, its heartbeat handed back to
the event loop, its own database session on PostgreSQL, and the workflow's waits and
signals. That layer is where the operator mission failed in production (2026-09-19),
with every other suite green. Only the BROWSER and the PLANNER are faked here.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import select
from temporalio.client import Client, WorkflowHandle
from temporalio.worker import Worker

from app.actions.confirmation_gate import CONFIRM_SOURCE_VOICE, Confirmation
from app.artifacts.runtime import build_artifact_context
from app.config import Settings
from app.webtask import activities, service
from app.webtask.activities import WEB_TASK_ACTIVITIES
from app.webtask.loop import Ports
from app.webtask.models import WebTaskRow
from app.webtask.planner import ScriptedPlanner
from app.webtask.types import (
    ASK_CONFIRM,
    ASK_PAYMENT,
    EXPECT_URL_CONTAINS,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WAITING_OWNER,
    Expectation,
)
from app.webtask.workflow import BrowserTaskRequest, BrowserTaskWorkflow
from tests.unit.test_webtask_acceptance import (
    QUOTE_GOAL,
    QUOTE_SCRIPT,
    STORY,
    T3_SCRIPT,
    click,
    done,
    news_site,
    quote_site,
    shop_site,
)
from tests.webtask_support import Clock, FakeBrowser

pytestmark = pytest.mark.integration

SESSION = "voice-session-integration"


@pytest.fixture()
def factory(settings: Settings) -> Iterator[Any]:
    """The dev database, with no browser task in flight: one task at a time is the
    product rule, and a row an earlier run left behind would refuse this one."""
    session_factory, _store = build_artifact_context(settings)
    with session_factory() as db:
        ids = list(
            db.execute(
                select(WebTaskRow.id).where(
                    WebTaskRow.status.in_((STATUS_RUNNING, STATUS_WAITING_OWNER))
                )
            ).scalars()
        )
        for task_id in ids:
            service.cancel_db(db, task_id)
    yield session_factory
    activities.set_ports_factory(None)


class Seen:
    """What the ports factory was handed, and whether the heartbeat of the round was
    usable from the thread the round runs on."""

    def __init__(self) -> None:
        self.rounds = 0
        self.beats = 0
        self.devices: list[uuid.UUID | None] = []


def fake_ports(browser: FakeBrowser, script: list[Any]) -> Seen:
    seen = Seen()
    ports = Ports(browser=browser, planner=ScriptedPlanner(script), clock=Clock())

    def build(
        task_id: uuid.UUID, device_id: uuid.UUID | None, heartbeat: Callable[[], None]
    ) -> Ports:
        seen.rounds += 1
        seen.devices.append(device_id)
        heartbeat()  # from the worker thread of the round: this is what killed the missions
        seen.beats += 1
        return ports

    activities.set_ports_factory(build)
    return seen


@asynccontextmanager
async def started(
    settings: Settings, factory: Any, goal: str
) -> AsyncIterator[tuple[uuid.UUID, WorkflowHandle[Any, Any]]]:
    with factory() as db:
        task_id = service.start_task_db(db, goal=goal, session_id=SESSION).id
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    task_queue = f"pagentos-webtask-{uuid.uuid4().hex[:8]}"
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[BrowserTaskWorkflow],
        activities=list(WEB_TASK_ACTIVITIES),
    ):
        handle = await client.start_workflow(
            BrowserTaskWorkflow.run,
            BrowserTaskRequest(task_id=str(task_id)),
            id=f"{service.workflow_id_for(task_id)}-{uuid.uuid4().hex[:6]}",
            task_queue=task_queue,
        )
        try:
            yield task_id, handle
        finally:
            try:
                await handle.terminate(reason="the test is over")
            except Exception:  # noqa: BLE001 - already closed is the expected case
                pass


def row(factory: Any, task_id: uuid.UUID) -> WebTaskRow:
    with factory() as db:
        return service.get_task(db, task_id)


async def until_parked(factory: Any, task_id: uuid.UUID, handle: WorkflowHandle[Any, Any]) -> None:
    """Wait until the ROW is parked and the WORKFLOW has seen it. A hang guard, not an
    assertion: what is asserted is the state that follows."""
    for _ in range(600):
        if row(factory, task_id).status != STATUS_RUNNING:
            status = await handle.query(BrowserTaskWorkflow.status)
            if status.get("status") != STATUS_RUNNING:
                return
        await asyncio.sleep(0.1)
    pytest.fail("the task never left 'running'")


async def test_a_task_runs_to_the_end_under_a_real_worker(settings: Settings, factory: Any) -> None:
    browser = news_site()
    seen = fake_ports(
        browser,
        [
            click(
                "Yapay zeka: yeni model duyuruldu", Expectation(EXPECT_URL_CONTAINS, "yeni-model")
            ),
            done("haber.example.org: Şirket yeni dil modelini tanıttı."),
        ],
    )
    async with started(settings, factory, "Bugünkü yapay zeka haberlerinden birini özetle") as (
        task_id,
        handle,
    ):
        result = await asyncio.wait_for(handle.result(), timeout=120)

    assert result["status"] == STATUS_DONE and result["task_id"] == str(task_id), result
    finished = row(factory, task_id)
    assert finished.status == STATUS_DONE and finished.completed_at is not None
    assert [r["outcome"] for r in finished.state_json["rounds"]] == ["acted", "done"]
    assert browser.url == STORY and browser.done == ["open_story"]
    # One activity per round, and each one could beat from its thread.
    assert seen.rounds == seen.beats == 2
    assert seen.devices == [None, None]
    # No key was used twice, across activities.
    assert len(browser.keys) == len(set(browser.keys))


async def test_the_task_stops_at_the_payment_boundary_and_a_cancel_ends_it(
    settings: Settings, factory: Any
) -> None:
    browser = shop_site()
    seen = fake_ports(browser, list(T3_SCRIPT))
    async with started(settings, factory, "Şu kulaklığı sepete ekle, ödemede dur") as (
        task_id,
        handle,
    ):
        await until_parked(factory, task_id, handle)
        parked = row(factory, task_id)
        assert (parked.status, parked.waiting_for) == (STATUS_WAITING_OWNER, ASK_PAYMENT)
        rounds_when_parked = seen.rounds

        # A word that is not a cancel wakes the workflow and changes nothing: no round
        # is run for a task the row says is waiting.
        await handle.signal(BrowserTaskWorkflow.woken)
        await asyncio.sleep(1.5)
        assert row(factory, task_id).waiting_for == ASK_PAYMENT
        assert seen.rounds == rounds_when_parked

        with factory() as db:
            service.request_cancel_db(db, task_id)
        await handle.signal(BrowserTaskWorkflow.cancel)
        result = await asyncio.wait_for(handle.result(), timeout=60)

    assert result["status"] == STATUS_CANCELLED, result
    assert row(factory, task_id).status == STATUS_CANCELLED
    assert browser.flags.get("in_cart") and not browser.flags.get("paid")
    assert "to_payment" not in browser.done and "pay" not in browser.done
    assert seen.rounds == rounds_when_parked


async def test_the_word_is_applied_to_the_row_and_the_signal_only_wakes(
    settings: Settings, factory: Any
) -> None:
    browser = quote_site()
    seen = fake_ports(browser, [*QUOTE_SCRIPT, done("Teklif iletildi.")])
    async with started(settings, factory, QUOTE_GOAL) as (task_id, handle):
        await until_parked(factory, task_id, handle)
        parked = row(factory, task_id)
        assert (parked.status, parked.waiting_for) == (STATUS_WAITING_OWNER, ASK_CONFIRM)
        assert "send" not in browser.done

        # Woken with no word on the row: the workflow reads the row and waits again.
        await handle.signal(BrowserTaskWorkflow.woken)
        await asyncio.sleep(1.5)
        assert row(factory, task_id).waiting_for == ASK_CONFIRM and "send" not in browser.done

        with factory() as db:
            service.note_read_back_db(db, task_id, session_id=SESSION, turn=4)
        with factory() as db:
            service.confirm_db(
                db,
                task_id,
                Confirmation(
                    source=CONFIRM_SOURCE_VOICE, session_id=SESSION, turn=5, owner_intent_ok=True
                ),
            )
        await handle.signal(BrowserTaskWorkflow.woken)
        result = await asyncio.wait_for(handle.result(), timeout=120)

    assert result["status"] == STATUS_DONE, result
    assert browser.done.count("send") == 1
    final = row(factory, task_id)
    acted = [r for r in final.state_json["rounds"] if r["confirmed_by"]]
    assert [(r["element"], r["confirmed_by"], r["verified"]) for r in acted] == [
        ("Teklifi ilet", "voice", True)
    ]
    assert seen.rounds == seen.beats


async def test_a_round_that_cannot_run_leaves_a_failed_row(
    settings: Settings, factory: Any
) -> None:
    def broken(
        task_id: uuid.UUID, device_id: uuid.UUID | None, heartbeat: Callable[[], None]
    ) -> Ports:
        attempts.append(task_id)
        raise RuntimeError("the device path is not there")

    attempts: list[uuid.UUID] = []
    activities.set_ports_factory(broken)
    async with started(settings, factory, "Bir sayfa aç") as (task_id, handle):
        result = await asyncio.wait_for(handle.result(), timeout=60)

    assert result["status"] == STATUS_FAILED, result
    failed = row(factory, task_id)
    assert failed.status == STATUS_FAILED and failed.failure == "round_could_not_run"
    assert failed.completed_at is not None
    # A round is never run a second time by a retry: it may have acted before it died,
    # and only a new observation can say what the page is now.
    assert attempts == [task_id]
    # The next task is not refused by this one.
    with factory() as db:
        following = service.start_task_db(db, goal="Başka bir sayfa aç", session_id=SESSION)
        service.cancel_db(db, following.id)


async def test_the_default_ports_refuse_a_task_with_no_device(
    settings: Settings, factory: Any
) -> None:
    """Nothing in PR-B starts a task on a device; a row without one fails in words
    instead of reaching for whichever device is online."""
    activities.set_ports_factory(None)
    async with started(settings, factory, "Bir sayfa aç") as (task_id, handle):
        result = await asyncio.wait_for(handle.result(), timeout=60)
    assert result["status"] == STATUS_FAILED and result["failure"] == "no_device", result
    assert row(factory, task_id).failure == "no_device"
