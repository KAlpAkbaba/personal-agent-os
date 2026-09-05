"""The experience subsystem's UI-state signal (ADR-0052 contract v2).

One function, in one place. ``engine.py`` and ``compiler.py`` each carried their own
copy of this helper, and both copies called ``uistate.publish()`` with keyword arguments
it does not accept (``phase=...`` plus ``**metadata``). Every call raised ``TypeError``
into a bare ``except`` that logged at debug — so ``agent.memory_retrieval`` was never
published at all, and the Core could only conclude that memory work simply never ran.
Two copies of a wrapper drift; one does not.

There is deliberately **no** ``try``/``except`` around the publish here. ``uistate.publish``
already documents and implements "never raises" for runtime failures, so the only thing an
exception at this call site can mean is that the *call* is wrong — exactly the defect this
module exists to have caught. A wiring bug must be loud, not quiet.
"""

from __future__ import annotations

from typing import Any

from app.uistate import UiState, publish

#: The publisher keeps only bounded numbers, bools and short tokens; a list or dict is
#: content-shaped and dropped. Counts carry the same information a renderer can draw, so
#: collections are reduced to their length rather than silently disappearing.
_MAX_TOKEN_CHARS = 64


def _bounded(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:_MAX_TOKEN_CHARS]
    if isinstance(value, list | tuple | set | dict):
        return len(value)
    return None


def publish_progress(*, phase: str, **metadata: Any) -> None:
    """Signal that memory consolidation entered ``phase``.

    ``phase`` is a machine token (``ingest_started``, ``compile_completed``, ...), not
    prose. Never pass memory or lesson content: ids and counts only.
    """
    clean = {key: _bounded(value) for key, value in metadata.items()}
    publish(
        UiState.MEMORY_RETRIEVAL,
        subsystem="experience",
        status=phase,
        label=phase,
        metadata={key: value for key, value in clean.items() if value is not None},
    )


__all__ = ["publish_progress"]
