"""B33 req 462-466, 468, 469, 471: what the factory does with a build AFTER the device made it.

Until this batch ``native.launch``, ``native.install`` and ``native.fix`` were dead tools -
each waited on a ``ctx.live`` port (``native_launcher``, ``native_installer``,
``native_fix_worker``) that nothing ever registered, and refused with a sentence about the
device. Everything they needed already existed on the device: ``app.launch`` reaches the
native root (ADR-0098), the UI Automation family reads and drives the window (M19/B29),
``window.close`` and ``file.read`` are there, and the projects family now carries
``project.install`` / ``project.uninstall`` / ``project.package`` / ``project.artifact``
(B33). This module is the Cloud Core half: one device call per step, every claim read back
from what the device answered - the same discipline ``device_build`` keeps for the build.

The verification flow (463-466) is the one the owner's qualification script proved once by
hand (docs/QUALIFICATION.md row 26.15): launch, set a note through UI Automation, add it,
read the app's own status line, close, relaunch, read the status line again, read the app's
own log. The automation ids are the notes template's (``MainWindow.xaml``).
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Final

from app.logging import get_logger
from app.nativefactory.models import STATE_VERIFIED, NativeBuildRow
from app.routines.dispatch import DeviceActionPort, DeviceRunResult

logger = get_logger(__name__)

#: The notes template's own automation ids (templates/notes-desktop/src/MainWindow.xaml).
AUTOMATION_NOTE_INPUT: Final = "NoteInput"
AUTOMATION_ADD_BUTTON: Final = "AddButton"
AUTOMATION_STATUS_TEXT: Final = "StatusText"
AUTOMATION_MAIN_WINDOW: Final = "MainWindow"
#: What the verification types; the status line then reads "<n> not".
VERIFY_NOTE_TEXT: Final = "B33 doğrulama notu"
_STATUS_RE: Final = re.compile(r"(\d+)\s+not")

LAUNCH_TIMEOUT_S: Final = 20.0
UI_TIMEOUT_S: Final = 15.0
PACKAGE_TIMEOUT_S: Final = 330.0
INSTALL_TIMEOUT_S: Final = 20.0
ARTIFACT_CHUNK_BYTES: Final = 32 * 1024
ARTIFACT_MAX_BYTES: Final = 50 * 1024 * 1024
LOG_RELATIVE_PATH: Final = "data\\app.log"


def project_id_for(row: NativeBuildRow) -> str:
    """The project id ``device_build`` scaffolded the row under (one rule, two callers)."""
    return f"native-{str(row.id)[:8]}"


@dataclass(frozen=True, slots=True)
class StepOutcome:
    ok: bool
    step: str
    error_class: str | None = None
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)


def _fail(step: str, outcome: DeviceRunResult) -> StepOutcome:
    return StepOutcome(
        ok=False,
        step=step,
        error_class=outcome.error_class or "device_error",
        message=(outcome.message or outcome.error_class or step)[:600],
        result=dict(outcome.result or {}),
    )


def _run(
    device: DeviceActionPort,
    capability: str,
    payload: dict[str, Any],
    *,
    key: str,
    timeout_s: float,
) -> DeviceRunResult:
    return device.run(
        capability=capability, payload=payload, idempotency_key=key, timeout_s=timeout_s
    )


# ------------------------------------------------------------------- launch (462)


def launch_on_device(
    device: DeviceActionPort, row: NativeBuildRow, *, attempt: str = "1"
) -> StepOutcome:
    """``app.launch`` with the built executable's absolute path (ADR-0098's native root).
    A window id is what proves the launch; a pid alone is followed by one ``window.list``."""
    if not row.artifact_path:
        return StepOutcome(
            ok=False, step="app.launch", error_class="no_artifact", message="no artefact to launch"
        )
    exe = _executable_path(row)
    launched = _run(
        device,
        "app.launch",
        {"application": exe},
        key=f"nativefactory-launch:{row.id}:{attempt}",
        timeout_s=LAUNCH_TIMEOUT_S,
    )
    if not launched.ok:
        return _fail("app.launch", launched)
    body = dict(launched.result or {})
    window_id = body.get("window_id")
    pid = body.get("pid")
    if not window_id and pid:
        listed = _run(
            device,
            "window.list",
            {"pid": pid},
            key=f"nativefactory-launch-list:{row.id}:{attempt}",
            timeout_s=UI_TIMEOUT_S,
        )
        windows = list((listed.result or {}).get("windows") or []) if listed.ok else []
        if windows:
            window_id = windows[0].get("window_id")
    if not window_id:
        return StepOutcome(
            ok=False,
            step="app.launch",
            error_class="no_window",
            message="the application started but no window was observed",
            result=body,
        )
    return StepOutcome(
        ok=True,
        step="app.launch",
        result={"pid": pid, "window_id": window_id, "title": body.get("title")},
    )


def _executable_path(row: NativeBuildRow) -> str:
    path = str(row.artifact_path or "")
    if path.lower().endswith((".zip", ".msix")):
        # A packaged row launches the executable the package was made from.
        base = path.rsplit("\\", 1)[0].rsplit("/", 1)[0]
        project = base.rsplit("\\", 1)[0] if base.lower().endswith("\\dist") else base
        return f"{project}\\out\\{row.slug}.exe"
    return path


# ------------------------------------------------------------- UI verification (463)


def _find_node(tree: Any, automation_id: str) -> dict[str, Any] | None:
    if isinstance(tree, dict):
        if tree.get("automation_id") == automation_id:
            return tree
        for child in list(tree.get("children") or []):
            found = _find_node(child, automation_id)
            if found is not None:
                return found
    if isinstance(tree, list):
        for item in tree:
            found = _find_node(item, automation_id)
            if found is not None:
                return found
    return None


def _node_text(node: dict[str, Any] | None) -> str:
    if node is None:
        return ""
    return str(node.get("value") or node.get("name") or node.get("text") or "")


def inspect_window(
    device: DeviceActionPort, row: NativeBuildRow, window_id: str, *, tag: str
) -> StepOutcome:
    """``ui.inspect``: the tree, and the template's own controls found in it (463)."""
    inspected = _run(
        device,
        "ui.inspect",
        {"window_id": window_id, "depth": 5, "max_nodes": 200},
        key=f"nativefactory-inspect:{row.id}:{tag}",
        timeout_s=UI_TIMEOUT_S,
    )
    if not inspected.ok:
        return _fail("ui.inspect", inspected)
    tree = (inspected.result or {}).get("root") or inspected.result or {}
    status = _find_node(tree, AUTOMATION_STATUS_TEXT)
    controls = {
        name: _find_node(tree, name) is not None
        for name in (AUTOMATION_NOTE_INPUT, AUTOMATION_ADD_BUTTON, AUTOMATION_STATUS_TEXT)
    }
    return StepOutcome(
        ok=True,
        step="ui.inspect",
        result={"controls": controls, "status_text": _node_text(status), "window_id": window_id},
    )


def _status_count(text: str) -> int | None:
    match = _STATUS_RE.search(text or "")
    return int(match.group(1)) if match else None


# ------------------------------------- drive, relaunch, persistence, log (463-466)


def verify_on_device(device: DeviceActionPort, row: NativeBuildRow) -> dict[str, Any]:
    """The 26.15 flow, as code. Returns a dict of what each step OBSERVED and a single
    ``verified`` verdict that is true only when every read-back answered as the template
    promises; a step that could not be read leaves the verdict false and names itself."""
    steps: list[dict[str, Any]] = []

    def record(outcome: StepOutcome, **facts: Any) -> bool:
        steps.append(
            {
                "step": outcome.step,
                "ok": outcome.ok,
                "error_class": outcome.error_class,
                "message": outcome.message,
                **facts,
            }
        )
        return outcome.ok

    launched = launch_on_device(device, row, attempt="verify-1")
    if not record(launched):
        return {"verified": False, "failed_step": launched.step, "steps": steps}
    window_id = str(launched.result["window_id"])

    before = inspect_window(device, row, window_id, tag="before")
    record(before, **before.result)
    controls_ok = before.ok and all(before.result.get("controls", {}).values())

    set_value = _run(
        device,
        "ui.set_value",
        {"window_id": window_id, "automation_id": AUTOMATION_NOTE_INPUT, "value": VERIFY_NOTE_TEXT},
        key=f"nativefactory-verify-set:{row.id}",
        timeout_s=UI_TIMEOUT_S,
    )
    record(
        StepOutcome(
            ok=set_value.ok,
            step="ui.set_value",
            error_class=None if set_value.ok else set_value.error_class,
        )
    )
    invoked = _run(
        device,
        "ui.invoke",
        {"window_id": window_id, "automation_id": AUTOMATION_ADD_BUTTON},
        key=f"nativefactory-verify-add:{row.id}",
        timeout_s=UI_TIMEOUT_S,
    )
    record(
        StepOutcome(
            ok=invoked.ok, step="ui.invoke", error_class=None if invoked.ok else invoked.error_class
        )
    )

    after_add = inspect_window(device, row, window_id, tag="after-add")
    count_after_add = (
        _status_count(after_add.result.get("status_text", "")) if after_add.ok else None
    )
    record(after_add, status_text=after_add.result.get("status_text"), count=count_after_add)

    closed = _run(
        device,
        "window.close",
        {"window_id": window_id},
        key=f"nativefactory-verify-close:{row.id}",
        timeout_s=UI_TIMEOUT_S,
    )
    record(
        StepOutcome(
            ok=closed.ok, step="window.close", error_class=None if closed.ok else closed.error_class
        )
    )

    relaunched = launch_on_device(device, row, attempt="verify-2")
    record(relaunched)
    count_after_relaunch: int | None = None
    if relaunched.ok:
        window_id_2 = str(relaunched.result["window_id"])
        after_relaunch = inspect_window(device, row, window_id_2, tag="after-relaunch")
        count_after_relaunch = (
            _status_count(after_relaunch.result.get("status_text", ""))
            if after_relaunch.ok
            else None
        )
        record(
            after_relaunch,
            status_text=after_relaunch.result.get("status_text"),
            count=count_after_relaunch,
        )
        closed_2 = _run(
            device,
            "window.close",
            {"window_id": window_id_2},
            key=f"nativefactory-verify-close2:{row.id}",
            timeout_s=UI_TIMEOUT_S,
        )
        record(
            StepOutcome(
                ok=closed_2.ok,
                step="window.close",
                error_class=None if closed_2.ok else closed_2.error_class,
            )
        )

    log = read_log_on_device(device, row)
    record(log, log_tail=log.result.get("text", "")[-300:] if log.ok else "")

    ui_verified = (
        controls_ok
        and set_value.ok
        and invoked.ok
        and count_after_add is not None
        and count_after_add >= 1
    )
    persisted = relaunched.ok and count_after_relaunch is not None and count_after_relaunch >= 1
    log_ok = log.ok and "started" in str(log.result.get("text") or "").lower()
    verdict = {
        "verified": bool(ui_verified and persisted and log_ok),
        "ui_verified": bool(ui_verified),
        "relaunched": bool(relaunched.ok),
        "persisted": bool(persisted),
        "log_read": bool(log.ok),
        "log_has_startup_line": bool(log_ok),
        "notes_after_add": count_after_add,
        "notes_after_relaunch": count_after_relaunch,
        "steps": steps,
    }
    if not verdict["verified"]:
        # The step that did not answer, else the READ that came back wrong - named so the
        # owner hears which promise of the template the window did not keep.
        failing = next((s["step"] for s in steps if not s["ok"]), None)
        verdict["failed_step"] = failing or (
            "ui.inspect"
            if not controls_ok
            else "status_count"
            if count_after_add is None or count_after_add < 1
            else "persistence"
            if not persisted
            else "file.read"
        )
    return verdict


def read_log_on_device(device: DeviceActionPort, row: NativeBuildRow) -> StepOutcome:
    """``file.read`` of the application's own ``data\\app.log`` beside the executable (466)."""
    exe = _executable_path(row)
    log_path = exe.rsplit("\\", 1)[0] + "\\" + LOG_RELATIVE_PATH
    read = _run(
        device,
        "file.read",
        {"path": log_path, "length": 8000},
        key=f"nativefactory-log:{row.id}:{hashlib.sha1(log_path.encode()).hexdigest()[:8]}",
        timeout_s=UI_TIMEOUT_S,
    )
    if not read.ok:
        return _fail("file.read", read)
    return StepOutcome(
        ok=True,
        step="file.read",
        result={"text": str((read.result or {}).get("text") or ""), "path": log_path},
    )


# ------------------------------------------------ package / install / uninstall


def package_on_device(device: DeviceActionPort, row: NativeBuildRow, *, kind: str) -> StepOutcome:
    packed = _run(
        device,
        "project.package",
        {"project_id": project_id_for(row), "kind": kind},
        key=f"nativefactory-package:{row.id}:{kind}",
        timeout_s=PACKAGE_TIMEOUT_S,
    )
    if not packed.ok:
        return _fail("project.package", packed)
    body = dict(packed.result or {})
    if not body.get("path") or not body.get("sha256"):
        return StepOutcome(
            ok=False,
            step="project.package",
            error_class="package_unreadable",
            message="the device answered no path or hash for the package",
            result=body,
        )
    return StepOutcome(ok=True, step="project.package", result=body)


def install_on_device(device: DeviceActionPort, row: NativeBuildRow) -> StepOutcome:
    installed = _run(
        device,
        "project.install",
        {"project_id": project_id_for(row), "name": row.display_name},
        key=f"nativefactory-install:{row.id}:{row.attempt}",
        timeout_s=INSTALL_TIMEOUT_S,
    )
    if not installed.ok:
        return _fail("project.install", installed)
    body = dict(installed.result or {})
    observed = dict(body.get("observed") or {})
    if not (body.get("installed") is True and observed.get("shortcut_exists") is True):
        return StepOutcome(
            ok=False,
            step="project.install",
            error_class="install_unverified",
            message="the device did not observe the shortcut it was asked to write",
            result=body,
        )
    return StepOutcome(ok=True, step="project.install", result=body)


def uninstall_on_device(device: DeviceActionPort, row: NativeBuildRow) -> StepOutcome:
    removed = _run(
        device,
        "project.uninstall",
        {"project_id": project_id_for(row)},
        key=f"nativefactory-uninstall:{row.id}:{row.attempt}",
        timeout_s=INSTALL_TIMEOUT_S,
    )
    if not removed.ok:
        return _fail("project.uninstall", removed)
    body = dict(removed.result or {})
    observed = dict(body.get("observed") or {})
    if observed.get("shortcut_exists") is True:
        return StepOutcome(
            ok=False,
            step="project.uninstall",
            error_class="uninstall_unverified",
            message="the shortcut is still there after the removal",
            result=body,
        )
    return StepOutcome(ok=True, step="project.uninstall", result=body)


# ------------------------------------------------------------- artifact pull (456)


class ArtifactPullError(RuntimeError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class


def pull_artifact(
    device: DeviceActionPort, row: NativeBuildRow, *, relative_path: str | None = None
) -> tuple[bytes, str, str]:
    """Stream the artefact off the device in bounded chunks and verify the whole against
    the hash the device reports for it. Returns ``(bytes, name, sha256)``."""
    payload_base: dict[str, Any] = {
        "project_id": project_id_for(row),
        "length": ARTIFACT_CHUNK_BYTES,
    }
    if relative_path:
        payload_base["path"] = relative_path
    elif row.artifact_path and row.artifact_path.lower().endswith((".zip", ".msix")):
        payload_base["path"] = "dist\\" + row.artifact_path.replace("/", "\\").rsplit("\\", 1)[-1]
    chunks: list[bytes] = []
    offset = 0
    expected_sha = ""
    name = ""
    total = 0
    while True:
        chunk = _run(
            device,
            "project.artifact",
            {**payload_base, "offset": offset},
            key=f"nativefactory-artifact:{row.id}:{offset}",
            timeout_s=UI_TIMEOUT_S,
        )
        if not chunk.ok:
            raise ArtifactPullError(
                chunk.error_class or "device_error",
                chunk.message or "the device refused the artefact read",
            )
        body = dict(chunk.result or {})
        expected_sha = str(body.get("sha256") or expected_sha)
        name = str(body.get("name") or name)
        total = int(body.get("bytes") or total)
        if total > ARTIFACT_MAX_BYTES:
            raise ArtifactPullError(
                "too_large",
                f"the artefact is {total} bytes, over the {ARTIFACT_MAX_BYTES} byte bound",
            )
        data = base64.b64decode(str(body.get("base64") or ""))
        chunks.append(data)
        offset += len(data)
        if body.get("eof") is True or not data:
            break
        if offset > ARTIFACT_MAX_BYTES:
            raise ArtifactPullError("too_large", "the artefact kept growing past the bound")
    blob = b"".join(chunks)
    digest = hashlib.sha256(blob).hexdigest()
    if expected_sha and digest != expected_sha:
        raise ArtifactPullError(
            "artifact_mismatch",
            f"the bytes pulled hash to {digest[:16]}…, the device says {expected_sha[:16]}…",
        )
    return blob, name or f"{row.slug}.bin", digest


def row_is_launchable(row: NativeBuildRow) -> bool:
    return bool(row.artifact_path) and row.state in (STATE_VERIFIED, "unverified", "mismatch")


__all__ = [
    "AUTOMATION_ADD_BUTTON",
    "AUTOMATION_NOTE_INPUT",
    "AUTOMATION_STATUS_TEXT",
    "ArtifactPullError",
    "StepOutcome",
    "VERIFY_NOTE_TEXT",
    "inspect_window",
    "install_on_device",
    "launch_on_device",
    "package_on_device",
    "project_id_for",
    "pull_artifact",
    "read_log_on_device",
    "row_is_launchable",
    "uninstall_on_device",
    "verify_on_device",
]
