"""The independent reader, held to real files.

An artefact reader that has only ever seen artefacts it built is not independent. So these
tests point it at binaries this machine already has — Windows' own `mspaint.exe`, the .NET
host, this repository's installed agent — and at deliberately malformed input, because what
the reader does with a file it cannot understand matters as much as what it does with one it
can.

The real generated EXE is proven by `scripts/tests/native-windows-lab.py`, which builds one;
this suite is what runs in CI, where there is no publish to read.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from app.nativefactory.artifacts import (
    ArtifactFacts,
    ArtifactUnreadable,
    read_apk,
    read_artifact,
    read_msix,
    read_pe,
    sha256_of,
    validate_against_spec,
)
from app.nativefactory.spec import parse_spec

#: Binaries every Windows machine has, with the shapes this reader must tell apart: a GUI
#: application and a console one. Skipped rather than failed off Windows, because their
#: absence says nothing about the reader.
MSPAINT = Path(r"C:\Windows\System32\mspaint.exe")
DOTNET = Path(r"C:\Program Files\dotnet\dotnet.exe")

pytestmark = pytest.mark.skipif(
    not MSPAINT.exists(), reason="Windows system binaries are the oracle for the PE reader"
)


def test_it_reads_a_real_gui_application() -> None:
    facts = read_pe(MSPAINT)
    assert facts.kind == "pe"
    assert facts.architecture in ("x64", "x86", "arm64")
    assert facts.subsystem == "windows_gui"
    assert facts.version and facts.version[0].isdigit()
    assert facts.size_bytes == MSPAINT.stat().st_size
    assert len(facts.sha256) == 64


@pytest.mark.skipif(not DOTNET.exists(), reason="no dotnet host to read")
def test_it_tells_a_console_image_from_a_gui_one() -> None:
    """The distinction the spec's own validation depends on: a desktop application that
    published as a console image is a real defect, not a cosmetic one."""
    assert read_pe(DOTNET).subsystem == "windows_console"
    assert read_pe(MSPAINT).subsystem == "windows_gui"


def test_the_version_it_reads_is_the_one_windows_reports() -> None:
    """Not a self-consistency check: the expected value comes from Windows' own version
    API, so a reader that agreed only with itself would fail here."""
    win32api = pytest.importorskip("win32api", reason="pywin32 not installed")
    info = win32api.GetFileVersionInfo(str(MSPAINT), "\\")
    expected = "{}.{}.{}.{}".format(
        info["FileVersionMS"] >> 16,
        info["FileVersionMS"] & 0xFFFF,
        info["FileVersionLS"] >> 16,
        info["FileVersionLS"] & 0xFFFF,
    )
    assert read_pe(MSPAINT).version == expected


def test_a_file_that_is_not_a_pe_is_unreadable_not_a_default(tmp_path: Path) -> None:
    plain = tmp_path / "notes.txt"
    plain.write_text("this is not a binary", encoding="utf-8")
    with pytest.raises(ArtifactUnreadable):
        read_pe(plain)


def test_a_truncated_pe_is_refused_rather_than_read_past_the_end(tmp_path: Path) -> None:
    """A hostile or half-written file must not walk the reader off the end of the buffer."""
    stub = tmp_path / "half.exe"
    stub.write_bytes(b"MZ" + b"\x00" * 0x3C + struct.pack("<I", 0x4000) + b"\x00" * 16)
    with pytest.raises(ArtifactUnreadable):
        read_pe(stub)


def test_an_unknown_extension_has_no_reader_and_says_so(tmp_path: Path) -> None:
    mystery = tmp_path / "thing.bin"
    mystery.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ArtifactUnreadable) as caught:
        read_artifact(mystery)
    assert "no independent reader" in str(caught.value)


def test_the_hash_is_of_the_whole_file(tmp_path: Path) -> None:
    import hashlib

    blob = tmp_path / "payload.bin"
    data = b"pagentos" * 4096
    blob.write_bytes(data)
    digest, size = sha256_of(blob)
    assert digest == hashlib.sha256(data).hexdigest()
    assert size == len(data)


# ------------------------------------------------------------------------------ MSIX


def _msix(path: Path, *, version: str = "0.1.0.0", identity: str = "PagentOS.Notlarim") -> Path:
    manifest = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">'
        f'<Identity Name="{identity}" Version="{version}" ProcessorArchitecture="x64" '
        'Publisher="CN=PagentOS Test" />'
        "<Properties><DisplayName>Notlarım</DisplayName></Properties>"
        "</Package>"
    )
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("AppxManifest.xml", manifest)
        bundle.writestr("notlarim.exe", b"MZ not a real image")
    return path


def test_it_opens_an_msix_and_reads_its_identity(tmp_path: Path) -> None:
    facts = read_msix(_msix(tmp_path / "notlarim.msix"))
    assert facts.kind == "msix"
    assert facts.identity == "PagentOS.Notlarim"
    assert facts.version == "0.1.0.0"
    assert facts.architecture == "x64"
    assert facts.display_name == "Notlarım"


def test_a_package_without_a_manifest_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "empty.msix"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("readme.txt", "nothing here")
    with pytest.raises(ArtifactUnreadable):
        read_msix(path)


def test_a_file_that_is_not_a_zip_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "broken.msix"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(ArtifactUnreadable):
        read_msix(path)


# ------------------------------------------------------------------------------- APK


def test_an_apk_container_is_validated_even_with_no_aapt2(tmp_path: Path) -> None:
    """Android cannot be BUILT here, but a package produced elsewhere must still be
    readable as far as the container allows - and what could not be decoded is reported as
    not decoded, never guessed."""
    path = tmp_path / "sayac.apk"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00binary xml")
        bundle.writestr("classes.dex", b"dex\n035\x00")
    facts = read_apk(path, aapt2=None)
    assert facts.kind == "apk"
    assert facts.detail["aapt2"] == "absent"
    assert facts.detail["has_dex"] == "true"
    assert facts.version is None  # not guessed


# -------------------------------------------------------------------------- verdicts


def _facts(**overrides) -> ArtifactFacts:
    base = {
        "path": "notlarim.exe",
        "kind": "pe",
        "size_bytes": 162304,
        "sha256": "0" * 64,
        "version": "0.1.0",
        "architecture": "x64",
        "subsystem": "windows_gui",
    }
    return ArtifactFacts(**{**base, **overrides})


SPEC = parse_spec(
    {
        "name": "Notlarim",
        "template": "notes-desktop",
        "targets": ["windows_exe"],
        "version": "0.1.0",
    }
)


def test_the_version_read_out_of_the_file_must_be_the_one_the_spec_asked_for() -> None:
    """The point of the whole module: a factory that hands back yesterday's EXE while
    reporting success is the failure this milestone exists to make impossible."""
    assert validate_against_spec(_facts(version="0.1.0"), SPEC).ok
    assert validate_against_spec(_facts(version="0.1.0.0"), SPEC).ok  # assembly form

    stale = validate_against_spec(_facts(version="0.0.9"), SPEC)
    assert not stale.ok
    assert "0.0.9" in stale.mismatches[0]


def test_an_artefact_with_no_version_is_a_mismatch_not_a_pass() -> None:
    verdict = validate_against_spec(_facts(version=None), SPEC)
    assert not verdict.ok
    assert "no version" in verdict.mismatches[0]


def test_a_console_image_fails_a_desktop_application() -> None:
    verdict = validate_against_spec(_facts(subsystem="windows_console"), SPEC)
    assert not verdict.ok


def test_an_empty_artefact_never_passes() -> None:
    assert not validate_against_spec(_facts(size_bytes=0), SPEC).ok
