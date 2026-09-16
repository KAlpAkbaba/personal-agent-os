"""B33 req 456, 462-466, 468, 469, 472: the lifecycle after the build, against a scripted
device.

Every function here is driven with a device that answers each capability from a SEQUENCE,
so the flow that reads a window three times (before, after the add, after the relaunch)
can be handed three different status lines - and the tests can show the verdict FALSE for
each read that comes back wrong, not only the happy path. Nothing below asserts a sentence
the Cloud Core composed against a sentence the Cloud Core composed: the expected values are
the fake device's answers.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.nativefactory import device_build
from app.nativefactory.device_lifecycle import (
    ARTIFACT_CHUNK_BYTES,
    ArtifactPullError,
    inspect_window,
    install_on_device,
    launch_on_device,
    package_on_device,
    project_id_for,
    pull_artifact,
    read_log_on_device,
    row_is_launchable,
    uninstall_on_device,
    verify_on_device,
)
from app.nativefactory.signing import (
    IMPLEMENTED_MODES,
    MODE_OWNER_CERTIFICATE,
    MODE_TEST_CERTIFICATE,
    MODE_UNSIGNED,
    SPEECH_UNSIGNED_MSIX,
    SPEECH_UNSIGNED_PORTABLE,
    SigningPolicy,
    policy_from_settings,
)
from app.routines.dispatch import DeviceRunResult

EXE = r"C:\Users\owner\Documents\PagentOS Projects\native\notlarim\out\notlarim.exe"


def _row(**overrides: Any) -> Any:
    base = {
        "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "slug": "notlarim",
        "display_name": "Notlarım",
        "state": "verified",
        "artifact_path": EXE,
        "attempt": 1,
        "version": "0.1.0",
    }
    return SimpleNamespace(**{**base, **overrides})


class ScriptedDevice:
    """Answers each capability from a list, in order; the last answer repeats."""

    def __init__(self, **script: list[DeviceRunResult] | DeviceRunResult) -> None:
        self._script: dict[str, list[DeviceRunResult]] = {
            k.replace("__", "."): (v if isinstance(v, list) else [v]) for k, v in script.items()
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(
        self, *, capability: str, payload: dict[str, Any], idempotency_key: str, timeout_s: float
    ) -> DeviceRunResult:
        self.calls.append((capability, dict(payload)))
        answers = self._script.get(capability)
        if not answers:
            return DeviceRunResult(False, "capability_missing", f"{capability} not scripted")
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def capabilities(self) -> list[str]:
        return [c for c, _ in self.calls]


def _tree(status: str, *, with_controls: bool = True) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    if with_controls:
        children = [
            {"automation_id": "NoteInput", "role": "edit", "value": ""},
            {"automation_id": "AddButton", "role": "button", "name": "Ekle"},
            {"role": "list", "children": [{"automation_id": "StatusText", "name": status}]},
        ]
    return {"root": {"automation_id": "MainWindow", "name": "Notlarım", "children": children}}


def _ok(**result: Any) -> DeviceRunResult:
    return DeviceRunResult(True, result=result)


# ------------------------------------------------------------------- launch (462)


def test_launch_uses_the_built_executables_absolute_path_and_reads_a_window_back() -> None:
    device = ScriptedDevice(app__launch=_ok(pid=4242, window_id="w-1", title="Notlarım"))
    outcome = launch_on_device(device, _row())
    assert outcome.ok
    assert outcome.result == {"pid": 4242, "window_id": "w-1", "title": "Notlarım"}
    assert device.calls[0] == ("app.launch", {"application": EXE})


def test_launch_with_a_pid_but_no_window_asks_window_list_and_refuses_without_one() -> None:
    device = ScriptedDevice(
        app__launch=_ok(pid=4242),
        window__list=[_ok(windows=[]), _ok(windows=[{"window_id": "w-9"}])],
    )
    first = launch_on_device(device, _row())
    assert not first.ok and first.error_class == "no_window"
    second = launch_on_device(device, _row(), attempt="2")
    assert second.ok and second.result["window_id"] == "w-9"
    assert device.capabilities() == ["app.launch", "window.list", "app.launch", "window.list"]


def test_a_packaged_row_launches_the_executable_the_package_was_made_from() -> None:
    device = ScriptedDevice(app__launch=_ok(pid=1, window_id="w"))
    zipped = _row(artifact_path=EXE.replace(r"\out\notlarim.exe", r"\dist\notlarim-portable.zip"))
    assert launch_on_device(device, zipped).ok
    assert device.calls[0][1]["application"] == EXE


def test_a_row_without_an_artefact_is_not_launchable_and_the_device_is_not_asked() -> None:
    device = ScriptedDevice()
    assert not row_is_launchable(_row(artifact_path=None))
    assert not row_is_launchable(_row(state="planned"))
    outcome = launch_on_device(device, _row(artifact_path=None))
    assert not outcome.ok and outcome.error_class == "no_artifact"
    assert device.calls == []


# ------------------------------------------------------------ inspect (463)


def test_inspect_finds_the_templates_controls_anywhere_in_the_tree() -> None:
    device = ScriptedDevice(ui__inspect=_ok(**_tree("3 not")))
    seen = inspect_window(device, _row(), "w-1", tag="t")
    assert seen.ok
    assert seen.result["controls"] == {"NoteInput": True, "AddButton": True, "StatusText": True}
    assert seen.result["status_text"] == "3 not"
    assert device.calls[0][1]["window_id"] == "w-1"


def test_inspect_of_a_foreign_window_reports_every_control_missing() -> None:
    device = ScriptedDevice(ui__inspect=_ok(**_tree("", with_controls=False)))
    seen = inspect_window(device, _row(), "w-1", tag="t")
    assert seen.ok
    assert seen.result["controls"] == {"NoteInput": False, "AddButton": False, "StatusText": False}


# ---------------------------------------------- the 26.15 flow, as code (463-466)


def _verifying_device(
    *, after_add: str = "1 not", after_relaunch: str = "1 not", log: str = "2026-09-15 app started"
) -> ScriptedDevice:
    return ScriptedDevice(
        app__launch=[_ok(pid=1, window_id="w-1"), _ok(pid=2, window_id="w-2")],
        ui__inspect=[_ok(**_tree("0 not")), _ok(**_tree(after_add)), _ok(**_tree(after_relaunch))],
        ui__set_value=_ok(),
        ui__invoke=_ok(),
        window__close=_ok(closed=True),
        file__read=_ok(text=log),
    )


def test_verify_is_true_only_when_every_read_back_answers_as_the_template_promises() -> None:
    device = _verifying_device()
    verdict = verify_on_device(device, _row())
    assert verdict["verified"] is True
    assert verdict["ui_verified"] and verdict["persisted"] and verdict["log_has_startup_line"]
    assert verdict["notes_after_add"] == 1 and verdict["notes_after_relaunch"] == 1
    assert "failed_step" not in verdict
    assert device.capabilities() == [
        "app.launch",
        "ui.inspect",
        "ui.set_value",
        "ui.invoke",
        "ui.inspect",
        "window.close",
        "app.launch",
        "ui.inspect",
        "window.close",
        "file.read",
    ]
    set_payload = device.calls[2][1]
    assert set_payload["automation_id"] == "NoteInput" and set_payload["value"]
    assert device.calls[3][1]["automation_id"] == "AddButton"
    assert device.calls[9][1]["path"].endswith(r"\out\data\app.log")


def test_verify_is_false_and_names_persistence_when_the_note_is_gone_after_relaunch() -> None:
    verdict = verify_on_device(_verifying_device(after_relaunch="0 not"), _row())
    assert verdict["verified"] is False
    assert verdict["ui_verified"] is True
    assert verdict["persisted"] is False
    assert verdict["failed_step"] == "persistence"


def test_verify_is_false_and_names_the_status_line_when_the_add_changed_nothing() -> None:
    verdict = verify_on_device(_verifying_device(after_add="0 not"), _row())
    assert verdict["verified"] is False
    assert verdict["ui_verified"] is False
    assert verdict["failed_step"] == "status_count"


def test_verify_is_false_and_names_the_log_when_it_has_no_startup_line() -> None:
    verdict = verify_on_device(_verifying_device(log="nothing here"), _row())
    assert verdict["verified"] is False
    assert verdict["log_read"] is True and verdict["log_has_startup_line"] is False
    assert verdict["failed_step"] == "file.read"


def test_verify_names_the_device_step_that_refused() -> None:
    device = _verifying_device()
    device._script["ui.invoke"] = [DeviceRunResult(False, "not_found", "no AddButton")]
    verdict = verify_on_device(device, _row())
    assert verdict["verified"] is False
    assert verdict["failed_step"] == "ui.invoke"


def test_the_log_is_read_beside_the_executable_and_a_missing_log_is_named() -> None:
    device = ScriptedDevice(file__read=DeviceRunResult(False, "not_found", "no such file"))
    outcome = read_log_on_device(device, _row())
    assert not outcome.ok and outcome.error_class == "not_found"
    assert device.calls[0][1]["path"] == EXE.rsplit("\\", 1)[0] + r"\data\app.log"


# --------------------------------------------- package / install / uninstall (456-469)


def test_package_returns_what_the_device_observed_and_refuses_an_answer_without_a_hash() -> None:
    device = ScriptedDevice(
        project__package=[
            _ok(
                kind="msix",
                path=r"C:\x\dist\notlarim.msix",
                name="notlarim.msix",
                bytes=10,
                sha256="c" * 64,
                signed=False,
            ),
            _ok(kind="msix", path=r"C:\x\dist\notlarim.msix"),
        ]
    )
    good = package_on_device(device, _row(), kind="msix")
    assert good.ok and good.result["sha256"] == "c" * 64
    assert device.calls[0] == ("project.package", {"project_id": "native-12345678", "kind": "msix"})
    bad = package_on_device(device, _row(), kind="msix")
    assert not bad.ok and bad.error_class == "package_unreadable"


def test_install_is_verified_only_by_the_shortcut_the_device_observed() -> None:
    device = ScriptedDevice(
        project__install=[
            _ok(
                installed=True,
                shortcut="x.lnk",
                observed={"shortcut_exists": True, "exe_exists": True},
            ),
            _ok(
                installed=True,
                shortcut="x.lnk",
                observed={"shortcut_exists": False, "exe_exists": True},
            ),
        ]
    )
    assert install_on_device(device, _row()).ok
    assert device.calls[0][1] == {"project_id": "native-12345678", "name": "Notlarım"}
    second = install_on_device(device, _row())
    assert not second.ok and second.error_class == "install_unverified"


def test_uninstall_is_unverified_while_the_shortcut_is_still_there() -> None:
    device = ScriptedDevice(
        project__uninstall=[
            _ok(
                uninstalled=True,
                shortcut_removed=True,
                build_kept=True,
                observed={"shortcut_exists": False},
            ),
            _ok(
                uninstalled=True,
                shortcut_removed=False,
                build_kept=True,
                observed={"shortcut_exists": True},
            ),
            DeviceRunResult(False, "not_found", "not installed by this system"),
        ]
    )
    assert uninstall_on_device(device, _row()).ok
    stuck = uninstall_on_device(device, _row())
    assert not stuck.ok and stuck.error_class == "uninstall_unverified"
    never = uninstall_on_device(device, _row())
    assert not never.ok and never.error_class == "not_found"


# ------------------------------------------------------------- artifact pull (456)


def _chunks(blob: bytes, *, sha: str | None = None) -> list[DeviceRunResult]:
    digest = sha or hashlib.sha256(blob).hexdigest()
    out: list[DeviceRunResult] = []
    for offset in range(0, len(blob), ARTIFACT_CHUNK_BYTES):
        part = blob[offset : offset + ARTIFACT_CHUNK_BYTES]
        out.append(
            _ok(
                path=EXE,
                name="notlarim.exe",
                bytes=len(blob),
                sha256=digest,
                offset=offset,
                length=len(part),
                base64=base64.b64encode(part).decode("ascii"),
                eof=offset + len(part) >= len(blob),
            )
        )
    return out


def test_pull_streams_the_artefact_in_bounded_chunks_and_verifies_the_whole() -> None:
    blob = bytes(range(256)) * 300  # 76 800 bytes: three chunks
    device = ScriptedDevice(project__artifact=_chunks(blob))
    pulled, name, sha = pull_artifact(device, _row())
    assert pulled == blob and name == "notlarim.exe"
    assert sha == hashlib.sha256(blob).hexdigest()
    assert [p["offset"] for _, p in device.calls] == [
        0,
        ARTIFACT_CHUNK_BYTES,
        2 * ARTIFACT_CHUNK_BYTES,
    ]
    assert all(p["length"] == ARTIFACT_CHUNK_BYTES for _, p in device.calls)


def test_pull_refuses_bytes_that_do_not_hash_to_what_the_device_said() -> None:
    blob = b"PE-ish bytes " * 100
    device = ScriptedDevice(project__artifact=_chunks(blob, sha="f" * 64))
    with pytest.raises(ArtifactPullError) as raised:
        pull_artifact(device, _row())
    assert raised.value.error_class == "artifact_mismatch"


def test_pull_of_a_packaged_row_names_the_package_under_dist() -> None:
    blob = b"zip"
    device = ScriptedDevice(project__artifact=_chunks(blob))
    zipped = _row(artifact_path=EXE.replace(r"\out\notlarim.exe", r"\dist\notlarim-portable.zip"))
    pull_artifact(device, zipped)
    assert device.calls[0][1]["path"] == r"dist\notlarim-portable.zip"


def test_pull_passes_the_devices_own_refusal_through_by_class() -> None:
    device = ScriptedDevice(
        project__artifact=DeviceRunResult(False, "not_found", "out\\x.exe does not exist")
    )
    with pytest.raises(ArtifactPullError) as raised:
        pull_artifact(device, _row())
    assert raised.value.error_class == "not_found"


# ------------------------------------------------------------------ one rule (456)


def test_the_project_id_the_build_scaffolds_is_the_one_the_lifecycle_addresses() -> None:
    """Contract halves must read each other: the build half addresses the device by a
    project id; if the lifecycle half derived its own, every install/launch would name a
    project the device never scaffolded and both suites would stay green."""
    row = _row()
    assert project_id_for(row) == "native-12345678"
    assert device_build.project_id_for is project_id_for


# ------------------------------------------------------------ signing policy (472/473)


def test_only_unsigned_is_implemented_and_the_other_modes_are_named_not_faked() -> None:
    assert IMPLEMENTED_MODES == frozenset({MODE_UNSIGNED})
    unsigned = SigningPolicy(MODE_UNSIGNED)
    assert unsigned.implemented and not unsigned.signs
    assert unsigned.speech_for("portable") == SPEECH_UNSIGNED_PORTABLE
    assert unsigned.speech_for("msix") == SPEECH_UNSIGNED_MSIX
    for mode in (MODE_TEST_CERTIFICATE, MODE_OWNER_CERTIFICATE):
        policy = SigningPolicy(mode)
        assert not policy.implemented and not policy.signs
        assert mode in policy.speech_for("msix")
        assert "imzasız" in policy.speech_for("msix")
        assert policy.as_dict()["implemented"] is False


def test_the_policy_is_read_from_settings_and_an_unknown_mode_falls_back_to_unsigned() -> None:
    assert (
        policy_from_settings(SimpleNamespace(native_signing_mode="unsigned")).mode == MODE_UNSIGNED
    )
    assert (
        policy_from_settings(SimpleNamespace(native_signing_mode="test_certificate")).mode
        == MODE_TEST_CERTIFICATE
    )
    assert policy_from_settings(SimpleNamespace(native_signing_mode="bogus")).mode == MODE_UNSIGNED
    assert policy_from_settings(SimpleNamespace()).mode == MODE_UNSIGNED
