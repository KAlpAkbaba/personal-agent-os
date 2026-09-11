"""Produce DURABLE evidence for QUALIFICATION row 26.7 — a portable package and an MSIX.

Row 26.7 carried `PROVEN_REAL` and two exact byte counts while no artefact in the
repository recorded them: the eight `m28-native-windows-lab-*.json` runs all stop at the
EXE, and `native-windows-lab.py` has no packaging step and imports nothing from
`packaging.py`. A proof mark whose evidence exists only in prose is a claim. The mark was
lowered on 2026-09-11; this script is how it is earned back.

What it does, and does not do:

* It packages an ALREADY PUBLISHED tree. It does not compile — `native-windows-lab.py`
  owns that, and re-running a compiler here would prove something that script already
  proved byte-identically eight times.
* It calls the SAME `app.nativefactory.packaging` functions the product uses, not a
  reimplementation. That is the whole point: the evidence must be about the shipped code.
* It reads both artefacts back with `app.nativefactory.artifacts`, which did not write
  them, and compares the MSIX against the spec through `validate_against_spec` — the
  version check that module calls "the point of the whole module".
* It writes one JSON under `docs/evidence/`. Every number in row 26.7 must be traceable to
  a key in that file.

Usage (Windows, from the repository root):

    python scripts/tests/native-packaging-lab.py --publish-dir "<...>/item28-build/out"

`--makeappx` is found under the Windows Kits root when not given. A missing `makeappx.exe`
is reported as `PROVIDER_UNAVAILABLE`, never as a failure of the packaging code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from app.nativefactory.artifacts import (  # noqa: E402
    ArtifactUnreadable,
    read_artifact,
    validate_against_spec,
)
from app.nativefactory.packaging import PackagingError, make_msix, make_portable_zip  # noqa: E402
from app.nativefactory.spec import parse_spec  # noqa: E402

#: The item-28 application, exactly as the lab built it. Kept here rather than read from a
#: manifest so the evidence records what was ASKED for independently of what was found.
ITEM28_SPEC = {
    "name": "notlarim",
    "title": "Notlarım",
    "template": "notes-desktop",
    "stack": "dotnet_wpf",
    "targets": ["windows_exe", "windows_portable", "windows_msix"],
    "version": "0.1.0",
    "persistence": "local_file",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_makeappx() -> Path | None:
    """Walk the Windows Kits bin tree. Newest SDK first, x64 before x86."""
    for base in (
        Path(r"C:\Program Files (x86)\Windows Kits\10\bin"),
        Path(r"C:\Program Files\Windows Kits\10\bin"),
    ):
        if not base.is_dir():
            continue
        for sdk in sorted(base.iterdir(), reverse=True):
            for arch in ("x64", "x86"):
                candidate = sdk / arch / "makeappx.exe"
                if candidate.is_file():
                    return candidate
    return None


def _read_zip_back(path: Path, publish_dir: Path, slug: str) -> dict:
    """Open the portable package as the zip it is, and prove the EXE inside is the one we
    published.

    ``read_artifact`` deliberately does not dispatch on ``.zip`` — a portable package has
    no version of its own to check, so there is nothing for ``validate_against_spec`` to
    compare. That is not a reason to record no read at all: the package is opened here,
    every entry is listed, the archive's own CRC table is verified, and the EXE is
    extracted and hashed against the file that went in. An artefact nobody opened is the
    thing row 26.7 was lowered for.
    """
    import zipfile

    with zipfile.ZipFile(path) as bundle:
        bad = bundle.testzip()
        names = bundle.namelist()
        exe_name = f"{slug}.exe"
        inner = bundle.read(exe_name) if exe_name in names else None
    source = (publish_dir / exe_name)
    return {
        "status": "read",
        "reader": "zipfile (stdlib), not the writer",
        "entry_count": len(names),
        "first_corrupt_entry": bad,
        "crc_table_ok": bad is None,
        "exe_entry_present": inner is not None,
        "exe_sha256_in_package": hashlib.sha256(inner).hexdigest() if inner else None,
        "exe_sha256_on_disk": _sha256(source) if source.is_file() else None,
        "exe_matches_published": bool(
            inner is not None
            and source.is_file()
            and hashlib.sha256(inner).hexdigest() == _sha256(source)
        ),
    }


def _read_back(path: Path, spec) -> dict:  # noqa: ANN001
    """Open an artefact with the reader that did not write it, and say what it saw."""
    out: dict = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    try:
        facts = read_artifact(path)
    except ArtifactUnreadable as exc:
        out["read_back"] = {"status": "unreadable", "detail": str(exc)}
        return out
    out["read_back"] = {"status": "read", "facts": facts.as_dict()}
    verdict = validate_against_spec(facts, spec)
    out["verdict"] = verdict.as_dict()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-dir", required=True, type=Path)
    parser.add_argument("--makeappx", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--evidence-dir", type=Path, default=REPO_ROOT / "docs" / "evidence"
    )
    args = parser.parse_args()

    publish_dir = args.publish_dir.resolve()
    spec = parse_spec(ITEM28_SPEC)
    started = datetime.now(UTC)

    record: dict = {
        "generated_at": started.isoformat().replace("+00:00", "Z"),
        "note": (
            "Durable evidence for QUALIFICATION row 26.7. Packaging only - the EXE was built "
            "by scripts/tests/native-windows-lab.py and is reused here. Both artefacts are "
            "produced by app.nativefactory.packaging (the shipped code) and read back by "
            "app.nativefactory.artifacts, which did not write them."
        ),
        "spec": ITEM28_SPEC,
        "publish_dir": str(publish_dir),
    }

    if not publish_dir.is_dir():
        record["status"] = "FAILED"
        record["detail"] = f"no publish directory at {publish_dir}"
        _emit(record, args.evidence_dir, started)
        return 2

    published = [p for p in publish_dir.rglob("*") if p.is_file()]
    exe = publish_dir / f"{spec.slug}.exe"
    record["publish_input"] = {
        "file_count": len(published),
        "total_bytes": sum(p.stat().st_size for p in published),
        "exe_present": exe.is_file(),
        "exe_size_bytes": exe.stat().st_size if exe.is_file() else None,
        "exe_sha256": _sha256(exe) if exe.is_file() else None,
    }

    work = Path(args.out_dir).resolve() if args.out_dir else Path(tempfile.mkdtemp(prefix="m28-pkg-"))
    work.mkdir(parents=True, exist_ok=True)
    record["work_dir"] = str(work)

    # ---------------------------------------------------------------- portable
    try:
        zip_result = make_portable_zip(publish_dir, work / f"{spec.slug}-portable.zip")
        record["windows_portable"] = {
            "path": str(zip_result.path),
            "size_bytes": zip_result.path.stat().st_size,
            "sha256": _sha256(zip_result.path),
            "read_back": _read_zip_back(zip_result.path, publish_dir, spec.slug),
            "signed": zip_result.signed,
            "status": "BUILT",
        }
    except PackagingError as exc:
        record["windows_portable"] = {"status": "FAILED", "detail": str(exc)}

    # -------------------------------------------------------------------- msix
    makeappx = args.makeappx or _find_makeappx()
    if makeappx is None or not Path(makeappx).is_file():
        record["windows_msix"] = {
            "status": "PROVIDER_UNAVAILABLE",
            "detail": "makeappx.exe was not found under any Windows Kits bin directory",
        }
    else:
        record["makeappx"] = str(makeappx)
        try:
            msix_result = make_msix(
                spec,
                publish_dir,
                work / f"{spec.slug}.msix",
                makeappx=str(makeappx),
            )
            record["windows_msix"] = _read_back(msix_result.path, spec)
            record["windows_msix"]["signed"] = msix_result.signed
            record["windows_msix"]["status"] = "BUILT"
        except PackagingError as exc:
            record["windows_msix"] = {"status": "FAILED", "detail": str(exc)}

    built = [
        key
        for key in ("windows_portable", "windows_msix")
        if record.get(key, {}).get("status") == "BUILT"
    ]
    agreed = [key for key in built if record[key].get("verdict", {}).get("ok")]
    unreadable = [
        key for key in built if record[key].get("read_back", {}).get("status") == "unreadable"
    ]
    portable = record.get("windows_portable", {}).get("read_back", {})
    portable_ok = bool(portable.get("crc_table_ok") and portable.get("exe_matches_published"))
    # A zip carries no version of its own, so it is read for what it is and is not expected
    # to produce a spec verdict; the MSIX is the one that must agree.
    # Both halves must have been OPENED by something that did not write them: the MSIX by
    # the product's own reader (and agreeing with the spec), the zip by the standard
    # library with its CRC table verified and the EXE inside hashed against the published
    # file. Either half unopened and this is not a proof.
    record["status"] = (
        "PROVEN_REAL"
        if "windows_msix" in agreed and "windows_portable" in built and portable_ok
        else "INCOMPLETE"
    )
    record["summary"] = {
        "built": built,
        "spec_verdict_agrees": agreed,
        "unreadable": unreadable,
        "portable_opened_and_matches": portable_ok,
        "duration_s": round((datetime.now(UTC) - started).total_seconds(), 3),
    }
    _emit(record, args.evidence_dir, started)
    return 0 if record["status"] == "PROVEN_REAL" else 1


def _emit(record: dict, evidence_dir: Path, started: datetime) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    name = f"m28-native-packaging-{started.strftime('%Y-%m-%d-%H%M%S')}.json"
    path = evidence_dir / name
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"\nevidence: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
