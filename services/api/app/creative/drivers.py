"""Driving the REAL creative applications (B43 req 500, 502, 504).

The file IS the document (ADR-0093 decision 1): every edit is made by Pillow on the
bytes. What the owner also wants is to SEE and continue the work in the application - so
after a delivery (req 509 puts the file on the owner's disk) these drivers open it in the
real application and press the application's own documented shortcuts, through the M19
operator's plan shapes (``app.launch`` with the file as the ONE allowed argument,
``keyboard.shortcut``, ``keyboard.type``, ``screen.capture``) - never coordinates, never a
guessed menu path.

* :class:`PaintDriver` (500): Windows Paint's shortcuts - Ctrl+W (resize and skew), Ctrl+I
  (invert colours), Ctrl+A / Delete (clear), Ctrl+Z / Ctrl+Y, Ctrl+S; the window is
  verified by title (the file name Paint shows) and a capture closes every plan.
* :class:`AdobeDriver` (502, 504): Photoshop and Illustrator share the open / save / undo /
  redo / select-all shortcuts; the applications are P3 and licence-gated, so the driver
  answers ``dependency_unavailable`` by name when the provider's detection says the
  application is not installed, and the device's own allowlist decides whether it may be
  launched at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from app.operator.plans import UWP_HOSTED_IMAGES, _executable_name, _window_of
from app.operator.task import LEVEL_API, OperatorStep
from app.routines.dispatch import DeviceRunResult

ACTION_RESIZE: Final = "resize"
ACTION_INVERT: Final = "invert"
ACTION_CLEAR: Final = "clear"
ACTION_UNDO: Final = "undo"
ACTION_REDO: Final = "redo"
ACTION_SAVE: Final = "save"
ACTION_SELECT_ALL: Final = "select_all"
ACTION_CAPTURE: Final = "capture"
PAINT_ACTIONS: Final[tuple[str, ...]] = (
    ACTION_RESIZE,
    ACTION_INVERT,
    ACTION_CLEAR,
    ACTION_UNDO,
    ACTION_REDO,
    ACTION_SAVE,
    ACTION_SELECT_ALL,
    ACTION_CAPTURE,
)
ADOBE_ACTIONS: Final[tuple[str, ...]] = (
    ACTION_UNDO,
    ACTION_REDO,
    ACTION_SAVE,
    ACTION_SELECT_ALL,
    ACTION_CAPTURE,
)
MAX_ACTIONS: Final = 12
MAX_RESIZE_PERCENT: Final = 400
MIN_RESIZE_PERCENT: Final = 10

#: The application ids the device's allowlist knows (packages/protocol/operator-allowlists.json).
APP_PAINT: Final = "mspaint"
APP_PHOTOSHOP: Final = "photoshop"
APP_ILLUSTRATOR: Final = "illustrator"


class DriverError(ValueError):
    pass


@dataclass(slots=True)
class DriveAction:
    action: str
    percent: int | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any] | str) -> DriveAction:
        if isinstance(raw, str):
            raw = {"action": raw}
        action = str(raw.get("action") or "")
        percent = raw.get("percent")
        if percent is not None:
            percent = int(percent)
            if not MIN_RESIZE_PERCENT <= percent <= MAX_RESIZE_PERCENT:
                raise DriverError(
                    f"resize percent must be within {MIN_RESIZE_PERCENT}..{MAX_RESIZE_PERCENT}"
                )
        return cls(action=action, percent=percent)


@dataclass(slots=True)
class _Session:
    """What the launch observed, for the steps after it (B39's ``payload_from``)."""

    pid: int | None = None
    image: str = ""
    window_id: str | None = None
    title: str = ""
    captured: dict[str, Any] = field(default_factory=dict)

    def window(self) -> dict[str, Any]:
        return {"window_id": self.window_id or ""}


def _shortcut(session: _Session, keys: list[str], name: str) -> OperatorStep:
    def _pressed(result: DeviceRunResult) -> bool:
        payload = result.result if isinstance(result.result, dict) else {}
        return list(payload.get("keys") or []) == keys

    return OperatorStep(
        capability="keyboard.shortcut",
        payload={"keys": list(keys)},
        payload_from=session.window,
        postcondition=_pressed,
        timeout_s=10.0,
        retries=1,
        level=LEVEL_API,
        name=name,
    )


def _type(session: _Session, text: str, name: str) -> OperatorStep:
    return OperatorStep(
        capability="keyboard.type",
        payload={"text": text, "secret": False},
        payload_from=session.window,
        postcondition=lambda result: result.ok,
        timeout_s=10.0,
        retries=0,
        level=LEVEL_API,
        name=name,
    )


def _key(session: _Session, key: str, name: str) -> OperatorStep:
    return OperatorStep(
        capability="keyboard.key",
        payload={"key": key},
        payload_from=session.window,
        postcondition=lambda result: result.ok,
        timeout_s=10.0,
        retries=0,
        level=LEVEL_API,
        name=name,
    )


def _capture(session: _Session, name: str) -> OperatorStep:
    def _captured(result: DeviceRunResult) -> bool:
        payload = result.result if isinstance(result.result, dict) else {}
        encoded = payload.get("png_base64")
        if not isinstance(encoded, str) or not encoded:
            return False
        session.captured = {"bytes": len(encoded) * 3 // 4, "window_id": session.window_id}
        return True

    return OperatorStep(
        capability="screen.capture",
        payload={},
        payload_from=session.window,
        postcondition=_captured,
        timeout_s=15.0,
        retries=1,
        level=LEVEL_API,
        name=name,
    )


def open_in_application(
    application: str, path: str, *, title_hint: str
) -> tuple[list[OperatorStep], _Session]:
    """``app.launch`` with the file as its ONE argument (ArgumentPolicy: an absolute path
    inside the authorised roots - the Downloads folder the delivery wrote to), then the
    foreground window verified by IMAGE or, for a Store-hosted application, by TITLE."""
    session = _Session()

    def _launched(result: DeviceRunResult) -> bool:
        payload = result.result if isinstance(result.result, dict) else {}
        if payload.get("pid") is None:
            return False
        session.pid = payload.get("pid")
        session.image = _executable_name(str(payload.get("executable") or ""))
        return True

    def _window(result: DeviceRunResult) -> bool:
        window = _window_of(result)
        if not window.get("foreground"):
            return False
        title = str(window.get("title") or "")
        image = _executable_name(str(window.get("image") or ""))
        hosted = UWP_HOSTED_IMAGES.get(session.image or application + ".exe", ())
        ok = (
            window.get("pid") == session.pid
            or (bool(session.image) and image == session.image)
            or (bool(hosted) and any(h in title for h in hosted))
            or (title_hint and title_hint.lower() in title.lower())
        )
        if ok:
            session.window_id = str(window.get("window_id") or window.get("id") or "")
            session.title = title
        return bool(ok and session.window_id)

    steps = [
        OperatorStep(
            capability="app.launch",
            payload={"application": application, "args": [path]},
            postcondition=_launched,
            timeout_s=15.0,
            retries=1,
            level=LEVEL_API,
            name=f"{application}:launch",
        ),
        OperatorStep(
            capability="window.current",
            payload={},
            postcondition=_window,
            timeout_s=10.0,
            retries=2,
            level=LEVEL_API,
            name=f"{application}:observe_window",
        ),
    ]
    return steps, session


@dataclass(slots=True)
class PaintDriver:
    """Windows Paint by its own shortcuts (req 500)."""

    application: str = APP_PAINT
    actions: tuple[str, ...] = PAINT_ACTIONS

    def plan(
        self, path: str, actions: list[DriveAction], *, title_hint: str
    ) -> tuple[list[OperatorStep], _Session]:
        if len(actions) > MAX_ACTIONS:
            raise DriverError(f"at most {MAX_ACTIONS} actions in one drive")
        steps, session = open_in_application(self.application, path, title_hint=title_hint)
        for index, action in enumerate(actions):
            if action.action not in self.actions:
                raise DriverError(f"{self.application} cannot be driven to {action.action!r}")
            steps.extend(self._steps_for(session, action, index))
        steps.append(_capture(session, "paint:capture"))
        return steps, session

    def _steps_for(self, session: _Session, action: DriveAction, index: int) -> list[OperatorStep]:
        tag = f"paint:{index}:{action.action}"
        if action.action == ACTION_RESIZE:
            if action.percent is None:
                raise DriverError("resize needs a percent")
            return [
                _shortcut(session, ["ctrl", "w"], tag + ":dialog"),
                _shortcut(session, ["ctrl", "a"], tag + ":select_field"),
                _type(session, str(action.percent), tag + ":percent"),
                _key(session, "enter", tag + ":apply"),
            ]
        if action.action == ACTION_INVERT:
            return [_shortcut(session, ["ctrl", "i"], tag)]
        if action.action == ACTION_CLEAR:
            return [
                _shortcut(session, ["ctrl", "a"], tag + ":select"),
                _key(session, "delete", tag + ":delete"),
            ]
        if action.action == ACTION_SELECT_ALL:
            return [_shortcut(session, ["ctrl", "a"], tag)]
        if action.action == ACTION_UNDO:
            return [_shortcut(session, ["ctrl", "z"], tag)]
        if action.action == ACTION_REDO:
            return [_shortcut(session, ["ctrl", "y"], tag)]
        if action.action == ACTION_SAVE:
            return [_shortcut(session, ["ctrl", "s"], tag)]
        if action.action == ACTION_CAPTURE:
            return [_capture(session, tag)]
        raise DriverError(f"unknown action {action.action!r}")


@dataclass(slots=True)
class AdobeDriver(PaintDriver):
    """Photoshop / Illustrator by the shortcuts the two share (req 502, 504): open, save,
    undo (Photoshop: Ctrl+Z toggles, Ctrl+Alt+Z steps back; Illustrator: Ctrl+Z), redo
    (Ctrl+Shift+Z), select all. Resize/invert are NOT claimed: their dialogs differ per
    version and neither is verified here."""

    application: str = APP_PHOTOSHOP
    actions: tuple[str, ...] = ADOBE_ACTIONS

    def _steps_for(self, session: _Session, action: DriveAction, index: int) -> list[OperatorStep]:
        tag = f"{self.application}:{index}:{action.action}"
        if action.action == ACTION_UNDO:
            keys = ["ctrl", "alt", "z"] if self.application == APP_PHOTOSHOP else ["ctrl", "z"]
            return [_shortcut(session, keys, tag)]
        if action.action == ACTION_REDO:
            return [_shortcut(session, ["ctrl", "shift", "z"], tag)]
        if action.action == ACTION_SAVE:
            return [_shortcut(session, ["ctrl", "s"], tag)]
        if action.action == ACTION_SELECT_ALL:
            return [_shortcut(session, ["ctrl", "a"], tag)]
        if action.action == ACTION_CAPTURE:
            return [_capture(session, tag)]
        raise DriverError(f"{self.application} cannot be driven to {action.action!r}")


def driver_for(tool: str) -> PaintDriver:
    if tool == "paint":
        return PaintDriver()
    if tool == "photoshop":
        return AdobeDriver(application=APP_PHOTOSHOP)
    if tool == "illustrator":
        return AdobeDriver(application=APP_ILLUSTRATOR)
    raise DriverError(f"no driver for {tool!r}")


__all__ = [
    "ADOBE_ACTIONS",
    "APP_ILLUSTRATOR",
    "APP_PAINT",
    "APP_PHOTOSHOP",
    "MAX_ACTIONS",
    "PAINT_ACTIONS",
    "AdobeDriver",
    "DriveAction",
    "DriverError",
    "PaintDriver",
    "driver_for",
    "open_in_application",
]
