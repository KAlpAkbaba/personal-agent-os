"""Packaging, and the two things it must never quietly do.

**It must not sign.** An MSIX built here carries no signature, and that is a decision, not
a gap: signing needs a certificate, and the owner's real signing identity is theirs. A
package that claimed to be signed - or that reached for a certificate to become so - would
be this system taking an authority it was not given.

**It must not mutate its input.** A packager that wrote its manifest into the build output
would make the second run of a build produce something different from the first, and the
difference would be invisible.

The real `makeappx` run is proven by `scripts/tests/native-windows-lab.py`; what runs here
is everything that does not need a 60 MB publish.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from defusedxml import ElementTree as ET

from app.nativefactory.artifacts import read_artifact
from app.nativefactory.packaging import (
    UNSIGNED_PUBLISHER,
    PackagingError,
    appx_manifest_text,
    make_msix,
    make_portable_zip,
    write_appx_manifest,
)
from app.nativefactory.signing import TEST_SIGNING_SUBJECT
from app.nativefactory.spec import parse_spec

SPEC = parse_spec(
    {
        "name": "Notlarim",
        "title": "Notlarım",
        "template": "notes-desktop",
        "targets": ["windows_msix"],
        "version": "1.2.3",
    }
)


def _publish(tmp_path: Path) -> Path:
    """A publish folder's shape, without a 60-second compile."""
    out = tmp_path / "publish"
    out.mkdir()
    (out / "notlarim.exe").write_bytes(b"MZ" + b"\x00" * 128)
    (out / "notlarim.dll").write_bytes(b"MZ" + b"\x00" * 64)
    (out / "runtimeconfig.json").write_text("{}", encoding="utf-8")
    return out


# ------------------------------------------------------------------------- manifest


def test_the_manifest_declares_what_the_application_actually_is(tmp_path: Path) -> None:
    """`makeappx` refused the first manifest with error 80080204 because a WPF app is a
    full-trust desktop program and the manifest did not say so. Declaring the capability an
    application really uses is the manifest's whole job."""
    manifest = write_appx_manifest(SPEC, tmp_path)
    root = ET.fromstring(manifest.read_text(encoding="utf-8"))
    text = manifest.read_text(encoding="utf-8")
    assert 'Name="runFullTrust"' in text
    assert root.tag.endswith("Package")


def test_the_version_in_the_manifest_is_the_four_part_form_windows_wants(tmp_path: Path) -> None:
    text = write_appx_manifest(SPEC, tmp_path).read_text(encoding="utf-8")
    assert 'Version="1.2.3.0"' in text


def test_the_display_name_goes_through_xml_escaping_not_string_formatting(tmp_path: Path) -> None:
    """`AppxManifest.xml` decides what the package CLAIMS to be, so it is the last place a
    display name should be spliced in raw. The spec refuses markup in a name, and this is
    the second wall."""
    spec = parse_spec({**SPEC.model_dump(mode="json"), "title": "Notlar & Fikirler"})
    text = write_appx_manifest(spec, tmp_path).read_text(encoding="utf-8")
    assert "Notlar &amp; Fikirler" in text
    assert "Notlar & Fikirler" not in text
    # ...and it still parses, which is the point of escaping rather than stripping.
    ET.fromstring(text)


def test_an_unsigned_package_does_not_claim_a_publisher_who_could_be_held_to_it(
    tmp_path: Path,
) -> None:
    text = write_appx_manifest(SPEC, tmp_path).read_text(encoding="utf-8")
    assert UNSIGNED_PUBLISHER in text
    assert "imzasız" in text  # said in the owner's language, in the package itself


def test_a_signed_manifest_names_exactly_the_device_s_signing_subject_as_publisher() -> None:
    """B33 req 473: the MSIX signer refuses a package whose Publisher is not its
    certificate's subject, byte for byte - so the attribute is parsed, not searched for."""
    signed = ET.fromstring(appx_manifest_text(SPEC, signed=True))
    unsigned = ET.fromstring(appx_manifest_text(SPEC))
    ns = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
    assert signed.find("m:Identity", ns).get("Publisher") == TEST_SIGNING_SUBJECT
    assert unsigned.find("m:Identity", ns).get("Publisher") == UNSIGNED_PUBLISHER
    display = signed.find("m:Properties/m:PublisherDisplayName", ns).text
    assert display == "PagentOS (kendinden imzalı)"
    # Everything else about the package is the same claim.
    assert signed.find("m:Identity", ns).get("Name") == unsigned.find("m:Identity", ns).get("Name")


# -------------------------------------------------------------------------- portable


def test_the_portable_zip_carries_the_publish_folder_and_nothing_else(tmp_path: Path) -> None:
    publish = _publish(tmp_path)
    result = make_portable_zip(publish, tmp_path / "out" / "notlarim.zip")
    assert not result.signed
    with zipfile.ZipFile(result.path) as bundle:
        assert set(bundle.namelist()) == {
            "notlarim.exe",
            "notlarim.dll",
            "runtimeconfig.json",
        }


@pytest.mark.parametrize("where", ["inside", "nested", "itself"])
def test_a_zip_inside_the_folder_it_packs_is_refused_before_it_is_written(
    tmp_path: Path, where: str
) -> None:
    """2026-09-17, a Linux CI runner: the package was written into the folder it zipped,
    was listed, and was copied into itself while it grew - 13.7 GB until the disk was full.
    Refused, and nothing is created."""
    publish = _publish(tmp_path)
    out = {
        "inside": publish / "notlarim.zip",
        "nested": publish / "sub" / "notlarim.zip",
        "itself": publish,
    }[where]
    before = sorted(p.name for p in publish.rglob("*"))
    with pytest.raises(PackagingError, match="inside the folder it packs"):
        make_portable_zip(publish, out)
    assert sorted(p.name for p in publish.rglob("*")) == before


def test_packaging_a_directory_that_is_not_there_is_an_error_not_an_empty_package(
    tmp_path: Path,
) -> None:
    with pytest.raises(PackagingError):
        make_portable_zip(tmp_path / "nothing", tmp_path / "out.zip")


# ------------------------------------------------------------------------------ MSIX


def test_msix_refuses_when_the_executable_is_not_in_the_publish_output(tmp_path: Path) -> None:
    """A package with no application in it is not a package, and `makeappx` would happily
    make one."""
    empty = tmp_path / "publish"
    empty.mkdir()
    (empty / "readme.txt").write_text("nothing to package", encoding="utf-8")
    with pytest.raises(PackagingError) as caught:
        make_msix(SPEC, empty, tmp_path / "x.msix", makeappx="makeappx.exe")
    assert "nothing to package" in str(caught.value)


def test_packaging_does_not_write_into_the_build_output(tmp_path: Path) -> None:
    """The manifest and the placeholder assets go to a staging copy, so a second run of the
    same build produces the same thing as the first."""
    publish = _publish(tmp_path)
    before = {p.name for p in publish.iterdir()}
    with pytest.raises(PackagingError):
        # `makeappx.exe` is not on this path; the point is what happened to `publish`.
        make_msix(SPEC, publish, tmp_path / "out" / "x.msix", makeappx="does-not-exist.exe")
    assert {p.name for p in publish.iterdir()} == before
    assert not (publish / "AppxManifest.xml").exists()
    assert not (publish / "Assets").exists()


def test_a_package_this_module_made_is_still_opened_by_the_independent_reader(
    tmp_path: Path,
) -> None:
    """A packager that validated its own output would be checking its own arithmetic, so
    the reader used here is the same one used on an artefact from anywhere else."""
    path = tmp_path / "hand-made.msix"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("AppxManifest.xml", write_appx_manifest(SPEC, tmp_path).read_text("utf-8"))
        bundle.writestr("notlarim.exe", b"MZ")
    facts = read_artifact(path)
    assert facts.identity == "PagentOS.notlarim"
    assert facts.version == "1.2.3.0"
    assert facts.display_name == "Notlarım"
