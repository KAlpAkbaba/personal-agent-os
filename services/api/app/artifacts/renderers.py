"""Deterministic renderers: canonical Markdown -> PDF / DOCX / HTML / TXT.

Design (task deliverable 3):

- `Renderer` is the seam; concrete renderers are pure functions of
  (title, canonical_markdown) -> bytes with NO clock/network/randomness, so the
  same input yields byte-identical output and a stable content_hash.
- Determinism specifics:
  - PDF (fpdf2): creation/modification date pinned; a bundled Unicode font
    (DejaVu Sans, regular + bold, under services/api/app/artifacts/fonts) is
    embedded so full Turkish orthography (ş/ğ/ı/İ/ç/ö/ü) and typographic
    punctuation render identically on every host — Turkish is first-class, so
    the PDF (the default mobile presentation format) must not fold glyphs.
    fpdf2 subsets and embeds the font deterministically.
  - DOCX (python-docx): core properties pinned to a fixed timestamp, then the
    package zip is re-emitted with fixed member dates/order so bytes are stable.
  - HTML/TXT: naturally deterministic.

M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §2, ADR-0085 decision 2) extends this module
with five more formats driven directly by an `ArtifactSpec` (app.artifacts.spec)
rather than by canonical Markdown: `XlsxRenderer`/`CsvRenderer` (spreadsheet/dataset),
`PptxRenderer` (presentation), `JsonRenderer` (dataset) and `MdRenderer` (document/page,
the same canonical Markdown this module already builds for PDF/DOCX/HTML/TXT). The
existing `SUPPORTED_FORMATS`/`_RENDERERS`/`Renderer`/`render()` free function are left
completely untouched (`tests/unit/test_renderers.py::test_all_formats_supported` pins
`SUPPORTED_FORMATS` exactly) — the new formats live behind a second registry
(`FactoryRenderer`/`render_factory()`) and reuse the OLD `render()` free function for the
four Markdown-based formats a document/page spec can still produce.
"""

import csv as csv_module
import hashlib
import io
import json as json_module
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import markdown as markdown_lib
from docx import Document
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from openpyxl import Workbook
from pptx import Presentation

if TYPE_CHECKING:
    from app.artifacts.spec import ArtifactSpec, Sheet

_FONT_DIR = Path(__file__).parent / "fonts"
_FONT_FAMILY = "DejaVu"
_FONT_REGULAR = _FONT_DIR / "DejaVuSans.ttf"
_FONT_BOLD = _FONT_DIR / "DejaVuSans-Bold.ttf"

# Fixed epoch used for every timestamp a renderer would otherwise pull from the
# clock. Any constant works; it just must never be "now".
_FIXED_DT = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)

# Render format identifiers.
FORMAT_PDF = "pdf"
FORMAT_DOCX = "docx"
FORMAT_HTML = "html"
FORMAT_TXT = "txt"
# M22 additions (ADR-0085 decision 2).
FORMAT_MD = "md"
FORMAT_XLSX = "xlsx"
FORMAT_CSV = "csv"
FORMAT_PPTX = "pptx"
FORMAT_JSON = "json"

MIME_TYPES = {
    FORMAT_PDF: "application/pdf",
    FORMAT_DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FORMAT_HTML: "text/html; charset=utf-8",
    FORMAT_TXT: "text/plain; charset=utf-8",
    FORMAT_MD: "text/markdown; charset=utf-8",
    FORMAT_XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FORMAT_CSV: "text/csv; charset=utf-8",
    FORMAT_PPTX: (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    FORMAT_JSON: "application/json",
}

EXTENSIONS = {
    FORMAT_PDF: "pdf",
    FORMAT_DOCX: "docx",
    FORMAT_HTML: "html",
    FORMAT_TXT: "txt",
    FORMAT_MD: "md",
    FORMAT_XLSX: "xlsx",
    FORMAT_CSV: "csv",
    FORMAT_PPTX: "pptx",
    FORMAT_JSON: "json",
}

#: Every format the factory can produce across all five kinds (ArtifactSpec.formats()
#: narrows this per spec) — used by routes/tests to validate a bare ``fmt`` path param.
FACTORY_FORMATS: tuple[str, ...] = (
    FORMAT_DOCX,
    FORMAT_PDF,
    FORMAT_HTML,
    FORMAT_MD,
    FORMAT_TXT,
    FORMAT_XLSX,
    FORMAT_CSV,
    FORMAT_PPTX,
    FORMAT_JSON,
)


def content_hash(data: bytes) -> str:
    """Canonical content hash used across artifact_versions/artifact_renders."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class RenderResult:
    format: str
    data: bytes
    mime_type: str
    content_hash: str


@runtime_checkable
class Renderer(Protocol):
    format: str
    mime_type: str

    def render(self, *, title: str, canonical_markdown: str) -> bytes:
        ...


# ------------------------------------------------------------- markdown parsing


@dataclass(frozen=True, slots=True)
class _Block:
    kind: str  # "h1" | "h2" | "h3" | "li" | "p" | "blank" | "table"
    text: str
    #: Only set when kind == "table": row 0 is the header, every row a tuple of cell
    #: strings (M22 addition, ADR-0085 decision 2 — a document/page section's table).
    rows: tuple[tuple[str, ...], ...] | None = None


#: A GFM pipe-table row: "| a | b |" (leading/trailing pipe required — the only shape
#: :func:`build_document_markdown` ever emits, so this is deliberately not lenient).
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
#: A separator cell: optional colons around one or more dashes ("---", ":--", "--:").
_TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def _split_table_row(line: str) -> tuple[str, ...]:
    inner = line.strip()[1:-1]
    return tuple(cell.strip() for cell in inner.split("|"))


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not _TABLE_ROW_RE.match(stripped):
        return False
    cells = _split_table_row(stripped)
    return len(cells) > 0 and all(_TABLE_SEP_CELL_RE.match(c) for c in cells)


def _parse_blocks(markdown_text: str) -> list[_Block]:
    """Minimal, deterministic block parser sufficient for our canonical bodies.

    A GFM pipe table (a header row immediately followed by a "|---|---|" separator
    row) is collected into one "table" block; every other line is classified as
    before. This never fires on pre-M22 canonical Markdown (research reports never
    emit a "|"-prefixed line), so existing content_hash values are unaffected.
    """
    raw_lines = [line.rstrip() for line in markdown_text.splitlines()]
    blocks: list[_Block] = []
    i = 0
    n = len(raw_lines)
    while i < n:
        line = raw_lines[i]
        if (
            _TABLE_ROW_RE.match(line.strip())
            and i + 1 < n
            and _is_table_separator(raw_lines[i + 1])
        ):
            rows: list[tuple[str, ...]] = [_split_table_row(line.strip())]
            i += 2
            while i < n and _TABLE_ROW_RE.match(raw_lines[i].strip()):
                rows.append(_split_table_row(raw_lines[i].strip()))
                i += 1
            blocks.append(_Block("table", "", rows=tuple(rows)))
            continue
        if not line.strip():
            blocks.append(_Block("blank", ""))
        elif line.startswith("### "):
            blocks.append(_Block("h3", line[4:].strip()))
        elif line.startswith("## "):
            blocks.append(_Block("h2", line[3:].strip()))
        elif line.startswith("# "):
            blocks.append(_Block("h1", line[2:].strip()))
        elif line.lstrip().startswith("- "):
            blocks.append(_Block("li", line.lstrip()[2:].strip()))
        else:
            blocks.append(_Block("p", line.strip()))
        i += 1
    return blocks


_EMPHASIS = re.compile(r"\*\*(.+?)\*\*")


def _strip_inline(text: str) -> str:
    return _EMPHASIS.sub(r"\1", text)


# --------------------------------------------------------------------- TXT/HTML


class TxtRenderer:
    format = FORMAT_TXT
    mime_type = MIME_TYPES[FORMAT_TXT]

    def render(self, *, title: str, canonical_markdown: str) -> bytes:
        # The canonical Markdown is already human-readable; emit it verbatim with
        # a normalized trailing newline. Deterministic.
        text = canonical_markdown
        if not text.endswith("\n"):
            text += "\n"
        return text.encode("utf-8")


class HtmlRenderer:
    format = FORMAT_HTML
    mime_type = MIME_TYPES[FORMAT_HTML]

    def render(self, *, title: str, canonical_markdown: str) -> bytes:
        # Neutralize any raw HTML in the (possibly untrusted, once a web research
        # provider is wired) body BEFORE Markdown conversion. python-markdown has
        # no safe mode and passes raw HTML/<script> through. Our own canonical
        # Markdown structure uses only #, -, ** — never <, >, & — so escaping
        # those three characters across the whole body is lossless for our
        # markup and removes every HTML-injection vector. (M3 security review #1.)
        safe_markdown = (
            canonical_markdown.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        body = markdown_lib.markdown(
            safe_markdown, extensions=["extra", "sane_lists"]
        )
        escaped_title = (
            title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        html = (
            "<!DOCTYPE html>\n"
            '<html lang="tr">\n<head>\n'
            '<meta charset="utf-8">\n'
            f"<title>{escaped_title}</title>\n"
            "</head>\n<body>\n"
            f"{body}\n"
            "</body>\n</html>\n"
        )
        return html.encode("utf-8")


# ----------------------------------------------------------------------- PDF


class PdfRenderer:
    format = FORMAT_PDF
    mime_type = MIME_TYPES[FORMAT_PDF]

    def render(self, *, title: str, canonical_markdown: str) -> bytes:
        pdf = FPDF(format="A4", unit="mm")
        # Pin every clock-derived field so output bytes are stable.
        pdf.set_creation_date(_FIXED_DT)
        pdf.set_title(title)
        pdf.set_author("Personal Agent OS")
        pdf.set_producer("pagentos-artifacts")
        # Embed a bundled Unicode font so full Turkish renders on any host.
        pdf.add_font(_FONT_FAMILY, "", str(_FONT_REGULAR))
        pdf.add_font(_FONT_FAMILY, "B", str(_FONT_BOLD))
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        def line(h: float, s: str) -> None:
            # w=epw with a left-margin reset each line: the fpdf2 default leaves
            # the cursor at the right edge, which would collapse the next cell.
            pdf.multi_cell(pdf.epw, h, s, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        for block in _parse_blocks(canonical_markdown):
            text = _strip_inline(block.text)
            if block.kind == "blank":
                pdf.ln(3)
            elif block.kind == "h1":
                pdf.set_font(_FONT_FAMILY, "B", 18)
                line(9, text)
            elif block.kind == "h2":
                pdf.set_font(_FONT_FAMILY, "B", 14)
                line(8, text)
            elif block.kind == "h3":
                pdf.set_font(_FONT_FAMILY, "B", 12)
                line(7, text)
            elif block.kind == "li":
                pdf.set_font(_FONT_FAMILY, "", 11)
                line(6, f"  - {text}")
            elif block.kind == "table":
                assert block.rows is not None
                for row_idx, row in enumerate(block.rows):
                    cells = [_strip_inline(c) for c in row]
                    pdf.set_font(_FONT_FAMILY, "B" if row_idx == 0 else "", 10)
                    line(6, "  |  ".join(cells))
                pdf.ln(2)
            else:
                pdf.set_font(_FONT_FAMILY, "", 11)
                line(6, text)

        out = pdf.output()
        return bytes(out)


# ----------------------------------------------------------------------- DOCX


#: openpyxl's own writer (writer/excel.py) unconditionally stamps
#: ``workbook.properties.modified = datetime.now(...)`` at save time — AFTER a
#: renderer has already set it to `_FIXED_DT` — so `docProps/core.xml`'s
#: ``<dcterms:modified>`` carries the real wall clock no matter what the caller set
#: (discovered by this module's own determinism smoke test: two renders one process
#: apart could disagree). Patched here, byte-level, on every OOXML package
#: (DOCX/XLSX/PPTX) rather than trusting each library's save() to honour an
#: explicit override — a safety net against the same trap in a future library
#: version, not just today's openpyxl bug.
_CORE_XML_TIMESTAMP_RE = re.compile(
    rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)"
)
_FIXED_W3CDTF = b"2020-01-01T00:00:00Z"


def _pin_core_properties(data: bytes) -> bytes:
    return _CORE_XML_TIMESTAMP_RE.sub(rb"\g<1>" + _FIXED_W3CDTF + rb"\g<2>", data)


def _normalize_ooxml_zip(data: bytes) -> bytes:
    """Re-emit an OOXML package (DOCX/XLSX/PPTX are all zip-of-XML) with fixed member
    dates and sorted names, so the render is byte-stable given stable part contents.

    python-docx / openpyxl / python-pptx all write zip members with the current
    wall-clock time; normalizing the archive removes that non-determinism. The
    ``docProps/core.xml`` member additionally gets its created/modified timestamps
    pinned byte-level (see :func:`_pin_core_properties`) since at least one of these
    libraries re-stamps "modified" at save time regardless of what was set beforehand.
    """
    src = zipfile.ZipFile(io.BytesIO(data), "r")
    try:
        names = sorted(src.namelist())
        out_buf = io.BytesIO()
        with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            for name in names:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                content = src.read(name)
                if name == "docProps/core.xml":
                    content = _pin_core_properties(content)
                dst.writestr(info, content)
        return out_buf.getvalue()
    finally:
        src.close()


#: Back-compat alias — pre-M22 name, kept in case anything imports it by the old name.
_normalize_docx_zip = _normalize_ooxml_zip


class DocxRenderer:
    format = FORMAT_DOCX
    mime_type = MIME_TYPES[FORMAT_DOCX]

    def render(self, *, title: str, canonical_markdown: str) -> bytes:
        document = Document()
        props = document.core_properties
        props.title = title
        props.author = "Personal Agent OS"
        props.created = _FIXED_DT.replace(tzinfo=None)
        props.modified = _FIXED_DT.replace(tzinfo=None)
        props.revision = 1

        for block in _parse_blocks(canonical_markdown):
            text = _strip_inline(block.text)
            if block.kind == "blank":
                continue
            if block.kind == "h1":
                document.add_heading(text, level=0)
            elif block.kind == "h2":
                document.add_heading(text, level=1)
            elif block.kind == "h3":
                document.add_heading(text, level=2)
            elif block.kind == "li":
                document.add_paragraph(text, style="List Bullet")
            elif block.kind == "table":
                assert block.rows is not None
                n_cols = len(block.rows[0])
                table = document.add_table(rows=len(block.rows), cols=n_cols)
                for r, row in enumerate(block.rows):
                    for c, cell_text in enumerate(row):
                        cell = table.cell(r, c)
                        cell.text = _strip_inline(cell_text)
                        if r == 0:
                            for para in cell.paragraphs:
                                for run in para.runs:
                                    run.bold = True
                document.add_paragraph("")
            else:
                document.add_paragraph(text)

        buf = io.BytesIO()
        document.save(buf)
        return _normalize_docx_zip(buf.getvalue())


_RENDERERS: dict[str, Renderer] = {
    FORMAT_PDF: PdfRenderer(),
    FORMAT_DOCX: DocxRenderer(),
    FORMAT_HTML: HtmlRenderer(),
    FORMAT_TXT: TxtRenderer(),
}

SUPPORTED_FORMATS = tuple(_RENDERERS.keys())
DEFAULT_RENDER_FORMATS = (FORMAT_PDF, FORMAT_DOCX)


def get_renderer(fmt: str) -> Renderer:
    try:
        return _RENDERERS[fmt]
    except KeyError:
        raise ValueError(f"unsupported render format: {fmt!r}") from None


def render(fmt: str, *, title: str, canonical_markdown: str) -> RenderResult:
    renderer = get_renderer(fmt)
    data = renderer.render(title=title, canonical_markdown=canonical_markdown)
    return RenderResult(
        format=fmt,
        data=data,
        mime_type=renderer.mime_type,
        content_hash=content_hash(data),
    )


# =============================================================================
# M22 Artifact Factory (docs/M22_ARTIFACT_FACTORY_SPEC.md §2, ADR-0085 decision 2).
#
# A second, parallel seam: `FactoryRenderer.render_spec(spec) -> bytes`, keyed by
# format in `_FACTORY_RENDERERS`. The four Markdown-based formats a document/page
# spec can produce (PDF/DOCX/HTML/TXT) are NOT reimplemented here — they go through
# `build_document_markdown()` and then the EXISTING `render()` free function above,
# so the M13 renderer classes/tests are untouched and get table support for free.
# =============================================================================


@runtime_checkable
class FactoryRenderer(Protocol):
    format: str
    mime_type: str

    def render_spec(self, spec: "ArtifactSpec") -> bytes: ...


def _cell_text(value: object) -> str:
    return "" if value is None else str(value)


def _aggregate(values: list[float], agg: str) -> float:
    if not values:
        return 0.0
    return sum(values) if agg == "sum" else sum(values) / len(values)


def _numeric_values(rows: list[list], col_index: int) -> list[float]:
    out: list[float] = []
    for row in rows:
        value = row[col_index]
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            out.append(float(value))
    return out


def _totals_row(sheet: "Sheet") -> list[object] | None:
    """The literal totals row a spreadsheet render appends after its data rows, or
    None when the sheet carries no ``totals`` directive. Column 0 always gets the
    Turkish label "Toplam"; every other named column gets its computed aggregate as
    a plain VALUE (never a formula — spec §1/ADR-0085 decision 3: the validator
    recomputes this independently from the spec's own rows and compares against
    exactly this cell)."""
    if not sheet.totals:
        return None
    row: list[object] = ["" for _ in sheet.columns]
    row[0] = "Toplam"
    for col_name, agg in sheet.totals.items():
        idx = sheet.columns.index(col_name)
        row[idx] = _aggregate(_numeric_values(sheet.rows, idx), agg)
    return row


# ------------------------------------------------------------- document/page: MD


def build_document_markdown(spec: "ArtifactSpec") -> str:
    """Canonical Markdown for a document/page spec's ``sections`` — headings
    (``#``/``##``/``###`` by ``level``), paragraphs, a bulleted list, and a GFM pipe
    table, exactly what :func:`_parse_blocks` above understands. Reused for the
    "md" format itself (:class:`MdRenderer`) and as the input to the existing
    PDF/DOCX/HTML/TXT renderers via the old ``render()`` free function."""
    lines: list[str] = []
    assert spec.sections is not None
    for section in spec.sections:
        lines.append("#" * section.level + " " + section.heading)
        lines.append("")
        for paragraph in section.paragraphs:
            lines.append(paragraph)
            lines.append("")
        if section.bullets:
            for bullet in section.bullets:
                lines.append(f"- {bullet}")
            lines.append("")
        if section.table:
            cols = section.table.columns
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("| " + " | ".join("---" for _ in cols) + " |")
            for row in section.table.rows:
                lines.append("| " + " | ".join(_cell_text(c) for c in row) + " |")
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


class MdRenderer:
    format = FORMAT_MD
    mime_type = MIME_TYPES[FORMAT_MD]

    def render_spec(self, spec: "ArtifactSpec") -> bytes:
        return build_document_markdown(spec).encode("utf-8")


# --------------------------------------------------------------- spreadsheet: XLSX


def _safe_sheet_name(name: str) -> str:
    """Excel sheet names: <=31 chars, no ``[]:*?/\\``."""
    cleaned = re.sub(r"[\[\]:*?/\\]", "_", name).strip() or "Sheet1"
    return cleaned[:31]


class XlsxRenderer:
    format = FORMAT_XLSX
    mime_type = MIME_TYPES[FORMAT_XLSX]

    def render_spec(self, spec: "ArtifactSpec") -> bytes:
        assert spec.sheets is not None
        workbook = Workbook()
        workbook.remove(workbook.active)
        for sheet_spec in spec.sheets:
            worksheet = workbook.create_sheet(title=_safe_sheet_name(sheet_spec.name))
            for c, col in enumerate(sheet_spec.columns, start=1):
                worksheet.cell(row=1, column=c, value=col)
            for r, row in enumerate(sheet_spec.rows, start=2):
                for c, value in enumerate(row, start=1):
                    worksheet.cell(row=r, column=c, value=value)
            totals_row = _totals_row(sheet_spec)
            if totals_row is not None:
                next_row = 2 + len(sheet_spec.rows)
                for c, value in enumerate(totals_row, start=1):
                    if value != "":
                        worksheet.cell(row=next_row, column=c, value=value)
            if sheet_spec.formulas:
                for cell_ref, raw in sheet_spec.formulas.items():
                    worksheet[cell_ref] = raw
        workbook.properties.creator = "Personal Agent OS"
        workbook.properties.title = spec.title
        workbook.properties.created = _FIXED_DT.replace(tzinfo=None)
        workbook.properties.modified = _FIXED_DT.replace(tzinfo=None)
        buf = io.BytesIO()
        workbook.save(buf)
        return _normalize_ooxml_zip(buf.getvalue())


# --------------------------------------------------------- spreadsheet/dataset: CSV


class CsvRenderer:
    format = FORMAT_CSV
    mime_type = MIME_TYPES[FORMAT_CSV]

    def render_spec(self, spec: "ArtifactSpec") -> bytes:
        buf = io.StringIO()
        writer = csv_module.writer(buf, lineterminator="\n")
        if spec.kind == "spreadsheet":
            assert spec.sheets is not None
            sheet = spec.sheets[0]
            writer.writerow(sheet.columns)
            for row in sheet.rows:
                writer.writerow(row)
            totals_row = _totals_row(sheet)
            if totals_row is not None:
                writer.writerow(totals_row)
        else:
            assert spec.columns is not None and spec.rows is not None
            writer.writerow(spec.columns)
            for row in spec.rows:
                writer.writerow(row)
        return buf.getvalue().encode("utf-8")


# ------------------------------------------------------------- presentation: PPTX


class PptxRenderer:
    format = FORMAT_PPTX
    mime_type = MIME_TYPES[FORMAT_PPTX]

    def render_spec(self, spec: "ArtifactSpec") -> bytes:
        assert spec.slides is not None
        presentation = Presentation()
        layout = presentation.slide_layouts[1]  # "Title and Content"
        for slide_spec in spec.slides:
            slide = presentation.slides.add_slide(layout)
            slide.shapes.title.text = slide_spec.title
            body = slide.placeholders[1]
            text_frame = body.text_frame
            text_frame.clear()
            for i, bullet in enumerate(slide_spec.bullets):
                if i == 0:
                    text_frame.text = bullet
                else:
                    text_frame.add_paragraph().text = bullet
            if slide_spec.notes:
                slide.notes_slide.notes_text_frame.text = slide_spec.notes
        presentation.core_properties.title = spec.title
        presentation.core_properties.author = "Personal Agent OS"
        presentation.core_properties.created = _FIXED_DT.replace(tzinfo=None)
        presentation.core_properties.modified = _FIXED_DT.replace(tzinfo=None)
        buf = io.BytesIO()
        presentation.save(buf)
        return _normalize_ooxml_zip(buf.getvalue())


# -------------------------------------------------------------------- dataset: JSON


class JsonRenderer:
    format = FORMAT_JSON
    mime_type = MIME_TYPES[FORMAT_JSON]

    def render_spec(self, spec: "ArtifactSpec") -> bytes:
        if spec.kind == "dataset":
            payload: dict[str, object] = {"columns": spec.columns, "rows": spec.rows}
        else:  # pragma: no cover - dataset is the only kind wired to "json" today
            payload = spec.model_dump(mode="json", exclude_none=True)
        text = json_module.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        return (text + "\n").encode("utf-8")


_FACTORY_RENDERERS: dict[str, FactoryRenderer] = {
    FORMAT_MD: MdRenderer(),
    FORMAT_XLSX: XlsxRenderer(),
    FORMAT_CSV: CsvRenderer(),
    FORMAT_PPTX: PptxRenderer(),
    FORMAT_JSON: JsonRenderer(),
}

#: The four legacy Markdown-based formats a document/page spec routes through the
#: OLD render() free function (spec.title + build_document_markdown(spec)).
_MARKDOWN_ROUTED_FORMATS = (FORMAT_PDF, FORMAT_DOCX, FORMAT_HTML, FORMAT_TXT)


def render_factory(fmt: str, spec: "ArtifactSpec") -> RenderResult:
    """Render one (spec, format) pair — the Artifact Factory's own entry point.

    Refuses a format the spec's own kind cannot produce (spec §1's table,
    ``ArtifactSpec.formats()``) rather than silently rendering something the owner
    never asked for.
    """
    if fmt not in spec.formats():
        raise ValueError(f"format {fmt!r} is not valid for kind {spec.kind!r}")
    if fmt in _MARKDOWN_ROUTED_FORMATS:
        markdown_text = build_document_markdown(spec)
        return render(fmt, title=spec.title, canonical_markdown=markdown_text)
    renderer = _FACTORY_RENDERERS.get(fmt)
    if renderer is None:  # pragma: no cover - defensive; formats() already narrowed this
        raise ValueError(f"unsupported factory format: {fmt!r}")
    data = renderer.render_spec(spec)
    return RenderResult(
        format=fmt,
        data=data,
        mime_type=renderer.mime_type,
        content_hash=content_hash(data),
    )
