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
from app.nativefactory.service import _tail, _touch
from app.nativefactory.spec import NativeAppSpec, NativeFactoryError
from app.nativefactory.stacks import (
    DEVICE_BUILDABLE_TARGETS,
    SPEECH_DEVICE_PACKAGING_NOT_WIRED,
)
from app.routines.dispatch import DeviceActionPort, DeviceRunResult

#: The capabilities this path needs. A device that does not advertise all of them cannot build,
#: and saying which one is missing is more useful than "unavailable".
REQUIRED_CAPABILITIES: tuple[str, ...] = (
    "project.scaffold",
    "project.run",
    "project.test",
    "file.inspect",
)

#: The device caps project.scaffold/run/status at 30 s and project.test at 5 min 30 s
#: (DEVICE_PROTOCOL.md §6l). A compile is a `project.run`, so it lives inside the 30 s cap —
#: the command RETURNS when the batch child has finished, and the companion's own job bound is
#: what limits the build, not this number.
SCAFFOLD_TIMEOUT_S = 30.0
RUN_TIMEOUT_S = 30.0
TEST_TIMEOUT_S = 330.0
INSPECT_TIMEOUT_S = 30.0


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
        "test": f"dotnet test {csproj} -c Release",
    }


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
    project_id = f"native-{str(row.id)[:8]}"

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
    csproj = str(manifest["entry"])
    publish_dir = "out"

    # ---- scaffold: the rendered files, written by the DEVICE under its native root -------
    scaffolded = device.run(
        capability="project.scaffold",
        payload={
            "project_id": project_id,
            "slug": project_slug,
            "root": "native",
            "files": [{"path": f.path, "text": f.text} for f in project.files],
            "manifest": native_manifest(csproj, publish_dir),
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
        payload={"project_id": project_id, "command_key": "build"},
        idempotency_key=f"nativefactory-build:{row.id}",
        timeout_s=RUN_TIMEOUT_S,
    )
    if not built.ok:
        return _fail(db, row, built, step="project.run build")

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
    exit_code = tested.result.get("exit_code")
    if exit_code not in (0, None):
        message = _tail(str(tested.result.get("report_tail") or ""), 1000)
        _touch(db, row, STATE_FAILED, error_class="tests_failed", error_message=message)
        return DeviceBuildOutcome(ok=False, error_class="tests_failed", message=message)

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
    tests = {
        "exit_code": exit_code,
        "passed": tested.result.get("passed"),
        "failed": tested.result.get("failed"),
    }

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
                + "); " + reason
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
    return DeviceBuildOutcome(
        ok=True,
        root_path=root_path,
        artifact_path=artifact_path,
        artifact=artifact,
        tests=tests,
    )
