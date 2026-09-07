"""Unit tests: app.documents.retrieval (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3,
ADR-0083 decision 2). Turkish casefold both ways (``I`` -> ``ı``, ``İ`` -> ``i``),
diacritics-insensitivity, literal reference matching (ordinal + place noun, JSON keys, MD
headings, PPTX slide titles), and top-k determinism (a fixed order for a fixed input,
never randomised, ties broken by document order).
"""

from __future__ import annotations

from app.documents import retrieval

# --------------------------------------------------------------- casefold / diacritics


def test_turkish_casefold_maps_both_i_variants_correctly() -> None:
    assert retrieval.normalize_word("İzmir") == retrieval.normalize_word("izmir")
    assert retrieval.normalize_word("İzmir") == retrieval.normalize_word("Izmir")
    # ASCII "I" -> Turkish dotless "ı", not the Latin "i" str.lower() would give it.
    assert retrieval.turkish_casefold("I") == "ı"
    assert retrieval.turkish_casefold("İ") == "i"


def test_diacritics_insensitive_matching() -> None:
    assert retrieval.normalize_word("bütçe") == retrieval.normalize_word("butce")
    assert retrieval.normalize_word("Şehir") == retrieval.normalize_word("sehir")


def test_content_words_drops_stopwords_digits_and_short_tokens() -> None:
    words = retrieval.content_words("Bu dosyada kaç sayfa var? 3 tane.")
    assert "kac" not in words  # "kaç"/"kac" is a stopword
    assert "3" not in words  # a bare number is not a content word
    assert "var" not in words  # stopword
    assert "sayfa" in words


def test_shares_prefix_needs_a_minimum_overlap_to_treat_a_stem_as_the_same_word() -> None:
    assert retrieval.shares_prefix("odeme", "odemesi")  # >= 4-char shared prefix
    assert retrieval.shares_prefix("kerem", "kerem")  # exact
    assert not retrieval.shares_prefix("ses", "sey")  # both short, not equal
    assert not retrieval.shares_prefix("sehirde", "kerem")


# ------------------------------------------------------------------------- score_block


def test_score_block_weighs_an_exact_match_higher_than_a_prefix_only_match() -> None:
    """The CSV oracle's own ambiguity (truth.json's ``csv-row-7`` question): "Kerem hangi
    şehirde?" must not tie a header row (only "şehir"/"şehirde" prefix-matches) against
    the actual data row (an EXACT "Kerem" match)."""
    header = {"ref": "r1", "kind": "row", "text": "Ad,Şehir,Yaş,Tutar,Durum"}
    kerem_row = {"ref": "r7", "kind": "row", "text": "Kerem,İzmir,45,1320,aktif"}
    words = retrieval.content_words("Kerem hangi şehirde?")
    assert retrieval.score_block(words, kerem_row) > retrieval.score_block(words, header)


def test_top_k_orders_by_score_then_document_order_deterministically() -> None:
    blocks = [
        {"ref": "p1", "kind": "paragraph", "text": "Giriş bölümü."},
        {"ref": "p2", "kind": "paragraph", "text": "Ödeme süresi otuz gündür."},
        {"ref": "p3", "kind": "paragraph", "text": "Ödeme koşulları ayrıca belirtilir."},
    ]
    results = retrieval.top_k(blocks, "Ödeme süresi ne kadar?", kind="docx", k=3)
    assert [r.ref for r in results][0] == "p2"
    # Running it again must give the identical order (deterministic, no randomisation).
    again = retrieval.top_k(blocks, "Ödeme süresi ne kadar?", kind="docx", k=3)
    assert [r.ref for r in results] == [r.ref for r in again]


def test_top_k_returns_nothing_when_no_block_scores() -> None:
    blocks = [{"ref": "p1", "kind": "paragraph", "text": "Alakasız bir cümle."}]
    assert retrieval.top_k(blocks, "Kuantum fiziği nedir?", kind="docx") == []


# --------------------------------------------------------------------- literal_ref


def test_ordinal_plus_place_noun_resolves_directly_for_pdf_pages() -> None:
    assert retrieval.literal_ref("Üçüncü sayfada ne yazıyor?", kind="pdf", structure={}) == "p3"


def test_ordinal_plus_place_noun_resolves_directly_for_pptx_slides() -> None:
    assert retrieval.literal_ref("Dördüncü slaytın başlığı ne?", kind="pptx", structure={}) == "s4"


def test_json_top_level_key_resolves_directly() -> None:
    ref = retrieval.literal_ref(
        "Ses dili ne?", kind="json", structure={"keys": ["surum", "ses", "cihaz", "kokler"]}
    )
    assert ref == "$.ses"


def test_md_heading_title_resolves_via_the_sentinel_and_top_k_finds_the_section_block() -> None:
    structure = {"headings": ["Toplantı Notları", "Giriş", "Kararlar", "Sonraki adımlar"]}
    ref = retrieval.literal_ref("Kararlar bölümünde ne var?", kind="md", structure=structure)
    assert ref == "@heading:Kararlar"
    blocks = [
        {
            "ref": "h1:Toplantı Notları",
            "kind": "section",
            "title": "Toplantı Notları",
            "text": "...",
        },
        {"ref": "h2:Kararlar", "kind": "section", "title": "Kararlar", "text": "Bütçe onaylandı."},
    ]
    top = retrieval.top_k(blocks, "Kararlar bölümünde ne var?", kind="md", structure=structure)
    assert top[0].ref == "h2:Kararlar"
    assert top[0].score == 1000  # a literal reference is unambiguous


def test_pptx_slide_title_resolves_by_name_not_ordinal() -> None:
    structure = {"slides": [{"index": 1, "title": "Q3 Özeti"}, {"index": 5, "title": "Riskler"}]}
    ref = retrieval.literal_ref("Riskler slaydında ne var?", kind="pptx", structure=structure)
    assert ref == "s5"


def test_a_literal_reference_always_wins_over_token_overlap() -> None:
    blocks = [
        {"ref": "p1", "kind": "page", "text": "Üçüncü sayfa hakkında bir yorum burada."},
        {"ref": "p3", "kind": "page", "text": "Bambaşka bir konu."},
    ]
    top = retrieval.top_k(blocks, "Üçüncü sayfada ne yazıyor?", kind="pdf", k=3)
    assert len(top) == 1
    assert top[0].ref == "p3"
