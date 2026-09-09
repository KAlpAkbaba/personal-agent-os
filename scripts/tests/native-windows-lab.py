"""M28's Windows lab: an owner-style request, through the real pipeline, to a real EXE.

    "Bana notlarımı tutacak bir Windows masaüstü uygulaması yap."
        -> NativeAppSpec
        -> project generation (the built-in template, every slot filled)
        -> the M23 ProjectFiles policy
        -> dotnet build
        -> dotnet test   (the store's own tests, including persistence across a restart)
        -> dotnet publish -c Release -r win-x64 --self-contained
        -> a real .exe
        -> read back by app.nativefactory.artifacts, which did not build it

No fake is in the path. The compiler is the real one, the tests are the generated project's
own, and the file at the end is a Windows binary this script did not write and validates
with a reader that knows nothing about the template.

What this lab deliberately does NOT do: launch the EXE. Launching and driving it is M19's
job and needs the device runtime the owner has not installed yet (item 28), so this lab
stops exactly where the honest boundary is and says so in its evidence.

Usage:
    services/api/.venv/Scripts/python.exe scripts/tests/native-windows-lab.py [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "api"))

from app.appfactory.validation import validate_files  # noqa: E402
from app.nativefactory.artifacts import (  # noqa: E402
    read_artifact,
    validate_against_spec,
)
from app.nativefactory.generator import render  # noqa: E402
from app.nativefactory.spec import parse_spec  # noqa: E402

DOTNET = Path(r"C:\Program Files\dotnet\dotnet.exe")

#: The owner's request, as a spec. Written the way the assistant would write it from
#: "Bana notlarımı tutacak bir Windows masaüstü uygulaması yap." - a name, a template, a
#: target, and the closed feature names. No sentence of the owner's reaches a file.
REQUEST = {
    "name": "Notlarim",
    "title": "Notlarım",
    "template": "notes-desktop",
    "targets": ["windows_exe"],
    "version": "0.1.0",
    "persistence": "local_file",
    "features": ["add_item", "list_items", "delete_item", "persist_local"],
}


#: The dotnet CLI on this machine answers in Turkish, so a summary line parsed by its
#: English words came back empty and the evidence recorded `None` where a test count
#: belonged. Pinning the CLI's language makes the output machine-readable wherever this
#: runs - the owner's machine, the CI runner, or a future one with a third locale.
DOTNET_ENV = {"DOTNET_CLI_UI_LANGUAGE": "en", "DOTNET_NOLOGO": "1"}


def run(cmd: list[str], cwd: Path, *, timeout: int = 900) -> tuple[int, str]:
    started = time.monotonic()
    env = {**os.environ, **DOTNET_ENV}
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )
    took = time.monotonic() - started
    tail = (proc.stdout or "")[-4000:] + (proc.stderr or "")[-2000:]
    shown = " ".join(Path(c).name if ":" in c else c for c in cmd[:3])
    print(f"    {shown}... exit={proc.returncode} in {took:.1f}s")
    return proc.returncode, tail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="leave the project on disk")
    args = parser.parse_args()

    if not DOTNET.exists():
        print(f"FAIL: no dotnet at {DOTNET}")
        return 2

    evidence: dict[str, object] = {
        "kind": "m28_native_windows_lab",
        "measured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "machine": "the owner's Windows machine",
        "request": REQUEST,
    }

    print("== 1. the request becomes a validated spec")
    spec = parse_spec(REQUEST)
    print(f"    slug={spec.slug} stack={spec.resolved_stack} version={spec.version}")
    evidence["spec"] = {
        "slug": spec.slug,
        "stack": spec.resolved_stack,
        "version": spec.version,
        "targets": spec.targets,
    }

    print("== 2. the template renders, every slot filled")
    project = render(spec)
    print(f"    {len(project)} files")
    evidence["generated_files"] = sorted(f.path for f in project.files)

    print("== 3. the M23 ProjectFiles policy accepts it")
    report = validate_files(project)
    print(f"    policy ok: {report.file_count} files, {report.total_bytes:,} bytes")
    evidence["policy"] = {"accepted": True, "files": report.file_count, "bytes": report.total_bytes}

    workdir = Path(tempfile.mkdtemp(prefix="pagentos-m28-"))
    try:
        for file in project.files:
            target = workdir / file.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file.text, encoding="utf-8")
        print(f"== 4. written to {workdir}")

        manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
        entry = manifest["entry"]
        tests_project = manifest["tests"]
        print(f"== 5. dotnet build {entry}  (the path the manifest names)")
        code, out = run([str(DOTNET), "build", entry, "-c", "Release", "--nologo"], workdir)
        evidence["manifest"] = manifest
        evidence["build"] = {"exit": code, "project": entry}
        if code != 0:
            evidence["build"]["output"] = out[-3000:]
            print(out[-3000:])
            _write(evidence)
            return 1

        print(f"== 6. dotnet test {tests_project}  (the generated project's own)")
        code, out = run(
            [str(DOTNET), "test", tests_project, "-c", "Release", "--nologo"], workdir
        )
        passed = None
        for line in out.splitlines():
            if "Passed:" in line or "Failed:" in line:
                passed = line.strip()[:200]
        evidence["tests"] = {"exit": code, "summary": passed}
        print(f"    {passed}")
        if code != 0:
            evidence["tests"]["output"] = out[-3000:]
            print(out[-3000:])
            _write(evidence)
            return 1

        print("== 7. dotnet publish -r win-x64 --self-contained (folder)")
        publish_dir = workdir / "out"
        code, out = run(
            [
                str(DOTNET), "publish", entry,
                # NOT PublishSingleFile: measured on 2026-09-09, single-file needs
                # `Microsoft.NET.ILLink.Tasks`, which cannot be restored on this machine
                # (NU1100). A self-contained FOLDER publish produces the same real EXE and
                # is what `windows_portable` zips, so the milestone loses nothing - and the
                # reason is recorded rather than the flag quietly dropped.
                "-c", "Release", "-r", "win-x64", "--self-contained", "true",
                "--nologo", "-o", str(publish_dir),
            ],
            workdir,
        )
        evidence["publish"] = {"exit": code}
        if code != 0:
            evidence["publish"]["output"] = out[-3000:]
            print(out[-3000:])
            _write(evidence)
            return 1

        exe = publish_dir / f"{spec.slug}.exe"
        if not exe.exists():
            print(f"FAIL: publish produced no {exe.name}")
            evidence["publish"]["error"] = f"no {exe.name}"
            _write(evidence)
            return 1

        print("== 8. an independent reader opens the produced binary")
        facts = read_artifact(exe)
        verdict = validate_against_spec(facts, spec)
        print(f"    {exe.name}: {facts.size_bytes:,} bytes, {facts.architecture}, "
              f"{facts.subsystem}, version {facts.version!r}")
        print(f"    sha256 {facts.sha256[:16]}...")
        print(f"    verdict: {'AGREES' if verdict.ok else 'MISMATCH ' + str(verdict.mismatches)}")
        evidence["artifact"] = verdict.as_dict()

        # The EXE is kept where the evidence can name it even when the tree is cleaned up.
        if args.keep:
            evidence["kept_at"] = str(publish_dir)

        evidence["m19_launch"] = {
            "status": "NOT_YET_PROVEN",
            "why": (
                "Launching the produced EXE and driving its window is M19's path through the "
                "device runtime. The installed runtime is 1.0.0+a3cb04e and advertises 29 "
                "capabilities with no app.launch/window.*/ui.*; it arrives with owner item 28."
            ),
        }
        _write(evidence)
        return 0 if verdict.ok else 1
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


def _write(evidence: dict[str, object]) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
    out = REPO / "docs" / "evidence" / f"m28-native-windows-lab-{stamp}.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nevidence: {out.relative_to(REPO)}")


if __name__ == "__main__":
    raise SystemExit(main())
