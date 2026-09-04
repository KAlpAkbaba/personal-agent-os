"""UI-state contract (Phase 4 foundation).

A tiny, provider-neutral seam between "something in Cloud Core is doing work"
and "something on a screen/voice surface shows that work" (ARCHITECTURE.md §1:
hybrid cloud-brain + device-local execution; CLAUDE.md: task completion,
artifact persistence and presentation are separate concepts).

``app.goals.cognitive.Orchestrator`` is the first publisher: every loop
iteration calls ``get_publisher().publish(...)`` so a UI (web/desktop/mobile/
voice) can render "thinking" / "running a tool" / "waiting on you" /
"done" / "something went wrong" without polling goal rows or ledger events.

The default publisher is in-process and ephemeral (CLAUDE.md platform
defaults: Redis only for ephemeral/cache concerns, never the sole source of
truth) — nothing here is durable. Durable evidence of what happened lives in
``app.ledger``, written alongside every publish by callers that care. A
future phase may swap in a Redis-backed ``UiStatePublisher`` (pub/sub to a
websocket) behind the same ``UiStatePublisher`` protocol without touching any
caller — that is the entire point of the seam.
"""

from app.uistate.publisher import (
    InMemoryUiStatePublisher,
    UiStateEvent,
    UiStatePublisher,
    get_publisher,
    reset_publisher,
    set_publisher,
)
from app.uistate.state import UiState

__all__ = [
    "InMemoryUiStatePublisher",
    "UiState",
    "UiStateEvent",
    "UiStatePublisher",
    "get_publisher",
    "reset_publisher",
    "set_publisher",
]
