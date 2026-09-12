"""B03 req 3/5: what Cloud Core puts in ``file.search``'s ``roots``, held to the contract.

The Cloud Core sent ``roots: ["documents"]`` - and before that, ``["Masaüstü"]``, the owner's
own Turkish word. The device answered ``payload.roots must be absolute paths``. It was right
to: it never agreed to resolve names. The Cloud Core was also right that it cannot name the
owner's Documents folder - only the device can, and the owner may have moved it.

What was missing was a contract. ``packages/protocol/file-search-roots.json`` is it, and this
is the Cloud Core half of the test: every value this service would put on the wire is a shape
that file declares, and the bucket list is READ from the file rather than restated here.

The device half is ``devices/windows-agent/tests/.../FileSearchRootsContractTests.cs``, which
drives the real parser with the same shapes.
"""

from __future__ import annotations

import json

import pytest

from app.documents import service as documents_service
from app.documents.service import canonical_folder

CONTRACT = documents_service._FILE_SEARCH_ROOTS
BUCKETS = tuple(bucket["name"] for bucket in json.loads(CONTRACT.read_text("utf-8"))["buckets"])


def test_the_contract_file_is_where_the_service_looks_for_it() -> None:
    assert CONTRACT.is_file(), CONTRACT
    assert BUCKETS, "the contract declares no buckets"


def test_the_service_reads_its_bucket_names_from_the_contract_and_does_not_restate_them() -> None:
    assert documents_service._KNOWN_FOLDER_ROOTS == frozenset(BUCKETS)
    source = documents_service.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    # The literal list is gone: a second copy is a drift waiting to happen, and this is the
    # drift that actually happened.
    assert '"documents", "desktop", "downloads"' not in text
    assert "file-search-roots.json" in text


@pytest.mark.parametrize("bucket", BUCKETS)
def test_every_bucket_the_contract_declares_is_sent_unchanged(bucket: str) -> None:
    assert canonical_folder(bucket) == bucket
    assert canonical_folder(bucket.upper()) == bucket
    assert canonical_folder(f"  {bucket}  ") == bucket


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Masaüstü", "desktop"),
        ("masaustu", "desktop"),
        ("MASAÜSTÜ", "desktop"),
        ("Belgelerim", "documents"),
        ("belgelerimde", "documents"),
        ("indirilenler", "downloads"),
        # The Turkish dotted capital. Python's default casing turns "İ" into "i" plus a
        # COMBINING DOT ABOVE, which matched no alias key: a folder the owner typed the
        # ordinary Turkish way was refused as unknown. Found by this test on 2026-09-12.
        ("İndirilenler", "downloads"),
        ("İNDİRİLENLER", "downloads"),
        # ...and the dotless I, which a Turkish keyboard produces for the same word.
        ("Indirilenler", "downloads"),
        ("ındirilenler", "downloads"),
    ],
)
def test_a_turkish_alias_becomes_a_name_the_device_knows(spoken: str, expected: str) -> None:
    """The owner's word never reaches the device. This is the exact value that was on the
    wire when production refused every folder search."""
    assert canonical_folder(spoken) == expected


def test_every_alias_resolves_to_a_bucket_the_contract_declares() -> None:
    """An alias table can only point at names the other half knows."""
    for alias, bucket in documents_service._KNOWN_FOLDER_ALIASES.items():
        assert bucket in BUCKETS, f"alias {alias!r} points at {bucket!r}, which is not a bucket"


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("documents/Faturalar", "documents/Faturalar"),
        (r"Belgelerim\Faturalar\2026", "documents/Faturalar/2026"),
        ("desktop/is", "desktop/is"),
    ],
)
def test_a_bucket_with_subfolders_keeps_the_bucket_first(folder: str, expected: str) -> None:
    assert canonical_folder(folder) == expected


@pytest.mark.parametrize(
    "folder",
    [
        "sozlesmeler/2026",
        "Faturalar",
        "C:\\Windows\\System32",
        "..",
        "documents/../../Windows",
        "\\\\server\\share",
        "/etc/passwd",
        "",
        "   ",
        "documents\x00evil",
    ],
)
def test_anything_the_contract_does_not_admit_never_becomes_a_wire_value(folder: str) -> None:
    assert canonical_folder(folder) is None


def test_the_contract_says_what_an_absent_roots_field_means() -> None:
    """The field is optional, and its absence is the common case - "find my invoices" with no
    folder at all. A contract that only described the present case would leave the busiest
    path undocumented."""
    document = json.loads(CONTRACT.read_text("utf-8"))
    assert "absent_or_empty" in document["roots_field"]
    assert "every authorised root" in document["roots_field"]["absent_or_empty"]


def test_the_contract_records_why_it_exists() -> None:
    """A contract file with no history is one somebody deletes as redundant."""
    document = json.loads(CONTRACT.read_text("utf-8"))
    why = " ".join(document["why"])
    assert "absolute paths" in why
    assert "ADR-0102" in why
