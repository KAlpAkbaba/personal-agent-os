"""Unit tests: renderers produce valid, non-empty, stable-hash artifacts."""

import io
import zipfile

import pytest

from app.artifacts import renderers as R
from app.research.compose import (
    compose_canonical_markdown,
    compose_executive_summary,
    score_and_dedup,
)
from app.research.provider import DeterministicResearchProvider

TOPIC = "yapay zekâ ajanlarındaki gelişmeler ve şeffaflık"


def _canonical() -> str:
    scored = score_and_dedup(DeterministicResearchProvider().gather(TOPIC, limit=6))
    summary = compose_executive_summary(TOPIC, scored)
    return compose_canonical_markdown(TOPIC, scored, summary)


def test_all_formats_supported() -> None:
    assert set(R.SUPPORTED_FORMATS) == {"pdf", "docx", "html", "txt"}
    assert R.DEFAULT_RENDER_FORMATS == ("pdf", "docx")


def test_pdf_magic_bytes_and_nonempty() -> None:
    result = R.render("pdf", title="Test", canonical_markdown=_canonical())
    assert result.mime_type == "application/pdf"
    assert result.data[:4] == b"%PDF"
    assert len(result.data) > 200


def test_docx_is_a_zip_with_ooxml_marker() -> None:
    result = R.render("docx", title="Test", canonical_markdown=_canonical())
    assert result.data[:2] == b"PK"  # zip local file header
    with zipfile.ZipFile(io.BytesIO(result.data)) as zf:
        names = zf.namelist()
    assert "[Content_Types].xml" in names
    assert "word/document.xml" in names


def test_html_is_valid_document() -> None:
    result = R.render("html", title="Başlık", canonical_markdown=_canonical())
    text = result.data.decode("utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert "<title>Başlık</title>" in text
    assert "</html>" in text


def test_txt_preserves_turkish_utf8() -> None:
    result = R.render("txt", title="Test", canonical_markdown=_canonical())
    text = result.data.decode("utf-8")
    assert "Yönetici Özeti" in text  # full Turkish preserved in TXT


@pytest.mark.parametrize("fmt", ["pdf", "docx", "html", "txt"])
def test_render_is_deterministic_stable_hash(fmt: str) -> None:
    md = _canonical()
    a = R.render(fmt, title="Aynı Başlık", canonical_markdown=md)
    b = R.render(fmt, title="Aynı Başlık", canonical_markdown=md)
    assert a.content_hash == b.content_hash
    assert a.data == b.data
    assert a.content_hash == R.content_hash(a.data)


def test_different_content_different_hash() -> None:
    a = R.render("txt", title="t", canonical_markdown="# A\n")
    b = R.render("txt", title="t", canonical_markdown="# B\n")
    assert a.content_hash != b.content_hash


def test_unsupported_format_raises() -> None:
    with pytest.raises(ValueError):
        R.render("rtf", title="t", canonical_markdown="# x")


def test_pdf_preserves_turkish_glyphs_with_embedded_font() -> None:
    # Turkish is first-class: the bundled Unicode font must render ş/ğ/ı/İ,
    # not fold them. The glyphs are embedded as a subset, so we assert the
    # PDF is well-formed, non-trivial, and declares the embedded font.
    md = "# Işık ğ ş İ çöğüş\n\n- Iğdır şehri\n"
    result = R.render("pdf", title="Işık", canonical_markdown=md)
    assert result.data[:4] == b"%PDF"
    # An embedded TrueType subset (/FontFile2) of DejaVu proves the glyphs are
    # really in the file — a folded latin-1 core-font PDF would have neither.
    assert b"/FontFile2" in result.data
    assert b"DejaVu" in result.data


def test_pdf_render_is_deterministic_with_turkish() -> None:
    md = _canonical()
    a = R.render("pdf", title="Başlık", canonical_markdown=md)
    b = R.render("pdf", title="Başlık", canonical_markdown=md)
    assert a.content_hash == b.content_hash


def test_html_render_neutralizes_injected_markup() -> None:
    # M3 security review #1: raw HTML in the (future untrusted) body must not
    # survive into the HTML render as live markup.
    md = "# Baslik\n\n<script>alert('xss')</script>\n\n- <img src=x onerror=alert(1)>\n"
    result = R.render("html", title="<script>t</script>", canonical_markdown=md)
    text = result.data.decode("utf-8")
    assert "<script>" not in text
    assert "onerror=" not in text or "&lt;img" in text
    # the escaped form is present instead
    assert "&lt;script&gt;" in text


def test_html_render_preserves_turkish_and_ampersand() -> None:
    md = "# Yapay Zeka & Ajanlar\n\n## Yonetici Ozeti\n\nseffaflik ve guven.\n"
    result = R.render("html", title="AI & ML", canonical_markdown=md)
    text = result.data.decode("utf-8")
    assert "Yapay Zeka &amp; Ajanlar" in text
    assert "Yonetici Ozeti" in text
