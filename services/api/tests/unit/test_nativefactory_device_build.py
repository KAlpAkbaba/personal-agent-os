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
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.nativefactory.device_build import (
    ANDROID_MANIFEST_EXAMPLE_ENTRY,
    REQUIRED_CAPABILITIES,
    android_manifest,
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
from app.nativefactory.packaging import UNSIGNED_PUBLISHER
from app.nativefactory.service import plan_build
from app.nativefactory.signing import (
    MODE_OWNER_CERTIFICATE,
    MODE_UNSIGNED,
    TEST_SIGNING_SUBJECT,
    SigningPolicy,
)
from app.nativefactory.stacks import ToolchainFacts
from app.routines.dispatch import DeviceRunResult
from tests.voice_corpus.harness import NATIVE_ANDROID_SPEC

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
        # The shape the REAL device returns: ProjectCapabilities.TestAsync always answers
        # `counts_parsed` - it is how the device says whether it could read its runner's
        # output at all. The fake omitted it, so nothing here could see that the Cloud Core
        # ignored it and stamped `verified` with `passed: null` (B03 req 467, and the same
        # class of gap the `file` wrapper comment below records).
        project_test=DeviceRunResult(
            True,
            result={
                "exit_code": 0,
                "passed": 3,
                "failed": 0,
                "counts_parsed": True,
                "duration_ms": 120,
                "report_tail": "Passed!  - Failed: 0, Passed: 3",
            },
        ),
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
    # The whole thing the device said about its test run, `counts_parsed` included - that
    # field is what decides whether a build may be called verified at all (B03 req 467).
    assert row.tests_json["exit_code"] == 0
    assert row.tests_json["passed"] == 3
    assert row.tests_json["failed"] == 0
    assert row.tests_json["counts_parsed"] is True


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


def test_a_test_run_whose_counts_could_not_be_read_is_not_a_pass(db):
    """B03 req 467. The device says `counts_parsed: false` precisely when it could not read
    its runner's output. Nothing here read that field, so production's row 26.16 was stamped
    `verified` carrying `passed: null, failed: null` - "nothing failed" and "nothing was
    counted" recorded as the same thing. Only one of them was true."""
    device = _healthy()
    device._answers["project_test"] = DeviceRunResult(
        True,
        result={
            "exit_code": 0,
            "passed": None,
            "failed": None,
            "counts_parsed": False,
            "report_tail": "Test run for tests.dll (.NETCoreApp,Version=v10.0)",
        },
    )
    row = _row(db)

    outcome = build_on_device(db, row, device)

    assert outcome.ok is False
    assert outcome.error_class == "tests_unreadable"
    assert row.state == STATE_FAILED
    assert "could not read the test counts" in row.error_message
    assert row.tests_json["counts_parsed"] is False
    # And it never went on to publish something it could not vouch for.
    assert [c for c, _ in device.calls] == ["project.scaffold", "project.run", "project.test"]


def test_a_test_run_with_no_exit_code_is_not_a_pass_either(db):
    """`exit_code: None` used to fall through the `not in (0, None)` check as success."""
    device = _healthy()
    device._answers["project_test"] = DeviceRunResult(
        True,
        result={"exit_code": None, "passed": 3, "failed": 0, "counts_parsed": True},
    )
    row = _row(db)

    outcome = build_on_device(db, row, device)

    assert outcome.ok is False
    assert outcome.error_class == "tests_unreadable"
    assert "exit code" in row.error_message


def test_a_healthy_test_run_records_the_counts_the_device_reported(db):
    """The counts are kept, not just consulted: a verified build can say what its tests did."""
    device = _healthy()
    row = _row(db)

    build_on_device(db, row, device)

    assert row.tests_json["passed"] == 3
    assert row.tests_json["failed"] == 0
    assert row.tests_json["counts_parsed"] is True


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
    """Defence in depth for the planner's rule: a target outside DEVICE_BUILDABLE_TARGETS is
    refused before the device is asked. Every target the spec knows is buildable there now
    (B33 moved the packages in, ADR-0161 the Android pair), so the set is widened on purpose
    here to prove the guard itself still stands."""
    import app.nativefactory.device_build as device_build

    apk = plan_build(db, NATIVE_ANDROID_SPEC, facts=FULL, on_device=True)[0]
    device = _healthy()
    original = device_build.DEVICE_BUILDABLE_TARGETS
    device_build.DEVICE_BUILDABLE_TARGETS = frozenset({"windows_exe"})
    try:
        outcome = build_on_device(db, apk, device)
    finally:
        device_build.DEVICE_BUILDABLE_TARGETS = original

    assert not outcome.ok
    assert outcome.error_class == "dependency_unavailable"
    assert apk.state == STATE_UNAVAILABLE
    assert device.calls == []


# ------------------------------------------------------------------ Android (ADR-0161)

ANDROID_ROOT = r"C:\Users\alpak\Documents\PagentOS Projects\native\sayac"


def _android_device(*, android: dict[str, Any] | None = None, build_exit: int = 0) -> FakeDevice:
    block = {
        "format": "apk",
        "package": "com.pagentos.sayac",
        "version_code": 1004003,
        "version_name": "1.4.2",
        "min_sdk": 23,
        "target_sdk": 33,
        "signed": True,
        "signature_schemes": ["v2+"],
    }
    if android is not None:
        block.update(android)
    inspect: dict[str, Any] = {"file": {"size": 800957, "sha256": "c" * 64}, "kind": "unknown"}
    if android != {}:
        inspect["android"] = block
    return FakeDevice(
        project_scaffold=DeviceRunResult(True, result={"root_path": ANDROID_ROOT}),
        project_run_build=DeviceRunResult(
            True, result={"exit_code": build_exit, "log_tail": "e: Unresolved reference: Countr"}
        ),
        project_run_bundle=DeviceRunResult(True, result={"exit_code": 0}),
        project_test=DeviceRunResult(
            True, result={"exit_code": 0, "passed": 2, "failed": 0, "counts_parsed": True}
        ),
        file_inspect=DeviceRunResult(True, result=inspect),
    )


def _android_row(db, target: str = "android_apk"):
    spec = {**NATIVE_ANDROID_SPEC, "targets": [target], "version": "1.4.2", "name": "Sayac"}
    return plan_build(db, spec, facts=FULL, on_device=True)[0]


def test_an_android_row_is_scaffolded_with_the_three_gradle_shapes_and_nothing_else(db):
    row = _android_row(db)
    device = _android_device()
    outcome = build_on_device(db, row, device)

    assert outcome.ok, outcome.message
    manifest = device.payload_for("project.scaffold")["manifest"]
    assert manifest == android_manifest("app/build.gradle.kts")
    assert manifest["run"] == {
        "build": "gradle --no-daemon --console=plain assembleDebug",
        "bundle": "gradle --no-daemon --console=plain bundleRelease",
    }
    assert manifest["test"] == {"unit": "gradle --no-daemon --console=plain test"}
    # scaffold, build, test, read back - and no publish: an APK is the build's own output.
    assert [c for c, _ in device.calls] == [
        "project.scaffold",
        "project.run",
        "project.test",
        "file.inspect",
    ]
    assert device.payload_for("file.inspect")["path"] == (
        ANDROID_ROOT + "\\app\\build\\outputs\\apk\\debug\\app-debug.apk"
    )


def test_an_apk_the_device_read_back_with_the_right_identity_is_verified(db):
    row = _android_row(db)
    build_on_device(db, row, _android_device())
    assert row.state == STATE_VERIFIED
    assert row.verdict_json["ok"] is True
    assert row.artifact_json["identity"] == "com.pagentos.sayac"
    assert row.artifact_json["signed"] is True
    assert row.tests_json["counts_parsed"] is True


def test_a_bundle_row_runs_bundle_release_and_reads_the_bundle(db):
    row = _android_row(db, "android_aab")
    device = _android_device(android={"format": "aab", "signed": False, "signature_schemes": []})
    build_on_device(db, row, device)
    assert [p.get("command_key") for c, p in device.calls if c == "project.run"] == ["bundle"]
    assert device.payload_for("file.inspect")["path"].endswith(
        "\\app\\build\\outputs\\bundle\\release\\app-release.aab"
    )
    assert row.state == STATE_VERIFIED
    assert row.artifact_json["signed"] is False


@pytest.mark.parametrize(
    ("android", "names"),
    [
        ({"version_code": 1004002}, "versionCode"),
        ({"package": "com.pagentos.other"}, "package"),
        ({"version_name": "1.4.1"}, "version"),
    ],
    ids=["yesterdays-code", "another-app", "another-name"],
)
def test_a_package_that_is_another_build_is_a_mismatch_not_a_pass(db, android, names):
    row = _android_row(db)
    build_on_device(db, row, _android_device(android=android))
    assert row.state == STATE_MISMATCH
    assert any(names in m for m in row.verdict_json["mismatches"]), row.verdict_json


def test_an_agent_that_cannot_read_an_apk_leaves_the_build_unverified(db):
    row = _android_row(db)
    build_on_device(db, row, _android_device(android={}))
    assert row.state == STATE_UNVERIFIED
    assert "android block" in row.verdict_json["reason"]


def test_a_compile_that_failed_is_a_failed_row_and_the_tests_never_run(db):
    row = _android_row(db)
    device = _android_device(build_exit=1)
    outcome = build_on_device(db, row, device)
    assert not outcome.ok
    assert row.state == STATE_FAILED
    assert row.error_class == "build_failed"
    assert "Unresolved reference" in (row.error_message or "")
    assert "project.test" not in [c for c, _ in device.calls]


def test_a_device_build_gets_the_devices_own_ceiling_not_thirty_seconds():
    """The device runs a command for the time left until it expires (clamped to its cap), so
    the timeout sent here IS the build's budget there. It was 30 s for a compile until
    2026-09-16 (ADR-0161), which ended every device build longer than that."""
    from app.nativefactory import device_build
    from app.nativefactory.service import BUILD_TIMEOUT_S

    assert device_build.RUN_TIMEOUT_S == BUILD_TIMEOUT_S + 30
    assert device_build.TEST_TIMEOUT_S == BUILD_TIMEOUT_S + 30
    # The device's cap for project.run / project.test, spelled in the protocol document.
    protocol = (
        Path(__file__).resolve().parents[4] / "packages" / "protocol" / "DEVICE_PROTOCOL.md"
    ).read_text(encoding="utf-8")
    assert "capped at 20 min 30 s" in protocol


def test_the_android_manifest_the_device_half_reads_is_the_one_this_module_writes():
    import json

    shared = (
        Path(__file__).resolve().parents[4]
        / "packages"
        / "protocol"
        / "android-manifest.example.json"
    )
    doc = json.loads(shared.read_text(encoding="utf-8"))
    assert doc["entry"] == ANDROID_MANIFEST_EXAMPLE_ENTRY
    assert doc["manifest"] == android_manifest(ANDROID_MANIFEST_EXAMPLE_ENTRY)
    protocol = (
        Path(__file__).resolve().parents[4] / "packages" / "protocol" / "DEVICE_PROTOCOL.md"
    ).read_text(encoding="utf-8")
    for command in (*doc["manifest"]["run"].values(), *doc["manifest"]["test"].values()):
        assert f"`{command}`" in protocol, command


def _packaged(kind: str, **signature: Any) -> FakeDevice:
    device = _healthy()
    device._answers["project_package"] = DeviceRunResult(
        True,
        result={
            "kind": kind,
            "path": ROOT
            + chr(92)
            + "dist"
            + chr(92)
            + ("notlarim-portable.zip" if kind == "portable" else "notlarim.msix"),
            "name": "notlarim-portable.zip" if kind == "portable" else "notlarim.msix",
            "bytes": 90112,
            "sha256": "b" * 64,
            "signed": False,
            "observed": {"exists": True, "bytes": 90112},
            **signature,
        },
    )
    return device


def test_an_msix_row_is_built_then_packaged_by_the_device_and_the_manifest_is_scaffolded(db):
    """B33 req 457: the device packs what the Cloud Core declared - AppxManifest.xml is
    scaffolded beside the sources, project.package runs AFTER the EXE was read back and
    judged, and the row's artefact becomes the package (the EXE kept under it)."""
    msix = plan_build(db, {**WINDOWS, "targets": ["windows_msix"]}, facts=FULL)[0]
    device = _packaged("msix")

    outcome = build_on_device(db, msix, device)

    assert outcome.ok, outcome.message
    assert [c for c, _ in device.calls][-1] == "project.package"
    # B33 req 473: the default policy (the owner's decision) asks the device to sign.
    assert device.payload_for("project.package") == {
        "project_id": f"native-{str(msix.id)[:8]}",
        "kind": "msix",
        "signing_mode": "test_certificate",
    }
    files = {f["path"]: f["text"] for f in device.payload_for("project.scaffold")["files"]}
    assert "staging/AppxManifest.xml" in files
    assert "<Identity" in files["staging/AppxManifest.xml"]
    assert 'Executable="notlarim.exe"' in files["staging/AppxManifest.xml"]
    assert f'Publisher="{TEST_SIGNING_SUBJECT}"' in files["staging/AppxManifest.xml"]
    assert msix.state == STATE_VERIFIED
    assert msix.artifact_path.endswith("notlarim.msix")
    assert msix.artifact_json["package"]["sha256"] == "b" * 64
    # The fake device answered signed:false, so the row says unsigned - never the intention.
    assert msix.artifact_json["package"]["signed"] is False
    assert msix.artifact_json["package"]["signing_mode"] == "unsigned"
    assert "signer_thumbprint" not in msix.artifact_json["package"]
    assert msix.artifact_json["package"]["executable"].endswith("notlarim.exe")


def test_a_signed_package_carries_the_signer_and_trust_the_device_read_back(db):
    """B33 req 473: signer and trust are copied from the device's answer, and only when it
    said signed; a thumbprint beside signed:false never reaches the row."""
    msix = plan_build(db, {**WINDOWS, "targets": ["windows_msix"]}, facts=FULL)[0]
    device = _packaged(
        "msix",
        signed=True,
        signing_mode="test_certificate",
        signer_thumbprint="A" * 40,
        signer_subject=TEST_SIGNING_SUBJECT,
        signer_not_after="2028-09-15T00:00:00Z",
        trusted=False,
        trust_step="scripts\\trust-native-signing-cert.ps1",
    )

    assert build_on_device(db, msix, device).ok

    package = msix.artifact_json["package"]
    assert package["signed"] is True
    assert package["signing_mode"] == "test_certificate"
    assert package["signer_thumbprint"] == "A" * 40
    assert package["signer_subject"] == TEST_SIGNING_SUBJECT
    assert package["trusted"] is False

    lying = plan_build(
        db, {**WINDOWS, "targets": ["windows_msix"], "version": "0.1.1"}, facts=FULL
    )[0]
    device = _packaged("msix", signed="yes", signer_thumbprint="B" * 40, trusted=True)
    assert build_on_device(db, lying, device).ok
    assert lying.artifact_json["package"]["signed"] is False
    assert "signer_thumbprint" not in lying.artifact_json["package"]
    assert "trusted" not in lying.artifact_json["package"]


def test_an_unsigned_policy_scaffolds_the_unsigned_publisher_and_asks_for_nothing(db):
    msix = plan_build(db, {**WINDOWS, "targets": ["windows_msix"]}, facts=FULL)[0]
    device = _packaged("msix")

    assert build_on_device(db, msix, device, signing=SigningPolicy(MODE_UNSIGNED)).ok

    assert device.payload_for("project.package")["signing_mode"] == "unsigned"
    files = {f["path"]: f["text"] for f in device.payload_for("project.scaffold")["files"]}
    assert f'Publisher="{UNSIGNED_PUBLISHER}"' in files["staging/AppxManifest.xml"]

    # owner_certificate is never forwarded as anything the device would apply.
    other = plan_build(
        db, {**WINDOWS, "targets": ["windows_msix"], "version": "0.2.0"}, facts=FULL
    )[0]
    device = _packaged("msix")
    assert build_on_device(db, other, device, signing=SigningPolicy(MODE_OWNER_CERTIFICATE)).ok
    assert device.payload_for("project.package")["signing_mode"] == "unsigned"


def test_a_portable_row_whose_package_the_device_could_not_make_is_failed_not_verified(db):
    """B33 req 456: a verified EXE with no package is not a verified PORTABLE row."""
    portable = plan_build(db, {**WINDOWS, "targets": ["windows_portable"]}, facts=FULL)[0]
    device = _healthy()
    device._answers["project_package"] = DeviceRunResult(False, "io_error", "disk full")

    outcome = build_on_device(db, portable, device)

    assert not outcome.ok
    assert portable.state == STATE_FAILED
    assert "project.package portable" in (portable.error_message or "")
    files = {f["path"] for f in device.payload_for("project.scaffold")["files"]}
    assert "staging/AppxManifest.xml" not in files


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
