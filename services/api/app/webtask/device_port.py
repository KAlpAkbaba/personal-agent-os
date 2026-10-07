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
  Since contract v1.8 so do ``fill``, ``select_option`` and ``set_checked``; a write
  answered without ``risk_class`` came from a worker that ignored the ceiling, and the
  task stops there (``capability_missing``).
* A URL is validated in full here (names resolved) before it is sent; the device
  validates again before it acts.
* A task whose target is the CLOUD (card cloud-task-loop-core, S1) opens the cloud
  worker's ``research`` profile instead: headless Chromium, READ / NAVIGATE /
  REVERSIBLE_WRITE only, and the owner's allow-list as it is when the round starts.

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
from app.logging import get_logger
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
    CLOUD_RISKS,
    RISK_ORDER,
    TARGET_CLOUD,
    Observation,
    Step,
)

logger = get_logger("app.webtask.device_port")

PROFILE_OWNER: Final = "owner"
#: The cloud worker's only profile (``services/browser/browser_agent/cloud/policy.py``).
PROFILE_RESEARCH: Final = "research"
CAPABILITY_OBSERVE: Final = "browser.observe"
CAPABILITY_SESSION_OPEN: Final = "browser.session_open"
CAPABILITY_SESSION_CLOSE: Final = "browser.session_close"
CAPABILITY_TAB_NEW: Final = "browser.tab_new"
CONTRACT_OBSERVE: Final = 1
DEFAULT_TIMEOUT_S: Final = 60.0
_REASON: Final = re.compile(r"\(([a-z_]+)\)")
#: Contract v1.8: the writes that carry ``risk_ceiling`` and answer ``risk_class``.
_WRITES: Final = frozenset({ACTION_FILL, ACTION_SELECT, ACTION_CHECK})

_lock = threading.Lock()
#: (device, session) pairs this process has opened. Lost on a restart, which is safe:
#: ``session_open`` on a live session answers ``created: false`` and no tab is opened.
_OPEN: set[tuple[str, str]] = set()
#: Devices whose worker answered a write without ``risk_class`` (before contract v1.8).
#: No further write is SENT to them: the loop counts a failed act and may plan another
#: write, and that one must not reach a worker that cannot refuse it. Lost on a restart,
#: which costs one more Cloud-gated write before the device is known again.
_NO_WRITE_CEILING: set[str] = set()


def reset_known_sessions() -> None:
    with _lock:
        _OPEN.clear()
        _NO_WRITE_CEILING.clear()


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
        target: str = "",
        owner_allow_list: tuple[str, ...] = (),
    ) -> None:
        self._client = command_client
        self._device_id = device_id
        self._trace_id = trace_id
        self._timeout_s = timeout_s
        self._heartbeat = heartbeat
        self._target = target
        self._owner_allow_list = tuple(owner_allow_list)

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

    def _session_open(self, session_id: str) -> dict[str, Any]:
        if self._target == TARGET_CLOUD:
            # The cloud worker's own profile in a headless Chromium (it has none of the
            # owner's sessions), no class above a reversible write, and the owner's list
            # as it is now: the worker refuses a write off it (contract S2/S3).
            return {
                "session_id": session_id,
                "profile": PROFILE_RESEARCH,
                "policy": {"allowed_risk_classes": list(CLOUD_RISKS), "visible": False},
                "channel": "chromium",
                "cloud_task": True,
                "owner_allow_list": list(self._owner_allow_list),
            }
        return {
            "session_id": session_id,
            "profile": PROFILE_OWNER,
            # Every class the loop may be ALLOWED to reach after the owner's word; what
            # each click may actually be is its own ``risk_ceiling``.
            "policy": {"allowed_risk_classes": list(RISK_ORDER), "visible": True},
            "channel": "chrome",
        }

    def _ensure_session(self, task_id: str, key: str) -> str:
        session_id = session_id_for(task_id)
        pair = (str(self._device_id), session_id)
        with _lock:
            if pair in _OPEN:
                return session_id
        opened = self._send(
            CAPABILITY_SESSION_OPEN, self._session_open(session_id), f"{key}:session_open"
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

    def close(self, task_id: str) -> None:
        """The task has ended: its session is closed (live run 2026-10-06 - an unclosed
        research session made the cloud worker refuse every later task). Sent even when
        this process does not know the session (a restart forgets ``_OPEN``): the worker
        answers an unknown session ``closed``. One key per task, so a second close is the
        same command. Never raises: a task that ended stays ended."""
        session_id = session_id_for(task_id)
        with _lock:
            _OPEN.discard((str(self._device_id), session_id))
        try:
            self._send(
                CAPABILITY_SESSION_CLOSE,
                {"session_id": session_id},
                f"webtask:{task_id}:session_close",
            )
        except BrowserPortError as exc:
            logger.warning(
                "webtask_session_close_failed", error_class=exc.error_class, reason=exc.reason
            )
        except Exception as exc:  # noqa: BLE001 - best effort, the device may be gone
            logger.warning("webtask_session_close_failed", error_class=type(exc).__name__)

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
            payload = {"target": target, "value": step.value or "", "risk_ceiling": risk_ceiling}
        elif step.action == ACTION_CHECK:
            payload = {
                "target": target,
                "checked": bool(step.checked),
                "risk_ceiling": risk_ceiling,
            }
        else:  # pragma: no cover - CAPABILITY_OF and this chain name the same actions
            raise BrowserPortError("validation_error", "unhandled action", reason="action")
        device = str(self._device_id)
        cannot_enforce = BrowserPortError(
            "capability_missing",
            f"the device's browser worker does not enforce a ceiling on {capability} "
            "(contract v1.8)",
        )
        if step.action in _WRITES:
            with _lock:
                if device in _NO_WRITE_CEILING:
                    raise cannot_enforce
        result = self._with_session(task_id, key, capability, payload)
        if step.action in _WRITES and not result.get("risk_class"):
            # A worker from before v1.8 ignores the ceiling and writes. That one write was
            # gated here; the next must not be sent to a device that cannot refuse it.
            with _lock:
                _NO_WRITE_CEILING.add(device)
            raise cannot_enforce
        return result


__all__ = [
    "CAPABILITY_OBSERVE",
    "CAPABILITY_SESSION_CLOSE",
    "DeviceTaskBrowser",
    "PROFILE_OWNER",
    "reset_known_sessions",
    "session_id_for",
]
