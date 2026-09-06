"""Persistence for research_runs/candidates/evidence/reports (M13 spec §5).

Same discipline as app/broker/service.py and app/artifacts/service.py: every
function takes an open Session and commits before returning; async callers
run them via asyncio.to_thread, Temporal activities call them directly
(activities are synchronous). Every write here is idempotent on its natural
key so a workflow-activity retry/replay never duplicates a row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.research.discovery import DiscoveredCandidate
from app.research.models import (
    STAGE_PLANNED,
    STAGE_READY,
    ResearchCandidateRow,
    ResearchEvidenceRow,
    ResearchReportRow,
    ResearchRunRow,
)

MAX_EVENTS = 50


def utcnow() -> datetime:
    return datetime.now(UTC)


# ----------------------------------------------------------------------- run


def get_or_create_run(session: Session, task_id: uuid.UUID) -> ResearchRunRow:
    run = session.get(ResearchRunRow, task_id)
    if run is not None:
        return run
    run = ResearchRunRow(task_id=task_id, stage=STAGE_PLANNED)
    session.add(run)
    session.commit()
    return run


def get_run(session: Session, task_id: uuid.UUID) -> ResearchRunRow | None:
    return session.get(ResearchRunRow, task_id)


def list_runs(session: Session, *, limit: int = 100) -> list[ResearchRunRow]:
    return list(
        session.execute(
            select(ResearchRunRow).order_by(ResearchRunRow.created_at.desc()).limit(limit)
        ).scalars()
    )


def update_run(
    session: Session,
    task_id: uuid.UUID,
    *,
    stage: str | None = None,
    plan_json: dict[str, Any] | None = None,
    device_id: uuid.UUID | None = None,
    progress: dict[str, Any] | None = None,
    error: str | None = None,
    event: dict[str, Any] | None = None,
) -> ResearchRunRow:
    run = get_or_create_run(session, task_id)
    if stage is not None:
        run.stage = stage
    if plan_json is not None:
        run.plan_json = plan_json
    if device_id is not None:
        run.device_id = device_id
    if progress is not None:
        run.progress_json = {**(run.progress_json or {}), **progress}
    if error is not None:
        run.error = error
    if event is not None:
        events = list(run.events_json or [])
        events.append({**event, "at": utcnow().isoformat()})
        run.events_json = events[-MAX_EVENTS:]
    run.updated_at = utcnow()
    # docs/DECISIONS.md ADR-0076: the moment a run becomes READY, it becomes the research
    # the owner is pointing at. HERE, because this is the one choke point every path that
    # marks a run ready goes through - the Temporal activity that persists the report, the
    # REST-started run and the voice-started run alike - so the focus cannot exist for one
    # entry point and not another. It is a best-effort append (see note_research_ready):
    # a completed research must never become a failed one because a focus row would not
    # write.
    if stage == STAGE_READY:
        from app.research import focus as focus_module

        focus_module.note_research_ready(session, task_id)
    session.commit()
    return run


# --------------------------------------------------------------- candidates


def insert_candidates(
    session: Session, task_id: uuid.UUID, candidates: list[DiscoveredCandidate]
) -> int:
    """Insert-or-ignore on (task_id, url); returns the count actually inserted."""
    if not candidates:
        return 0
    existing = set(
        session.execute(
            select(ResearchCandidateRow.url).where(ResearchCandidateRow.task_id == task_id)
        ).scalars()
    )
    inserted = 0
    for c in candidates:
        if c.url in existing:
            continue
        row = ResearchCandidateRow(
            task_id=task_id,
            url=c.url,
            publisher=c.publisher,
            discovered_by=c.discovered_by,
            query_id=c.query_id,
            published_hint=c.published_hint,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()  # lost a race with another writer; already present
            continue
        existing.add(c.url)
        inserted += 1
    session.commit()
    return inserted


def list_candidates(session: Session, task_id: uuid.UUID) -> list[ResearchCandidateRow]:
    return list(
        session.execute(
            select(ResearchCandidateRow)
            .where(ResearchCandidateRow.task_id == task_id)
            .order_by(ResearchCandidateRow.created_at)
        ).scalars()
    )


# ----------------------------------------------------------------- evidence


def upsert_evidence(
    session: Session,
    task_id: uuid.UUID,
    url: str,
    *,
    evidence_json: dict[str, Any],
    device_id: uuid.UUID | None,
    command_id: uuid.UUID | None,
    injection_suspected: bool,
    syndicated_of: str | None = None,
) -> tuple[ResearchEvidenceRow, bool]:
    """Idempotent on (task_id, url): a second success for the same URL is
    ignored — the existing row is returned unchanged (spec §5)."""
    existing = session.execute(
        select(ResearchEvidenceRow).where(
            ResearchEvidenceRow.task_id == task_id, ResearchEvidenceRow.url == url
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = ResearchEvidenceRow(
        task_id=task_id,
        url=url,
        evidence_json=evidence_json,
        device_id=device_id,
        command_id=command_id,
        injection_suspected=injection_suspected,
        syndicated_of=syndicated_of,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        winner = session.execute(
            select(ResearchEvidenceRow).where(
                ResearchEvidenceRow.task_id == task_id, ResearchEvidenceRow.url == url
            )
        ).scalar_one()
        return winner, False
    session.commit()
    return row, True


def update_evidence_ranking(session: Session, task_id: uuid.UUID, records: list[Any]) -> None:
    """After dedup/rank (``app.research.evidence.dedup_and_rank`` +
    ``app.research.report.assign_evidence_ids``), persist the computed
    id/rank/score/syndicated_of back onto each row's ``evidence_json`` so a
    replayed activity reads the same ranking rather than recomputing it from
    scratch against evidence that may have grown since (idempotent: writing
    the same ranking twice is a no-op in effect)."""
    rows = {
        r.url: r
        for r in session.execute(
            select(ResearchEvidenceRow).where(ResearchEvidenceRow.task_id == task_id)
        ).scalars()
    }
    for record in records:
        row = rows.get(record.url)
        if row is None:
            continue
        row.evidence_json = record.as_dict()
        row.injection_suspected = record.injection_suspected
        row.syndicated_of = record.syndicated_of
    session.commit()


def clear_stale_evidence_ids(session: Session, task_id: uuid.UUID, keep_urls: set[str]) -> None:
    """Drop the citation id from every evidence row the latest ranking did not keep.

    Ranking can run more than once for a job (a top-up round after the quality gate leaves
    too little), and it renumbers e1..eN over whatever it keeps. A row kept by an earlier
    round but not by the latest one would otherwise hold a stale id that a different source
    now also carries - two sources answering to "[e2]", so a finding's citation no longer
    identifies anything. The row itself is untouched; only the id, rank and score go.
    """
    rows = session.execute(
        select(ResearchEvidenceRow).where(ResearchEvidenceRow.task_id == task_id)
    ).scalars()
    for row in rows:
        if row.url in keep_urls:
            continue
        payload = dict(row.evidence_json or {})
        if not payload.get("id"):
            continue
        payload["id"] = ""
        payload["rank"] = 0
        payload["score"] = 0.0
        row.evidence_json = payload
    session.commit()


def record_evidence_gate(
    session: Session, task_id: uuid.UUID, verdicts: dict[str, dict[str, Any]]
) -> None:
    """Persist the pre-synthesis quality gate verdict onto each evidence row.

    The verdict has to live on the row, not only in the run's event trail, because
    synthesis reloads evidence from the store on a later activity (and on replay). Before
    this existed, ranking refused a page and synthesis then read the very same row back and
    cited it - which is how a Cloudflare interstitial became source [e7] on 2026-09-04.

    Rows are annotated, never deleted: the fetched page stays exactly as it was captured so
    the run can still be audited, and only the verdict is added alongside it.
    """
    rows = {
        r.url: r
        for r in session.execute(
            select(ResearchEvidenceRow).where(ResearchEvidenceRow.task_id == task_id)
        ).scalars()
    }
    for url, verdict in verdicts.items():
        row = rows.get(url)
        if row is None:
            continue
        payload = dict(row.evidence_json or {})
        payload["gate"] = verdict
        row.evidence_json = payload
    session.commit()


def list_evidence(session: Session, task_id: uuid.UUID) -> list[ResearchEvidenceRow]:
    return list(
        session.execute(
            select(ResearchEvidenceRow)
            .where(ResearchEvidenceRow.task_id == task_id)
            .order_by(ResearchEvidenceRow.created_at)
        ).scalars()
    )


# ------------------------------------------------------------------- report


def upsert_report(
    session: Session,
    task_id: uuid.UUID,
    *,
    report_json: dict[str, Any],
    synthesis_provider: str,
    artifact_id: uuid.UUID | None = None,
    memory_id: uuid.UUID | None = None,
) -> ResearchReportRow:
    """Idempotent: the report JSON is stored once per task_id, updated on retry."""
    row = session.get(ResearchReportRow, task_id)
    if row is None:
        row = ResearchReportRow(
            task_id=task_id,
            report_json=report_json,
            synthesis_provider=synthesis_provider,
            artifact_id=artifact_id,
            memory_id=memory_id,
        )
        session.add(row)
    else:
        row.report_json = report_json
        row.synthesis_provider = synthesis_provider
        if artifact_id is not None:
            row.artifact_id = artifact_id
        if memory_id is not None:
            row.memory_id = memory_id
        row.updated_at = utcnow()
    session.commit()
    return row


def get_report(session: Session, task_id: uuid.UUID) -> ResearchReportRow | None:
    return session.get(ResearchReportRow, task_id)


def set_report_artifact(session: Session, task_id: uuid.UUID, artifact_id: uuid.UUID) -> None:
    row = session.get(ResearchReportRow, task_id)
    if row is not None:
        row.artifact_id = artifact_id
        row.updated_at = utcnow()
        session.commit()


def set_report_memory(session: Session, task_id: uuid.UUID, memory_id: uuid.UUID) -> None:
    row = session.get(ResearchReportRow, task_id)
    if row is not None:
        row.memory_id = memory_id
        row.updated_at = utcnow()
        session.commit()


__all__ = [
    "get_or_create_run",
    "get_report",
    "get_run",
    "insert_candidates",
    "list_candidates",
    "list_evidence",
    "list_runs",
    "set_report_artifact",
    "set_report_memory",
    "update_run",
    "upsert_evidence",
    "upsert_report",
]
