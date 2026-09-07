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

from app.operator.task import (
    LEVEL_API,
    LEVEL_UI_AUTOMATION,
    OperatorStep,
)
from app.routines.dispatch import DeviceRunResult

# ------------------------------------------------------------------ app allowlist

#: docs/M19_DIGITAL_OPERATOR_SPEC.md §2 ``app.launch``'s allowlisted names. The single
#: place that says which application ids exist; the voice tool refuses anything else by
#: naming this list, never by guessing a path.
APP_ALLOWLIST: Final[tuple[str, ...]] = (
    "notepad",
    "calc",
    "explorer",
    "powershell",
    "chrome",
    "msedge",
)

#: Turkish alias phrase (lower-cased; an apostrophe suffix like "chrome'u" is cut by the
#: caller before matching) -> the canonical allowlisted id. Longer phrases first so
#: "google chrome" is not shadowed by the bare "chrome" entry below it.
_APP_ALIAS_PHRASES: Final[tuple[tuple[str, str], ...]] = (
    ("google chrome", "chrome"),
    ("microsoft edge", "msedge"),
    ("dosya gezgini", "explorer"),
    ("not defteri", "notepad"),
    ("hesap makinesi", "calc"),
    ("notepad", "notepad"),
    ("calculator", "calc"),
    ("calc", "calc"),
    ("gezgin", "explorer"),
    ("explorer", "explorer"),
    ("powershell", "powershell"),
    ("tarayıcı", "chrome"),
    ("tarayici", "chrome"),
    ("chrome", "chrome"),
    ("edge", "msedge"),
    ("msedge", "msedge"),
)


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


# ------------------------------------------------------------------------- plans


def _window_of(result: DeviceRunResult) -> dict[str, Any]:
    window = result.result.get("window") if isinstance(result.result, dict) else None
    return window if isinstance(window, dict) else {}


def open_application(name: str) -> list[OperatorStep]:
    """``app.launch`` then ``window.current`` (spec §4): postcondition, a foreground
    window of the pid ``app.launch`` returned."""
    launched: dict[str, Any] = {}

    def _launch_ok(result: DeviceRunResult) -> bool:
        pid = result.result.get("pid") if isinstance(result.result, dict) else None
        if pid is None:
            return False
        launched["pid"] = pid
        return True

    def _window_ok(result: DeviceRunResult) -> bool:
        window = _window_of(result)
        return bool(window.get("foreground") and window.get("pid") == launched.get("pid"))

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


def type_text(window_id: str, text: str) -> list[OperatorStep]:
    """``window.activate`` -> ``keyboard.type`` -> ``ui.inspect`` (spec §4): postcondition,
    the focused control's value ends with ``text``. Interaction level ``ui_automation``."""

    def _activated(result: DeviceRunResult) -> bool:
        return bool(_window_of(result).get("foreground"))

    def _typed(result: DeviceRunResult) -> bool:
        typed = result.result.get("typed_chars") if isinstance(result.result, dict) else None
        return bool(typed)

    def _value_ends_with_text(result: DeviceRunResult) -> bool:
        root = result.result.get("root") if isinstance(result.result, dict) else None
        value = str((root or {}).get("value") or "") if isinstance(root, dict) else ""
        return value.endswith(text)

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
            payload={"window_id": window_id, "text": text},
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
    """``terminal.execute`` (``hostname`` / ``ipconfig``); postcondition: exit 0 and the
    value the query asked for actually parses out."""
    command = "hostname" if kind == "hostname" else "ipconfig"

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


__all__ = [
    "APP_ALLOWLIST",
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
]
