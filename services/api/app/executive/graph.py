"""``validate_graph``: the ONE gate every ``TaskGraph`` passes through before anything
executes (spec §1, §2, §8; ADR-0089 decision 1).

Both planners go through this — the deterministic one (``app.executive.planner.
RuleBasedExecutivePlanner``) and the inert model seam (``ClaudeExecutivePlanner``, which
may only PROPOSE a graph). Pydantic (``app.executive.spec``) already refuses a
structurally malformed graph at construction; what this module adds is everything a field
type cannot express on its own: the DAG (a step may depend only on an EARLIER step, never
itself or a later one), that every step's risk class / compensation / evidence kind is
exactly the FIXED profile its kind carries (never something a planner chose), that no step
is ``high_risk`` (spec §1: that class exists in the vocabulary and is carried by no kind —
this is where its absence is checked POSITIVELY rather than assumed by omission), and that
a retry's ``only_on`` names only the two genuinely transient error classes.

Every violation is collected, not just the first — a planner bug usually breaks more than
one rule at once, and the caller (the planner's own tests, or a REST/voice caller reporting
a refusal to the owner) benefits from seeing the whole list in one turn.
"""

from __future__ import annotations

from app.executive.refs import parse_reference
from app.executive.spec import (
    MAX_STEPS,
    PLANNERS,
    PRECONDITION_STEP_DONE,
    PRECONDITION_STEP_FAILED,
    PRECONDITION_STEP_VERIFIED,
    RETRYABLE_ERROR_CLASSES,
    RISK_HIGH_RISK,
    STEP_KIND_PROFILES,
    Step,
    TaskGraph,
)


class GraphValidationError(ValueError):
    """Every reason the graph was refused, in one place — never just the first."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons) if self.reasons else "invalid graph")


def _referenced_step_id(value: str) -> str | None:
    ref = parse_reference(value)
    return ref[0] if ref is not None else None


def validate_graph(graph: TaskGraph) -> None:
    """Raises :class:`GraphValidationError` with every violation found; returns
    ``None`` (silently) when the graph is clean. Pure — no I/O, no database, no clock;
    a graph is either well-formed on its own terms or it is not."""
    reasons: list[str] = []

    if graph.planner is not None and graph.planner not in PLANNERS:
        reasons.append(f"graph names an unknown planner {graph.planner!r}")
    if len(graph.steps) > MAX_STEPS:
        reasons.append(f"graph has {len(graph.steps)} steps, over the bound of {MAX_STEPS}")

    seen_ids: set[str] = set()
    for step in graph.steps:
        if step.id in seen_ids:
            reasons.append(f"step id {step.id!r} is used more than once")
        seen_ids.add(step.id)

    for index, step in enumerate(graph.steps):
        reasons.extend(_validate_step(step, index, graph.steps))

    if reasons:
        raise GraphValidationError(reasons)


def _validate_step(step: Step, index: int, all_steps: list[Step]) -> list[str]:
    reasons: list[str] = []
    profile = STEP_KIND_PROFILES.get(step.kind)
    if profile is None:
        reasons.append(f"step {step.id!r}: kind {step.kind!r} is not in the closed vocabulary")
        # Nothing else about this step can be checked against a profile that does not
        # exist; the caller already has the one reason that matters.
        return reasons

    # The risk class / compensation / evidence kind a planner attaches to a step are not
    # its own choice — they are looked up from the kind (spec §1's table is a FUNCTION of
    # the kind, never a free field a proposal can set independently). A mismatch here is
    # either a planner bug or an attempt to smuggle a stronger authority onto a weaker
    # kind (e.g. claiming risk_class=mutate_external on a documents.find step) — refused
    # either way, never silently corrected to the right value.
    if step.risk_class != profile.risk_class:
        reasons.append(
            f"step {step.id!r}: risk_class {step.risk_class!r} does not match "
            f"{step.kind!r}'s fixed profile {profile.risk_class!r}"
        )
    if step.risk_class == RISK_HIGH_RISK:
        # Defense in depth (spec §1, §8): even though no kind's profile is ever
        # high_risk, a graph that somehow claims it anyway is refused explicitly rather
        # than relying only on the mismatch check above ever catching it.
        reasons.append(f"step {step.id!r}: risk_class=high_risk is never permitted")
    if step.compensation != profile.compensation:
        reasons.append(
            f"step {step.id!r}: compensation {step.compensation!r} does not match "
            f"{step.kind!r}'s fixed profile {profile.compensation!r}"
        )
    if step.postcondition.evidence != profile.evidence_kind:
        reasons.append(
            f"step {step.id!r}: postcondition.evidence {step.postcondition.evidence!r} "
            f"does not match {step.kind!r}'s fixed evidence kind {profile.evidence_kind!r}"
        )

    for error_class in step.retry.only_on:
        if error_class not in RETRYABLE_ERROR_CLASSES:
            reasons.append(
                f"step {step.id!r}: retry.only_on names {error_class!r}, "
                f"not one of {RETRYABLE_ERROR_CLASSES}"
            )

    earlier_ids = {s.id for s in all_steps[:index]}
    for name, value in step.inputs.items():
        ref = _referenced_step_id(value)
        if ref is None:
            continue  # a literal, not a cross-step reference
        if ref not in {s.id for s in all_steps}:
            continue  # not a step id at all — a literal that happens to contain a dot
        if ref not in earlier_ids:
            reasons.append(
                f"step {step.id!r}: input {name!r} references {ref!r}, "
                f"which is not an EARLIER step (DAG violation)"
            )

    if step.precondition.check in (
        PRECONDITION_STEP_DONE,
        PRECONDITION_STEP_FAILED,
        PRECONDITION_STEP_VERIFIED,
    ):
        arg = step.precondition.arg
        if arg is None or arg not in earlier_ids:
            reasons.append(
                f"step {step.id!r}: precondition {step.precondition.check} names {arg!r}, "
                f"not an earlier step in this graph"
            )
    # B38 (req 555): a loop with nothing to reach is a loop for ever - refused.
    if step.repeat.max_rounds > 1 and step.postcondition.min is None:
        reasons.append(
            f"step {step.id!r}: repeat.max_rounds > 1 needs a postcondition.min to reach"
        )

    return reasons


__all__ = ["GraphValidationError", "validate_graph"]
