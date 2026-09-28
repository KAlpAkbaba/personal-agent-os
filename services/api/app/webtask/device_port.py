"""The browser port over the device path (ADR-0207 b): the loop's hands.

``DeviceTaskBrowser`` implements ``app.webtask.loop.BrowserPort`` with exactly the
commands of BROWSER_CAPABILITIES.md v1.6, dispatched through the SAME
``DeviceCommandClientProtocol`` research and the operator use - one command envelope, one
idempotency store, one audit log. The device stays a dumb executor: it is handed one
command and answers it.

* The session is the OWNER's Chrome (profile ``owner``, ADR-0113), attached and never
  launched. The task works in a tab it opened itself - ``pages[0]`` is the owner's work
  (ADR-0113's own lesson) - so the first thing a new session does is ``browser.tab_new``.
* Every ``browser.click`` carries ``risk_ceiling``: the class the gate allowed. The
  worker classifies the click from the element it resolved and refuses above the
  ceiling, so a step that turns out to be more than it looked is never performed.
* A URL is validated in full here (names resolved) before it is sent; the device
  validates again before it acts.

Not exercised against a real device in PR-B: the acceptance tasks run on the fake
browser. This module is held by its own unit tests against a recording command client,
and meets a real Chrome in PR-C.
"""

from __future__ import annotations

import re
import threading
import uuid
from typing import Any, Final

from app.devices.commands import (
    CommandExpired,
    CommandFailed,
    CommandOutcome,
    CommandSucceeded,
    DeviceCommandClientProtocol,
)
from app.research.destination import DestinationPolicyError, validate_fetch_target
from app.research.forbidden_keys import find_forbidden_keys
from app.webtask.loop import BrowserPortError
from app.webtask.types import (
    ACTION_BACK,
    ACTION_CHECK,
    ACTION_CLICK,
    ACTION_FILL,
    ACTION_NAVIGATE,
    ACTION_SCROLL,
    ACTION_SELECT,
    CAPABILITY_OF,
    RISK_ORDER,
    Observation,
    Step,
)

PROFILE_OWNER: Final = "owner"
CAPABILITY_OBSERVE: Final = "browser.observe"
CAPABILITY_SESSION_OPEN: Final = "browser.session_open"
CAPABILITY_TAB_NEW: Final = "browser.tab_new"
CONTRACT_OBSERVE: Final = 1
DEFAULT_TIMEOUT_S: Final = 60.0
_REASON: Final = re.compile(r"\(([a-z_]+)\)")

_lock = threading.Lock()
#: (device, session) pairs this process has opened. Lost on a restart, which is safe:
#: ``session_open`` on a live session answers ``created: false`` and no tab is opened.
_OPEN: set[tuple[str, str]] = set()


def reset_known_sessions() -> None:
    with _lock:
        _OPEN.clear()


def session_id_for(task_id: str) -> str:
    return f"webtask-{task_id}"


def _result(outcome: CommandOutcome) -> dict[str, Any]:
    if isinstance(outcome, CommandSucceeded):
        result = dict(outcome.result or {})
        found = find_forbidden_keys(result)
        if found:
            raise BrowserPortError(
                "security_scope_error", "the result carried forbidden keys", reason="forbidden_key"
            )
        return result
    if isinstance(outcome, CommandFailed):
        message = str(outcome.message or "")
        match = _REASON.search(message)
        raise BrowserPortError(
            str(outcome.error_class or "internal_bug"),
            # The device's message may quote up to 200 characters of a page. It is kept
            # out of anything the loop records: the class and the reason are what travel.
            str(outcome.error_class or "internal_bug"),
            retryable=bool(outcome.retryable),
            reason=match.group(1) if match else "",
        )
    if isinstance(outcome, CommandExpired):
        raise BrowserPortError("timeout", "the device did not answer in time", retryable=True)
    raise BrowserPortError("internal_bug", "unexpected command outcome")


class DeviceTaskBrowser:
    def __init__(
        self,
        command_client: DeviceCommandClientProtocol,
        *,
        device_id: uuid.UUID,
        trace_id: str = "",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        heartbeat: Any = None,
    ) -> None:
        self._client = command_client
        self._device_id = device_id
        self._trace_id = trace_id
        self._timeout_s = timeout_s
        self._heartbeat = heartbeat

    # ------------------------------------------------------------- plumbing

    def _send(self, capability: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        outcome = self._client.run(
            device_id=self._device_id,
            capability=capability,
            payload=payload,
            idempotency_key=key,
            timeout_s=self._timeout_s,
            trace_id=self._trace_id or key,
            heartbeat=self._heartbeat,
        )
        return _result(outcome)

    def _ensure_session(self, task_id: str, key: str) -> str:
        session_id = session_id_for(task_id)
        pair = (str(self._device_id), session_id)
        with _lock:
            if pair in _OPEN:
                return session_id
        opened = self._send(
            CAPABILITY_SESSION_OPEN,
            {
                "session_id": session_id,
                "profile": PROFILE_OWNER,
                # Every class the loop may be ALLOWED to reach after the owner's word; what
                # each click may actually be is its own ``risk_ceiling``.
                "policy": {"allowed_risk_classes": list(RISK_ORDER), "visible": True},
                "channel": "chrome",
            },
            f"{key}:session_open",
        )
        if opened.get("created", True):
            # Never the owner's own tab: the task works in one it opened.
            self._send(
                CAPABILITY_TAB_NEW, {"session_id": session_id, "url": None}, f"{key}:tab_new"
            )
        with _lock:
            _OPEN.add(pair)
        return session_id

    def _with_session(
        self, task_id: str, key: str, capability: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        session_id = self._ensure_session(task_id, key)
        try:
            return self._send(capability, {"session_id": session_id, **payload}, key)
        except BrowserPortError as exc:
            if exc.error_class != "validation_error" or exc.reason:
                raise
            # "unknown session": the worker restarted. Reopen ONCE and send again, under
            # a key of its own - never a replay of the refused command's answer.
            with _lock:
                _OPEN.discard((str(self._device_id), session_id))
            session_id = self._ensure_session(task_id, f"{key}:reopen")
            return self._send(
                capability, {"session_id": session_id, **payload}, f"{key}:session-retry"
            )

    # ------------------------------------------------------------- the port

    def observe(self, *, task_id: str, key: str) -> Observation:
        result = self._with_session(task_id, key, CAPABILITY_OBSERVE, {"scope": "page"})
        if not result.get("observation_id"):
            # An agent installed before contract v1.6 cannot observe. Named, not guessed.
            raise BrowserPortError(
                "capability_missing",
                "the device's browser worker does not serve browser.observe (contract v1.6)",
            )
        return Observation.from_result(result)

    def act(
        self, *, task_id: str, key: str, step: Step, observation_id: str, risk_ceiling: str
    ) -> dict[str, Any]:
        capability = CAPABILITY_OF.get(step.action)
        if capability is None:
            raise BrowserPortError("validation_error", "not an acting step", reason="action")
        target = {"ref": step.ref, "observation_id": observation_id}
        if step.action == ACTION_NAVIGATE:
            assert step.url is not None
            try:
                validate_fetch_target(step.url)
            except DestinationPolicyError as exc:
                raise BrowserPortError(
                    "security_scope_error", "destination refused", reason="destination"
                ) from exc
            payload: dict[str, Any] = {"url": step.url}
        elif step.action == ACTION_BACK:
            payload = {}
        elif step.action == ACTION_SCROLL:
            payload = {"direction": step.direction or "down", "amount_px": 800}
        elif step.action == ACTION_CLICK:
            payload = {"target": target, "risk_ceiling": risk_ceiling}
        elif step.action in (ACTION_FILL, ACTION_SELECT):
            payload = {"target": target, "value": step.value or ""}
        elif step.action == ACTION_CHECK:
            payload = {"target": target, "checked": bool(step.checked)}
        else:  # pragma: no cover - CAPABILITY_OF and this chain name the same actions
            raise BrowserPortError("validation_error", "unhandled action", reason="action")
        return self._with_session(task_id, key, capability, payload)


__all__ = [
    "CAPABILITY_OBSERVE",
    "DeviceTaskBrowser",
    "PROFILE_OWNER",
    "reset_known_sessions",
    "session_id_for",
]
