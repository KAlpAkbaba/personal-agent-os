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
    return observation, assertion, changed


def evaluate_greeting_now(
    session: Session,
    *,
    engine: PresenceFusionEngine | None = None,
    policy: GreetingPolicy = DEFAULT_GREETING_POLICY,
    now: datetime | None = None,
) -> GreetingDecision:
    """Evaluate the greeting policy against the engine's real episode
    history and the ledger's own cooldown record, and durably record a
    delivered greeting so the cooldown holds across process restarts.

    This does not itself speak or notify the owner — narrating a greeting is
    ``app.routines``'/``app.voice``'s job (M18 item 3, not built by this
    change). This is the decision the routine engine's trigger will call.
    """
    moment = now or datetime.now(UTC)
    eng = engine or get_engine()
    last_rows = ledger_service.query(
        session,
        subsystems=[SUBSYSTEM_PRESENCE],
        event_types=[EVENT_TYPE_PRESENCE_GREETING_DELIVERED],
        limit=1,
    )
    last_greeted_at = last_rows[0].occurred_at if last_rows else None

    decision = evaluate_greeting(
        eng.episodes(), now=moment, last_greeted_at=last_greeted_at, policy=policy
    )
    if decision.should_greet:
        try:
            ledger_service.record(
                session,
                ledger_service.ActivityEvent(
                    event_type=EVENT_TYPE_PRESENCE_GREETING_DELIVERED,
                    subsystem=SUBSYSTEM_PRESENCE,
                    action="greeting_delivered",
                    factual_summary="greeting conditions met: sustained wake after sustained rest",
                    source="presence_engine",
                    source_ref=f"presence-greeting:{moment.isoformat()}",
                    occurred_at=moment,
                    detail_json=decision.as_dict(),
                ),
            )
        except Exception:  # noqa: BLE001 - ledger is evidence, never a hard dependency
            logger.warning("presence_greeting_ledger_note_failed")
    return decision


__all__ = [
    "EyeDisabledError",
    "evaluate_greeting_now",
    "ingest_observation",
]
