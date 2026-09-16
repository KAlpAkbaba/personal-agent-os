"""Deterministic plans for the operator voice tools (docs/M19_DIGITAL_OPERATOR_SPEC.md §4).

Every function here returns a fixed list of :class:`app.operator.task.OperatorStep` —
never a re-plan mid-flight (this milestone's scope, spec §7). A postcondition closes over
a small ``dict`` a preceding step's own callback fills in, when a later step's check needs
something an earlier step observed (e.g. the pid ``app.launch`` returned): the loop in
``app.operator.task`` only ever sees one step's own :class:`DeviceRunResult`, and this is
how a plan still verifies something that spans two of them.
"""

from __future__ import annotations

import re
from typing import Any, Final

from app.operator import allowlists as _allowlists
from app.operator.task import (
    LEVEL_API,
    LEVEL_KEYBOARD,
    LEVEL_POINTER,
    LEVEL_UI_AUTOMATION,
    LEVEL_VISUAL,
    OperatorStep,
)
from app.routines.dispatch import DeviceRunResult

# ------------------------------------------------------------- B28: input vocabulary

#: docs/M19_DIGITAL_OPERATOR_SPEC.md §2's ``keyboard.key`` names - the SAME list the
#: companion's ``KeyMap.Named`` accepts (``InputSynthesizer.cs``). A name here that the
#: device does not know is refused there as ``validation_error`` after a window was
#: already activated; ``tests/unit/test_operator_input.py`` reads the other side's source
#: and fails if the two ever drift.
KEY_NAMES: Final[tuple[str, ...]] = (
    "enter",
    "escape",
    "tab",
    "backspace",
    "delete",
    "insert",
    "home",
    "end",
    "pageup",
    "pagedown",
    "up",
    "down",
    "left",
    "right",
    "space",
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
)
#: ``keyboard.shortcut``'s modifiers, as the companion's ``KeyMap.Modifiers`` names them.
MODIFIER_NAMES: Final[tuple[str, ...]] = ("ctrl", "alt", "shift")
#: A chord is 2..4 names: at least one modifier, then ONE key (a named key, a letter or a
#: digit) - the companion's ``KeyMap.ValidateShortcut`` rule, held here so a bad chord is
#: refused before a window is activated for it.
SHORTCUT_MIN_KEYS: Final = 2
SHORTCUT_MAX_KEYS: Final = 4

POINTER_ACTIONS: Final[tuple[str, ...]] = ("move", "click", "double_click", "right_click", "scroll")
POINTER_SPACES: Final[tuple[str, ...]] = ("window", "screen")
#: ``pointer.scroll``'s ``delta`` bound, the companion's own (±50 notches).
SCROLL_MAX_DELTA: Final = 50


def valid_key(key: str) -> bool:
    return key in KEY_NAMES


def valid_shortcut(keys: list[str]) -> bool:
    """The companion's rule, restated once: modifiers then exactly one key."""
    if not SHORTCUT_MIN_KEYS <= len(keys) <= SHORTCUT_MAX_KEYS:
        return False
    modifiers = [k for k in keys if k in MODIFIER_NAMES]
    plain = [k for k in keys if k not in MODIFIER_NAMES]
    if not modifiers or len(plain) != 1:
        return False
    key = plain[0]
    return key in KEY_NAMES or (len(key) == 1 and key.isalnum())


# ------------------------------------------------------------------ app allowlist

#: docs/M19_DIGITAL_OPERATOR_SPEC.md §2 ``app.launch``'s allowlisted names. Since B30
#: (req 117) read from ``packages/protocol/operator-allowlists.json`` - the ONE place both
#: halves' lists are written - rather than restated here; the voice tool refuses anything
#: else by naming this list, never by guessing a path.
APP_ALLOWLIST: Final[tuple[str, ...]] = _allowlists.APP_IDS

#: Turkish alias phrase (lower-cased; an apostrophe suffix like "chrome'u" is cut by the
#: caller before matching) -> the canonical allowlisted id. Longer phrases first so
#: "google chrome" is not shadowed by the bare "chrome" entry below it - the contract
#: module sorts them that way.
_APP_ALIAS_PHRASES: Final[tuple[tuple[str, str], ...]] = _allowlists.APP_ALIAS_PHRASES


def resolve_app_alias(tokens: tuple[str, ...]) -> str | None:
    """The allowlisted app id ``tokens`` (already Turkish-casefolded, punctuation
    stripped — ``app.voice.intents.normalize_transcript``'s tokens) names, or ``None``.

    An apostrophe suffix ("chrome'u", "powershell'i") is cut before matching, so
    "chrome'u aç" reads as "chrome aç".
    """
    cleaned = " ".join(tok.split("'", 1)[0] for tok in tokens)
    for phrase, canonical in _APP_ALIAS_PHRASES:
        if phrase in cleaned:
            return canonical
    return None


#: B39 (req 123): images whose windows are hosted by ApplicationFrameHost.exe, and the
#: titles those windows carry in this locale (see ``open_application``).
UWP_HOSTED_IMAGES: Final[dict[str, tuple[str, ...]]] = {
    "systemsettings.exe": ("Ayarlar", "Settings"),
}

# ------------------------------------------------------------------------- plans


def _window_of(result: DeviceRunResult) -> dict[str, Any]:
    window = result.result.get("window") if isinstance(result.result, dict) else None
    return window if isinstance(window, dict) else {}


def _executable_name(path: str) -> str:
    """The bare executable file name, case-folded: "C:\\...\\chrome.exe" -> "chrome.exe".

    Split on both separators by hand rather than through ``pathlib``: this compares a
    WINDOWS path reported by the device against a WINDOWS image name, and the server runs
    on Linux, where ``PurePath`` would treat the backslashes as ordinary characters.
    """
    return path.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def _any_value_ends_with(node: Any, text: str) -> bool:
    """True when any node of a ``ui.inspect`` subtree has a ``value`` ending with ``text``.

    The device's own tree, walked as it was returned. It is already bounded by the
    companion (``MaxDepth``/``MaxNodes``), so this needs no depth limit of its own — but
    it must not assume a shape: ``children`` may be absent, null, or not a list.
    """
    if not isinstance(node, dict):
        return False
    if str(node.get("value") or "").endswith(text):
        return True
    children = node.get("children")
    if not isinstance(children, list):
        return False
    return any(_any_value_ends_with(child, text) for child in children)


def open_application(name: str) -> list[OperatorStep]:
    """``app.launch`` then ``window.current`` (spec §4): postcondition, a foreground
    window belonging to the application that was launched.

    Deliberately NOT "a window of the pid ``app.launch`` returned". Chrome, Edge, Explorer
    and anything else that launches through a broker hand the request to an instance that
    is already running and exit at once, so the pid that starts is not the pid that owns
    the window. Measured on the owner's device (2026-09-09 19:58): ``app.launch`` for
    chrome returned pid 36836 with ``process_alive: false`` and ``window_appeared: false``,
    while the foreground window a second later was chrome.exe under pid 9088. The old
    postcondition compared those two pids, failed, and took the whole open_application task
    down with it — with Chrome open and in front of the owner.

    That failure was not cosmetic. ``OperatorService`` writes window focus only for steps
    whose postcondition PASSED, so the focus stack never learned about Chrome; the owner's
    next sentence, "youtube.com", was typed into the Notepad they had used minutes earlier,
    twice, and reported as done (ADR-0101).
    """
    launched: dict[str, Any] = {}

    def _launch_ok(result: DeviceRunResult) -> bool:
        payload = result.result if isinstance(result.result, dict) else {}
        pid = payload.get("pid")
        if pid is None:
            return False
        launched["pid"] = pid
        launched["image"] = _executable_name(str(payload.get("executable") or ""))
        return True

    def _window_ok(result: DeviceRunResult) -> bool:
        window = _window_of(result)
        if not window.get("foreground"):
            return False
        if window.get("pid") == launched.get("pid"):
            return True
        image = launched.get("image") or ""
        if image in UWP_HOSTED_IMAGES:
            # B39 (req 123): a Store/UWP application's window belongs to
            # ApplicationFrameHost.exe, never to the launched image - it is known by
            # its title, the one identity such a window offers.
            return str(window.get("title") or "") in UWP_HOSTED_IMAGES[image]
        return bool(image) and _executable_name(str(window.get("image") or "")) == image

    return [
        OperatorStep(
            capability="app.launch",
            payload={"application": name},
            postcondition=_launch_ok,
            timeout_s=15.0,
            retries=1,
            level=LEVEL_API,
            name="open_application:launch",
        ),
        OperatorStep(
            capability="window.current",
            payload={},
            postcondition=_window_ok,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="open_application:observe_window",
        ),
    ]


def close_window(window_id: str) -> list[OperatorStep]:
    """``window.close`` then ``window.list`` (spec §4): postcondition, the window is
    gone. A modal in either result fails the task ``modal_open`` (checked generically by
    ``app.operator.task.run_task`` for every step, not just this one)."""

    def _gone(result: DeviceRunResult) -> bool:
        windows = result.result.get("windows") if isinstance(result.result, dict) else None
        if not isinstance(windows, list):
            return False
        return not any(isinstance(w, dict) and w.get("window_id") == window_id for w in windows)

    return [
        OperatorStep(
            capability="window.close",
            payload={"window_id": window_id},
            postcondition=None,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="close_window:close",
        ),
        OperatorStep(
            capability="window.list",
            payload={},
            postcondition=_gone,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="close_window:observe",
        ),
    ]


_STATE_BY_CAPABILITY: Final[dict[str, str]] = {
    "window.maximize": "maximized",
    "window.minimize": "minimized",
    "window.restore": "normal",
}


def _window_state_plan(capability: str, window_id: str) -> list[OperatorStep]:
    wanted = _STATE_BY_CAPABILITY[capability]

    def _state_ok(result: DeviceRunResult) -> bool:
        return _window_of(result).get("state") == wanted

    return [
        OperatorStep(
            capability=capability,
            payload={"window_id": window_id},
            postcondition=_state_ok,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name=f"{capability}:observe",
        )
    ]


def maximize_window(window_id: str) -> list[OperatorStep]:
    return _window_state_plan("window.maximize", window_id)


def minimize_window(window_id: str) -> list[OperatorStep]:
    return _window_state_plan("window.minimize", window_id)


def restore_window(window_id: str) -> list[OperatorStep]:
    return _window_state_plan("window.restore", window_id)


#: The companion's own tolerance for a move/resize read-back (``OperatorCapabilities.
#: RectTolerance``, spec §2: "within 8 px").
RECT_TOLERANCE_PX: Final = 8


def _rect_of(result: DeviceRunResult) -> dict[str, Any]:
    rect = _window_of(result).get("rect")
    return rect if isinstance(rect, dict) else {}


def move_window(window_id: str, x: int, y: int) -> list[OperatorStep]:
    """B30 req 84: ``window.move`` — postcondition, the re-observed rect is within 8 px."""

    def _moved(result: DeviceRunResult) -> bool:
        rect = _rect_of(result)
        try:
            return (
                abs(int(rect["x"]) - int(x)) <= RECT_TOLERANCE_PX
                and abs(int(rect["y"]) - int(y)) <= RECT_TOLERANCE_PX
            )
        except (KeyError, TypeError, ValueError):
            return False

    return [
        OperatorStep(
            capability="window.move",
            payload={"window_id": window_id, "x": int(x), "y": int(y)},
            postcondition=_moved,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="move_window",
        )
    ]


def resize_window(window_id: str, width: int, height: int) -> list[OperatorStep]:
    """B30 req 85: ``window.resize`` — postcondition, the re-observed size is within 8 px."""

    def _resized(result: DeviceRunResult) -> bool:
        rect = _rect_of(result)
        try:
            return (
                abs(int(rect["width"]) - int(width)) <= RECT_TOLERANCE_PX
                and abs(int(rect["height"]) - int(height)) <= RECT_TOLERANCE_PX
            )
        except (KeyError, TypeError, ValueError):
            return False

    return [
        OperatorStep(
            capability="window.resize",
            payload={"window_id": window_id, "width": int(width), "height": int(height)},
            postcondition=_resized,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="resize_window",
        )
    ]


def close_app(window_id: str, *, force: bool = False) -> list[OperatorStep]:
    """B30 req 82: ``app.close`` (WM_CLOSE first; terminate only with ``force``) then
    ``window.list`` — postcondition, the window is gone. A save dialog is reported
    (``modal``) and fails the task ``modal_open``, exactly as ``close_window`` does."""

    def _closed(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        return body.get("closed") is True

    def _gone(result: DeviceRunResult) -> bool:
        windows = result.result.get("windows") if isinstance(result.result, dict) else None
        if not isinstance(windows, list):
            return False
        return not any(isinstance(w, dict) and w.get("window_id") == window_id for w in windows)

    payload: dict[str, Any] = {"window_id": window_id}
    if force:
        payload["force"] = True
    return [
        OperatorStep(
            capability="app.close",
            payload=payload,
            postcondition=_closed,
            timeout_s=15.0,
            retries=0,
            level=LEVEL_API,
            name="close_application:close",
        ),
        OperatorStep(
            capability="window.list",
            payload={},
            postcondition=_gone,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="close_application:observe",
        ),
    ]


# ------------------------------------------------------- B30: processes and services


def process_list(name: str | None = None) -> list[OperatorStep]:
    """B30 req 119: ``process.list`` (optionally by image/name) — a read; postcondition,
    the device answered with a list at all."""

    def _listed(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        return isinstance(body.get("processes"), list)

    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    return [
        OperatorStep(
            capability="process.list",
            payload=payload,
            postcondition=_listed,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="process_list",
        )
    ]


def process_stop(name: str, *, force: bool = False) -> list[OperatorStep]:
    """B30 req 120: ``process.stop`` for an image the policy allows (checked by the tool
    against ``packages/protocol/operator-allowlists.json`` before this plan is built, and
    again by the device) then ``process.list`` — postcondition, no process of that image
    is left."""
    if not _allowlists.image_stoppable(name):
        raise ValueError(f"'{name}' is not an image the stop policy allows")
    bare = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()

    def _stopped(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        return body.get("stopped") is True

    def _none_left(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        processes = body.get("processes")
        if not isinstance(processes, list):
            return False
        return not any(
            isinstance(p, dict) and str(p.get("image") or p.get("name") or "").lower() == bare
            for p in processes
        )

    payload: dict[str, Any] = {"name": bare}
    if force:
        payload["force"] = True
    return [
        OperatorStep(
            capability="process.stop",
            payload=payload,
            postcondition=_stopped,
            timeout_s=15.0,
            retries=0,
            level=LEVEL_API,
            name="process_stop:stop",
        ),
        OperatorStep(
            capability="process.list",
            payload={"name": bare},
            postcondition=_none_left,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="process_stop:observe",
        ),
    ]


def service_status(name: str) -> list[OperatorStep]:
    """B30 req 121: ``service.status`` — a read; postcondition, the device named a state."""

    def _has_state(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        return isinstance(body.get("state"), str) and bool(body.get("state"))

    return [
        OperatorStep(
            capability="service.status",
            payload={"name": name},
            postcondition=_has_state,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="service_status",
        )
    ]


def service_restart(name: str) -> list[OperatorStep]:
    """B30 req 122: ``service.restart`` for a service the policy names, then
    ``service.status`` — postcondition, the service is running again. The device refuses
    an unelevated companion with ``permission_denied``; the task then fails with that
    class and the receipt says so (UAC is the owner's, never bypassed)."""
    if not _allowlists.service_restartable(name):
        raise ValueError(f"'{name}' is not a service the restart policy allows")

    def _restarted(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        return body.get("restarted") is True

    def _running(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        return str(body.get("state") or "").lower() == "running"

    return [
        OperatorStep(
            capability="service.restart",
            payload={"name": name},
            postcondition=_restarted,
            timeout_s=30.0,
            retries=0,
            level=LEVEL_API,
            name="service_restart:restart",
        ),
        OperatorStep(
            capability="service.status",
            payload={"name": name},
            postcondition=_running,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="service_restart:observe",
        ),
    ]


def activate_window(window_id: str) -> list[OperatorStep]:
    """``window.activate`` — postcondition: it is the foreground window."""

    def _foreground_ok(result: DeviceRunResult) -> bool:
        window = _window_of(result)
        return bool(window.get("foreground") and window.get("window_id") == window_id)

    return [
        OperatorStep(
            capability="window.activate",
            payload={"window_id": window_id},
            postcondition=_foreground_ok,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="activate_window",
        )
    ]


def previous_window(window_id: str) -> list[OperatorStep]:
    """Activate the previous window id from the focus stack (the caller resolves it
    through ``app.operator.focus.previous`` before building this plan)."""
    return activate_window(window_id)


def _activate_step(window_id: str, name: str) -> OperatorStep:
    """The OBSERVE before an input step: the target window in front, verified."""

    def _activated(result: DeviceRunResult) -> bool:
        return bool(_window_of(result).get("foreground"))

    return OperatorStep(
        capability="window.activate",
        payload={"window_id": window_id},
        postcondition=_activated,
        timeout_s=10.0,
        retries=1,
        level=LEVEL_API,
        name=name,
    )


def _landed_in(window_id: str, result: DeviceRunResult) -> bool:
    """The device's own read-back after an input: the foreground is still the target.

    ``keyboard.*`` and ``pointer.*`` answer with ``observed.window`` (the foreground at
    the moment after acting). The companion's focus guard already refused to SEND when the
    foreground had moved; this is the other half - the input that was sent went where it
    was meant to, read back rather than assumed.
    """
    payload = result.result if isinstance(result.result, dict) else {}
    observed = payload.get("observed") if isinstance(payload.get("observed"), dict) else {}
    window = observed.get("window") if isinstance(observed.get("window"), dict) else {}
    return window.get("window_id") == window_id


def press_key(window_id: str, key: str) -> list[OperatorStep]:
    """B28 req 92: ``window.activate`` -> ``keyboard.key`` (level ``keyboard``);
    postcondition, the device echoes the key and the foreground is still the target."""
    if not valid_key(key):
        raise ValueError(f"'{key}' is not a key keyboard.key accepts")

    def _pressed(result: DeviceRunResult) -> bool:
        payload = result.result if isinstance(result.result, dict) else {}
        return payload.get("key") == key and _landed_in(window_id, result)

    return [
        _activate_step(window_id, "press_key:activate"),
        OperatorStep(
            capability="keyboard.key",
            payload={"window_id": window_id, "key": key},
            postcondition=_pressed,
            timeout_s=10.0,
            retries=0,
            level=LEVEL_KEYBOARD,
            name="press_key:key",
        ),
    ]


def press_shortcut(window_id: str, keys: list[str]) -> list[OperatorStep]:
    """B28 req 93: ``window.activate`` -> ``keyboard.shortcut`` (level ``keyboard``).

    ``retries=0`` on the input step, like ``press_key``: a chord that was sent and not
    read back is not sent again - Ctrl+S twice is one save, Ctrl+Z twice is two undos.
    """
    if not valid_shortcut(keys):
        raise ValueError(f"{keys!r} is not a chord keyboard.shortcut accepts")

    def _pressed(result: DeviceRunResult) -> bool:
        payload = result.result if isinstance(result.result, dict) else {}
        return list(payload.get("keys") or []) == list(keys) and _landed_in(window_id, result)

    return [
        _activate_step(window_id, "press_shortcut:activate"),
        OperatorStep(
            capability="keyboard.shortcut",
            payload={"window_id": window_id, "keys": list(keys)},
            postcondition=_pressed,
            timeout_s=10.0,
            retries=0,
            level=LEVEL_KEYBOARD,
            name="press_shortcut:shortcut",
        ),
    ]


def pointer(
    window_id: str,
    action: str,
    *,
    x: int,
    y: int,
    space: str = "window",
    delta: int | None = None,
) -> list[OperatorStep]:
    """B28 req 94-98: ``window.activate`` -> ``pointer.<action>`` (level ``pointer``);
    postcondition, the device's re-observed cursor is within two pixels of where it was
    asked to go (the lab's own tolerance) and the foreground is still the target."""
    if action not in POINTER_ACTIONS:
        raise ValueError(f"'{action}' is not a pointer action")
    if space not in POINTER_SPACES:
        raise ValueError(f"'{space}' is not a pointer space")
    if action == "scroll":
        if delta is None or delta == 0 or abs(delta) > SCROLL_MAX_DELTA:
            raise ValueError("pointer.scroll needs a non-zero delta within ±50")
    payload: dict[str, Any] = {"window_id": window_id, "x": int(x), "y": int(y), "space": space}
    if action == "scroll":
        payload["delta"] = int(delta or 0)

    def _landed(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        observed = body.get("observed") if isinstance(body.get("observed"), dict) else {}
        cursor = observed.get("cursor") if isinstance(observed.get("cursor"), dict) else None
        if cursor is None or "screen_x" not in body or "screen_y" not in body:
            return False
        try:
            off = max(
                abs(int(cursor["x"]) - int(body["screen_x"])),
                abs(int(cursor["y"]) - int(body["screen_y"])),
            )
        except (KeyError, TypeError, ValueError):
            return False
        return off <= 2 and _landed_in(window_id, result)

    return [
        _activate_step(window_id, f"pointer_{action}:activate"),
        OperatorStep(
            capability=f"pointer.{action}",
            payload=payload,
            postcondition=_landed,
            timeout_s=10.0,
            retries=0,
            level=LEVEL_POINTER,
            name=f"pointer_{action}:{action}",
        ),
    ]


# ------------------------------------------------------------- B29: UI Automation

UI_ACTIONS: Final[tuple[str, ...]] = ("invoke", "set_value", "select")
#: What an ``invoke`` is expected to have done, checked by an INDEPENDENT read after it:
#: the dialog window is gone; the element is gone from the tree; an element of that name
#: is now present; the document's value ends with a text. ``None`` falls back to the
#: device's own before/after description of the element (gone or changed).
UI_EXPECTATIONS: Final[tuple[str, ...]] = (
    "window_gone",
    "element_gone",
    "element_present",
    "value_ends_with",
)


def _tree_nodes(root: Any) -> list[dict[str, Any]]:
    """Every node of a ``ui.inspect`` subtree, as the device returned it."""
    out: list[dict[str, Any]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        out.append(node)
        children = node.get("children")
        if isinstance(children, list):
            stack.extend(children)
    return out


def _inspect_root(result: DeviceRunResult) -> Any:
    return result.result.get("root") if isinstance(result.result, dict) else None


def _node_named(root: Any, name: str) -> bool:
    return any(str(node.get("name") or "") == name for node in _tree_nodes(root))


def _element_changed(before: Any, after: Any) -> bool:
    """The device's own description of the element before and after an invoke differs,
    or the element is gone - the weakest observable consequence of a button that did
    something."""
    if after is None:
        return True
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    watched = ("name", "toggle_state", "enabled", "selected", "value")
    return any(before.get(key) != after.get(key) for key in watched)


def ui_invoke(
    window_id: str,
    query: dict[str, str],
    *,
    expect: str | None = None,
    expect_arg: str | None = None,
) -> list[OperatorStep]:
    """B29 req 100: ``window.activate`` -> ``ui.invoke`` -> an independent read.

    The invoke step's own postcondition reads the device's before/after description of
    the element: unchanged and still present means the button did nothing observable,
    and that is a failure, not a success (req 111). When the caller says what to expect,
    a third step reads it back from a source the invoke did not write: ``window.list`` for
    a dialog that should have closed, ``ui.inspect`` for the tree.
    """
    if expect is not None and expect not in UI_EXPECTATIONS:
        raise ValueError(f"'{expect}' is not a ui expectation")
    if expect in ("element_present", "value_ends_with") and not expect_arg:
        raise ValueError(f"'{expect}' needs a value")
    before_after: dict[str, Any] = {}

    def _invoked(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        if body.get("invoked") is not True:
            return False
        observed = body.get("observed") if isinstance(body.get("observed"), dict) else {}
        before = body.get("element")
        after = observed.get("element") if observed.get("element_present") else None
        before_after["name"] = str(before.get("name") or "") if isinstance(before, dict) else ""
        return _element_changed(before, after)

    steps = [
        _activate_step(window_id, "ui_invoke:activate"),
        OperatorStep(
            capability="ui.invoke",
            payload={"window_id": window_id, **query},
            postcondition=_invoked,
            timeout_s=15.0,
            retries=0,
            level=LEVEL_UI_AUTOMATION,
            name="ui_invoke:invoke",
        ),
    ]
    if expect == "window_gone":

        def _gone(result: DeviceRunResult) -> bool:
            windows = result.result.get("windows") if isinstance(result.result, dict) else None
            if not isinstance(windows, list):
                return False
            return not any(isinstance(w, dict) and w.get("window_id") == window_id for w in windows)

        steps.append(
            OperatorStep(
                capability="window.list",
                payload={},
                postcondition=_gone,
                timeout_s=10.0,
                retries=1,
                level=LEVEL_API,
                name="ui_invoke:verify_window_gone",
            )
        )
    elif expect is not None:

        def _tree_ok(result: DeviceRunResult) -> bool:
            root = _inspect_root(result)
            if root is None:
                return False
            if expect == "element_gone":
                name = before_after.get("name") or query.get("name") or ""
                return bool(name) and not _node_named(root, name)
            if expect == "element_present":
                return _node_named(root, str(expect_arg))
            return _any_value_ends_with(root, str(expect_arg))

        steps.append(
            OperatorStep(
                capability="ui.inspect",
                payload={"window_id": window_id},
                postcondition=_tree_ok,
                timeout_s=10.0,
                retries=1,
                level=LEVEL_UI_AUTOMATION,
                name=f"ui_invoke:verify_{expect}",
            )
        )
    return steps


def ui_set_value(window_id: str, query: dict[str, str], value: str) -> list[OperatorStep]:
    """B29 req 101: ``window.activate`` -> ``ui.set_value`` (the device reads the value
    back through the Value pattern) -> ``ui.inspect`` on the SAME query, an independent
    read that must end with the value. The ``secret`` flag rides along as for
    ``keyboard.type`` (req 109)."""
    from app.voice.intents import contains_secret_reference

    secret = bool(contains_secret_reference(value))

    def _set(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        return str(body.get("observed_value") or "") == value

    def _read_back(result: DeviceRunResult) -> bool:
        return _any_value_ends_with(_inspect_root(result), value)

    return [
        _activate_step(window_id, "ui_set_value:activate"),
        OperatorStep(
            capability="ui.set_value",
            payload={"window_id": window_id, **query, "value": value, "secret": secret},
            postcondition=_set,
            timeout_s=15.0,
            retries=0,
            level=LEVEL_UI_AUTOMATION,
            name="ui_set_value:set",
        ),
        OperatorStep(
            capability="ui.inspect",
            payload={"window_id": window_id, **query},
            postcondition=_read_back,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_UI_AUTOMATION,
            name="ui_set_value:verify",
        ),
    ]


def ui_select(window_id: str, query: dict[str, str], item: str) -> list[OperatorStep]:
    """B29 req 103: ``window.activate`` -> ``ui.select`` (the device reads the selection
    back) -> ``ui.inspect`` on the container: a node named ``item`` is selected."""

    def _selected(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        observed = body.get("observed") if isinstance(body.get("observed"), dict) else {}
        chosen = observed.get("selected")
        return isinstance(chosen, list) and item in [str(c) for c in chosen]

    def _read_back(result: DeviceRunResult) -> bool:
        return any(
            str(node.get("name") or "") == item and node.get("selected") is True
            for node in _tree_nodes(_inspect_root(result))
        )

    return [
        _activate_step(window_id, "ui_select:activate"),
        OperatorStep(
            capability="ui.select",
            payload={"window_id": window_id, **query, "item": item},
            postcondition=_selected,
            timeout_s=15.0,
            retries=0,
            level=LEVEL_UI_AUTOMATION,
            name="ui_select:select",
        ),
        OperatorStep(
            capability="ui.inspect",
            payload={"window_id": window_id, **query},
            postcondition=_read_back,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_UI_AUTOMATION,
            name="ui_select:verify",
        ),
    ]


def ui_read(window_id: str, query: dict[str, str] | None = None) -> list[OperatorStep]:
    """B29 req 99/102: ONE ``ui.inspect`` (the window, or the control the query names).
    A query is a read; its postcondition is that the device returned a tree at all."""

    def _has_tree(result: DeviceRunResult) -> bool:
        return isinstance(_inspect_root(result), dict)

    return [
        OperatorStep(
            capability="ui.inspect",
            payload={"window_id": window_id, **(query or {})},
            postcondition=_has_tree,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_UI_AUTOMATION,
            name="ui_read:inspect" if query else "ui_inspect:tree",
        )
    ]


def tree_text(root: Any) -> str:
    """The text a subtree holds - the first non-empty ``value``, else the names of the
    nodes that have one - for the sentence the owner hears."""
    for node in _tree_nodes(root):
        value = node.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    names = [
        str(n.get("name")).strip() for n in _tree_nodes(root) if str(n.get("name") or "").strip()
    ]
    return ", ".join(names[:8])


def type_text(window_id: str, text: str) -> list[OperatorStep]:
    """``window.activate`` -> ``keyboard.type`` -> ``ui.inspect`` (spec §4): postcondition,
    the focused control's value ends with ``text``. Interaction level ``ui_automation``.

    B28 req 109: the payload carries the ``secret`` flag the companion's ``RefuseSecret``
    reads (spec §1). The voice tool refuses a secret-looking text before this plan is ever
    built; the flag is the other half - a caller that did not check gets refused by the
    device, which is the layer that actually has the keyboard.
    """
    from app.voice.intents import contains_secret_reference

    secret = bool(contains_secret_reference(text))

    def _activated(result: DeviceRunResult) -> bool:
        return bool(_window_of(result).get("foreground"))

    def _typed(result: DeviceRunResult) -> bool:
        typed = result.result.get("typed_chars") if isinstance(result.result, dict) else None
        return bool(typed)

    def _value_ends_with_text(result: DeviceRunResult) -> bool:
        """The text is somewhere in the tree the device reported for THIS window.

        It used to read only ``root["value"]``, which is the window's own value. Notepad's
        window has none — its text lives one node down, in the "Metin Düzenleyici" edit
        control — so every real typing run into Notepad failed this postcondition even
        though the device had reported ``typed_chars: 20`` and the title had turned
        "*Adsız - Not Defteri". The owner was told "metni doğrulayamadım" about text that
        was on their screen (2026-09-09, ADR-0100).

        This is the same claim, read where the device actually put the answer: still the
        device's own read-back, still required to END with what was asked for, and still
        confined to the inspected window's bounded subtree (the companion caps it at
        ``MaxDepth``/``MaxNodes``).
        """
        root = result.result.get("root") if isinstance(result.result, dict) else None
        return _any_value_ends_with(root, text)

    return [
        OperatorStep(
            capability="window.activate",
            payload={"window_id": window_id},
            postcondition=_activated,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name="type_text:activate",
        ),
        OperatorStep(
            capability="keyboard.type",
            payload={"window_id": window_id, "text": text, "secret": secret},
            postcondition=_typed,
            timeout_s=15.0,
            retries=1,
            level=LEVEL_UI_AUTOMATION,
            name="type_text:type",
        ),
        OperatorStep(
            capability="ui.inspect",
            payload={"window_id": window_id},
            postcondition=_value_ends_with_text,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_UI_AUTOMATION,
            name="type_text:verify",
        ),
    ]


#: The one IPv4-looking group in a ``ipconfig`` transcript (spec §4: "parse the IPv4 for
#: 'IP adresimi göster'"). Deliberately simple: the companion's own ``ipconfig`` output is
#: what this parses, not an arbitrary string.
_IPV4_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


def parse_ipv4(stdout: str) -> str | None:
    for match in _IPV4_RE.finditer(stdout or ""):
        candidate = match.group(1)
        if candidate != "0.0.0.0" and not candidate.startswith("255."):
            return candidate
    return None


def shell_query(kind: str) -> list[OperatorStep]:
    """``terminal.execute`` (``hostname`` / ``ipconfig`` / ``whoami``); postcondition: exit
    0 and the value the query asked for actually parses out. The command per kind comes
    from the shared contract (B30 req 118), where a test holds it against the device's
    own patterns."""
    command = _allowlists.SHELL_COMMANDS.get(kind)
    if command is None:
        raise ValueError(f"'{kind}' is not a shell query the contract names")

    def _ok(result: DeviceRunResult) -> bool:
        if not isinstance(result.result, dict) or result.result.get("exit_code") != 0:
            return False
        stdout = str(result.result.get("stdout") or "")
        if kind == "ip":
            return parse_ipv4(stdout) is not None
        return bool(stdout.strip())

    return [
        OperatorStep(
            capability="terminal.execute",
            payload={"command": command},
            postcondition=_ok,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_API,
            name=f"shell_query:{kind}",
        )
    ]


def open_terminal() -> list[OperatorStep]:
    """``app.launch`` ``powershell`` (spec §4)."""

    def _ok(result: DeviceRunResult) -> bool:
        return bool(isinstance(result.result, dict) and result.result.get("pid"))

    return [
        OperatorStep(
            capability="app.launch",
            payload={"application": "powershell"},
            postcondition=_ok,
            timeout_s=15.0,
            retries=1,
            level=LEVEL_API,
            name="open_terminal",
        )
    ]


# ------------------------------------------------------------- B39: mission plans
#
# The plans a mission's DECIDE builds (app.operator.mission). Same shape as every plan
# above - fixed steps, each with the read that verifies it - with one addition:
# ``payload_from`` lets a later step carry a value an EARLIER step of the same plan
# observed (the window id a launch produced), resolved at dispatch time by
# ``app.operator.task.run_task``. A plan still never re-plans mid-flight; the mission
# loop is where a plan is rebuilt from a fresh observation.

#: Req 123: the Settings app's window title in this locale. Its host process is
#: ApplicationFrameHost.exe, so a Settings window is known by its title, never its image.
SETTINGS_WINDOW_TITLES: Final[tuple[str, ...]] = ("Ayarlar", "Settings")


def _title_of(result: DeviceRunResult) -> str:
    return str(_window_of(result).get("title") or "")


def _node_name_startswith(root: Any, prefix: str) -> bool:
    wanted = prefix.strip().lower()
    return bool(wanted) and any(
        str(node.get("name") or "").strip().lower().startswith(wanted) for node in _tree_nodes(root)
    )


def browser_navigate(url: str) -> list[OperatorStep]:
    """Req 127: ``browser.navigate`` then ``browser.inspect`` - the URL the page reports
    AFTER the navigation shares the host that was asked for; read by a second command,
    never taken from the navigate's own answer alone. The session is the mission's
    (opened by the mission loop before this plan is built - a session's profile is chosen
    from what the device answers, and a fixed plan cannot branch on that)."""
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    wanted = host[4:] if host.startswith("www.") else host

    def _same_host(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        seen = urlsplit(str(body.get("url") or "")).hostname or ""
        seen = seen[4:] if seen.startswith("www.") else seen
        return bool(wanted) and (seen == wanted or seen.endswith("." + wanted))

    return [
        OperatorStep(
            capability="browser.navigate",
            payload={"url": url},
            postcondition=_same_host,
            timeout_s=30.0,
            retries=1,
            level="dom",
            name="browser_navigate:navigate",
        ),
        OperatorStep(
            capability="browser.inspect",
            payload={},
            postcondition=_same_host,
            timeout_s=10.0,
            retries=1,
            level="dom",
            name="browser_navigate:inspect",
        ),
    ]


def open_settings(page: str, *, existing_window_id: str | None = None) -> list[OperatorStep]:
    """Req 123: the Settings app in front (launched, or the open one activated), and -
    when a page is named - that page reached through the app's own search box and read
    back from the tree. The title check is the only identity a UWP window offers."""
    seen: dict[str, Any] = {}

    def _settings_in_front(result: DeviceRunResult) -> bool:
        window = _window_of(result)
        if not window.get("foreground") or _title_of(result) not in SETTINGS_WINDOW_TITLES:
            return False
        seen["window_id"] = window.get("window_id")
        return True

    def _window_payload() -> dict[str, Any]:
        return {"window_id": str(seen.get("window_id") or "")}

    def _page_reached(result: DeviceRunResult) -> bool:
        root = result.result.get("root") if isinstance(result.result, dict) else None
        return _node_name_startswith(root, page)

    steps: list[OperatorStep]
    if existing_window_id:
        steps = [
            OperatorStep(
                capability="window.activate",
                payload={"window_id": existing_window_id},
                postcondition=_settings_in_front,
                timeout_s=10.0,
                retries=1,
                level=LEVEL_API,
                name="open_settings:activate",
            )
        ]
    else:
        steps = [
            OperatorStep(
                capability="app.launch",
                payload={"application": "settings"},
                postcondition=lambda r: (
                    isinstance(r.result, dict) and r.result.get("pid") is not None
                ),
                timeout_s=15.0,
                retries=1,
                level=LEVEL_API,
                name="open_settings:launch",
            ),
            OperatorStep(
                capability="window.current",
                payload={},
                postcondition=_settings_in_front,
                timeout_s=10.0,
                retries=2,
                level=LEVEL_API,
                name="open_settings:observe_window",
            ),
        ]
    if page:
        steps.extend(
            [
                OperatorStep(
                    capability="ui.set_value",
                    payload={"query": {"control_type": "Edit"}, "value": page},
                    payload_from=_window_payload,
                    postcondition=lambda r: (
                        isinstance(r.result, dict)
                        and str(r.result.get("observed_value") or "") == page
                    ),
                    timeout_s=10.0,
                    retries=1,
                    level=LEVEL_UI_AUTOMATION,
                    name="open_settings:search",
                ),
                OperatorStep(
                    capability="keyboard.key",
                    payload={"key": "enter"},
                    payload_from=_window_payload,
                    postcondition=None,
                    timeout_s=10.0,
                    retries=0,
                    level=LEVEL_KEYBOARD,
                    name="open_settings:enter",
                ),
                OperatorStep(
                    capability="ui.inspect",
                    payload={},
                    payload_from=_window_payload,
                    postcondition=_page_reached,
                    timeout_s=10.0,
                    retries=2,
                    level=LEVEL_UI_AUTOMATION,
                    name="open_settings:verify_page",
                ),
            ]
        )
    return steps


def explorer_open(target: str, titles: tuple[str, ...]) -> list[OperatorStep]:
    """Req 124: File Explorer to a named folder. Launched (its window found by image on
    the re-observe), then the address bar (Ctrl+L), the shell folder typed, Enter, and the
    window's title read back - Explorer titles a window with the folder it shows, so the
    verification is the folder's own name, in whichever locale the device runs."""
    seen: dict[str, Any] = {}

    def _explorer_in_front(result: DeviceRunResult) -> bool:
        window = _window_of(result)
        if not window.get("foreground"):
            return False
        if _executable_name(str(window.get("image") or "")) != "explorer.exe":
            return False
        seen["window_id"] = window.get("window_id")
        return True

    def _window_payload() -> dict[str, Any]:
        return {"window_id": str(seen.get("window_id") or "")}

    def _typed(result: DeviceRunResult) -> bool:
        return bool(isinstance(result.result, dict) and result.result.get("typed_chars"))

    def _folder_shown(result: DeviceRunResult) -> bool:
        return _window_of(result).get("foreground") is True and _title_of(result) in titles

    return [
        OperatorStep(
            capability="app.launch",
            payload={"application": "explorer"},
            postcondition=lambda r: isinstance(r.result, dict) and r.result.get("pid") is not None,
            timeout_s=15.0,
            retries=1,
            level=LEVEL_API,
            name="explorer_open:launch",
        ),
        OperatorStep(
            capability="window.current",
            payload={},
            postcondition=_explorer_in_front,
            timeout_s=10.0,
            retries=2,
            level=LEVEL_API,
            name="explorer_open:observe_window",
        ),
        OperatorStep(
            capability="keyboard.shortcut",
            payload={"keys": ["ctrl", "l"]},
            payload_from=_window_payload,
            postcondition=None,
            timeout_s=10.0,
            retries=0,
            level=LEVEL_KEYBOARD,
            name="explorer_open:address_bar",
        ),
        OperatorStep(
            capability="keyboard.type",
            payload={"text": target, "secret": False},
            payload_from=_window_payload,
            postcondition=_typed,
            timeout_s=15.0,
            retries=0,
            level=LEVEL_KEYBOARD,
            name="explorer_open:type_target",
        ),
        OperatorStep(
            capability="keyboard.key",
            payload={"key": "enter"},
            payload_from=_window_payload,
            postcondition=None,
            timeout_s=10.0,
            retries=0,
            level=LEVEL_KEYBOARD,
            name="explorer_open:enter",
        ),
        OperatorStep(
            capability="window.current",
            payload={},
            postcondition=_folder_shown,
            timeout_s=10.0,
            retries=2,
            level=LEVEL_API,
            name="explorer_open:verify_folder",
        ),
    ]


def ide_open_file(window_id: str, file_name: str) -> list[OperatorStep]:
    """Req 126: VS Code's quick-open (Ctrl+P), the file name typed, Enter, and the window
    title read back - VS Code titles its window "<file> - <folder> - Visual Studio Code",
    so the file that opened is in the title or it did not open."""

    def _typed(result: DeviceRunResult) -> bool:
        return bool(isinstance(result.result, dict) and result.result.get("typed_chars"))

    def _file_in_title(result: DeviceRunResult) -> bool:
        window = _window_of(result)
        return bool(window.get("foreground")) and file_name.lower() in _title_of(result).lower()

    return [
        _activate_step(window_id, "ide_open_file:activate"),
        OperatorStep(
            capability="keyboard.shortcut",
            payload={"window_id": window_id, "keys": ["ctrl", "p"]},
            postcondition=None,
            timeout_s=10.0,
            retries=0,
            level=LEVEL_KEYBOARD,
            name="ide_open_file:quick_open",
        ),
        OperatorStep(
            capability="keyboard.type",
            payload={"window_id": window_id, "text": file_name, "secret": False},
            postcondition=_typed,
            timeout_s=15.0,
            retries=0,
            level=LEVEL_KEYBOARD,
            name="ide_open_file:type_name",
        ),
        OperatorStep(
            capability="keyboard.key",
            payload={"window_id": window_id, "key": "enter"},
            postcondition=None,
            timeout_s=10.0,
            retries=0,
            level=LEVEL_KEYBOARD,
            name="ide_open_file:enter",
        ),
        OperatorStep(
            capability="window.current",
            payload={},
            postcondition=_file_in_title,
            timeout_s=10.0,
            retries=2,
            level=LEVEL_API,
            name="ide_open_file:verify_title",
        ),
    ]


def office_type(window_id: str, image: str, text: str) -> list[OperatorStep]:
    """Req 125: text into an Office document. Activate, type, then read the document
    control the adapter declares (Word exposes its body as a Document control); an
    application whose adapter declares no document control is verified by the device's
    own count and the foreground still being the target - and the level says so."""
    from app.operator.adapters import adapter_for
    from app.voice.intents import contains_secret_reference

    adapter = adapter_for(image)
    secret = bool(contains_secret_reference(text))

    def _typed(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        return bool(body.get("typed_chars")) and _landed_in(window_id, result)

    def _value_ends_with_text(result: DeviceRunResult) -> bool:
        root = result.result.get("root") if isinstance(result.result, dict) else None
        return _any_value_ends_with(root, text)

    steps = [
        _activate_step(window_id, "office_type:activate"),
        OperatorStep(
            capability="keyboard.type",
            payload={"window_id": window_id, "text": text, "secret": secret},
            postcondition=_typed,
            timeout_s=15.0,
            retries=1,
            level=LEVEL_KEYBOARD,
            name="office_type:type",
        ),
    ]
    if adapter.document_query:
        steps.append(
            OperatorStep(
                capability="ui.inspect",
                payload={"window_id": window_id, "query": dict(adapter.document_query)},
                postcondition=_value_ends_with_text,
                timeout_s=10.0,
                retries=1,
                level=LEVEL_UI_AUTOMATION,
                name="office_type:verify",
            )
        )
    return steps


def visual_click(window_id: str, x: int, y: int, *, absent_name: str) -> list[OperatorStep]:
    """Req 106: the visual rung. A click where the vision provider said the element is
    (screen space), verified by the TREE afterwards: the element of that name is gone -
    the same consequence ``ui_invoke`` reads for a button that closed its dialog. Level
    ``visual``: the receipt says a picture, not the tree, decided where to click."""

    def _landed(result: DeviceRunResult) -> bool:
        body = result.result if isinstance(result.result, dict) else {}
        observed = body.get("observed") if isinstance(body.get("observed"), dict) else {}
        cursor = observed.get("cursor") if isinstance(observed.get("cursor"), dict) else None
        if cursor is None:
            return False
        try:
            return max(abs(int(cursor["x"]) - x), abs(int(cursor["y"]) - y)) <= 2
        except (KeyError, TypeError, ValueError):
            return False

    def _element_gone(result: DeviceRunResult) -> bool:
        root = result.result.get("root") if isinstance(result.result, dict) else None
        return not _node_named(root, absent_name)

    return [
        _activate_step(window_id, "visual_click:activate"),
        OperatorStep(
            capability="pointer.click",
            payload={"window_id": window_id, "x": int(x), "y": int(y), "space": "screen"},
            postcondition=_landed,
            timeout_s=10.0,
            retries=0,
            level=LEVEL_VISUAL,
            name="visual_click:click",
        ),
        OperatorStep(
            capability="ui.inspect",
            payload={"window_id": window_id},
            postcondition=_element_gone,
            timeout_s=10.0,
            retries=1,
            level=LEVEL_UI_AUTOMATION,
            name="visual_click:verify",
        ),
    ]


__all__ = [
    "APP_ALLOWLIST",
    "KEY_NAMES",
    "MODIFIER_NAMES",
    "POINTER_ACTIONS",
    "POINTER_SPACES",
    "SCROLL_MAX_DELTA",
    "UI_ACTIONS",
    "UI_EXPECTATIONS",
    "close_app",
    "move_window",
    "pointer",
    "press_key",
    "press_shortcut",
    "process_list",
    "process_stop",
    "resize_window",
    "service_restart",
    "service_status",
    "tree_text",
    "ui_invoke",
    "ui_read",
    "ui_select",
    "ui_set_value",
    "valid_key",
    "valid_shortcut",
    "activate_window",
    "close_window",
    "maximize_window",
    "minimize_window",
    "open_application",
    "open_terminal",
    "parse_ipv4",
    "previous_window",
    "resolve_app_alias",
    "restore_window",
    "shell_query",
    "type_text",
    "browser_navigate",
    "explorer_open",
    "ide_open_file",
    "office_type",
    "open_settings",
    "visual_click",
    "SETTINGS_WINDOW_TITLES",
    "UWP_HOSTED_IMAGES",
]
