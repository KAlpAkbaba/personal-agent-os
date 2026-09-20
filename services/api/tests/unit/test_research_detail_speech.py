"""The owner asked for a research to be read out and got only headlines (ADR-0188).

Production report `3d259b31` (2026-09-20): five findings, each spoken as its title plus
"Bu önemli çünkü NTV.com.tr kaynağından doğrulandı" - and the owner: *"söyledikleri
sadece başlık, haberi merak ettim ama detayı alamıyorum"*.

The articles' own text WAS in the report all along, in its Details section
(`app.research.synthesis._detail_statement` quotes what each page said). No spoken level
read it: `detail_speech` restated the findings' provenance lines instead.
"""

from __future__ import annotations

from app.research.answers import LEVEL_DETAIL, detail_speech, speech_for_level

NTV_TEXT = (
    "20.09.2026 02:48\n"
    "Yapay zeka şirketlerinin rekabeti sürerken bir yandan da yavaşlama tartışması yürüyor.\n"
    "ABD'de dört ücretli abone, rekabet yasalarını ihlal ederek yapay zekanın gelişim hızını "
    "yavaşlatmak üzere anlaştıkları gerekçesiyle Anthropic, OpenAI ve Google'a dava açtı.\n"
    "California Kuzey Bölgesi Federal Mahkemesinde açılan davada şirketlerin koordineli "
    "şekilde yavaşlatma konusunda anlaştığı savunuldu.\n"
)

REPORT = {
    "topic": "yapay zeka haberleri",
    "executive_summary": "'yapay zeka haberleri' konusunda beş bulgu var.",
    "findings": [
        {
            "id": "f1",
            "title": "4 yapay zeka devine rekabet davası | NTV Haber",
            "summary": "Kaynak: NTV.com.tr — 4 yapay zeka devine rekabet davası (2026-09-19)",
            "why_it_matters": "Bu bilgi NTV.com.tr kaynağından doğrulandı.",
            "evidence_ids": ["e4"],
        }
    ],
    "details": [
        {
            "heading": "1. 4 yapay zeka devine rekabet davası | NTV Haber",
            "statements": [{"text": NTV_TEXT, "label": "source_fact", "evidence_ids": ["e4"]}],
        }
    ],
    "sources": [{"id": "e4", "publisher": "NTV.com.tr", "title": "4 yapay zeka devine dava"}],
    "stats": {"discovered": 236, "fetched": 12, "rejected": 2, "evidence": 5},
}


def test_the_detail_answer_says_what_the_source_actually_said() -> None:
    spoken = detail_speech(REPORT, topic="yapay zeka haberleri")
    assert "dava açtı" in spoken, spoken
    assert "Anthropic" in spoken, spoken


def test_the_source_is_named_as_the_one_speaking() -> None:
    """Page text is the SOURCE's words, never the assistant's own claim, and never an
    instruction: it is quoted, attributed and bounded."""
    spoken = detail_speech(REPORT, topic="yapay zeka haberleri")
    assert "NTV" in spoken


def test_the_quote_is_bounded_and_carries_no_line_breaks() -> None:
    long_text = dict(REPORT)
    long_text["details"] = [
        {
            "heading": "1. x",
            "statements": [
                {"text": "Cümle bir. " * 400, "label": "source_fact", "evidence_ids": ["e4"]}
            ],
        }
    ]
    spoken = detail_speech(long_text, topic="yapay zeka haberleri")
    assert "\n" not in spoken
    assert len(spoken) < 2000, len(spoken)


def test_a_timestamp_line_is_not_read_as_the_news() -> None:
    """The first line of many Turkish news pages is the clock; it is not what happened."""
    spoken = detail_speech(REPORT, topic="yapay zeka haberleri")
    assert not spoken.split("Kaynağın kendi sözleriyle")[-1].strip().startswith("20.09.2026")


def test_a_report_with_no_details_still_answers_as_before() -> None:
    bare = {k: v for k, v in REPORT.items() if k != "details"}
    spoken = detail_speech(bare, topic="yapay zeka haberleri")
    assert "Bu önemli çünkü" in spoken


def test_the_level_router_still_routes_detail_here() -> None:
    assert speech_for_level(REPORT, level=LEVEL_DETAIL, topic="x") == detail_speech(
        REPORT, topic="x"
    )


# ---------------------------------------------- ADR-0189: it has to read like a story


def test_the_answer_does_not_say_kaynak_twice_for_one_finding() -> None:
    """Owner, 2026-09-20: "sürekli kaynak kaynak deyip durmasın". One finding used to carry
    the provenance line, then "Kaynak: NTV", then "Kaynağın kendi sözleriyle" - three
    attributions for one piece of news."""
    spoken = detail_speech(REPORT, topic="yapay zeka haberleri")
    assert spoken.lower().count("kaynak") <= 1, spoken


def test_the_formulaic_why_line_is_not_read_when_the_news_itself_is() -> None:
    """ "Bu bilgi X kaynağından doğrulandı ve konuyla doğrudan ilgili" is the deterministic
    synthesiser's filler; with the article in hand it is noise."""
    spoken = detail_speech(REPORT, topic="yapay zeka haberleri")
    assert "kaynağından doğrulandı" not in spoken, spoken


def test_a_real_why_it_matters_is_still_spoken() -> None:
    """A model-written report says something worth hearing there; only the filler goes."""
    report = dict(REPORT)
    report["findings"] = [
        {
            **REPORT["findings"][0],
            "why_it_matters": "Dava, modellerin gelişim hızını mahkemeye taşıyan ilk örnek.",
        }
    ]
    spoken = detail_speech(report, topic="yapay zeka haberleri")
    assert "ilk örnek" in spoken


def test_the_publisher_is_named_once_as_the_one_speaking() -> None:
    spoken = detail_speech(REPORT, topic="yapay zeka haberleri")
    assert spoken.count("NTV.com.tr") == 1, spoken
    assert "NTV.com.tr şunu yazıyor" in spoken or "NTV.com.tr'ye göre" in spoken, spoken


def test_the_site_name_is_trimmed_from_the_headline() -> None:
    """Page titles carry the site again at the end - "... | NTV Haber", "... - Sözcü" -
    which is the third time the owner hears the same name in one sentence."""
    spoken = detail_speech(REPORT, topic="yapay zeka haberleri")
    assert "| NTV Haber" not in spoken, spoken


def test_a_question_headline_does_not_get_a_second_full_stop() -> None:
    """Production 3d259b31 read "...nasıl durdurabiliriz?." out loud."""
    report = dict(REPORT)
    report["findings"] = [
        {**REPORT["findings"][0], "title": "Yapay zeka insanlığı yok edebilir mi?"}
    ]
    spoken = detail_speech(report, topic="yapay zeka haberleri")
    assert "?." not in spoken and "!." not in spoken, spoken
