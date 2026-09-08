"""`display off` must never be able to become `Windows sleep`, and it cannot.

The owner's ambient qualification directive (2026-09-08) puts one invariant above the rest:

    display off != Windows sleep

A forbidden-tools list in the voice corpus cannot prove that. It only says the router did
not pick a suspend tool for the utterances someone thought to write down — it says nothing
about the utterances nobody wrote, and nothing at all about a future capability. The real
question is whether this product CAN suspend the machine, and the answer is structural:
nothing in it can.

Two things are asserted here, and both are about source rather than behaviour:

1. **No Windows power-state API is referenced anywhere in the device agent or the Cloud
   Core.** `SetSuspendState`, `SetSystemPowerState`, `ExitWindowsEx`,
   `InitiateSystemShutdown`, `PowrProf`, `shutdown.exe`, `Restart-Computer`,
   `Stop-Computer` — a capability that darkens a screen has no business importing any of
   them, and if one ever appears this test is where the reason gets argued.
2. **The one darkening mechanism is a MONITOR power message**, `WM_SYSCOMMAND` /
   `SC_MONITORPOWER` with the "off" parameter — the same message the shell sends when a
   power plan blanks the screen. It changes what the panel does, not what the operating
   system is doing, so every service, worker, alarm and durable run keeps running behind a
   dark screen. That is the property the owner is asking to be sure of.

This is deliberately a source scan, not a mock. A test that asserted "the display service
did not call sleep" would prove one path on one day; reading the source proves there is no
such call to reach, on any path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: Every Windows API, shell command and PowerShell cmdlet that changes the MACHINE's power
#: state, as opposed to a display's. Spelled out so a reviewer can see exactly what is
#: being refused rather than trusting a clever regex.
SUSPEND_MARKERS: tuple[str, ...] = (
    "SetSuspendState",
    "SetSystemPowerState",
    "ExitWindowsEx",
    "InitiateSystemShutdown",
    "PowrProf",
    "powrprof",
    "shutdown.exe",
    "Restart-Computer",
    "Stop-Computer",
)

#: `SetThreadExecutionState` is NOT on that list, and the reason is worth stating because
#: this guard flagged it on its first run. The agent calls it in exactly one place, with the
#: display-required flag ALONE, to reset the idle timer once when waking a screen — the
#: opposite of a suspend. What would be wrong is a STANDING claim (`ES_CONTINUOUS`) or one
#: that speaks for the system rather than the display (`ES_SYSTEM_REQUIRED`), because a claim
#: nobody clears is indistinguishable from a broken power plan from the owner's side.
#: `AmbientCapabilityTests.cs` already forbids those flags in the display-wake file; this
#: asserts the same rule across the WHOLE agent, so a second caller cannot appear elsewhere.
EXECUTION_STATE_FLAGS_FORBIDDEN: tuple[str, ...] = (
    "ES_CONTINUOUS",
    "EsContinuous",
    "ES_SYSTEM_REQUIRED",
    "EsSystemRequired",
    "ES_AWAYMODE_REQUIRED",
    "EsAwaymodeRequired",
)

#: The one message the product may use to darken a panel (see `DisplayPowerController.cs`).
MONITOR_POWER_MARKERS: tuple[str, ...] = ("SC_MONITORPOWER", "ScMonitorPower")


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "devices" / "windows-agent").is_dir():
            return parent
    raise AssertionError("the repository root was not found from this test's location")


ROOT = _repo_root()
DEVICE_SRC = ROOT / "devices" / "windows-agent" / "src"
CORE_APP = ROOT / "services" / "api" / "app"


def _sources(root: Path, suffix: str) -> list[Path]:
    return [
        p
        for p in root.rglob(f"*{suffix}")
        if "obj" not in p.parts and "bin" not in p.parts and "__pycache__" not in p.parts
    ]


def test_this_guard_is_reading_the_source_it_thinks_it_is() -> None:
    """A source scan that finds no files passes for the wrong reason. The M24 lesson: a
    guard that compares nothing is worse than no guard."""
    assert DEVICE_SRC.is_dir(), f"the device source is not at {DEVICE_SRC}"
    cs = _sources(DEVICE_SRC, ".cs")
    py = _sources(CORE_APP, ".py")
    assert len(cs) > 50, f"only {len(cs)} C# files found — this guard would prove nothing"
    assert len(py) > 200, f"only {len(py)} Python files found — this guard would prove nothing"


@pytest.mark.parametrize("marker", SUSPEND_MARKERS)
def test_the_windows_agent_cannot_suspend_the_machine(marker: str) -> None:
    """The agent darkens screens. It has no way to sleep, hibernate, shut down or log out
    the machine, and that is why every service keeps running behind a dark screen."""
    hits = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in _sources(DEVICE_SRC, ".cs")
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if marker in line
    ]
    assert not hits, (
        f"'{marker}' appears in the device agent at {hits} — the owner's invariant is that "
        "display off is never machine sleep, and a capability that darkens a screen must "
        "not be able to suspend the computer that is running the alarms"
    )


@pytest.mark.parametrize("marker", SUSPEND_MARKERS)
def test_the_cloud_core_cannot_ask_for_a_machine_suspend(marker: str) -> None:
    """And the Cloud Core cannot ask for one either: there is no such command to send."""
    hits = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in _sources(CORE_APP, ".py")
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if marker in line
    ]
    assert not hits, f"'{marker}' appears in the Cloud Core at {hits}"


def test_the_only_darkening_mechanism_is_a_monitor_power_message() -> None:
    """Positive half: the guard above would also pass if the product could not darken a
    screen at all, so this one proves the mechanism that IS there is the monitor's, and
    that it is used in exactly one place."""
    files = [
        p
        for p in _sources(DEVICE_SRC, ".cs")
        if any(m in p.read_text(encoding="utf-8", errors="replace") for m in MONITOR_POWER_MARKERS)
    ]
    assert len(files) == 1, (
        f"the monitor power message appears in {[str(p.relative_to(ROOT)) for p in files]} — "
        "one darkening mechanism in one place is what makes this reviewable"
    )
    text = files[0].read_text(encoding="utf-8", errors="replace")
    # 2 is "power off"; 1 is low power and -1 is on. The comment beside it says only 2 is
    # ever sent, and this holds it to that.
    assert re.search(r"MonitorOff\s*=\s*2", text), "the 'off' parameter is not the monitor's"
    assert "SendMessageTimeout" in text, (
        "a broadcast without a timeout can be stalled forever by one hung window, and a "
        "stuck display path is the quiet failure this companion must not have"
    )


@pytest.mark.parametrize("flag", EXECUTION_STATE_FLAGS_FORBIDDEN)
def test_no_execution_state_claim_outlives_the_call_that_made_it(flag: str) -> None:
    """The agent may reset the display idle timer once. It may not hold a standing claim on
    the owner's power plan, and it may never speak for the SYSTEM's idle timer — the first
    is a wake, the second is a power plan the owner never chose."""
    hits = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in _sources(DEVICE_SRC, ".cs")
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if flag in line
    ]
    assert not hits, f"'{flag}' appears at {hits} — a wake resets the timer once and lets go"


def test_the_execution_state_call_is_the_display_flag_alone() -> None:
    """And the positive half: where the call IS made, the flag is the display's own (0x2),
    sent by itself. Found by this guard's first run, which flagged the call and sent me to
    read it — the right outcome for a guard, and the reason it stays narrow rather than
    banning the API outright."""
    callers = [
        p
        for p in _sources(DEVICE_SRC, ".cs")
        if "SetThreadExecutionState" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert len(callers) == 1, (
        f"the execution-state API is called from {[str(p.relative_to(ROOT)) for p in callers]} — "
        "one caller is what makes this reviewable at a glance"
    )
    text = callers[0].read_text(encoding="utf-8", errors="replace")
    assert re.search(r"DisplayNeeded\s*=\s*0x0*2", text), "the flag is not the display's own"


def test_no_capability_name_offers_to_suspend_the_machine() -> None:
    """The wire vocabulary itself: a capability the owner could ask for by name. There is a
    `desktop.display_off`, and there is deliberately nothing beside it that ends the
    session or the machine."""
    constants = DEVICE_SRC / "PagentOS.Agent.Core" / "Protocol" / "ProtocolConstants.cs"
    assert constants.is_file(), f"the protocol constants are not at {constants}"
    names = set(
        re.findall(r'"((?:desktop|system|power)\.[a-z_]+)"', constants.read_text(encoding="utf-8"))
    )
    assert "desktop.display_off" in names, "the display capability is gone; this guard is stale"
    forbidden = {
        n for n in names if re.search(r"sleep|hibernate|suspend|shutdown|logoff|logout|restart", n)
    }
    assert not forbidden, (
        f"the protocol offers {sorted(forbidden)} — the owner's machine stays awake"
    )
