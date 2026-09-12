"""B05 req 678: the gate every permanent deletion goes through.

Owner decision, 2026-09-13, asked because this is a privacy policy and not an implementation
detail. Two operations, deliberately different:

* **Soft delete** is the default and it is reversible. The owner can ask for it by voice in
  one step, because nothing is destroyed - the row is marked and stops being shown.
* **Permanent destruction** is the irreversible one, and it needs a confirmation from a
  SECOND CHANNEL: something the owner clicked or typed, never an utterance.

The reason for the second channel is the same reason the step-up policy exists: a voice is a
factor, not a key. An utterance is the easiest thing in this system to produce by accident -
a television, a guest, a misheard word - and destruction is the one action with nothing
behind it to undo the mistake. So voice may PROPOSE a permanent delete; it may not complete
one.

**What this module is.** The gate and the vocabulary, not the delete surfaces. File delete
(req 159, B34), event cancellation (354, B46) and artifact deletion (412, B42) each arrive in
their own batch and call in here; the policy exists first so that all three inherit one rule
rather than inventing three.

**One named exception, because a gate with an unlisted bypass is not a gate.**
``app.memory.service.forget_memory`` already performs a hard delete and deliberately does
NOT come through here. Forgetting is the owner exercising a privacy right over their own
record, and requiring them to go and click something before the system will stop remembering
what they asked it to forget gets that backwards. It keeps its own, older gate (an
explicit/pinned memory can only be forgotten by the OWNER actor, never by a policy or the
Evolution Engine). :data:`GATE_EXEMPT` names it, and `test_the_only_exempt_deletion_is_the
_one_we_decided_on` fails if a second permanent deletion quietly joins it.

**Confirmation is bound to one subject and used once.** A confirmation names the exact thing
it destroys and expires. Otherwise "yes" clicked for one file would authorise the deletion of
another, which is precisely how a confirmation dialog becomes a formality.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.logging import get_logger

logger = get_logger("app.security.deletion")

#: The channel a confirmation was PRODUCED on. Note this is not simply the session's
#: `client_kind`: a voice session's client_kind is whatever client hosts the microphone
#: ("web", usually), so reading client_kind alone would let an utterance in a browser tab
#: look exactly like a click in the Cockpit. The voice relay stamps ``CHANNEL_VOICE``
#: explicitly, and every other caller passes the REST request's own client_kind.
CHANNEL_VOICE: Final[str] = "voice"

#: Channels a permanent-delete confirmation may come from: the owner clicked or typed it.
#: `cli` is the owner at a shell on a machine they are sitting at.
CONFIRMING_CHANNELS: Final[frozenset[str]] = frozenset({"web", "mobile", "desktop", "cli"})

#: Channels a destruction may NOT be confirmed from. Listed rather than left as "anything
#: not above", so a channel added later is a decision and not an omission. `device` is the
#: agent process, not a person: a machine confirming its own destruction request is not a
#: second channel, it is the same one twice.
NON_CONFIRMING_CHANNELS: Final[frozenset[str]] = frozenset({CHANNEL_VOICE, "device"})

assert not (CONFIRMING_CHANNELS & NON_CONFIRMING_CHANNELS), "a channel cannot be both"

#: How long a confirmation is good for. Short: it is the gap between the owner reading what
#: they are about to destroy and it being destroyed, and nothing about that needs an hour.
CONFIRMATION_TTL: Final[timedelta] = timedelta(minutes=5)

#: Permanent deletions that deliberately do not pass this gate, and why. Not a list of
#: things nobody got round to: each entry is a decision, and the test that reads it exists so
#: that adding a second one has to be a decision too.
GATE_EXEMPT: Final[dict[str, str]] = {
    "app.memory.service.forget_memory": (
        "the owner exercising a privacy right over their own record; making them click "
        "first to be forgotten gets the right backwards. Keeps its own owner-actor gate."
    ),
}

MODE_SOFT: Final[str] = "soft"
MODE_PERMANENT: Final[str] = "permanent"

REASON_NO_CONFIRMATION: Final[str] = "no_confirmation"
REASON_WRONG_SUBJECT: Final[str] = "confirmation_names_another_subject"
REASON_EXPIRED: Final[str] = "confirmation_expired"
REASON_VOICE_CHANNEL: Final[str] = "voice_cannot_confirm_destruction"
REASON_UNKNOWN_CHANNEL: Final[str] = "unknown_channel"

#: tr-TR first-class: what the owner hears when a permanent delete is refused.
SPEECH_BY_REASON: Final[dict[str, str]] = {
    REASON_VOICE_CHANNEL: (
        "Kalıcı silmeyi sesle onaylayamam efendim; panelden onaylamanız gerekiyor."
    ),
    REASON_NO_CONFIRMATION: "Kalıcı silme için onayınızı alamadım efendim.",
    REASON_WRONG_SUBJECT: "Onay başka bir şey için verilmişti efendim; bunu silemem.",
    REASON_EXPIRED: "Onayın süresi doldu efendim; tekrar onaylamanız gerekiyor.",
    REASON_UNKNOWN_CHANNEL: "Bu kanaldan kalıcı silmeyi onaylayamam efendim.",
}


class PermanentDeleteRefused(PermissionError):
    """Raised when a permanent deletion is attempted without a valid confirmation."""

    def __init__(self, decision: DeletionDecision) -> None:
        super().__init__(f"permanent delete refused: {decision.reason}")
        self.decision = decision


@dataclasses.dataclass(frozen=True, slots=True)
class DeletionConfirmation:
    """A confirmation the owner produced, for ONE subject, on ONE channel, at one moment."""

    subject_kind: str
    subject_id: str
    channel: str
    confirmed_at: datetime
    #: The owner session it was produced on, for the audit.
    owner_session_id: uuid.UUID | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class DeletionDecision:
    allowed: bool
    mode: str
    reason: str | None
    subject_kind: str
    subject_id: str

    @property
    def speech(self) -> str | None:
        return SPEECH_BY_REASON.get(self.reason or "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "mode": self.mode,
            "reason": self.reason,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
        }


def evaluate_soft_delete(*, subject_kind: str, subject_id: str) -> DeletionDecision:
    """Always allowed. Reversible by construction, so voice can ask for it in one step.

    A function rather than an implicit yes: the delete surfaces call ONE module for both
    modes, so nothing has to remember which of the two it is holding.
    """
    return DeletionDecision(
        allowed=True,
        mode=MODE_SOFT,
        reason=None,
        subject_kind=subject_kind,
        subject_id=subject_id,
    )


def evaluate_permanent_delete(
    *,
    subject_kind: str,
    subject_id: str,
    confirmation: DeletionConfirmation | None,
    now: datetime | None = None,
) -> DeletionDecision:
    """Whether this destruction may proceed. Never raises; the caller decides how to refuse."""
    moment = now or datetime.now(UTC)

    def refuse(reason: str) -> DeletionDecision:
        return DeletionDecision(
            allowed=False,
            mode=MODE_PERMANENT,
            reason=reason,
            subject_kind=subject_kind,
            subject_id=subject_id,
        )

    if confirmation is None:
        return refuse(REASON_NO_CONFIRMATION)
    if confirmation.channel in NON_CONFIRMING_CHANNELS:
        return refuse(REASON_VOICE_CHANNEL)
    if confirmation.channel not in CONFIRMING_CHANNELS:
        return refuse(REASON_UNKNOWN_CHANNEL)
    if (confirmation.subject_kind, confirmation.subject_id) != (subject_kind, subject_id):
        return refuse(REASON_WRONG_SUBJECT)
    confirmed = confirmation.confirmed_at
    if confirmed.tzinfo is None:
        confirmed = confirmed.replace(tzinfo=UTC)
    if moment - confirmed > CONFIRMATION_TTL:
        return refuse(REASON_EXPIRED)
    return DeletionDecision(
        allowed=True,
        mode=MODE_PERMANENT,
        reason=None,
        subject_kind=subject_kind,
        subject_id=subject_id,
    )


def require_permanent_delete(
    *,
    subject_kind: str,
    subject_id: str,
    confirmation: DeletionConfirmation | None,
    now: datetime | None = None,
) -> DeletionDecision:
    """`evaluate_permanent_delete`, but raising. The form a delete surface should call."""
    decision = evaluate_permanent_delete(
        subject_kind=subject_kind,
        subject_id=subject_id,
        confirmation=confirmation,
        now=now,
    )
    if not decision.allowed:
        logger.warning(
            "permanent_delete_refused",
            subject_kind=subject_kind,
            subject_id=subject_id,
            reason=decision.reason,
        )
        raise PermanentDeleteRefused(decision)
    logger.info(
        "permanent_delete_authorised",
        subject_kind=subject_kind,
        subject_id=subject_id,
        channel=confirmation.channel if confirmation else None,
    )
    return decision


__all__ = [
    "CHANNEL_VOICE",
    "CONFIRMATION_TTL",
    "CONFIRMING_CHANNELS",
    "GATE_EXEMPT",
    "MODE_PERMANENT",
    "MODE_SOFT",
    "NON_CONFIRMING_CHANNELS",
    "REASON_EXPIRED",
    "REASON_NO_CONFIRMATION",
    "REASON_UNKNOWN_CHANNEL",
    "REASON_VOICE_CHANNEL",
    "REASON_WRONG_SUBJECT",
    "SPEECH_BY_REASON",
    "DeletionConfirmation",
    "DeletionDecision",
    "PermanentDeleteRefused",
    "evaluate_permanent_delete",
    "evaluate_soft_delete",
    "require_permanent_delete",
]
