"""The one seam between the Routine Engine and the Presence Engine.

``app/routines`` deliberately does not import ``app/presence`` anywhere else: it was
written against three published event names and nothing more, so a change to the fusion
engine cannot reach into routine evaluation. This module is the single, named exception —
the place where "is the owner here?" is answered by the subsystem that actually knows,
rather than by whoever called the endpoint.

That distinction is the point. ``RoutineConditionContext.owner_present`` was a boolean the
HTTP caller supplied, which means a routine's ``owner_present`` condition could pass on a
client's assertion about a room the client cannot see. Now:

* the server resolves presence from the fusion engine when the caller says nothing;
* a caller may still assert it — some callers genuinely know something the camera does not —
  but the assertion is **labelled**, and the firing record says which of the two it was;
* a stale assertion resolves to ``None``, never to ``True`` and never to ``False``. The
  engine already degrades a stale state to ``UNKNOWN``; this module just refuses to launder
  that back into a boolean.

"Present" here means *in the room*, not *awake*. An owner who is resting or likely asleep is
present, and it is the quiet-hours condition and the greeting policy — not this function —
that decide whether it is acceptable to act. Collapsing the two would silently make every
presence-gated routine also a wakefulness-gated one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy.orm import Session

from app.presence import service as presence_service
from app.presence.engine import get_engine
from app.presence.states import PresenceState

#: Where the ``owner_present`` value in a condition context came from. Recorded so a
#: routine that fired on real perception is distinguishable from one that fired on a
#: caller's claim, after the fact and without re-running anything.
SOURCE_PRESENCE_ENGINE: Final = "presence_engine"
SOURCE_CALLER: Final = "caller"
SOURCE_UNKNOWN: Final = "unknown"
SOURCE_STALE: Final = "presence_stale"
SOURCE_NEVER_OBSERVED: Final = "presence_never_observed"

#: Every state except AWAY means the owner is in the room. UNKNOWN is handled separately
#: because it is not a state about the room at all - it is a statement about the evidence.
_AWAY_STATES: Final[frozenset[PresenceState]] = frozenset({PresenceState.AWAY})


def resolve_owner_present(*, now: datetime | None = None) -> tuple[bool | None, str]:
    """``(owner_present, source)`` from the fusion engine.

    ``None`` whenever the honest answer is "we do not know": nothing has ever been
    observed, or what was observed has aged out. A routine whose condition needs presence
    then does not fire, which is the safe direction — the alternative is acting on a guess
    about whether someone is in the room.
    """
    assertion = get_engine().current()
    if assertion is None:
        return None, SOURCE_NEVER_OBSERVED
    # `effective_state` is where staleness is enforced: it returns UNKNOWN once the
    # assertion has aged past its own TTL, rather than the state it used to hold.
    state = assertion.effective_state(now=now)
    if state is PresenceState.UNKNOWN:
        stale = assertion.is_stale(now=now)
        return None, SOURCE_STALE if stale else SOURCE_UNKNOWN
    return state not in _AWAY_STATES, SOURCE_PRESENCE_ENGINE


def resolve_greeting_allowed(session: Session, *, now: datetime | None = None) -> tuple[bool, str]:
    """``(allowed, reason)`` from the Presence Engine's own greeting policy.

    Evaluation only - it does not record a delivery and so does not start the cooldown.
    That matters here more than anywhere: a condition is evaluated on every
    ``POST /v1/routines/evaluate``, including the calls where a later condition fails and
    nothing is ever narrated. A side effect there would burn the morning greeting on a
    routine that never fired.
    """
    decision = presence_service.evaluate_greeting_now(session, now=now)
    return decision.should_greet, decision.reason


__all__ = [
    "SOURCE_CALLER",
    "SOURCE_NEVER_OBSERVED",
    "SOURCE_PRESENCE_ENGINE",
    "SOURCE_STALE",
    "SOURCE_UNKNOWN",
    "resolve_greeting_allowed",
    "resolve_owner_present",
]
