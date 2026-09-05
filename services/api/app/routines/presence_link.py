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

The greeting cooldown (ADR-0060). ``resolve_greeting_decision``/``greeting_verdict`` split
what used to be one call (``resolve_greeting_allowed``) into "evaluate the policy" and "read
the verdict off what it returned", because ``app.routines.service`` needs the WHOLE decision
object, not just its two summary fields: only the caller that actually narrated a briefing is
allowed to start the cooldown, via ``record_greeting_delivered``, and it needs the original
``GreetingDecision`` to do that (``app.presence.service.record_greeting_delivered`` refuses a
decision that never said to greet). ``resolve_greeting_allowed`` stays exactly as it was for
every existing caller — it is now implemented on top of the pair below, so the policy is
still evaluated in exactly one place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.presence import service as presence_service
from app.presence.engine import get_engine
from app.presence.greeting import GreetingDecision
from app.presence.states import PresenceState

logger = get_logger("app.routines.presence_link")

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


def resolve_greeting_decision(session: Session, *, now: datetime | None = None) -> GreetingDecision:
    """The Presence Engine's greeting policy, evaluated whole (ADR-0060).

    Pure — evaluating it has no side effect, deliberately: ``POST /v1/routines/evaluate``
    calls this on every tick, including the ticks where a later condition fails and nothing
    is ever narrated. A side effect here would burn the morning greeting on a routine that
    never fired. ``app.routines.service`` is the only caller allowed to turn a
    ``should_greet`` verdict into a started cooldown, via :func:`record_greeting_delivered`,
    and only after a briefing was actually delivered.
    """
    return presence_service.evaluate_greeting_now(session, now=now)


def greeting_verdict(decision: GreetingDecision) -> tuple[bool, str]:
    """``(allowed, reason)`` — the shape ``RoutineConditionContext`` wants, unpacked from
    the decision object so a caller that only needs the boolean need not know its shape."""
    return decision.should_greet, decision.reason


def resolve_greeting_allowed(session: Session, *, now: datetime | None = None) -> tuple[bool, str]:
    """``(allowed, reason)`` from the Presence Engine's own greeting policy.

    Kept for every existing caller: implemented on top of :func:`resolve_greeting_decision`
    / :func:`greeting_verdict` so the policy is evaluated in exactly one place, not two.
    """
    return greeting_verdict(resolve_greeting_decision(session, now=now))


def record_greeting_delivered(
    session: Session, decision: GreetingDecision, *, now: datetime | None = None
) -> bool:
    """Start the greeting cooldown — AFTER, and only after, a briefing was actually
    narrated (ADR-0060). Returns whether the cooldown actually started.

    ``app.presence.service.record_greeting_delivered`` raises on a decision that never said
    to greet (a cooldown started by a refusal would silence the next real greeting). A
    caller reaching this function with such a decision has already checked ``should_greet``
    itself in every path this package builds, so this is defence in depth, not the primary
    guard: log and report ``False`` rather than let a caller's logic error surface as an
    unrelated 500 from deep inside routine evaluation.
    """
    if not decision.should_greet:
        logger.warning(
            "record_greeting_delivered_called_with_refused_decision", reason=decision.reason
        )
        return False
    presence_service.record_greeting_delivered(session, decision, now=now)
    return True


__all__ = [
    "SOURCE_CALLER",
    "SOURCE_NEVER_OBSERVED",
    "SOURCE_PRESENCE_ENGINE",
    "SOURCE_STALE",
    "SOURCE_UNKNOWN",
    "greeting_verdict",
    "record_greeting_delivered",
    "resolve_greeting_allowed",
    "resolve_greeting_decision",
    "resolve_owner_present",
]
