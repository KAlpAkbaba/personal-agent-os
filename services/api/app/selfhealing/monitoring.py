"""Monitoring evaluation: turn check results / supervisor reports into
incident drafts (pure functions, no I/O).

Two producers feed the incident store:
- the Recovery Supervisor posts (or outbox-drains) a full incident report
  after it already restored service (`draft_from_supervisor_report`);
- in-process monitors can evaluate raw health/selftest check results directly
  (`evaluate_checks`).

Both converge on the same `IncidentDraft`, whose (component, error_class,
failing_check) triple is exactly the fingerprint material the service hashes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.selfhealing.errors import SelfHealingError, SelfHealingErrorClass

SUPPORTED_SCHEMAS = ("pagentos.selfhealing.incident.v1",)

_MAX_COMPONENT_LEN = 128
_MAX_CLASS_LEN = 64
_MAX_EVIDENCE_BYTES = 64 * 1024


@dataclass(slots=True)
class CheckResult:
    name: str  # "health" | "selftest" | ...
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IncidentDraft:
    component: str
    error_class: str
    failing_check: str
    severity: str = "critical"
    evidence: dict[str, Any] = field(default_factory=dict)
    active_version: str | None = None
    active_manifest_digest: str | None = None
    rolled_back_to: str | None = None
    workspace: str | None = None
    detected_at: str | None = None
    recovered_at: str | None = None


def evaluate_checks(
    component: str, checks: list[CheckResult], *, active_version: str | None = None
) -> IncidentDraft | None:
    """All checks ok -> None; otherwise a draft for the FIRST failing check."""
    for check in checks:
        if check.ok:
            continue
        error_class = check.detail.get("error_class")
        if not isinstance(error_class, str) or not error_class:
            error_class = f"{check.name}_check_failed"
        return IncidentDraft(
            component=component,
            error_class=error_class[:_MAX_CLASS_LEN],
            failing_check=check.name,
            evidence={check.name: check.detail},
            active_version=active_version,
        )
    return None


def draft_from_supervisor_report(report: dict[str, Any]) -> IncidentDraft:
    """Validate + normalize a supervisor incident report into a draft."""
    schema = report.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        raise SelfHealingError(
            SelfHealingErrorClass.VALIDATION_ERROR,
            f"unsupported incident schema: {schema!r}",
        )
    material = report.get("fingerprint_material")
    if not isinstance(material, dict):
        raise SelfHealingError(
            SelfHealingErrorClass.VALIDATION_ERROR, "fingerprint_material missing"
        )
    component = _required_str(material, "component", _MAX_COMPONENT_LEN)
    if report.get("component") not in (None, component):
        raise SelfHealingError(
            SelfHealingErrorClass.VALIDATION_ERROR,
            "component mismatch between report and fingerprint_material",
        )
    error_class = _required_str(material, "error_class", _MAX_CLASS_LEN)
    failing_check = _required_str(material, "failing_check", _MAX_CLASS_LEN)
    severity = report.get("severity", "critical")
    if severity not in ("info", "warning", "critical"):
        raise SelfHealingError(
            SelfHealingErrorClass.VALIDATION_ERROR, f"invalid severity: {severity!r}"
        )
    evidence = report.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    if len(repr(evidence).encode("utf-8", errors="replace")) > _MAX_EVIDENCE_BYTES:
        raise SelfHealingError(
            SelfHealingErrorClass.VALIDATION_ERROR, "evidence payload too large"
        )
    return IncidentDraft(
        component=component,
        error_class=error_class,
        failing_check=failing_check,
        severity=severity,
        evidence=evidence,
        active_version=_optional_str(report, "active_version", 64),
        active_manifest_digest=_optional_str(report, "active_manifest_digest", 128),
        rolled_back_to=_optional_str(report, "rolled_back_to", 64),
        workspace=_optional_str(report, "workspace", 1024),
        detected_at=_optional_str(report, "detected_at", 64),
        recovered_at=_optional_str(report, "recovered_at", 64),
    )


def _required_str(payload: dict[str, Any], key: str, max_len: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise SelfHealingError(
            SelfHealingErrorClass.VALIDATION_ERROR, f"invalid or missing field: {key}"
        )
    return value


def _optional_str(payload: dict[str, Any], key: str, max_len: int) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_len:
        raise SelfHealingError(
            SelfHealingErrorClass.VALIDATION_ERROR, f"invalid field: {key}"
        )
    return value


__all__ = [
    "CheckResult",
    "IncidentDraft",
    "draft_from_supervisor_report",
    "evaluate_checks",
]
