"""Unit tests: "ne başarısız oldu" tells the failures ONLY (ROADMAP order 2c).

``only_failures`` narrows the facts BEFORE any narrator sees them, so the rule narrator, the
model narrator and the auditor are the ones every other narrative uses: a completed
subsystem is not in the facts, so no audited text is held to naming it and its counts are
foreign numbers.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.assistant_chat import ChatAnswer
from app.ledger.vocabulary import (
    EVENT_TYPE_BROWSER_SEARCH,
    STATUS_COMPLETED,
    SUBSYSTEM_BROWSER,
)
from app.narrative.auditor import audit
from app.narrative.collector import collect
from app.narrative.facts import only_failures, subsystem_label
from app.narrative.model_narrator import ModelNarrator
from app.narrative.narrator import RuleNarrator
from app.narrative.service import NO_FAILURES_TEXT, tell
from tests.unit.test_narrative_collector import (
    FAIL_MAIL,
    FAIL_RESEARCH,
    NOW,
    add_row,
    make_session,
    seed,
)


class _Provider:
    """A scripted, configured ChatProvider that counts how often it was asked."""

    name = "fake"
    configured = True

    def __init__(self, speech: str = "") -> None:
        self.speech = speech
        self.questions: list[str] = []

    def answer(self, question, *, history, now_tr, about_owner=""):
        self.questions.append(question)
        return ChatAnswer(self.speech, True, None)


@pytest.fixture()
def db():
    s = make_session()
    seed(s)
    yield s
    s.close()


@pytest.fixture()
def facts(db):
    return collect(db, "bu hafta", now=NOW)


def test_the_fixture_week_holds_two_failures_and_three_completed_rows(facts):
    assert len(facts.failed) == 2
    assert sum(len(evs) for _, evs in facts.completed) == 3
    assert facts.total == 5


# --------------------------------------------------------------------------- only_failures


def test_only_failures_keeps_the_failed_rows_and_drops_every_completed_one(facts):
    narrowed = only_failures(facts)
    assert narrowed.failed == facts.failed
    assert narrowed.no_capable_device == facts.no_capable_device
    assert narrowed.completed == ()
    assert narrowed.total == 2


def test_only_failures_counts_the_failed_rows_alone(facts):
    narrowed = only_failures(facts)
    assert narrowed.counts_by_subsystem == (("mail", 1), ("research", 1))
    assert narrowed.counts_by_device == (("ev", 1), ("ofis", 1))  # the cloud row was a draft


def test_only_failures_keeps_the_period_and_the_device(db):
    office = collect(db, "bu hafta", "ofiste", now=NOW)
    narrowed = only_failures(office)
    assert narrowed.covered == office.covered
    assert narrowed.device == "ofis"
    assert [e.summary for e in narrowed.failed] == [FAIL_RESEARCH]


def test_only_failures_is_pure(facts):
    before = dataclasses.replace(facts)
    first = only_failures(facts)
    assert facts == before, "the facts it was given are not changed"
    assert only_failures(facts) == first, "the same facts give an equal value"
    assert only_failures(first) == first, "narrowing what is already narrowed changes nothing"


def test_a_period_with_no_failure_narrows_to_empty_facts(db):
    today = collect(db, "bugün", now=NOW)
    assert today.total == 1 and not today.failed
    assert only_failures(today).is_empty


# --------------------------------------------------------------------------- tell


def test_failures_only_names_both_failures_and_no_completed_subsystem(db):
    text = tell(db, "bu hafta", None, None, now=NOW, failures_only=True)
    assert FAIL_RESEARCH in text and FAIL_MAIL in text
    assert "Tamamlananlar" not in text
    assert subsystem_label(SUBSYSTEM_BROWSER) not in text, "the browser only completed work"
    assert "Toplam 2 kayıt." in text
    assert "5" not in text and "3" not in text


def test_without_the_flag_the_narrative_still_tells_everything(db):
    text = tell(db, "bu hafta", None, None, now=NOW)
    assert text == tell(db, "bu hafta", None, None, now=NOW, failures_only=False)
    assert "Tamamlananlar" in text and "Toplam 5 kayıt." in text


def test_the_failures_only_text_passes_the_auditor_over_the_narrowed_facts(db, facts):
    text = tell(db, "bu hafta", None, None, now=NOW, failures_only=True)
    assert text == RuleNarrator().tell(only_failures(facts))
    assert audit(only_failures(facts), text).ok


def test_the_model_is_shown_the_failures_only(db):
    provider = _Provider(f"İki iş başarısız oldu: {FAIL_RESEARCH}; {FAIL_MAIL}.")
    text = tell(db, "bu hafta", None, ModelNarrator(provider), now=NOW, failures_only=True)
    assert text == provider.speech
    (prompt,) = provider.questions
    assert '"tamamlanan": []' in prompt
    assert '"toplam_kayıt": 2' in prompt
    assert "tarayıcı" not in prompt


def test_a_model_draft_that_counts_the_completed_rows_is_not_spoken(db, facts):
    """The whole week's total is not a fact of the failures: the auditor refuses it."""
    provider = _Provider(f"{FAIL_RESEARCH}; {FAIL_MAIL}. Tarayıcıda 5 iş de tamamlandı.")
    text = tell(db, "bu hafta", None, ModelNarrator(provider), now=NOW, failures_only=True)
    assert len(provider.questions) == 1
    assert text == RuleNarrator().tell(only_failures(facts))
    assert "Tarayıcıda" not in text


def test_no_failure_is_one_constant_sentence_and_the_model_is_never_called(db):
    provider = _Provider("Her şey yolunda gitti, 9 iş tamamlandı.")
    text = tell(db, "bugün", None, ModelNarrator(provider), now=NOW, failures_only=True)
    assert text == NO_FAILURES_TEXT == "Bu dönemde başarısız iş yok."
    assert provider.questions == []


def test_an_empty_period_asked_for_failures_says_the_same_constant_sentence():
    s = make_session()
    try:
        assert tell(s, "bu hafta", None, None, now=NOW, failures_only=True) == NO_FAILURES_TEXT
    finally:
        s.close()


def test_a_device_with_completed_work_only_has_no_failure_to_tell():
    s = make_session()
    try:
        add_row(
            s,
            "c1",
            NOW.replace(hour=9),
            EVENT_TYPE_BROWSER_SEARCH,
            SUBSYSTEM_BROWSER,
            STATUS_COMPLETED,
            "Sayfa açıldı",
            "ofis",
        )
        s.commit()
        assert tell(s, "bugün", "ofiste", None, now=NOW, failures_only=True) == NO_FAILURES_TEXT
        assert "tarayıcı" in tell(s, "bugün", "ofiste", None, now=NOW)
    finally:
        s.close()
