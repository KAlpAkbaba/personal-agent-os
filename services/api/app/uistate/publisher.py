"""The bus every subsystem publishes to, and the tail a late client draws from.

In process, bounded and non-blocking: publishing a UI state must never be able to slow
down or fail the work it describes. A subsystem calls :func:`publish` and moves on; the
publisher validates the event's metadata, stamps a sequence, keeps a short tail and hands
it to whatever subscribers exist (the REST stream today, a renderer later).

Nothing here is durable state. The Activity Ledger is the durable record of what happened;
this is only what is happening *now*, for something to draw.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.logging import get_logger
from app.uistate.contract import (
    MAX_LABEL_CHARS,
    MAX_METADATA_KEYS,
    SEVERITIES,
    SUBSYSTEMS,
    UiState,
    UiStateEvent,
)

logger = get_logger("app.uistate")

#: How many recent events a late client may replay to reach the current picture.
TAIL_SIZE = 64

#: Metadata keys that normalize to any of these are refused: the UI carries state, never
#: content. Mirrors the voice/ledger scrubber's intent (ADR-0036 §3).
_FORBIDDEN_KEY_PARTS = (
    "text",
    "transcript",
    "audio",
    "pcm",
    "wave",
    "secret",
    "credential",
    "token",
    "apikey",
    "api_key",
    "password",
    "content",
    "excerpt",
    "body",
)


def _normalize_key(key: str) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def is_forbidden_metadata_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(part.replace("_", "") in normalized for part in _FORBIDDEN_KEY_PARTS)


def _clean_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only bounded numbers, bools and short tokens under allowed keys."""
    if not metadata:
        return {}
    out: dict[str, Any] = {}
    for key, value in list(metadata.items())[:MAX_METADATA_KEYS]:
        if is_forbidden_metadata_key(key):
            logger.warning("uistate_metadata_key_refused", key=str(key)[:32])
            continue
        if isinstance(value, bool) or isinstance(value, int | float):
            out[str(key)[:32]] = value
        elif isinstance(value, str):
            out[str(key)[:32]] = value[:MAX_LABEL_CHARS]
        # anything else (dicts, lists, objects) is content-shaped and dropped
    return out


class UiStatePublisher:
    """Bounded fan-out with a replayable tail. Thread-safe; never raises to the caller."""

    def __init__(self, *, tail_size: int = TAIL_SIZE) -> None:
        self._lock = threading.Lock()
        self._tail: deque[UiStateEvent] = deque(maxlen=tail_size)
        self._subscribers: list[Callable[[UiStateEvent], None]] = []
        self._sequence = 0
        self._current: UiStateEvent | None = None

    # ------------------------------------------------------------------ publish

    def publish(self, event: UiStateEvent) -> UiStateEvent | None:
        """Validate, stamp and fan out. Returns the stamped event, or None if refused."""
        if event.subsystem not in SUBSYSTEMS:
            logger.warning("uistate_unknown_subsystem", subsystem=str(event.subsystem)[:32])
            return None
        severity = event.severity if event.severity in SEVERITIES else "info"
        stamped = UiStateEvent(
            state=event.state,
            subsystem=event.subsystem,
            at=event.at or datetime.now(UTC),
            intensity=event.intensity,
            progress=event.progress,
            severity=severity,
            status=event.status,
            task_id=event.task_id,
            goal_id=event.goal_id,
            module_id=event.module_id,
            session_id=event.session_id,
            label=event.label,
            metadata=_clean_metadata(event.metadata),
            event_id=event.event_id,
        )
        with self._lock:
            self._sequence += 1
            stamped = replace(stamped, sequence=self._sequence)
            self._tail.append(stamped)
            self._current = stamped
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(stamped)
            except Exception:  # noqa: BLE001 - a broken renderer must not break the agent
                logger.warning("uistate_subscriber_failed", state=stamped.state.value)
        return stamped

    # ------------------------------------------------------------------ read

    def current(self) -> UiStateEvent | None:
        with self._lock:
            return self._current

    def tail(self, *, limit: int = TAIL_SIZE, after_sequence: int = 0) -> list[UiStateEvent]:
        with self._lock:
            events = [e for e in self._tail if e.sequence > after_sequence]
        return events[-limit:]

    def subscribe(self, callback: Callable[[UiStateEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def reset(self) -> None:
        with self._lock:
            self._tail.clear()
            self._subscribers.clear()
            self._sequence = 0
            self._current = None


_publisher = UiStatePublisher()


def get_publisher() -> UiStatePublisher:
    return _publisher


def set_publisher(publisher: UiStatePublisher) -> None:
    """Tests and alternative runtimes swap the process-wide publisher."""
    global _publisher
    _publisher = publisher


def publish(
    state: UiState,
    *,
    subsystem: str,
    intensity: float | None = None,
    progress: float | None = None,
    severity: str = "info",
    status: str | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
    module_id: str | None = None,
    session_id: str | None = None,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> UiStateEvent | None:
    """Publish one UI state. Never raises: a UI signal must not fail real work."""
    try:
        return _publisher.publish(
            UiStateEvent(
                state=state,
                subsystem=subsystem,
                intensity=intensity,
                progress=progress,
                severity=severity,
                status=status,
                task_id=task_id,
                goal_id=goal_id,
                module_id=module_id,
                session_id=session_id,
                label=label,
                metadata=metadata or {},
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("uistate_publish_failed", state=getattr(state, "value", str(state)))
        return None


def publish_many(events: Iterable[UiStateEvent]) -> None:
    for event in events:
        try:
            _publisher.publish(event)
        except Exception:  # noqa: BLE001
            continue


__all__ = [
    "TAIL_SIZE",
    "UiStatePublisher",
    "get_publisher",
    "is_forbidden_metadata_key",
    "publish",
    "publish_many",
    "set_publisher",
]
