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
from app.notifications.models import NotificationRow
from app.routines.dispatch import DeviceRunResult
from app.routines.models import Routine, RoutineFiring

ALL_TABLES = [
    WakeAlarm.__table__,
    AmbientPolicyRow.__table__,
    Routine.__table__,
    RoutineFiring.__table__,
    ActivityEventRow.__table__,
    # B20 req 233: the wake path writes the unspoken greeting/briefing here when it cannot
    # be said aloud, so the table has to exist for the sequence to be exercised at all.
    NotificationRow.__table__,
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


#: A valid 1x1 PNG (67 bytes), base64 - what a device's ``screen.capture`` returns in
#: ``png_base64``, at the smallest size that is still an image a decoder accepts.
ONE_PIXEL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


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
    "image": "notepad.exe",
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
def _observed_window(payload: dict[str, Any]) -> dict[str, Any]:
    return {**_OPERATOR_WINDOW, "window_id": str(payload.get("window_id") or window_id(1))}


def _key_result(payload: dict[str, Any]) -> DeviceRunResult:
    return ok(
        key=payload.get("key"),
        window_id=payload.get("window_id"),
        observed={"window": _observed_window(payload)},
    )


def _shortcut_result(payload: dict[str, Any]) -> DeviceRunResult:
    return ok(
        keys=list(payload.get("keys") or []),
        window_id=payload.get("window_id"),
        observed={"window": _observed_window(payload)},
    )


def _pointer_result(payload: dict[str, Any]) -> DeviceRunResult:
    """Window space maps onto a fixed fake rect at (100, 100), the way the companion adds
    the window's rect to window-space coordinates; the cursor is read back exactly there."""
    x, y = int(payload.get("x") or 0), int(payload.get("y") or 0)
    if payload.get("space") == "screen":
        screen_x, screen_y = x, y
    else:
        screen_x, screen_y = 100 + x, 100 + y
    result: dict[str, Any] = {
        "x": x,
        "y": y,
        "space": payload.get("space") or "window",
        "screen_x": screen_x,
        "screen_y": screen_y,
        "window_id": payload.get("window_id"),
        "observed": {
            "cursor": {"x": screen_x, "y": screen_y},
            "window": _observed_window(payload),
        },
    }
    if "delta" in payload:
        result["delta"] = payload["delta"]
    return ok(**result)


def _ui_invoke_result(payload: dict[str, Any]) -> DeviceRunResult:
    name = str(payload.get("name") or payload.get("automation_id") or "Tamam")
    element = {"automation_id": "", "name": name, "control_type": "Button", "enabled": True}
    return ok(
        invoked=True,
        method="Invoke",
        element=element,
        window_id=payload.get("window_id"),
        observed={
            "element": None,
            "element_present": False,
            "window": _observed_window(payload),
        },
    )


def _ui_set_value_result(payload: dict[str, Any]) -> DeviceRunResult:
    value = str(payload.get("value") or "")
    return ok(
        element={"automation_id": "15", "name": "Metin Düzenleyici", "control_type": "Edit"},
        observed_value=value,
        window_id=payload.get("window_id"),
        observed={"value": value, "window": _observed_window(payload)},
    )


def _ui_select_result(payload: dict[str, Any]) -> DeviceRunResult:
    item = str(payload.get("item") or "")
    return ok(
        element={"automation_id": "", "name": "Liste", "control_type": "List"},
        selected=item,
        window_id=payload.get("window_id"),
        observed={"selected": [item], "window": _observed_window(payload)},
    )


def _window_move_result(payload: dict[str, Any]) -> DeviceRunResult:
    return ok(
        window={
            **_OPERATOR_WINDOW,
            "window_id": str(payload.get("window_id") or window_id(1)),
            "rect": {"x": payload.get("x"), "y": payload.get("y"), "width": 800, "height": 600},
        }
    )


def _window_resize_result(payload: dict[str, Any]) -> DeviceRunResult:
    return ok(
        window={
            **_OPERATOR_WINDOW,
            "window_id": str(payload.get("window_id") or window_id(1)),
            "rect": {
                "x": 100,
                "y": 100,
                "width": payload.get("width"),
                "height": payload.get("height"),
            },
        }
    )


#: The processes the fake desktop runs: one Notepad, always; a process.stop marks its image
#: stopped so the observe step after it sees none left (a fake that kept listing it would
#: turn every stop into a postcondition failure - and one that never listed it would prove
#: nothing about the stop).
_STOPPED_IMAGES: set[str] = set()


def _process_list_result(payload: dict[str, Any]) -> DeviceRunResult:
    wanted = str(payload.get("name") or "").lower()
    running = [
        {"pid": 4242, "image": "notepad.exe", "name": "notepad", "window_count": 1},
        {"pid": 9088, "image": "chrome.exe", "name": "chrome", "window_count": 2},
    ]
    running = [p for p in running if p["image"] not in _STOPPED_IMAGES]
    if wanted:
        running = [p for p in running if p["image"] == wanted or p["name"] == wanted]
    return ok(processes=running, observed={"count": len(running)})


def _process_stop_result(payload: dict[str, Any]) -> DeviceRunResult:
    name = str(payload.get("name") or "")
    _STOPPED_IMAGES.add(name.lower())
    return ok(stopped=True, name=name, method="wm_close")


def _service_status_result(payload: dict[str, Any]) -> DeviceRunResult:
    return ok(name=payload.get("name"), state="Running", start_mode="Auto")


def _service_restart_result(payload: dict[str, Any]) -> DeviceRunResult:
    return ok(restarted=True, name=payload.get("name"), state="Running")


def _terminal_result(payload: dict[str, Any]) -> DeviceRunResult:
    """``whoami`` answers DOMAIN\\user (B30 req 118); anything else the static line pair
    the hostname and IP postconditions both accept (see the note above)."""
    if str(payload.get("command") or "").strip().lower() == "whoami":
        return ok(exit_code=0, stdout="mail\\alp\r\n", stderr="", duration_ms=9)
    return ok(
        exit_code=0,
        stdout="MAIL\r\nIPv4 Address. . . . . . . . . . . : 192.168.1.50\r\n",
        stderr="",
        duration_ms=12,
    )


def _activate_result(payload: dict[str, Any]) -> DeviceRunResult:
    """Echoes back the requested ``window_id`` (M19: a plan that resolved the PREVIOUS
    window's id must see THAT id come back foreground, not always the same one)."""
    requested = str(payload.get("window_id") or window_id(1))
    return ok(window={**_OPERATOR_WINDOW, "window_id": requested, "foreground": True})


def _pointer_stream_begin_result(payload: dict[str, Any]) -> DeviceRunResult:
    """ADR-0199 stage 2: echoes the session id it was asked to open and says it
    started - the plan's own postcondition (``plans.pointer_session_begin``) reads
    both back, so a stale/foreign echo does not read as success."""
    return ok(session=payload.get("session"), window_id=payload.get("window_id"), started=True)


def _pointer_stream_end_result(payload: dict[str, Any]) -> DeviceRunResult:
    return ok(session=payload.get("session"), stopped=True)


def happy_operator_device_results() -> dict[str, DeviceRunResult | Callable]:
    _STOPPED_IMAGES.clear()  # a fresh desktop per harness: nothing stopped yet
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
        # B28 req 92-98: the shapes docs/M19_DIGITAL_OPERATOR_SPEC.md §2 gives for the
        # input family - each echoes what it was asked and re-observes the foreground and
        # the cursor, exactly where the payload put it (the plan's postcondition reads
        # both back; a fake that answered "ok" with nothing would prove nothing).
        "keyboard.key": _key_result,
        "keyboard.shortcut": _shortcut_result,
        "pointer.move": _pointer_result,
        "pointer.click": _pointer_result,
        "pointer.double_click": _pointer_result,
        "pointer.right_click": _pointer_result,
        "pointer.scroll": _pointer_result,
        # B29 req 100/101/103: the shapes ``OperatorCapabilities.UiInvoke/UiSetValue/
        # UiSelect`` return. The invoke's element is GONE afterwards (a dialog button that
        # closed its dialog) - the plan's own postcondition reads before/after; the set
        # value and the selection are read back through the device's own patterns.
        "ui.invoke": _ui_invoke_result,
        "ui.set_value": _ui_set_value_result,
        "ui.select": _ui_select_result,
        # B30 req 82/84/85/119-122: the shapes the companion returns for geometry, app
        # close, processes and services - each re-observed as the plan's postcondition reads
        # it (a rect within 8 px, a closed flag, a process list, a service state).
        "window.move": _window_move_result,
        "window.resize": _window_resize_result,
        "app.close": ok(closed=True, method="wm_close", observed={"process_alive": False}),
        "process.list": _process_list_result,
        "process.stop": _process_stop_result,
        "service.status": _service_status_result,
        "service.restart": _service_restart_result,
        # B27 req 735: the shape docs/M19_DIGITAL_OPERATOR_SPEC.md §2 gives for
        # ``screen.capture`` - a real (one-pixel) PNG, so the tool's own decode-and-hash
        # runs on genuine bytes rather than on a sentinel string.
        "screen.capture": ok(width=1, height=1, png_base64=ONE_PIXEL_PNG_B64),
        # The shape a REAL ui.inspect of a Notepad window returns (captured from the
        # owner's device, 2026-09-09): the window root carries no value of its own and the
        # text sits one node down in the edit control. The flat root-with-a-value this
        # fixture used to return is a shape no window actually has, and it hid a
        # verification that looked in the wrong place (ADR-0100).
        "ui.inspect": ok(
            root={
                "name": "*Adsız - Not Defteri",
                "enabled": True,
                "children": [
                    {
                        "automation_id": "15",
                        "name": "Metin Düzenleyici",
                        "control_type": "Edit",
                        "value": "merhaba",
                    }
                ],
            }
        ),
        "terminal.execute": _terminal_result,
        # ADR-0199 stage 2: the pinch-mouse's pointer-streaming session.
        "pointer.stream_begin": _pointer_stream_begin_result,
        "pointer.stream_end": _pointer_stream_end_result,
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
        # ADR-0112: the owner's own media asks the device to SEARCH first, then plays
        # the first real watch URL the results carry. One YouTube result and one
        # decoy, so a corpus case proves the picker skipped the decoy rather than
        # taking whatever came back first.
        "browser.search": ok(
            results=[
                {"rank": 1, "url": "https://example.com/lyrics", "title": "sozler"},
                {
                    "rank": 2,
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "title": "Dogum Gunun Kutlu Olsun",
                },
            ]
        ),
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
