"""The owner-confirmation gate (docs/M21_MAIL_CALENDAR_SPEC.md §1, §3; ADR-0084 decision 1,
addendum 2).

One implementation, two users: ``app.mail.service.MailService.send`` and
``app.calendar.service.CalendarService.commit`` both call :func:`check_gate` before
touching a real provider — a mutation runs only when

1. a provider is actually configured (``account_missing`` otherwise — never a guess at
   what a missing account would have done);
2. the target row is sitting in ``read_back_state`` (``not_read_back`` when it never got
   there, ``already_sent`` when it has already moved past it — sent/committed/discarded);
3. a :class:`Confirmation` describing THIS call is actually bound to that read-back —
   never a bare "enough time passed" (ADDENDUM 2, replacing the original clock-only
   check H1 found vacuous):

   * a REST confirmation (``Confirmation.source == CONFIRM_SOURCE_REST``) is the owner's
     own authenticated act in an owner session (the Cockpit's Approve button) — the
     source alone is the signal once the row is genuinely ``read_back``; there is no
     "turn" on that surface to check;
   * a VOICE confirmation must be issued by the SAME realtime session that performed the
     read-back (``confirmation.session_id == read_back_session_id`` — a different
     session, even the same owner, has not heard this draft/proposal read back to IT;
     the honest answer is ``not_read_back``, not a guess that the owner remembers), must
     carry a turn STRICTLY AFTER the read-back's own turn (``no_confirmation`` otherwise
     — the ONE router resolved something, but not on a turn that could be this
     confirmation), and — the fix for H1 itself — the CURRENT turn's utterance must have
     been resolved by the ONE deterministic router to the expected intent
     (``confirmation_not_owner`` otherwise: a model calling ``mail.send``/
     ``calendar.commit`` on its own initiative, or steered by a hostile document it just
     read, with no owner "Gönder."/"Onayla." turn behind it, is refused here — never
     reaches a real provider);
   * no :class:`Confirmation` at all is ``no_confirmation`` — a caller that forgot to
     describe how this call claims to be the owner's confirmation gets the same refusal
     as one that tried and failed the turn check, never treated as "good enough".

4. the host flag is on (``send_disabled`` — a flag the autonomous system never writes,
   ADR-0084 decision 1's own words).

Every one of the six refusal codes below is the literal string spec §3 names; a caller
must not invent a seventh.

The actual state TRANSITION (``read_back`` -> ``sending``/``committing`` -> ``sent``/
``committed``, or back to ``read_back`` on a provider failure) is a compare-and-swap the
service layer performs directly against the database (H2: read-check-write is not atomic
under two concurrent confirmations) — this module stays pure, no I/O, no clock read of its
own, so a test can construct any ordering it needs without waiting on a clock or a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

GATE_NOT_READ_BACK = "not_read_back"
GATE_NO_CONFIRMATION = "no_confirmation"
GATE_ALREADY_SENT = "already_sent"
GATE_SEND_DISABLED = "send_disabled"
GATE_ACCOUNT_MISSING = "account_missing"
#: ADDENDUM 2 (H1): the row IS read back, in THIS session, but the CURRENT turn's
#: utterance was not resolved by the router to the expected MAIL_SEND/CALENDAR_COMMIT
#: intent — a model-issued tool call with no owner word behind it.
GATE_CONFIRMATION_NOT_OWNER = "confirmation_not_owner"

GATE_REASONS: tuple[str, ...] = (
    GATE_NOT_READ_BACK,
    GATE_NO_CONFIRMATION,
    GATE_ALREADY_SENT,
    GATE_SEND_DISABLED,
    GATE_ACCOUNT_MISSING,
    GATE_CONFIRMATION_NOT_OWNER,
)

#: The Cockpit's own authenticated act (``POST .../confirm`` in an owner session) — the
#: act itself IS the confirmation; there is no "turn" to bind on this surface.
CONFIRM_SOURCE_REST = "rest"
#: A realtime voice session's tool dispatch — legitimate only when bound to the SAME
#: session's read-back and a LATER turn the router itself resolved to the expected intent.
CONFIRM_SOURCE_VOICE = "voice"

#: Owner decision 2026-09-19 ("Tüm 2. ses onaylarını kaldır, mail hariç"): for a CALENDAR
#: proposal the owner's standing decision replaces the second spoken word - the proposal is
#: committed in the same turn it was made. The gate still requires the row to be read back
#: (the proposal speech IS the read-back), the account to exist and the host flag to be on.
#: Mail never uses this source: a sent mail cannot be taken back, and the owner kept its
#: second word on purpose.
CONFIRM_SOURCE_OWNER_POLICY = "owner_policy"

CONFIRM_SOURCES: tuple[str, ...] = (
    CONFIRM_SOURCE_REST,
    CONFIRM_SOURCE_VOICE,
    CONFIRM_SOURCE_OWNER_POLICY,
)


@dataclass(frozen=True, slots=True)
class Confirmation:
    """How THIS call claims to be the owner's confirmation (module docstring, ADDENDUM 2).

    Built by the caller — ``app.mail.routes``/``app.calendar.routes`` for REST,
    ``app.voice.realtime_sessions.tools_mail``/``tools_calendar`` for voice — never by
    the gate itself: the gate only ever judges a claim it did not make.
    """

    source: str
    #: The REST owner session id, or the voice realtime session id — whichever session
    #: is making THIS call. Compared against the row's own ``read_back_session_id`` for
    #: a voice confirmation; unused for REST (the authenticated act is the signal).
    session_id: str
    #: The voice turn number this confirmation was spoken on. ``None`` for REST (there is
    #: no turn on that surface).
    turn: int | None = None
    #: Voice only: did the CURRENT turn's utterance resolve, through the ONE
    #: deterministic router, to the intent this tool call claims to answer (MAIL_SEND for
    #: ``mail.send``, CALENDAR_COMMIT for ``calendar.commit``)? Computed by the caller
    #: from ``ctx.context["last_utterance"]`` — never trusted from the model's own
    #: argument (H1's own words). Always ``True`` for REST (module docstring).
    owner_intent_ok: bool = False


@dataclass(frozen=True, slots=True)
class GateResult:
    ok: bool
    reason: str | None = None


def check_gate(
    *,
    state: str,
    prepared_state: str,
    read_back_state: str,
    read_back_at: datetime | None,
    read_back_session_id: str | None,
    read_back_turn: int | None,
    confirmation: Confirmation | None,
    host_flag_enabled: bool,
    provider_available: bool,
) -> GateResult:
    """Pure: no I/O, no database, no clock read of its own.

    ``state``, against ``prepared_state`` and ``read_back_state``
    (``app.mail.models.DRAFT_STATE_PREPARED``/``DRAFT_STATE_READ_BACK`` or their
    calendar equivalents), tells the story: still ``prepared`` is "never read back"
    (``not_read_back``); anything past ``read_back`` — sending/committing/sent/committed/
    discarded — is "already gone" (``already_sent``, the same code for "sent twice" and
    "confirmed after being discarded": both are "nothing left here to confirm"); only
    exactly ``read_back`` may proceed to the confirmation check below.
    """
    if not provider_available:
        return GateResult(False, GATE_ACCOUNT_MISSING)
    if state == prepared_state or read_back_at is None:
        return GateResult(False, GATE_NOT_READ_BACK)
    if state != read_back_state:
        return GateResult(False, GATE_ALREADY_SENT)
    if confirmation is None:
        return GateResult(False, GATE_NO_CONFIRMATION)
    if confirmation.source == CONFIRM_SOURCE_REST:
        pass  # the owner-authenticated REST act IS the confirmation (module docstring).
    elif confirmation.source == CONFIRM_SOURCE_OWNER_POLICY:
        # The owner's standing decision (2026-09-19): the same session that just heard the
        # proposal commits it at once. Still the owner's own utterance, resolved by the ONE
        # router, that produced the proposal - never a model's initiative.
        if confirmation.session_id != read_back_session_id:
            return GateResult(False, GATE_NOT_READ_BACK)
        if not confirmation.owner_intent_ok:
            return GateResult(False, GATE_CONFIRMATION_NOT_OWNER)
    elif confirmation.source == CONFIRM_SOURCE_VOICE:
        if confirmation.session_id != read_back_session_id:
            # Never read back TO THIS session - the honest answer is the same one a draft
            # that was never read back at all gets, never a guess that the owner still
            # remembers a different conversation.
            return GateResult(False, GATE_NOT_READ_BACK)
        if not confirmation.owner_intent_ok:
            return GateResult(False, GATE_CONFIRMATION_NOT_OWNER)
        if read_back_turn is not None and (
            confirmation.turn is None or confirmation.turn <= read_back_turn
        ):
            return GateResult(False, GATE_NO_CONFIRMATION)
    else:
        return GateResult(False, GATE_NO_CONFIRMATION)
    if not host_flag_enabled:
        return GateResult(False, GATE_SEND_DISABLED)
    return GateResult(True, None)


__all__ = [
    "CONFIRM_SOURCE_OWNER_POLICY",
    "CONFIRM_SOURCE_REST",
    "CONFIRM_SOURCE_VOICE",
    "CONFIRM_SOURCES",
    "GATE_ACCOUNT_MISSING",
    "GATE_ALREADY_SENT",
    "GATE_CONFIRMATION_NOT_OWNER",
    "GATE_NOT_READ_BACK",
    "GATE_NO_CONFIRMATION",
    "GATE_REASONS",
    "GATE_SEND_DISABLED",
    "Confirmation",
    "GateResult",
    "check_gate",
]
