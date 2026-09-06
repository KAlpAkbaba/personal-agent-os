"""Wires observation intake to the ledger and the UI-state bus
(M18_HOLOGRAPHIC_CORE_SPEC.md §5).

Two rules from the spec, both enforced here rather than left to callers:

* **Ledger events for meaningful transitions only.** ``PresenceFusionEngine.
  add_observation`` already tells us whether its OWN state changed; a ledger
  row is written only when it did — never once per observation (spec §5:
  "not every observation - do not overcollect"). Same idea as
  ``app.goals.service``'s ledger notes: best-effort, never fails the caller.
* **The Active Eye disable path stops perception immediately.** A
  camera-sourced observation is refused, before it ever reaches the fusion
  engine, whenever :func:`app.presence.eye.is_eye_enabled` reads False —
  checked fresh against the database on every call (see that module's
  docstring for why there is no cached flag).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_PRESENCE_GREETING_DELIVERED,
    EVENT_TYPE_PRESENCE_STATE_CHANGED,
    SUBSYSTEM_PRESENCE,
)
from app.logging import get_logger
from app.presence.engine import PresenceFusionEngine, get_engine
from app.presence.eye import is_eye_enabled
from app.presence.greeting import (
    DEFAULT_GREETING_POLICY,
    GreetingDecision,
    GreetingPolicy,
    evaluate_greeting,
)
from app.presence.observations import Observation, parse_observation
from app.presence.states import PresenceAssertion, PresenceState
from app.uistate.contract import UiState
from app.uistate.publisher import publish

logger = get_logger("app.presence.service")

#: Only these six of the seven states are owner-facing UI events (spec §1's
#: state list has no "owner.unknown" — UNKNOWN is the honest internal answer
#: for "not enough evidence", not something to announce).
_UI_STATE_BY_PRESENCE: dict[PresenceState, UiState] = {
    PresenceState.PRESENT: UiState.OWNER_PRESENT,
    PresenceState.AWAY: UiState.OWNER_AWAY,
    PresenceState.RETURNED: UiState.OWNER_RETURNED,
    PresenceState.AWAKE: UiState.OWNER_AWAKE,
    PresenceState.RESTING: UiState.OWNER_RESTING,
    PresenceState.LIKELY_ASLEEP: UiState.OWNER_LIKELY_ASLEEP,
}


class EyeDisabledError(ValueError):
    """Raised when a camera-sourced observation arrives while the Active Eye
    is disabled. Not a schema problem — refused for a policy reason, and the
    route maps it to its own status code so a client can tell the two apart."""


def _record_transition(session: Session, assertion: PresenceAssertion) -> None:
    """Best-effort, like every other subsystem's ledger note
    (``app.goals.service._record_ledger`` docstring): the ledger is
    evidence, never a hard dependency of the fusion path itself."""
    try:
        ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_PRESENCE_STATE_CHANGED,
                subsystem=SUBSYSTEM_PRESENCE,
                action="state_changed",
                factual_summary=f"owner presence changed to {assertion.state.value}",
                source="presence_engine",
                source_ref=f"presence-transition:{assertion.state.value}:{assertion.observed_at.isoformat()}",
                occurred_at=assertion.observed_at,
                detail_json={
                    "to_state": assertion.state.value,
                    "confidence": assertion.confidence,
                    "reason": assertion.reason,
                    "signal_sources": sorted({s.source for s in assertion.signals}),
                    "signal_count": len(assertion.signals),
                },
            ),
        )
    except Exception:  # noqa: BLE001 - ledger is evidence, never a hard dependency
        logger.warning("presence_transition_ledger_note_failed", state=assertion.state.value)

    _publish_presence(assertion)
    _note_published(assertion)


#: When a held state was last put on the UI-state bus. The bus is published on CHANGE, but
#: the Core expires an observation after its ttl_s - so a state held for ten minutes on fresh
#: signals went "unknown" on the Core after ninety seconds while the engine still held it
#: (2026-09-06 owner run: `away` held 09:43-09:54, published once). A heartbeat republish
#: keeps the Core's claim alive exactly as long as the engine's evidence does - and never
#: republishes UNKNOWN, which is the absence of a claim.
_last_published_at: datetime | None = None
_last_published_state: PresenceState | None = None


def _note_published(assertion: PresenceAssertion) -> None:
    global _last_published_at, _last_published_state
    _last_published_at = datetime.now(UTC)
    _last_published_state = assertion.state


def _publish_presence(assertion: PresenceAssertion) -> None:
    ui_state = _UI_STATE_BY_PRESENCE.get(assertion.state)
    if ui_state is not None:
        publish(
            ui_state,
            subsystem="presence",
            # NOT `intensity`. The contract defines intensity as "how much is going
            # on" and the Core's wording describes it that way, so sending a
            # confidence there would have the renderer draw certainty as activity.
            # Confidence is its own metadata figure, and the client reads it as one.
            status=assertion.state.value,
            label=assertion.reason or None,
            metadata={
                "confidence": assertion.confidence,
                "signals": len(assertion.signals),
                # How long this observation is good for. The engine owns the
                # staleness policy; publishing it means a client never has to guess,
                # and an assertion that has aged out degrades to unknown rather than
                # to "still present" on both sides of the wire.
                "ttl_s": assertion.stale_after_s,
            },
        )


def heartbeat_due(assertion: PresenceAssertion, *, now: datetime | None = None) -> bool:
    """A held, non-UNKNOWN state is republished once half its TTL has elapsed since the
    last publish, so the Core's claim never expires while the evidence is still fresh."""
    if assertion.state is PresenceState.UNKNOWN:
        return False
    if _last_published_at is None or _last_published_state != assertion.state:
        return True
    moment = now or datetime.now(UTC)
    return (moment - _last_published_at).total_seconds() >= assertion.stale_after_s / 2


def reset_heartbeat() -> None:
    """Tests only: forget the last publish."""
    global _last_published_at, _last_published_state
    _last_published_at = None
    _last_published_state = None


def ingest_observation(
    session: Session,
    payload: dict[str, Any],
    *,
    engine: PresenceFusionEngine | None = None,
    now: datetime | None = None,
) -> tuple[Observation, PresenceAssertion, bool]:
    """Validate, screen, fuse, and — only on a real state change — record.

    Raises :class:`app.presence.observations.ObservationRejected` for any
    schema/privacy violation, and :class:`EyeDisabledError` for a
    camera-sourced observation while perception is off. Both are the
    caller's (the route's) responsibility to translate into an HTTP status.
    """
    observation = parse_observation(payload)
    if observation.source == "camera" and not is_eye_enabled(session):
        raise EyeDisabledError(
            "the Active Eye is disabled; camera observations are refused until re-enabled"
        )

    eng = engine or get_engine()
    assertion, changed = eng.add_observation(observation, now=now)
    if changed:
        _record_transition(session, assertion)
    elif heartbeat_due(assertion, now=now):
        # Nothing changed, but the Core would otherwise let a still-current state expire.
        # A republish, not a ledger row: the ledger records transitions only.
        _publish_presence(assertion)
        _note_published(assertion)
    return observation, assertion, changed


#: The assertion reason, and the World Model uncertainty reason, after the owner disabled
#: the eye (docs/M18_ACTION_CONTRACT.md §5.4).
REASON_EYE_DISABLED = "eye_disabled"


def on_eye_disabled(
    now: datetime | None = None, *, engine: PresenceFusionEngine | None = None
) -> PresenceAssertion:
    """The owner disabled the Active Eye: invalidate the camera evidence NOW
    (docs/M18_ACTION_CONTRACT.md §5.4).

    Called by ``app.presence.eye.disable_eye`` on a REAL change of the durable flag
    (never on an idempotent repeat). Camera-sourced observations leave the fusion
    window, the current assertion becomes UNKNOWN with reason ``eye_disabled``, the
    degraded state is published once, and the heartbeat forgets the state it was
    keeping alive - otherwise the Core would go on showing "present" for up to a
    camera TTL after the owner said "stop watching me", republished by the heartbeat
    from frames the owner had just forbidden.

    There is no ``owner.unknown`` UI state by design (``_UI_STATE_BY_PRESENCE``:
    UNKNOWN is the absence of a claim). The one publish is therefore ``eye.disabled``
    carrying ``presence: unknown`` in its metadata, which is what actually happened:
    the eye closed, and with it the presence claim. No ledger row: the ``eye.disabled``
    event already records the owner's action, and the ledger records transitions the
    engine made from evidence, not evidence it was told to forget.
    """
    moment = now or datetime.now(UTC)
    eng = engine or get_engine()
    assertion = eng.invalidate_source("camera", now=moment, reason=REASON_EYE_DISABLED)
    reset_heartbeat()
    publish(
        UiState.EYE_DISABLED,
        subsystem="presence",
        status="presence_invalidated",
        label=REASON_EYE_DISABLED,
        metadata={"presence": assertion.state.value, "reason": assertion.reason, "ttl_s": 0},
    )
    return assertion


def last_greeted_at(session: Session) -> datetime | None:
    """When a greeting was last actually DELIVERED, from the ledger.

    The cooldown is accounted from delivery, so this reads the delivery record and
    nothing else. It survives a process restart because the ledger does.
    """
    rows = ledger_service.query(
        session,
        subsystems=[SUBSYSTEM_PRESENCE],
        event_types=[EVENT_TYPE_PRESENCE_GREETING_DELIVERED],
        limit=1,
    )
    return rows[0].occurred_at if rows else None


def evaluate_greeting_now(
    session: Session,
    *,
    engine: PresenceFusionEngine | None = None,
    policy: GreetingPolicy = DEFAULT_GREETING_POLICY,
    now: datetime | None = None,
) -> GreetingDecision:
    """Evaluate the greeting policy against the engine's real episode history and the
    ledger's own cooldown record. **Pure**: it decides, and changes nothing.

    It used to record ``presence.greeting_delivered`` whenever the decision came out
    true - while its own docstring said it does not speak or notify the owner. Both
    could not be so. An evaluation that marks a greeting as delivered starts the
    cooldown for a greeting nobody heard, and then suppresses the real one for the
    whole window; worse, the ledger would carry a delivery that never happened.

    Deciding and delivering are now separate calls. Whoever actually narrates the
    greeting calls :func:`record_greeting_delivered` afterwards, and only then.
    """
    moment = now or datetime.now(UTC)
    eng = engine or get_engine()
    return evaluate_greeting(
        eng.episodes(), now=moment, last_greeted_at=last_greeted_at(session), policy=policy
    )


def record_greeting_delivered(
    session: Session, decision: GreetingDecision, *, now: datetime | None = None
) -> None:
    """Record that a greeting was DELIVERED - which starts the cooldown.

    Called by whatever actually narrated it, after it narrated it. Refuses a decision
    that did not say to greet, because a cooldown started by a refusal would silence
    the next real greeting.
    """
    if not decision.should_greet:
        raise ValueError(
            "record_greeting_delivered called with a decision that refused to greet: "
            f"{decision.reason!r}"
        )
    moment = now or datetime.now(UTC)
    try:
        ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_PRESENCE_GREETING_DELIVERED,
                subsystem=SUBSYSTEM_PRESENCE,
                action="greeting_delivered",
                factual_summary="greeting delivered after sustained wake following sustained rest",
                source="presence_engine",
                source_ref=f"presence-greeting:{moment.isoformat()}",
                occurred_at=moment,
                detail_json=decision.as_dict(),
            ),
        )
    except Exception:  # noqa: BLE001 - ledger is evidence, never a hard dependency
        logger.warning("presence_greeting_ledger_note_failed")


__all__ = [
    "REASON_EYE_DISABLED",
    "EyeDisabledError",
    "on_eye_disabled",
    # `record_greeting_delivered` and `last_greeted_at` were missing here even though both
    # are public API other modules call directly (app.routines.presence_link calls the
    # former; this module's own docstring for evaluate_greeting_now describes the pairing
    # with the latter) — found while auditing this module for M18 routine dispatch
    # (ADR-0060). A `from app.presence.service import *` would not have re-exported either
    # one, silently.
    "evaluate_greeting_now",
    "heartbeat_due",
    "ingest_observation",
    "last_greeted_at",
    "record_greeting_delivered",
    "reset_heartbeat",
]
