# ruff: noqa: E501 - the briefing fixture keeps its real one-line sentences
"""Where "dur" landed: aligning the spoken transcript with the plan (M16 §3.2)."""

from __future__ import annotations

from app.narration.align import align
from app.narration.engine import build_plan
from app.voice.intents import level_section_cursor, speech_from

BRIEFING = """# Özet

Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı. Beş sonuç ve beş farklı kaynak ürettim. Dokuz konu dışı sayfayı ve on bir ara doğrulama sayfasını eledim. Tarayıcı temiz şekilde kapandı. Bilginize.

# Ayrıntı

1. Yapay zekâ ajanları. Bilim ve Gelecek ajan temelli yapay zekâyı açıkladı. Kaynak: Bilim ve Gelecek.
2. Kamuda yapay zeka dönemi. On bakanlıkta otuz pilot uygulama başlıyor. Kaynak: takvim.

# Teknik

1. Çalışma. Keşfedilen aday 240, getirilen sayfa 33, kanıt 5, elenen 28.
"""


def _plan():
    return build_plan(BRIEFING, artifact_id="a", version=1)


def test_interrupted_mid_section_resumes_at_the_next_unspoken_sentence() -> None:
    plan = _plan()
    start = level_section_cursor(plan, "summary")
    spoken = (
        "Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı. "
        "Beş sonuç ve beş farklı kaynak ürettim. Dokuz konu dışı"
    )
    result = align(plan, start, spoken)
    assert result.spoken_chunks == 2
    assert not result.complete
    resumed = speech_from(plan, result.cursor)
    assert resumed.startswith("Dokuz konu dışı sayfayı")
    assert "Bilginize." in resumed


def test_provider_paraphrase_of_numbers_still_aligns() -> None:
    """The provider may render "beş" as "5" or drop a comma; the sentence still counts."""
    plan = _plan()
    start = level_section_cursor(plan, "summary")
    spoken = (
        "Efendim son araştırma motoru qualificationı başarıyla tamamlandı "
        "5 sonuç ve 5 farklı kaynak ürettim 9 konu dışı sayfayı ve 11 ara doğrulama "
        "sayfasını eledim tarayıcı"
    )
    result = align(plan, start, spoken)
    assert result.spoken_chunks == 3
    assert speech_from(plan, result.cursor).startswith("Tarayıcı temiz şekilde kapandı.")


def test_interrupted_before_the_first_sentence_ends_repeats_it() -> None:
    plan = _plan()
    start = level_section_cursor(plan, "summary")
    result = align(plan, start, "Efendim, son")
    assert result.spoken_chunks == 0
    assert result.cursor == start


def test_whole_section_spoken_is_complete_and_moves_to_the_next_section() -> None:
    plan = _plan()
    start = level_section_cursor(plan, "summary")
    result = align(plan, start, speech_from(plan, start))
    assert result.complete
    assert result.cursor is not None and result.cursor.section_id != start.section_id


def test_short_closing_word_is_not_found_inside_unrelated_speech() -> None:
    """ "Bilginize." is two tokens; it must match exactly, never by overlap."""
    plan = _plan()
    start = level_section_cursor(plan, "summary")
    spoken = (
        "Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı. "
        "Beş sonuç ve beş farklı kaynak ürettim. Dokuz konu dışı sayfayı ve on bir ara "
        "doğrulama sayfasını eledim. Tarayıcı temiz şekilde kapandı. Bilgi"
    )
    result = align(plan, start, spoken)
    assert result.spoken_chunks == 4
    assert speech_from(plan, result.cursor) == "Bilginize."


def test_list_items_resume_at_item_granularity_within_the_section() -> None:
    """A numbered item is one narration chunk: interrupted early in item 1, "devam"
    repeats item 1; interrupted after item 1 was mostly spoken, it goes on to item 2 -
    and never leaves the section that was handed out."""
    plan = _plan()
    start = level_section_cursor(plan, "detail")
    early = align(plan, start, "Bir: Yapay zekâ ajanları.")
    assert early.cursor == start and early.spoken_chunks == 0
    later = align(
        plan,
        start,
        "Bir: Yapay zekâ ajanları. Bilim ve Gelecek ajan temelli yapay zekâyı açıkladı. "
        "Kaynak: Bilim ve Gelecek. İki: Kamuda",
    )
    assert later.spoken_chunks == 1
    assert later.cursor is not None and later.cursor.section_id == start.section_id
    assert speech_from(plan, later.cursor).startswith("Kamuda yapay zeka dönemi.")


def test_empty_transcript_keeps_the_start_cursor() -> None:
    plan = _plan()
    start = level_section_cursor(plan, "technical")
    result = align(plan, start, "")
    assert result.cursor == start and result.spoken_chunks == 0
