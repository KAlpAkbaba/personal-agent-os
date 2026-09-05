"""The six questions the M17 combined qualification asks, and the defects that hid.

The owner's integrated qualification is one short conversation. These tests pin the two
things that conversation depends on: that each question REACHES the subsystem it is about,
and that each answer degrades to an honest "I have no record" when that subsystem has
nothing to say.

Two defects found by rehearsing it on 2026-09-05 are pinned here, because both were silent:

* ``LedgerEvidenceSource.lessons`` called ``app.experience.service.list_lessons``, which
  has never existed, and ``opportunities`` called a SERVICE METHOD with the wrong keyword.
  Both raised on every call; both were swallowed by a defensive ``except``. The owner was
  told "henüz kayda geçmiş bir ders çıkarmadım" while the table held two compiled lessons.
  A wiring bug that impersonates an honest absence is worse than a crash.
* the engine compared opportunity status against ``"SHADOW_READY"`` while the enum stores
  ``"shadow_ready"``, so every comparison was false and "gece kendi üzerinde ne
  geliştirdin?" answered "sıfır" with a real shadow-ready candidate one query away.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.explain.classify import (
    LEVEL_EXECUTIVE,
    QUERY_CAN_DEPLOY,
    QUERY_EVOLUTION,
    QUERY_GOALS,
    QUERY_LEARNED,
    QUERY_SELF_CODE,
    QUERY_WORLD_STATE,
    classify,
)
from app.explain.engine import explain, speech_for_level

# The owner's six questions, exactly as the qualification asks them, with the ASCII
# spellings an ASR may return when it drops diacritics.
QUESTIONS: list[tuple[str, str]] = [
    ("Son yaşadığın hatalardan ne öğrendin?", QUERY_LEARNED),
    ("Son yasadigin hatalardan ne ogrendin?", QUERY_LEARNED),
    ("Şu anda hangi hedeflerin var?", QUERY_GOALS),
    ("Su anda hangi hedeflerin var?", QUERY_GOALS),
    ("Kendi sisteminde şu anda ne görüyorsun?", QUERY_WORLD_STATE),
    ("Kendi sisteminde su anda ne goruyorsun?", QUERY_WORLD_STATE),
    ("Kendi kodun hakkında ne biliyorsun?", QUERY_SELF_CODE),
    ("Kendi kodun hakkinda ne biliyorsun?", QUERY_SELF_CODE),
    ("Gece kendi üzerinde ne geliştirdin?", QUERY_EVOLUTION),
    ("Gece kendi uzerinde ne gelistirdin?", QUERY_EVOLUTION),
    ("Bunu canlıya alabilir misin?", QUERY_CAN_DEPLOY),
    ("Bunu canliya alabilir misin?", QUERY_CAN_DEPLOY),
]


class BareSource:
    """A source with no M17 subsystems at all - an older deployment."""

    def events(self, **_kwargs: Any) -> list[Any]:
        return []

    def research_report(self, task_id: str) -> None:
        return None

    def open_incidents(self) -> list[Any]:
        return []


class RichSource(BareSource):
    """Real-shaped rows, in the shape the production accessors return."""

    def lessons(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "lesson_id": "les-1",
                "title": "Kabul kanıtı yapısal olmalı, ifade değil",
                "statement": "Kabul kontrolü cümlenin kurulduğu yapıya bakmalı.",
                "root_cause": "Kabul kontrolü üretilen dile bağlanmıştı.",
                "resolution": "Yapısal provenance eklendi.",
                "status": "candidate",
                "score": 0.59,
                "recurrence": 2,
                "confidence": 0.6,
            },
            # the SAME statement again: two incidents of one defect class
            {
                "lesson_id": "les-2",
                "title": "Kabul kanıtı yapısal olmalı, ifade değil",
                "statement": "Kabul kontrolü cümlenin kurulduğu yapıya bakmalı.",
                "status": "candidate",
                "score": 0.59,
                "recurrence": 2,
                "confidence": 0.6,
            },
        ]

    def procedural_memories(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def opportunities(
        self, *, statuses: tuple[str, ...] | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = [
            {
                "opportunity_id": "opp-1",
                "title": "Acceptance Wording Guard",
                "statement": "Kabul kontrolleri yapıya dayanmalı.",
                # lowercase, as OpportunityStatus actually stores it
                "status": "shadow_ready",
            }
        ]
        if statuses:
            rows = [r for r in rows if r["status"] in statuses]
        return rows

    def goals(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def world_state(self) -> dict[str, Any]:
        return {
            "observed_at": "2026-09-05T15:00:00Z",
            "facts": [
                {"key": "cloud_core.version", "value": "0.1.0", "truth_kind": "source_truth"},
                {"key": "agent.installed", "value": "0.4.0", "truth_kind": "installed_truth"},
                {
                    "key": "worker.running",
                    "value": "0.3.9",
                    "truth_kind": "runtime_truth",
                    "stale": True,
                },
                {"key": "research.qualified", "value": True, "truth_kind": "evidence_truth"},
            ],
            "uncertainties": [
                {"category": "devices", "subject": "phone", "reason": "never_enrolled"}
            ],
        }

    def code_overview(self, *, limit: int = 8) -> dict[str, Any]:
        return {
            "module_count": 697,
            "modules": [
                {
                    "module_id": "app.explain.engine",
                    "owner_area": "explain",
                    "production_state": "source_only",
                    "purpose": "Kanıt önce, sonra cümle.",
                    "adr_refs": ["ADR-0051"],
                }
            ],
        }

    def authority_policy(self) -> dict[str, Any]:
        return {
            "root_policies": [
                {"policy_id": "deployment_authority", "title": "Dağıtım yetkisi", "statement": "x"}
            ],
            "production_actions": ["deploy_release", "sign_release"],
            "lab_grants": ["propose_candidate"],
            "production_grants": ["deploy"],
            "lab_holds_any_production_grant": False,
        }


# ------------------------------------------------------------------ routing


@pytest.mark.parametrize(("question", "expected"), QUESTIONS)
def test_each_question_reaches_its_subsystem(question: str, expected: str) -> None:
    assert classify(question).kind == expected, question


def test_a_general_night_question_is_still_a_general_briefing() -> None:
    """"Gece kendi üzerinde ne geliştirdin" is an Evolution question; "gece ne oldu" is
    not. The evolution rules must not swallow the absence briefing."""
    assert classify("Gece ne oldu?").kind == "since_you_left"
    assert classify("Ben yokken neler oldu?").kind == "since_you_left"


# ------------------------------------------------------------------ honest absence


@pytest.mark.parametrize(("question", "_kind"), QUESTIONS)
def test_every_question_degrades_honestly_with_no_subsystem(question: str, _kind: str) -> None:
    """An older deployment has none of these tables. Each answer must say so, in Turkish,
    and must never be empty - silence is indistinguishable from a broken tool."""
    briefing = explain(BareSource(), question, classify(question))
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert speech.strip(), question
    assert briefing.executive, question


# ------------------------------------------------------------------ real answers


def test_lessons_are_spoken_once_each_and_in_turkish() -> None:
    question = "Son yaşadığın hatalardan ne öğrendin?"
    briefing = explain(RichSource(), question, classify(question))
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert "ders adayım var" in speech
    # the identical second lesson must not be read again
    assert speech.count("cümlenin kurulduğu yapıya bakmalı") == 1
    assert len(speech) <= 420, f"executive budget: {len(speech)} chars"


def test_the_shadow_ready_candidate_is_found_despite_lowercase_status() -> None:
    """The regression: status comparison was case-sensitive against uppercase literals."""
    question = "Gece kendi üzerinde ne geliştirdin?"
    briefing = explain(RichSource(), question, classify(question))
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert "sıfır" not in speech.lower(), "a real shadow-ready candidate was reported as zero"
    assert "gölge" in speech
    assert "canlı" in speech.lower(), "it must say it is NOT live"


def test_the_world_model_keeps_the_four_truths_apart_and_admits_staleness() -> None:
    question = "Kendi sisteminde şu anda ne görüyorsun?"
    briefing = explain(RichSource(), question, classify(question))
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    for phrase in ("kaynak kodda", "kurulu olan", "çalışan", "kanıtlı"):
        assert phrase in speech, phrase
    assert "emin değilim" in speech, "uncertainty must be stated, never guessed"
    assert "bayat" in speech, "a stale observation must be admitted as stale"


def test_the_self_model_reports_a_real_module_count() -> None:
    question = "Kendi kodun hakkında ne biliyorsun?"
    briefing = explain(RichSource(), question, classify(question))
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert "modül tanıyorum" in speech
    assert briefing.detailed, "the modules themselves belong in the detailed level"


def test_the_authority_answer_is_a_refusal_with_its_reason() -> None:
    question = "Bunu canlıya alabilir misin?"
    briefing = explain(RichSource(), question, classify(question))
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert speech.startswith("Hayır"), "the answer to 'can you deploy this' is no"
    assert "onayınızı" in speech, "owner approval must be named as the requirement"
    assert "Acceptance Wording Guard" in speech, "and the real waiting candidate named"
    assert "canlıda değil" in speech


def test_no_goals_is_answered_as_no_goals() -> None:
    """The qualification must not be made to pass by inventing a goal."""
    question = "Şu anda hangi hedeflerin var?"
    briefing = explain(RichSource(), question, classify(question))
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert "hedefim yok" in speech


# ------------------------------------------------- the accessors that silently failed


def test_the_evidence_source_names_the_tables_it_reads() -> None:
    """Both broken accessors called helpers that do not exist. Pin the real ones."""
    import inspect

    from app.explain.service import LedgerEvidenceSource

    source = inspect.getsource(LedgerEvidenceSource)
    assert "ExperienceLessonRow" in source, "lessons must read the real model"
    assert "EvolutionOpportunity" in source, "opportunities must read the real model"
    # Assert what the code DOES, not what it avoids. Both docstrings deliberately name the
    # broken helpers to explain the defect, so every "must not appear" assertion here
    # matched its own explanation - three attempts in a row on 2026-09-05. A positive
    # assertion cannot be fooled by a comment.
    assert "select(ExperienceLessonRow)" in inspect.getsource(LedgerEvidenceSource.lessons)
    assert "select(EvolutionOpportunity)" in inspect.getsource(LedgerEvidenceSource.opportunities)


def test_a_broken_read_is_logged_rather_than_reported_as_absence() -> None:
    import inspect

    from app.explain.service import LedgerEvidenceSource

    source = inspect.getsource(LedgerEvidenceSource._rows)
    assert "OperationalError" in source and "ProgrammingError" in source, (
        "a missing table is a genuine absence and must be distinguished"
    )
    assert "logger.warning" in source, (
        "anything else is a bug in this file and must not masquerade as an honest absence"
    )


# ------------------------------------------- the persistence path, which is what crashed


@pytest.mark.parametrize(("question", "_kind"), QUESTIONS)
def test_every_answer_survives_being_persisted(question: str, _kind: str) -> None:
    """``as_dict()`` is what the voice tool stores, and it is where the failure happened.

    The world-model branch shadowed the briefing's ``facts`` accumulator with a local list,
    so ``provenance()`` called ``dict(<list of 9-key dicts>)`` and raised a ValueError about
    a "dictionary update sequence". The owner heard "activity.explain failed" with error
    class ``internal_bug``. Nothing caught it because these tests only ever called
    ``speech_for_level`` - they exercised the sentence and never the record (2026-09-05).
    """
    for source in (BareSource(), RichSource()):
        briefing = explain(source, question, classify(question))
        record = briefing.as_dict()  # must not raise
        assert isinstance(record["provenance"]["facts"], dict)
        assert record["cognition"]["query_kind"] == briefing.query.kind


def test_a_branch_that_shadows_the_facts_accumulator_is_refused_at_construction() -> None:
    """The guard fails where the briefing is BUILT, not deep inside persistence."""
    from datetime import UTC, datetime

    from app.explain.engine import Briefing

    with pytest.raises(TypeError, match="shadowed"):
        Briefing(
            question="q",
            query=classify("Son yaptıklarını anlat"),
            generated_at=datetime.now(UTC),
            executive=(),
            detailed=(),
            technical=(),
            evidence_refs=(),
            facts=[{"a": 1}],  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(("question", "expected"), QUESTIONS)
def test_the_routing_record_names_the_subsystem_that_answered(question: str, expected: str) -> None:
    """Routing is recorded by the engine that dispatched it, never inferred from Turkish."""
    from app.explain.engine import SUBSYSTEM_FOR_QUERY

    briefing = explain(RichSource(), question, classify(question))
    cognition = briefing.cognition()
    assert cognition["query_kind"] == expected
    assert cognition["subsystem"] == SUBSYSTEM_FOR_QUERY[expected]
    assert set(cognition) >= {
        "query_kind",
        "subsystem",
        "facts",
        "uncertainties",
        "evidence_count",
        "evidence_kinds",
        "entity_ids",
        "research_job_id",
    }


def test_a_cognitive_answer_is_not_required_to_cite_a_research_job() -> None:
    """Research provenance belongs to research answers. Requiring it everywhere would be
    demanding provenance from the wrong subsystem (owner direction, 2026-09-05)."""
    for question in ("Kendi kodun hakkında ne biliyorsun?", "Bunu canlıya alabilir misin?"):
        cognition = explain(RichSource(), question, classify(question)).cognition()
        assert cognition["research_job_id"] is None
        # ...but it must still say what it DID rest on
        assert cognition["facts"] >= 1 or cognition["uncertainties"] >= 1
