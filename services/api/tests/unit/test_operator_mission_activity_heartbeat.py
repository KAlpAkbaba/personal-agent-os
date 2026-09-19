"""The mission step activity runs its round on a worker thread (``asyncio.to_thread``) and
heartbeats Temporal from inside that round. An ASYNC activity's heartbeat schedules a task
on the activity's event loop, so it must be issued ON that loop's thread - issued from the
worker thread it raised ``RuntimeError: no running event loop`` and took the activity down
after the first task of the first step.

Production, 2026-09-19 09:39:58 (operator-mission-2bbf055f, and both 08:16 missions before
it): "YouTube'u aç" planned, Edge opened, then nothing - the row stayed "planned" with
``started_at`` empty, blocked every later mission for six minutes, and the owner heard
"Görev sizin cevabınızı bekliyor". This test runs the REAL activity under Temporal's own
``ActivityEnvironment`` and pins where the heartbeat is issued, not merely that one happened.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from typing import Any

import pytest
from temporalio.testing import ActivityEnvironment

from app.operator import mission_activities, mission_service


@pytest.mark.asyncio
async def test_the_rounds_heartbeat_is_issued_on_the_activitys_own_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_thread = threading.get_ident()
    beats: list[int] = []
    ran_on: list[int] = []

    @contextmanager
    def _db():
        yield object()

    monkeypatch.setattr(mission_activities, "_factory", lambda: _db)
    monkeypatch.setattr(mission_activities, "_ports", lambda: object())

    def run_step_db(db: Any, mission_id: Any, ports: Any, *, on_task: Any = None) -> dict[str, Any]:
        ran_on.append(threading.get_ident())
        assert on_task is not None
        on_task(None)  # the loop's own callback, fired from inside the round: the worker thread
        return {"status": "succeeded", "current_step": 1}

    monkeypatch.setattr(mission_service, "run_step_db", run_step_db)

    env = ActivityEnvironment()
    env.on_heartbeat = lambda *_details: beats.append(threading.get_ident())
    outcome = await env.run(mission_activities.mission_step_activity, str(uuid.uuid4()))

    assert outcome["status"] == "succeeded"
    assert ran_on and ran_on[0] != loop_thread, "the round must run off the loop (to_thread)"
    assert beats == [loop_thread], f"heartbeat issued on thread(s) {beats}, loop is {loop_thread}"
