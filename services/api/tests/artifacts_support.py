"""Shared fixtures for the M22 Artifact Factory voice/corpus suites.

The fake device's ``file.fetch`` (DEVICE_PROTOCOL.md §6k): never a real HTTP request,
never a real file system — echoes the payload back in exactly the shape the real
companion answers with (``{file, path, verified, sha256, bytes, mark_of_the_web,
opened}``), so ``app.artifacts.open_service`` and the voice tool that calls it are
exercised against a realistic result without a network or a device (task brief).
"""

from __future__ import annotations

from typing import Any

from app.routines.dispatch import DeviceRunResult


def file_fetch_ok(payload: dict[str, Any]) -> DeviceRunResult:
    """A successful fetch + open, echoing the caller's own name/hash/size back — the
    same discipline ``tests.alarms_support.FakeDeviceAction`` documents for a callable
    entry (M19: ``window.activate`` echoing the requested ``window_id``)."""
    name = str(payload.get("name") or "artifact")
    path = f"C:/Users/owner/Downloads/{name}"
    return DeviceRunResult(
        True,
        result={
            "file": {"file_id": f"file:fetched-{name}", "name": name, "path": path},
            "path": path,
            "verified": True,
            "sha256": payload.get("sha256"),
            "bytes": payload.get("size"),
            "mark_of_the_web": True,
            "opened": {
                "opened": True,
                "path": path,
                "pid": 4242,
                "window_id": 1,
                "observed": {"window": {"title": name}, "window_appeared": True},
            },
        },
    )


def artifact_capability_results() -> dict[str, Any]:
    """``{capability: DeviceRunResult | callable}`` for ``FakeDeviceAction`` (the same
    shape ``tests.documents_support.document_capability_results`` returns)."""
    return {"file.fetch": file_fetch_ok}


__all__ = ["artifact_capability_results", "file_fetch_ok"]
