"""Unit tests: the §3 gap-detection decision tree and its audit trail.

Acceptance coverage:
- "gap detector identifies missing capability";
- "existing-tool composition is attempted first";
- "new generated skill is created only when necessary" (the composition and
  configuration/extension branches never reach generation).
"""

import uuid

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.gaps import (
    STEP_COMPONENT,
    STEP_COMPOSITION,
    STEP_EXISTING,
    STEP_NEW_SKILL,
    STEP_PRODUCT_CHANGE,
    STEP_REQUEST,
    CapabilityComposer,
    CapabilityRequest,
    GapDetector,
    GapService,
    composition_step,
    request_from_trail,
    require_composition_attempted,
)
from app.evolution.registry import CapabilityRegistry
from tests.unit.test_evolution_registry import (
    make_session_factory,
    manifest_for,
    register_fully,
)


@pytest.fixture()
def stack() -> tuple[CapabilityRegistry, GapService, GapDetector]:
    factory = make_session_factory()
    registry = CapabilityRegistry(factory)
    return registry, GapService(factory), GapDetector(registry)


def make_request(**overrides) -> CapabilityRequest:
    body = {
        "requested_capability": "text.slugify",
        "request_text": "Bir baslik verildiginde url slug uret.",
        "required_inputs": ["text"],
        "required_outputs": ["slug"],
    }
    body.update(overrides)
    return CapabilityRequest.parse(body)


def steps_by_name(trail: list[dict]) -> dict[str, dict]:
    return {entry["step"]: entry for entry in trail}


# ------------------------------------------------------ 1. missing capability


def test_gap_detector_identifies_missing_capability(stack) -> None:
    _, _, detector = stack
    decision = detector.detect(make_request())
    assert decision.resolution == "generation"
    steps = steps_by_name(decision.trail)
    assert steps[STEP_EXISTING]["outcome"] == "insufficient"
    assert steps[STEP_NEW_SKILL]["outcome"] == "satisfied"
    assert steps[STEP_PRODUCT_CHANGE]["outcome"] == "not_applicable"
    assert [entry["index"] for entry in decision.trail] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert steps[STEP_COMPONENT]["outcome"] == "insufficient"
    # the component question is asked BEFORE the generate decision
    assert steps[STEP_COMPONENT]["index"] < steps[STEP_NEW_SKILL]["index"]
    assert decision.resolution_path == "generation_from_scratch"


def test_existing_capability_short_circuits_the_tree(stack) -> None:
    registry, _, detector = stack
    register_fully(registry, "text.slugify")
    decision = detector.detect(make_request())
    assert decision.resolution == "existing_capability"
    assert composition_step(decision.trail) is None  # never needed to ask


# ------------------------------------------- 2. composition attempted FIRST


def test_composition_is_attempted_before_the_generation_decision(stack) -> None:
    registry, _, detector = stack
    register_fully(registry, "text.normalize", inputs=["text"], outputs=["normalized_text"])
    decision = detector.detect(make_request())
    assert decision.resolution == "generation"

    composition = composition_step(decision.trail)
    assert composition is not None
    evidence = composition["evidence"]
    assert evidence["attempted"] is True
    assert evidence["satisfied"] is False
    # The attempt is REAL: it enumerated the dispatchable catalog and named the
    # output it could not produce.
    assert [c["capability_id"] for c in evidence["considered"]] == ["text.normalize"]
    assert evidence["missing_outputs"] == ["slug"]
    assert evidence["reason"]

    new_skill = steps_by_name(decision.trail)[STEP_NEW_SKILL]
    assert composition["index"] < new_skill["index"]
    assert new_skill["evidence"]["composition_attempted_at_index"] == composition["index"]


def test_composition_satisfies_a_chainable_request(stack) -> None:
    """A request an existing chain can serve resolves to composition — the tree
    never reaches the new_skill question, so no code can be generated."""
    registry, _, detector = stack
    register_fully(registry, "text.tokenize", inputs=["text"], outputs=["tokens"])
    register_fully(registry, "text.count_tokens", inputs=["tokens"], outputs=["count"])
    decision = detector.detect(
        make_request(
            requested_capability="text.word_count",
            required_inputs=["text"],
            required_outputs=["count"],
        )
    )
    assert decision.resolution == "composition"
    assert decision.plan == ["text.count_tokens", "text.tokenize"] or decision.plan == [
        "text.tokenize",
        "text.count_tokens",
    ]
    assert STEP_NEW_SKILL not in steps_by_name(decision.trail)


def test_composer_only_uses_dispatchable_capabilities(stack) -> None:
    registry, _, _ = stack
    registry.upsert_capability(
        "text.tokenize",
        manifest_for("text.tokenize", inputs=["text"], outputs=["tokens"]),
    )
    attempt = CapabilityComposer(registry).attempt(
        make_request(required_inputs=["text"], required_outputs=["tokens"])
    )
    assert attempt.attempted is True
    assert attempt.satisfied is False
    assert attempt.considered == []
    assert "no dispatchable capabilities" in attempt.reason


def test_composer_reports_a_request_without_declared_outputs(stack) -> None:
    registry, _, _ = stack
    attempt = CapabilityComposer(registry).attempt(make_request(required_outputs=[]))
    assert attempt.attempted is True
    assert attempt.satisfied is False
    assert "no required outputs" in attempt.reason


# ---------------------------------------- 3/4. configuration and extension


def test_configuration_branch_beats_generation(stack) -> None:
    registry, _, detector = stack
    register_fully(
        registry,
        "text.normalize",
        inputs=["text"],
        outputs=["normalized_text"],
        configurable_for=["text.slugify"],
    )
    decision = detector.detect(make_request())
    assert decision.resolution == "configuration"
    assert decision.plan == ["text.normalize"]
    assert STEP_NEW_SKILL not in steps_by_name(decision.trail)
    # Composition was still attempted first, and recorded.
    assert composition_step(decision.trail)["evidence"]["attempted"] is True


def test_extension_branch_beats_generation(stack) -> None:
    registry, _, detector = stack
    register_fully(
        registry,
        "text.normalize",
        inputs=["text"],
        outputs=["normalized_text"],
        extension_points=["text.slugify"],
    )
    decision = detector.detect(make_request())
    assert decision.resolution == "extension"
    assert STEP_NEW_SKILL not in steps_by_name(decision.trail)


# -------------------------------------------- the composition-first hard gate


def test_require_composition_attempted_accepts_a_real_trail(stack) -> None:
    _, _, detector = stack
    decision = detector.detect(make_request())
    entry = require_composition_attempted(decision.trail)
    assert entry["step"] == STEP_COMPOSITION


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda trail: [e for e in trail if e["step"] != STEP_COMPOSITION], "no composition step"),
        (
            lambda trail: [
                {**e, "evidence": {**e["evidence"], "attempted": False}}
                if e["step"] == STEP_COMPOSITION
                else e
                for e in trail
            ],
            "never attempted",
        ),
        (
            lambda trail: [
                {**e, "index": 99} if e["step"] == STEP_COMPOSITION else e for e in trail
            ],
            "not attempted before",
        ),
        (lambda trail: [e for e in trail if e["step"] != STEP_NEW_SKILL], "new_skill question"),
    ],
)
def test_forged_trails_cannot_unlock_generation(stack, mutate, expected) -> None:
    _, _, detector = stack
    trail = detector.detect(make_request()).trail
    with pytest.raises(EvolutionError) as excinfo:
        require_composition_attempted(mutate(trail))
    assert excinfo.value.error_class == EvolutionErrorClass.GENERATION_REFUSED
    assert expected in excinfo.value.message


def test_satisfied_composition_blocks_generation(stack) -> None:
    registry, _, detector = stack
    register_fully(registry, "text.tokenize", inputs=["text"], outputs=["slug"])
    decision = detector.detect(make_request())
    assert decision.resolution == "composition"
    with pytest.raises(EvolutionError) as excinfo:
        require_composition_attempted(decision.trail)
    assert "generating code is not permitted" in excinfo.value.message


# ----------------------------------------------------------- request parsing


def test_request_parsing_bounds_and_tokens() -> None:
    with pytest.raises(EvolutionError):
        CapabilityRequest.parse({"requested_capability": "Text.Slug", "request_text": "x"})
    with pytest.raises(EvolutionError):
        CapabilityRequest.parse({"requested_capability": "text.slugify", "request_text": ""})
    with pytest.raises(EvolutionError):
        CapabilityRequest.parse(
            {
                "requested_capability": "text.slugify",
                "request_text": "x" * 4001,
            }
        )
    with pytest.raises(EvolutionError):
        CapabilityRequest.parse(
            {
                "requested_capability": "text.slugify",
                "request_text": "x",
                "required_inputs": ["../etc/passwd"],
            }
        )
    with pytest.raises(EvolutionError):
        CapabilityRequest.parse(
            {"requested_capability": "text.slugify", "request_text": "x", "task_id": "nope"}
        )


def test_request_round_trips_through_the_trail(stack) -> None:
    _, _, detector = stack
    task_id = uuid.uuid4()
    request = make_request(task_id=str(task_id), resume_payload={"text": "Merhaba Dunya"})
    trail = detector.detect(request).trail
    assert trail[0]["step"] == STEP_REQUEST
    restored = request_from_trail(trail)
    assert restored.requested_capability == request.requested_capability
    assert restored.task_id == task_id
    assert restored.resume_payload == {"text": "Merhaba Dunya"}


def test_request_from_trail_requires_the_request_step() -> None:
    with pytest.raises(EvolutionError):
        request_from_trail([{"step": STEP_COMPOSITION, "evidence": {}}])


# ------------------------------------------------------------- persistence


def test_gap_service_persists_the_decision_trail(stack) -> None:
    _, gaps, detector = stack
    request = make_request()
    decision = detector.detect(request)
    gap = gaps.record(request, decision, trace_id="trace-1")
    assert gap["status"] == "open"
    assert gap["resolution"] == "generation"
    assert [e["step"] for e in gap["decision_trail"]][:3] == [
        STEP_REQUEST,
        STEP_EXISTING,
        STEP_COMPOSITION,
    ]
    fetched = gaps.get(uuid.UUID(gap["id"]))
    assert fetched["trace_id"] == "trace-1"
    assert len(gaps.list(status="open")) == 1


def test_gap_resolved_by_composition_is_closed_immediately(stack) -> None:
    registry, gaps, detector = stack
    register_fully(registry, "text.tokenize", inputs=["text"], outputs=["slug"])
    request = make_request()
    gap = gaps.record(request, detector.detect(request))
    assert gap["resolution"] == "composition"
    assert gap["status"] == "resolved"


def test_gap_service_rejects_invalid_states(stack) -> None:
    _, gaps, detector = stack
    request = make_request()
    gap = gaps.record(request, detector.detect(request))
    gap_id = uuid.UUID(gap["id"])
    with pytest.raises(EvolutionError):
        gaps.set_status(gap_id, "nonsense")
    with pytest.raises(EvolutionError):
        gaps.mark_resolved(gap_id, resolution="nonsense")
    with pytest.raises(EvolutionError) as excinfo:
        gaps.get(uuid.uuid4())
    assert excinfo.value.error_class == EvolutionErrorClass.NOT_FOUND


# ------------------------------- 4. install/adapt a reusable component


def test_component_adaptation_is_considered_before_generation(stack) -> None:
    """A request the vetted catalog covers resolves through step 4, and the
    trail proves the question was asked BEFORE the generate decision."""
    _, _, detector = stack
    decision = detector.detect(
        make_request(
            requested_capability="text.word_count",
            required_inputs=["text"],
            required_outputs=["count"],
        )
    )
    assert decision.resolution == "generation"  # frozen CHECK has no `component`
    assert decision.resolution_path == "component_adaptation"
    assert decision.component["name"] == "text_metrics_kit"
    assert decision.component["digest"].startswith("sha256:")

    steps = steps_by_name(decision.trail)
    component = steps[STEP_COMPONENT]
    assert component["outcome"] == "satisfied"
    assert component["index"] == 5
    assert component["index"] < steps[STEP_NEW_SKILL]["index"]
    assert component["evidence"]["install"] == "offline-local-catalog"
    assert steps[STEP_NEW_SKILL]["evidence"]["resolution_path"] == "component_adaptation"


def test_composition_still_wins_over_component_adaptation(stack) -> None:
    """The order is enforced: if registered capabilities can be chained, the
    catalog is never even consulted."""
    registry, _, detector = stack
    register_fully(registry, "text.tokenize", inputs=["text"], outputs=["count"])
    decision = detector.detect(
        make_request(
            requested_capability="text.word_count",
            required_inputs=["text"],
            required_outputs=["count"],
        )
    )
    assert decision.resolution == "composition"
    assert STEP_COMPONENT not in steps_by_name(decision.trail)


def test_the_catalog_survey_is_recorded_even_when_nothing_matches(stack) -> None:
    _, _, detector = stack
    decision = detector.detect(make_request())  # wants `slug`; catalog has none
    component = steps_by_name(decision.trail)[STEP_COMPONENT]
    assert component["outcome"] == "insufficient"
    assert component["evidence"]["catalog"], "the survey is the audit evidence"
    assert "no vetted component" in component["evidence"]["reason"]


def test_the_full_resolution_order_is_the_owner_order(stack) -> None:
    """owner step 3 == trail steps 3+4; owner step 4 == trail step 5."""
    _, _, detector = stack
    trail = detector.detect(make_request()).trail
    assert [entry["step"] for entry in trail] == [
        "request_received",
        "existing_capability",
        "composition",
        "configuration",
        "extension",
        "component_adaptation",
        "new_skill",
        "product_core_change",
    ]
