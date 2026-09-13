"""Artifact + task persistence (synchronous, one transaction per call).

Same discipline as app/broker/service.py: every function takes an open Session
and commits before returning, so async callers run them via
`asyncio.to_thread`. All mutating operations are written to be idempotent so the
durable research workflow can retry any activity (worker restart / replay)
without creating duplicate artifacts, versions, renders or sources.
"""

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.artifacts.models import (
    ARTIFACT_KIND_RESEARCH_REPORT,
    CANONICAL_FORMAT_MARKDOWN,
    RENDER_STATE_VALID,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_CREATED,
    TASK_STATUS_FAILED_TERMINAL,
    TASK_STATUS_READY,
    Artifact,
    ArtifactRender,
    ArtifactVersion,
    ResearchSource,
    Task,
    TaskRun,
)
from app.artifacts.state import assert_artifact_transition, assert_task_transition
from app.logging import get_logger
from app.research.compose import ScoredSource

logger = get_logger("app.artifacts.service")


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------- tasks


def create_task(
    session: Session,
    *,
    intent: str,
    conversation_id: uuid.UUID | None = None,
    created_from_device_id: uuid.UUID | None = None,
    priority: int = 0,
    trace_id: str | None = None,
) -> Task:
    task = Task(
        intent=intent,
        conversation_id=conversation_id,
        created_from_device_id=created_from_device_id,
        priority=priority,
        status=TASK_STATUS_CREATED,
        trace_id=trace_id,
    )
    session.add(task)
    session.commit()
    return task


def get_task(session: Session, task_id: uuid.UUID) -> Task | None:
    return session.get(Task, task_id)


def list_tasks(session: Session, *, limit: int = 100) -> list[Task]:
    return list(
        session.execute(select(Task).order_by(Task.created_at.desc()).limit(limit)).scalars()
    )


def set_task_workflow_id(session: Session, task_id: uuid.UUID, workflow_id: str) -> None:
    task = session.get(Task, task_id)
    if task is not None:
        task.workflow_id = workflow_id
        session.commit()


def transition_task(
    session: Session,
    task_id: uuid.UUID,
    new_status: str,
    *,
    error_class: str | None = None,
    error_message: str | None = None,
) -> Task | None:
    task = session.get(Task, task_id)
    if task is None:
        return None
    assert_task_transition(task.status, new_status)
    task.status = new_status
    if error_class is not None:
        task.error_class = error_class
        task.error_message = error_message
    now = utcnow()
    if new_status == TASK_STATUS_READY and task.ready_at is None:
        task.ready_at = now
    if new_status == TASK_STATUS_COMPLETED and task.completed_at is None:
        task.completed_at = now
    session.commit()
    # B12 req 381/382/387: the owner hears about work finishing or failing HERE, at the one
    # transition every kind of task passes through, rather than at each producer. A producer
    # added later is covered without remembering to be - and three of them (research, the
    # app factory, the native factory) each had their own idea of "done" before this.
    _notify_task_transition(session, task, new_status)
    return task


#: Task intents that are a research question. The owner asked these in their own words, so
#: the notification quotes the question rather than saying "task 3f2a completed".
_RESEARCH_KINDS: Final[tuple[str, ...]] = ("research", "araştır")


def _notify_task_transition(session: Session, task: Task, new_status: str) -> None:
    """Best-effort: a notification fault must never undo a transition already committed."""
    if new_status not in (TASK_STATUS_READY, TASK_STATUS_FAILED_TERMINAL):
        return
    try:
        from app.notifications import events

        intent = (task.intent or "").strip()
        what = intent[:120] if intent else "İş"
        if new_status == TASK_STATUS_FAILED_TERMINAL:
            events.task_failed(
                session, task_id=task.id, what=what, error_class=task.error_class or ""
            )
        elif any(kind in intent.lower() for kind in _RESEARCH_KINDS):
            events.research_finished(session, task_id=task.id, question=what)
        else:
            events.task_completed(session, task_id=task.id, what=what)
    except Exception as exc:  # noqa: BLE001 - see docstring
        # Rolling back is not optional: a swallowed database error leaves the session in a
        # failed transaction, and the NEXT caller inherits it. B07 learned this on the
        # routine clock's sub-ticks - isolation that hands on a poisoned session is not
        # isolation, it is a delayed failure with somebody else's name on it.
        with contextlib.suppress(Exception):
            session.rollback()
        logger.warning(
            "task_transition_notification_failed",
            task_id=str(task.id),
            error=f"{type(exc).__name__}: {exc}",
        )


def start_task_run(
    session: Session, *, task_id: uuid.UUID, attempt: int, status: str, plan: dict[str, Any] | None
) -> TaskRun:
    """Idempotent per (task_id, attempt)."""
    existing = session.execute(
        select(TaskRun).where(TaskRun.task_id == task_id, TaskRun.attempt == attempt)
    ).scalar_one_or_none()
    if existing is not None:
        existing.status = status
        if plan is not None:
            existing.plan_json = plan
        session.commit()
        return existing
    run = TaskRun(task_id=task_id, attempt=attempt, status=status, plan_json=plan)
    session.add(run)
    session.commit()
    return run


def finish_task_run(
    session: Session,
    *,
    task_id: uuid.UUID,
    attempt: int,
    status: str,
    telemetry: dict[str, Any] | None = None,
    error_class: str | None = None,
) -> None:
    run = session.execute(
        select(TaskRun).where(TaskRun.task_id == task_id, TaskRun.attempt == attempt)
    ).scalar_one_or_none()
    if run is None:
        return
    run.status = status
    run.telemetry_json = telemetry
    run.error_class = error_class
    run.ended_at = utcnow()
    session.commit()


# ----------------------------------------------------------- research sources


def replace_research_sources(
    session: Session, *, task_id: uuid.UUID, sources: list[ScoredSource]
) -> list[ResearchSource]:
    """Persist sources for a task, idempotently (delete-then-insert)."""
    session.execute(delete(ResearchSource).where(ResearchSource.task_id == task_id))
    rows = [
        ResearchSource(
            task_id=task_id,
            url=s.url,
            title=s.title,
            snippet=s.snippet,
            score=s.score,
            rank=s.rank,
            provider=s.provider,
        )
        for s in sources
    ]
    session.add_all(rows)
    session.commit()
    return rows


def list_research_sources(session: Session, task_id: uuid.UUID) -> list[ResearchSource]:
    return list(
        session.execute(
            select(ResearchSource)
            .where(ResearchSource.task_id == task_id)
            .order_by(ResearchSource.rank)
        ).scalars()
    )


# ------------------------------------------------------------------ artifacts


def get_artifact(session: Session, artifact_id: uuid.UUID) -> Artifact | None:
    return session.get(Artifact, artifact_id)


def list_artifacts(session: Session, *, limit: int = 100) -> list[Artifact]:
    return list(
        session.execute(
            select(Artifact).order_by(Artifact.created_at.desc()).limit(limit)
        ).scalars()
    )


def get_artifact_for_task(session: Session, task_id: uuid.UUID) -> Artifact | None:
    return session.execute(
        select(Artifact).where(Artifact.task_id == task_id).order_by(Artifact.created_at)
    ).scalars().first()


def get_or_create_artifact_for_task(
    session: Session,
    *,
    task_id: uuid.UUID | None,
    title: str,
    kind: str = ARTIFACT_KIND_RESEARCH_REPORT,
    conversation_id: uuid.UUID | None = None,
) -> Artifact:
    if task_id is not None:
        existing = get_artifact_for_task(session, task_id)
        if existing is not None:
            return existing
    artifact = Artifact(
        task_id=task_id,
        conversation_id=conversation_id,
        title=title,
        kind=kind,
        canonical_format=CANONICAL_FORMAT_MARKDOWN,
    )
    session.add(artifact)
    session.commit()
    return artifact


def create_artifact(
    session: Session,
    *,
    title: str,
    kind: str,
    canonical_format: str = CANONICAL_FORMAT_MARKDOWN,
    conversation_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> Artifact:
    """A standalone artifact with no owning Task (M22 factory artifacts: the owner
    asked for a document/spreadsheet/... directly, not through a research task)."""
    artifact = Artifact(
        title=title,
        kind=kind,
        canonical_format=canonical_format,
        conversation_id=conversation_id,
        project_id=project_id,
    )
    session.add(artifact)
    session.commit()
    return artifact


def get_artifact_version_by_content_hash(
    session: Session, content_hash: str
) -> ArtifactVersion | None:
    """Idempotency for app.artifacts.factory.create(): the SAME spec submitted twice
    (a retried voice tool call, a retried POST) hashes to the SAME canonical JSON, so
    this finds the version already created for it instead of minting a duplicate
    artifact. sha256 collisions are not a practical concern at this scale."""
    return session.execute(
        select(ArtifactVersion).where(ArtifactVersion.content_hash == content_hash)
    ).scalar_one_or_none()


def set_artifact_state(session: Session, artifact_id: uuid.UUID, new_state: str) -> Artifact | None:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        return None
    assert_artifact_transition(artifact.state, new_state)
    artifact.state = new_state
    artifact.updated_at = utcnow()
    session.commit()
    return artifact


def set_executive_summary(
    session: Session, artifact_id: uuid.UUID, executive_summary: str
) -> Artifact | None:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        return None
    artifact.executive_summary = executive_summary
    artifact.updated_at = utcnow()
    session.commit()
    return artifact


def add_artifact_version(
    session: Session,
    *,
    artifact_id: uuid.UUID,
    canonical_body: str,
    content_hash: str,
    source_manifest: dict[str, Any] | None = None,
) -> ArtifactVersion:
    """Create the next version, or return the current one if its content_hash is
    unchanged (idempotent re-compose)."""
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        raise ValueError(f"unknown artifact: {artifact_id}")
    current = get_current_version(session, artifact_id)
    if current is not None and current.content_hash == content_hash:
        return current
    next_version = artifact.current_version + 1
    version = ArtifactVersion(
        artifact_id=artifact_id,
        version=next_version,
        canonical_body=canonical_body,
        content_hash=content_hash,
        source_manifest_json=source_manifest,
    )
    session.add(version)
    artifact.current_version = next_version
    artifact.updated_at = utcnow()
    session.commit()
    return version


def get_current_version(session: Session, artifact_id: uuid.UUID) -> ArtifactVersion | None:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None or artifact.current_version == 0:
        return None
    return get_version(session, artifact_id, artifact.current_version)


def get_version(
    session: Session, artifact_id: uuid.UUID, version: int
) -> ArtifactVersion | None:
    return session.execute(
        select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == artifact_id,
            ArtifactVersion.version == version,
        )
    ).scalar_one_or_none()


# -------------------------------------------------------------------- renders


def record_render(
    session: Session,
    *,
    artifact_version_id: uuid.UUID,
    fmt: str,
    object_key: str,
    mime_type: str,
    content_hash: str,
    size_bytes: int,
    validation_json: dict[str, Any] | None = None,
    state: str = RENDER_STATE_VALID,
) -> ArtifactRender:
    """Upsert a render row keyed on (artifact_version_id, format).

    ``validation_json``/``state`` are optional (M22, ADR-0085 decision 3) so the
    pre-M22 call site (app.artifacts.render_store.ensure_render, the research-report
    pipeline) is unaffected — it never passes them, and every row it writes keeps
    the column defaults (``state="valid"``, ``validation_json=None``). The Artifact
    Factory (app.artifacts.factory) always passes both, from a real
    ``app.artifacts.validation.validate()`` call.
    """
    existing = session.execute(
        select(ArtifactRender).where(
            ArtifactRender.artifact_version_id == artifact_version_id,
            ArtifactRender.format == fmt,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.object_key = object_key
        existing.mime_type = mime_type
        existing.content_hash = content_hash
        existing.size_bytes = size_bytes
        existing.validation_json = validation_json
        existing.state = state
        session.commit()
        return existing
    render = ArtifactRender(
        artifact_version_id=artifact_version_id,
        format=fmt,
        object_key=object_key,
        mime_type=mime_type,
        content_hash=content_hash,
        size_bytes=size_bytes,
        validation_json=validation_json,
        state=state,
    )
    session.add(render)
    session.commit()
    return render


def get_render(
    session: Session, artifact_version_id: uuid.UUID, fmt: str
) -> ArtifactRender | None:
    return session.execute(
        select(ArtifactRender).where(
            ArtifactRender.artifact_version_id == artifact_version_id,
            ArtifactRender.format == fmt,
        )
    ).scalar_one_or_none()


def list_renders(session: Session, artifact_version_id: uuid.UUID) -> list[ArtifactRender]:
    return list(
        session.execute(
            select(ArtifactRender)
            .where(ArtifactRender.artifact_version_id == artifact_version_id)
            .order_by(ArtifactRender.format)
        ).scalars()
    )
