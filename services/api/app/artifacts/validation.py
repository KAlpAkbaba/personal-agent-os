"""Reopen, parse, compare (docs/M22_ARTIFACT_FACTORY_SPEC.md §3, ADR-0085 decision 3).

``validate(spec, fmt, data) -> ValidationReport`` independently re-derives, from the
``ArtifactSpec`` alone, what a correctly rendered ``data`` blob must contain, then
reopens ``data`` with an INDEPENDENT reader per format — never the renderer's own
in-memory object model — and compares. A render whose validation fails is kept with
``state = invalid`` (app/artifacts/factory.py) and the receipt names the failing ref;
nothing invalid is ever presented to the owner as done.

Readers, one per format family:

* DOCX -> ``python-docx`` (heading paragraphs matched by style name + text — the SAME
  ``Title``/``Heading 1``/``Heading 2`` mapping ``renderers.DocxRenderer`` writes for
  section levels 1/2/3).
* XLSX -> ``openpyxl`` with ``data_only=False`` (a formula cell compares by its raw
  formula text, never an evaluated value the reader might not even have cached).
* PPTX -> ``python-pptx`` (slide title shape + every text-frame paragraph).
* PDF -> ``pypdf`` text extraction (a PDF carries no addressable heading structure, so
  a heading/paragraph is checked the same way HTML/MD/TXT are: present somewhere in
  the extracted text).
* CSV/JSON/MD/HTML/TXT -> the standard library (``csv``, ``json``, ``html.parser``).

Resource bounds (M21 security review's rule, carried over: "every third-party format
your validators parse needs a resource bound, with a test"): every OOXML package
(DOCX/XLSX/PPTX are all zip-of-XML) has its central directory inspected — total
uncompressed size, part count, and any single part's compression ratio — BEFORE an
independent-reader library is asked to parse it; a PDF's page count and per-page/total
extracted-text length are capped; CSV/MD/HTML/TXT are capped by raw byte size; JSON
is capped before the whole document is parsed (it needs the whole document to answer
"what are the top-level keys", so unlike the others there is no bounded-prefix reading
here — same reasoning M21's own JSON structure bound used). A bound violation is
reported as a failing validation (``state = invalid``), never an unhandled exception.
"""

from __future__ import annotations

import io
import json as json_module
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from docx import Document
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from pptx import Presentation
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.artifacts.renderers import _safe_sheet_name
from app.artifacts.spec import (
    KIND_DATASET,
    KIND_DOCUMENT,
    KIND_PAGE,
    KIND_PRESENTATION,
    KIND_SPREADSHEET,
    ArtifactSpec,
)

# ------------------------------------------------------------------- resource bounds

#: The Cloud Core's own render download bound (ADR-0085 decision 4) applied
#: defensively to every format validate() reopens, not only OOXML packages.
MAX_INPUT_BYTES = 50 * 1024 * 1024
#: JSON needs the whole document to answer "what are the top-level keys" (M21's own
#: reasoning for its structure bound) — capped well below the general input bound.
MAX_JSON_BYTES = 8 * 1024 * 1024
#: OOXML (DOCX/XLSX/PPTX) central-directory bounds, mirroring the device-side
#: ContainerGuard (docs/DECISIONS.md ADR-0084 addendum 3, closed High finding).
MAX_OOXML_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_OOXML_PARTS = 10_000
MAX_OOXML_PART_RATIO = 100
MAX_OOXML_PART_RATIO_FLOOR_BYTES = 1 * 1024 * 1024
#: PDF: page count and extracted-text bounds (pypdf has no PdfPig-style per-filter
#: streaming counter, so what is bounded here is what THIS module controls — the
#: page count and how much extracted text is kept, not the decoder's own peak memory).
MAX_PDF_PAGES = 200
MAX_PDF_PAGE_TEXT_CHARS = 256 * 1024
MAX_PDF_TOTAL_TEXT_CHARS = 8 * 1024 * 1024


class ArtifactValidationBoundError(Exception):
    """Raised internally when a resource bound trips; always caught into a failing
    :class:`ValidationReport`, never allowed to propagate as an unhandled exception."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _check_ooxml_bounds(data: bytes) -> None:
    import zipfile

    if len(data) > MAX_INPUT_BYTES:
        raise ArtifactValidationBoundError("input_too_large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArtifactValidationBoundError("not_a_zip") from exc
    infos = zf.infolist()
    if len(infos) > MAX_OOXML_PARTS:
        raise ArtifactValidationBoundError("too_many_parts")
    total_uncompressed = sum(i.file_size for i in infos)
    if total_uncompressed > MAX_OOXML_UNCOMPRESSED_BYTES:
        raise ArtifactValidationBoundError("decompression_bound")
    for info in infos:
        if info.file_size <= MAX_OOXML_PART_RATIO_FLOOR_BYTES:
            continue
        compressed = max(info.compress_size, 1)
        if info.file_size / compressed > MAX_OOXML_PART_RATIO:
            raise ArtifactValidationBoundError("decompression_bound")


def _bounded_text(data: bytes) -> str:
    if len(data) > MAX_INPUT_BYTES:
        raise ArtifactValidationBoundError("input_too_large")
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------- the report


@dataclass(frozen=True, slots=True)
class ValidationReport:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    recomputed_totals: dict[str, float] = field(default_factory=dict)
    failing_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": self.checks,
            "recomputed_totals": self.recomputed_totals,
            "failing_refs": self.failing_refs,
        }


def _finish_report(
    checks: list[dict[str, Any]], recomputed_totals: dict[str, float]
) -> ValidationReport:
    failing_refs: list[str] = []
    for c in checks:
        if not c["ok"] and c["ref"] not in failing_refs:
            failing_refs.append(c["ref"])
    return ValidationReport(
        ok=len(failing_refs) == 0,
        checks=checks,
        recomputed_totals=recomputed_totals,
        failing_refs=failing_refs,
    )


def _bound_failure_report(reason: str) -> ValidationReport:
    ref = f"bound:{reason}"
    return ValidationReport(
        ok=False,
        checks=[
            {
                "ref": ref,
                "expected": "within resource bounds",
                "found": reason,
                "ok": False,
                "kind": "resource_bound",
            }
        ],
        recomputed_totals={},
        failing_refs=[ref],
    )


def _numbers_equal(found: Any, expected: Any) -> bool:
    if isinstance(found, bool) or isinstance(expected, bool):
        return found == expected
    if isinstance(found, int | float) and isinstance(expected, int | float):
        return float(found) == float(expected)
    return found == expected


def _format_number(value: float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _aggregate(values: list[float], agg: str) -> float:
    if not values:
        return 0.0
    return sum(values) if agg == "sum" else sum(values) / len(values)


def _numeric_row_values(rows: list[list[Any]], col_index: int) -> list[float]:
    out: list[float] = []
    for row in rows:
        value = row[col_index]
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            out.append(float(value))
    return out


# ------------------------------------------------------------------ document / page


_DOCX_HEADING_STYLE = {1: "Title", 2: "Heading 1", 3: "Heading 2"}


def _docx_full_text(document: Any) -> str:
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _docx_heading_ok(document: Any, level: int, heading: str) -> bool:
    style_name = _DOCX_HEADING_STYLE.get(level, "Heading 3")
    for p in document.paragraphs:
        if p.text.strip() == heading and p.style is not None and p.style.name == style_name:
            return True
    return False


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_to_text(html_text: str) -> str:
    parser = _TextExtractor()
    parser.feed(html_text)
    return "".join(parser.parts)


def _extract_pdf_text(data: bytes) -> str:
    if len(data) > MAX_INPUT_BYTES:
        raise ArtifactValidationBoundError("input_too_large")
    try:
        reader = PdfReader(io.BytesIO(data))
        page_count = len(reader.pages)
    except PdfReadError as exc:
        raise ArtifactValidationBoundError("parse_failed") from exc
    if page_count > MAX_PDF_PAGES:
        raise ArtifactValidationBoundError("page_bound")
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        if len(text) > MAX_PDF_PAGE_TEXT_CHARS:
            text = text[:MAX_PDF_PAGE_TEXT_CHARS]
        parts.append(text)
        total += len(text)
        if total > MAX_PDF_TOTAL_TEXT_CHARS:
            break
    return "\n".join(parts)


def _document_checks(spec: ArtifactSpec, extracted: str, *, document: Any = None) -> list[dict]:
    checks: list[dict[str, Any]] = []
    assert spec.sections is not None
    for section in spec.sections:
        ref = f"h{section.level}:{section.heading}"
        ok = (
            _docx_heading_ok(document, section.level, section.heading)
            if document is not None
            else section.heading in extracted
        )
        checks.append(
            {
                "ref": ref,
                "expected": section.heading,
                "found": section.heading if ok else None,
                "ok": ok,
                "kind": "heading",
            }
        )
        for paragraph in section.paragraphs:
            ok_p = paragraph in extracted
            checks.append(
                {
                    "ref": f"text:{paragraph[:60]}",
                    "expected": paragraph,
                    "found": paragraph if ok_p else None,
                    "ok": ok_p,
                    "kind": "contains",
                }
            )
        if section.bullets:
            for bullet in section.bullets:
                ok_b = bullet in extracted
                checks.append(
                    {
                        "ref": f"text:{bullet[:60]}",
                        "expected": bullet,
                        "found": bullet if ok_b else None,
                        "ok": ok_b,
                        "kind": "contains",
                    }
                )
        if section.table:
            for col in section.table.columns:
                ok_c = col in extracted
                checks.append(
                    {
                        "ref": f"text:{col[:60]}",
                        "expected": col,
                        "found": col if ok_c else None,
                        "ok": ok_c,
                        "kind": "contains",
                    }
                )
            for row in section.table.rows:
                for cell in row:
                    cell_s = str(cell)
                    ok_cell = cell_s in extracted
                    checks.append(
                        {
                            "ref": f"text:{cell_s[:60]}",
                            "expected": cell_s,
                            "found": cell_s if ok_cell else None,
                            "ok": ok_cell,
                            "kind": "contains",
                        }
                    )
    return checks


def _validate_document(spec: ArtifactSpec, fmt: str, data: bytes) -> ValidationReport:
    try:
        document = None
        if fmt == "docx":
            _check_ooxml_bounds(data)
            document = Document(io.BytesIO(data))
            extracted = _docx_full_text(document)
        elif fmt == "pdf":
            extracted = _extract_pdf_text(data)
        elif fmt == "html":
            extracted = _html_to_text(_bounded_text(data))
        elif fmt in ("md", "txt"):
            extracted = _bounded_text(data)
        else:
            raise ValueError(f"unsupported document format: {fmt!r}")
    except ArtifactValidationBoundError as exc:
        return _bound_failure_report(exc.reason)

    checks = _document_checks(spec, extracted, document=document)
    return _finish_report(checks, {})


# -------------------------------------------------------------------- spreadsheet


def _totals_expected_row(sheet: Any) -> tuple[list[Any], dict[str, float]] | None:
    if not sheet.totals:
        return None
    row: list[Any] = ["" for _ in sheet.columns]
    row[0] = "Toplam"
    recomputed: dict[str, float] = {}
    for col_name, agg in sheet.totals.items():
        idx = sheet.columns.index(col_name)
        computed = _aggregate(_numeric_row_values(sheet.rows, idx), agg)
        row[idx] = computed
        recomputed[f"{sheet.name}.{col_name}"] = computed
    return row, recomputed


def _validate_xlsx(spec: ArtifactSpec, data: bytes) -> ValidationReport:
    try:
        _check_ooxml_bounds(data)
        workbook = load_workbook(io.BytesIO(data), data_only=False)
    except ArtifactValidationBoundError as exc:
        return _bound_failure_report(exc.reason)
    except (ValueError, KeyError) as exc:
        # LOW security-review finding (ADR-0085 addendum 6): a malicious inner XML part
        # (an entity-expansion/"billion laughs" payload well within the zip-level size
        # bounds above) is refused by openpyxl's own XML layer -- with defusedxml
        # active (see docs/THIRD_PARTY_COMPONENTS.md) that refusal surfaces as a
        # ``ValueError`` (openpyxl wraps whatever its XML backend raised), not the
        # ``ArtifactValidationBoundError`` this module raises itself; a missing
        # required part surfaces as ``KeyError``. Either way this is "refused",
        # never "crashed" (module docstring's own invariant).
        return _bound_failure_report(f"xml_parse_refused:{type(exc).__name__}")

    checks: list[dict[str, Any]] = []
    recomputed_totals: dict[str, float] = {}
    assert spec.sheets is not None
    for sheet_spec in spec.sheets:
        sheet_name = _safe_sheet_name(sheet_spec.name)
        if sheet_name not in workbook.sheetnames:
            checks.append(
                {
                    "ref": f"sheet:{sheet_spec.name}",
                    "expected": sheet_name,
                    "found": workbook.sheetnames,
                    "ok": False,
                    "kind": "sheet_missing",
                }
            )
            continue
        worksheet = workbook[sheet_name]
        # HIGH security-review finding (ADR-0085 addendum 6): a cell whose STORED TEXT
        # happens to equal the spec's own expected literal (e.g. the spec cell IS the
        # string "=HYPERLINK(...)") passes the value-equality check above/below even
        # though the file now carries a LIVE formula, not the literal text the spec
        # asked for — ``data_type`` is what actually decides what Excel does when it
        # opens the file, and openpyxl exposes it independently of ``.value``. Any cell
        # whose coordinate is not explicitly declared in ``formulas`` must never be
        # ``data_type == "f"``, regardless of whether its text happens to match.
        declared_coords = set(sheet_spec.formulas or {})

        def _unexpected_formula_check(
            cell_obj: Any, ref: str, coord: str, *, _declared: set[str] = declared_coords
        ) -> dict[str, Any] | None:
            if coord in _declared or cell_obj.data_type != "f":
                return None
            return {
                "ref": ref,
                "expected": "not a formula",
                "found": "formula",
                "ok": False,
                "kind": "unexpected_formula",
            }

        for c, col in enumerate(sheet_spec.columns, start=1):
            ref = f"sheet:{sheet_spec.name}!{get_column_letter(c)}1"
            cell_obj = worksheet.cell(row=1, column=c)
            found = cell_obj.value
            checks.append(
                {"ref": ref, "expected": col, "found": found, "ok": found == col, "kind": "text"}
            )
            extra = _unexpected_formula_check(cell_obj, ref, f"{get_column_letter(c)}1")
            if extra is not None:
                checks.append(extra)
        for r, row in enumerate(sheet_spec.rows, start=2):
            for c, value in enumerate(row, start=1):
                ref = f"sheet:{sheet_spec.name}!{get_column_letter(c)}{r}"
                cell_obj = worksheet.cell(row=r, column=c)
                found = cell_obj.value
                checks.append(
                    {
                        "ref": ref,
                        "expected": value,
                        "found": found,
                        "ok": _numbers_equal(found, value),
                        "kind": "value" if isinstance(value, int | float) else "text",
                    }
                )
                extra = _unexpected_formula_check(cell_obj, ref, f"{get_column_letter(c)}{r}")
                if extra is not None:
                    checks.append(extra)
        totals = _totals_expected_row(sheet_spec)
        if totals is not None:
            totals_row, totals_recomputed = totals
            recomputed_totals.update(totals_recomputed)
            totals_row_idx = 2 + len(sheet_spec.rows)
            label_ref = f"sheet:{sheet_spec.name}!A{totals_row_idx}"
            label_found = worksheet.cell(row=totals_row_idx, column=1).value
            checks.append(
                {
                    "ref": label_ref,
                    "expected": "Toplam",
                    "found": label_found,
                    "ok": label_found == "Toplam",
                    "kind": "text",
                }
            )
            for c, value in enumerate(totals_row, start=1):
                if value == "" or c == 1:
                    continue
                ref = f"sheet:{sheet_spec.name}!{get_column_letter(c)}{totals_row_idx}"
                found = worksheet.cell(row=totals_row_idx, column=c).value
                checks.append(
                    {
                        "ref": ref,
                        "expected": value,
                        "found": found,
                        "ok": _numbers_equal(found, value),
                        "kind": "total",
                    }
                )
        if sheet_spec.formulas:
            for cell_ref, raw in sheet_spec.formulas.items():
                ref = f"sheet:{sheet_spec.name}!{cell_ref}"
                found = worksheet[cell_ref].value
                checks.append(
                    {
                        "ref": ref,
                        "expected": raw,
                        "found": found,
                        "ok": found == raw,
                        "kind": "formula" if raw.startswith("=") else "text",
                    }
                )
    return _finish_report(checks, recomputed_totals)


def _validate_spreadsheet_csv(spec: ArtifactSpec, data: bytes) -> ValidationReport:
    try:
        text = _bounded_text(data)
    except ArtifactValidationBoundError as exc:
        return _bound_failure_report(exc.reason)
    lines = text.splitlines()
    assert spec.sheets is not None
    sheet = spec.sheets[0]
    rows_expected: list[list[str]] = [[str(c) for c in sheet.columns]]
    rows_expected.extend([str(c) for c in row] for row in sheet.rows)
    recomputed_totals: dict[str, float] = {}
    totals = _totals_expected_row(sheet)
    if totals is not None:
        totals_row, totals_recomputed = totals
        recomputed_totals.update(totals_recomputed)
        rows_expected.append(
            [_format_number(c) if isinstance(c, int | float) else str(c) for c in totals_row]
        )

    checks: list[dict[str, Any]] = []
    for i, cells in enumerate(rows_expected, start=1):
        ref = f"r{i}"
        line = lines[i - 1] if i - 1 < len(lines) else ""
        for cell in cells:
            if cell == "":
                continue
            ok = cell in line
            checks.append(
                {"ref": ref, "expected": cell, "found": line, "ok": ok, "kind": "contains"}
            )
    return _finish_report(checks, recomputed_totals)


def _validate_spreadsheet(spec: ArtifactSpec, fmt: str, data: bytes) -> ValidationReport:
    if fmt == "xlsx":
        return _validate_xlsx(spec, data)
    if fmt == "csv":
        return _validate_spreadsheet_csv(spec, data)
    raise ValueError(f"unsupported spreadsheet format: {fmt!r}")


# ------------------------------------------------------------------- presentation


def _pptx_slide_text(slide: Any) -> str:
    parts: list[str] = []
    for shape in slide.shapes:
        if getattr(shape, "has_text_frame", False):
            for paragraph in shape.text_frame.paragraphs:
                parts.append("".join(run.text for run in paragraph.runs))
    return "\n".join(parts)


def _validate_pptx(spec: ArtifactSpec, data: bytes) -> ValidationReport:
    try:
        _check_ooxml_bounds(data)
        presentation = Presentation(io.BytesIO(data))
    except ArtifactValidationBoundError as exc:
        return _bound_failure_report(exc.reason)

    assert spec.slides is not None
    slides = list(presentation.slides)
    checks: list[dict[str, Any]] = [
        {
            "ref": "structure:slide_count",
            "expected": {"slide_count": len(spec.slides)},
            "found": {"slide_count": len(slides)},
            "ok": len(slides) == len(spec.slides),
            "kind": "structure",
        }
    ]
    for i, slide_spec in enumerate(spec.slides, start=1):
        ref = f"s{i}"
        if i - 1 < len(slides):
            slide = slides[i - 1]
            title_shape = slide.shapes.title
            title_text = title_shape.text if title_shape is not None else ""
            all_text = _pptx_slide_text(slide)
        else:
            title_text = ""
            all_text = ""
        checks.append(
            {
                "ref": ref,
                "expected": slide_spec.title,
                "found": title_text,
                "ok": title_text == slide_spec.title,
                "kind": "title",
            }
        )
        for bullet in slide_spec.bullets:
            ok_bullet = bullet in all_text
            checks.append(
                {
                    "ref": ref,
                    "expected": bullet,
                    "found": all_text if not ok_bullet else bullet,
                    "ok": ok_bullet,
                    "kind": "contains",
                }
            )
    return _finish_report(checks, {})


# ----------------------------------------------------------------------- dataset


def _validate_dataset_csv(spec: ArtifactSpec, data: bytes) -> ValidationReport:
    try:
        text = _bounded_text(data)
    except ArtifactValidationBoundError as exc:
        return _bound_failure_report(exc.reason)
    lines = text.splitlines()
    assert spec.columns is not None and spec.rows is not None
    rows_expected: list[list[str]] = [[str(c) for c in spec.columns]]
    rows_expected.extend([str(c) for c in row] for row in spec.rows)
    checks: list[dict[str, Any]] = []
    for i, cells in enumerate(rows_expected, start=1):
        ref = f"r{i}"
        line = lines[i - 1] if i - 1 < len(lines) else ""
        for cell in cells:
            if cell == "":
                continue
            ok = cell in line
            checks.append(
                {"ref": ref, "expected": cell, "found": line, "ok": ok, "kind": "contains"}
            )
    return _finish_report(checks, {})


def _validate_dataset_json(spec: ArtifactSpec, data: bytes) -> ValidationReport:
    if len(data) > MAX_JSON_BYTES:
        return _bound_failure_report("too_large")
    try:
        parsed = json_module.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json_module.JSONDecodeError):
        return _bound_failure_report("parse_failed")

    assert spec.columns is not None and spec.rows is not None
    checks: list[dict[str, Any]] = []
    found_columns = parsed.get("columns") if isinstance(parsed, dict) else None
    checks.append(
        {
            "ref": "$.columns",
            "expected": list(spec.columns),
            "found": found_columns,
            "ok": found_columns == list(spec.columns),
            "kind": "equals",
        }
    )
    found_rows = parsed.get("rows") if isinstance(parsed, dict) else None
    found_count = len(found_rows) if isinstance(found_rows, list) else None
    checks.append(
        {
            "ref": "$.rows",
            "expected": len(spec.rows),
            "found": found_count,
            "ok": found_count == len(spec.rows),
            "kind": "count",
        }
    )
    if isinstance(found_rows, list):
        for i, row in enumerate(spec.rows):
            ref = f"$.rows[{i}]"
            found_row = found_rows[i] if i < len(found_rows) else None
            checks.append(
                {
                    "ref": ref,
                    "expected": list(row),
                    "found": found_row,
                    "ok": found_row == list(row),
                    "kind": "equals",
                }
            )
    return _finish_report(checks, {})


def _validate_dataset(spec: ArtifactSpec, fmt: str, data: bytes) -> ValidationReport:
    if fmt == "csv":
        return _validate_dataset_csv(spec, data)
    if fmt == "json":
        return _validate_dataset_json(spec, data)
    raise ValueError(f"unsupported dataset format: {fmt!r}")


# --------------------------------------------------------------------- dispatch


def validate(spec: ArtifactSpec, fmt: str, data: bytes) -> ValidationReport:
    """Reopen ``data`` (a render of ``spec`` in format ``fmt``) with an independent
    reader and compare against what the spec says must be there. Never trusts the
    renderer's own object model — a fresh parse, every time (ADR-0085 decision 3)."""
    if fmt not in spec.formats():
        raise ValueError(f"format {fmt!r} is not valid for kind {spec.kind!r}")
    if spec.kind in (KIND_DOCUMENT, KIND_PAGE):
        return _validate_document(spec, fmt, data)
    if spec.kind == KIND_SPREADSHEET:
        return _validate_spreadsheet(spec, fmt, data)
    if spec.kind == KIND_PRESENTATION:
        return _validate_pptx(spec, data)
    if spec.kind == KIND_DATASET:
        return _validate_dataset(spec, fmt, data)
    raise ValueError(f"unknown kind: {spec.kind!r}")  # pragma: no cover - Literal-closed


__all__ = [
    "ArtifactValidationBoundError",
    "MAX_INPUT_BYTES",
    "MAX_JSON_BYTES",
    "MAX_OOXML_PARTS",
    "MAX_OOXML_PART_RATIO",
    "MAX_OOXML_UNCOMPRESSED_BYTES",
    "MAX_PDF_PAGES",
    "MAX_PDF_PAGE_TEXT_CHARS",
    "MAX_PDF_TOTAL_TEXT_CHARS",
    "ValidationReport",
    "validate",
]
