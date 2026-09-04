"""Opportunity scoring — a pure function, no I/O, no model call.

Why a formula rather than a judgement
-------------------------------------

The backlog decides what the engine works on next while the owner is asleep.
If that ranking came from a model call it would be unauditable and unstable:
the same opportunity would score differently on Tuesday. So the composite is
arithmetic over six declared inputs, and the rationale explains the arithmetic
rather than paraphrasing it.

The six inputs (each 0.0..1.0)
------------------------------

``owner_relevance``
    How much this matters to *this* owner. Not "is it a good idea" — is it a
    good idea for the person who owns this system.
``expected_utility``
    Size of the improvement if it works: time saved, failures removed,
    capability gained.
``recurrence``
    How often the underlying need shows up. A daily annoyance beats a
    once-a-year one at equal size.
``confidence``
    How much the evidence actually supports the claim. Two ledger events is
    not the same as forty.
``engineering_cost``
    How much work it is. A *discount*, never a veto.
``operational_risk``
    What it could break in production if it is wrong. A *penalty* with a veto.

Why these weights
-----------------

``W_RELEVANCE = 0.45`` is the largest term because the constitution has exactly
one human authority (§2): a technically excellent improvement the owner does
not care about has no value here, unlike in a product with many users where
breadth could compensate. ``W_UTILITY = 0.35`` is next: measurable benefit is
the point, but it is subordinate to relevance for the same reason.
``W_RECURRENCE = 0.20`` is real but smallest — recurrence multiplies value over
time, yet a single high-value fix (an incident that lost data once) must still
be able to outrank a frequent triviality, which it can because relevance and
utility together hold 0.80 of the weight.

``confidence`` scales the value instead of adding to it (``0.5 + 0.5·c``):
adding it would let a well-evidenced trivial idea beat a poorly-evidenced
important one, which is backwards. Scaling means weak evidence *halves* an
opportunity's value but never zeroes it — a real problem with thin evidence is
still a real problem, and the correct response is to research it (the
RESEARCHING state exists for exactly this), not to discard it.

``engineering_cost`` is a discount of at most ``COST_WEIGHT = 0.35``. It is
deliberately weak: the constitution says the owner should not become the
software's operator (§5), so engineering effort is the machine's problem, not
the owner's. Cost breaks ties between comparable opportunities; it must not
make the system systematically prefer easy work over valuable work.

Why risk vetoes instead of subtracting
--------------------------------------

``operational_risk`` is the only asymmetric input. A wrong high-utility change
in a system that operates the owner's devices and data is not "slightly
negative expected value" — it is an incident, and the constitution's evolution
boundaries (§6) exist because that class of failure is not recoverable by
scoring better next time. So risk applies twice:

1. a continuous quadratic penalty (``RISK_PENALTY · risk²``) below the
   threshold, so moderate risk costs progressively more than it saves;
2. a hard **veto** at ``RISK_VETO_THRESHOLD = 0.7``: composite becomes 0.0 and
   ``vetoed`` is True, whatever the other five inputs say.

The veto is what makes the guarantee checkable: no combination of relevance,
utility, recurrence and confidence can outscore a high-risk change, because
above the threshold the other terms are not consulted at all. A vetoed
opportunity is not deleted — it is recorded, explained, and left for the owner,
who is the only authority that can accept that risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from app.evolution.errors import EvolutionError, EvolutionErrorClass

# --------------------------------------------------------------- coefficients

W_RELEVANCE: Final[float] = 0.45
W_UTILITY: Final[float] = 0.35
W_RECURRENCE: Final[float] = 0.20

#: Confidence scales value into [0.5, 1.0] of the raw weighted benefit.
CONFIDENCE_FLOOR: Final[float] = 0.5

#: Maximum fraction of value that engineering cost can discount.
COST_WEIGHT: Final[float] = 0.35

#: Coefficient on the quadratic sub-threshold risk penalty.
RISK_PENALTY: Final[float] = 0.60

#: At or above this, the composite is zero regardless of every other input.
RISK_VETO_THRESHOLD: Final[float] = 0.70

SCORE_FIELDS: Final[tuple[str, ...]] = (
    "owner_relevance",
    "expected_utility",
    "recurrence",
    "confidence",
    "engineering_cost",
    "operational_risk",
)

WEIGHTS: Final[dict[str, float]] = {
    "owner_relevance": W_RELEVANCE,
    "expected_utility": W_UTILITY,
    "recurrence": W_RECURRENCE,
    "confidence_floor": CONFIDENCE_FLOOR,
    "engineering_cost": COST_WEIGHT,
    "operational_risk": RISK_PENALTY,
    "risk_veto_threshold": RISK_VETO_THRESHOLD,
}

assert abs((W_RELEVANCE + W_UTILITY + W_RECURRENCE) - 1.0) < 1e-9, (
    "benefit weights must sum to 1.0 so the composite stays in [0, 1]"
)


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    """The composite plus every intermediate value that produced it."""

    owner_relevance: float
    expected_utility: float
    recurrence: float
    confidence: float
    engineering_cost: float
    operational_risk: float
    raw_benefit: float
    expected_value: float
    cost_adjusted: float
    risk_penalty: float
    composite: float
    vetoed: bool
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_relevance": self.owner_relevance,
            "expected_utility": self.expected_utility,
            "recurrence": self.recurrence,
            "confidence": self.confidence,
            "engineering_cost": self.engineering_cost,
            "operational_risk": self.operational_risk,
            "raw_benefit": self.raw_benefit,
            "expected_value": self.expected_value,
            "cost_adjusted": self.cost_adjusted,
            "risk_penalty": self.risk_penalty,
            "composite": self.composite,
            "vetoed": self.vetoed,
            "rationale": list(self.rationale),
        }


def _unit(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"{field_name} must be a number in [0.0, 1.0]",
            details={"field": field_name},
        )
    number = float(value)
    if not (0.0 <= number <= 1.0) or number != number:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"{field_name} must be in [0.0, 1.0]",
            details={"field": field_name},
        )
    return number


def score_opportunity(
    *,
    owner_relevance: float,
    expected_utility: float,
    recurrence: float,
    confidence: float,
    engineering_cost: float,
    operational_risk: float,
) -> OpportunityScore:
    """Score one opportunity. Pure: same inputs, same output, forever.

    Returns a composite in ``[0.0, 1.0]`` and a rationale a human can read
    without opening this file. Every input must be in ``[0.0, 1.0]``; anything
    else is a typed ``validation_error`` rather than a silently clamped value,
    because a caller passing 5.0 for "very relevant" has a bug worth surfacing.
    """
    relevance = _unit(owner_relevance, field_name="owner_relevance")
    utility = _unit(expected_utility, field_name="expected_utility")
    recur = _unit(recurrence, field_name="recurrence")
    conf = _unit(confidence, field_name="confidence")
    cost = _unit(engineering_cost, field_name="engineering_cost")
    risk = _unit(operational_risk, field_name="operational_risk")

    raw_benefit = W_RELEVANCE * relevance + W_UTILITY * utility + W_RECURRENCE * recur
    confidence_factor = CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * conf
    expected_value = raw_benefit * confidence_factor
    cost_adjusted = expected_value * (1.0 - COST_WEIGHT * cost)
    penalty = RISK_PENALTY * risk * risk

    rationale: list[str] = [
        f"benefit {raw_benefit:.3f} = "
        f"{W_RELEVANCE:.2f}*relevance({relevance:.2f}) + "
        f"{W_UTILITY:.2f}*utility({utility:.2f}) + "
        f"{W_RECURRENCE:.2f}*recurrence({recur:.2f})",
        f"confidence {conf:.2f} scales it by {confidence_factor:.3f} "
        f"-> expected value {expected_value:.3f} "
        "(weak evidence halves value, it never discards the idea)",
        f"engineering cost {cost:.2f} discounts up to {COST_WEIGHT:.2f} "
        f"-> {cost_adjusted:.3f} (cost breaks ties; it does not set priority)",
    ]

    if risk >= RISK_VETO_THRESHOLD:
        composite = 0.0
        vetoed = True
        rationale.append(
            f"VETO: operational risk {risk:.2f} is at or above the "
            f"{RISK_VETO_THRESHOLD:.2f} threshold, so the composite is 0.00 "
            "regardless of relevance, utility, recurrence or confidence — a "
            "high-risk change to a system that holds the owner's data cannot "
            "be justified by upside. Only the owner may accept this risk."
        )
    else:
        composite = max(0.0, cost_adjusted - penalty)
        vetoed = False
        rationale.append(
            f"operational risk {risk:.2f} costs {penalty:.3f} "
            f"({RISK_PENALTY:.2f}*risk^2) -> composite {composite:.3f}"
        )

    return OpportunityScore(
        owner_relevance=relevance,
        expected_utility=utility,
        recurrence=recur,
        confidence=conf,
        engineering_cost=cost,
        operational_risk=risk,
        raw_benefit=round(raw_benefit, 6),
        expected_value=round(expected_value, 6),
        cost_adjusted=round(cost_adjusted, 6),
        risk_penalty=round(penalty, 6),
        composite=round(composite, 6),
        vetoed=vetoed,
        rationale=rationale,
    )


def score_from_mapping(scores: dict[str, Any]) -> OpportunityScore:
    """Score from a request/JSON body, refusing unknown or missing fields."""
    if not isinstance(scores, dict):
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            "scores must be an object with the six declared inputs",
            details={"expected": list(SCORE_FIELDS)},
        )
    unknown = sorted(set(scores) - set(SCORE_FIELDS))
    if unknown:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            "unknown score field",
            details={"unknown": unknown, "expected": list(SCORE_FIELDS)},
        )
    missing = sorted(set(SCORE_FIELDS) - set(scores))
    if missing:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            "missing score field",
            details={"missing": missing},
        )
    return score_opportunity(**{name: scores[name] for name in SCORE_FIELDS})


def weights() -> dict[str, float]:
    """The published coefficients, for ``GET /v1/evolution/policy``."""
    return dict(WEIGHTS)


__all__ = [
    "CONFIDENCE_FLOOR",
    "COST_WEIGHT",
    "RISK_PENALTY",
    "RISK_VETO_THRESHOLD",
    "SCORE_FIELDS",
    "WEIGHTS",
    "W_RECURRENCE",
    "W_RELEVANCE",
    "W_UTILITY",
    "OpportunityScore",
    "score_from_mapping",
    "score_opportunity",
    "weights",
]
