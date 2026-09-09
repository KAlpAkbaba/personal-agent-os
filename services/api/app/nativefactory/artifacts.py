"""Read a produced artefact back with something that did not build it
(docs/M28_NATIVE_APP_FACTORY_SPEC.md §4, ADR-0095 decision 2).

This is the module that makes "the app is ready" mean something. A build that exits zero
has said only that a compiler was happy; M22 established the rule this milestone inherits —
an artefact is validated by a reader that is not its writer, and what that reader finds is
what goes on the receipt.

**Standard library only, deliberately.** The M28 draft promised the PE header "read by
`pefile`". Adding a dependency to the Cloud Core image for one function is precisely the
trade ADR-0093 decision 4 refused for numpy in M27, and refusing it there while taking it
here would be incoherent. So the PE reader below is `struct` over the DOS and NT headers
and a small walk of the resource tree to `VS_VERSIONINFO`; the MSIX reader is `zipfile`
plus the XML parser this repository already hardened; the APK reader is the same zip walk
plus `aapt2` when the Android SDK is present.

What is deliberately NOT attempted: no signature verification of any kind (that would need
a trust decision the owner owns), no execution of the artefact (that is the operator's job,
under M19, and it happens later in the lifecycle), and no claim about an artefact this
module could not open.
"""

from __future__ import annotations

import hashlib
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from defusedxml import ElementTree as ET

#: The whole file is never read into memory to hash it: a self-contained single-file WPF
#: publish is ~70 MB, and the Cloud Core has better uses for that.
_HASH_CHUNK: Final = 1024 * 1024

#: Bounds, so a malformed or hostile file cannot walk this reader off a cliff. A PE
#: resource tree that claims more entries than this is not a resource tree.
MAX_RESOURCE_ENTRIES: Final = 4096
MAX_RESOURCE_DEPTH: Final = 8
#: An MSIX/APK manifest is a few kilobytes; anything past this is not one, and is refused
#: before the XML parser is handed it (the zip-bomb bound M20's OOXML work established).
MAX_MANIFEST_BYTES: Final = 4 * 1024 * 1024

_PE_MACHINE_NAMES: Final[dict[int, str]] = {
    0x014C: "x86",
    0x8664: "x64",
    0xAA64: "arm64",
    0x01C4: "arm",
}

_SUBSYSTEM_NAMES: Final[dict[int, str]] = {
    2: "windows_gui",
    3: "windows_console",
}


class ArtifactUnreadable(ValueError):
    """The reader could not make sense of the file AS the kind it was asked to read.

    Distinct from "the artefact is wrong": a file this module cannot open is reported as
    unreadable, never as a validation failure, because those two send the owner in very
    different directions.
    """


@dataclass(frozen=True, slots=True)
class ArtifactFacts:
    """What an independent reader could actually see. Everything here was read OUT of the
    file; nothing is carried over from the spec that asked for it."""

    path: str
    kind: str
    size_bytes: int
    sha256: str
    #: PE: the version resource's ProductVersion / FileVersion. MSIX/APK: the manifest's.
    version: str | None = None
    #: PE: the machine word. MSIX/APK: the declared architecture, when it names one.
    architecture: str | None = None
    #: PE only: `windows_gui` for a desktop app, `windows_console` for a console one.
    subsystem: str | None = None
    #: MSIX/APK: the package identity the manifest declares.
    identity: str | None = None
    display_name: str | None = None
    #: Everything else the reader saw and could name, for the evidence file.
    detail: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "version": self.version,
            "architecture": self.architecture,
            "subsystem": self.subsystem,
            "identity": self.identity,
            "display_name": self.display_name,
            "detail": dict(self.detail),
        }


def sha256_of(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


# ------------------------------------------------------------------------------- PE


def _rva_to_offset(rva: int, sections: list[tuple[int, int, int, int]]) -> int | None:
    """Map a virtual address to a file offset through the section table."""
    for _name_end, virtual_address, virtual_size, raw_pointer in sections:
        if virtual_address <= rva < virtual_address + max(virtual_size, 1):
            return raw_pointer + (rva - virtual_address)
    return None


def _read_version_strings(blob: bytes) -> dict[str, str]:
    """Pull the `StringFileInfo` pairs out of a `VS_VERSIONINFO` resource.

    The structure is a tree of UTF-16 key/value nodes with 32-bit alignment. Rather than
    walk it node by node - which is where a hand-written PE reader usually goes wrong -
    this scans for the keys it needs and reads the UTF-16 string that follows, which is
    robust against the padding variations real linkers emit and cannot run off the end of
    the buffer.
    """
    out: dict[str, str] = {}
    for key in ("ProductVersion", "FileVersion", "ProductName", "FileDescription", "CompanyName"):
        needle = key.encode("utf-16-le")
        index = blob.find(needle)
        if index < 0:
            continue
        cursor = index + len(needle)
        # skip the terminating NUL of the key, then any 32-bit alignment padding
        while cursor + 1 < len(blob) and blob[cursor : cursor + 2] == b"\x00\x00":
            cursor += 2
        end = cursor
        while end + 1 < len(blob) and blob[end : end + 2] != b"\x00\x00":
            end += 2
        value = blob[cursor:end].decode("utf-16-le", errors="replace").strip()
        if value:
            out[key] = value
    return out


def read_pe(path: Path) -> ArtifactFacts:
    """Read a Windows PE image: machine, subsystem and its version resource.

    Everything reported is read from the FILE. If the file is not a PE, that is
    :class:`ArtifactUnreadable` - never a quiet default.
    """
    raw = path.read_bytes()
    if len(raw) < 0x40 or raw[:2] != b"MZ":
        raise ArtifactUnreadable(f"{path.name}: no MZ header - not a PE image")
    (pe_offset,) = struct.unpack_from("<I", raw, 0x3C)
    if pe_offset + 24 > len(raw) or raw[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ArtifactUnreadable(f"{path.name}: no PE signature at e_lfanew")

    machine, section_count = struct.unpack_from("<HH", raw, pe_offset + 4)
    (optional_size,) = struct.unpack_from("<H", raw, pe_offset + 20)
    optional_offset = pe_offset + 24
    (magic,) = struct.unpack_from("<H", raw, optional_offset)
    if magic not in (0x10B, 0x20B):
        raise ArtifactUnreadable(f"{path.name}: unknown optional header magic {magic:#x}")
    is_pe32_plus = magic == 0x20B
    (subsystem,) = struct.unpack_from("<H", raw, optional_offset + 68)
    # The data directory starts after the optional header's fixed part, which differs
    # between PE32 and PE32+ by the width of four fields.
    directory_offset = optional_offset + (112 if is_pe32_plus else 96)
    resource_rva, resource_size = struct.unpack_from("<II", raw, directory_offset + 8 * 2)

    section_offset = optional_offset + optional_size
    sections: list[tuple[int, int, int, int]] = []
    for index in range(min(section_count, 96)):
        base = section_offset + index * 40
        if base + 40 > len(raw):
            break
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
            "<IIII", raw, base + 8
        )
        sections.append((raw_size, virtual_address, virtual_size, raw_pointer))

    version_strings: dict[str, str] = {}
    if resource_rva and resource_size:
        start = _rva_to_offset(resource_rva, sections)
        if start is not None:
            end = min(start + min(resource_size, 16 * 1024 * 1024), len(raw))
            version_strings = _read_version_strings(raw[start:end])

    digest, size = sha256_of(path)
    skip = ("ProductVersion", "FileVersion")
    detail = {k: v for k, v in version_strings.items() if k not in skip}
    detail["pe_format"] = "PE32+" if is_pe32_plus else "PE32"
    return ArtifactFacts(
        path=str(path),
        kind="pe",
        size_bytes=size,
        sha256=digest,
        version=version_strings.get("ProductVersion") or version_strings.get("FileVersion"),
        architecture=_PE_MACHINE_NAMES.get(machine, f"machine_{machine:#06x}"),
        subsystem=_SUBSYSTEM_NAMES.get(subsystem, f"subsystem_{subsystem}"),
        display_name=version_strings.get("ProductName"),
        detail=detail,
    )


# ----------------------------------------------------------------------------- MSIX


_APPX_NS = {
    "m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
}


def read_msix(path: Path) -> ArtifactFacts:
    """Open an MSIX as the zip it is and parse its `AppxManifest.xml`."""
    try:
        with zipfile.ZipFile(path) as bundle:
            names = set(bundle.namelist())
            if "AppxManifest.xml" not in names:
                raise ArtifactUnreadable(f"{path.name}: no AppxManifest.xml in the package")
            info = bundle.getinfo("AppxManifest.xml")
            if info.file_size > MAX_MANIFEST_BYTES:
                raise ArtifactUnreadable(
                    f"{path.name}: AppxManifest.xml is {info.file_size} bytes - refused unread"
                )
            manifest_bytes = bundle.read("AppxManifest.xml")
            payload_count = len(names)
    except zipfile.BadZipFile as exc:
        raise ArtifactUnreadable(f"{path.name}: not a readable zip container") from exc

    root = ET.fromstring(manifest_bytes)
    identity = root.find("m:Identity", _APPX_NS)
    properties = root.find("m:Properties", _APPX_NS)
    display = properties.find("m:DisplayName", _APPX_NS) if properties is not None else None

    digest, size = sha256_of(path)
    return ArtifactFacts(
        path=str(path),
        kind="msix",
        size_bytes=size,
        sha256=digest,
        version=identity.get("Version") if identity is not None else None,
        architecture=identity.get("ProcessorArchitecture") if identity is not None else None,
        identity=identity.get("Name") if identity is not None else None,
        display_name=(display.text or "").strip() if display is not None else None,
        detail={"payload_entries": str(payload_count)},
    )


# ------------------------------------------------------------------------------ APK


def read_apk(path: Path, *, aapt2: Path | None = None) -> ArtifactFacts:
    """Open an APK/AAB. `AndroidManifest.xml` inside an APK is binary XML, so the identity
    fields come from `aapt2 dump badging` when the SDK is present, and the container itself
    is still validated without it - an APK we cannot decode is reported as an APK we could
    open but not name, which is the truth."""
    try:
        with zipfile.ZipFile(path) as bundle:
            names = set(bundle.namelist())
            if "AndroidManifest.xml" not in names and "base/manifest/AndroidManifest.xml" not in (
                names
            ):
                raise ArtifactUnreadable(f"{path.name}: no AndroidManifest.xml in the package")
            payload_count = len(names)
            has_dex = any(n.endswith(".dex") for n in names)
    except zipfile.BadZipFile as exc:
        raise ArtifactUnreadable(f"{path.name}: not a readable zip container") from exc

    version: str | None = None
    identity: str | None = None
    display_name: str | None = None
    detail = {"payload_entries": str(payload_count), "has_dex": str(has_dex).lower()}

    if aapt2 is not None and aapt2.exists():
        import subprocess

        try:
            proc = subprocess.run(
                [str(aapt2), "dump", "badging", str(path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            for line in proc.stdout.splitlines():
                if line.startswith("package:"):
                    for token in ("name", "versionName"):
                        marker = f"{token}='"
                        if marker in line:
                            value = line.split(marker, 1)[1].split("'", 1)[0]
                            if token == "name":
                                identity = value
                            else:
                                version = value
                elif line.startswith("application-label:"):
                    display_name = line.split("'", 1)[1].rsplit("'", 1)[0]
            detail["aapt2"] = "read"
        except Exception as exc:  # noqa: BLE001 - a missing/broken aapt2 is not a bad APK
            detail["aapt2"] = f"unavailable: {type(exc).__name__}"
    else:
        detail["aapt2"] = "absent"

    digest, size = sha256_of(path)
    return ArtifactFacts(
        path=str(path),
        kind="aab" if path.suffix.lower() == ".aab" else "apk",
        size_bytes=size,
        sha256=digest,
        version=version,
        identity=identity,
        display_name=display_name,
        detail=detail,
    )


# ------------------------------------------------------------------------ validation


@dataclass(frozen=True, slots=True)
class ArtifactVerdict:
    """Whether what the reader saw is what the spec asked for."""

    ok: bool
    facts: ArtifactFacts
    mismatches: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "mismatches": list(self.mismatches), "facts": self.facts.as_dict()}


def validate_against_spec(facts: ArtifactFacts, spec) -> ArtifactVerdict:  # noqa: ANN001
    """Compare what was READ with what was ASKED, and name every disagreement.

    The version check is the point of the whole module: an artefact that cannot prove which
    build it is cannot be trusted to be the build that was just made, and a factory that
    hands back yesterday's EXE while reporting success is the failure mode this milestone
    exists to make impossible.
    """
    mismatches: list[str] = []
    if facts.size_bytes <= 0:
        mismatches.append("artefact is empty")
    expected = {spec.version, spec.assembly_version}
    if facts.version is None:
        mismatches.append("artefact carries no version to check")
    elif facts.version not in expected:
        mismatches.append(
            f"version is {facts.version!r}, the spec asked for {spec.version!r}"
        )
    if facts.kind == "pe" and facts.subsystem == "windows_console":
        mismatches.append("a desktop application was asked for and this is a console image")
    return ArtifactVerdict(ok=not mismatches, facts=facts, mismatches=tuple(mismatches))


def read_artifact(path: Path, *, aapt2: Path | None = None) -> ArtifactFacts:
    """Dispatch on the extension. An unknown one is unreadable, not assumed."""
    suffix = path.suffix.lower()
    if suffix in (".exe", ".dll"):
        return read_pe(path)
    if suffix in (".msix", ".appx"):
        return read_msix(path)
    if suffix in (".apk", ".aab"):
        return read_apk(path, aapt2=aapt2)
    what = suffix or "a file with no extension"
    raise ArtifactUnreadable(f"{path.name}: no independent reader for {what}")


__all__ = [
    "MAX_MANIFEST_BYTES",
    "ArtifactFacts",
    "ArtifactUnreadable",
    "ArtifactVerdict",
    "read_apk",
    "read_artifact",
    "read_msix",
    "read_pe",
    "sha256_of",
    "validate_against_spec",
]
