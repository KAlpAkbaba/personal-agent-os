"""Unit tests for opportunity scoring: monotonicity, the risk veto, rationale.

The composite decides what the engine works on while the owner is asleep, so
the properties tested here are the ones that would quietly misrank the backlog
if a weight were edited carelessly.
"""

from __future__ import annotations

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.scoring import (
    RISK_VETO_THRESHOLD,
    SCORE_FIELDS,
    W_RECURRENCE,
    W_RELEVANCE,
    W_UTILITY,
    score_from_mapping,
    score_opportunity,
    weights,
)

BASE = {
    "owner_relevance": 0.5,
    "expected_utility": 0.5,
    "recurrence": 0.5,
    "confidence": 0.5,
    "engineering_cost": 0.5,
    "operational_risk": 0.2,
}


def score(**overrides: float) -> float:
    return score_opportunity(**{**BASE, **overrides}).composite


# ------------------------------------------------------------- monotonicity


@pytest.mark.parametrize(
    "field_name", ["owner_relevance", "expected_utility", "recurrence", "confidence"]
)
def test_more_of_a_benefit_input_never_lowers_the_composite(field_name: str) -> None:
    previous = -1.0
    for value in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        current = score(**{field_name: value})
        assert current > previous, (field_name, value)
        previous = current


@pytest.mark.parametrize("field_name", ["engineering_cost", "operational_risk"])
def test_more_of_a_penalty_input_never_raises_the_composite(field_name: str) -> None:
    previous = 2.0
    for value in (0.0, 0.2, 0.4, 0.6):
        current = score(**{field_name: value})
        assert current < previous, (field_name, value)
        previous = current


def test_the_composite_stays_in_the_unit_interval() -> None:
    corners = (0.0, 1.0)
    for relevance in corners:
        for utility in corners:
            for recur in corners:
                for confidence in corners:
                    for cost in corners:
                        for risk in corners:
                            result = score_opportunity(
                                owner_relevance=relevance,
                                expected_utility=utility,
                                recurrence=recur,
                                confidence=confidence,
                                engineering_cost=cost,
                                operational_risk=risk,
                            )
                            assert 0.0 <= result.composite <= 1.0


def test_relevance_outweighs_utility_which_outweighs_recurrence() -> None:
    """The single-owner weighting, asserted rather than assumed."""
    assert W_RELEVANCE > W_UTILITY > W_RECURRENCE
    assert score(owner_relevance=1.0) > score(expected_utility=1.0)
    assert score(expected_utility=1.0) > score(recurrence=1.0)


def test_confidence_scales_value_instead_of_adding_to_it() -> None:
    """Weak evidence halves an opportunity; it never deletes it."""
    weak = score_opportunity(
        owner_relevance=0.9,
        expected_utility=0.9,
        recurrence=0.9,
        confidence=0.0,
        engineering_cost=0.0,
        operational_risk=0.0,
    )
    strong = score_opportunity(
        owner_relevance=0.9,
        expected_utility=0.9,
        recurrence=0.9,
        confidence=1.0,
        engineering_cost=0.0,
        operational_risk=0.0,
    )
    assert weak.composite > 0.0
    assert weak.composite == pytest.approx(strong.composite * 0.5, rel=1e-6)


def test_cost_discounts_but_never_dominates() -> None:
    """An expensive, highly relevant idea still beats a cheap, irrelevant one."""
    expensive_and_valuable = score_opportunity(
        owner_relevance=1.0,
        expected_utility=1.0,
        recurrence=1.0,
        confidence=1.0,
        engineering_cost=1.0,
        operational_risk=0.0,
    )
    cheap_and_pointless = score_opportunity(
        owner_relevance=0.1,
        expected_utility=0.1,
        recurrence=0.1,
        confidence=1.0,
        engineering_cost=0.0,
        operational_risk=0.0,
    )
    assert expensive_and_valuable.composite > cheap_and_pointless.composite


# ---------------------------------------------------------------- risk veto


def test_high_operational_risk_vetoes_regardless_of_upside() -> None:
    perfect_but_risky = score_opportunity(
        owner_relevance=1.0,
        expected_utility=1.0,
        recurrence=1.0,
        confidence=1.0,
        engineering_cost=0.0,
        operational_risk=RISK_VETO_THRESHOLD,
    )
    assert perfect_but_risky.vetoed is True
    assert perfect_but_risky.composite == 0.0
    assert any("VETO" in line for line in perfect_but_risky.rationale)


@pytest.mark.parametrize("risk", [0.7, 0.8, 0.9, 1.0])
def test_no_combination_of_upside_can_outscore_a_vetoed_opportunity(risk: float) -> None:
    risky = score_opportunity(
        owner_relevance=1.0,
        expected_utility=1.0,
        recurrence=1.0,
        confidence=1.0,
        engineering_cost=0.0,
        operational_risk=risk,
    )
    modest_but_safe = score_opportunity(
        owner_relevance=0.2,
        expected_utility=0.2,
        recurrence=0.2,
        confidence=0.2,
        engineering_cost=0.9,
        operational_risk=0.0,
    )
    assert risky.composite == 0.0
    assert modest_but_safe.composite > risky.composite


def test_just_below_the_threshold_is_penalised_but_not_vetoed() -> None:
    near = score_opportunity(
        owner_relevance=1.0,
        expected_utility=1.0,
        recurrence=1.0,
        confidence=1.0,
        engineering_cost=0.0,
        operational_risk=RISK_VETO_THRESHOLD - 0.01,
    )
    safe = score_opportunity(
        owner_relevance=1.0,
        expected_utility=1.0,
        recurrence=1.0,
        confidence=1.0,
        engineering_cost=0.0,
        operational_risk=0.0,
    )
    assert near.vetoed is False
    assert 0.0 < near.composite < safe.composite


# ---------------------------------------------------------------- rationale


def test_the_rationale_explains_every_stage_in_plain_arithmetic() -> None:
    result = score_opportunity(**BASE)
    assert len(result.rationale) == 4
    joined = " ".join(result.rationale)
    for word in ("benefit", "confidence", "cost", "risk"):
        assert word in joined
    # The intermediate values are exposed, not just the answer.
    assert result.raw_benefit > 0
    assert result.expected_value > 0
    assert result.cost_adjusted > 0
    assert result.risk_penalty > 0


def test_the_score_is_pure() -> None:
    first = score_opportunity(**BASE).to_dict()
    second = score_opportunity(**BASE).to_dict()
    assert first == second


def test_to_dict_carries_the_inputs_and_the_verdict() -> None:
    payload = score_opportunity(**BASE).to_dict()
    for field_name in SCORE_FIELDS:
        assert field_name in payload
    assert payload["composite"] == pytest.approx(score(**BASE))
    assert payload["vetoed"] is False
    assert isinstance(payload["rationale"], list)


# --------------------------------------------------------------- validation


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan"), "0.5", None, True])
def test_an_out_of_range_or_wrong_typed_input_is_a_validation_error(bad: object) -> None:
    with pytest.raises(EvolutionError) as excinfo:
        score_opportunity(**{**BASE, "owner_relevance": bad})  # type: ignore[arg-type]
    assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR


def test_score_from_mapping_refuses_missing_and_unknown_fields() -> None:
    with pytest.raises(EvolutionError) as missing:
        score_from_mapping({k: v for k, v in BASE.items() if k != "confidence"})
    assert missing.value.details["missing"] == ["confidence"]

    with pytest.raises(EvolutionError) as unknown:
        score_from_mapping({**BASE, "vibes": 1.0})
    assert unknown.value.details["unknown"] == ["vibes"]


def test_the_published_weights_match_the_module_constants() -> None:
    published = weights()
    assert published["owner_relevance"] == W_RELEVANCE
    assert published["expected_utility"] == W_UTILITY
    assert published["recurrence"] == W_RECURRENCE
    assert published["risk_veto_threshold"] == RISK_VETO_THRESHOLD
