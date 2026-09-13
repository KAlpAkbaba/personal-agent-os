"""The desktop capability names exist in four places. They must not drift.

The browser family already had this guard
(``services/browser/tests/unit/test_capability_mirrors.py``). The DESKTOP family
did not, and the 2026-09-08 incident report asked for exactly this: reconcile
alarm arm/start, alarm disarm/stop, display wake, play audio, activity status
across Cloud Core, the Device Service, the Session Companion and the owner
harness, and leave no alias inconsistent between producers and consumers.

The four places:

* ``app.routines.dispatch`` -- what Cloud Core may ask a device to do. A name
  Cloud Core spells differently fails at device selection as
  ``no_capable_device``, which reads like "the device cannot do it" rather than
  "we asked for a name that does not exist".
* ``AgentCapabilities`` (C#) -- what the Device Service ADVERTISES and routes;
  ``AgentCapabilities.IsInteractive`` is what it forwards to the companion.
* the installed-agent verification in ``scripts/lib/InstallEvidence.ps1`` --
  what an install is allowed to call a success.
* the owner harness under ``scripts/core`` -- what a real qualification run
  sends.

This test reads the other three as text: no C# build, no PowerShell process.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.routines import dispatch

REPO_ROOT = Path(__file__).resolve().parents[4]
PROTOCOL_CS = (
    REPO_ROOT
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.Agent.Core"
    / "Protocol"
    / "ProtocolConstants.cs"
)

#: Every desktop name the M18 / M18.3 architecture is built on, canonically spelled.
#: `desktop.open_*` are the M1/M3 baseline; the rest are what the wake alarm and the
#: ambient loop dispatch. This tuple IS the reconciliation the incident asked for.
CANONICAL_DESKTOP = (
    "desktop.open_application",
    "desktop.open_artifact",
    "desktop.alarm_start",
    "desktop.alarm_stop",
    "desktop.alarm_arm",
    "desktop.alarm_disarm",
    "desktop.display_wake",
    "desktop.display_status",
    "desktop.activity_status",
    "desktop.play_audio",
    "desktop.display_off",
    "desktop.notify",
)

CANONICAL_BROWSER_MEDIA = (
    "browser.media_play",
    "browser.media_volume",
    "browser.media_status",
    "browser.media_stop",
)


def _require(path: Path) -> str:
    if not path.exists():  # pragma: no cover - only in a partial checkout
        pytest.skip(f"{path} is not present in this checkout")
    return path.read_text(encoding="utf-8")


def _csharp_desktop_constants(source: str) -> set[str]:
    return set(
        re.findall(r'public\s+const\s+string\s+\w+\s*=\s*"(desktop\.[a-z_]+)"\s*;', source)
    )


def test_cloud_core_names_are_the_canonical_ones() -> None:
    """Every ``CAPABILITY_DESKTOP_*`` Cloud Core dispatches is a canonical name."""
    declared = {
        value
        for name, value in vars(dispatch).items()
        if name.startswith("CAPABILITY_DESKTOP_") and isinstance(value, str)
    }
    assert declared, "app.routines.dispatch declares no desktop capability constants"
    unknown = declared - set(CANONICAL_DESKTOP)
    assert not unknown, (
        f"Cloud Core dispatches names the device does not advertise: {sorted(unknown)}"
    )


def test_cloud_core_browser_media_names_are_the_canonical_ones() -> None:
    declared = {
        value
        for name, value in vars(dispatch).items()
        if name.startswith("CAPABILITY_BROWSER_MEDIA_") and isinstance(value, str)
    }
    assert declared == set(CANONICAL_BROWSER_MEDIA)


def test_the_device_service_declares_every_canonical_desktop_name() -> None:
    """A name Cloud Core can ask for that the agent never declares is an alias bug."""
    constants = _csharp_desktop_constants(_require(PROTOCOL_CS))
    missing = [name for name in CANONICAL_DESKTOP if name not in constants]
    assert not missing, f"AgentCapabilities (C#) declares no constant for: {missing}"


def test_the_device_service_declares_no_desktop_name_nobody_asks_for() -> None:
    """The reverse direction: a capability advertised and never dispatched is dead
    weight the owner still sees on their device row."""
    constants = _csharp_desktop_constants(_require(PROTOCOL_CS))
    extra = sorted(constants - set(CANONICAL_DESKTOP))
    assert not extra, (
        f"the agent declares desktop names this reconciliation does not know about: {extra}. "
        "Add them to CANONICAL_DESKTOP and to app.routines.dispatch, or remove them."
    )


def test_the_composed_manifest_gates_only_display_off() -> None:
    """Ordering and gating, read from the C# source.

    ``Compose`` adds desktop + alarm + ambient unconditionally, display power
    behind its own flag, then the browser family. The 2026-09-08 candidate
    advertised 40 names with ``-DisplayPower`` and a browser worker: 2 + 2 + 6
    + 1 + 29. If this arithmetic changes, the installer's expectation and the
    owner's device row change with it.
    """
    source = _require(PROTOCOL_CS)
    compose = source[source.index("public static IReadOnlyList<string> Compose(") :]
    compose = compose[: compose.index("public static bool IsDesktop(")]

    assert "if (displayPowerEnabled)" in compose, "display-off must stay behind its own flag"
    assert "if (browserEnabled)" in compose, "the browser family must stay behind BrowserEnabled"
    # The always-on groups are added without a condition before either gate.
    head = compose[: compose.index("if (displayPowerEnabled)")]
    for group in ("names.AddRange(Desktop);", "names.AddRange(Alarm);", "names.AddRange(Ambient);"):
        assert group in head, (
            f"{group} must be unconditional -- these names add, they never subtract"
        )


def test_the_installed_agent_verification_requires_the_browser_media_family() -> None:
    """Forbidden fix, pinned from the Cloud Core side too.

    "Remove the media capabilities from the health check to make it pass" is
    explicitly not an option: the wake-alarm media path dispatches exactly these
    four names, so an install that cannot verify them is an alarm that will not
    ring.
    """
    evidence = _require(REPO_ROOT / "scripts" / "lib" / "InstallEvidence.ps1")
    for name in CANONICAL_BROWSER_MEDIA:
        assert f'"{name}"' in evidence, (
            f"{name} must stay in the installed-agent verification; "
            "app.routines.dispatch dispatches it"
        )


def test_the_owner_harness_uses_canonical_names_only() -> None:
    """Anything the owner's own qualification scripts send must be a real name."""
    harness = REPO_ROOT / "scripts" / "core"
    if not harness.exists():  # pragma: no cover
        pytest.skip("scripts/core is not present in this checkout")
    used: set[str] = set()
    for script in harness.glob("*.ps1"):
        used |= set(re.findall(r'"(desktop\.[a-z_]+)"', script.read_text(encoding="utf-8")))
    unknown = used - set(CANONICAL_DESKTOP)
    assert not unknown, (
        f"the owner harness sends desktop names that do not exist: {sorted(unknown)}"
    )
