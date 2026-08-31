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
"""

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import markdown as markdown_lib
from docx import Document
from fpdf import FPDF
from fpdf.enums import XPos, YPos

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

MIME_TYPES = {
    FORMAT_PDF: "application/pdf",
    FORMAT_DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FORMAT_HTML: "text/html; charset=utf-8",
    FORMAT_TXT: "text/plain; charset=utf-8",
}

EXTENSIONS = {
    FORMAT_PDF: "pdf",
    FORMAT_DOCX: "docx",
    FORMAT_HTML: "html",
    FORMAT_TXT: "txt",
}


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
    kind: str  # "h1" | "h2" | "h3" | "li" | "p" | "blank"
    text: str


def _parse_blocks(markdown_text: str) -> list[_Block]:
    """Minimal, deterministic block parser sufficient for our canonical bodies."""
    blocks: list[_Block] = []
    for raw in markdown_text.splitlines():
        line = raw.rstrip()
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
        body = markdown_lib.markdown(
            canonical_markdown, extensions=["extra", "sane_lists"]
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
            else:
                pdf.set_font(_FONT_FAMILY, "", 11)
                line(6, text)

        out = pdf.output()
        return bytes(out)


# ----------------------------------------------------------------------- DOCX


def _normalize_docx_zip(data: bytes) -> bytes:
    """Re-emit an OOXML package with fixed member dates and order.

    python-docx writes zip members with the current wall-clock time; normalizing
    the archive makes the render byte-stable given stable part contents.
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
                dst.writestr(info, src.read(name))
        return out_buf.getvalue()
    finally:
        src.close()


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
