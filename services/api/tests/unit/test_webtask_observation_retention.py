"""ADR-0207 PR-C: the page a task saw is not kept for ever.

A task's row holds its LAST observation - up to 6 000 characters of the page's text - so
that the next round can act on it without observing twice. Once the task has ended there
is no next round: the stored document says ``observation: null``. Three ways a task ends
and one way it is abandoned are each driven through the real service functions; rows
written before this rule are cleared by ``scrub_observations``. Every read-back goes
through a FRESH session (an in-place change to a JSON column is never written).
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.webtask import service
from app.webtask.models import WebTaskRow
from app.webtask.types import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WAITING_OWNER,
)
from tests.unit.test_webtask_acceptance import T3_SCRIPT, shop_site
from tests.unit.test_webtask_service import (
    SESSION,
    ports,
    row,
    run,
    start,
    voice,
    waiting_for_confirmation,
)


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    WebTaskRow.__table__.create(engine)
    ActivityEventRow.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def stored(factory: sessionmaker[Session], task_id: uuid.UUID) -> dict[str, Any]:
    return dict(row(factory, task_id).state_json)


def assert_cleared(factory: sessionmaker[Session], task_id: uuid.UUID) -> None:
    state = stored(factory, task_id)
    assert "observation" in state and state["observation"] is None
    assert state["observation_fresh"] is False


def assert_shown_without_it(factory: sessionmaker[Session], task_id: uuid.UUID) -> None:
    """``task_dict`` never showed the observation: with or without it, it is the same."""
    with factory() as db:
        found = service.get_task(db, task_id)
        shown = service.task_dict(found)
        kept = copy.deepcopy(dict(found.state_json))
        kept["observation"] = {"observation_id": "obs-x", "url": "https://a.example/", "text": "x"}
        found.state_json = kept
        assert service.task_dict(found) == shown
        db.rollback()
    assert "observation" not in shown


# ------------------------------------------------------------------ the three endings


def test_a_task_that_is_done_keeps_no_observation(factory) -> None:
    task_id, browser, p = waiting_for_confirmation(factory)
    with factory() as db:
        service.note_read_back_db(db, task_id, session_id=SESSION, turn=4)
    with factory() as db:
        service.confirm_db(db, task_id, voice(5))
    # The confirm path still works: park -> the owner's word -> act.
    assert run(factory, task_id, p)["status"] == STATUS_DONE
    assert browser.done.count("send") == 1
    assert_cleared(factory, task_id)
    assert_shown_without_it(factory, task_id)


def test_a_task_that_failed_keeps_no_observation(factory) -> None:
    browser = shop_site()
    p = ports(browser, list(T3_SCRIPT))
    task_id = start(factory, "Şu kulaklığı sepete ekle, ödemede dur")
    with factory() as db:
        service.run_round_db(db, task_id, p)
    assert stored(factory, task_id)["observation"] is not None
    with factory() as db:
        service.fail_db(db, task_id, reason="worker_lost", detail="activity timed out")
    assert row(factory, task_id).status == STATUS_FAILED
    assert_cleared(factory, task_id)
    assert_shown_without_it(factory, task_id)


def test_a_task_that_was_cancelled_keeps_no_observation(factory) -> None:
    browser = shop_site()
    p = ports(browser, list(T3_SCRIPT))
    task_id = start(factory, "Şu kulaklığı sepete ekle, ödemede dur")
    with factory() as db:
        service.run_round_db(db, task_id, p)
    with factory() as db:
        service.cancel_db(db, task_id)
    assert row(factory, task_id).status == STATUS_CANCELLED
    assert_cleared(factory, task_id)
    assert_shown_without_it(factory, task_id)


def test_a_parked_task_abandoned_after_the_wait_keeps_no_observation(factory) -> None:
    task_id, _browser, _p = waiting_for_confirmation(factory)
    parked = stored(factory, task_id)["observation"]
    assert parked is not None and parked["elements"]  # what was asked about is still there
    written = row(factory, task_id).updated_at
    assert written is not None
    later = (written if written.tzinfo else written.replace(tzinfo=UTC)) + (
        service.PARKED_AFTER + timedelta(minutes=1)
    )
    with factory() as db:
        assert service.active_task(db, now=later) is None
    found = row(factory, task_id)
    assert found.status == STATUS_FAILED and found.failure == "abandoned"
    assert_cleared(factory, task_id)


# ------------------------------------------------------------------ a running task keeps it


def test_a_running_task_keeps_its_observation_and_does_not_observe_twice(factory) -> None:
    browser = shop_site()
    p = ports(browser, list(T3_SCRIPT))
    task_id = start(factory, "Şu kulaklığı sepete ekle, ödemede dur")
    with factory() as db:
        service.run_round_db(db, task_id, p)
    after_first = browser.observations
    kept = stored(factory, task_id)
    assert kept["status"] == STATUS_RUNNING
    assert kept["observation"] is not None and kept["observation_fresh"] is True

    with factory() as db:
        service.run_round_db(db, task_id, p)
    # The second round acted on the stored observation and observed once - after acting.
    assert browser.observations - after_first == 1


def test_a_parked_task_keeps_what_was_asked_about_and_not_the_page_text(factory) -> None:
    task_id, _browser, _p = waiting_for_confirmation(factory)
    parked = stored(factory, task_id)
    assert parked["status"] == STATUS_WAITING_OWNER
    assert parked["observation_fresh"] is False  # the loop observes anew after the word
    assert parked["observation"]["url"] and parked["observation"]["elements"]
    assert parked["observation"]["text"] == ""


# ------------------------------------------------------------------ rows from before


def _seed(factory: sessionmaker[Session], status: str, *, minutes_ago: int = 1) -> uuid.UUID:
    task_id = uuid.uuid4()
    moment = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    with factory() as db:
        db.add(
            WebTaskRow(
                id=task_id,
                goal="Eski görev",
                status=status,
                attended=True,
                state_json={
                    "task_id": str(task_id),
                    "goal": "Eski görev",
                    "status": status,
                    "observation": {
                        "observation_id": "obs-old",
                        "url": "https://posta.example.com/",
                        "title": "Gelen kutusu",
                        "page_kind": "ok",
                        "elements": [],
                        "text": "Sahibin postası: banka şifreniz değişti.",
                    },
                    "observation_fresh": True,
                },
                created_at=moment,
                updated_at=moment,
                completed_at=moment if status != STATUS_RUNNING else None,
            )
        )
        db.commit()
    return task_id


def test_scrub_clears_terminal_rows_from_before_and_leaves_a_running_one(factory) -> None:
    old_done = _seed(factory, STATUS_DONE)
    old_failed = _seed(factory, STATUS_FAILED)
    running = _seed(factory, STATUS_RUNNING)
    running_before = stored(factory, running)

    with factory() as db:
        assert service.scrub_observations(db, datetime.now(UTC)) == 2
    for task_id in (old_done, old_failed):
        assert_cleared(factory, task_id)
        assert stored(factory, task_id)["goal"] == "Eski görev"
    assert stored(factory, running) == running_before

    snapshot = {t: stored(factory, t) for t in (old_done, old_failed, running)}
    with factory() as db:
        assert service.scrub_observations(db, datetime.now(UTC)) == 0
    assert {t: stored(factory, t) for t in snapshot} == snapshot


def test_starting_a_task_scrubs_the_rows_from_before(factory) -> None:
    old = _seed(factory, STATUS_CANCELLED)
    start(factory, "Yeni görev")
    assert_cleared(factory, old)
