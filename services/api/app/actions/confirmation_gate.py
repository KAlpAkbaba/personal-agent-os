"""The owner-confirmation gate (docs/M21_MAIL_CALENDAR_SPEC.md §1, §3; ADR-0084 decision 1).

One implementation, two users: ``app.mail.service.MailService.send`` and
``app.calendar.service.CalendarService.commit`` both call :func:`check_gate` before
touching a real provider — a mutation runs only when

1. a provider is actually configured (``account_missing`` otherwise — never a guess at
   what a missing account would have done);
2. the target row's own content was read back to the owner in THIS session
   (``read_back_at`` is set — ``not_read_back`` otherwise);
3. the confirmation turn came AFTER that read-back (``confirmed_at`` — the time the
   provider layer stamps this call at — must be strictly later; ``no_confirmation``
   otherwise, which also catches a confirmation that raced ahead of its own read-back);
4. the row is still in its ``prepared`` state (``already_sent`` when it is already
   ``sent``/``committed`` OR already ``discarded`` — a second confirmation must never act
   twice, and the SAME code covers "sent twice" and "confirmed after being discarded":
   both are "there is nothing left here to confirm");
5. the host flag is on (``send_disabled`` — a flag the autonomous system never writes,
   ADR-0084 decision 1's own words).

Every one of the five refusal codes below is the literal string spec §3 names; a caller
must not invent a sixth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

GATE_NOT_READ_BACK = "not_read_back"
GATE_NO_CONFIRMATION = "no_confirmation"
GATE_ALREADY_SENT = "already_sent"
GATE_SEND_DISABLED = "send_disabled"
GATE_ACCOUNT_MISSING = "account_missing"

GATE_REASONS: tuple[str, ...] = (
    GATE_NOT_READ_BACK,
    GATE_NO_CONFIRMATION,
    GATE_ALREADY_SENT,
    GATE_SEND_DISABLED,
    GATE_ACCOUNT_MISSING,
)


@dataclass(frozen=True, slots=True)
class GateResult:
    ok: bool
    reason: str | None = None


def check_gate(
    *,
    state: str,
    prepared_state: str,
    read_back_at: datetime | None,
    confirmed_at: datetime,
    host_flag_enabled: bool,
    provider_available: bool,
) -> GateResult:
    """Pure: no I/O, no database, no clock read of its own — every timestamp is the
    caller's, so a test can construct any ordering it needs without waiting on a clock."""
    if not provider_available:
        return GateResult(False, GATE_ACCOUNT_MISSING)
    if state != prepared_state:
        return GateResult(False, GATE_ALREADY_SENT)
    if read_back_at is None:
        return GateResult(False, GATE_NOT_READ_BACK)
    if confirmed_at <= read_back_at:
        return GateResult(False, GATE_NO_CONFIRMATION)
    if not host_flag_enabled:
        return GateResult(False, GATE_SEND_DISABLED)
    return GateResult(True, None)


__all__ = [
    "GATE_ACCOUNT_MISSING",
    "GATE_ALREADY_SENT",
    "GATE_NO_CONFIRMATION",
    "GATE_NOT_READ_BACK",
    "GATE_REASONS",
    "GATE_SEND_DISABLED",
    "GateResult",
    "check_gate",
]
