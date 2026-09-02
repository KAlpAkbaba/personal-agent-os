"""Pure provider selection for the ``ConversationRealtime`` mode (M12 spec §2).

Selection is by DECLARED CAPABILITY, never by model or vendor name:

- hard requirement: ``speech_to_speech ∧ full_duplex ∧ barge_in ∧ tool_calling ∧
  language`` (``ProviderCapabilities.is_conversation_capable``);
- preference, in order: semantic end-of-turn, then WebRTC transport, then the
  configured preference list, then the name (a deterministic tie-break);
- a candidate that fails the hard requirement is reported with the exact
  requirements it misses, so a health check / benchmark can say *why* a
  provider was not chosen instead of "it wasn't picked".

Nothing here performs I/O; the runtime decides which candidates are activated
(keys present) before calling this, and the tests cover every branch.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    END_OF_TURN_SEMANTIC,
    TRANSPORT_WEBRTC,
    ProviderCapabilities,
)

CONVERSATION_REQUIREMENTS = (
    "kind=realtime",
    "speech_to_speech",
    "full_duplex",
    "barge_in",
    "tool_calling",
    "language",
)


@dataclass(frozen=True, slots=True)
class SelectionResult:
    selected: ProviderCapabilities
    #: eligible candidates in rank order (the winner first)
    ranked: tuple[str, ...]
    #: name -> the hard requirements it does not meet
    rejected: dict[str, tuple[str, ...]]
    #: why the winner ranked first (human-readable, deterministic)
    reasons: tuple[str, ...]
    #: the transport the client should open (WebRTC when the provider has it)
    transport: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected.name,
            "transport": self.transport,
            "ranked": list(self.ranked),
            "rejected": {k: list(v) for k, v in self.rejected.items()},
            "reasons": list(self.reasons),
        }


def missing_requirements(
    caps: ProviderCapabilities, *, language: str = "tr-TR",
    require_ephemeral_credentials: bool = False,
) -> tuple[str, ...]:
    """The hard requirements ``caps`` fails for ConversationRealtime (empty = eligible)."""
    missing: list[str] = []
    if caps.kind != "realtime":
        missing.append("kind=realtime")
    if not caps.speech_to_speech:
        missing.append("speech_to_speech")
    if not caps.full_duplex:
        missing.append("full_duplex")
    if not caps.barge_in:
        missing.append("barge_in")
    if not caps.tool_calling:
        missing.append("tool_calling")
    if not caps.supports_language(language):
        missing.append("language")
    if require_ephemeral_credentials and not caps.ephemeral_credentials:
        missing.append("ephemeral_credentials")
    return tuple(missing)


def preferred_transport(caps: ProviderCapabilities) -> str:
    """WebRTC when offered (spec §1: preferred), else the first declared transport."""
    if TRANSPORT_WEBRTC in caps.transports:
        return TRANSPORT_WEBRTC
    if caps.transports:
        return caps.transports[0]
    raise VoiceError(
        VoiceErrorClass.CAPABILITY_MISSING,
        f"provider {caps.name!r} declares no realtime transport",
        provider=caps.name,
    )


def rank_key(
    caps: ProviderCapabilities, preference_order: Sequence[str]
) -> tuple[int, int, int, str]:
    """Lower sorts first: semantic end-of-turn, WebRTC, configured order, name."""
    try:
        pref = list(preference_order).index(caps.name)
    except ValueError:
        pref = len(preference_order)
    return (
        0 if caps.end_of_turn == END_OF_TURN_SEMANTIC else 1,
        0 if TRANSPORT_WEBRTC in caps.transports else 1,
        pref,
        caps.name,
    )


def _reasons(caps: ProviderCapabilities, preference_order: Sequence[str]) -> tuple[str, ...]:
    reasons = ["meets speech_to_speech+full_duplex+barge_in+tool_calling+language"]
    if caps.end_of_turn == END_OF_TURN_SEMANTIC:
        reasons.append("end_of_turn=semantic")
    if TRANSPORT_WEBRTC in caps.transports:
        reasons.append("transport=webrtc")
    if caps.name in preference_order:
        reasons.append(f"preference_order[{list(preference_order).index(caps.name)}]")
    return tuple(reasons)


def select_conversation_provider(
    candidates: Iterable[ProviderCapabilities],
    preference_order: Sequence[str] = (),
    *,
    language: str = "tr-TR",
    require_ephemeral_credentials: bool = False,
) -> SelectionResult:
    """Choose the ConversationRealtime provider from ``candidates`` (spec §2).

    Raises ``CAPABILITY_MISSING`` (with the per-candidate missing requirements
    in ``details``) when nothing qualifies. Pure and deterministic.
    """
    eligible: list[ProviderCapabilities] = []
    rejected: dict[str, tuple[str, ...]] = {}
    for caps in candidates:
        missing = missing_requirements(
            caps, language=language,
            require_ephemeral_credentials=require_ephemeral_credentials,
        )
        if missing:
            rejected[caps.name] = missing
        else:
            eligible.append(caps)
    if not eligible:
        raise VoiceError(
            VoiceErrorClass.CAPABILITY_MISSING,
            "no provider satisfies ConversationRealtime "
            f"(speech_to_speech, full_duplex, barge_in, tool_calling, {language})",
            details={"rejected": {k: list(v) for k, v in rejected.items()}},
        )
    eligible.sort(key=lambda c: rank_key(c, preference_order))
    winner = eligible[0]
    return SelectionResult(
        selected=winner,
        ranked=tuple(c.name for c in eligible),
        rejected=rejected,
        reasons=_reasons(winner, preference_order),
        transport=preferred_transport(winner),
    )


__all__ = [
    "CONVERSATION_REQUIREMENTS",
    "SelectionResult",
    "missing_requirements",
    "preferred_transport",
    "rank_key",
    "select_conversation_provider",
]
