"""The broker's WebSocket frame bound is ONE number on both sides.

2026-09-17, production: the Cloud Core ran uvicorn with ``--ws-max-size 65536`` while the
device sends frames up to its ``ProtocolConstants.MaxFrameBytes`` (1 MiB). A Blender
``scene.inspect`` result - a 62 KB render, inline as base64 - closed the connection; the
broker re-delivered the unfinished command on the next one, and the device reconnected about
once a second until the command expired. Both suites were green: neither side read the
other's number. This test reads both.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DOCKERFILE = REPO / "services" / "api" / "Dockerfile"
CONSTANTS = (
    REPO
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.Agent.Core"
    / "Protocol"
    / "ProtocolConstants.cs"
)
LAUNCHERS = [
    REPO / "scripts" / "dev-broker.ps1",
    REPO / "scripts" / "e2e-m1-device.ps1",
    REPO / "scripts" / "e2e-m13-research.ps1",
]


def _cs_int(pattern: str) -> int:
    text = CONSTANTS.read_text(encoding="utf-8")
    match = re.search(pattern, text)
    assert match, f"{pattern!r} not found in {CONSTANTS.name}"
    expression = match.group(1).replace("L", "")
    value = 1
    for factor in expression.split("*"):
        value *= int(factor.strip())
    return value


def device_frame_bytes() -> int:
    return _cs_int(r"public const int MaxFrameBytes = ([0-9 *]+);")


def test_production_accepts_every_frame_the_device_may_send() -> None:
    match = re.search(r'"--ws-max-size",\s*"(\d+)"', DOCKERFILE.read_text(encoding="utf-8"))
    assert match, "the production command names no --ws-max-size"
    assert int(match.group(1)) == device_frame_bytes()


def test_every_local_broker_launcher_uses_the_same_bound() -> None:
    for launcher in LAUNCHERS:
        sizes = re.findall(r"--ws-max-size (\d+)", launcher.read_text(encoding="utf-8"))
        assert sizes, launcher.name
        assert {int(s) for s in sizes} == {device_frame_bytes()}, launcher.name


def test_the_largest_scene_inspect_result_fits_one_frame() -> None:
    """scene.inspect carries the render inline (base64) beside the driver's inspection."""
    render = _cs_int(r"public const long MaxRenderBytes = ([0-9 *L]+);")
    driver = REPO / "services" / "api" / "app" / "creative3d" / "drivers"
    inspection = (
        max(
            int(m)
            for path in (driver / "SceneDriver.cs", driver / "blender_driver.py")
            for m in re.findall(
                r"MAX_?OUT_?JSON_?BYTES\s*=\s*(\d+)\s*\*\s*1024",
                path.read_text(encoding="utf-8"),
                re.I,
            )
        )
        * 1024
    )
    base64_render = 4 * ((render + 2) // 3)
    envelope = 16 * 1024
    assert base64_render + inspection + envelope <= device_frame_bytes()
