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
    return DeviceRunResult(
        True, result={"exit_code": 0, "passed": 5, "failed": 0, "report_tail": "5/5 passed"}
    )


def project_test_failing(_payload: dict[str, Any]) -> DeviceRunResult:
    return DeviceRunResult(
        True,
        result={
            "exit_code": 1,
            "passed": 3,
            "failed": 2,
            "report_tail": "not ok - toggleDone flips only the matching task",
        },
    )


def file_reveal_ok(_payload: dict[str, Any]) -> DeviceRunResult:
    return DeviceRunResult(True, result={"revealed": True, "window_id": 1})


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
