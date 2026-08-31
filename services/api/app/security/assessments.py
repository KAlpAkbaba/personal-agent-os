"""In-scope defensive assessment runner (M8).

The acceptance point this module exists to prove (SECURITY_MODEL §8):

    Within a recorded scope, assessments run WITHOUT repeated approvals.

Authorization here is scope-based, not per-command. `run()` asks `scope.py` the
one enforcement question, and if the answer is yes it executes — every time,
with no prompt, no second confirmation and no new authorization record. The
`assessment_authorized` rows it appends are AUDIT, not approval: the count of
owner grant events (`enrolled` / `scope_changed`) does not move no matter how
many assessments run.

The converse is equally structural: a refusal creates no asset, widens no
locator and runs no check. It appends an `assessment_refused` event and stops.
When the refusal was for a target that DID match an enrolled asset (wrong
testing class, expired window, disruption over the constraint) a `refused`
assessment row is recorded too, so the run shows up in the assessment history
rather than only in the event log. When no asset matched at all there is
nothing to attach a row to — and inventing one would be exactly the silent
scope expansion §8 forbids — so only the refusal event is written.

What actually runs is `checks.py`: read-only, offline, deterministic
configuration audit over ONE directory that the owner recorded on the asset in
`constraints.config_roots`. A caller may narrow to a subdirectory of a recorded
root; it can never introduce a new one.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.security import checks as checks_module
from app.security.errors import SecurityError, SecurityErrorClass
from app.security.models import (
    ASSESSMENT_STATUS_COMPLETED,
    ASSESSMENT_STATUS_FAILED,
    ASSESSMENT_STATUS_REFUSED,
    ASSESSMENT_STATUS_RUNNING,
    ASSESSMENT_STATUSES,
    FINDING_STATUS_OPEN,
    FINDING_STATUS_REMEDIATED,
    FINDING_STATUSES,
    SEVERITIES,
    SEVERITY_ORDER,
    TESTING_CLASS_CONFIGURATION_AUDIT,
    AuthorizedAsset,
    SecurityAssessment,
    SecurityFinding,
)
from app.security.redaction import assert_redacted, redact_value
from app.security.registry import AuthorizedAssetRegistry, as_aware
from app.security.scope import ScopeDecision, ScopeGuard

logger = get_logger("app.security.assessments")

SessionFactory = Callable[[], AbstractContextManager[Session]]

COLLECTOR_ID = "configuration_audit/1"
MAX_TARGET_LENGTH = 512
MAX_CONFIG_ROOT_LENGTH = 512


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso8601(value: datetime | None) -> str | None:
    aware = as_aware(value)
    return aware.isoformat() if aware else None


def assessment_to_dict(row: SecurityAssessment) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "asset_id": str(row.asset_id),
        "testing_class": row.testing_class,
        "status": row.status,
        "target": row.target,
        "scope_decision": dict(row.scope_decision_json or {}),
        "result": dict(row.result_json or {}),
        "artifact_id": str(row.artifact_id) if row.artifact_id else None,
        "trace_id": row.trace_id,
        "created_at": iso8601(row.created_at),
        "started_at": iso8601(row.started_at),
        "completed_at": iso8601(row.completed_at),
    }


def finding_to_dict(row: SecurityFinding) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "assessment_id": str(row.assessment_id),
        "asset_id": str(row.asset_id),
        "fingerprint": row.fingerprint,
        "title": row.title,
        "severity": row.severity,
        "status": row.status,
        "evidence": dict(row.evidence_json or {}),
        "remediation": dict(row.remediation_json or {}),
        "created_at": iso8601(row.created_at),
        "resolved_at": iso8601(row.resolved_at),
    }


def authorized_config_root(asset: AuthorizedAsset, requested: str | None) -> Path:
    """Resolve the directory this assessment may read — registry-driven.

    The collector root is NEVER simply what the caller passed. It must be a
    root the owner recorded on the asset, or a directory inside one. This is
    the filesystem half of "no silent scope expansion": an authorized asset
    does not authorize reading arbitrary paths on the machine.
    """
    constraints = dict(asset.constraints_json or {})
    recorded = [str(r) for r in (constraints.get("config_roots") or []) if str(r).strip()]
    if not recorded:
        raise SecurityError(
            SecurityErrorClass.VALIDATION_ERROR,
            "asset records no constraints.config_roots; configuration_audit has nothing "
            "it is authorized to read",
            {"asset_ref": asset.asset_ref},
        )
    roots = [Path(r).expanduser().resolve() for r in recorded]

    if requested is None:
        if len(roots) != 1:
            raise SecurityError(
                SecurityErrorClass.VALIDATION_ERROR,
                "asset records several config_roots; name the one to assess",
                {"config_roots": [str(r) for r in roots]},
            )
        return roots[0]

    if len(requested) > MAX_CONFIG_ROOT_LENGTH:
        raise SecurityError(
            SecurityErrorClass.VALIDATION_ERROR, "config_root is too long", {}
        )
    candidate = Path(requested).expanduser().resolve()
    for root in roots:
        if candidate == root or root in candidate.parents:
            return candidate
    raise SecurityError(
        SecurityErrorClass.COLLECTOR_ROOT_VIOLATION,
        "requested config_root is outside every root recorded for this asset",
        {"requested": str(candidate), "authorized_roots": [str(r) for r in roots]},
    )


class AssessmentService:
    """Runs, records and reads defensive assessments and their findings."""

    def __init__(
        self,
        session_factory: SessionFactory,
        registry: AuthorizedAssetRegistry,
        guard: ScopeGuard,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._guard = guard

    # ------------------------------------------------------------------ run

    def run(
        self,
        *,
        target: str,
        testing_class: str = TESTING_CLASS_CONFIGURATION_AUDIT,
        config_root: str | None = None,
        disruption: str = "none",
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Scope-check, then execute. No approval prompt on the happy path."""
        requested_target = (target or "").strip()[:MAX_TARGET_LENGTH]
        with self._session_factory() as session:
            decision = self._guard.evaluate(
                session,
                target=requested_target,
                testing_class=testing_class,
                disruption=disruption,
                purpose="assessment",
                trace_id=trace_id,
            )
            if not decision.allowed:
                self._record_refusal(session, decision, trace_id=trace_id)
                raise SecurityError(
                    SecurityErrorClass.OUT_OF_SCOPE,
                    decision.message,
                    {"decision": decision.to_dict()},
                )

            asset = session.get(AuthorizedAsset, decision.asset_id)
            if asset is None:  # pragma: no cover - decision implies existence
                raise SecurityError(
                    SecurityErrorClass.INTERNAL_BUG, "authorized asset vanished mid-decision"
                )

            # Root resolution can refuse; do it BEFORE creating a run row so a
            # misconfigured request does not leave a half-started assessment.
            root = authorized_config_root(asset, config_root)

            assessment = SecurityAssessment(
                asset_id=asset.id,
                testing_class=testing_class,
                status=ASSESSMENT_STATUS_RUNNING,
                target=decision.target,
                scope_decision_json=decision.to_dict(),
                result_json={},
                trace_id=trace_id,
                started_at=utcnow(),
            )
            session.add(assessment)
            session.commit()

            try:
                summary, raw_findings = self._collect(root)
            except SecurityError as exc:
                assessment.status = ASSESSMENT_STATUS_FAILED
                assessment.completed_at = utcnow()
                assessment.result_json = {"error": exc.to_dict()}
                session.commit()
                raise

            self._persist_findings(session, assessment, asset, raw_findings)
            assessment.status = ASSESSMENT_STATUS_COMPLETED
            assessment.completed_at = utcnow()
            assessment.result_json = assert_redacted(
                redact_value(summary), where="assessment.result"
            )
            session.commit()

            logger.info(
                "security_assessment_completed",
                assessment_id=str(assessment.id),
                asset_ref=asset.asset_ref,
                findings=summary["findings_total"],
            )
            payload = assessment_to_dict(assessment)
            payload["findings"] = [
                finding_to_dict(f)
                for f in self._findings_for_assessment(session, assessment.id)
            ]
            return payload

    def _collect(self, root: Path) -> tuple[dict[str, Any], list[checks_module.RawFinding]]:
        if not root.exists() or not root.is_dir():
            raise SecurityError(
                SecurityErrorClass.TARGET_UNAVAILABLE,
                "the authorized config root does not exist on this machine",
                {"config_root": str(root)},
            )
        files = checks_module.collect_files(root)
        findings = checks_module.run_checks(files)
        by_severity = {severity: 0 for severity in SEVERITIES}
        for finding in findings:
            by_severity[finding.severity] += 1
        summary = {
            "collector": COLLECTOR_ID,
            "config_root": str(root),
            "files_scanned": len(files),
            "files": [f.relative_path for f in files],
            "checks_run": len(checks_module.CHECKS),
            "findings_total": len(findings),
            "by_severity": by_severity,
            # Deterministic: the same tree always yields this same list.
            "check_ids": sorted({f.check_id for f in findings}),
        }
        return summary, findings

    def _persist_findings(
        self,
        session: Session,
        assessment: SecurityAssessment,
        asset: AuthorizedAsset,
        raw_findings: list[checks_module.RawFinding],
    ) -> None:
        """Upsert on (asset_id, fingerprint).

        A repeated in-scope run therefore refreshes the same findings instead
        of duplicating them, and a previously remediated issue that reappears
        is REOPENED rather than silently staying "remediated".
        """
        for raw in raw_findings:
            evidence = assert_redacted(redact_value(raw.evidence), where="finding.evidence")
            remediation = assert_redacted(
                redact_value(raw.remediation), where="finding.remediation"
            )
            existing = session.execute(
                select(SecurityFinding).where(
                    SecurityFinding.asset_id == asset.id,
                    SecurityFinding.fingerprint == raw.fingerprint,
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.assessment_id = assessment.id
                existing.title = raw.title
                existing.severity = raw.severity
                existing.evidence_json = evidence
                existing.remediation_json = remediation
                if existing.status == FINDING_STATUS_REMEDIATED:
                    existing.status = FINDING_STATUS_OPEN
                    existing.resolved_at = None
                continue
            session.add(
                SecurityFinding(
                    assessment_id=assessment.id,
                    asset_id=asset.id,
                    fingerprint=raw.fingerprint,
                    title=raw.title,
                    severity=raw.severity,
                    status=FINDING_STATUS_OPEN,
                    evidence_json=evidence,
                    remediation_json=remediation,
                )
            )
        session.commit()

    def _record_refusal(
        self, session: Session, decision: ScopeDecision, *, trace_id: str | None
    ) -> None:
        """Only when an enrolled asset matched. Never creates an asset."""
        if decision.asset_id is None:
            return
        session.add(
            SecurityAssessment(
                asset_id=decision.asset_id,
                testing_class=decision.testing_class,
                status=ASSESSMENT_STATUS_REFUSED,
                target=decision.target,
                scope_decision_json=decision.to_dict(),
                result_json={"refused": True, "reason": decision.reason},
                trace_id=trace_id,
                completed_at=utcnow(),
            )
        )
        session.commit()

    # -------------------------------------------------------------- reading

    def get(self, assessment_id: uuid.UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SecurityAssessment, assessment_id)
            if row is None:
                raise SecurityError(
                    SecurityErrorClass.NOT_FOUND,
                    f"assessment {assessment_id} not found",
                    {"assessment_id": str(assessment_id)},
                )
            payload = assessment_to_dict(row)
            payload["findings"] = [
                finding_to_dict(f) for f in self._findings_for_assessment(session, row.id)
            ]
            return payload

    def list(
        self,
        *,
        asset_ref: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(SecurityAssessment)
            if asset_ref:
                asset = session.execute(
                    select(AuthorizedAsset).where(
                        AuthorizedAsset.asset_ref == asset_ref.strip().lower()
                    )
                ).scalar_one_or_none()
                if asset is None:
                    return []
                stmt = stmt.where(SecurityAssessment.asset_id == asset.id)
            if status:
                if status not in ASSESSMENT_STATUSES:
                    raise SecurityError(
                        SecurityErrorClass.VALIDATION_ERROR,
                        f"status must be one of {ASSESSMENT_STATUSES}",
                    )
                stmt = stmt.where(SecurityAssessment.status == status)
            stmt = stmt.order_by(SecurityAssessment.created_at.desc()).limit(limit)
            return [assessment_to_dict(r) for r in session.execute(stmt).scalars()]

    def _findings_for_assessment(
        self, session: Session, assessment_id: uuid.UUID
    ) -> list[SecurityFinding]:
        rows = list(
            session.execute(
                select(SecurityFinding).where(SecurityFinding.assessment_id == assessment_id)
            ).scalars()
        )
        rows.sort(key=lambda r: (-SEVERITY_ORDER.get(r.severity, 0), r.fingerprint))
        return rows

    def list_findings(
        self,
        *,
        asset_ref: str | None = None,
        assessment_id: uuid.UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(SecurityFinding)
            if asset_ref:
                asset = session.execute(
                    select(AuthorizedAsset).where(
                        AuthorizedAsset.asset_ref == asset_ref.strip().lower()
                    )
                ).scalar_one_or_none()
                if asset is None:
                    return []
                stmt = stmt.where(SecurityFinding.asset_id == asset.id)
            if assessment_id is not None:
                stmt = stmt.where(SecurityFinding.assessment_id == assessment_id)
            if severity:
                if severity not in SEVERITIES:
                    raise SecurityError(
                        SecurityErrorClass.VALIDATION_ERROR,
                        f"severity must be one of {SEVERITIES}",
                    )
                stmt = stmt.where(SecurityFinding.severity == severity)
            if status:
                if status not in FINDING_STATUSES:
                    raise SecurityError(
                        SecurityErrorClass.VALIDATION_ERROR,
                        f"status must be one of {FINDING_STATUSES}",
                    )
                stmt = stmt.where(SecurityFinding.status == status)
            rows = list(session.execute(stmt.limit(limit)).scalars())
            rows.sort(key=lambda r: (-SEVERITY_ORDER.get(r.severity, 0), r.fingerprint))
            return [finding_to_dict(r) for r in rows]

    def get_finding(self, finding_id: uuid.UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SecurityFinding, finding_id)
            if row is None:
                raise SecurityError(
                    SecurityErrorClass.NOT_FOUND,
                    f"finding {finding_id} not found",
                    {"finding_id": str(finding_id)},
                )
            return finding_to_dict(row)


__all__ = [
    "COLLECTOR_ID",
    "AssessmentService",
    "assessment_to_dict",
    "authorized_config_root",
    "finding_to_dict",
]
