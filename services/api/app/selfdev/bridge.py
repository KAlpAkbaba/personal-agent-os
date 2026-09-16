"""The opportunity -> defect bridge (B35 req 583, 585).

Fourteen opportunities waited in the evolution backlog with nothing to carry them to the
engine: the supervisor derived a promotion class for each and nobody consumed it. This
module turns one opportunity row (the dict ``opportunity_dict`` produces) into the
defect the engine takes, and carries the promotion class WITH it - so what the owner
sees on the queue row is what the supervisor derived, and what the run ends in
(``awaiting_owner`` for every class; NEVER_AUTO_PROMOTE says so on the row) is decided
by that class and never by the engine's own opinion of itself.

Pure: a dict in, a dict out, no session.
"""

from __future__ import annotations

from typing import Any

from app.evolution.supervisor import PROMOTION_CLASSES, promotion_class_for
from app.selfdev.models import DEFECT_KIND_BUG, DEFECT_KIND_FEATURE

#: When an opportunity names no paths, the candidate may touch the API application and
#: its tests - the scope the engine's context builder already knows how to show.
DEFAULT_SCOPE: tuple[str, ...] = ("services/api/app/", "services/api/tests/")
MAX_TITLE = 200
MAX_EVIDENCE = 8000

_INCIDENT_SOURCES = ("incident", "release_failure", "ledger", "selftest", "ci")


def _paths_of(detail: dict[str, Any]) -> tuple[str, ...]:
    for key in ("paths", "scope", "changed_paths", "footprint"):
        value = detail.get(key)
        if isinstance(value, list | tuple):
            paths = tuple(str(p) for p in value if isinstance(p, str) and p.strip())
            if paths:
                return paths
    return ()


def kind_of(opportunity: dict[str, Any]) -> str:
    """An opportunity born from an incident or a failure is a bug; one born from a
    capability gap or an idea is a feature. The word decides what the model is asked."""
    source = str(opportunity.get("source") or "").lower()
    origins = " ".join(
        str(o.get("kind", "")) if isinstance(o, dict) else str(o)
        for o in (opportunity.get("origin") or [])
    ).lower()
    detail = opportunity.get("detail") or {}
    if str(detail.get("kind") or "") in (DEFECT_KIND_BUG, DEFECT_KIND_FEATURE):
        return str(detail["kind"])
    if any(word in source or word in origins for word in _INCIDENT_SOURCES):
        return DEFECT_KIND_BUG
    return DEFECT_KIND_FEATURE


def defect_from_opportunity(opportunity: dict[str, Any]) -> dict[str, Any]:
    """The engine's ``DefectSpec`` fields plus what the queue row needs (kind, source,
    opportunity_id, promotion_class). The promotion class is the supervisor's when the
    row carries one and is DERIVED from the paths otherwise - never defaulted to the
    lowest class."""
    opportunity_id = str(opportunity.get("opportunity_id") or "")
    if not opportunity_id:
        raise ValueError("an opportunity without an id cannot become a defect")
    detail = dict(opportunity.get("detail") or {})
    paths = _paths_of(detail) or DEFAULT_SCOPE
    promotion_class = opportunity.get("promotion_class") or detail.get("promotion_class")
    if promotion_class not in PROMOTION_CLASSES:
        promotion_class, _tier, _reasons = promotion_class_for(paths)
    evidence_lines = [str(opportunity.get("statement") or "").strip()]
    for ref in opportunity.get("origin") or []:
        if isinstance(ref, dict):
            evidence_lines.append(
                "origin: " + ", ".join(f"{k}={v}" for k, v in sorted(ref.items()) if v)
            )
    failing_test = detail.get("failing_test")
    return {
        "defect_id": f"opp-{opportunity_id[:8]}",
        "title": str(opportunity.get("title") or opportunity_id)[:MAX_TITLE],
        "evidence": "\n".join(line for line in evidence_lines if line)[:MAX_EVIDENCE],
        "scope": list(paths),
        "failing_test": str(failing_test) if isinstance(failing_test, str) else None,
        "kind": kind_of(opportunity),
        "opportunity_id": opportunity_id,
        "promotion_class": str(promotion_class),
        "priority": _priority_of(detail),
    }


def _priority_of(detail: dict[str, Any]) -> int:
    raw = str(detail.get("priority") or "P2").upper()
    return int(raw[1]) if len(raw) == 2 and raw[0] == "P" and raw[1].isdigit() else 2
