"""Shared fixtures for the M18.3 alarm/ambient suites.

A SQLite database built from the model metadata, a FAKE device port, and a manual clock.
Nothing here opens a browser, plays audio, changes a volume or touches a display —
``app.routines.dispatch``'s own suite draws the same boundary, and this one inherits it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.alarms.models import AmbientPolicyRow, WakeAlarm
from app.ledger.models import ActivityEventRow
from app.routines.dispatch import DeviceRunResult
from app.routines.models import Routine, RoutineFiring

ALL_TABLES = [
    WakeAlarm.__table__,
    AmbientPolicyRow.__table__,
    Routine.__table__,
    RoutineFiring.__table__,
    ActivityEventRow.__table__,
]

#: A Wednesday 07:29:30 UTC — a minute before a 07:30 Istanbul alarm would be irrelevant,
#: so tests state their own instants explicitly rather than depending on this.
BASE_NOW = datetime(2026, 9, 9, 4, 0, tzinfo=UTC)


def build_session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ALL_TABLES:
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


#: The Windows companion's own window-id shape, restated here from its source rather than
#: imported from the server it is meant to police: ``WindowRegistry.TryParseHandle`` splits
#: on "-", demands exactly three parts, the literal "w", a POSITIVE ``long`` handle and a
#: ``ulong`` creation tick, all plain decimal digits. ``tests/unit/test_operator_window_ref``
#: reads the C# and asserts this and the server's copy both still match it.
_DEVICE_WINDOW_ID = re.compile(r"^w-(?P<handle>[0-9]+)-(?P<tick>[0-9]+)$")


def device_window_id_ok(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = _DEVICE_WINDOW_ID.match(value)
    if match is None:
        return False
    return 0 < int(match.group("handle")) <= 2**63 - 1 and int(match.group("tick")) <= 2**64 - 1


#: A device-shaped id for tests. The literal ids this file used to hand out ("w-1", "w-0")
#: were ones the REAL device refuses outright, which is why a whole suite stayed green
#: while production spent an owner's turn on ``validation_error`` (2026-09-09).
def window_id(n: int = 1) -> str:
    return f"w-{9000 + n}-365601875"


@dataclass
class FakeDeviceAction:
    """A ``DeviceActionPort`` that records every call and answers from a script.

    It REFUSES a ``window_id`` the real device would refuse, with that device's own
    ``validation_error``. A fake that is kinder than the thing it stands in for does not
    catch anything: on 2026-09-09 the server sent the model's window TITLE where an id
    belongs, every test passed, and the owner's Notepad never received a keystroke.

    ``results`` maps a capability name to the ``DeviceRunResult`` it should return; anything
    unscripted succeeds with an empty result, which is deliberately the LEAST informative
    success — a test that wants a verified read-back has to say so. A mapped value may
    instead be a ``Callable[[dict], DeviceRunResult]`` when a result must echo something
    from its own payload (M19: ``window.activate`` echoing back the requested
    ``window_id``, so a plan that resolves the PREVIOUS window's id gets a foreground
    window matching THAT id back, not always the same canned one).
    """

    results: dict[str, DeviceRunResult | Callable[[dict[str, Any]], DeviceRunResult]] = field(
        default_factory=dict
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run(
        self,
        *,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str,
        timeout_s: float,
    ) -> DeviceRunResult:
        self.calls.append(
            {
                "capability": capability,
                "payload": payload,
                "idempotency_key": idempotency_key,
                "timeout_s": timeout_s,
            }
        )
        if "window_id" in payload and not device_window_id_ok(payload["window_id"]):
            return DeviceRunResult(
                False,
                "validation_error",
                f"'{payload['window_id']}' is not a window id (expected w-<hwnd>-<tick>)",
            )
        scripted = self.results.get(capability, DeviceRunResult(True, result={}))
        return scripted(payload) if callable(scripted) else scripted

    def capabilities_called(self) -> list[str]:
        return [c["capability"] for c in self.calls]

    def payload_for(self, capability: str) -> dict[str, Any] | None:
        for call in self.calls:
            if call["capability"] == capability:
                return call["payload"]
        return None

    def count(self, capability: str) -> int:
        return sum(1 for c in self.calls if c["capability"] == capability)

    def reset(self) -> None:
        self.calls.clear()


def ok(**result: Any) -> DeviceRunResult:
    return DeviceRunResult(True, result=result)


def failed(error_class: str = "capability_missing", message: str = "") -> DeviceRunResult:
    return DeviceRunResult(False, error_class, message or error_class)


def refused(reason: str, **extra: Any) -> DeviceRunResult:
    """A SUCCESSFUL device command carrying a refusal (spec §5.1)."""
    return DeviceRunResult(True, result={"refused": reason, "display_off": False, **extra})


#: M19 (docs/M19_DIGITAL_OPERATOR_SPEC.md §2): the window ``app.launch`` produces and
#: every window-observing capability re-observes, so a plan's postcondition VERIFIES by
#: default the same way the alarm capabilities above do. ``window.list``'s happy default
#: is EMPTY (the window already gone) because the only plan that calls it is
#: ``close_window``'s second step, and its postcondition is "the window is gone".
_OPERATOR_WINDOW: dict[str, Any] = {
    "window_id": window_id(1),
    "pid": 4242,
    "title": "Adsız - Not Defteri",
    "state": "normal",
    "foreground": True,
}


#: Every operator device capability the corpus/unit tests need, answered so a plan's
#: postcondition VERIFIES by default. ``terminal.execute``'s canned stdout carries BOTH a
#: hostname-shaped first line ("MAIL") and an IPv4-looking line, because the fake device is
#: keyed on capability alone (never on payload) and one static answer has to satisfy both
#: the "IP adresimi göster" and "Bilgisayarın adı ne?" postconditions
#: (``app.operator.plans.shell_query``); the tool reads only the first line for the
#: hostname's spoken value.
def _activate_result(payload: dict[str, Any]) -> DeviceRunResult:
    """Echoes back the requested ``window_id`` (M19: a plan that resolved the PREVIOUS
    window's id must see THAT id come back foreground, not always the same one)."""
    requested = str(payload.get("window_id") or window_id(1))
    return ok(window={**_OPERATOR_WINDOW, "window_id": requested, "foreground": True})


def happy_operator_device_results() -> dict[str, DeviceRunResult | Callable]:
    return {
        "app.launch": ok(pid=4242, window_id=window_id(1), title="Adsız - Not Defteri"),
        "window.current": ok(window=dict(_OPERATOR_WINDOW)),
        "window.list": ok(windows=[]),
        "window.activate": _activate_result,
        "window.maximize": ok(window={**_OPERATOR_WINDOW, "state": "maximized"}),
        "window.minimize": ok(
            window={**_OPERATOR_WINDOW, "state": "minimized", "foreground": False}
        ),
        "window.restore": ok(window={**_OPERATOR_WINDOW, "state": "normal"}),
        "window.close": ok(closed=True),
        "keyboard.type": ok(typed_chars=8, window_id=window_id(1)),
        "ui.inspect": ok(
            root={
                "automation_id": "15",
                "name": "Edit",
                "control_type": "Edit",
                "value": "merhaba",
            }
        ),
        "terminal.execute": ok(
            exit_code=0,
            stdout="MAIL\r\nIPv4 Address. . . . . . . . . . . : 192.168.1.50\r\n",
            stderr="",
            duration_ms=12,
        ),
    }


#: Every device capability a fully-successful wake sequence needs, answered so that each
#: read-back VERIFIES. Tests override one key at a time to make a single step fail.
def happy_device_results() -> dict[str, DeviceRunResult]:
    return {
        "desktop.alarm_arm": ok(armed=True),
        "desktop.alarm_disarm": ok(disarmed=True, was_armed=True),
        "desktop.display_wake": ok(display_wake_requested=True, observed_state="on"),
        "desktop.display_off": ok(display_off=True, observed_state="off"),
        "desktop.alarm_start": ok(started=True),
        "desktop.alarm_stop": ok(stopped=True),
        "desktop.play_audio": ok(played=True, duration_ms=2500),
        "browser.session_open": ok(opened=True),
        "browser.media_play": ok(playing=True, verified=True, current_time_s=1.2),
        "browser.media_volume": ok(applied=True, level_to=0.6),
        "browser.media_status": ok(present=True, playing=True, ended=False),
        "browser.media_stop": ok(stopped=True, was_playing=True),
        **happy_operator_device_results(),
    }


class ManualClock:
    """A clock the test moves by hand. Every decision the alarm service makes is derived
    from stored timestamps compared to a supplied ``now``, so this is enough to drive a
    whole wake sequence, a greeting and a completion without sleeping once."""

    def __init__(self, start: datetime = BASE_NOW) -> None:
        self.now = start

    def advance(self, seconds: float) -> datetime:
        from datetime import timedelta

        self.now = self.now + timedelta(seconds=seconds)
        return self.now


__all__ = [
    "ALL_TABLES",
    "BASE_NOW",
    "FakeDeviceAction",
    "ManualClock",
    "build_session_factory",
    "failed",
    "happy_device_results",
    "happy_operator_device_results",
    "ok",
    "refused",
]
