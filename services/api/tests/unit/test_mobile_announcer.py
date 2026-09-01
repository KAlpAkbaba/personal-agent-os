"""The artifact-ready announcer: the wire between the worker and push.

The worker only makes a task READY; the API process holds the registrations and
must be the one that delivers. They meet at `tasks.announced_at`, so no
long-lived worker credential exists and nothing is lost if the API is down when
a task completes (app/mobile/announcer.py).
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import (
    ARTIFACT_STATE_READY,
    TASK_STATUS_READY,
    TASK_STATUS_RUNNING,
    Artifact,
    Task,
)
from app.mobile.announcer import ArtifactReadyAnnouncer, pending_task_ids


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Task.__table__.create(engine)
    Artifact.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return scope


def _seed(scope, *, status: str, with_artifact: bool = True, title: str = "Rapor") -> uuid.UUID:
    with scope() as session:
        task = Task(
            id=uuid.uuid4(),
            intent="test",
            status=status,
            ready_at=datetime.now(UTC) if status == TASK_STATUS_READY else None,
        )
        session.add(task)
        session.flush()
        if with_artifact:
            session.add(
                Artifact(
                    id=uuid.uuid4(),
                    task_id=task.id,
                    title=title,
                    kind="research_report",
                    canonical_format="markdown",
                    state=ARTIFACT_STATE_READY,
                    current_version=1,
                    executive_summary="ozet",
                )
            )
        session.commit()
        return task.id


class _Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[uuid.UUID, str, uuid.UUID]] = []
        self.fail = fail

    def __call__(self, artifact_id, title, task_id):
        self.calls.append((artifact_id, title, task_id))
        if self.fail:
            raise RuntimeError("provider down")


def test_a_ready_task_is_announced_once(session_factory) -> None:
    task_id = _seed(session_factory, status=TASK_STATUS_READY, title="Türkçe Rapor")
    recorder = _Recorder()
    announcer = ArtifactReadyAnnouncer(session_factory, recorder)

    assert announcer.sweep_once() == 1
    assert [c[1] for c in recorder.calls] == ["Türkçe Rapor"]
    assert recorder.calls[0][2] == task_id

    # The second sweep must not re-announce: the marker is the whole point.
    assert announcer.sweep_once() == 0
    assert len(recorder.calls) == 1


def test_a_task_that_is_not_ready_is_never_announced(session_factory) -> None:
    _seed(session_factory, status=TASK_STATUS_RUNNING)
    recorder = _Recorder()
    assert ArtifactReadyAnnouncer(session_factory, recorder).sweep_once() == 0
    assert recorder.calls == []


def test_a_ready_task_without_an_artifact_is_stamped_not_retried(session_factory) -> None:
    """Nothing to announce, but it must not be re-examined forever."""
    task_id = _seed(session_factory, status=TASK_STATUS_READY, with_artifact=False)
    announcer = ArtifactReadyAnnouncer(session_factory, _Recorder())
    assert announcer.sweep_once() == 0
    with session_factory() as session:
        assert session.get(Task, task_id).announced_at is not None
        assert list(pending_task_ids(session)) == []


def test_a_failing_provider_does_not_stall_the_batch(session_factory) -> None:
    for i in range(3):
        _seed(session_factory, status=TASK_STATUS_READY, title=f"R{i}")
    recorder = _Recorder(fail=True)
    announcer = ArtifactReadyAnnouncer(session_factory, recorder)
    # Every task is attempted even though each delivery raises.
    assert announcer.sweep_once() == 0
    assert len(recorder.calls) == 3


def test_pending_tasks_are_visible_before_the_sweep(session_factory) -> None:
    task_id = _seed(session_factory, status=TASK_STATUS_READY)
    with session_factory() as session:
        assert list(pending_task_ids(session)) == [task_id]
    ArtifactReadyAnnouncer(session_factory, _Recorder()).sweep_once()
    with session_factory() as session:
        assert list(pending_task_ids(session)) == []


def test_the_batch_size_bounds_one_sweep(session_factory) -> None:
    for i in range(5):
        _seed(session_factory, status=TASK_STATUS_READY, title=f"R{i}")
    recorder = _Recorder()
    announcer = ArtifactReadyAnnouncer(session_factory, recorder, batch=2)
    assert announcer.sweep_once() == 2
    assert announcer.sweep_once() == 2
    assert announcer.sweep_once() == 1
    assert announcer.sweep_once() == 0


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent(session_factory) -> None:
    announcer = ArtifactReadyAnnouncer(session_factory, _Recorder(), interval_s=0.01)
    await announcer.start()
    await announcer.start()  # second start must not spawn a second loop
    assert announcer.running
    await announcer.stop()
    await announcer.stop()
    assert not announcer.running
