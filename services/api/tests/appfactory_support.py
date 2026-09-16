"""Shared fixtures for the M23 App Factory unit/tool/corpus suites.

The fake device's ``project.*`` family (DEVICE_PROTOCOL.md §6l / M23_APP_FACTORY_SPEC.md
§3): never a real Windows Job Object, never a real process — echoes back the shapes the
real companion answers with, so ``app.appfactory.service.AppFactoryService`` and the voice
tools that call it are exercised against realistic results without a device (task brief:
no real device in tests). ``file.reveal`` is the existing M19 capability
(DEVICE_PROTOCOL.md §6i), reused verbatim for ``app.open``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from app.routines.dispatch import DeviceRunResult

_BASE_ROOT = "C:/Users/owner/Documents/PagentOS Projects"


def project_scaffold_ok(payload: dict[str, Any]) -> DeviceRunResult:
    slug = str(payload.get("slug") or "app")
    files = payload.get("files") or []
    root_path = f"{_BASE_ROOT}/{slug}"
    sha256_by_path = {
        f["path"]: hashlib.sha256(str(f.get("text", "")).encode("utf-8")).hexdigest()
        for f in files
        if isinstance(f, dict) and f.get("path")
    }
    return DeviceRunResult(
        True,
        result={
            "root_path": root_path,
            "files_written": len(files),
            "sha256_by_path": sha256_by_path,
        },
    )


def _port_for(project_id: str) -> int:
    digest = hashlib.sha256(project_id.encode("utf-8")).digest()
    return 20000 + (digest[0] << 8 | digest[1]) % 10000


def project_run_ok(payload: dict[str, Any]) -> DeviceRunResult:
    project_id = str(payload.get("project_id") or "")
    port = _port_for(project_id)
    return DeviceRunResult(
        True,
        result={
            "pid": 4242,
            "port": port,
            "url": f"http://127.0.0.1:{port}/",
            "started_at": datetime.now(UTC).isoformat(),
        },
    )


def project_status_ok(payload: dict[str, Any]) -> DeviceRunResult:
    project_id = str(payload.get("project_id") or "")
    return DeviceRunResult(
        True,
        result={
            "state": "running",
            "pid": 4242,
            "port": _port_for(project_id),
            "uptime_s": 5,
            "log_tail": "",
        },
    )


def project_stop_ok(_payload: dict[str, Any]) -> DeviceRunResult:
    return DeviceRunResult(True, result={"stopped": True})


def project_test_ok(_payload: dict[str, Any]) -> DeviceRunResult:
    # `counts_parsed` is part of what the REAL device answers (ProjectCapabilities.TestAsync):
    # it is how the device says whether it could read its runner's output at all. Every fake
    # that left it out let the Cloud Core's "verified" verdict be tested against a shape the
    # device never sends - which is how row 26.16 was stamped verified with `passed: null`.
    return DeviceRunResult(
        True,
        result={
            "exit_code": 0,
            "passed": 5,
            "failed": 0,
            "counts_parsed": True,
            "duration_ms": 240,
            "report_tail": "5/5 passed",
        },
    )


def project_test_failing(_payload: dict[str, Any]) -> DeviceRunResult:
    return DeviceRunResult(
        True,
        result={
            "exit_code": 1,
            "passed": 3,
            "failed": 2,
            "counts_parsed": True,
            "duration_ms": 310,
            "report_tail": "not ok - toggleDone flips only the matching task",
        },
    )


def file_reveal_ok(_payload: dict[str, Any]) -> DeviceRunResult:
    return DeviceRunResult(True, result={"revealed": True, "window_id": 1})


# --------------------------------------------------------------- B33 lifecycle fakes

#: What the fake desktop installed (shortcut paths), and the artefact bytes it serves.
INSTALLED: dict[str, str] = {}
ARTIFACT_BYTES = b"MZ\x90\x00" + b"PagentOS native artefact fixture " * 64


def project_package_ok(payload: dict[str, Any]) -> DeviceRunResult:
    import hashlib

    kind = str(payload.get("kind") or "portable")
    project_id = str(payload.get("project_id") or "")
    name = f"{project_id}-portable.zip" if kind == "portable" else f"{project_id}.msix"
    path = f"C:\\Users\\owner\\Documents\\PagentOS Projects\\native\\{project_id}\\dist\\{name}"
    return DeviceRunResult(
        True,
        result={
            "kind": kind,
            "path": path,
            "name": name,
            "bytes": len(ARTIFACT_BYTES),
            "sha256": hashlib.sha256(ARTIFACT_BYTES).hexdigest(),
            "signed": False,
            "observed": {"exists": True, "bytes": len(ARTIFACT_BYTES)},
        },
    )


def project_install_ok(payload: dict[str, Any]) -> DeviceRunResult:
    project_id = str(payload.get("project_id") or "")
    name = str(payload.get("name") or project_id)
    programs = "C:\\Users\\owner\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs"
    shortcut = f"{programs}\\PagentOS\\{name}.lnk"
    INSTALLED[project_id] = shortcut
    return DeviceRunResult(
        True,
        result={
            "installed": True,
            "method": "start_menu_shortcut",
            "name": name,
            "exe": f"C:\\...\\native\\{project_id}\\out\\{project_id}.exe",
            "shortcut": shortcut,
            "observed": {"shortcut_exists": True, "exe_exists": True},
        },
    )


def project_uninstall_ok(payload: dict[str, Any]) -> DeviceRunResult:
    project_id = str(payload.get("project_id") or "")
    shortcut = INSTALLED.pop(project_id, None)
    if shortcut is None:
        return DeviceRunResult(False, "not_found", "not installed by this system")
    return DeviceRunResult(
        True,
        result={
            "uninstalled": True,
            "shortcut_removed": True,
            "shortcut": shortcut,
            "build_kept": True,
            "observed": {"shortcut_exists": False},
        },
    )


def project_artifact_ok(payload: dict[str, Any]) -> DeviceRunResult:
    import base64
    import hashlib

    offset = int(payload.get("offset") or 0)
    length = min(int(payload.get("length") or 32768), 32768)
    chunk = ARTIFACT_BYTES[offset : offset + length]
    return DeviceRunResult(
        True,
        result={
            "path": "C:\\...\\out\\app.exe",
            "name": "app.exe",
            "bytes": len(ARTIFACT_BYTES),
            "sha256": hashlib.sha256(ARTIFACT_BYTES).hexdigest(),
            "offset": offset,
            "length": len(chunk),
            "base64": base64.b64encode(chunk).decode("ascii"),
            "eof": offset + len(chunk) >= len(ARTIFACT_BYTES),
        },
    )


def native_lifecycle_capability_results() -> dict[str, Any]:
    """B33: the four projects-family lifecycle answers (the UI tree the notes app shows
    after a launch is ``tests.alarms_support``'s ``ui.inspect`` fake, overridden by tests
    that need the template's own controls)."""
    return {
        "project.package": project_package_ok,
        "project.install": project_install_ok,
        "project.uninstall": project_uninstall_ok,
        "project.artifact": project_artifact_ok,
    }


def appfactory_capability_results() -> dict[str, Any]:
    """``{capability: DeviceRunResult | callable}`` for ``FakeDeviceAction`` (the same
    shape ``tests.artifacts_support.artifact_capability_results`` returns)."""
    return {
        "project.scaffold": project_scaffold_ok,
        "project.run": project_run_ok,
        "project.status": project_status_ok,
        "project.stop": project_stop_ok,
        "project.test": project_test_ok,
        "file.reveal": file_reveal_ok,
        **native_lifecycle_capability_results(),
    }


__all__ = [
    "appfactory_capability_results",
    "file_reveal_ok",
    "project_run_ok",
    "project_scaffold_ok",
    "project_status_ok",
    "project_stop_ok",
    "project_test_failing",
    "project_test_ok",
]
