"""The seam between a routine firing and the wake sequence (M18.3 spec §3.1, §3.5).

``app.routines.dispatch.WakeAlarmPort`` is the ``Protocol``; this is its one
implementation. It owns a session factory because the routine dispatcher runs inside the
routine engine's own transaction and the wake sequence must not: the sequence commits state
transitions as it goes (FIRING, DISPLAY_WAKING, MEDIA_STARTING, PLAYING), and those must be
durable BEFORE the physical steps that follow them, so a process that dies mid-sequence
leaves a record of exactly how far it got instead of an all-or-nothing rollback that would
say the alarm never fired while the speaker was audibly ringing.

A dispatcher exception is not a possibility this class leaves open: every failure becomes a
``DispatchOutcome`` the routine engine records, so the firing's ``dispatch_results`` and its
``routine.action_failed`` ledger row tell the truth about a wake-up that did not happen.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.alarms import service as alarms_service
from app.alarms.sequence import WakeSequence
from app.logging import get_logger
from app.routines.actions import DispatchOutcome

logger = get_logger("app.alarms.routine_port")


class WakeAlarmRunner:
    """Runs one alarm's wake sequence in its own session (module docstring)."""

    def __init__(
        self, *, session_factory: sessionmaker[Session], sequence: WakeSequence
    ) -> None:
        self._session_factory = session_factory
        self._sequence = sequence

    def fire(self, *, alarm_id: UUID, routine_id: UUID, firing_id: UUID) -> DispatchOutcome:
        del routine_id  # the alarm is the aggregate; the routine is only how it was timed
        session = self._session_factory()
        try:
            decision = alarms_service.fire_alarm(
                session, alarm_id, sequence=self._sequence, firing_id=firing_id
            )
        except Exception as exc:  # noqa: BLE001 - a wake-up that failed must say so
            logger.error(
                "wake_alarm_sequence_failed",
                alarm_id=str(alarm_id),
                error=f"{type(exc).__name__}: {exc}",
            )
            return DispatchOutcome.failed(
                f"wake_alarm_exception:{type(exc).__name__}",
                {"alarm_id": str(alarm_id), "error": str(exc)[:200]},
            )
        finally:
            session.close()

        detail: dict[str, object] = {"alarm_id": str(alarm_id)}
        if decision.result is not None:
            detail.update(decision.result.as_dict())
        if decision.fired:
            return DispatchOutcome.succeeded(detail)
        # "already_firing" / "firing_already_handled" are the idempotency answers: the alarm
        # IS ringing, this particular call simply did not start it. Reporting them as
        # failures would turn a correct duplicate-suppression into a critical UiState.ERROR
        # every time a tick overlapped a dispatch.
        if decision.reason in ("already_firing", "firing_already_handled"):
            return DispatchOutcome.succeeded({**detail, "deduplicated": decision.reason})
        return DispatchOutcome.failed(f"wake_alarm_not_fired:{decision.reason}", detail)


__all__ = ["WakeAlarmRunner"]
