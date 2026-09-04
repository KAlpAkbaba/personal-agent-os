"""Typed field contracts for the research pipeline (ADR-0050 item 20).

Regression corpus for the owner incident of 2026-09-04: research run `f6eb5021` discovered
243 candidates over DuckDuckGo, fetched and ranked 12, and then died with

    ValueError: invalid literal for int() with base 10:
    'Bu model, yapay zeka uygulamalarının etkinliğini artıracak ve insan gibi
     düşünme yeteneğine sahip sistemlerin geliştirilmesine olanak tanıyacak.'

because the synthesis model answered a finding's numeric ``importance`` with a Turkish prose
sentence. Everything here is the shape of data that reaches those fields in the real product:
Turkish prose, quoted text, dates, numbers embedded in sentences, missing scores, malformed
candidate structures - and the inverse, a number arriving where a title/snippet belongs.
"""

from __future__ import annotations

import pytest

from app.research.contracts import (
    ENTITY_DISCOVERED_RESULT,
    ENTITY_EVIDENCE_ITEM,
    ENTITY_FINDING,
    ENTITY_RANKED_CANDIDATE,
    ERROR_INVALID_EVIDENCE_CONTRACT,
    NUMERIC_FIELDS,
    TEXT_FIELDS,
    ContractViolation,
    InsufficientValidEvidence,
    QuarantineLedger,
    classify_value,
    numeric_spec,
    require_number,
    require_text,
)

#: The exact value from the owner's failed run (research-1.json, task f6eb5021).
INCIDENT_PROSE = (
    "Bu model, yapay zeka uygulamalarının etkinliğini artıracak ve insan gibi düşünme "
    "yeteneğine sahip sistemlerin geliştirilmesine olanak tanıyacak."
)

#: Text shapes a real Turkish source page puts in front of the parser.
TURKISH_TEXTS = (
    INCIDENT_PROSE,
    "“Yapay zekâ ajanları” başlıklı yazıda üç gelişme sıralanıyor.",
    "Şirket, 5 yeni model duyurdu ve 3 ülkede kullanıma açtı.",  # numbers inside prose
    "2026 yılında ajanlar yaygınlaşacak",  # a year inside prose
    "birinci",  # a rank written as a word
    "  ",  # whitespace only
    "",  # empty
)


# --------------------------------------------------------------------------- #
# the registry itself
# --------------------------------------------------------------------------- #


def test_every_numeric_field_declares_name_type_range_and_provenance() -> None:
    assert NUMERIC_FIELDS, "the registry must not be empty"
    for (entity, name), spec in NUMERIC_FIELDS.items():
        assert spec.entity == entity and spec.name == name
        assert spec.kind in ("int", "float")
        assert spec.minimum <= spec.maximum
        assert spec.provenance and len(spec.provenance) > 10, f"{entity}.{name} lacks provenance"
        if not spec.required:
            assert spec.default is None or isinstance(spec.default, (int, float))


def test_the_five_pipeline_entities_all_have_contracts() -> None:
    entities_with_numbers = {entity for entity, _ in NUMERIC_FIELDS}
    assert {
        "discovered_result",
        "fetched_source",
        "evidence_item",
        "ranked_candidate",
        "finding",
    } <= (entities_with_numbers | set(TEXT_FIELDS))


def test_an_undeclared_field_is_a_programming_error_not_a_silent_pass() -> None:
    with pytest.raises(LookupError):
        numeric_spec(ENTITY_FINDING, "not_a_declared_field")
    with pytest.raises(LookupError):
        require_text("x", ENTITY_FINDING, "not_a_declared_text_field")


# --------------------------------------------------------------------------- #
# the incident itself
# --------------------------------------------------------------------------- #


def test_the_incident_value_is_refused_with_a_named_field_and_value_class() -> None:
    with pytest.raises(ContractViolation) as excinfo:
        require_number(
            INCIDENT_PROSE, ENTITY_FINDING, "importance", entity_id="f1", stage="synthesizing"
        )
    detail = excinfo.value.as_dict()
    assert detail["error_class"] == ERROR_INVALID_EVIDENCE_CONTRACT
    assert detail["entity"] == "finding"
    assert detail["entity_id"] == "f1"
    assert detail["field"] == "importance"
    assert detail["expected"] == "int in [1, 5]"
    assert detail["observed_type"] == "str"
    assert detail["observed_class"] == "prose_text"
    assert detail["reason"] == "text_is_not_a_number"
    assert detail["stage"] == "synthesizing"


def test_a_violation_never_carries_the_offending_content() -> None:
    """Source text must not leak into errors, logs or the run's event trail."""
    with pytest.raises(ContractViolation) as excinfo:
        require_number(INCIDENT_PROSE, ENTITY_FINDING, "importance", entity_id="f1")
    violation = excinfo.value
    serialized = repr(violation.as_dict()) + str(violation)
    assert "yapay zeka" not in serialized
    assert "Bu model" not in serialized
    assert violation.as_dict()["observed_length"] == len(INCIDENT_PROSE)


@pytest.mark.parametrize("text", TURKISH_TEXTS)
def test_no_turkish_prose_quote_or_date_is_ever_accepted_as_a_number(text: str) -> None:
    for entity, field_name in (
        (ENTITY_FINDING, "importance"),
        (ENTITY_EVIDENCE_ITEM, "rank"),
        (ENTITY_EVIDENCE_ITEM, "score"),
        (ENTITY_RANKED_CANDIDATE, "rank"),
        (ENTITY_DISCOVERED_RESULT, "rank"),
    ):
        with pytest.raises(ContractViolation):
            require_number(text, entity, field_name, entity_id="x")


# --------------------------------------------------------------------------- #
# what IS accepted
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3, 3), ("3", 3), (" 4 ", 4), (5.0, 5)],
)
def test_real_numbers_and_clean_numeric_strings_are_accepted(value: object, expected: int) -> None:
    assert require_number(value, ENTITY_FINDING, "importance", entity_id="f1") == expected


def test_a_missing_optional_number_takes_its_declared_default() -> None:
    assert require_number(None, ENTITY_EVIDENCE_ITEM, "rank", entity_id="u") == 0
    assert require_number(None, ENTITY_EVIDENCE_ITEM, "score", entity_id="u") == 0.0


def test_a_missing_required_number_is_a_violation_not_a_zero() -> None:
    with pytest.raises(ContractViolation) as excinfo:
        require_number(None, ENTITY_FINDING, "importance", entity_id="f1")
    assert excinfo.value.as_dict()["reason"] == "missing"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (True, "bool_is_not_a_number"),
        (float("nan"), "not_finite"),
        (float("inf"), "not_finite"),
        (0, "out_of_range"),
        (9, "out_of_range"),
        (2.5, "not_an_integer"),
        ([3], "wrong_type"),
        ({"importance": 3}, "wrong_type"),
    ],
)
def test_every_other_malformed_shape_is_named_precisely(value: object, reason: str) -> None:
    with pytest.raises(ContractViolation) as excinfo:
        require_number(value, ENTITY_FINDING, "importance", entity_id="f1")
    assert excinfo.value.as_dict()["reason"] == reason


# --------------------------------------------------------------------------- #
# the inverse: text fields must not accept numbers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("entity", ["discovered_result", "evidence_item", "ranked_candidate"])
@pytest.mark.parametrize("field_name", ["title", "url"])
def test_a_rank_or_score_can_never_be_read_as_a_title_or_url(entity: str, field_name: str) -> None:
    for numeric in (1, 2.5, 0):
        with pytest.raises(ContractViolation) as excinfo:
            require_text(numeric, entity, field_name, entity_id="x")
        assert excinfo.value.as_dict()["reason"] == "number_is_not_text"


def test_text_fields_accept_real_turkish_text_including_quotes_and_numbers() -> None:
    for text in TURKISH_TEXTS:
        assert require_text(text, ENTITY_EVIDENCE_ITEM, "title", entity_id="u") == text


def test_snippet_and_title_are_declared_text_on_the_discovered_result() -> None:
    assert "snippet" in TEXT_FIELDS[ENTITY_DISCOVERED_RESULT]
    assert "title" in TEXT_FIELDS[ENTITY_DISCOVERED_RESULT]
    assert ("discovered_result", "snippet") not in NUMERIC_FIELDS
    assert ("discovered_result", "title") not in NUMERIC_FIELDS


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "null"),
        (True, "bool"),
        (3, "int"),
        (2.5, "float"),
        ("7", "numeric_string"),
        ("-7.5", "numeric_string"),
        ("2026-09-04", "date_like_string"),
        ("2026/09/04 tarihinde", "date_like_string"),
        ("", "empty_string"),
        ("   ", "empty_string"),
        (INCIDENT_PROSE, "prose_text"),
        ([1], "list"),
        ({"a": 1}, "mapping"),
    ],
)
def test_value_classification_is_precise(value: object, expected: str) -> None:
    assert classify_value(value) == expected


# --------------------------------------------------------------------------- #
# quarantine and the failure threshold
# --------------------------------------------------------------------------- #


def test_quarantine_records_reasons_and_summarises_them() -> None:
    ledger = QuarantineLedger(stage="ranking")
    assert ledger.empty
    try:
        require_number(INCIDENT_PROSE, ENTITY_FINDING, "importance", entity_id="f1")
    except ContractViolation as violation:
        ledger.record(violation, url="https://example.com/1")
    ledger.record_reason(entity="evidence_item", entity_id="https://x/2", reason="KeyError")
    assert len(ledger) == 2
    assert ledger.entries[0]["url"] == "https://example.com/1"
    assert ledger.entries[0]["stage"] == "synthesizing" or ledger.entries[0]["stage"] == "ranking"
    summary = ledger.summary()
    assert summary["quarantined"] == 2
    assert any("importance" in key for key in summary["by_reason"])


def test_insufficient_valid_evidence_reports_counts_and_reasons() -> None:
    exc = InsufficientValidEvidence(
        stage="ranking",
        valid=1,
        required=3,
        quarantined=[{"entity": "evidence_item", "reason": "text_is_not_a_number"}],
    )
    detail = exc.as_dict()
    assert detail["error_class"] == "insufficient_valid_evidence"
    assert detail["valid"] == 1 and detail["required"] == 3
    assert detail["quarantined_total"] == 1
    assert "1 contract-valid item(s)" in str(exc)
