"""Unit tests: ArtifactSpec (docs/M22_ARTIFACT_FACTORY_SPEC.md §1, ADR-0085 §1).

Kind-shape validation, the bounds (sections/rows/slides/columns/text), and the
"never invented" rule — every number in the structure must be in spoken_numbers
when that list is given.
"""

from __future__ import annotations

import glob
import json

import pytest
from pydantic import ValidationError

from app.artifacts.spec import (
    KIND_FORMATS,
    MAX_COLUMNS,
    MAX_ROWS,
    MAX_SECTIONS,
    MAX_SLIDES,
    MAX_TEXT_CHARS,
    ArtifactSpec,
)

FIXTURE_SPECS = sorted(glob.glob("tests/fixtures/artifacts/specs/*.json"))


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------- the fixtures


@pytest.mark.parametrize("path", FIXTURE_SPECS)
def test_every_fixture_spec_is_a_valid_artifact_spec(path: str) -> None:
    spec = ArtifactSpec.model_validate(_load(path))
    assert spec.kind in KIND_FORMATS
    assert spec.formats() == KIND_FORMATS[spec.kind]


def test_fixtures_cover_all_five_kinds() -> None:
    kinds = {ArtifactSpec.model_validate(_load(p)).kind for p in FIXTURE_SPECS}
    assert kinds == {"document", "spreadsheet", "presentation", "dataset", "page"}


# ------------------------------------------------------------------- kind shape


def test_document_requires_sections() -> None:
    with pytest.raises(ValidationError, match="requires 'sections'"):
        ArtifactSpec.model_validate({"kind": "document", "title": "T"})


def test_document_rejects_spreadsheet_fields() -> None:
    with pytest.raises(ValidationError, match="must not carry 'sheets'"):
        ArtifactSpec.model_validate(
            {
                "kind": "document",
                "title": "T",
                "sections": [{"heading": "H", "paragraphs": ["p"]}],
                "sheets": [{"name": "S", "columns": ["A"], "rows": [["x"]]}],
            }
        )


def test_dataset_requires_columns_and_rows() -> None:
    with pytest.raises(ValidationError):
        ArtifactSpec.model_validate({"kind": "dataset", "title": "T", "columns": ["A"]})


def test_dataset_row_width_must_match_columns() -> None:
    with pytest.raises(ValidationError, match="has 1 cells, expected 2"):
        ArtifactSpec.model_validate(
            {"kind": "dataset", "title": "T", "columns": ["A", "B"], "rows": [["x"]]}
        )


def test_sheet_row_width_must_match_columns() -> None:
    with pytest.raises(ValidationError, match="has 3 cells"):
        ArtifactSpec.model_validate(
            {
                "kind": "spreadsheet",
                "title": "T",
                "sheets": [
                    {"name": "S", "columns": ["A", "B"], "rows": [["x", "y", "z"]]}
                ],
            }
        )


def test_sheet_totals_must_name_a_known_column() -> None:
    with pytest.raises(ValidationError, match="totals name unknown column"):
        ArtifactSpec.model_validate(
            {
                "kind": "spreadsheet",
                "title": "T",
                "sheets": [
                    {
                        "name": "S",
                        "columns": ["A", "B"],
                        "rows": [["x", 1]],
                        "totals": {"C": "sum"},
                    }
                ],
            }
        )


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactSpec.model_validate({"kind": "spreadsheeet", "title": "T"})


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        ArtifactSpec.model_validate(
            {
                "kind": "page",
                "title": "T",
                "sections": [{"heading": "H", "paragraphs": []}],
                "unexpected_field": 1,
            }
        )


# ----------------------------------------------------------------------- bounds


def test_too_many_sections_rejected() -> None:
    sections = [{"heading": f"H{i}", "paragraphs": ["x"]} for i in range(MAX_SECTIONS + 1)]
    with pytest.raises(ValidationError):
        ArtifactSpec.model_validate({"kind": "document", "title": "T", "sections": sections})


def test_max_sections_accepted() -> None:
    sections = [{"heading": f"H{i}", "paragraphs": ["x"]} for i in range(MAX_SECTIONS)]
    spec = ArtifactSpec.model_validate({"kind": "document", "title": "T", "sections": sections})
    assert len(spec.sections) == MAX_SECTIONS


def test_too_many_slides_rejected() -> None:
    slides = [{"title": f"S{i}", "bullets": ["x"]} for i in range(MAX_SLIDES + 1)]
    with pytest.raises(ValidationError):
        ArtifactSpec.model_validate({"kind": "presentation", "title": "T", "slides": slides})


def test_too_many_rows_rejected() -> None:
    rows = [["x"] for _ in range(MAX_ROWS + 1)]
    with pytest.raises(ValidationError):
        ArtifactSpec.model_validate(
            {"kind": "dataset", "title": "T", "columns": ["A"], "rows": rows}
        )


def test_too_many_columns_rejected() -> None:
    columns = [f"C{i}" for i in range(MAX_COLUMNS + 1)]
    with pytest.raises(ValidationError):
        ArtifactSpec.model_validate(
            {"kind": "dataset", "title": "T", "columns": columns, "rows": []}
        )


def test_too_much_text_rejected() -> None:
    long_paragraph = "x" * (MAX_TEXT_CHARS + 1)
    with pytest.raises(ValidationError, match="more than the"):
        ArtifactSpec.model_validate(
            {
                "kind": "page",
                "title": "T",
                "sections": [{"heading": "H", "paragraphs": [long_paragraph]}],
            }
        )


# -------------------------------------------------------- the never-invented rule


def test_a_number_not_in_spoken_numbers_is_rejected() -> None:
    with pytest.raises(ValidationError, match="never invented"):
        ArtifactSpec.model_validate(
            {
                "kind": "spreadsheet",
                "title": "T",
                "sheets": [
                    {"name": "S", "columns": ["Kalem", "Tutar"], "rows": [["Kira", 12000]]}
                ],
                "spoken_numbers": [45000],  # 12000 was never said
            }
        )


def test_a_number_embedded_in_free_text_is_also_checked() -> None:
    with pytest.raises(ValidationError, match="never invented"):
        ArtifactSpec.model_validate(
            {
                "kind": "presentation",
                "title": "T",
                "slides": [{"title": "S1", "bullets": ["Bulut maliyeti yüzde 8 arttı"]}],
                "spoken_numbers": [12],  # 8 was never said
            }
        )


def test_spoken_numbers_covering_every_number_is_accepted() -> None:
    spec = ArtifactSpec.model_validate(
        {
            "kind": "spreadsheet",
            "title": "T",
            "sheets": [
                {"name": "S", "columns": ["Kalem", "Tutar"], "rows": [["Kira", 12000]]}
            ],
            "spoken_numbers": [12000],
        }
    )
    assert spec.spoken_numbers == [12000]


def test_empty_spoken_numbers_means_no_numbers_allowed() -> None:
    with pytest.raises(ValidationError, match="never invented"):
        ArtifactSpec.model_validate(
            {
                "kind": "page",
                "title": "T",
                "sections": [{"heading": "H", "paragraphs": ["12 tane var"]}],
                "spoken_numbers": [],
            }
        )


def test_no_spoken_numbers_field_skips_the_check() -> None:
    # spoken_numbers omitted entirely (None) -> the rule is not enforced (a caller
    # that has not adopted it yet, e.g. a bare structural spec in a test).
    spec = ArtifactSpec.model_validate(
        {
            "kind": "page",
            "title": "T",
            "sections": [{"heading": "H", "paragraphs": ["12 tane var"]}],
        }
    )
    assert spec.spoken_numbers is None


def test_formula_constants_are_never_checked_against_spoken_numbers() -> None:
    # The budget fixture's own formula (=B5*0.2, a VAT rate) is not in spoken_numbers,
    # and must not be -- a formula is the renderer's own arithmetic, not a spoken number.
    spec = ArtifactSpec.model_validate(_load("tests/fixtures/artifacts/specs/butce-tablosu.json"))
    assert "0.2" not in (spec.spoken_numbers or [])
    formulas = spec.sheets[0].formulas or {}
    assert formulas.get("B6") == "=B5*0.2"


def test_title_is_never_checked_against_spoken_numbers() -> None:
    # "Bütçe 2026" -- 2026 in the title is a label, not part of the structure.
    spec = ArtifactSpec.model_validate(_load("tests/fixtures/artifacts/specs/butce-tablosu.json"))
    assert 2026.0 not in spec._structure_numbers()
    assert "2026" in spec.title
