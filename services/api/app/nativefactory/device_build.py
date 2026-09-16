"""The Cloud Core half of a native build that actually happens on the device (M28 §5/§9).

Everything else in this package builds where the *caller* points it: ``generate()`` writes a
rendered tree to a directory and ``build_and_test()`` runs a ``BuildRunner`` over it. That is
the right shape for the lab and for the tests, and it is the wrong shape for production —
because production is a Linux Cloud Core with no .NET SDK, no ``makeappx`` and no Windows, and
the machine that has all three is the owner's enrolled device.

So this module is the other implementation of the same lifecycle, and it owns exactly one
thing: turning a rendered project into a build that ran **on the device**, through capabilities
the device already advertises.

    project.scaffold(root="native")   the rendered files, written under the native root
    project.run(command_key="build")  dotnet build, as a bounded batch child
    project.test                      dotnet test, same bounds
    project.run(command_key="publish")  dotnet publish -r win-x64 --self-contained
    file.inspect                      the artefact read back by the device itself

No new capability name (DEVICE_PROTOCOL.md §6n): the four argv shapes ride the projects
family's manifest allowlist, matched token for token, under the one root where a compiler may
run. The manifest this module writes is therefore not free-form — every command in it is one
of the four forms the device will admit, and a fifth would be refused at scaffold time, before
a process exists.

The device is reached through :class:`app.routines.dispatch.DeviceActionPort` — the same port
M23's app factory uses, so this adds no second way to talk to a device and is testable with
the same fakes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.appfactory.validation import validate_files
from app.nativefactory.artifacts import ArtifactFacts, validate_against_spec
from app.nativefactory.device_lifecycle import project_id_for
from app.nativefactory.generator import render
from app.nativefactory.models import (
    STATE_BUILDING,
    STATE_FAILED,
    STATE_GENERATING,
    STATE_MISMATCH,
    STATE_TESTING,
    STATE_UNAVAILABLE,
    STATE_UNVERIFIED,
    STATE_VERIFIED,
    NativeBuildRow,
)
from app.nativefactory.roots import check_extensions
from app.nativefactory.service import BUILD_TIMEOUT_S, _tail, _touch
from app.nativefactory.spec import (
    ANDROID_TARGETS,
    TARGET_ANDROID_AAB,
    TARGET_ANDROID_APK,
    TARGET_WINDOWS_MSIX,
    TARGET_WINDOWS_PORTABLE,
    NativeAppSpec,
    NativeFactoryError,
)
from app.nativefactory.stacks import (
    DEVICE_BUILDABLE_TARGETS,
    SPEECH_DEVICE_PACKAGING_NOT_WIRED,
)
from app.routines.dispatch import DeviceActionPort, DeviceRunResult

#: The capabilities this path needs. A device that does not advertise all of them cannot build,
#: and saying which one is missing is more useful than "unavailable".
#: B33 req 456/457: makeappx on a cold machine, bounded (the device's own ceiling is 5 min).
PACKAGE_TIMEOUT_S: float = 330.0

REQUIRED_CAPABILITIES: tuple[str, ...] = (
    "project.scaffold",
    "project.run",
    "project.test",
    "file.inspect",
)

#: How long a command may take before it EXPIRES. The device runs a command for the time left
#: until its expiry, clamped to its own cap (InteractiveCapabilityExecutor.ResolveTimeout), so
#: this number is the build's budget on the device, not only this side's patience. It was 30 s
#: for a compile and 330 s for a test until 2026-09-16 (ADR-0161), which ended every device
#: build that took longer than that - a real Gradle build of the Android template takes minutes.
#: A build or a test now gets the device's own ceiling for a native command: the build bound
#: plus the 30 s it keeps for the typed answer (§6n, NativeCapabilityNames.CommandTimeoutCap).
SCAFFOLD_TIMEOUT_S = 30.0
RUN_TIMEOUT_S = float(BUILD_TIMEOUT_S + 30)
TEST_TIMEOUT_S = float(BUILD_TIMEOUT_S + 30)
INSPECT_TIMEOUT_S = 30.0

#: B49 (ADR-0161): the three Gradle shapes the device admits, token for token (§6n).
GRADLE = "gradle --no-daemon --console=plain"
#: Which run command makes which Android target.
ANDROID_RUN_KEY: dict[str, str] = {TARGET_ANDROID_APK: "build", TARGET_ANDROID_AAB: "bundle"}


@dataclass(frozen=True, slots=True)
class DeviceBuildOutcome:
    """What happened, in the terms the row and the receipt both need."""

    ok: bool
    error_class: str = ""
    message: str = ""
    root_path: str = ""
    artifact_path: str = ""
    artifact: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)


def native_manifest(csproj: str, publish_dir: str) -> dict[str, Any]:
    """The manifest a native project carries, holding ONLY forms the device admits.

    Written here rather than by the template because the device validates these token for
    token at scaffold time: a manifest is a promise about what may run, and the promise has to
    be one the device is willing to keep. `port` is 0 because nothing here binds one — every
    command is a batch run (§6n).
    """
    return {
        "entry": csproj,
        "port": 0,
        "run": {
            "build": f"dotnet build {csproj} -c Release",
            "publish": (
                f"dotnet publish {csproj} -c Release -r win-x64 "
                f"--self-contained true -o {publish_dir}"
            ),
        },
        # An OBJECT of {key: command}, like `run`: the device's parser refuses a bare string
        # ("manifest.test must be a non-empty object of {key: command}"), which is what the
        # first real production build hit, at the first device step (2026-09-12).
        "test": {"unit": f"dotnet test {csproj} -c Release"},
    }


def android_manifest(entry: str) -> dict[str, Any]:
    """The manifest an Android project carries: the three Gradle shapes and nothing else.

    No path and no property reaches the device: the project folder is Gradle's working
    directory, and the task is the only word that differs. `assembleDebug` makes the APK the
    device can install (the Android plugin signs debug builds with its own throwaway key),
    `bundleRelease` makes the unsigned bundle, `test` runs the template's JVM unit tests.
    """
    return {
        "entry": entry,
        "port": 0,
        "run": {"build": f"{GRADLE} assembleDebug", "bundle": f"{GRADLE} bundleRelease"},
        "test": {"unit": f"{GRADLE} test"},
    }


#: The Android manifest for the template's own entry, in the file both halves read.
ANDROID_MANIFEST_EXAMPLE_ENTRY = "app/build.gradle.kts"


#: The manifest above for one canonical project, written where BOTH halves read it: the
#: Cloud Core test keeps it equal to what `native_manifest` returns, and the device's own
#: test scaffolds it through the real parser. Restating the shape in two languages is how
#: two green suites drift - and they did.
MANIFEST_EXAMPLE_CSPROJ = "src/notlarim/notlarim.csproj"
MANIFEST_EXAMPLE_PUBLISH_DIR = "publish"


def _as_text(value: object) -> str | None:
    """A JSON value the device sent, as text -- or None. Never str(None)."""
    return str(value) if isinstance(value, str) and value.strip() else None


def _fail(
    db: Session, row: NativeBuildRow, result: DeviceRunResult, *, step: str
) -> DeviceBuildOutcome:
    """One device refusal, recorded on the row in the device's OWN words.

    The device's message is the most useful thing it produces — it named the allowlist when a
    Blender command was wrong, and named the owning project when a folder was taken — so it is
    kept whole rather than replaced with a category.
    """
    _touch(
        db,
        row,
        STATE_FAILED,
        error_class=result.error_class or "device_refused",
        error_message=_tail(f"{step}: {result.message}", 1000),
    )
    return DeviceBuildOutcome(
        ok=False,
        error_class=result.error_class or "device_refused",
        message=f"{step}: {result.message}",
    )


def build_on_device(
    db: Session,
    row: NativeBuildRow,
    device: DeviceActionPort,
    *,
    slug: str | None = None,
) -> DeviceBuildOutcome:
    """Render this row's spec, scaffold it onto the device, and build it there.

    The row moves through the same states the local path uses, so a caller reading a row
    cannot tell which machine compiled it — only the artefact's path says that, and it is a
    Windows path because a Windows device produced it.
    """
    project_slug = slug or row.slug
    project_id = project_id_for(row)  # B33: ONE rule, shared with device_lifecycle

    # ---- only what this path can honestly make -------------------------------------------
    # It publishes and reads back ONE artefact, the EXE. The judge (validate_against_spec)
    # compares version and subsystem, not kind, so a portable or MSIX row sent down here
    # would come back `verified` carrying an EXE. Refused before the device is asked.
    if row.target not in DEVICE_BUILDABLE_TARGETS:
        _touch(
            db,
            row,
            STATE_UNAVAILABLE,
            error_class="dependency_unavailable",
            error_message=SPEECH_DEVICE_PACKAGING_NOT_WIRED,
        )
        return DeviceBuildOutcome(
            ok=False,
            error_class="dependency_unavailable",
            message=SPEECH_DEVICE_PACKAGING_NOT_WIRED,
        )

    # ---- render, and let the SAME policy that guards the local path judge it -------------
    _touch(db, row, STATE_GENERATING)
    spec = NativeAppSpec.model_validate(row.spec_json)
    try:
        project = render(spec)
        check_extensions(project)
    except NativeFactoryError as exc:
        _touch(db, row, STATE_UNAVAILABLE, error_class=exc.error_class, error_message=exc.speech)
        return DeviceBuildOutcome(ok=False, error_class=exc.error_class, message=exc.speech)

    report = validate_files(project)
    if not report.ok:
        message = "; ".join(report.errors)[:1000]
        _touch(db, row, STATE_FAILED, error_class="policy_refused", error_message=message)
        return DeviceBuildOutcome(ok=False, error_class="policy_refused", message=message)

    manifest = json.loads(next(f.text for f in project.files if f.path == "manifest.json"))
    is_android = row.target in ANDROID_TARGETS
    publish_dir = "out"
    if is_android:
        device_manifest = android_manifest(str(manifest["entry"]))
    else:
        device_manifest = native_manifest(str(manifest["entry"]), publish_dir)
    scaffold_files = [{"path": f.path, "text": f.text} for f in project.files]
    if row.target == TARGET_WINDOWS_MSIX:
        # B33 req 457: the device packs what the Cloud Core declares - the manifest is
        # scaffolded beside the sources (never generated on the device), so what the
        # package claims to be is what this row's spec says.
        from app.nativefactory.packaging import appx_manifest_text

        scaffold_files.append(
            {"path": "staging/AppxManifest.xml", "text": appx_manifest_text(spec)}
        )

    # ---- scaffold: the rendered files, written by the DEVICE under its native root -------
    scaffolded = device.run(
        capability="project.scaffold",
        payload={
            "project_id": project_id,
            "slug": project_slug,
            "root": "native",
            "files": scaffold_files,
            "manifest": device_manifest,
        },
        idempotency_key=f"nativefactory-scaffold:{row.id}",
        timeout_s=SCAFFOLD_TIMEOUT_S,
    )
    if not scaffolded.ok:
        return _fail(db, row, scaffolded, step="project.scaffold")
    root_path = str(scaffolded.result.get("root_path") or "")
    row.project_path = root_path

    # ---- build -------------------------------------------------------------------------
    _touch(db, row, STATE_BUILDING)
    built = device.run(
        capability="project.run",
        payload={
            "project_id": project_id,
            "command_key": ANDROID_RUN_KEY[row.target] if is_android else "build",
        },
        idempotency_key=f"nativefactory-build:{row.id}",
        timeout_s=RUN_TIMEOUT_S,
    )
    if not built.ok:
        return _fail(db, row, built, step="project.run build")
    if built.result.get("exit_code") not in (0, None):
        # A batch run answers its exit code rather than failing (§6n): a compile that failed
        # is a failed row in the compiler's own words, never a step toward "verified".
        message = _tail(str(built.result.get("log_tail") or ""), 1000)
        _touch(db, row, STATE_FAILED, error_class="build_failed", error_message=message)
        return DeviceBuildOutcome(ok=False, error_class="build_failed", message=message)

    # ---- test: the project's OWN tests, run by the device -------------------------------
    _touch(db, row, STATE_TESTING)
    tested = device.run(
        capability="project.test",
        payload={"project_id": project_id},
        idempotency_key=f"nativefactory-test:{row.id}",
        timeout_s=TEST_TIMEOUT_S,
    )
    if not tested.ok:
        return _fail(db, row, tested, step="project.test")
    # What the DEVICE said about its own test run, recorded before anything is decided from
    # it. The device answers `counts_parsed` precisely because it knows when it could not read
    # its runner's output - and until 2026-09-12 nothing here read that field, so a run whose
    # counts were unknown was stamped `verified` with `passed: null, failed: null`. A verdict
    # about tests nobody could count is not a verdict (B03 req 467).
    exit_code = tested.result.get("exit_code")
    counts_parsed = bool(tested.result.get("counts_parsed"))
    row.tests_json = {
        "exit_code": exit_code,
        "passed": tested.result.get("passed"),
        "failed": tested.result.get("failed"),
        "counts_parsed": counts_parsed,
        "duration_ms": tested.result.get("duration_ms"),
        "report_tail": _tail(str(tested.result.get("report_tail") or ""), 1000),
    }
    if exit_code not in (0, None):
        message = _tail(str(tested.result.get("report_tail") or ""), 1000)
        _touch(db, row, STATE_FAILED, error_class="tests_failed", error_message=message)
        return DeviceBuildOutcome(ok=False, error_class="tests_failed", message=message)
    if exit_code is None or not counts_parsed:
        # The tests ran and the device could not say how they went. "Nothing failed" is not
        # the same claim as "nothing was counted", and only one of them is true here.
        reason = (
            "the device could not read the test counts"
            if exit_code is not None
            else "the device could not read the test runner's exit code"
        )
        message = f"{reason}; this build is not verified. " + _tail(
            str(tested.result.get("report_tail") or ""), 800
        )
        _touch(db, row, STATE_FAILED, error_class="tests_unreadable", error_message=message)
        return DeviceBuildOutcome(ok=False, error_class="tests_unreadable", message=message)

    if is_android:
        return _read_back_android(db, row, device, spec, manifest, root_path)

    # ---- publish: the artefact itself ---------------------------------------------------
    published = device.run(
        capability="project.run",
        payload={"project_id": project_id, "command_key": "publish"},
        idempotency_key=f"nativefactory-publish:{row.id}",
        timeout_s=RUN_TIMEOUT_S,
    )
    if not published.ok:
        return _fail(db, row, published, step="project.run publish")

    # ---- read the artefact back, with the DEVICE's own eyes ------------------------------
    # Not "the publish exited 0, so an EXE exists" - that is the assumption this whole
    # milestone exists to refuse. The device is asked what is actually on its disk.
    artifact_path = f"{root_path}\\{publish_dir}\\{project_slug}.exe"
    inspected = device.run(
        capability="file.inspect",
        payload={"path": artifact_path},
        idempotency_key=f"nativefactory-inspect:{row.id}",
        timeout_s=INSPECT_TIMEOUT_S,
    )
    if not inspected.ok:
        return _fail(db, row, inspected, step="file.inspect")

    # `file.inspect` nests the file record under "file" (FileIdentity.ToJson: file_id, path,
    # name, extension, size, mtime, sha256). Reading `size_bytes`/`sha256` off the TOP of the
    # result -- which this did until 2026-09-11 -- produced None for both, and the row was
    # still stamped `verified`. A verdict with literally nothing behind it.
    record = inspected.result.get("file")
    record = record if isinstance(record, dict) else {}
    artifact: dict[str, Any] = {
        "size_bytes": record.get("size"),
        "sha256": record.get("sha256"),
        "kind": inspected.result.get("kind"),
        "read_back_by": "device file.inspect",
    }
    # Built once, at the test step, and carried from there: a second construction here used
    # to drop `counts_parsed` again on its way into the row (B03 req 467).
    tests = row.tests_json or {}

    # The state this run has EARNED, and no more.
    #
    # `validate_against_spec` calls its version check "the point of the whole module": an
    # artefact that cannot prove which build it is cannot be trusted to be the build that was
    # just made. So this path may reach `verified` only by running that check, on facts the
    # DEVICE read out of the file -- never by inferring it from an exit code, and never by
    # borrowing a word the local path earns by reading the artefact itself.
    #
    # `file.inspect` answers the PE block when the file really is one (PeImageReader, M28
    # row 26.16); a device older than that answers none, and a build read back by such a
    # device is `unverified` BY CONSTRUCTION rather than quietly passing.
    pe = inspected.result.get("pe")
    pe = pe if isinstance(pe, dict) else None

    missing: list[str] = []
    if artifact["size_bytes"] is None:
        missing.append("size")
    if not artifact["sha256"]:
        missing.append("sha256")

    if missing or pe is None:
        reason = (
            "the device reported the file, not its identity: file.inspect returned no PE "
            "block, so app.nativefactory.artifacts.validate_against_spec could not run. An "
            "agent older than M28 row 26.16 cannot report a PE's own version, and a build it "
            "read back is unverified by construction."
        )
        if missing:
            reason = (
                "the device's file.inspect answered nothing usable about the artefact ("
                + ", ".join(missing)
                + "); "
                + reason
            )
        row.artifact_path = artifact_path
        row.artifact_json = artifact
        row.verdict_json = {
            "ok": False,
            "checked_by": "device file.inspect",
            "not_checked": ["version", "subsystem"],
            "reason": reason,
        }
        row.tests_json = tests
        row.updated_at = datetime.now(UTC)
        _touch(db, row, STATE_UNVERIFIED)
        return DeviceBuildOutcome(
            ok=True,
            root_path=root_path,
            artifact_path=artifact_path,
            artifact=artifact,
            tests=tests,
        )

    # The device read the file. Cloud Core now compares what it read with what was ASKED,
    # through the SAME function the local path uses -- one judge, two sources of facts.
    facts = ArtifactFacts(
        path=artifact_path,
        kind="pe",
        size_bytes=int(artifact["size_bytes"]),
        sha256=str(artifact["sha256"]),
        version=_as_text(pe.get("version")),
        architecture=_as_text(pe.get("architecture")),
        subsystem=_as_text(pe.get("subsystem")),
        detail={"read_back_by": "device file.inspect"},
    )
    outcome_verdict = validate_against_spec(facts, spec)
    artifact.update({k: v for k, v in facts.as_dict().items() if k not in artifact})

    row.artifact_path = artifact_path
    row.artifact_json = artifact
    row.verdict_json = outcome_verdict.as_dict()
    row.tests_json = tests
    row.updated_at = datetime.now(UTC)
    _touch(db, row, STATE_VERIFIED if outcome_verdict.ok else STATE_MISMATCH)

    # ---- B33 req 456/457: the package, made by the device from the EXE it just read back --
    if row.target in (TARGET_WINDOWS_PORTABLE, TARGET_WINDOWS_MSIX):
        kind = "msix" if row.target == TARGET_WINDOWS_MSIX else "portable"
        packed = device.run(
            capability="project.package",
            payload={"project_id": project_id, "kind": kind},
            idempotency_key=f"nativefactory-package:{row.id}:{kind}",
            timeout_s=PACKAGE_TIMEOUT_S,
        )
        if not packed.ok:
            return _fail(db, row, packed, step=f"project.package {kind}")
        package = dict(packed.result or {})
        if not package.get("path") or not package.get("sha256"):
            _touch(
                db,
                row,
                STATE_FAILED,
                error_class="package_unreadable",
                error_message="the device answered no path or hash for the package",
            )
            return DeviceBuildOutcome(
                ok=False, error_class="package_unreadable", message="the device answered no package"
            )
        row.artifact_path = str(package["path"])
        row.artifact_json = {
            **artifact,
            "package": {
                "kind": kind,
                "path": package["path"],
                "bytes": package.get("bytes"),
                "sha256": package["sha256"],
                "signed": package.get("signed") is True,
                "executable": artifact_path,
            },
        }
        row.updated_at = datetime.now(UTC)
        db.commit()
        artifact_path = row.artifact_path
        artifact = row.artifact_json

    return DeviceBuildOutcome(
        ok=True,
        root_path=root_path,
        artifact_path=artifact_path,
        artifact=artifact,
        tests=tests,
    )


def _read_back_android(
    db: Session,
    row: NativeBuildRow,
    device: DeviceActionPort,
    spec: NativeAppSpec,
    manifest: dict[str, Any],
    root_path: str,
) -> DeviceBuildOutcome:
    """B49 (ADR-0161): the APK or bundle the build made, read back by the DEVICE.

    The path is the one the template declares (``artifact`` / ``bundle``), under the folder
    the device said it scaffolded. The device's ``file.inspect`` answers an ``android`` block
    only when the package's own manifest reads (AndroidPackageReader); without it the row is
    `unverified` by construction, exactly as a PE with no version block is.
    """
    relative = str(manifest["bundle" if row.target == TARGET_ANDROID_AAB else "artifact"])
    artifact_path = root_path + "\\" + relative.replace("/", "\\")
    inspected = device.run(
        capability="file.inspect",
        payload={"path": artifact_path},
        idempotency_key=f"nativefactory-inspect:{row.id}",
        timeout_s=INSPECT_TIMEOUT_S,
    )
    if not inspected.ok:
        return _fail(db, row, inspected, step="file.inspect")

    record = inspected.result.get("file")
    record = record if isinstance(record, dict) else {}
    artifact: dict[str, Any] = {
        "size_bytes": record.get("size"),
        "sha256": record.get("sha256"),
        "kind": inspected.result.get("kind"),
        "read_back_by": "device file.inspect",
    }
    tests = row.tests_json or {}
    android = inspected.result.get("android")
    android = android if isinstance(android, dict) else None

    if artifact["size_bytes"] is None or not artifact["sha256"] or android is None:
        row.artifact_path = artifact_path
        row.artifact_json = artifact
        row.verdict_json = {
            "ok": False,
            "checked_by": "device file.inspect",
            "not_checked": ["package", "version", "version_code"],
            "reason": (
                "the device reported the file, not the package's identity: file.inspect "
                "returned no android block, so validate_against_spec could not run. An agent "
                "older than ADR-0161 cannot read an APK's manifest, and a build it read back "
                "is unverified by construction."
            ),
        }
        row.tests_json = tests
        row.updated_at = datetime.now(UTC)
        _touch(db, row, STATE_UNVERIFIED)
        return DeviceBuildOutcome(
            ok=True,
            root_path=root_path,
            artifact_path=artifact_path,
            artifact=artifact,
            tests=tests,
        )

    version_code = android.get("version_code")
    facts = ArtifactFacts(
        path=artifact_path,
        kind=str(android.get("format") or ("aab" if row.target == TARGET_ANDROID_AAB else "apk")),
        size_bytes=int(artifact["size_bytes"]),
        sha256=str(artifact["sha256"]),
        version=_as_text(android.get("version_name")),
        identity=_as_text(android.get("package")),
        detail={
            "read_back_by": "device file.inspect",
            "version_code": str(version_code) if isinstance(version_code, int) else "",
            "min_sdk": str(android.get("min_sdk") or ""),
            "target_sdk": str(android.get("target_sdk") or ""),
            "signed": "true" if android.get("signed") is True else "false",
        },
    )
    verdict = validate_against_spec(facts, spec)
    artifact.update({k: v for k, v in facts.as_dict().items() if k not in artifact})
    artifact["signed"] = android.get("signed") is True

    row.artifact_path = artifact_path
    row.artifact_json = artifact
    row.verdict_json = verdict.as_dict()
    row.tests_json = tests
    row.updated_at = datetime.now(UTC)
    _touch(db, row, STATE_VERIFIED if verdict.ok else STATE_MISMATCH)
    return DeviceBuildOutcome(
        ok=True, root_path=root_path, artifact_path=artifact_path, artifact=artifact, tests=tests
    )
