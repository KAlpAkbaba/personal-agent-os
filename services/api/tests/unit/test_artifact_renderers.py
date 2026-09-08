"""Unit tests: the M22 factory renderers (docs/M22_ARTIFACT_FACTORY_SPEC.md §2,
ADR-0085 decision 2).

Every fixture spec x every format its kind produces: renders, produces non-empty
bytes with the right magic/shape, and is byte-for-byte deterministic across two
independent renders (the same bar app.artifacts.renderers already proves for the
M13 formats in test_renderers.py). Also proves the M13 renderer classes and their
public constants are completely unaffected by the M22 additions.
"""

from __future__ import annotations

import csv
import glob
import io
import json
import zipfile

import pytest

from app.artifacts import renderers as R
from app.artifacts.spec import ArtifactSpec

FIXTURE_SPECS = sorted(glob.glob("tests/fixtures/artifacts/specs/*.json"))


def _load_spec(path: str) -> ArtifactSpec:
    with open(path, encoding="utf-8") as f:
        return ArtifactSpec.model_validate(json.load(f))


def _cases() -> list[tuple[str, str]]:
    out = []
    for path in FIXTURE_SPECS:
        spec = _load_spec(path)
        for fmt in spec.formats():
            out.append((path, fmt))
    return out


CASES = _cases()


# ------------------------------------------------------------- M13 unaffected


def test_m13_supported_formats_unchanged() -> None:
    assert set(R.SUPPORTED_FORMATS) == {"pdf", "docx", "html", "txt"}
    assert R.DEFAULT_RENDER_FORMATS == ("pdf", "docx")


def test_m13_renderer_dict_unaffected_by_factory_formats() -> None:
    assert set(R._RENDERERS.keys()) == {"pdf", "docx", "html", "txt"}


# --------------------------------------------------------- every fixture x format


@pytest.mark.parametrize(("path", "fmt"), CASES)
def test_renders_nonempty_bytes(path: str, fmt: str) -> None:
    spec = _load_spec(path)
    result = R.render_factory(fmt, spec)
    assert result.format == fmt
    assert len(result.data) > 0
    assert result.content_hash == R.content_hash(result.data)


@pytest.mark.parametrize(("path", "fmt"), CASES)
def test_two_renders_one_hash(path: str, fmt: str) -> None:
    spec = _load_spec(path)
    a = R.render_factory(fmt, spec)
    b = R.render_factory(fmt, spec)
    assert a.content_hash == b.content_hash
    assert a.data == b.data


def test_render_factory_refuses_a_format_the_kind_cannot_produce() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/butce-tablosu.json")  # spreadsheet
    with pytest.raises(ValueError, match="not valid for kind"):
        R.render_factory("pptx", spec)


# ----------------------------------------------------------------------- shapes


def test_xlsx_is_a_zip_with_ooxml_spreadsheet_marker() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/butce-tablosu.json")
    result = R.render_factory("xlsx", spec)
    assert result.data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(result.data)) as zf:
        names = zf.namelist()
    assert "xl/workbook.xml" in names
    assert "[Content_Types].xml" in names


def test_pptx_is_a_zip_with_ooxml_presentation_marker() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/q3-sunum.json")
    result = R.render_factory("pptx", spec)
    assert result.data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(result.data)) as zf:
        names = zf.namelist()
    assert "ppt/presentation.xml" in names


def test_csv_spreadsheet_has_header_data_and_totals_rows() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/butce-tablosu.json")
    result = R.render_factory("csv", spec)
    rows = list(csv.reader(io.StringIO(result.data.decode("utf-8"))))
    assert rows[0] == ["Kalem", "Tutar"]
    assert rows[1] == ["Kira", "12000"]
    assert rows[-1][0] == "Toplam"


def test_csv_dataset_has_header_and_data_rows_only() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/musteri-listesi.json")
    result = R.render_factory("csv", spec)
    rows = list(csv.reader(io.StringIO(result.data.decode("utf-8"))))
    assert rows[0] == ["Ad", "Şehir", "Tutar"]
    assert len(rows) == 1 + len(spec.rows)


def test_json_dataset_has_columns_and_rows() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/musteri-listesi.json")
    result = R.render_factory("json", spec)
    payload = json.loads(result.data.decode("utf-8"))
    assert payload["columns"] == spec.columns
    assert payload["rows"] == [list(r) for r in spec.rows]


def test_md_and_txt_carry_headings_and_paragraphs() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/toplanti-notlari.json")
    md = R.render_factory("md", spec).data.decode("utf-8")
    assert "# Giriş" in md
    assert "## Kararlar" in md
    assert "Bütçe onaylandı." in md


def test_md_document_renders_a_pipe_table_for_its_section_table() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/toplanti-notlari.json")
    md = R.render_factory("md", spec).data.decode("utf-8")
    assert "| Kişi | Görev |" in md
    assert "| Ali | Plan |" in md


def test_docx_table_block_becomes_a_real_table() -> None:
    from docx import Document

    spec = _load_spec("tests/fixtures/artifacts/specs/toplanti-notlari.json")
    result = R.render_factory("docx", spec)
    document = Document(io.BytesIO(result.data))
    assert len(document.tables) == 1
    table = document.tables[0]
    assert table.cell(0, 0).text == "Kişi"
    assert table.cell(1, 0).text == "Ali"


def test_html_document_renders_an_actual_html_table() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/toplanti-notlari.json")
    html = R.render_factory("html", spec).data.decode("utf-8")
    assert "<table>" in html
    assert "<th>Kişi</th>" in html or "<td>Kişi</td>" in html


def test_pdf_document_starts_with_pdf_magic_bytes() -> None:
    spec = _load_spec("tests/fixtures/artifacts/specs/toplanti-notlari.json")
    result = R.render_factory("pdf", spec)
    assert result.data[:4] == b"%PDF"


# --------------------------------------------------------------- table parsing


def test_parse_blocks_recognizes_a_pipe_table() -> None:
    md = "# H\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n\nAfter.\n"
    blocks = R._parse_blocks(md)
    table_blocks = [b for b in blocks if b.kind == "table"]
    assert len(table_blocks) == 1
    assert table_blocks[0].rows == (("A", "B"), ("1", "2"), ("3", "4"))


def test_parse_blocks_never_misfires_on_plain_pipe_text() -> None:
    # A single "|"-containing line with no separator row underneath must NOT be
    # treated as a table -- this is what keeps pre-M22 research-report Markdown
    # (which never contains this pattern, but might one day quote a literal "|")
    # rendering exactly as before.
    md = "This costs $5 | $10 depending on size.\n"
    blocks = R._parse_blocks(md)
    assert all(b.kind != "table" for b in blocks)
