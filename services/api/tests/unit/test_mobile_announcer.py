"""The artifact-ready announcer: the wire between the worker and push.

The worker only makes a task READY; the API process holds the registrations and
must be the one that delivers. They meet at `tasks.announced_at`, so no
long-lived worker credential exists and nothing is lost if the API is down when
a task completes (app/mobile/announcer.py).
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass
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


# ------------------------------------- M9 security review #1: deliver, then stamp


def test_a_failed_delivery_leaves_the_task_for_the_next_sweep(session_factory) -> None:
    """Stamping before delivering would silently drop the notification forever.

    Regression for the ordering the migration promises: a task whose delivery
    raised must remain unannounced so a later sweep retries it.
    """
    task_id = _seed(session_factory, status=TASK_STATUS_READY, title="Retry Me")
    failing = _Recorder(fail=True)
    announcer = ArtifactReadyAnnouncer(session_factory, failing)

    assert announcer.sweep_once() == 0
    with session_factory() as session:
        assert session.get(Task, task_id).announced_at is None, "task was consumed by a failure"
        assert list(pending_task_ids(session)) == [task_id]

    # Provider recovers -> the next sweep delivers it.
    working = _Recorder()
    assert ArtifactReadyAnnouncer(session_factory, working).sweep_once() == 1
    assert working.calls[0][2] == task_id
    with session_factory() as session:
        assert session.get(Task, task_id).announced_at is not None


@dataclass(frozen=True)
class _DeliveryReceipt:
    delivered: int


def test_a_returned_provider_failure_is_not_stamped_as_delivered(session_factory) -> None:
    """MobileService reports a transient provider failure as a returned receipt."""
    task_id = _seed(session_factory, status=TASK_STATUS_READY, title="Tekrar Dene")
    announcer = ArtifactReadyAnnouncer(
        session_factory, lambda *_args: _DeliveryReceipt(delivered=0)
    )

    assert announcer.sweep_once() == 0
    with session_factory() as session:
        assert session.get(Task, task_id).announced_at is None
        assert list(pending_task_ids(session)) == [task_id]

    recovered = ArtifactReadyAnnouncer(
        session_factory, lambda *_args: _DeliveryReceipt(delivered=1)
    )
    assert recovered.sweep_once() == 1
    with session_factory() as session:
        assert session.get(Task, task_id).announced_at is not None


def test_a_crash_mid_sweep_stamps_nothing_at_all(session_factory) -> None:
    """The pass is one transaction, so a crash rolls the whole sweep back.

    The task that was already delivered is retried on the next pass (a repeat
    push, which the collapse key makes harmless) rather than being stamped by a
    transaction that never finished.
    """
    ids = {
        _seed(session_factory, status=TASK_STATUS_READY, title="First"),
        _seed(session_factory, status=TASK_STATUS_READY, title="Second"),
    }

    delivered: list[uuid.UUID] = []

    def crash(artifact_id, title, task_id):
        if delivered:
            raise KeyboardInterrupt("process killed mid-sweep")
        delivered.append(task_id)

    announcer = ArtifactReadyAnnouncer(session_factory, crash)
    with pytest.raises(KeyboardInterrupt):
        announcer.sweep_once()

    assert len(delivered) == 1 and delivered[0] in ids
    with session_factory() as session:
        assert session.get(Task, delivered[0]).announced_at is None
        assert set(pending_task_ids(session)) == ids


def test_a_partial_batch_failure_only_settles_the_delivered_tasks(session_factory) -> None:
    good = _seed(session_factory, status=TASK_STATUS_READY, title="Good")
    bad = _seed(session_factory, status=TASK_STATUS_READY, title="Bad")

    class Selective:
        def __init__(self) -> None:
            self.calls = []

        def __call__(self, artifact_id, title, task_id):
            self.calls.append(task_id)
            if task_id == bad:
                raise RuntimeError("provider down for this one")

    announcer = ArtifactReadyAnnouncer(session_factory, Selective())
    assert announcer.sweep_once() == 1
    with session_factory() as session:
        assert session.get(Task, good).announced_at is not None
        assert session.get(Task, bad).announced_at is None


def test_a_boolean_where_a_count_belongs_is_not_proof_of_delivery(session_factory) -> None:
    """`delivered` is a COUNT on `NotificationResult`, and Python's `bool` is an `int`
    subclass -- so `True > 0` holds, and a notifier that answered `delivered=True` would slip
    through a plain `isinstance(delivered, int)` check as "one delivery".

    The reader refuses it: a boolean in a count field is a type confusion, and the only
    conservative reading of a receipt this module cannot interpret is "not proven". The task
    stays unstamped and is retried rather than marked delivered on a value that means
    something other than what the field says.

    Added at the handover review (2026-09-11). The guard was already in the code and no test
    held it -- removing `not isinstance(delivered, bool)` left the whole suite green.
    """
    task_id = _seed(session_factory, status=TASK_STATUS_READY, title="Bool Receipt")
    announcer = ArtifactReadyAnnouncer(
        session_factory, lambda *_args: _DeliveryReceipt(delivered=True)  # type: ignore[arg-type]
    )

    assert announcer.sweep_once() == 0
    with session_factory() as session:
        assert session.get(Task, task_id).announced_at is None
