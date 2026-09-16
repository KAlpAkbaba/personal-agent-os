"""B52 req 143-146: EPUB, RTF, ODT and the 97-2003 Office formats - the Cloud Core's half.

The device reads them (``ExtractionOracleTests`` holds every fixture to its expected extract;
``LegacyFormatBoundsTests`` holds the refusals). Here: the two halves read each other, the
Cloud Core speaks the new kinds, and - the guard that matters most - every expected extract
is the SOURCE fixture's oracle with only the named per-format differences, so an expected file
can never have been written from the extractor it judges.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.documents.answers import DocRef
from app.documents.service import _preview_facts

REPO = Path(__file__).resolve().parents[4]
FIX = REPO / "services/api/tests/fixtures/documents"
DEVICE_DOCS = REPO / "devices/windows-agent/src/PagentOS.SessionCompanion/Documents"
LEGACY = [
    "legacy/sozlesme.odt",
    "legacy/sozlesme.rtf",
    "legacy/sozlesme.epub",
    "legacy/sozlesme.doc",
    "legacy/butce-2026.xls",
    "legacy/sunum-q3.ppt",
]


def _expected(relative: str) -> dict:
    return json.loads(
        (FIX / "expected" / (relative.replace("/", "__") + ".extract.json")).read_text("utf-8")
    )


def _truth() -> dict:
    return json.loads((FIX / "truth.json").read_text("utf-8"))


# ------------------------------------------------------------------- the two halves


def test_the_device_names_the_six_kinds_and_registers_their_readers() -> None:
    kinds = (DEVICE_DOCS / "FileKinds.cs").read_text("utf-8")
    for name, value in (
        ("Epub", "epub"),
        ("Odt", "odt"),
        ("Rtf", "rtf"),
        ("Doc", "doc"),
        ("Xls", "xls"),
        ("Ppt", "ppt"),
    ):
        assert f'public const string {name} = "{value}"' in kinds
        assert f'".{value}" => {name},' in kinds
    assert "IsPackage(string kind) => kind is Docx or Xlsx or Pptx or Epub or Odt;" in kinds
    capabilities = (DEVICE_DOCS / "DocumentCapabilities.cs").read_text("utf-8")
    for reader in ("OpenPackageExtractor", "RtfExtractor", "LegacyOfficeExtractor"):
        assert f"new {reader}()" in capabilities
    errors = (DEVICE_DOCS / "DocumentExtractor.cs").read_text("utf-8")
    assert 'public const string Encrypted = "encrypted";' in errors


def test_the_protocol_names_the_kinds_the_schemes_and_the_refusal() -> None:
    protocol = (REPO / "packages/protocol/DEVICE_PROTOCOL.md").read_text("utf-8")
    assert "image | archive | epub | odt | rtf | doc | xls | ppt | unknown" in protocol
    assert "page_bound | encrypted" in protocol
    assert "B52: ODT and RTF use the DOCX scheme" in protocol
    assert "16 MiB per part" in protocol


# ------------------------------------------------------------ the Cloud Core speaks them


@pytest.mark.parametrize(
    ("kind", "structure", "words"),
    [
        ("odt", {"paragraphs": 12}, "paragraflık belge"),
        ("rtf", {}, "paragraflık belge"),
        ("doc", {"paragraphs": 13}, "13 paragraflık belge"),
        ("epub", {"chapters": 3}, "3 bölümlük e-kitap"),
        ("xls", {"sheets": [{"name": "Ozet"}, {"name": "Detay"}]}, "2 sayfalı Excel tablosu"),
        ("ppt", {"slide_count": 7}, "7 slaytlık sunum"),
    ],
)
def test_a_preview_names_each_new_kind_in_its_own_units(
    kind: str, structure: dict, words: str
) -> None:
    doc = DocRef(
        file_id="file:x",
        doc_id="doc:x",
        path="C:/x",
        name="x",
        kind=kind,
        structure=structure,
        blocks=[{"ref": "p1"}],
    )
    assert words in _preview_facts(doc)


# ------------------------------------------------------------ the fixtures and their oracle


def test_every_legacy_fixture_is_on_the_record_with_its_real_bytes() -> None:
    entries = {f["path"]: f for f in _truth()["files"]}
    for relative in LEGACY:
        entry = entries[relative]
        data = (FIX / relative).read_bytes()
        assert entry["size"] == len(data) and entry["sha256"] == hashlib.sha256(data).hexdigest(), (
            relative
        )
        assert "LibreOffice" in entry["producer"], relative
        assert entry["blocks"] == len(_expected(relative)["blocks"])


def test_every_legacy_expected_extract_is_the_source_oracle_with_only_named_differences() -> None:
    """Expected and actual from one source cannot fail: the expected extracts must follow from
    the committed DOCX/XLSX/PPTX oracle, not from the readers being judged."""
    docx = _expected("sozlesmeler/2025/sozlesme.docx")
    xlsx = _expected("butce-2026.xlsx")
    pptx = _expected("sunum-q3.pptx")
    norm = lambda t: " ".join(t.split())  # noqa: E731

    def without(body: dict, *keys: str) -> dict:
        body = copy.deepcopy(body)
        for key in keys:
            body.pop(key, None)
        return body

    for relative in ("legacy/sozlesme.odt", "legacy/sozlesme.rtf"):
        assert without(_expected(relative), "path", "kind") == without(docx, "path", "kind"), (
            relative
        )

    epub = _expected("legacy/sozlesme.epub")
    assert [norm(b["text"]) for b in epub["blocks"]] == [norm(b["text"]) for b in docx["blocks"]]
    assert all(b["kind"] != "heading" for b in epub["blocks"])

    doc = _expected("legacy/sozlesme.doc")
    assert [b["text"] for b in doc["blocks"]] == [norm(b["text"]) for b in docx["blocks"]]

    xls = _expected("legacy/butce-2026.xls")
    assert [(b["ref"], b["text"]) for b in xls["blocks"]] == [
        (b["ref"], b["text"]) for b in xlsx["blocks"]
    ]
    assert xls["structure"]["sheets"] == xlsx["structure"]["sheets"]

    ppt = _expected("legacy/sunum-q3.ppt")
    assert without(ppt, "path", "kind") == without(pptx, "path", "kind")
