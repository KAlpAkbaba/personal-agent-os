"""Ambient display policy (M18.3 spec §3.9).

The one place in this system that may conclude "turn the owner's screens off", and the
holdoffs that keep it from doing so. ``policy.decide`` is pure and exhaustively tested;
``service`` reads the world and issues the receipted command; ``ingest`` turns a device's
heartbeat status into presence, holdoffs and bus state.

The rule the whole package exists to enforce: **uncertain means ON**.
"""

from app.ambient.holdoff import HoldoffRegistry, get_holdoffs, set_holdoffs
from app.ambient.policy import AmbientInputs, AmbientPolicy, Decision, decide

__all__ = [
    "AmbientInputs",
    "AmbientPolicy",
    "Decision",
    "HoldoffRegistry",
    "decide",
    "get_holdoffs",
    "set_holdoffs",
]
