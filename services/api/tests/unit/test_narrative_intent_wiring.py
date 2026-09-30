"""Unit tests: the narrative reaches the owner through the ONE router (ROADMAP order 2c).

``resolve_intent`` is pure, so the narrative is a READ routed as ``Intent.EXPLAIN`` with
``query_kind == "narrative"``; the explain engine answers that kind with
``app.narrative.service.tell`` through an evidence source's optional ``narrative`` method
(the narrator is ModelNarrator over the router's chat provider, RuleNarrator without one).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.assistant_chat import ChatAnswer
from app.explain.classify import ExplainQuery
from app.explain.engine import (
    QUERY_NARRATIVE,
    explain,
    narrative_query,
    speech_for_level,
)
from app.narrative.intent import recognise
from app.narrative.model_narrator import ModelNarrator
from app.narrative.service import tell
from app.voice.intents import Intent, klass_for, resolve_intent
from tests.unit.test_narrative_collector import FAIL_MAIL, FAIL_RESEARCH, NOW, make_session, seed

NARRATIVE_KIND = "narrative"


# --------------------------------------------------------------------------- fakes


class _Provider:
    """A chat provider whose draft is what the test says (``None``: there is no provider)."""

    def __init__(self, speech: str) -> None:
        self.speech = speech
        self.calls = 0

    def answer(self, text, *, history, now_tr):
        self.calls += 1
        return ChatAnswer(self.speech, True, None)


class _Source:
    """An EvidenceSource stand-in: the ledger is a real (sqlite) ledger, the narrator is what
    the wiring would pick - ModelNarrator over a provider, RuleNarrator without one."""

    def __init__(self, db, provider=None) -> None:
        self._db = db
        self._provider = provider
        self.asked = []

    def events(self, *, since, subsystems, statuses, limit):
        return []

    def research_report(self, task_id):
        return None

    def open_incidents(self):
        return []

    def narrative(self, ask, *, now):
        self.asked.append(ask)
        narrator = ModelNarrator(self._provider) if self._provider is not None else None
        return tell(self._db, ask.period, ask.device, narrator, now=now)


@pytest.fixture()
def db():
    s = make_session()
    seed(s)
    yield s
    s.close()


def _answer(source, utterance: str) -> str:
    """The whole path the voice tool takes: route, then the engine's answer for that kind."""
    routed = resolve_intent(utterance)
    assert routed.intent is Intent.EXPLAIN, routed
    assert routed.query_kind == NARRATIVE_KIND
    query = narrative_query(utterance, now=NOW)
    assert query is not None and query.kind == QUERY_NARRATIVE
    briefing = explain(source, utterance, query, now=NOW)
    return speech_for_level(briefing, query.level)


# --------------------------------------------------------------------------- the router


@pytest.mark.parametrize(
    "utterance",
    [
        "bu hafta ne oldu",
        "Bu hafta ne oldu?",
        "dün ne oldu",
        "ofiste bu hafta ne oldu",
        "evde ne oldu",
        "bu hafta ofiste ne yapıldı",
    ],
)
def test_the_router_takes_a_what_happened_question_as_a_narrative_read(utterance):
    routed = resolve_intent(utterance)
    assert routed.intent is Intent.EXPLAIN
    assert routed.query_kind == NARRATIVE_KIND
    assert klass_for(routed.intent) == "query"  # the read class: no step-up, no device command


@pytest.mark.parametrize(
    "utterance",
    [
        "hafta sonu ne yapalım",  # a plan
        "geçen hafta ne oldu",  # a window nobody resolves
        "dün ne yaptım",  # the owner's own doing
        "ne oldu",  # too bare
        "bu hafta sonu ne oldu",
    ],
)
def test_a_near_miss_is_not_routed_as_a_narrative(utterance):
    assert resolve_intent(utterance).query_kind != NARRATIVE_KIND


# Every phrase here is ALSO a narrative ask by the recogniser alone; another intent already
# owns it, so the router must keep answering as it did before the narrative existed.
_OWNED = [
    ("bugün ne yaptın", Intent.ARTIFACT_LIST, None),
    ("dün ne yaptın", Intent.ARTIFACT_LIST, None),
    ("ofiste ne yaptın", Intent.ARTIFACT_LIST, None),
    ("bugün neler yaptın", Intent.ARTIFACT_LIST, None),
    ("bugün neler oldu", Intent.EXPLAIN, "today"),
    ("bugün ne oldu", Intent.EXPLAIN, "today"),
    ("ne başarısız oldu", Intent.EXPLAIN, "failures"),
    ("bu hafta ne başarısız oldu", Intent.EXPLAIN, "failures"),
    ("son yaptıkların neler", Intent.EXPLAIN, "last_activity"),
    ("hata varsa düzelt", Intent.EXPLAIN, "failures"),
]


@pytest.mark.parametrize(("utterance", "intent", "kind"), _OWNED)
def test_an_intent_another_family_owns_keeps_going_where_it_went(utterance, intent, kind):
    routed = resolve_intent(utterance)
    assert routed.intent is intent
    assert routed.query_kind == kind


def test_at_least_one_owned_phrase_is_also_a_narrative_ask():
    """The owned list is only a shadow test when the recogniser WOULD have taken a phrase."""
    shadowable = [u for u, _, _ in _OWNED if recognise(u) is not None]
    assert len(shadowable) >= 8, shadowable


def test_a_narrative_ask_never_reads_a_non_narrative_kind_for_its_question():
    assert narrative_query("bugün hava nasıl", now=NOW) is None
    assert narrative_query("hafta sonu ne yapalım", now=NOW) is None


# --------------------------------------------------------------------------- the answer


def test_bu_hafta_ne_oldu_answers_with_the_narrative_and_names_every_failure(db):
    source = _Source(db)
    spoken = _answer(source, "bu hafta ne oldu")
    assert spoken == tell(db, "bu hafta", None, None, now=NOW)
    assert FAIL_RESEARCH in spoken
    assert FAIL_MAIL in spoken
    assert [a.period for a in source.asked] == ["bu hafta"]


def test_the_device_word_narrows_the_narrative(db):
    spoken = _answer(_Source(db), "ofiste bu hafta ne oldu")
    assert FAIL_RESEARCH in spoken
    assert FAIL_MAIL not in spoken  # that failure is the home machine's


def test_with_a_provider_the_model_narrates_and_still_names_every_failure(db):
    provider = _Provider("Hafta yoğundu; kayıtlar iyi gitmedi.")  # drops both failures
    spoken = _answer(_Source(db, provider), "bu hafta ne oldu")
    assert provider.calls == 1
    assert FAIL_RESEARCH in spoken and FAIL_MAIL in spoken  # the auditor put them back


def test_without_a_provider_the_rule_narrator_answers(db):
    source = _Source(db, None)
    assert _answer(source, "bu hafta ne oldu") == tell(db, "bu hafta", None, now=NOW)


@pytest.mark.parametrize("level", ["executive", "detailed", "technical"])
def test_every_presentation_level_speaks_the_narrative(db, level):
    query = narrative_query("bu hafta ne oldu", now=NOW)
    query = ExplainQuery(
        kind=query.kind,
        level=level,
        since=query.since,
        subsystem=None,
        module=None,
        normalized=query.normalized,
        matched=True,
    )
    briefing = explain(_Source(db), "bu hafta ne oldu", query, now=NOW)
    assert FAIL_RESEARCH in speech_for_level(briefing, level)


def test_a_source_without_a_narrative_says_it_has_no_record_and_invents_nothing():
    class Bare(_Source):
        narrative = None  # type: ignore[assignment]

    query = narrative_query("bu hafta ne oldu", now=NOW)
    spoken = speech_for_level(explain(Bare(None), "bu hafta ne oldu", query, now=NOW), query.level)
    assert spoken == "Bu konuda kayıt bulamadım."


def test_the_briefing_is_labelled_a_fact_with_the_narrative_provenance(db):
    query = narrative_query("bu hafta ne oldu", now=NOW)
    briefing = explain(_Source(db), "bu hafta ne oldu", query, now=NOW)
    assert briefing.cognition()["query_kind"] == QUERY_NARRATIVE
    assert briefing.counts()["facts"] >= 1
    assert briefing.generated_at == NOW.astimezone(UTC) and isinstance(NOW, datetime)
