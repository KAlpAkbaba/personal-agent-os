"""Activity Ledger writer, reader and backfill (M16_ACTIVITY_LEDGER_SPEC.md §1.2, §1.4).

Every function takes the SQLAlchemy ``Session`` first and owns its commit,
like the other service modules (``app.research.runs_service``,
``app.voice.realtime_sessions.service``).

Backfill never fabricates: every event it writes carries ``evidence_refs``
pointing at the row it came from, and ``factual_summary`` states only what
that row's own numbers support (spec §1.4). It is safe to call on every API
start and safe to call twice — unchanged source rows produce zero new
events, because idempotency is keyed on ``(source, source_ref)``.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_INCIDENT_OPENED,
    EVENT_TYPE_LEDGER_BACKFILL,
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_RESEARCH_FAILED,
    EVENT_TYPE_RESEARCH_QUALITY_GATE,
    EVENT_TYPE_VOICE_SESSION_ATTACHED,
    EVENT_TYPE_VOICE_SESSION_CLOSED,
    EVENT_TYPE_VOICE_SESSION_CREATED,
    PRODUCTION_STATE_DEPLOYED,
    PRODUCTION_STATE_NA,
    PRODUCTION_STATE_ROLLED_BACK,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INFO,
    SUBSYSTEM_DEPLOYMENT,
    SUBSYSTEM_LEDGER,
    SUBSYSTEM_RESEARCH,
    SUBSYSTEM_SELF_MODEL,
    SUBSYSTEM_VOICE,
    InvalidVocabulary,
    validate_event_type,
    validate_production_state,
    validate_severity,
    validate_status,
    validate_subsystem,
)
from app.logging import get_logger

logger = get_logger("app.ledger.service")

_SEVERITY_TR = {"info": "bilgi", "notice": "bildirim", "warning": "uyarı", "critical": "kritik"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def research_policy_version() -> str | None:
    """``app.research.routes.RESEARCH_POLICY_VERSION`` if importable without a
    cycle, else ``None`` (spec §1.2 writer table: "version = research policy
    when known"). Imported lazily — never at module load — precisely so a
    genuine cycle degrades to "omit version" instead of failing app startup."""
    try:
        from app.research.routes import RESEARCH_POLICY_VERSION
    except Exception:  # noqa: BLE001 - degrade to "unknown", never break the caller
        return None
    return str(RESEARCH_POLICY_VERSION)


# ------------------------------------------------------------- write contract


@dataclasses.dataclass(slots=True)
class ActivityEvent:
    """The write contract (spec §1.2). One instance = one row, or a no-op
    when ``(source, source_ref)`` already exists."""

    event_type: str
    subsystem: str
    action: str
    factual_summary: str
    source: str
    source_ref: str
    status: str = STATUS_COMPLETED
    severity: str = "info"
    result: str | None = None
    production_state: str = PRODUCTION_STATE_NA
    module: str | None = None
    version: str | None = None
    occurred_at: datetime | None = None
    command_id: uuid.UUID | None = None
    trace_id: str | None = None
    research_job_id: uuid.UUID | None = None
    browser_session_id: str | None = None
    related_goal_id: uuid.UUID | None = None
    related_module_id: str | None = None
    evidence_refs: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    detail_json: dict[str, Any] = dataclasses.field(default_factory=dict)


def _validate(event: ActivityEvent) -> None:
    validate_event_type(event.event_type)
    validate_subsystem(event.subsystem)
    validate_status(event.status)
    validate_severity(event.severity)
    validate_production_state(event.production_state)


def _find_existing(session: Session, source: str, source_ref: str) -> ActivityEventRow | None:
    return session.execute(
        select(ActivityEventRow).where(
            ActivityEventRow.source == source, ActivityEventRow.source_ref == source_ref
        )
    ).scalar_one_or_none()


def record(session: Session, event: ActivityEvent) -> ActivityEventRow:
    """Idempotent on ``(source, source_ref)``: a second write with the same
    key returns the existing row and changes nothing (spec §1.2)."""
    _validate(event)
    existing = _find_existing(session, event.source, event.source_ref)
    if existing is not None:
        return existing

    row = ActivityEventRow(
        occurred_at=event.occurred_at or utcnow(),
        event_type=event.event_type,
        subsystem=event.subsystem,
        module=event.module,
        version=event.version,
        status=event.status,
        severity=event.severity,
        action=event.action,
        result=event.result,
        production_state=event.production_state,
        command_id=event.command_id,
        trace_id=event.trace_id,
        research_job_id=event.research_job_id,
        browser_session_id=event.browser_session_id,
        related_goal_id=event.related_goal_id,
        related_module_id=event.related_module_id,
        evidence_refs=list(event.evidence_refs),
        factual_summary=event.factual_summary,
        detail_json=dict(event.detail_json),
        source=event.source,
        source_ref=event.source_ref,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # a concurrent writer won the race on (source, source_ref) — the row
        # now exists; return it rather than raising (idempotency, spec §1.2).
        session.rollback()
        existing = _find_existing(session, event.source, event.source_ref)
        if existing is None:
            raise
        return existing
    return row


# ------------------------------------------------------------------- reading


def query(
    session: Session,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    subsystems: list[str] | tuple[str, ...] | None = None,
    statuses: list[str] | tuple[str, ...] | None = None,
    event_types: list[str] | tuple[str, ...] | None = None,
    research_job_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[ActivityEventRow]:
    """Newest first (spec §1.2)."""
    stmt = select(ActivityEventRow)
    if since is not None:
        stmt = stmt.where(ActivityEventRow.occurred_at >= since)
    if until is not None:
        stmt = stmt.where(ActivityEventRow.occurred_at <= until)
    if subsystems:
        stmt = stmt.where(ActivityEventRow.subsystem.in_(list(subsystems)))
    if statuses:
        stmt = stmt.where(ActivityEventRow.status.in_(list(statuses)))
    if event_types:
        stmt = stmt.where(ActivityEventRow.event_type.in_(list(event_types)))
    if research_job_id is not None:
        stmt = stmt.where(ActivityEventRow.research_job_id == research_job_id)
    stmt = stmt.order_by(
        ActivityEventRow.occurred_at.desc(), ActivityEventRow.recorded_at.desc()
    ).limit(max(1, min(limit, 200)))
    return list(session.execute(stmt).scalars().all())


def latest(
    session: Session,
    *,
    subsystems: list[str] | tuple[str, ...] | None = None,
    statuses: tuple[str, ...] | list[str] = (STATUS_COMPLETED, STATUS_FAILED),
) -> ActivityEventRow | None:
    """The most recent completed/failed event, for "son ne yaptın" (spec §1.2)."""
    stmt = select(ActivityEventRow)
    if subsystems:
        stmt = stmt.where(ActivityEventRow.subsystem.in_(list(subsystems)))
    if statuses:
        stmt = stmt.where(ActivityEventRow.status.in_(list(statuses)))
    stmt = stmt.order_by(
        ActivityEventRow.occurred_at.desc(), ActivityEventRow.recorded_at.desc()
    ).limit(1)
    return session.execute(stmt).scalars().first()


def _count_by(session: Session, column: Any, since: datetime) -> dict[str, int]:
    stmt = (
        select(column, func.count(ActivityEventRow.event_id))
        .where(ActivityEventRow.occurred_at >= since)
        .group_by(column)
    )
    return {key: count for key, count in session.execute(stmt).all()}


def count_by_status(session: Session, since: datetime) -> dict[str, int]:
    return _count_by(session, ActivityEventRow.status, since)


def count_by_subsystem(session: Session, since: datetime) -> dict[str, int]:
    return _count_by(session, ActivityEventRow.subsystem, since)


# --------------------------------------------------------------------- backfill


@dataclasses.dataclass(slots=True)
class BackfillReport:
    """Per-category counts of source rows examined/newly-recorded/already-
    present (spec §1.4). A re-run against unchanged source rows reports
    ``created == {}`` (or all-zero) for every category."""

    examined: dict[str, int] = dataclasses.field(default_factory=dict)
    created: dict[str, int] = dataclasses.field(default_factory=dict)
    skipped: dict[str, int] = dataclasses.field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "examined": dict(self.examined),
            "created": dict(self.created),
            "skipped": dict(self.skipped),
            "total_created": self.total_created,
        }

    @property
    def total_created(self) -> int:
        return sum(self.created.values())


def _bump(bucket: dict[str, int], category: str) -> None:
    bucket[category] = bucket.get(category, 0) + 1


def _emit(session: Session, report: BackfillReport, category: str, event: ActivityEvent) -> None:
    _bump(report.examined, category)
    existed = _find_existing(session, event.source, event.source_ref) is not None
    try:
        record(session, event)
    except InvalidVocabulary as exc:
        # One row that cannot be described must not end the backfill of every other
        # row; it is counted and named, never silently dropped (found on the real dev
        # database: a release component name the vocabulary could not spell).
        _bump(report.skipped, f"{category}:invalid")
        logger.warning(
            "ledger_backfill_row_skipped",
            category=category,
            source_ref=event.source_ref,
            reason=str(exc),
        )
        return
    _bump(report.skipped if existed else report.created, category)


def component_slug(name: str) -> str:
    """A release component as an event-type segment: lowercase, [a-z0-9_] only.

    ``browser-agent-demo-4326f3af`` becomes ``browser_agent_demo_4326f3af``; the raw
    name stays on the event's ``module`` field, so nothing is lost and the vocabulary's
    ``deployment.<component>.<state>`` rule holds for every component that exists.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or "unknown"


def _research_completed_summary(stats: dict[str, Any], findings_n: int, sources_n: int) -> str:
    rejected_n = int(stats.get("rejected", 0) or 0)
    text = f"Araştırma tamamlandı: {findings_n} bulgu, {sources_n} kaynak"
    if rejected_n:
        text += f"; {rejected_n} sayfa elendi."
    else:
        text += "."
    return text


def build_research_completed_event(
    *,
    task_id: uuid.UUID,
    occurred_at: datetime,
    report_json: dict[str, Any],
    artifact_id: uuid.UUID | None = None,
    memory_id: uuid.UUID | None = None,
    source: str = "live",
    source_ref: str | None = None,
) -> ActivityEvent:
    """Shared by the live writer (``app.research.browser_activities.
    persist_artifact_activity``) and backfill, so the two mechanisms describe
    the same fact identically (spec §1.2 writer table + §1.4 backfill table)."""
    stats = dict(report_json.get("stats") or {})
    findings_n = len(report_json.get("findings") or [])
    sources_n = len(report_json.get("sources") or [])
    detail = {**stats, "findings": findings_n, "sources": sources_n}
    evidence_refs: list[dict[str, Any]] = [{"kind": "research_report", "ref": str(task_id)}]
    if artifact_id is not None:
        evidence_refs.append({"kind": "artifact", "ref": str(artifact_id)})
    if memory_id is not None:
        evidence_refs.append({"kind": "memory", "ref": str(memory_id)})
    return ActivityEvent(
        event_type=EVENT_TYPE_RESEARCH_COMPLETED,
        subsystem=SUBSYSTEM_RESEARCH,
        action="research_completed",
        status=STATUS_COMPLETED,
        result=f"{findings_n} findings, {sources_n} sources",
        factual_summary=_research_completed_summary(stats, findings_n, sources_n),
        occurred_at=occurred_at,
        research_job_id=task_id,
        version=research_policy_version(),
        evidence_refs=evidence_refs,
        detail_json=detail,
        source=source,
        source_ref=source_ref or f"research_runs:{task_id}:ready",
    )


def build_research_failed_event(
    *,
    task_id: uuid.UUID,
    occurred_at: datetime,
    error_class: str,
    error: str,
    source: str = "live",
    source_ref: str | None = None,
) -> ActivityEvent:
    return ActivityEvent(
        event_type=EVENT_TYPE_RESEARCH_FAILED,
        subsystem=SUBSYSTEM_RESEARCH,
        action="research_failed",
        status=STATUS_FAILED,
        result=error_class,
        factual_summary=f"Araştırma başarısız oldu: {error_class}.",
        occurred_at=occurred_at,
        research_job_id=task_id,
        evidence_refs=[{"kind": "research_run", "ref": str(task_id)}],
        detail_json={"error_class": error_class, "error": error[:2000]},
        source=source,
        source_ref=source_ref or f"research_runs:{task_id}:failed",
    )


def _live_already_recorded(
    session: Session, *, event_type: str, research_job_id: uuid.UUID
) -> bool:
    """True when the live writer already put this exact fact in the ledger.

    Backfill and the live writer (spec §1.2/§1.4) describe the SAME research
    completion/failure under deliberately different ``source`` values
    (``live`` vs ``backfill:research_runs``), so the ``(source, source_ref)``
    uniqueness alone cannot dedup them. This checks the natural key instead —
    called only for runs a live writer covers today, so an OLD run that
    finished before this milestone shipped (no live event exists) is still
    backfilled, exactly as the M16 acceptance flow requires."""
    stmt = (
        select(ActivityEventRow.event_id)
        .where(
            ActivityEventRow.event_type == event_type,
            ActivityEventRow.research_job_id == research_job_id,
            ActivityEventRow.source == "live",
        )
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def _backfill_research(session: Session, report: BackfillReport) -> None:
    from app.research.models import (
        STAGE_FAILED,
        STAGE_RANKING,
        STAGE_READY,
        ResearchReportRow,
        ResearchRunRow,
    )

    runs = session.execute(select(ResearchRunRow)).scalars().all()
    for run in runs:
        task_id = run.task_id
        if run.stage == STAGE_READY:
            if _live_already_recorded(
                session, event_type=EVENT_TYPE_RESEARCH_COMPLETED, research_job_id=task_id
            ):
                _bump(report.examined, "research_completed")
                _bump(report.skipped, "research_completed")
            else:
                report_row = session.execute(
                    select(ResearchReportRow).where(ResearchReportRow.task_id == task_id)
                ).scalar_one_or_none()
                if report_row is not None:
                    _emit(
                        session,
                        report,
                        "research_completed",
                        build_research_completed_event(
                            task_id=task_id,
                            occurred_at=run.updated_at,
                            report_json=report_row.report_json or {},
                            artifact_id=report_row.artifact_id,
                            memory_id=report_row.memory_id,
                            source="backfill:research_runs",
                            source_ref=f"research_runs:{task_id}:ready",
                        ),
                    )
        elif run.stage == STAGE_FAILED:
            if _live_already_recorded(
                session, event_type=EVENT_TYPE_RESEARCH_FAILED, research_job_id=task_id
            ):
                _bump(report.examined, "research_failed")
                _bump(report.skipped, "research_failed")
            else:
                error_class = "unknown"
                try:
                    from app.artifacts.models import Task

                    task_row = session.get(Task, task_id)
                    if task_row is not None and task_row.error_class:
                        error_class = task_row.error_class
                except Exception:  # noqa: BLE001 - never fail backfill on a lookup miss
                    pass
                _emit(
                    session,
                    report,
                    "research_failed",
                    build_research_failed_event(
                        task_id=task_id,
                        occurred_at=run.updated_at,
                        error_class=error_class,
                        error=run.error or "",
                        source="backfill:research_runs",
                        source_ref=f"research_runs:{task_id}:failed",
                    ),
                )

        for entry in run.events_json or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("stage") != STAGE_RANKING:
                continue
            rejected = entry.get("rejected")
            if not rejected:
                continue
            at_raw = entry.get("at")
            try:
                occurred_at = datetime.fromisoformat(str(at_raw)) if at_raw else run.updated_at
            except ValueError:
                occurred_at = run.updated_at
            rejected_total = sum(int(v) for v in rejected.values())
            _emit(
                session,
                report,
                "research_quality_gate",
                ActivityEvent(
                    event_type=EVENT_TYPE_RESEARCH_QUALITY_GATE,
                    subsystem=SUBSYSTEM_RESEARCH,
                    action="quality_gate_rejected",
                    status=STATUS_INFO,
                    result=f"{rejected_total} rejected",
                    factual_summary=f"Kalite kapısı {rejected_total} sayfayı eledi.",
                    occurred_at=occurred_at,
                    research_job_id=task_id,
                    evidence_refs=[{"kind": "research_run", "ref": str(task_id)}],
                    detail_json={
                        "rejected": rejected,
                        "rejected_examples": (entry.get("rejected_examples") or [])[:10],
                    },
                    source="backfill:research_runs",
                    source_ref=f"research_runs:{task_id}:quality_gate:{at_raw}",
                ),
            )


#: audit action -> (session state, event type, the one Turkish sentence). The state is the
#: same word the live writer keys its own row on, so the two halves name one fact.
_VOICE_ACTION_EVENT_TYPE = {
    "voice_session_created": (
        "created",
        EVENT_TYPE_VOICE_SESSION_CREATED,
        "Sesli oturum oluşturuldu.",
    ),
    "voice_session_attached": (
        "attached",
        EVENT_TYPE_VOICE_SESSION_ATTACHED,
        "Sesli oturum yeniden bağlandı.",
    ),
    "voice_session_closed": (
        "closed",
        EVENT_TYPE_VOICE_SESSION_CLOSED,
        "Sesli oturum kapandı.",
    ),
}


def voice_session_source_ref(session_ref: object, state: str) -> str:
    """The key for "this voice session reached this state" — defined once, read by both.

    The live writer (``app.voice.realtime_sessions.service._ledger``) uses it as its own
    ``source_ref``; the backfill below uses it to ask whether the live writer already
    covered the session it is looking at. A guard that restated the shape instead of
    sharing it would drift the moment either side changed its wording.
    """
    return f"realtime_sessions:{session_ref}:{state}"


def _voice_live_already_recorded(session: Session, *, session_ref: str, state: str) -> bool:
    """True when the live writer already put this session's transition in the ledger.

    Same problem as ``_live_already_recorded`` for research: the live writer and the
    backfill describe the SAME fact under deliberately different ``source`` values, so the
    ``(source, source_ref)`` uniqueness alone cannot see they are one fact. Voice had no
    such guard, so every session created since the live writer shipped had TWO rows.
    """
    stmt = (
        select(ActivityEventRow.event_id)
        .where(
            ActivityEventRow.source == "live",
            ActivityEventRow.source_ref == voice_session_source_ref(session_ref, state),
        )
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def _backfill_voice(session: Session, report: BackfillReport) -> None:
    """One session, one row per state.

    Two audit rows can also describe one fact: attaching is repeatable, and the live writer
    collapses every attach of a session into the single row its key names. The backfill
    keeps that meaning by emitting only the FIRST audit row of each (session, state) — the
    oldest one, so the choice is stable and a re-run still records nothing new.
    """
    from app.broker.models import AuditEvent

    rows = (
        session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.category == "voice_realtime",
                AuditEvent.action.in_(list(_VOICE_ACTION_EVENT_TYPE)),
            )
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
        .scalars()
        .all()
    )
    seen: set[tuple[str, str]] = set()
    for row in rows:
        state, event_type, summary = _VOICE_ACTION_EVENT_TYPE[row.action]
        # An audit row with no subject cannot be tied to a session; it keeps the old
        # per-audit-row key rather than being dropped.
        session_ref = row.subject_ref or ""
        if session_ref:
            already = (session_ref, state) in seen or _voice_live_already_recorded(
                session, session_ref=session_ref, state=state
            )
            seen.add((session_ref, state))
            if already:
                _bump(report.examined, "voice_session")
                _bump(report.skipped, "voice_session")
                continue
        _emit(
            session,
            report,
            "voice_session",
            ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_VOICE,
                action=row.action,
                status=STATUS_COMPLETED,
                factual_summary=summary,
                occurred_at=row.created_at,
                trace_id=row.trace_id,
                evidence_refs=[{"kind": "audit_event", "ref": str(row.id)}],
                detail_json={"subject_ref": row.subject_ref},
                source="backfill:audit_events",
                source_ref=f"audit_events:{row.id}:{row.action}",
            ),
        )


def _backfill_deployment(session: Session, report: BackfillReport) -> None:
    from app.selfhealing.models import Release

    releases = session.execute(select(Release)).scalars().all()
    for rel in releases:
        base_evidence = [{"kind": "release", "ref": str(rel.id)}]
        if rel.promoted_at is not None:
            event_type = f"deployment.{component_slug(rel.component)}.released"
            _emit(
                session,
                report,
                "deployment",
                ActivityEvent(
                    event_type=event_type,
                    subsystem=SUBSYSTEM_DEPLOYMENT,
                    module=rel.component,
                    action="release_promoted",
                    status=STATUS_COMPLETED,
                    version=rel.version,
                    production_state=PRODUCTION_STATE_DEPLOYED,
                    factual_summary=f"{rel.component} sürüm {rel.version} yayına alındı.",
                    occurred_at=rel.promoted_at,
                    evidence_refs=base_evidence,
                    detail_json={
                        "status": rel.status,
                        "git_commit": rel.git_commit,
                        "manifest_digest": rel.manifest_digest,
                    },
                    source="backfill:releases",
                    source_ref=f"releases:{rel.id}:released",
                ),
            )
        if rel.rolled_back_at is not None:
            event_type = f"deployment.{component_slug(rel.component)}.rolled_back"
            _emit(
                session,
                report,
                "deployment",
                ActivityEvent(
                    event_type=event_type,
                    subsystem=SUBSYSTEM_DEPLOYMENT,
                    module=rel.component,
                    action="release_rolled_back",
                    status=STATUS_COMPLETED,
                    version=rel.version,
                    production_state=PRODUCTION_STATE_ROLLED_BACK,
                    factual_summary=f"{rel.component} sürüm {rel.version} geri alındı.",
                    occurred_at=rel.rolled_back_at,
                    evidence_refs=base_evidence,
                    detail_json={
                        "status": rel.status,
                        "git_commit": rel.git_commit,
                        "manifest_digest": rel.manifest_digest,
                    },
                    source="backfill:releases",
                    source_ref=f"releases:{rel.id}:rolled_back",
                ),
            )


def _backfill_incidents(session: Session, report: BackfillReport) -> None:
    from app.selfhealing.models import Incident

    incidents = session.execute(select(Incident)).scalars().all()
    for inc in incidents:
        severity = inc.severity if inc.severity else "warning"
        severity_tr = _SEVERITY_TR.get(severity, severity)
        _emit(
            session,
            report,
            "incident",
            ActivityEvent(
                event_type=EVENT_TYPE_INCIDENT_OPENED,
                subsystem=SUBSYSTEM_SELF_MODEL,
                module=inc.component,
                action="incident_opened",
                status=STATUS_INFO,
                severity=severity,
                trace_id=inc.trace_id,
                factual_summary=(
                    f"{inc.component} bileşeninde {severity_tr} düzeyinde olay açıldı."
                ),
                occurred_at=inc.first_seen_at,
                evidence_refs=[{"kind": "incident", "ref": str(inc.id)}],
                detail_json={
                    "status": inc.status,
                    "occurrence_count": inc.occurrence_count,
                    "fingerprint": inc.fingerprint,
                },
                source="backfill:incidents",
                source_ref=f"incidents:{inc.id}:opened",
            ),
        )


def backfill(session: Session, *, now: datetime | None = None) -> BackfillReport:
    """Only rows that already exist in canonical tables become events, and
    every event carries the reference it was derived from (spec §1.4).
    Re-runnable: a re-run against unchanged rows records nothing new."""
    now = now or utcnow()
    report = BackfillReport()
    for name, step in (
        ("research", _backfill_research),
        ("voice", _backfill_voice),
        ("deployment", _backfill_deployment),
        ("incidents", _backfill_incidents),
    ):
        try:
            step(session, report)
        except Exception as exc:  # noqa: BLE001 - one source must not end the others
            session.rollback()
            _bump(report.skipped, f"{name}:error")
            logger.warning("ledger_backfill_source_failed", source=name, reason=type(exc).__name__)

    # the run itself is a live fact (spec §1.2 writer table: "ledger |
    # backfill runs | ledger.backfill"), always new — NOT counted in the
    # per-category created/skipped tallies above, which report only on the
    # source tables.
    record(
        session,
        ActivityEvent(
            event_type=EVENT_TYPE_LEDGER_BACKFILL,
            subsystem=SUBSYSTEM_LEDGER,
            action="backfill_run",
            status=STATUS_COMPLETED,
            factual_summary=f"Kayıt defteri {report.total_created} yeni olay ekledi.",
            occurred_at=now,
            evidence_refs=[],
            detail_json=report.as_dict(),
            source="live",
            source_ref=f"ledger_backfill:{now.isoformat()}",
        ),
    )
    return report


__all__ = [
    "ActivityEvent",
    "BackfillReport",
    "backfill",
    "build_research_completed_event",
    "build_research_failed_event",
    "count_by_status",
    "count_by_subsystem",
    "latest",
    "query",
    "record",
    "research_policy_version",
    "utcnow",
    "voice_session_source_ref",
]
