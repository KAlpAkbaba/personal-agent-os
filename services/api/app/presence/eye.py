"""The Active Eye disable path (M18_HOLOGRAPHIC_CORE_SPEC.md §2, §6).

"``Gözünü kapat``, ``Kamerayı kapat`` and ``Beni izleme`` stop perception
immediately" (spec §2) and the resulting state "must be durable/observable"
(spec §6). Both are satisfied without a new table:

* **Durable** — every enable/disable is an Activity Ledger row
  (``eye.enabled`` / ``eye.disabled``). The ledger already IS the durable
  record of what happened (ADR-0052 §3); a dedicated boolean column would
  duplicate a fact this table already answers well via
  ``app.ledger.service.latest``.
* **Immediate** — :func:`is_eye_enabled` is read fresh, from the database, on
  every camera-sourced observation the intake route accepts
  (``app.presence.routes``). There is no in-memory flag that could go stale
  relative to a disable recorded seconds ago on another request.
* **Observable** — every enable/disable also publishes the corresponding
  ``eye.active`` / ``eye.disabled`` UI-state event (ADR-0052), so a renderer
  reflects it without polling the ledger.

**Perception is never authentication** (spec §2: "No face or body observation
may act as owner identity, and none may carry production-release
authority"). This module — and every other file in ``app.presence`` —
intentionally exposes no function that could be mistaken for one: nothing
here returns a session, a token, a scope, or an owner identity. The route
that calls this module still requires ``require_owner_session`` like every
other write surface; presence data itself grants nothing (see
``tests/unit/test_presence_routes.py::test_presence_state_requires_owner_session``
for the negative proof).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_EYE_DISABLED,
    EVENT_TYPE_EYE_ENABLED,
    SUBSYSTEM_PRESENCE,
)
from app.logging import get_logger
from app.uistate.contract import UiState
from app.uistate.publisher import publish

logger = get_logger("app.presence.eye")

#: Default when no enable/disable event has ever been recorded — perception
#: is on unless the owner has explicitly turned it off (spec §2's examples
#: are all disable phrases; there is no "please start watching me" default
#: needed because local perception simply is not running until the owner's
#: device turns its camera on in the first place — this flag is the
#: server-side belt to that device-side suspenders).
DEFAULT_EYE_ENABLED = True


def is_eye_enabled(session: Session) -> bool:
    """Read fresh, every time — see module docstring on why there is no
    cached flag.

    ``query()`` is used rather than ``latest()`` because presence also writes
    ``presence.state_changed`` / ``presence.greeting_delivered`` rows into
    the same subsystem; this must look only at the two eye events.
    """
    rows = ledger_service.query(
        session,
        subsystems=[SUBSYSTEM_PRESENCE],
        event_types=[EVENT_TYPE_EYE_ENABLED, EVENT_TYPE_EYE_DISABLED],
        limit=1,
    )
    if not rows:
        return DEFAULT_EYE_ENABLED
    return rows[0].event_type == EVENT_TYPE_EYE_ENABLED


def _set_eye_state(session: Session, *, enabled: bool, reason: str) -> None:
    event_type = EVENT_TYPE_EYE_ENABLED if enabled else EVENT_TYPE_EYE_DISABLED
    action = "enable" if enabled else "disable"
    now = datetime.now(UTC)
    try:
        ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_PRESENCE,
                action=action,
                factual_summary=f"Active Eye {action}d by the owner",
                source="owner",
                # A fresh key per call: each explicit owner action is its own
                # durable event, never deduplicated away (unlike a retried
                # observation write, there is no accidental-retry case here
                # worth collapsing — every real call is a genuine owner ask).
                source_ref=f"presence-eye:{action}:{now.isoformat()}",
                occurred_at=now,
                detail_json={"reason": reason} if reason else {},
            ),
        )
    except Exception:  # noqa: BLE001 - the ledger is evidence, never a hard dependency
        logger.warning("presence_eye_ledger_note_failed", action=action)

    publish(
        UiState.EYE_ACTIVE if enabled else UiState.EYE_DISABLED,
        subsystem="presence",
        status=action,
        label=reason[:64] if reason else None,
    )


def enable_eye(session: Session, *, reason: str = "") -> None:
    _set_eye_state(session, enabled=True, reason=reason)


def disable_eye(session: Session, *, reason: str = "") -> None:
    """Stops perception immediately: after this call,
    ``app.presence.routes`` refuses every subsequent camera-sourced
    observation until re-enabled (spec §2: "stop perception immediately")."""
    _set_eye_state(session, enabled=False, reason=reason)


__all__ = [
    "DEFAULT_EYE_ENABLED",
    "disable_eye",
    "enable_eye",
    "is_eye_enabled",
]
