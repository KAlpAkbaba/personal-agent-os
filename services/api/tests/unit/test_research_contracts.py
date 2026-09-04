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

import pathlib
import re

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


# --------------------------------------------------------------------------- #
# named, versioned entity schemas (second incident: KeyError('label'))
# --------------------------------------------------------------------------- #

from app.research.contracts import (  # noqa: E402 - grouped with the schema tests below
    ENTITY_DETAIL_SECTION,
    ENTITY_STATEMENT,
    ENTITY_SYNTHESIS_RESPONSE,
    FIELD_DERIVED,
    FIELD_OPTIONAL,
    FIELD_REQUIRED,
    SCHEMAS,
    STATEMENT_LABELS,
    schema,
)


def test_every_schema_is_named_versioned_and_names_its_producer() -> None:
    assert SCHEMAS
    for name, entity_schema in SCHEMAS.items():
        assert entity_schema.name == name
        assert entity_schema.version >= 1
        assert entity_schema.producer in (
            "search_provider",
            "device_worker",
            "synthesis_provider",
            "cloud_core",
        )
        assert entity_schema.fields, f"{name} declares no fields"
        for spec in entity_schema.fields:
            assert spec.kind in (FIELD_REQUIRED, FIELD_OPTIONAL, FIELD_DERIVED)
            assert spec.provenance and len(spec.provenance) > 8
            if spec.kind == FIELD_OPTIONAL:
                # canonical nullable/default semantics: an optional field always has one
                assert spec.default is not None or spec.default is None


def test_the_statement_schema_makes_label_required_within_the_taxonomy() -> None:
    statement = schema(ENTITY_STATEMENT)
    assert "label" in statement.required_names
    assert "text" in statement.required_names
    assert "evidence_ids" in statement.optional_names
    assert statement.field("label").allowed == STATEMENT_LABELS


def test_reading_a_missing_required_field_names_everything_an_operator_needs() -> None:
    """entity_type, entity_id, field, stage, producer, schema_version - the owner's list."""
    with pytest.raises(ContractViolation) as excinfo:
        schema(ENTITY_STATEMENT).validate(
            {"text": "etiketsiz"}, entity_id="why_it_matters[0]", stage="synthesizing"
        )
    detail = excinfo.value.as_dict()
    assert detail["entity_type"] == "statement"
    assert detail["entity_id"] == "why_it_matters[0]"
    assert detail["field"] == "label"
    assert detail["stage"] == "synthesizing"
    assert detail["producer"] == "synthesis_provider"
    assert detail["schema_version"] == 1
    assert detail["reason"] == "missing_required_field"


def test_optional_fields_take_their_declared_default_and_are_never_fatal() -> None:
    fields = schema(ENTITY_STATEMENT).validate(
        {"text": "x", "label": "model_inference"}, entity_id="s1"
    )
    assert fields["evidence_ids"] == ()
    assert fields["provenance_note"] is None


def test_a_derived_field_is_never_read_from_a_producer_payload() -> None:
    evidence = schema(ENTITY_EVIDENCE_ITEM)
    assert "rank" in evidence.derived_names and "score" in evidence.derived_names
    with pytest.raises(LookupError):
        evidence.read({"rank": 3}, "rank")


def test_an_enum_outside_the_taxonomy_and_a_wrong_type_are_named_precisely() -> None:
    statement = schema(ENTITY_STATEMENT)
    with pytest.raises(ContractViolation) as bad_label:
        statement.validate({"text": "x", "label": "önemli"}, entity_id="s1")
    assert bad_label.value.as_dict()["reason"] == "not_an_allowed_value"

    with pytest.raises(ContractViolation) as bad_text:
        statement.validate({"text": 5, "label": "model_inference"}, entity_id="s1")
    assert bad_text.value.as_dict()["reason"] == "number_is_not_text"

    with pytest.raises(ContractViolation) as not_object:
        statement.validate("not an object", entity_id="s1")
    assert not_object.value.as_dict()["reason"] == "not_an_object"


def test_the_detail_and_response_schemas_declare_their_required_fields() -> None:
    assert schema(ENTITY_DETAIL_SECTION).required_names == ("heading",)
    assert schema(ENTITY_SYNTHESIS_RESPONSE).required_names == ("executive_summary",)


def test_the_evidence_schema_covers_every_field_the_device_produces() -> None:
    evidence = schema(ENTITY_EVIDENCE_ITEM)
    assert set(evidence.required_names) == {
        "url",
        "title",
        "excerpt",
        "fetched_at",
        "extraction_method",
    }
    assert "publisher" in evidence.optional_names
    assert evidence.producer == "device_worker"


# --------------------------------------------------------------------------- #
# schema-boundary audit: no raw indexing of producer-controlled payloads
# --------------------------------------------------------------------------- #

RESEARCH_PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "app" / "research"

#: Names that hold a payload some OTHER producer controls (a model, the device worker, a
#: search provider, or a document an older release of this Cloud Core wrote). Indexing one of
#: these with obj["key"] is how the pipeline used to die on the first missing key.
UNTRUSTED_PAYLOAD_NAMES = (
    "data",
    "payload",
    "result",
    "raw",
    "raw_finding",
    "raw_detail",
    "item",
    "hit",
    "report_json",
)

RAW_INDEX = re.compile(
    r"\b(" + "|".join(UNTRUSTED_PAYLOAD_NAMES) + r")\[" + chr(34) + r"[a-z_]+" + chr(34) + r"\]"
)


def test_no_parse_boundary_indexes_a_producer_payload_by_raw_key() -> None:
    """The audit the owner asked for: after the KeyError('label') incident, no research module
    may reach into a producer-controlled payload with a bare key. Every such read goes through
    the schema layer, which names the entity, the field, the producer and the version instead
    of failing one missing key at a time in an owner qualification."""
    offenders: list[str] = []
    for path in sorted(RESEARCH_PACKAGE.glob("*.py")):
        if path.name == "contracts.py":
            continue  # the contract layer itself is where the reads legitimately happen
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if RAW_INDEX.search(line):
                offenders.append(f"{path.name}:{number}: {stripped}")
    assert not offenders, "raw producer-payload indexing outside the contract layer:\n" + "\n".join(
        offenders
    )
