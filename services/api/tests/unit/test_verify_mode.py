"""Doğrula modu (card verify-mode, d20261006): "bunu doğrula: ..." -> a claim, a verdict
DOĞRU | YANLIŞ | KISMEN | BELİRSİZ, 2-5 sources, the strongest counter-argument; recalled
later by text and by date.

The two voice tools live in the research family (``research.verify`` starts a crawl the same
way ``research.start`` does; ``research.verify_recall`` reads the owner's own records back).
Every registered tool must carry a step-up tier (``app.security.step_up._TIERS``,
``test_voice_step_up.test_every_registered_tool_has_a_tier``), so the tiers are decided here
first. The verdict core (``app.research.verify``) is pure and tested on fixture evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.research import verify
from app.research.verify import VerificationRecord, VerifySource
from app.security import step_up

TOOL_VERIFY = "research.verify"
TOOL_VERIFY_RECALL = "research.verify_recall"

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


def test_the_verify_tools_have_a_decided_tier() -> None:
    """A verify starts a crawl on an owner device - the same tier ``research.start`` has.
    Reading back what was verified changes nothing - OPEN, like ``memory.search``."""
    assert step_up._TIERS.get(TOOL_VERIFY) == step_up.TIER_SENSITIVE
    assert step_up._TIERS.get(TOOL_VERIFY_RECALL) == step_up.TIER_OPEN


def _src(
    stance: str, n: int = 1, *, year: int = 2026, quote: str = "karar veren cümle"
) -> VerifySource:
    return VerifySource(
        url=f"https://ornek{n}.example/haber",
        title=f"Kaynak {n}",
        published_at=datetime(year, 9, 1, tzinfo=UTC),
        quote=quote,
        stance=stance,
    )


# --- claim -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "claim"),
    [
        ("Bunu doğrula: Ay'ın yüzeyinde su buzu var.", "Ay'ın yüzeyinde su buzu var"),
        (
            "şunu kontrol et: asgari ücret 2026'da iki kez arttı",
            "asgari ücret 2026'da iki kez arttı",
        ),
        ("Everest'in 8849 metre olduğu doğru mu?", "Everest'in 8849 metre olduğu"),
        ("doğrula   Türkiye'nin başkenti Ankara", "Türkiye'nin başkenti Ankara"),
    ],
)
def test_the_claim_is_the_sentence_without_the_trigger(said: str, claim: str) -> None:
    assert verify.normalise_claim(said) == claim


def test_the_year_a_claim_names_is_read() -> None:
    assert verify.claim_subject_year("asgari ücret 2026'da iki kez arttı") == 2026
    assert verify.claim_subject_year("Ay'da su buzu var") is None


# --- verdict -----------------------------------------------------------------------------


def test_supporting_sources_only_is_dogru() -> None:
    v = verify.decide_verdict([_src("supports", 1), _src("supports", 2), _src("supports", 3)])
    assert v.code == verify.VERDICT_TRUE
    assert v.label == "DOĞRU"
    assert 0.5 < v.confidence <= 1.0


def test_refuting_sources_only_is_yanlis() -> None:
    v = verify.decide_verdict([_src("refutes", 1), _src("refutes", 2)])
    assert v.code == verify.VERDICT_FALSE
    assert v.label == "YANLIŞ"


def test_mixed_sources_is_kismen_with_lower_confidence() -> None:
    mixed = verify.decide_verdict([_src("supports", 1), _src("supports", 2), _src("refutes", 3)])
    agreed = verify.decide_verdict([_src("supports", 1), _src("supports", 2), _src("supports", 3)])
    assert mixed.code == verify.VERDICT_PARTLY
    assert mixed.label == "KISMEN"
    assert mixed.confidence < agreed.confidence


def test_one_source_never_reads_as_certain() -> None:
    assert verify.decide_verdict([_src("supports")]).confidence <= 0.5


def test_no_source_is_belirsiz_never_a_guess() -> None:
    """ADR-0063: no decisive source -> BELİRSİZ with confidence 0, whatever the rest says."""
    for sources in (
        [],
        [_src("neutral", 1), _src("neutral", 2)],
        [_src("supports", 1, quote="  ")],
    ):
        v = verify.decide_verdict(sources)
        assert v.code == verify.VERDICT_UNKNOWN
        assert v.label == "BELİRSİZ"
        assert v.confidence == 0.0


def test_the_kept_sources_are_at_most_five_decisive_ones() -> None:
    sources = [_src("supports", i) for i in range(1, 8)] + [_src("neutral", 9)]
    kept = verify.select_sources(sources)
    assert len(kept) == 5
    assert all(s.stance != "neutral" for s in kept)


def test_the_counter_argument_is_the_strongest_opposite_source() -> None:
    sources = [
        _src("supports", 1),
        _src("supports", 2),
        _src("refutes", 3, quote="aksi yönde veri"),
    ]
    v = verify.decide_verdict(sources)
    counter = verify.counter_argument(v, sources)
    assert counter is not None
    assert counter.quote == "aksi yönde veri"
    assert (
        verify.counter_argument(verify.decide_verdict([_src("supports")]), [_src("supports")])
        is None
    )


def test_a_source_older_than_the_claims_subject_is_said() -> None:
    claim = "asgari ücret 2026'da iki kez arttı"
    sources = [_src("supports", 1, year=2025), _src("supports", 2, year=2026)]
    v = verify.decide_verdict(sources)
    spoken = verify.spoken_sentence(claim, v, sources)
    assert "eski" in spoken
    fresh = verify.spoken_sentence(claim, v, [_src("supports", 2, year=2026)])
    assert "eski" not in fresh


def test_the_spoken_sentence_names_the_verdict_and_a_source() -> None:
    sources = [_src("supports", 1), _src("supports", 2)]
    spoken = verify.spoken_sentence("Ay'da su buzu var", verify.decide_verdict(sources), sources)
    assert "doğru" in spoken
    assert "Kaynak 1" in spoken
    assert spoken.count(".") <= 3


def test_the_spoken_sentence_without_sources_says_so() -> None:
    spoken = verify.spoken_sentence("Ay'da su buzu var", verify.decide_verdict([]), [])
    assert "belirsiz" in spoken
    assert "kaynak bulamadım" in spoken


# --- recall ------------------------------------------------------------------------------


def _record(claim: str, day: int, month: int = 9) -> VerificationRecord:
    sources = [_src("supports", 1), _src("supports", 2)]
    return VerificationRecord(
        said=f"bunu doğrula: {claim}",
        claim=claim,
        verdict=verify.decide_verdict(sources),
        sources=tuple(sources),
        created_at=datetime(2026, month, day, 12, tzinfo=UTC),
    )


RECORDS = [
    _record("Ay'da su buzu var", 29),
    _record("İstanbul'un nüfusu 16 milyonu geçti", 30),
    _record("asgari ücret iki kez arttı", 5, month=10),
]


def test_recall_by_text_matches_words_turkish_folded() -> None:
    found = verify.recall(RECORDS, text="istanbul nüfus")
    assert [r.claim for r in found] == ["İstanbul'un nüfusu 16 milyonu geçti"]
    assert verify.recall(RECORDS, text="mars") == []


def test_recall_by_date_window() -> None:
    since, until = verify.recall_window("geçen hafta neyi doğrulamıştık", NOW)
    found = verify.recall(RECORDS, since=since, until=until)
    assert {r.claim for r in found} == {"Ay'da su buzu var", "İstanbul'un nüfusu 16 milyonu geçti"}
    since, until = verify.recall_window("bu hafta ne doğruladık", NOW)
    assert [r.claim for r in verify.recall(RECORDS, since=since, until=until)] == [
        "asgari ücret iki kez arttı"
    ]
    assert verify.recall_window("dünya hakkında ne bulmuştuk", NOW) == (None, None)


def test_recall_newest_first() -> None:
    assert [r.created_at.day for r in verify.recall(RECORDS)] == [5, 30, 29]
