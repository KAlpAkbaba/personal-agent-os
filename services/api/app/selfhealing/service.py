"""Self-healing records service: incident ingest (fingerprint dedup) and
release CRUD, plus the shared manifest-digest formula.

Fingerprint formula (stable across supervisor restarts and service versions):

    fingerprint = sha256("<component>\\n<error_class>\\n<failing_check>")

The supervisor ships only the MATERIAL; this service is the single place that
hashes it, so dedup can never diverge between producers.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.selfhealing.errors import SelfHealingError, SelfHealingErrorClass
from app.selfhealing.models import INCIDENT_STATUSES, RELEASE_STATUSES, Incident, Release
from app.selfhealing.monitoring import IncidentDraft

logger = get_logger("app.selfhealing.service")

SessionFactory = Callable[[], AbstractContextManager[Session]]

_DIGEST_EXCLUDED_DIRS = {"__pycache__"}
_DIGEST_EXCLUDED_FILES = {"manifest.json"}
_DIGEST_EXCLUDED_SUFFIXES = {".pyc"}


def compute_fingerprint(component: str, error_class: str, failing_check: str) -> str:
    material = f"{component}\n{error_class}\n{failing_check}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def compute_manifest_digest(release_dir: Path | str) -> str:
    """sha256 over sorted ``<relpath>\\0<sha256(file)>\\n`` lines.

    IDENTICAL formula to recovery_supervisor.workspace.manifest_digest (the
    supervisor is stdlib-only and cannot import this package, so the formula is
    duplicated by contract; the M6 E2E asserts both sides agree).
    """
    release_dir = Path(release_dir)
    entries: list[tuple[str, Path]] = []
    for path in release_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(release_dir)
        if any(part in _DIGEST_EXCLUDED_DIRS for part in rel.parts):
            continue
        if rel.name in _DIGEST_EXCLUDED_FILES or path.suffix in _DIGEST_EXCLUDED_SUFFIXES:
            continue
        entries.append((rel.as_posix(), path))
    outer = hashlib.sha256()
    for rel_posix, path in sorted(entries):
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        outer.update(f"{rel_posix}\0{file_hash}\n".encode())
    return outer.hexdigest()


def build_manifest(release_dir: Path | str, version: str) -> dict[str, Any]:
    release_dir = Path(release_dir)
    files = []
    for path in sorted(release_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(release_dir)
        if any(part in _DIGEST_EXCLUDED_DIRS for part in rel.parts):
            continue
        if rel.name in _DIGEST_EXCLUDED_FILES or path.suffix in _DIGEST_EXCLUDED_SUFFIXES:
            continue
        files.append(
            {
                "path": rel.as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        )
    return {"version": version, "digest": compute_manifest_digest(release_dir), "files": files}


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class IngestResult:
    incident_id: uuid.UUID
    fingerprint: str
    occurrence_count: int
    created: bool
    status: str


class SelfHealingService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    # -------------------------------------------------------------- incidents

    def ingest_incident(
        self, draft: IncidentDraft, *, trace_id: str | None = None
    ) -> IngestResult:
        fingerprint = compute_fingerprint(
            draft.component, draft.error_class, draft.failing_check
        )
        now = _utcnow()
        with self._session_factory() as session:
            introduced_id = self._get_or_record_introduced_release(session, draft)
            row = session.execute(
                select(Incident).where(
                    Incident.component == draft.component,
                    Incident.fingerprint == fingerprint,
                )
            ).scalar_one_or_none()
            evidence = dict(draft.evidence)
            evidence["fingerprint_material"] = {
                "component": draft.component,
                "error_class": draft.error_class,
                "failing_check": draft.failing_check,
            }
            for key, value in (
                ("workspace", draft.workspace),
                ("active_version", draft.active_version),
                ("rolled_back_to", draft.rolled_back_to),
                ("detected_at", draft.detected_at),
                ("recovered_at", draft.recovered_at),
            ):
                if value is not None:
                    evidence[key] = value
            if row is None:
                # A supervisor that already rolled back reports a RECOVERED
                # incident (service restored, bug unfixed) — recovery first,
                # coding second.
                status = "recovered" if draft.rolled_back_to else "open"
                row = Incident(
                    component=draft.component,
                    severity=draft.severity,
                    fingerprint=fingerprint,
                    evidence_json=evidence,
                    status=status,
                    introduced_release_id=introduced_id,
                    occurrence_count=1,
                    first_seen_at=now,
                    last_seen_at=now,
                    trace_id=trace_id,
                )
                session.add(row)
                created = True
            else:
                row.occurrence_count += 1
                row.last_seen_at = now
                # Do NOT overwrite the evidence of an incident that is already
                # being (or has been) repaired: the pipeline derives code from
                # this evidence, so a later duplicate report must not be able
                # to swap the counterexample under a running repair
                # (M6 security review). Fresh evidence is only accepted while
                # the incident is still open/recovered.
                if row.status in ("open", "recovered"):
                    row.evidence_json = evidence
                if introduced_id is not None and row.introduced_release_id is None:
                    row.introduced_release_id = introduced_id
                created = False
            session.commit()
            logger.info(
                "incident_ingested",
                incident_id=str(row.id),
                component=draft.component,
                fingerprint=fingerprint,
                occurrence_count=row.occurrence_count,
                created=created,
            )
            return IngestResult(
                incident_id=row.id,
                fingerprint=fingerprint,
                occurrence_count=row.occurrence_count,
                created=created,
                status=row.status,
            )

    def _get_or_record_introduced_release(
        self, session: Session, draft: IncidentDraft
    ) -> uuid.UUID | None:
        if not draft.active_version:
            return None
        row = session.execute(
            select(Release).where(
                Release.component == draft.component,
                Release.version == draft.active_version,
            )
        ).scalar_one_or_none()
        if row is None:
            row = Release(
                component=draft.component,
                version=draft.active_version,
                manifest_digest=draft.active_manifest_digest or "unknown",
                status="rolled_back" if draft.rolled_back_to else "active",
                rolled_back_at=_utcnow() if draft.rolled_back_to else None,
            )
            session.add(row)
            session.flush()
        elif draft.rolled_back_to and row.status not in ("rolled_back", "rejected"):
            row.status = "rolled_back"
            row.rolled_back_at = _utcnow()
        return row.id

    def get_incident(self, incident_id: uuid.UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(Incident, incident_id)
            if row is None:
                raise SelfHealingError(
                    SelfHealingErrorClass.NOT_FOUND, f"incident {incident_id} not found"
                )
            return _incident_dict(row)

    def list_incidents(
        self,
        *,
        component: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = select(Incident).order_by(Incident.last_seen_at.desc()).limit(limit)
        if component:
            query = query.where(Incident.component == component)
        if status:
            query = query.where(Incident.status == status)
        with self._session_factory() as session:
            return [_incident_dict(row) for row in session.execute(query).scalars()]

    def set_incident_status(
        self,
        incident_id: uuid.UUID,
        status: str,
        *,
        fixed_release_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        if status not in INCIDENT_STATUSES:
            raise SelfHealingError(
                SelfHealingErrorClass.VALIDATION_ERROR, f"invalid incident status: {status!r}"
            )
        with self._session_factory() as session:
            row = session.get(Incident, incident_id)
            if row is None:
                raise SelfHealingError(
                    SelfHealingErrorClass.NOT_FOUND, f"incident {incident_id} not found"
                )
            row.status = status
            if fixed_release_id is not None:
                row.fixed_release_id = fixed_release_id
            session.commit()
            logger.info("incident_status_set", incident_id=str(incident_id), status=status)
            return _incident_dict(row)

    # --------------------------------------------------------------- releases

    def record_release(
        self,
        component: str,
        version: str,
        manifest_digest: str,
        *,
        status: str = "candidate",
        git_commit: str | None = None,
        health: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in RELEASE_STATUSES:
            raise SelfHealingError(
                SelfHealingErrorClass.VALIDATION_ERROR, f"invalid release status: {status!r}"
            )
        with self._session_factory() as session:
            existing = session.execute(
                select(Release).where(
                    Release.component == component, Release.version == version
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise SelfHealingError(
                    SelfHealingErrorClass.VALIDATION_ERROR,
                    f"release {component}/{version} already recorded (releases are immutable)",
                )
            row = Release(
                component=component,
                version=version,
                manifest_digest=manifest_digest,
                status=status,
                git_commit=git_commit,
                health_json=health or {},
            )
            session.add(row)
            session.commit()
            logger.info(
                "release_recorded", component=component, version=version, status=status
            )
            return _release_dict(row)

    def get_release(self, component: str, version: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(
                select(Release).where(
                    Release.component == component, Release.version == version
                )
            ).scalar_one_or_none()
            return _release_dict(row) if row is not None else None

    def list_releases(
        self, *, component: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = select(Release).order_by(Release.created_at.desc()).limit(limit)
        if component:
            query = query.where(Release.component == component)
        with self._session_factory() as session:
            return [_release_dict(row) for row in session.execute(query).scalars()]

    def set_release_status(
        self,
        release_id: uuid.UUID,
        status: str,
        *,
        health: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in RELEASE_STATUSES:
            raise SelfHealingError(
                SelfHealingErrorClass.VALIDATION_ERROR, f"invalid release status: {status!r}"
            )
        with self._session_factory() as session:
            row = session.get(Release, release_id)
            if row is None:
                raise SelfHealingError(
                    SelfHealingErrorClass.NOT_FOUND, f"release {release_id} not found"
                )
            row.status = status
            if status == "active":
                row.promoted_at = _utcnow()
            if status in ("rolled_back", "rejected"):
                row.rolled_back_at = _utcnow()
            if health is not None:
                row.health_json = health
            session.commit()
            logger.info("release_status_set", release_id=str(release_id), status=status)
            return _release_dict(row)

    def supersede_active_releases(
        self, component: str, *, except_release_id: uuid.UUID
    ) -> int:
        """Mark previously-active releases of the component as superseded."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(Release).where(
                        Release.component == component,
                        Release.status == "active",
                        Release.id != except_release_id,
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status = "superseded"
            session.commit()
            return len(rows)


def _release_dict(row: Release) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "component": row.component,
        "version": row.version,
        "git_commit": row.git_commit,
        "manifest_digest": row.manifest_digest,
        "status": row.status,
        "health": row.health_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None,
        "rolled_back_at": row.rolled_back_at.isoformat() if row.rolled_back_at else None,
    }


def _incident_dict(row: Incident) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "component": row.component,
        "severity": row.severity,
        "fingerprint": row.fingerprint,
        "evidence": row.evidence_json,
        "status": row.status,
        "introduced_release_id": (
            str(row.introduced_release_id) if row.introduced_release_id else None
        ),
        "fixed_release_id": str(row.fixed_release_id) if row.fixed_release_id else None,
        "occurrence_count": row.occurrence_count,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "trace_id": row.trace_id,
    }


__all__ = [
    "IngestResult",
    "SelfHealingService",
    "build_manifest",
    "compute_fingerprint",
    "compute_manifest_digest",
]
