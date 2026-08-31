"""Generated-skill lifecycle state machine (ACCEPTANCE_TESTS M7
"Generated-skill lifecycle", ADR-0025 delta).

    candidate -> sandbox -> validated -> shadow -> canary -> active
                                                          -> deprecated
                                                          -> rolled_back

The frozen migration `0007_evolution` pins ``skill_versions.status`` to
(draft, built, tested, reviewed, evaluated, registered, rejected, superseded)
via a CHECK constraint and MUST NOT be changed. The lifecycle stage is therefore
carried in ``evaluation_json["lifecycle"]`` (stage + full transition history with
evidence) and MAPPED onto the DB status, so both stay meaningful:

    lifecycle stage   DB status     meaning of the DB status
    ---------------   -----------   --------------------------------------------
    candidate         draft         a version row exists, nothing built yet
    sandbox           built         built inside the isolated workspace
    validated         evaluated     gates ran and passed (evaluation + review)
    shadow            evaluated     validated, now running mirrored against the incumbent
    canary            evaluated     shadow-clean, now serving a bounded sample
    active            registered    dispatchable; registry.resolve() returns it
    deprecated        superseded    replaced by a newer registered version
    rolled_back       superseded    demoted by an explicit rollback
    (any) rejected    rejected      terminal failure before active

``active`` is unreachable on a generator's say-so: ``require_promotion_evidence``
demands INDEPENDENT evidence recorded at every edge — passing evaluation, an
approving independent review, a clean supply-chain scan, a shadow report and a
canary report that is not worse than the incumbent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.evolution.errors import EvolutionError, EvolutionErrorClass

STAGE_CANDIDATE = "candidate"
STAGE_SANDBOX = "sandbox"
STAGE_VALIDATED = "validated"
STAGE_SHADOW = "shadow"
STAGE_CANARY = "canary"
STAGE_ACTIVE = "active"
STAGE_DEPRECATED = "deprecated"
STAGE_ROLLED_BACK = "rolled_back"
STAGE_REJECTED = "rejected"

STAGES = (
    STAGE_CANDIDATE,
    STAGE_SANDBOX,
    STAGE_VALIDATED,
    STAGE_SHADOW,
    STAGE_CANARY,
    STAGE_ACTIVE,
    STAGE_DEPRECATED,
    STAGE_ROLLED_BACK,
    STAGE_REJECTED,
)

# lifecycle stage -> frozen DB status (see the module docstring for the rationale)
STAGE_TO_DB_STATUS = {
    STAGE_CANDIDATE: "draft",
    STAGE_SANDBOX: "built",
    STAGE_VALIDATED: "evaluated",
    STAGE_SHADOW: "evaluated",
    STAGE_CANARY: "evaluated",
    STAGE_ACTIVE: "registered",
    STAGE_DEPRECATED: "superseded",
    STAGE_ROLLED_BACK: "superseded",
    STAGE_REJECTED: "rejected",
}

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STAGE_CANDIDATE: (STAGE_SANDBOX, STAGE_REJECTED),
    STAGE_SANDBOX: (STAGE_VALIDATED, STAGE_REJECTED),
    STAGE_VALIDATED: (STAGE_SHADOW, STAGE_REJECTED),
    STAGE_SHADOW: (STAGE_CANARY, STAGE_REJECTED),
    STAGE_CANARY: (STAGE_ACTIVE, STAGE_REJECTED),
    STAGE_ACTIVE: (STAGE_DEPRECATED, STAGE_ROLLED_BACK),
    STAGE_DEPRECATED: (STAGE_ACTIVE,),  # rollback can restore a deprecated version
    STAGE_ROLLED_BACK: (STAGE_ACTIVE,),
    STAGE_REJECTED: (),
}

# Evidence keys that MUST be present (and truthy) to enter each stage.
REQUIRED_EVIDENCE: dict[str, tuple[str, ...]] = {
    STAGE_SANDBOX: ("workspace", "manifest_digest"),
    STAGE_VALIDATED: ("evaluation_passed", "review_approved", "supply_chain_ok"),
    STAGE_SHADOW: ("samples", "mismatches"),
    STAGE_CANARY: ("samples", "not_worse_than_incumbent"),
    STAGE_ACTIVE: ("registered_by",),
}

# Stages whose evidence must exist in history before `active` is legal.
PROMOTION_PATH = (STAGE_SANDBOX, STAGE_VALIDATED, STAGE_SHADOW, STAGE_CANARY)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def empty_lifecycle() -> dict[str, Any]:
    return {"stage": STAGE_CANDIDATE, "history": []}


def read_lifecycle(evaluation_json: Any) -> dict[str, Any]:
    if isinstance(evaluation_json, dict):
        lifecycle = evaluation_json.get("lifecycle")
        if isinstance(lifecycle, dict) and lifecycle.get("stage") in STAGES:
            history = lifecycle.get("history")
            return {
                "stage": lifecycle["stage"],
                "history": list(history) if isinstance(history, list) else [],
            }
    return empty_lifecycle()


def current_stage(evaluation_json: Any) -> str:
    return read_lifecycle(evaluation_json)["stage"]


def stage_evidence(evaluation_json: Any, stage: str) -> dict[str, Any] | None:
    for entry in read_lifecycle(evaluation_json)["history"]:
        if isinstance(entry, dict) and entry.get("stage") == stage:
            evidence = entry.get("evidence")
            return evidence if isinstance(evidence, dict) else {}
    return None


def advance(
    evaluation_json: Any, stage: str, evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return a NEW evaluation_json with the lifecycle advanced to ``stage``.

    Raises ``lifecycle_violation`` on an illegal transition or missing evidence.
    """
    if stage not in STAGES:
        raise EvolutionError(
            EvolutionErrorClass.LIFECYCLE_VIOLATION, f"unknown lifecycle stage: {stage!r}"
        )
    base = dict(evaluation_json) if isinstance(evaluation_json, dict) else {}
    lifecycle = read_lifecycle(base)
    previous = lifecycle["stage"]
    if stage not in ALLOWED_TRANSITIONS.get(previous, ()):
        raise EvolutionError(
            EvolutionErrorClass.LIFECYCLE_VIOLATION,
            f"illegal lifecycle transition {previous!r} -> {stage!r}",
            details={"allowed": list(ALLOWED_TRANSITIONS.get(previous, ()))},
        )
    evidence = dict(evidence or {})
    # A key is missing only when it is ABSENT: `mismatches: 0` is legitimate
    # evidence. Whether the recorded value is GOOD ENOUGH to promote is decided
    # by require_promotion_evidence, not here.
    missing = [key for key in REQUIRED_EVIDENCE.get(stage, ()) if key not in evidence]
    if missing:
        raise EvolutionError(
            EvolutionErrorClass.LIFECYCLE_VIOLATION,
            f"stage {stage!r} requires evidence: {missing}",
            details={"missing": missing, "required": list(REQUIRED_EVIDENCE.get(stage, ()))},
        )
    base["lifecycle"] = {
        "stage": stage,
        "history": [
            *lifecycle["history"],
            {"stage": stage, "from": previous, "at": _utcnow_iso(), "evidence": evidence},
        ],
    }
    return base


def require_promotion_evidence(evaluation_json: Any) -> dict[str, Any]:
    """`active` is unreachable without independent evidence at EVERY edge."""
    lifecycle = read_lifecycle(evaluation_json)
    if lifecycle["stage"] != STAGE_CANARY:
        raise EvolutionError(
            EvolutionErrorClass.LIFECYCLE_VIOLATION,
            f"promotion to active requires the canary stage; version is {lifecycle['stage']!r}",
            details={"stage": lifecycle["stage"]},
        )
    seen = {entry.get("stage"): entry.get("evidence") or {} for entry in lifecycle["history"]}
    missing_stages = [stage for stage in PROMOTION_PATH if stage not in seen]
    if missing_stages:
        raise EvolutionError(
            EvolutionErrorClass.LIFECYCLE_VIOLATION,
            f"promotion path is incomplete; missing stages: {missing_stages}",
            details={"missing_stages": missing_stages},
        )
    validated = seen[STAGE_VALIDATED]
    if not (
        validated.get("evaluation_passed") is True
        and validated.get("review_approved") is True
        and validated.get("supply_chain_ok") is True
    ):
        raise EvolutionError(
            EvolutionErrorClass.LIFECYCLE_VIOLATION,
            "the validated stage lacks independent evaluation/review/supply-chain evidence",
        )
    shadow = seen[STAGE_SHADOW]
    if int(shadow.get("samples") or 0) <= 0 or int(shadow.get("mismatches", 1)) != 0:
        raise EvolutionError(
            EvolutionErrorClass.LIFECYCLE_VIOLATION,
            "the shadow stage recorded no samples or non-zero mismatches",
            details=dict(shadow),
        )
    canary = seen[STAGE_CANARY]
    if int(canary.get("samples") or 0) <= 0 or canary.get("not_worse_than_incumbent") is not True:
        raise EvolutionError(
            EvolutionErrorClass.LIFECYCLE_VIOLATION,
            "the canary stage did not record a result at least as good as the incumbent",
            details=dict(canary),
        )
    return lifecycle


def db_status_for(stage: str) -> str:
    if stage not in STAGE_TO_DB_STATUS:
        raise EvolutionError(
            EvolutionErrorClass.LIFECYCLE_VIOLATION, f"unknown lifecycle stage: {stage!r}"
        )
    return STAGE_TO_DB_STATUS[stage]


__all__ = [
    "ALLOWED_TRANSITIONS",
    "PROMOTION_PATH",
    "REQUIRED_EVIDENCE",
    "STAGES",
    "STAGE_ACTIVE",
    "STAGE_CANARY",
    "STAGE_CANDIDATE",
    "STAGE_DEPRECATED",
    "STAGE_REJECTED",
    "STAGE_ROLLED_BACK",
    "STAGE_SANDBOX",
    "STAGE_SHADOW",
    "STAGE_TO_DB_STATUS",
    "STAGE_VALIDATED",
    "advance",
    "current_stage",
    "db_status_for",
    "empty_lifecycle",
    "read_lifecycle",
    "require_promotion_evidence",
    "stage_evidence",
]
