"""``app.documents.answers`` against ``tests/fixtures/documents/truth.json`` — the oracle
(docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §4, ADR-0083 decision 6): every
``questions[]`` entry checked against ``answer()`` (or the structural fields for the two
questions the oracle answers from ``structure`` rather than a block), the one
``comparisons[]`` entry's changed refs and the changed clause's two texts, the one
``common_points[]`` entry's terms and refs, and the same-title pair named by path.

Nothing here touches a database or a device: every ``DocRef`` comes straight from
``tests/documents_support.doc_ref``, built from the oracle's own ``expected/*.extract.json``
files — the SAME data the device lab (a real dispatcher, real fixture files) is qualified
to produce byte for byte (spec §4's oracle discipline: the expected extracts are the one
seam between the two halves).
"""

from __future__ import annotations

import pytest

from app.documents import answers as answers_module
from tests.documents_support import doc_ref, load_truth

TRUTH = load_truth()


# ------------------------------------------------------------------------- questions


@pytest.mark.parametrize(
    "question_entry", TRUTH["questions"], ids=[q["id"] for q in TRUTH["questions"]]
)
def test_truth_question(question_entry: dict) -> None:
    doc = doc_ref(question_entry["file"])
    result = answers_module.answer(doc, question_entry["question"])

    if "expect_structure" in question_entry:
        # pptx-count / py-symbol: the oracle answers from STRUCTURE, not a retrieved
        # block - checked against the structural fields the answer's own "structure" key
        # carries, never against a ref (there is no single ref for "how many slides").
        assert result["found"] is True
        for key, value in question_entry["expect_structure"].items():
            assert result["structure"] is not None
            assert result["structure"].get(key) == value
        return

    assert result["found"] is True, (question_entry["id"], result["speech"])
    refs = [r["ref"] for r in result["refs"]]
    assert question_entry["expect_ref"] in refs, (question_entry["id"], refs)
    matching = next(r for r in result["refs"] if r["ref"] == question_entry["expect_ref"])
    assert question_entry["expect_contains"] in matching["excerpt"], (
        question_entry["id"],
        matching["excerpt"],
    )


def test_every_truth_question_id_is_covered_exactly_once() -> None:
    """A drift guard: the ten questions the task brief names are exactly the ones
    parametrised above - the oracle grows, this test's parametrisation grows with it,
    never silently skipping a newly added question."""
    assert len(TRUTH["questions"]) == 10
    ids = {q["id"] for q in TRUTH["questions"]}
    assert ids == {
        "pdf-page-3",
        "xlsx-total",
        "xlsx-formula",
        "pptx-slide-4",
        "pptx-count",
        "docx-clause-3",
        "md-decisions",
        "csv-row-7",
        "json-dil",
        "py-symbol",
    }


def test_a_question_no_block_answers_is_an_honest_bulamadim_naming_what_was_searched() -> None:
    doc = doc_ref("rapor.pdf")
    result = answers_module.answer(doc, "Kuantum bilgisayarların geleceği nedir?")
    assert result["found"] is False
    assert "bulamadım" in result["speech"].lower()
    assert result["refs"], "an honest miss still names what it looked at"


# ------------------------------------------------------------------------- comparisons


def test_truth_comparison() -> None:
    entry = TRUTH["comparisons"][0]
    doc_a = doc_ref(entry["a"])
    doc_b = doc_ref(entry["b"])
    result = answers_module.compare_blocks(doc_a, doc_b)

    assert sorted(result["changed_refs"]) == sorted(entry["changed_refs"])
    assert sorted(result["unchanged_refs"]) == sorted(entry["unchanged_refs"])

    clause = result["changed_clause"]
    assert clause is not None
    assert clause["ref"] == entry["changed_clause"]["ref"]
    assert entry["changed_clause"]["a_contains"] in clause["a_text"]
    assert entry["changed_clause"]["b_contains"] in clause["b_text"]


def test_the_same_title_pair_is_named_by_path_in_the_comparison_speech() -> None:
    """ADR-0076's ambiguity rule (spec §3): two documents with one title and two paths -
    every sentence naming either one names the PATH too."""
    entry = TRUTH["comparisons"][0]
    doc_a = doc_ref(entry["a"], ambiguous=True)
    doc_b = doc_ref(entry["b"], ambiguous=True)
    result = answers_module.compare_blocks(doc_a, doc_b)
    assert entry["a"] in result["speech"]
    assert entry["b"] in result["speech"]


# ---------------------------------------------------------------------- common_points


def test_truth_common_points() -> None:
    entry = TRUTH["common_points"][0]
    docs = [doc_ref(path) for path in entry["files"]]
    result = answers_module.common_points(docs)
    assert result["found"] is True

    expected_terms_normalized = {
        answers_module.retrieval.normalize_word(t) for t in entry["expect_terms"]
    }
    actual_terms_normalized = {answers_module.retrieval.normalize_word(t) for t in result["terms"]}
    assert expected_terms_normalized <= actual_terms_normalized

    for _term, refs in result["terms"].items():
        assert len(refs) == len(docs), "one ref per document per term"
        for doc, ref in zip(docs, refs, strict=True):
            assert ref["file_id"] == doc.file_id


def test_common_points_with_fewer_than_two_documents_is_handled_honestly() -> None:
    assert answers_module.common_points([]) == {
        "speech": "Karşılaştıracak belge yok efendim.",
        "terms": {},
        "found": False,
    }


def test_common_points_speech_uses_the_natural_turkish_spelling_not_the_ascii_fold() -> None:
    entry = TRUTH["common_points"][0]
    docs = [doc_ref(path) for path in entry["files"]]
    result = answers_module.common_points(docs)
    assert "bütçe" in result["speech"]


# --------------------------------------------------------------------------- summarize


def test_summarize_is_kind_aware_and_always_returns_refs() -> None:
    for path, expect_substring in (
        ("sozlesmeler/2026/sozlesme.docx", "Madde 3"),
        ("butce-2026.xlsx", "Ozet"),
        ("sunum-q3.pptx", "Riskler"),
        ("rapor.pdf", None),
        ("notlar.md", "Kararlar"),
    ):
        doc = doc_ref(path)
        result = answers_module.summarize(doc)
        assert result["found"] is True
        assert result["refs"], path
        if expect_substring:
            assert expect_substring in result["speech"], (path, result["speech"])


def test_place_phrase_speaks_every_reference_shape_in_the_owners_words() -> None:
    assert answers_module.place_phrase("p3", kind="pdf") == "3. sayfa"
    assert answers_module.place_phrase("p8", kind="docx") == "8. paragraf"
    assert answers_module.place_phrase("s4", kind="pptx") == "4. slayt"
    assert answers_module.place_phrase("sheet:Ozet!A5:B5", kind="xlsx") == "Ozet sayfası, 5. satır"
    assert answers_module.place_phrase("h2:Kararlar", kind="md") == "Kararlar bölümü"
    assert answers_module.place_phrase("r7", kind="csv") == "7. satır"
    assert answers_module.place_phrase("$.ses", kind="json") == "ses anahtarı"
    assert answers_module.place_phrase("L1-40", kind="source") == "1-40. satırlar"
