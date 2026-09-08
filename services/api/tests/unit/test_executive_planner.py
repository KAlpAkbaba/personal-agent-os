"""``app.executive.planner`` (spec §2): the deterministic RuleBasedExecutivePlanner's
three shapes, and the inert model seam.
"""

from __future__ import annotations

import pytest

from app.executive.graph import validate_graph
from app.executive.planner import (
    SHAPE_FOLDER_COMPARE,
    SHAPE_MAIL_THREAD,
    SHAPE_RESEARCH,
    ClaudeExecutivePlanner,
    PlanningClarificationNeeded,
    RuleBasedExecutivePlanner,
)
from app.executive.spec import (
    STEP_KIND_ARTIFACTS_CREATE,
    STEP_KIND_CALENDAR_PROPOSE,
    STEP_KIND_DOCUMENTS_COMPARE,
    STEP_KIND_DOCUMENTS_FIND,
    STEP_KIND_MAIL_ANALYZE_THREAD,
    STEP_KIND_MAIL_DRAFT,
    STEP_KIND_RESEARCH_RUN,
    STEP_KIND_RESEARCH_SYNTHESIZE,
    STEP_KIND_SYNTHESIS,
)

_RESEARCH_DIRECTIVE = (
    "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, Word raporu ve sunum hazırla."
)
_FOLDER_DIRECTIVE = "Bu klasördeki teklifleri karşılaştır, Excel oluştur ve yönetici özeti hazırla."
_MAIL_DIRECTIVE = "Bu mail zincirini analiz et, ilgili dosyaları bul ve cevap taslağı hazırla."


def test_research_shape_produces_the_spec_pipeline() -> None:
    graph = RuleBasedExecutivePlanner().plan(_RESEARCH_DIRECTIVE)
    kinds = [s.kind for s in graph.steps]
    assert kinds == [
        STEP_KIND_RESEARCH_RUN,
        STEP_KIND_RESEARCH_SYNTHESIZE,
        STEP_KIND_ARTIFACTS_CREATE,
        STEP_KIND_ARTIFACTS_CREATE,  # presentation, "sunum" was said
        STEP_KIND_SYNTHESIS,
    ]
    validate_graph(graph)


def test_research_shape_without_presentation_word_skips_the_slide_step() -> None:
    graph = RuleBasedExecutivePlanner().plan(
        "AI gelişmelerini araştır ve bana bir Word raporu hazırla."
    )
    kinds = [s.kind for s in graph.steps]
    assert STEP_KIND_ARTIFACTS_CREATE in kinds
    assert kinds.count(STEP_KIND_ARTIFACTS_CREATE) == 1
    validate_graph(graph)


def test_folder_compare_shape_produces_the_spec_pipeline() -> None:
    graph = RuleBasedExecutivePlanner().plan(_FOLDER_DIRECTIVE)
    kinds = [s.kind for s in graph.steps]
    assert kinds == [
        STEP_KIND_DOCUMENTS_FIND,
        STEP_KIND_DOCUMENTS_COMPARE,
        STEP_KIND_ARTIFACTS_CREATE,  # spreadsheet
        STEP_KIND_ARTIFACTS_CREATE,  # summary document
        STEP_KIND_SYNTHESIS,
    ]
    validate_graph(graph)


def test_mail_thread_shape_produces_the_spec_pipeline() -> None:
    graph = RuleBasedExecutivePlanner().plan(_MAIL_DIRECTIVE)
    kinds = [s.kind for s in graph.steps]
    assert kinds == [
        STEP_KIND_MAIL_ANALYZE_THREAD,
        STEP_KIND_DOCUMENTS_FIND,
        STEP_KIND_MAIL_DRAFT,
        STEP_KIND_SYNTHESIS,
    ]
    validate_graph(graph)
    # Never mail.send/calendar.commit — the closed vocabulary carries no such kind at
    # all (spec §1, §8): the draft step is where this shape ends.
    assert STEP_KIND_CALENDAR_PROPOSE not in kinds


def test_mail_thread_shape_never_produces_a_send() -> None:
    """Structurally unreachable, not merely asserted: the closed vocabulary
    (app.executive.spec.STEP_KIND_PROFILES) simply has no send/commit kind for any
    planner to emit."""
    from app.executive.spec import STEP_KIND_PROFILES

    assert "mail.send" not in STEP_KIND_PROFILES
    assert "calendar.commit" not in STEP_KIND_PROFILES


def test_deterministic_same_directive_same_graph() -> None:
    planner = RuleBasedExecutivePlanner()
    graph_a = planner.plan(_RESEARCH_DIRECTIVE)
    graph_b = planner.plan(_RESEARCH_DIRECTIVE)
    assert graph_a.canonical_json() == graph_b.canonical_json()


def test_unrecognised_directive_asks_for_clarification() -> None:
    with pytest.raises(PlanningClarificationNeeded):
        RuleBasedExecutivePlanner().plan("Bana yardım eder misin?")


def test_empty_directive_asks_for_clarification() -> None:
    with pytest.raises(PlanningClarificationNeeded):
        RuleBasedExecutivePlanner().plan("")


def test_folder_slot_is_carried_when_known() -> None:
    graph = RuleBasedExecutivePlanner().plan(_FOLDER_DIRECTIVE, folder="Desktop")
    find_step = graph.steps[0]
    assert find_step.inputs["folder"] == "Desktop"


def test_folder_slot_defaults_to_current_when_not_named() -> None:
    graph = RuleBasedExecutivePlanner().plan(_FOLDER_DIRECTIVE, folder=None)
    find_step = graph.steps[0]
    assert find_step.inputs["folder"] == "current"


@pytest.mark.parametrize(
    ("directive", "shape"),
    [
        (_RESEARCH_DIRECTIVE, SHAPE_RESEARCH),
        (_FOLDER_DIRECTIVE, SHAPE_FOLDER_COMPARE),
        (_MAIL_DIRECTIVE, SHAPE_MAIL_THREAD),
    ],
)
def test_shape_tokens_are_distinct_and_correct(directive: str, shape: str) -> None:
    from app.voice.intents import resolve_intent

    resolved = resolve_intent(directive)
    assert resolved.exec_shape == shape


def test_claude_planner_is_inert() -> None:
    with pytest.raises(NotImplementedError):
        ClaudeExecutivePlanner().plan(_RESEARCH_DIRECTIVE)
