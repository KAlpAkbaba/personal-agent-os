"""Normalized owner-facing UI state (the future Holographic Core's event contract).

A later 3D "Agent Core" renderer must be able to show what PersonalAgentOS is actually
doing without knowing anything about how the subsystems work. So the subsystems publish a
small, fixed vocabulary of *truthful* state events — never decorative animation triggers —
and the renderer consumes only this.

The contract, not the renderer, is what this package is: an enum of states, a bounded
event shape, an in-process bus with a durable tail, and a REST/stream surface. Nothing
here stores audio, transcripts or any owner content; a voice event carries a bounded
energy number, never a sample.
"""

from app.uistate.contract import (
    UI_STATES,
    UiState,
    UiStateEvent,
    ui_state_contract,
)
from app.uistate.publisher import UiStatePublisher, get_publisher, publish, set_publisher

__all__ = [
    "UI_STATES",
    "UiState",
    "UiStateEvent",
    "UiStatePublisher",
    "get_publisher",
    "publish",
    "set_publisher",
    "ui_state_contract",
]
