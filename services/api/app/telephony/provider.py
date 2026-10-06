"""The telephony provider interface (CLAUDE.md: third parties behind provider interfaces).

Twilio is the one implementation today (``app.telephony.twilio``). The caller
(``app.telephony.service``) speaks only this: place a call with TwiML, ask its status.
"""

from __future__ import annotations

import dataclasses
from typing import Final, Protocol

#: Twilio's call statuses, grouped by what the caller does with them. "completed" means the
#: call was picked up - by the owner or by his voicemail; the trial gives no way to tell.
ANSWERED: Final[frozenset[str]] = frozenset({"completed"})
IN_FLIGHT: Final[frozenset[str]] = frozenset({"queued", "initiated", "ringing", "in-progress"})
NOT_ANSWERED: Final[frozenset[str]] = frozenset({"busy", "no-answer", "failed", "canceled"})


@dataclasses.dataclass(frozen=True, slots=True)
class PlacedCall:
    sid: str
    status: str


class TelephonyError(RuntimeError):
    """The provider refused or could not be reached. The message never carries a credential:
    it is built from a status code and the provider's own error code only."""


class TelephonyProvider(Protocol):
    name: str

    def configured(self) -> bool: ...

    def place_call(self, *, to: str, from_: str, twiml: str) -> PlacedCall: ...

    def call_status(self, sid: str) -> str: ...


__all__ = [
    "ANSWERED",
    "IN_FLIGHT",
    "NOT_ANSWERED",
    "PlacedCall",
    "TelephonyError",
    "TelephonyProvider",
]
