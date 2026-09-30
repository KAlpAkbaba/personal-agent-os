"""Unit tests: app.narrative.narrator / auditor / service."""

from __future__ import annotations

import re

import pytest

from app.narrative.auditor import audit, repair
from app.narrative.collector import Period, collect
from app.narrative.facts import FactEvent, NarrativeFacts
from app.narrative.narrator import RuleNarrator
from app.narrative.service import tell
from tests.unit.test_narrative_collector import FAIL_MAIL, FAIL_RESEARCH, NOW, make_session, seed

PERIODS = [
    ("bu hafta", None),
    ("dün", None),
    ("bugün", None),
    ("bu hafta", "ofiste"),
    ("bu hafta", "bulut"),
    ("bu hafta", "ev"),
    ("bu hafta", "kütüphane"),
]


@pytest.fixture()
def db():
    s = make_session()
    seed(s)
    yield s
    s.close()


@pytest.fixture()
def facts(db):
    return collect(db, "bu hafta", now=NOW)


def test_a_narrative_that_omits_one_failure_is_rejected(facts):
    text = f"Başarısız: {FAIL_RESEARCH}. Araştırma, tarayıcı ve posta işleri tamamlandı."
    verdict = audit(facts, text)
    assert not verdict.ok and len(verdict.missing_failures) == 1
    assert FAIL_MAIL in verdict.missing_failures[0]


def test_a_narrative_that_names_every_failure_is_not_rejected_for_failures(facts):
    text = f"{FAIL_RESEARCH}. {FAIL_MAIL}. Araştırma, tarayıcı ve posta."
    assert audit(facts, text).missing_failures == ()


def test_repair_appends_the_missing_failure_and_then_passes(facts):
    text = f"{FAIL_RESEARCH}. Araştırma, tarayıcı ve posta işleri tamamlandı."
    fixed = repair(facts, text)
    assert fixed.startswith(text) and FAIL_MAIL in fixed
    assert audit(facts, fixed).ok


def test_repair_leaves_a_complete_narrative_untouched(facts):
    good = RuleNarrator().tell(facts)
    assert repair(facts, good) == good


def test_a_narrative_missing_a_completed_subsystem_is_rejected(facts):
    text = RuleNarrator().tell(facts).replace("tarayıcı", "x")
    assert audit(facts, text).missing_subsystems == ("browser",)


def test_a_number_that_is_not_a_fact_is_rejected(facts):
    text = RuleNarrator().tell(facts) + " Toplam 47 olay oldu."
    verdict = audit(facts, text)
    assert not verdict.ok and verdict.foreign_numbers == ("47",)


def test_a_number_that_is_a_fact_count_is_accepted(facts):
    assert audit(facts, RuleNarrator().tell(facts) + " Toplam 5.").foreign_numbers == ()


def test_a_number_inside_a_failure_summary_is_not_foreign():
    ev = FactEvent("1", NOW, "research", "research.failed", "3 kaynak reddedildi", None, "ev")
    f = NarrativeFacts(
        Period(NOW, NOW, "x"), None, (ev,), (), (("research", 1),), (("ev", 1),), (), 1
    )
    assert audit(f, RuleNarrator().tell(f)).ok


@pytest.mark.parametrize(("period", "device"), PERIODS)
def test_the_rule_narrators_output_passes_the_auditor_for_every_fixture(db, period, device):
    f = collect(db, period, device, now=NOW)
    text = RuleNarrator().tell(f)
    assert audit(f, text).ok, text


def test_an_empty_period_is_told_as_no_records(db):
    f = collect(db, "bu hafta", "kütüphane", now=NOW)
    assert RuleNarrator().tell(f) == "bu dönemde kayıt yok"
    assert audit(f, "bu dönemde kayıt yok").ok


def test_failures_are_spoken_before_what_was_done(facts):
    text = RuleNarrator().tell(facts)
    assert text.index(FAIL_RESEARCH) < text.index("tamamlandı")


def test_the_narration_is_about_thirty_seconds_of_speech(facts):
    assert 15 <= len(re.findall(r"\w+", RuleNarrator().tell(facts))) <= 90


def test_tell_names_every_failure_in_turkish_text(db):
    text = tell(db, "bu hafta", now=NOW)
    assert FAIL_RESEARCH in text and FAIL_MAIL in text and "başarısız" in text


class _Omitting:
    def tell(self, facts):
        return "Her şey yolunda."


class _Inventing:
    def tell(self, facts):
        return RuleNarrator().tell(facts) + " Toplam 127 olay oldu."


def test_tell_repairs_a_narrator_that_drops_failures(db):
    text = tell(db, "bu hafta", narrator=_Omitting(), now=NOW)
    assert FAIL_RESEARCH in text and FAIL_MAIL in text


def test_tell_falls_back_to_the_rule_text_when_a_narrator_invents_a_number(db):
    text = tell(db, "bu hafta", narrator=_Inventing(), now=NOW)
    assert "127" not in text
    assert audit(collect(db, "bu hafta", now=NOW), text).ok
