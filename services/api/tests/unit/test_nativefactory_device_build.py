"""The build that happens on the DEVICE, and the four things it is allowed to ask for.

M28 row 26.16. Production is a Linux Cloud Core with no .NET SDK, no ``makeappx`` and no
Windows; the machine that has all three is the owner's enrolled device. So the Cloud Core does
not compile — it scaffolds, asks, and reads back.

What a fake device can prove, and a real one cannot prove cheaply, is the shape of the ask and
the whole refusal matrix. Every command this module sends must be one the device is willing to
admit (DEVICE_PROTOCOL.md §6n matches them token for token, at scaffold time, before a process
exists), and every refusal must reach the row in the device's OWN words rather than as a
category. The real end-to-end run against the owner's device is Stage 28's job; this suite is
what makes that run worth attempting.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.nativefactory.device_build import (
    REQUIRED_CAPABILITIES,
    build_on_device,
    native_manifest,
)
from app.nativefactory.models import (
    STATE_FAILED,
    STATE_MISMATCH,
    STATE_UNAVAILABLE,
    STATE_UNVERIFIED,
    STATE_VERIFIED,
    NativeBuildRow,
)
from app.nativefactory.service import plan_build
from app.nativefactory.stacks import ToolchainFacts
from app.routines.dispatch import DeviceRunResult

FULL = ToolchainFacts(
    dotnet=r"C:\dotnet.exe",
    dotnet_sdk="10.0.400",
    makeappx=r"C:\makeappx.exe",
    signtool=r"C:\signtool.exe",
    java=r"C:\java.exe",
    java_home=r"C:\Java",
    android_sdk=r"C:\Sdk",
    aapt2=r"C:\aapt2.exe",
    macos=False,
)

WINDOWS = {
    "name": "Notlarim",
    "title": "Notlarım",
    "template": "notes-desktop",
    "targets": ["windows_exe"],
    "version": "0.1.0",
    "persistence": "local_file",
    "features": ["add_item", "list_items", "delete_item", "persist_local"],
}

ROOT = r"C:\Users\alpak\Documents\PagentOS Projects\native\notlarim"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    NativeBuildRow.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


class FakeDevice:
    """A device that answers each capability from a script, and records what it was asked."""

    def __init__(self, **answers: DeviceRunResult) -> None:
        #: capability -> answer, or capability+command_key for the two project.run forms
        self._answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(
        self, *, capability: str, payload: dict[str, Any], idempotency_key: str, timeout_s: float
    ) -> DeviceRunResult:
        self.calls.append((capability, payload))
        key = capability.replace(".", "_")
        command_key = payload.get("command_key")
        if command_key:
            key = f"{key}_{command_key}"
        return self._answers.get(key, DeviceRunResult(True, result={}))

    def payload_for(self, capability: str) -> dict[str, Any]:
        return next(p for c, p in self.calls if c == capability)


def _healthy() -> FakeDevice:
    return FakeDevice(
        project_scaffold=DeviceRunResult(True, result={"root_path": ROOT, "files_written": 9}),
        project_run_build=DeviceRunResult(True, result={"command_key": "build"}),
        project_test=DeviceRunResult(True, result={"exit_code": 0, "passed": 3, "failed": 0}),
        project_run_publish=DeviceRunResult(True, result={"command_key": "publish"}),
        file_inspect=DeviceRunResult(
            # The shape the REAL device returns: DocumentCapabilities.Inspect wraps
            # FileIdentity.ToJson under "file" and names the size `size`. The fake used to
            # answer {"size_bytes": …, "sha256": …} at the top level, which the device has
            # never produced -- so this test proved the reader against a shape that does not
            # exist, and the reader was reading None for both while stamping `verified`.
            True,
            result={
                "file": {
                    "file_id": "f1",
                    "path": ROOT + chr(92) + "out" + chr(92) + "notlarim.exe",
                    "name": "notlarim.exe",
                    "extension": ".exe",
                    "size": 162304,
                    "mtime": "2026-09-09T19:07:00.0000000Z",
                    "sha256": "a" * 64,
                },
                "kind": "unknown",
                "is_text": False,
                # M28 row 26.16: the PE block PeImageReader adds when the bytes really are a
                # PE. The names are ArtifactFacts', because Cloud Core builds that out of it.
                "pe": {
                    "version": "0.1.0",
                    "architecture": "x64",
                    "subsystem": "windows_gui",
                },
            },
        ),
    )


def _row(db):
    return plan_build(db, WINDOWS, facts=FULL)[0]


# ------------------------------------------------------------------ the ask itself


def test_the_manifest_carries_only_forms_the_device_admits():
    """DEVICE_PROTOCOL.md §6n admits four argv shapes under the native root and nothing else.

    The device matches them TOKEN FOR TOKEN at scaffold time, so a manifest is a promise about
    what may run and the promise has to be one the device will keep. These are the two dotnet
    forms this path needs, spelled exactly.
    """
    manifest = native_manifest("src/notlarim/notlarim.csproj", "out")

    assert manifest["run"]["build"] == "dotnet build src/notlarim/notlarim.csproj -c Release"
    assert manifest["run"]["publish"] == (
        "dotnet publish src/notlarim/notlarim.csproj -c Release -r win-x64 "
        "--self-contained true -o out"
    )
    # An object of {key: command}, like run: a bare string is what the device refused at the
    # first device step of the first real production build (2026-09-12).
    assert manifest["test"] == {"unit": "dotnet test src/notlarim/notlarim.csproj -c Release"}
    # Nothing binds a port: every one of these is a batch run (§6n).
    assert manifest["port"] == 0
    # And no web runtime, which the native root refuses by name.
    rendered = " ".join([*manifest["run"].values(), *manifest["test"].values()])
    for refused in ("python", "node", "npm ", "makeappx"):
        assert refused not in rendered


def test_the_scaffold_names_the_native_root_and_carries_the_rendered_files(db):
    device = _healthy()

    outcome = build_on_device(db, _row(db), device)

    assert outcome.ok, outcome.message
    scaffold = device.payload_for("project.scaffold")
    # The CLOSED vocabulary of three words, never a path.
    assert scaffold["root"] == "native"
    assert scaffold["slug"] == "notlarim"
    paths = {f["path"] for f in scaffold["files"]}
    assert "manifest.json" in paths
    assert any(p.endswith(".csproj") for p in paths)
    # Text only: the device refuses every launcher extension, and a rendered tree is source.
    assert all(isinstance(f["text"], str) for f in scaffold["files"])


def test_the_whole_order_is_scaffold_build_test_publish_then_read_back(db):
    device = _healthy()

    build_on_device(db, _row(db), device)

    assert [c for c, _ in device.calls] == [
        "project.scaffold",
        "project.run",
        "project.test",
        "project.run",
        "file.inspect",
    ]
    keys = [p.get("command_key") for c, p in device.calls if c == "project.run"]
    assert keys == ["build", "publish"]


def test_the_artefact_is_read_back_by_the_device_not_inferred_from_an_exit_code(db):
    """`publish exited 0` is not `an EXE exists`. The device is asked what is on its disk."""
    device = _healthy()
    row = _row(db)

    outcome = build_on_device(db, row, device)

    # `verified` is EARNED here, not inferred: the device read the PE and Cloud Core ran the
    # same `validate_against_spec` the local path runs. One judge, two sources of facts.
    assert row.state == STATE_VERIFIED
    assert row.verdict_json["ok"] is True
    assert row.verdict_json["mismatches"] == []
    assert row.verdict_json["facts"]["version"] == "0.1.0"
    assert outcome.artifact["size_bytes"] == 162304, "read from file.size, where the device puts it"
    assert outcome.artifact["sha256"] == "a" * 64
    assert outcome.artifact["read_back_by"] == "device file.inspect"
    assert row.artifact_path == ROOT + r"\out\notlarim.exe"
    assert device.payload_for("file.inspect")["path"] == row.artifact_path
    assert row.tests_json == {"exit_code": 0, "passed": 3, "failed": 0}


def test_an_inspect_that_answers_nothing_usable_is_named_not_stamped(db):
    """The bypass, in its worst form. Before 2026-09-11 this path read `size_bytes`/`sha256`
    off the TOP of the result -- keys the device has never emitted -- so both were None and
    the row was stamped `verified` anyway: a verdict with literally nothing behind it."""
    device = _healthy()
    device._answers["file_inspect"] = DeviceRunResult(True, result={"kind": "binary"})
    row = _row(db)

    outcome = build_on_device(db, row, device)

    assert row.state == STATE_UNVERIFIED
    assert outcome.artifact["size_bytes"] is None and outcome.artifact["sha256"] is None
    assert "answered nothing usable" in row.verdict_json["reason"]
    assert "size" in row.verdict_json["reason"] and "sha256" in row.verdict_json["reason"]


def test_an_empty_artefact_is_never_a_success(db):
    """`publish exited 0` plus a zero-byte file is the failure this milestone exists to
    refuse; the local path names it through validate_against_spec and so must this one."""
    device = _healthy()
    device._answers["file_inspect"] = DeviceRunResult(
        True,
        result={
            "file": {"size": 0, "sha256": "b" * 64, "name": "notlarim.exe"},
            "kind": "unknown",
            "pe": {"version": "0.1.0", "architecture": "x64", "subsystem": "windows_gui"},
        },
    )
    row = _row(db)

    build_on_device(db, row, device)

    assert row.state == STATE_MISMATCH
    assert "artefact is empty" in row.verdict_json["mismatches"]


def test_a_build_that_produced_yesterdays_exe_is_a_mismatch_not_a_pass(db):
    """The failure this milestone exists to make impossible: a factory that hands back an
    older binary while reporting success. The version is the only thing that can catch it."""
    device = _healthy()
    device._answers["file_inspect"] = DeviceRunResult(
        True,
        result={
            "file": {"size": 162304, "sha256": "c" * 64, "name": "notlarim.exe"},
            "kind": "unknown",
            "pe": {"version": "0.0.9", "architecture": "x64", "subsystem": "windows_gui"},
        },
    )
    row = _row(db)

    build_on_device(db, row, device)

    assert row.state == STATE_MISMATCH
    assert any("0.0.9" in m for m in row.verdict_json["mismatches"])


def test_a_console_image_where_a_desktop_app_was_asked_for_is_a_mismatch(db):
    device = _healthy()
    device._answers["file_inspect"] = DeviceRunResult(
        True,
        result={
            "file": {"size": 162304, "sha256": "d" * 64, "name": "notlarim.exe"},
            "kind": "unknown",
            "pe": {"version": "0.1.0", "architecture": "x64", "subsystem": "windows_console"},
        },
    )
    row = _row(db)

    build_on_device(db, row, device)

    assert row.state == STATE_MISMATCH
    assert any("console" in m for m in row.verdict_json["mismatches"])


def test_an_agent_older_than_row_26_16_leaves_the_build_unverified(db):
    """A device that cannot report a PE's own identity cannot produce a verified build. It
    says so, in the row, rather than passing on size and hash -- which is what this path did
    until 2026-09-11."""
    device = _healthy()
    device._answers["file_inspect"] = DeviceRunResult(
        True,
        result={
            "file": {"size": 162304, "sha256": "e" * 64, "name": "notlarim.exe"},
            "kind": "unknown",
        },
    )
    row = _row(db)

    build_on_device(db, row, device)

    assert row.state == STATE_UNVERIFIED
    assert row.verdict_json["ok"] is False
    assert "no PE block" in row.verdict_json["reason"]
    assert row.verdict_json["not_checked"] == ["version", "subsystem"]


# ------------------------------------------------------------------ the refusal matrix


def test_a_refused_scaffold_reaches_the_row_in_the_devices_own_words(db):
    device = _healthy()
    device._answers["project_scaffold"] = DeviceRunResult(
        False, "permission_denied", "'notlarim' belongs to another project; nothing was written"
    )
    row = _row(db)

    outcome = build_on_device(db, row, device)

    assert not outcome.ok
    assert row.state == STATE_FAILED
    assert row.error_class == "permission_denied"
    # The sentence, kept whole: it is the most useful thing the device produces.
    assert "belongs to another project" in row.error_message
    assert "project.scaffold" in row.error_message
    # And nothing was asked of the device afterwards.
    assert [c for c, _ in device.calls] == ["project.scaffold"]


def test_a_build_that_fails_stops_before_the_tests(db):
    device = _healthy()
    device._answers["project_run_build"] = DeviceRunResult(
        False, "command_not_allowlisted", "the manifest command is not in the runtime allowlist"
    )
    row = _row(db)

    outcome = build_on_device(db, row, device)

    assert not outcome.ok
    assert row.state == STATE_FAILED
    assert row.error_class == "command_not_allowlisted"
    assert "project.test" not in [c for c, _ in device.calls]


def test_tests_that_fail_are_named_as_tests_not_as_a_build_failure(db):
    device = _healthy()
    device._answers["project_test"] = DeviceRunResult(
        True, result={"exit_code": 1, "passed": 2, "failed": 1, "report_tail": "1 failed"}
    )
    row = _row(db)

    outcome = build_on_device(db, row, device)

    assert not outcome.ok
    assert outcome.error_class == "tests_failed"
    assert row.state == STATE_FAILED
    # Nothing was published: an application whose own tests fail is not an artefact.
    assert [p.get("command_key") for c, p in device.calls if c == "project.run"] == ["build"]


def test_an_artefact_the_device_cannot_read_is_a_failure_not_a_verified_row(db):
    """The rule this whole milestone exists for: no reader, no `verified`."""
    device = _healthy()
    device._answers["file_inspect"] = DeviceRunResult(
        False, "ui_target_not_found", "'...\\out\\notlarim.exe' is not a file"
    )
    row = _row(db)

    outcome = build_on_device(db, row, device)

    assert not outcome.ok
    assert row.state == STATE_FAILED
    assert row.state != STATE_VERIFIED
    assert "is not a file" in row.error_message


# ------------------------------------------------------------------ the contract halves


def test_the_capabilities_this_path_needs_are_the_ones_it_actually_sends(db):
    """A list nobody checks drifts. This one is checked against the calls themselves."""
    device = _healthy()

    build_on_device(db, _row(db), device)

    assert set(c for c, _ in device.calls) == set(REQUIRED_CAPABILITIES)


def test_the_manifest_forms_are_the_ones_the_protocol_document_admits():
    """Both halves read each other: the shapes here, against DEVICE_PROTOCOL.md's own table.

    Restating the four forms in prose and in code is how two green suites drift apart, so the
    document is parsed rather than trusted.
    """
    from pathlib import Path

    protocol = Path(__file__).resolve().parents[4] / "packages" / "protocol" / "DEVICE_PROTOCOL.md"
    text = protocol.read_text(encoding="utf-8")
    manifest = native_manifest("<project.csproj>", "<dir>")

    for command in (
        manifest["run"]["build"],
        manifest["test"]["unit"],
        manifest["run"]["publish"],
    ):
        # The table writes each form inside backticks; compare the token sequence, since the
        # document spells the placeholders the same way this manifest does.
        assert re.search(re.escape("`" + command + "`"), text), command


def test_a_row_this_path_cannot_honestly_build_is_refused_before_the_device_is_asked(db):
    """Defence in depth for the planner's rule: whatever opened it, a portable or MSIX row
    that reaches this path is refused, because it would publish and read back an EXE and
    the judge would call that `verified`."""
    msix = plan_build(db, {**WINDOWS, "targets": ["windows_msix"]}, facts=FULL)[0]
    device = _healthy()

    outcome = build_on_device(db, msix, device)

    assert not outcome.ok
    assert outcome.error_class == "dependency_unavailable"
    assert msix.state == STATE_UNAVAILABLE
    assert device.calls == []


def test_the_manifest_the_device_half_reads_is_the_one_this_module_writes():
    """packages/protocol/native-manifest.example.json is the contract BOTH halves read: this
    test keeps it equal to native_manifest's output, and the device's own
    NativeManifestContractTests scaffolds that very file through its real parser. The first
    real production build died at the first device step - "manifest.test must be a non-empty
    object of {key: command}" - with both suites green, because each half only ever restated
    the shape to itself.
    """
    import json
    from pathlib import Path

    from app.nativefactory.device_build import (
        MANIFEST_EXAMPLE_CSPROJ,
        MANIFEST_EXAMPLE_PUBLISH_DIR,
    )

    shared = (
        Path(__file__).resolve().parents[4]
        / "packages"
        / "protocol"
        / "native-manifest.example.json"
    )
    doc = json.loads(shared.read_text(encoding="utf-8"))

    assert doc["csproj"] == MANIFEST_EXAMPLE_CSPROJ
    assert doc["publish_dir"] == MANIFEST_EXAMPLE_PUBLISH_DIR
    assert doc["manifest"] == native_manifest(MANIFEST_EXAMPLE_CSPROJ, MANIFEST_EXAMPLE_PUBLISH_DIR)
