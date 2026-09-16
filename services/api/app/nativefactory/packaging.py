"""Packaging: a portable zip, and an MSIX made by Windows' own tool
(docs/M28_NATIVE_APP_FACTORY_SPEC.md §4, ADR-0095 decision 4).

Two targets, one rule: the package is produced by the platform's real tool and then opened
by :mod:`app.nativefactory.artifacts`, which did not make it. A packager that validated its
own output would be checking its own arithmetic.

**This module signs nothing.** The Cloud Core holds no certificate and never will. Since the
owner's decision of 2026-09-16 (B33 req 473, ``app.nativefactory.signing``) the DEVICE signs
an MSIX in its own process with its self-signed identity - never the owner's real signing
identity, which is theirs. What this module contributes is the manifest's ``Publisher``,
which must equal that identity's subject for the device to sign: :func:`appx_manifest_text`
names ``signing.TEST_SIGNING_SUBJECT`` when asked for a signed package and
:data:`UNSIGNED_PUBLISHER` otherwise. The local :func:`make_msix` (the lab path) still
produces an unsigned package, and its receipt says so.

The manifest is written from a validated spec through XML escaping, never by formatting a
string with owner text in it. `AppxManifest.xml` is the file that decides what the package
CLAIMS to be, so it is the last place a display name should be spliced in raw.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.sax.saxutils import escape, quoteattr

from app.nativefactory.signing import TEST_SIGNING_PUBLISHER_DISPLAY, TEST_SIGNING_SUBJECT
from app.nativefactory.spec import NativeAppSpec

#: Windows requires a four-part version and refuses a revision of anything but 0 in the
#: store; `spec.assembly_version` is already `major.minor.patch.0`.
_APPX_TEMPLATE: Final = """<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap rescap">
  <Identity Name={identity} Version={version} Publisher={publisher} ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>{display_name}</DisplayName>
    <PublisherDisplayName>{publisher_display}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0"
                        MaxVersionTested="10.0.22621.0" />
  </Dependencies>
  <Resources>
    <Resource Language="tr-TR" />
  </Resources>
  <Applications>
    <Application Id="App" Executable={executable} EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName={display_attr} Description={display_attr}
                          BackgroundColor="transparent"
                          Square150x150Logo="Assets\\Square150x150Logo.png"
                          Square44x44Logo="Assets\\Square44x44Logo.png" />
    </Application>
  </Applications>
  <Capabilities>
    <!-- A WPF application is a full-trust desktop program, and `makeappx` refuses the
         manifest without this: `Windows.FullTrustApplication` as an EntryPoint REQUIRES
         `runFullTrust` to be declared (error 80080204). Declaring the capability an
         application actually uses is the point of the manifest, so this is the fix rather
         than a different entry point that would misdescribe what the package contains. -->
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""

#: The publisher of an UNSIGNED package. It is deliberately not the owner's name or any
#: real organisation: a package nobody signed should not claim a publisher who might be
#: held to it.
UNSIGNED_PUBLISHER: Final = "CN=PagentOS Unsigned Build"
UNSIGNED_PUBLISHER_DISPLAY: Final = "PagentOS (imzasız)"


class PackagingError(RuntimeError):
    """Packaging could not produce the artefact, with the reason."""


@dataclass(frozen=True, slots=True)
class PackageResult:
    path: Path
    signed: bool
    #: What the owner needs to know about installing it, in their own language.
    note: str


def appx_manifest_text(spec: NativeAppSpec, *, signed: bool = False) -> str:
    """The manifest, escaped by XML's own rules rather than by hoping.

    `quoteattr` and `escape` are the standard library's, and they are used because the
    display name is the one field that carries owner-facing text into a file that decides
    what the package claims to be. B33: the same text is scaffolded onto the device
    (``staging/AppxManifest.xml``) for ``project.package`` to pack; with ``signed`` the
    Publisher is the device's signing subject (req 473), which the signer requires exactly.
    """
    identity = f"PagentOS.{spec.slug.replace('-', '')}"
    return _APPX_TEMPLATE.format(
        identity=quoteattr(identity),
        version=quoteattr(spec.assembly_version),
        publisher=quoteattr(TEST_SIGNING_SUBJECT if signed else UNSIGNED_PUBLISHER),
        display_name=escape(spec.display_title),
        publisher_display=escape(
            TEST_SIGNING_PUBLISHER_DISPLAY if signed else UNSIGNED_PUBLISHER_DISPLAY
        ),
        executable=quoteattr(f"{spec.slug}.exe"),
        display_attr=quoteattr(spec.display_title),
    )


def write_appx_manifest(spec: NativeAppSpec, target_dir: Path) -> Path:
    path = target_dir / "AppxManifest.xml"
    path.write_text(appx_manifest_text(spec), encoding="utf-8")
    return path


def make_portable_zip(publish_dir: Path, out_path: Path) -> PackageResult:
    """The `windows_portable` target: the publish folder, zipped, nothing else.

    Deliberately the standard library rather than a tool: there is no format subtlety to
    get wrong, and a zip this module wrote is still opened by the independent reader
    afterwards like any other artefact.
    """
    if not publish_dir.is_dir():
        raise PackagingError(f"no publish directory at {publish_dir}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(publish_dir.rglob("*")):
            if file.is_file():
                bundle.write(file, file.relative_to(publish_dir).as_posix())
    return PackageResult(
        path=out_path,
        signed=False,
        note="Taşınabilir paket: açıp doğrudan çalıştırabilirsiniz efendim, kurulum gerekmez.",
    )


def make_msix(
    spec: NativeAppSpec,
    publish_dir: Path,
    out_path: Path,
    *,
    makeappx: str,
    timeout_s: int = 600,
) -> PackageResult:
    """The `windows_msix` target, built by Windows' own `makeappx.exe`.

    The publish folder is copied to a staging directory first so the manifest and the
    placeholder assets never land in the build output - a packager that mutated its input
    would make the second run of the same build produce a different thing from the first.
    """
    if not publish_dir.is_dir():
        raise PackagingError(f"no publish directory at {publish_dir}")
    exe = publish_dir / f"{spec.slug}.exe"
    if not exe.exists():
        raise PackagingError(f"{exe.name} is not in the publish output; nothing to package")

    staging = out_path.parent / f".{spec.slug}-msix-staging"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(publish_dir, staging)
    write_appx_manifest(spec, staging)
    _write_placeholder_assets(staging / "Assets")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [makeappx, "pack", "/d", str(staging), "/p", str(out_path), "/o", "/nv"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except OSError as exc:
        # A tool that is not there is a fact about the machine, and it must arrive as this
        # module's own error naming the tool - not as a raw WinError from `subprocess`,
        # which tells the caller nothing about WHAT was missing.
        shutil.rmtree(staging, ignore_errors=True)
        raise PackagingError(f"makeappx could not be run at {makeappx!r}: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if proc.returncode != 0 or not out_path.exists():
        raise PackagingError(
            f"makeappx failed ({proc.returncode}): "
            f"{((proc.stdout or '') + (proc.stderr or '')).strip()[-600:]}"
        )
    return PackageResult(
        path=out_path,
        signed=False,
        note=(
            "MSIX imzasız efendim: bu, Cloud Core'un laboratuvar yolu ve imzalamaz; "
            "imzayı cihaz kendi kendinden imzalı sertifikasıyla atar."
        ),
    )


#: The smallest thing `makeappx` will accept as a logo. A real icon is a design decision
#: and an owner's; a 1x1 transparent PNG is a placeholder that is obviously a placeholder.
_PNG_1X1: Final = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def _write_placeholder_assets(assets: Path) -> None:
    assets.mkdir(parents=True, exist_ok=True)
    for name in ("StoreLogo.png", "Square150x150Logo.png", "Square44x44Logo.png"):
        (assets / name).write_bytes(_PNG_1X1)


__all__ = [
    "UNSIGNED_PUBLISHER",
    "PackageResult",
    "PackagingError",
    "appx_manifest_text",
    "make_msix",
    "make_portable_zip",
    "write_appx_manifest",
]
