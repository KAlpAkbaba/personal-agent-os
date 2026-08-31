"""Gap detection decision tree (EVOLUTION_ENGINE_SPEC §3) + its audit trail.

For every unmet request the engine walks a fixed, ordered tree and records ONE
trail entry per question, in order, into ``capability_gaps.decision_trail_json``:

    0. request_received     the normalized request (spec + resume payload)
    1. existing_capability   can an already-registered capability solve it?
    2. composition           can registered capabilities be CHAINED to solve it?
    3. configuration         can an existing skill be configured to solve it?
    4. extension             can an existing skill be safely extended?
    5. new_skill             is a new skill/module required?
    6. product_core_change   does the requirement imply a product/core change?

"Composition was attempted first" is therefore *evidence*, not a claim: the
composer really runs a forward-chaining search over the registered capabilities'
declared inputs/outputs and reports the plan it found or the outputs it could
not produce. ``require_composition_attempted`` is a hard precondition of code
generation — the pipeline calls it before the generator is even constructed, so
a trail without an attempted composition step can never reach a generator.

Step 6 is a STOP, never a work order: a requirement that implies a core,
recovery, schema, API or UI change resolves to ``product_change_required`` and
the engine refuses. Per constitution §6 / EVOLUTION_ENGINE_SPEC §12 a new
recovery version may be *developed*, but that is an owner-gated, two-phase
activation path — not something this pipeline may start on its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.models import GAP_RESOLUTIONS, GAP_STATUSES, CapabilityGap
from app.evolution.registry import CapabilityRegistry
from app.evolution.skills import SkillSpec
from app.evolution.tokens import require_capability_id, require_slug_list
from app.logging import get_logger

logger = get_logger("app.evolution.gaps")

SessionFactory = Callable[[], AbstractContextManager[Session]]

STEP_REQUEST = "request_received"
STEP_EXISTING = "existing_capability"
STEP_COMPOSITION = "composition"
STEP_CONFIGURATION = "configuration"
STEP_EXTENSION = "extension"
STEP_NEW_SKILL = "new_skill"
STEP_PRODUCT_CHANGE = "product_core_change"

DECISION_STEPS = (
    STEP_EXISTING,
    STEP_COMPOSITION,
    STEP_CONFIGURATION,
    STEP_EXTENSION,
    STEP_NEW_SKILL,
    STEP_PRODUCT_CHANGE,
)

# Constitution §6 recovery root + the product's own core. A gap that would
# require touching any of these is refused, never auto-generated.
PROTECTED_COMPONENTS = frozenset(
    {
        "recovery-supervisor",
        "recovery_supervisor",
        "owner-identity-root",
        "secret-root",
        "update-signature-verification",
        "last-known-good-pointer",
        "backup-restore",
        "api-core",
        "database-schema",
    }
)
PROTECTED_CAPABILITY_PREFIXES = (
    "core.",
    "recovery.",
    "security.identity",
    "security.secrets",
    "update.signing",
    "backup.",
    "platform.schema",
)
# Change kinds that are product changes by definition (§11).
PRODUCT_CHANGE_KINDS = frozenset({"schema", "api", "ui", "core", "recovery", "migration"})

MAX_REQUEST_TEXT = 4000
MAX_IO_NAMES = 16


# ------------------------------------------------------------------- request


@dataclass(slots=True)
class CapabilityRequest:
    """A normalized unmet request. ``parse`` is the choke point for the fields
    that can reach generated source (capability id, io names, skill spec)."""

    requested_capability: str
    request_text: str
    required_inputs: list[str] = field(default_factory=list)
    required_outputs: list[str] = field(default_factory=list)
    task_id: uuid.UUID | None = None
    target_component: str | None = None
    change_kind: str | None = None
    spec: dict[str, Any] | None = None
    resume_payload: dict[str, Any] | None = None

    @classmethod
    def parse(cls, raw: Any) -> CapabilityRequest:
        if not isinstance(raw, dict):
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, "capability request must be an object"
            )
        text = raw.get("request_text") or ""
        if not isinstance(text, str) or not text.strip():
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, "request_text is required"
            )
        if len(text) > MAX_REQUEST_TEXT:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"request_text is too long (max {MAX_REQUEST_TEXT} chars)",
            )
        task_id = raw.get("task_id")
        if isinstance(task_id, str):
            try:
                task_id = uuid.UUID(task_id)
            except ValueError as exc:
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR, "task_id is not a uuid"
                ) from exc
        target = raw.get("target_component")
        if target is not None and (not isinstance(target, str) or len(target) > 128):
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, "target_component must be a short string"
            )
        change_kind = raw.get("change_kind")
        if change_kind is not None and (
            not isinstance(change_kind, str) or len(change_kind) > 32
        ):
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, "change_kind must be a short string"
            )
        spec = raw.get("spec")
        if spec is not None:
            if not isinstance(spec, dict):
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR, "spec must be an object"
                )
            # CHOKE POINT (ADR-0025 §3): the skill spec is validated the moment
            # it enters the system, not when the generator is about to use it —
            # a hostile spec never even reaches the capability_gaps table. The
            # pipeline re-parses it later as defense in depth.
            requested = raw.get("requested_capability")
            if spec.get("capability_id", requested) != requested:
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR,
                    "spec.capability_id does not match requested_capability",
                )
            SkillSpec.parse({**spec, "capability_id": requested})
        resume_payload = raw.get("resume_payload")
        if resume_payload is not None and not isinstance(resume_payload, dict):
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, "resume_payload must be an object"
            )
        return cls(
            requested_capability=require_capability_id(
                raw.get("requested_capability"), field="requested_capability"
            ),
            request_text=text,
            required_inputs=require_slug_list(
                raw.get("required_inputs") or [], field="required_inputs",
                max_items=MAX_IO_NAMES,
            ),
            required_outputs=require_slug_list(
                raw.get("required_outputs") or [], field="required_outputs",
                max_items=MAX_IO_NAMES,
            ),
            task_id=task_id,
            target_component=target,
            change_kind=change_kind,
            spec=spec,
            resume_payload=resume_payload,
        )

    def to_evidence(self) -> dict[str, Any]:
        return {
            "requested_capability": self.requested_capability,
            "request_text": self.request_text,
            "required_inputs": list(self.required_inputs),
            "required_outputs": list(self.required_outputs),
            "task_id": str(self.task_id) if self.task_id else None,
            "target_component": self.target_component,
            "change_kind": self.change_kind,
            "spec": self.spec,
            "resume_payload": self.resume_payload,
        }


# ------------------------------------------------------------------ composer


@dataclass(slots=True)
class CompositionAttempt:
    attempted: bool
    satisfied: bool
    plan: list[str] = field(default_factory=list)
    considered: list[dict[str, Any]] = field(default_factory=list)
    produced: list[str] = field(default_factory=list)
    missing_outputs: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "satisfied": self.satisfied,
            "plan": list(self.plan),
            "considered": list(self.considered),
            "produced": list(self.produced),
            "missing_outputs": list(self.missing_outputs),
            "reason": self.reason,
        }


class CapabilityComposer:
    """Tries to satisfy a request by CHAINING already-registered capabilities.

    Forward chaining over declared io: starting from the request's available
    inputs, any dispatchable capability whose declared inputs are already
    available contributes its outputs; repeat until a fixpoint. The request is
    satisfiable by composition when every required output has been produced by
    at least one capability in the plan.

    Only capabilities that ``registry.resolve`` returns are considered — a
    proposed, deprecated or rejected-version capability is not a building block.
    """

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def attempt(self, request: CapabilityRequest) -> CompositionAttempt:
        goal = set(request.required_outputs)
        available = set(request.required_inputs)
        catalog: list[dict[str, Any]] = []
        for row in self.registry.list_capabilities(status="production", limit=500):
            resolved = self.registry.resolve(row["capability_id"])
            if resolved is None:
                continue
            manifest = resolved.get("manifest") or {}
            catalog.append(
                {
                    "capability_id": resolved["capability_id"],
                    "version": resolved["version"],
                    "inputs": list(manifest.get("inputs") or []),
                    "outputs": list(manifest.get("outputs") or []),
                }
            )
        catalog.sort(key=lambda entry: entry["capability_id"])

        if not goal:
            return CompositionAttempt(
                attempted=True,
                satisfied=False,
                considered=catalog,
                produced=sorted(available),
                missing_outputs=[],
                reason=(
                    "the request declares no required outputs, so no composition can be "
                    "verified against the registry"
                ),
            )
        if not catalog:
            return CompositionAttempt(
                attempted=True,
                satisfied=False,
                considered=[],
                produced=sorted(available),
                missing_outputs=sorted(goal - available),
                reason="no dispatchable capabilities are registered to compose from",
            )

        plan: list[str] = []
        progressed = True
        while progressed and not goal.issubset(available):
            progressed = False
            for entry in catalog:
                if entry["capability_id"] in plan:
                    continue
                inputs = set(entry["inputs"])
                outputs = set(entry["outputs"])
                if inputs.issubset(available) and not outputs.issubset(available):
                    plan.append(entry["capability_id"])
                    available |= outputs
                    progressed = True
        missing = sorted(goal - available)
        satisfied = not missing and bool(plan)
        reason = (
            f"composed {len(plan)} registered capabilities covering every required output"
            if satisfied
            else (
                "registered capabilities cannot produce "
                f"{missing or 'the request'} from the available inputs"
            )
        )
        return CompositionAttempt(
            attempted=True,
            satisfied=satisfied,
            plan=plan,
            considered=catalog,
            produced=sorted(available),
            missing_outputs=missing,
            reason=reason,
        )


# ------------------------------------------------------------- decision tree


@dataclass(slots=True)
class GapDecision:
    requested_capability: str
    resolution: str
    trail: list[dict[str, Any]] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)
    refusal_reason: str | None = None

    @property
    def generation_required(self) -> bool:
        return self.resolution == "generation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_capability": self.requested_capability,
            "resolution": self.resolution,
            "trail": list(self.trail),
            "plan": list(self.plan),
            "refusal_reason": self.refusal_reason,
        }


def _step(
    index: int, step: str, question: str, outcome: str, evidence: dict[str, Any]
) -> dict[str, Any]:
    return {
        "index": index,
        "step": step,
        "question": question,
        "outcome": outcome,
        "evidence": evidence,
        "at": datetime.now(UTC).isoformat(),
    }


def implies_product_change(request: CapabilityRequest) -> tuple[bool, dict[str, Any]]:
    """Fail-SAFE core/recovery detector.

    Every signal here can only push the answer towards *refuse*, so a hostile or
    confused request can never talk the engine into generating code — at worst
    it talks it into refusing. Explicit structured signals
    (``target_component``/``change_kind``) come first; the request text is
    scanned last and only for protected-component markers.
    """
    reasons: list[str] = []
    if request.target_component and request.target_component.lower() in PROTECTED_COMPONENTS:
        reasons.append(f"target_component={request.target_component}")
    if request.change_kind and request.change_kind.lower() in PRODUCT_CHANGE_KINDS:
        reasons.append(f"change_kind={request.change_kind}")
    for prefix in PROTECTED_CAPABILITY_PREFIXES:
        if request.requested_capability.startswith(prefix):
            reasons.append(f"capability_prefix={prefix}")
    lowered = request.request_text.lower()
    for marker in PROTECTED_COMPONENTS:
        if marker in lowered:
            reasons.append(f"request_text_mentions={marker}")
    return (bool(reasons), {"reasons": sorted(set(reasons))})


class GapDetector:
    """Walks §3 in order and produces an auditable decision trail."""

    def __init__(
        self, registry: CapabilityRegistry, composer: CapabilityComposer | None = None
    ) -> None:
        self.registry = registry
        self.composer = composer or CapabilityComposer(registry)

    def detect(self, request: CapabilityRequest) -> GapDecision:
        trail: list[dict[str, Any]] = [
            _step(0, STEP_REQUEST, "what was requested?", "recorded", request.to_evidence())
        ]

        # 1. Existing capability.
        existing = self.registry.resolve(request.requested_capability)
        if existing is not None:
            trail.append(
                _step(
                    1,
                    STEP_EXISTING,
                    "can an existing capability solve it?",
                    "satisfied",
                    {
                        "capability_id": existing["capability_id"],
                        "version": existing["version"],
                        "skill_version_id": existing["current_skill_version_id"],
                    },
                )
            )
            return GapDecision(
                requested_capability=request.requested_capability,
                resolution="existing_capability",
                trail=trail,
            )
        trail.append(
            _step(
                1,
                STEP_EXISTING,
                "can an existing capability solve it?",
                "insufficient",
                {"reason": "no production capability with a registered skill version"},
            )
        )

        # 2. Composition — ALWAYS attempted before anything may be generated.
        attempt = self.composer.attempt(request)
        trail.append(
            _step(
                2,
                STEP_COMPOSITION,
                "can existing capabilities be composed?",
                "satisfied" if attempt.satisfied else "insufficient",
                attempt.to_dict(),
            )
        )
        if attempt.satisfied:
            return GapDecision(
                requested_capability=request.requested_capability,
                resolution="composition",
                trail=trail,
                plan=list(attempt.plan),
            )

        # 3. Configuration of an existing skill.
        configurable = self._find_declaring(request, "configurable_for")
        trail.append(
            _step(
                3,
                STEP_CONFIGURATION,
                "can an existing skill be configured?",
                "satisfied" if configurable else "insufficient",
                configurable
                or {"reason": "no registered skill declares configurable_for this capability"},
            )
        )
        if configurable:
            return GapDecision(
                requested_capability=request.requested_capability,
                resolution="configuration",
                trail=trail,
                plan=[configurable["capability_id"]],
            )

        # 4. Safe extension of an existing skill.
        extendable = self._find_declaring(request, "extension_points")
        trail.append(
            _step(
                4,
                STEP_EXTENSION,
                "can an existing skill be safely extended?",
                "satisfied" if extendable else "insufficient",
                extendable
                or {"reason": "no registered skill declares an extension point for it"},
            )
        )
        if extendable:
            return GapDecision(
                requested_capability=request.requested_capability,
                resolution="extension",
                trail=trail,
                plan=[extendable["capability_id"]],
            )

        # 5/6. New skill, unless the requirement implies a product/core change.
        product_change, evidence = implies_product_change(request)
        if product_change:
            trail.append(
                _step(
                    5,
                    STEP_NEW_SKILL,
                    "is a new skill/module required?",
                    "not_applicable",
                    {"reason": "the requirement reaches a product/core/recovery surface"},
                )
            )
            trail.append(
                _step(
                    6,
                    STEP_PRODUCT_CHANGE,
                    "does the requirement imply a product/core change?",
                    "refused",
                    evidence
                    | {
                        "policy": (
                            "core/recovery changes are never auto-generated "
                            "(constitution section 6, EVOLUTION_ENGINE_SPEC section 12)"
                        )
                    },
                )
            )
            return GapDecision(
                requested_capability=request.requested_capability,
                resolution="product_change_required",
                trail=trail,
                refusal_reason=(
                    "requirement implies a product/core/recovery change: "
                    + ", ".join(evidence["reasons"])
                ),
            )
        trail.append(
            _step(
                5,
                STEP_NEW_SKILL,
                "is a new skill/module required?",
                "satisfied",
                {
                    "reason": "composition, configuration and extension were all insufficient",
                    "composition_attempted_at_index": 2,
                    "composition_missing_outputs": list(attempt.missing_outputs),
                },
            )
        )
        trail.append(
            _step(
                6,
                STEP_PRODUCT_CHANGE,
                "does the requirement imply a product/core change?",
                "not_applicable",
                {"reason": "the requirement is satisfiable by an isolated generated skill"},
            )
        )
        return GapDecision(
            requested_capability=request.requested_capability,
            resolution="generation",
            trail=trail,
        )

    def _find_declaring(
        self, request: CapabilityRequest, manifest_key: str
    ) -> dict[str, Any] | None:
        for row in self.registry.list_capabilities(status="production", limit=500):
            resolved = self.registry.resolve(row["capability_id"])
            if resolved is None:
                continue
            declared = (resolved.get("manifest") or {}).get(manifest_key) or []
            if request.requested_capability in declared:
                return {
                    "capability_id": resolved["capability_id"],
                    "version": resolved["version"],
                    "declared_by": manifest_key,
                }
        return None


# ------------------------------------------------- composition-first gate


def composition_step(trail: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in trail or []:
        if isinstance(entry, dict) and entry.get("step") == STEP_COMPOSITION:
            return entry
    return None


def require_composition_attempted(trail: list[dict[str, Any]]) -> dict[str, Any]:
    """Hard precondition of code generation.

    The trail must contain a composition step, it must record a genuine attempt
    (``evidence.attempted is True``), it must have reported the composition
    INSUFFICIENT, and it must appear BEFORE the new_skill decision. Anything
    else raises ``generation_refused`` — the generator is never constructed.
    """
    entry = composition_step(trail)
    if entry is None:
        raise EvolutionError(
            EvolutionErrorClass.GENERATION_REFUSED,
            "no composition step in the decision trail; composition must be attempted "
            "before any code is generated",
        )
    evidence = entry.get("evidence") or {}
    if evidence.get("attempted") is not True:
        raise EvolutionError(
            EvolutionErrorClass.GENERATION_REFUSED,
            "the decision trail records a composition step that was never attempted",
        )
    if evidence.get("satisfied") is True or entry.get("outcome") != "insufficient":
        raise EvolutionError(
            EvolutionErrorClass.GENERATION_REFUSED,
            "composition satisfied the request; generating code is not permitted",
        )
    generation_entry = next(
        (e for e in trail if isinstance(e, dict) and e.get("step") == STEP_NEW_SKILL), None
    )
    if generation_entry is None:
        raise EvolutionError(
            EvolutionErrorClass.GENERATION_REFUSED,
            "the decision trail never reached the new_skill question",
        )
    if int(entry.get("index", 99)) >= int(generation_entry.get("index", 0)):
        raise EvolutionError(
            EvolutionErrorClass.GENERATION_REFUSED,
            "composition was not attempted before the new_skill decision",
        )
    return entry


def request_from_trail(trail: list[dict[str, Any]]) -> CapabilityRequest:
    for entry in trail or []:
        if isinstance(entry, dict) and entry.get("step") == STEP_REQUEST:
            return CapabilityRequest.parse(entry.get("evidence") or {})
    raise EvolutionError(
        EvolutionErrorClass.VALIDATION_ERROR,
        "decision trail does not contain the original request",
    )


# ---------------------------------------------------------------- persistence


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GapService:
    """Persistence for capability gaps and their decision trails."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def record(
        self,
        request: CapabilityRequest,
        decision: GapDecision,
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_immediately = decision.resolution in (
            "existing_capability",
            "composition",
            "configuration",
            "extension",
        )
        status = "resolved" if resolved_immediately else "open"
        if decision.resolution == "product_change_required":
            # Refused: nothing further will happen automatically.
            status = "abandoned"
        with self._session_factory() as session:
            row = CapabilityGap(
                requested_capability=request.requested_capability[:256],
                request_text=request.request_text,
                task_id=request.task_id,
                status=status,
                resolution=decision.resolution,
                decision_trail_json=decision.trail,
                trace_id=trace_id,
                resolved_at=_utcnow() if status in ("resolved", "abandoned") else None,
            )
            session.add(row)
            session.commit()
            logger.info(
                "capability_gap_recorded",
                gap_id=str(row.id),
                requested_capability=row.requested_capability,
                resolution=row.resolution,
                status=row.status,
            )
            return _gap_dict(row)

    def get(self, gap_id: uuid.UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(CapabilityGap, gap_id)
            if row is None:
                raise EvolutionError(
                    EvolutionErrorClass.NOT_FOUND, f"capability gap {gap_id} not found"
                )
            return _gap_dict(row)

    def list(
        self,
        *,
        status: str | None = None,
        resolution: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = select(CapabilityGap).order_by(CapabilityGap.created_at.desc()).limit(limit)
        if status:
            query = query.where(CapabilityGap.status == status)
        if resolution:
            query = query.where(CapabilityGap.resolution == resolution)
        with self._session_factory() as session:
            return [_gap_dict(row) for row in session.execute(query).scalars()]

    def set_status(self, gap_id: uuid.UUID, status: str) -> dict[str, Any]:
        if status not in GAP_STATUSES:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, f"invalid gap status: {status!r}"
            )
        with self._session_factory() as session:
            row = self._require(session, gap_id)
            row.status = status
            session.commit()
            return _gap_dict(row)

    def append_step(self, gap_id: uuid.UUID, step: dict[str, Any]) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require(session, gap_id)
            trail = list(row.decision_trail_json or [])
            trail.append(step)
            row.decision_trail_json = trail
            session.commit()
            return _gap_dict(row)

    def mark_resolved(
        self,
        gap_id: uuid.UUID,
        *,
        resolution: str,
        skill_version_id: uuid.UUID | None = None,
        status: str = "resolved",
    ) -> dict[str, Any]:
        if resolution not in GAP_RESOLUTIONS:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, f"invalid gap resolution: {resolution!r}"
            )
        if status not in GAP_STATUSES:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, f"invalid gap status: {status!r}"
            )
        with self._session_factory() as session:
            row = self._require(session, gap_id)
            row.resolution = resolution
            row.status = status
            row.resolved_skill_version_id = skill_version_id
            row.resolved_at = _utcnow()
            session.commit()
            logger.info(
                "capability_gap_resolved",
                gap_id=str(gap_id),
                resolution=resolution,
                status=status,
            )
            return _gap_dict(row)

    def _require(self, session: Session, gap_id: uuid.UUID) -> CapabilityGap:
        row = session.get(CapabilityGap, gap_id)
        if row is None:
            raise EvolutionError(
                EvolutionErrorClass.NOT_FOUND, f"capability gap {gap_id} not found"
            )
        return row


def _gap_dict(row: CapabilityGap) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "requested_capability": row.requested_capability,
        "request_text": row.request_text,
        "task_id": str(row.task_id) if row.task_id else None,
        "status": row.status,
        "resolution": row.resolution,
        "decision_trail": row.decision_trail_json,
        "resolved_skill_version_id": (
            str(row.resolved_skill_version_id) if row.resolved_skill_version_id else None
        ),
        "trace_id": row.trace_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


__all__ = [
    "DECISION_STEPS",
    "PRODUCT_CHANGE_KINDS",
    "PROTECTED_CAPABILITY_PREFIXES",
    "PROTECTED_COMPONENTS",
    "STEP_COMPOSITION",
    "STEP_CONFIGURATION",
    "STEP_EXISTING",
    "STEP_EXTENSION",
    "STEP_NEW_SKILL",
    "STEP_PRODUCT_CHANGE",
    "STEP_REQUEST",
    "CapabilityComposer",
    "CapabilityRequest",
    "CompositionAttempt",
    "GapDecision",
    "GapDetector",
    "GapService",
    "composition_step",
    "implies_product_change",
    "request_from_trail",
    "require_composition_attempted",
]
