"""The closed set of UI states a presentation surface may be told about.

Mirrors the style of ``app.voice.realtime.RealtimeState``: a ``StrEnum`` so a
published value serializes to a plain string over HTTP/WS without a custom
encoder, while callers still get enum safety (typos fail at call time, not at
render time on some other device).
"""

from __future__ import annotations

from enum import StrEnum


class UiState(StrEnum):
    #: nothing in flight for this subject.
    IDLE = "IDLE"
    #: the Cognitive Core is planning or replanning (no side effect yet).
    THINKING = "THINKING"
    #: a capability/tool call is in flight for the current plan step.
    TOOL_RUNNING = "TOOL_RUNNING"
    #: bounded replanning was exhausted, or the goal otherwise needs an
    #: explicit owner decision before it can continue.
    WAITING_OWNER = "WAITING_OWNER"
    #: every success criterion is satisfied from evidence; the goal is done.
    GOAL_COMPLETED = "GOAL_COMPLETED"
    #: an unexpected failure ended the loop (not a routine unsatisfied step).
    ERROR = "ERROR"


__all__ = ["UiState"]
