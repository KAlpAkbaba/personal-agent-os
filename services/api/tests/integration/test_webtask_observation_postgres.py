"""ADR-0207 PR-C retention, on PostgreSQL: an ended task's row holds no page.

SQLite keeps whatever object the session last held; PostgreSQL keeps only what was
WRITTEN. An in-place change to the JSON document would pass a SQLite test and leave the
page text in the database, so every claim here is read with a JSON expression through a
fresh session after ``alembic upgrade head`` (the integration conftest migrates).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from app.artifacts.runtime import build_artifact_context
from app.config import Settings
from app.webtask import service
from app.webtask.loop import Ports
from app.webtask.models import WebTaskRow
from app.webtask.planner import ScriptedPlanner
from app.webtask.types import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_RUNNING,
    STATUS_WAITING_OWNER,
)
from tests.unit.test_webtask_acceptance import T3_SCRIPT, shop_site
from tests.webtask_support import Clock

pytestmark = pytest.mark.integration

_READ = text(
    "SELECT jsonb_typeof(state_json::jsonb -> 'observation') AS kind, "
    "state_json::jsonb ->> 'status' AS status, "
    "state_json::jsonb ->> 'observation_fresh' AS fresh "
    "FROM web_tasks WHERE id = :id"
)


@pytest.fixture()
def factory(settings: Settings) -> Iterator[Any]:
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


def _read(factory: Any, task_id: uuid.UUID) -> dict[str, Any]:
    with factory() as db:
        found = db.execute(_READ, {"id": task_id}).mappings().one()
        return dict(found)


def test_a_cancelled_task_holds_no_observation_in_postgres(factory) -> None:
    browser = shop_site()
    p = Ports(browser=browser, planner=ScriptedPlanner(list(T3_SCRIPT)), clock=Clock())
    with factory() as db:
        task_id = service.start_task_db(db, goal="Kulaklığı sepete ekle, ödemede dur").id
    with factory() as db:
        service.run_round_db(db, task_id, p)
    assert _read(factory, task_id) == {"kind": "object", "status": STATUS_RUNNING, "fresh": "true"}

    with factory() as db:
        service.cancel_db(db, task_id)
    assert _read(factory, task_id) == {"kind": "null", "status": STATUS_CANCELLED, "fresh": "false"}


def test_scrub_clears_a_seeded_terminal_row_in_postgres(factory) -> None:
    task_id = uuid.uuid4()
    moment = datetime.now(UTC) - timedelta(days=2)
    with factory() as db:
        db.add(
            WebTaskRow(
                id=task_id,
                goal="Eski görev (entegrasyon)",
                status=STATUS_DONE,
                attended=True,
                state_json={
                    "task_id": str(task_id),
                    "goal": "Eski görev (entegrasyon)",
                    "status": STATUS_DONE,
                    "observation": {
                        "observation_id": "obs-old",
                        "url": "https://posta.example.com/",
                        "title": "Gelen kutusu",
                        "page_kind": "ok",
                        "elements": [],
                        "text": "Sahibin postası.",
                    },
                    "observation_fresh": True,
                },
                created_at=moment,
                updated_at=moment,
                completed_at=moment,
            )
        )
        db.commit()
    assert _read(factory, task_id)["kind"] == "object"

    with factory() as db:
        assert service.scrub_observations(db, datetime.now(UTC)) >= 1
    assert _read(factory, task_id) == {"kind": "null", "status": STATUS_DONE, "fresh": "false"}
    with factory() as db:
        assert service.scrub_observations(db, datetime.now(UTC)) == 0
    with factory() as db:
        db.execute(text("DELETE FROM web_tasks WHERE id = :id"), {"id": task_id})
        db.commit()
