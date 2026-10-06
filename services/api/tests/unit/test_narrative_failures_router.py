"""Unit tests: the owner's "bu hafta ne başarısız oldu" reaches the narrative (ADR-0244 A).

Through the REAL router (``resolve_intent``) and the real ``explain_to_briefing`` - nothing
substituted. A failure question over a PERIOD, in the plural or about a named machine is the
narrative with ``failures_only=True``; the bare singular "ne başarısız oldu" and "en son ne
başarısız oldu" stay with the explain ``failures`` family (the latest failure).

The seeded ledger (``test_narrative_collector.seed``, NOW = 2026-09-30 12:00 UTC): the
research failure is yesterday's on the office machine, the mail failure is four days old on
the home machine, and nothing failed today.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, ArtifactVersion
from app.explain.engine import QUERY_NARRATIVE
from app.explain.service import LedgerEvidenceSource, explain_to_briefing
from app.ledger.models import ActivityEventRow
from app.narration.models import NarrationSession, PronunciationEntry
from app.narrative.service import NO_FAILURES_TEXT
from app.voice.intents import Intent, asr_fold, klass_for, resolve_intent
from tests.unit.test_narrative_collector import FAIL_MAIL, FAIL_RESEARCH, NOW, seed

TABLES = (ActivityEventRow, Artifact, ArtifactVersion, NarrationSession, PronunciationEntry)
FAILURES = "failures"

#: (the owner's sentence, measured 2026-10-02 going to ``failures``) -> period, device,
#: the failures the answer names, the ones it must not name.
MEASURED = [
    ("bu hafta ne başarısız oldu", "bu hafta", None, (FAIL_RESEARCH, FAIL_MAIL), ()),
    ("bugün ne başarısız oldu", "bugün", None, (), (FAIL_RESEARCH, FAIL_MAIL)),
    ("neler başarısız oldu", "bu hafta", None, (FAIL_RESEARCH, FAIL_MAIL), ()),
    ("dün neler başarısız oldu", "dün", None, (FAIL_RESEARCH,), (FAIL_MAIL,)),
    ("ofiste bu hafta ne başarısız oldu", "bu hafta", "ofis", (FAIL_RESEARCH,), (FAIL_MAIL,)),
    ("Ofiste ne başarısız oldu?", "bu hafta", "ofis", (FAIL_RESEARCH,), (FAIL_MAIL,)),
]


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for model in TABLES:
        model.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    seed(session)
    yield session
    session.close()


def _ascii(sentence: str) -> str:
    return " ".join(asr_fold(word) for word in sentence.lower().split())


# --------------------------------------------------------------------------- the router


@pytest.mark.parametrize(("utterance", "period", "device", "_named", "_not"), MEASURED)
def test_a_failure_question_over_a_period_is_the_narrative(utterance, period, device, _named, _not):
    routed = resolve_intent(utterance)
    assert routed.intent is Intent.EXPLAIN, routed
    assert routed.query_kind == QUERY_NARRATIVE, routed
    assert routed.matched == period
    assert klass_for(routed.intent) == "query"


@pytest.mark.parametrize(("utterance", "period", "device", "_named", "_not"), MEASURED)
def test_the_ascii_spelling_resolves_as_the_turkish_one(utterance, period, device, _named, _not):
    spelled = _ascii(utterance)
    assert spelled != utterance.lower()
    routed = resolve_intent(spelled)
    assert routed.intent is Intent.EXPLAIN
    assert routed.query_kind == QUERY_NARRATIVE
    assert routed.matched == period


@pytest.mark.parametrize(
    ("utterance", "kind"),
    [
        ("ne başarısız oldu", FAILURES),  # the bare singular stays where it was
        ("Ne başarısız oldu?", FAILURES),
        ("en son ne başarısız oldu", FAILURES),  # the latest one
        ("bu hafta en son ne başarısız oldu", FAILURES),  # "son" asks for the latest
        ("son hata neydi", "last_defect"),  # the latest defect, as before
        ("dünyada ne başarısız oldu", FAILURES),  # "dün" is a word, not a stem
        ("nelerden başarısız oldu", None),  # "neler" is a word, not a stem
    ],
)
def test_the_latest_failure_stays_with_the_failures_family(utterance, kind):
    routed = resolve_intent(utterance)
    assert routed.query_kind != QUERY_NARRATIVE, routed
    if kind is not None:
        assert routed.intent is Intent.EXPLAIN
        assert routed.query_kind == kind


@pytest.mark.parametrize(
    "utterance",
    [
        "dün sonuçta neler başarısız oldu",  # "sonuçta" is not "son"
    ],
)
def test_a_word_that_only_starts_like_son_does_not_ask_for_the_latest(utterance):
    routed = resolve_intent(utterance)
    assert routed.query_kind == QUERY_NARRATIVE, routed


@pytest.mark.parametrize(
    "utterance",
    [
        "başarısız olduğunda haber ver",
        "basarisiz oldugunda haber ver",
        "Bugün başarısız olduğunda haber ver.",
    ],
)
def test_a_sentence_that_merely_contains_the_words_is_neither(utterance):
    routed = resolve_intent(utterance)
    assert routed.query_kind not in (QUERY_NARRATIVE, FAILURES), routed


# --------------------------------------------------------------------------- the answer


@pytest.mark.parametrize(("utterance", "period", "device", "named", "not_named"), MEASURED)
def test_explain_to_briefing_tells_the_failures_of_that_period_only(
    db, utterance, period, device, named, not_named
):
    record = explain_to_briefing(db, utterance, now=NOW, source=LedgerEvidenceSource(db))
    assert record.briefing.query.kind == QUERY_NARRATIVE
    assert record.briefing.facts == {"period": period, "device": device, "failures_only": True}
    told = record.briefing.executive[0].text
    assert "Tamamlananlar" not in told
    for failure in named:
        assert failure in told
    for failure in not_named:
        assert failure not in told
    if not named:
        assert told == NO_FAILURES_TEXT


@pytest.mark.parametrize(("utterance", "period", "device", "_named", "_not"), MEASURED)
def test_explain_to_briefing_answers_the_ascii_spelling_alike(
    db, utterance, period, device, _named, _not
):
    record = explain_to_briefing(db, _ascii(utterance), now=NOW, source=LedgerEvidenceSource(db))
    assert record.briefing.query.kind == QUERY_NARRATIVE
    assert record.briefing.facts == {"period": period, "device": device, "failures_only": True}


@pytest.mark.parametrize("utterance", ["ne başarısız oldu", "en son ne başarısız oldu"])
def test_explain_to_briefing_keeps_the_latest_failure_answer(db, utterance):
    record = explain_to_briefing(db, utterance, now=NOW, source=LedgerEvidenceSource(db))
    assert record.briefing.query.kind == FAILURES
