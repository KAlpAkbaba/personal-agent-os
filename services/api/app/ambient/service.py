"""Ambient display service (M18.3 spec §3.9, §8.2): the policy row, the tick, the test.

``app.ambient.policy.decide`` is the decision and it is pure. This module is everything
around it: reading the owner's persisted policy, assembling the inputs from the live
runtimes, issuing the receipted ``display.off``, waking the display when the owner returns,
and running the owner's display test.

The order of a tick is the order of the risk. Nothing is asked of a device until
``decide`` has said so, and ``decide`` is handed only what was actually read — a presence
assertion that has already been degraded for staleness by ``app.presence.states``, a
display state that is ``None`` when nobody reported one, and an eye flag read from the
durable row. There is no path here that infers "the owner is asleep" from a missing signal.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alarms import service as alarms_service
from app.alarms import speech as alarm_speech
from app.alarms.models import (
    ALARM_PENDING_STATES,
    AMBIENT_POLICY_ID,
    AmbientPolicyRow,
)
from app.alarms.sequence import WakeSequence
from app.ambient.holdoff import (
    SOURCE_ALARM_WAKE,
    SOURCE_INPUT,
    SOURCE_OWNER_COMMAND,
    SOURCE_OWNER_RETURN,
    HoldoffRegistry,
    get_holdoffs,
)
from app.ambient.policy import (
    ACTION_DISPLAY_OFF,
    OWNER_TEST_HOLDOFF_S,
    REASON_OWNER_RETURNED,
    REASON_OWNER_TEST,
    AmbientInputs,
    AmbientPolicy,
    Decision,
    asleep_needed_s,
    decide,
    holdoff_seconds,
    quiet_hours_state,
    validate_quiet_hours,
)
from app.devices.status import DISPLAY_OFF, DeviceStatusRegistry, get_status_registry
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_ACTION_RECEIPT,
    EVENT_TYPE_ALARM_FIRING,
    EVENT_TYPE_AMBIENT_POLICY_CHANGED,
    EVENT_TYPE_OWNER_INPUT_ACTIVE,
    EVENT_TYPE_PRESENCE_STATE_CHANGED,
    SEVERITY_INFO,
    SUBSYSTEM_AMBIENT,
)
from app.logging import get_logger
from app.presence.states import PresenceState
from app.uistate.contract import UiState
from app.uistate.publisher import publish

logger = get_logger("app.ambient.service")

#: Spec §8.2: the owner's display test darkens the screens this many seconds from now by
#: default, so they have time to look away from the keyboard first.
DEFAULT_TEST_DELAY_S = 10
MAX_TEST_DELAY_S = 120


def utcnow() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------- the policy row


def get_policy_row(session: Session) -> AmbientPolicyRow:
    """The single policy row, created with the conservative defaults on first read.

    Creating it lazily rather than in the migration keeps the defaults in ONE place (the
    model), so changing a default is a model edit rather than a model edit plus a data
    migration that only helps installations that never read the row.
    """
    row = session.get(AmbientPolicyRow, AMBIENT_POLICY_ID)
    if row is None:
        row = AmbientPolicyRow(policy_id=AMBIENT_POLICY_ID)
        session.add(row)
        session.commit()
    return row


def get_policy(session: Session) -> AmbientPolicy:
    return AmbientPolicy.from_row(get_policy_row(session))


_EDITABLE_FIELDS = (
    "auto_off_enabled",
    "off_when_away",
    "off_when_asleep",
    "wake_on_return",
    "away_after_s",
    "asleep_after_s",
    "asleep_min_confidence",
    "input_holdoff_s",
    "command_holdoff_s",
    "alarm_holdoff_s",
    "return_holdoff_s",
    "quiet_hours",
    # ADR-0079
    "keep_on",
    "asleep_after_outside_quiet_s",
    "camera_unknown_grace_s",
)


def set_policy(
    session: Session,
    changes: dict[str, Any],
    *,
    holdoffs: HoldoffRegistry | None = None,
    source: str = "owner",
    now: datetime | None = None,
) -> tuple[AmbientPolicy, dict[str, Any]]:
    """Apply the owner's changes, record them, and start the owner-command holdoff.

    Returns the new policy and the fields that ACTUALLY changed — the caller speaks about
    what changed, not about what was asked for, so "otomatik ekran kapatmayı aç" said twice
    does not claim to have turned it on twice.
    """
    moment = now or utcnow()
    row = get_policy_row(session)
    applied: dict[str, Any] = {}
    for field_name in _EDITABLE_FIELDS:
        if field_name not in changes or changes[field_name] is None:
            continue
        value = changes[field_name]
        if field_name == "quiet_hours":
            # ADR-0079 §8: validated on the way in (ValueError to the caller), and ``{}``
            # clears the window - the loop above treats None as "not named".
            value = validate_quiet_hours(value)
        if getattr(row, field_name) != value:
            setattr(row, field_name, value)
            applied[field_name] = value
    row.updated_at = moment
    session.commit()
    policy = AmbientPolicy.from_row(row)

    if applied:
        try:
            ledger_service.record(
                session,
                ledger_service.ActivityEvent(
                    event_type=EVENT_TYPE_AMBIENT_POLICY_CHANGED,
                    subsystem=SUBSYSTEM_AMBIENT,
                    action="ambient_policy_changed",
                    severity=SEVERITY_INFO,
                    factual_summary=(
                        "Ekran otomasyonu ayarı değişti: " + ", ".join(sorted(applied))
                    ),
                    source="live",
                    source_ref=f"ambient:policy:{int(moment.timestamp())}",
                    occurred_at=moment,
                    detail_json={"changed": applied, "source": source},
                ),
            )
        except Exception as exc:  # noqa: BLE001 - the ledger is evidence, not a dependency
            logger.warning("ambient_policy_ledger_failed", error=type(exc).__name__)

    # The owner just spoke about the display: nothing automatic touches it for a while.
    (holdoffs or get_holdoffs()).start(
        SOURCE_OWNER_COMMAND,
        seconds=policy.command_holdoff_s,
        now=moment,
        reason="policy_changed",
    )
    return policy, applied


# ------------------------------------------------------------------- the inputs


@dataclass(frozen=True, slots=True)
class AmbientRuntimes:
    """The live things a tick reads. Injected rather than imported as singletons, the same
    way the World Model routes take theirs (docs/M18_ACTION_CONTRACT.md §4)."""

    statuses: DeviceStatusRegistry | None = None
    holdoffs: HoldoffRegistry | None = None
    presence_engine: Any = None

    def resolved(self) -> AmbientRuntimes:
        return AmbientRuntimes(
            statuses=self.statuses or get_status_registry(),
            holdoffs=self.holdoffs or get_holdoffs(),
            presence_engine=self.presence_engine,
        )


def collect_inputs(
    session: Session, *, runtimes: AmbientRuntimes | None = None, now: datetime | None = None
) -> AmbientInputs:
    """Read the world once, honestly (module docstring).

    Every read is wrapped: a subsystem that is down contributes UNCERTAINTY, never a
    default that happens to allow an action. ``eye_enabled`` defaults to ``False`` on a
    read failure for exactly that reason — no perception means no inference.
    """
    moment = now or utcnow()
    live = (runtimes or AmbientRuntimes()).resolved()

    display_on = None
    alarm_ringing_on_device = False
    try:
        display_on = live.statuses.displays_on() if live.statuses else None
        if live.statuses:
            alarm_ringing_on_device = bool(live.statuses.any_alarm_ringing())
    except Exception as exc:  # noqa: BLE001 - an unreadable registry is "unknown"
        logger.warning("ambient_status_read_failed", error=type(exc).__name__)

    state = PresenceState.UNKNOWN
    confidence = 0.0
    held_s = 0.0
    stale = True
    perception_age_s: float | None = None
    try:
        from app.presence.engine import get_engine

        engine = live.presence_engine or get_engine()
        assertion = engine.current()
        if assertion is not None:
            state = assertion.effective_state(now=moment)
            confidence = assertion.effective_confidence(now=moment)
            held_s = assertion.held_for_s(now=moment)
            stale = assertion.is_stale(now=moment)
        # ADR-0079 §3: when the camera last actually delivered, or None. Read from the
        # engine's own buffer, never inferred from the assertion's age.
        last_camera = engine.last_observation_at(source="camera")
        if last_camera is not None:
            perception_age_s = max(0.0, (moment - last_camera).total_seconds())
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("ambient_presence_read_failed", error=type(exc).__name__)

    eye_enabled = False
    try:
        from app.presence.eye import is_eye_enabled

        eye_enabled = bool(is_eye_enabled(session))
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("ambient_eye_read_failed", error=type(exc).__name__)

    alarm_active = alarm_ringing_on_device
    next_alarm_at: datetime | None = None
    try:
        alarm_active = alarm_active or bool(alarms_service.alarms_ringing(session))
        pending = [
            a
            for a in alarms_service.list_alarms(session, limit=200)
            if a.state in ALARM_PENDING_STATES
        ]
        if pending:
            next_alarm_at = min(a.scheduled_for for a in pending)
            if next_alarm_at.tzinfo is None:
                next_alarm_at = next_alarm_at.replace(tzinfo=UTC)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("ambient_alarm_read_failed", error=type(exc).__name__)

    return AmbientInputs(
        display_on=display_on,
        presence_state=state,
        presence_confidence=confidence,
        presence_held_s=held_s,
        presence_stale=stale,
        eye_enabled=eye_enabled,
        alarm_active=alarm_active,
        next_alarm_at=next_alarm_at,
        holdoffs=live.holdoffs.active(now=moment) if live.holdoffs else (),
        perception_age_s=perception_age_s,
    )


# ---------------------------------------------------------------------- the tick


@dataclass(frozen=True, slots=True)
class AmbientTickResult:
    decision: Decision
    acted: bool = False
    receipt_capability: str | None = None
    terminal_status: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "acted": self.acted,
            "decision": self.decision.as_dict(),
            "receipt_capability": self.receipt_capability,
            "terminal_status": self.terminal_status,
        }


def tick(
    session: Session,
    *,
    sequence: WakeSequence | None = None,
    runtimes: AmbientRuntimes | None = None,
    now: datetime | None = None,
) -> AmbientTickResult:
    """One ambient pass: decide, and act only if the decision says to (spec §3.9).

    Also runs any scheduled owner display TEST that has come due — the test is a real
    ``display.off`` on the production path (spec §9), issued by this clock, not a
    simulation.
    """
    moment = now or utcnow()
    live = (runtimes or AmbientRuntimes()).resolved()
    policy = get_policy(session)
    # ADR-0079 §11: the first tick of a process rebuilds the holdoffs a restart lost,
    # from the ledger - so a Cloud Core restarted a minute after the owner touched the
    # keyboard does not meet a stale AWAY with no reason to say no.
    ensure_holdoffs_restored(session, holdoffs=live.holdoffs, now=moment)

    pending_test = _due_test(moment)
    if pending_test is not None and sequence is not None:
        _clear_test()
        # The owner asked for this darkening out loud seconds ago - typing or speaking the
        # request IS recent input. A short device holdoff (not the automatic policy's 120 s)
        # lets the test run; the automatic path below keeps the long one.
        step = sequence.display_off(
            session, reason=REASON_OWNER_TEST, holdoff_s=OWNER_TEST_HOLDOFF_S, now=moment
        )
        _publish_display_intent(step, reason=REASON_OWNER_TEST)
        return AmbientTickResult(
            decision=Decision(ACTION_DISPLAY_OFF, REASON_OWNER_TEST, {"test": True}),
            acted=True,
            receipt_capability=step.receipt_capability,
            terminal_status=step.receipt.terminal_status,
        )

    # The owner came back to a dark screen (spec §3.9's ``wake_on_return``). Checked before
    # the decision, and from the presence BUS rather than from the current assertion,
    # because ``owner.returned`` is a transition and the assertion that follows it is
    # ``present`` — by the time a tick reads the state, the moment of return is gone.
    if _owner_returned_since(moment):
        wake_on_return(session, sequence=sequence, runtimes=live, now=moment)

    inputs = collect_inputs(session, runtimes=live, now=moment)
    decision = decide(inputs, policy, moment)
    if not decision.acts or sequence is None:
        return AmbientTickResult(decision=decision)

    step = sequence.display_off(
        session, reason=decision.reason, holdoff_s=120, now=moment
    )
    _publish_display_intent(step, reason=decision.reason)
    # Deliberately NO holdoff started here (spec §3.9): a successful automatic off is not a
    # reason to stop watching. A device REFUSAL starts the input holdoff instead — see
    # `app.ambient.ingest.note_display_refusal`.
    if step.reason:
        note_display_refusal(step.reason, policy=policy, holdoffs=live.holdoffs, now=moment)
    return AmbientTickResult(
        decision=decision,
        acted=True,
        receipt_capability=step.receipt_capability,
        terminal_status=step.receipt.terminal_status,
    )


#: Watermark into ``app.uistate.publisher``'s own monotonic sequence, so an
#: ``owner.returned`` event is acted on ONCE — the same technique
#: ``app.routines.models.Routine.last_presence_sequence`` uses for a presence trigger, in
#: memory here because the consequence (waking a display that is already awake) is
#: harmless and the watermark is worth less than a table.
_last_presence_sequence = 0


def _owner_returned_since(now: datetime) -> bool:
    """True when ``owner.returned`` was published since the last tick looked.

    Never raises: a UI bus that cannot be read must not stop the ambient tick, and the
    consequence of missing a return is a screen that stays dark until the owner touches
    the keyboard — which wakes it anyway, on the device, without us.
    """
    global _last_presence_sequence
    try:
        from app.uistate.publisher import get_publisher

        events = get_publisher().tail(limit=100, after_sequence=_last_presence_sequence)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("ambient_presence_tail_failed", error=type(exc).__name__)
        return False
    returned = False
    for event in events:
        if event.sequence > _last_presence_sequence:
            _last_presence_sequence = event.sequence
        if getattr(event.state, "value", event.state) == UiState.OWNER_RETURNED.value:
            returned = True
    del now  # the bus's own sequence is the ordering; wall-clock time is not needed
    return returned


def reset_presence_watermark() -> None:
    """Tests only: forget which bus events this process has already seen."""
    global _last_presence_sequence
    _last_presence_sequence = 0


def _publish_display_intent(step: Any, *, reason: str) -> None:
    """A FAILED or REFUSED display command publishes nothing about the display state.

    The bus's ``display.on`` / ``display.off`` come from the device's OBSERVED power state
    on the next heartbeat (``app.ambient.ingest``), never from having asked. This function
    exists to make that explicit rather than implicit — the only thing an intent publishes
    is an error, when there is one.
    """
    if step.ok:
        return
    publish(
        UiState.ERROR,
        subsystem=SUBSYSTEM_AMBIENT,
        severity="warning",
        status="display_off_not_applied",
        label=reason[:64],
        metadata={"error_class": str(step.error_class or "")[:32]},
    )


def note_display_refusal(
    refusal: str,
    *,
    policy: AmbientPolicy,
    holdoffs: HoldoffRegistry | None = None,
    now: datetime | None = None,
) -> None:
    """The device refused ``desktop.display_off`` because the owner just used the keyboard
    (spec §3.9's last line): start the input holdoff, so the cloud stops asking too."""
    if refusal != alarm_speech.REFUSAL_RECENT_INPUT:
        return
    from app.ambient.holdoff import SOURCE_INPUT

    (holdoffs or get_holdoffs()).start(
        SOURCE_INPUT, seconds=policy.input_holdoff_s, now=now, reason="device_refused"
    )


# --------------------------------------------------------------- owner display test

#: Spec §8.2's scheduled test. In memory and single-slot: the owner asks for one display
#: test at a time, and a process restart cancelling a ten-second countdown is the correct
#: outcome — a display that goes dark after an unexplained restart would be the bug.
_pending_test_at: datetime | None = None


def schedule_display_test(
    *, delay_seconds: int = DEFAULT_TEST_DELAY_S, now: datetime | None = None
) -> datetime:
    """Arm the owner's display test (spec §8.2). The CLOCK issues the real ``display.off``
    when it comes due; nothing is darkened inside the tool call itself."""
    global _pending_test_at
    moment = now or utcnow()
    delay = max(1, min(int(delay_seconds), MAX_TEST_DELAY_S))
    _pending_test_at = moment + timedelta(seconds=delay)
    return _pending_test_at


def pending_display_test() -> datetime | None:
    return _pending_test_at


def _due_test(now: datetime) -> datetime | None:
    if _pending_test_at is not None and _pending_test_at <= now:
        return _pending_test_at
    return None


def _clear_test() -> None:
    global _pending_test_at
    _pending_test_at = None


def cancel_display_test() -> None:
    """Tests, and an owner who changed their mind."""
    _clear_test()


# ------------------------------------------------------------------ owner returned


def wake_on_return(
    session: Session,
    *,
    sequence: WakeSequence | None = None,
    runtimes: AmbientRuntimes | None = None,
    now: datetime | None = None,
) -> Any | None:
    """``owner.returned`` while the display is off -> ``display.wake`` (spec §3.9).

    Returns the step outcome, or ``None`` when nothing was done — which is the common case:
    the owner usually returns to a screen that is already on.
    """
    moment = now or utcnow()
    live = (runtimes or AmbientRuntimes()).resolved()
    policy = get_policy(session)
    if not policy.wake_on_return or sequence is None:
        return None
    displays_on = live.statuses.displays_on() if live.statuses else None
    if displays_on is not False:
        return None
    step = sequence.display_wake(session, reason=REASON_OWNER_RETURNED, now=moment)
    if live.holdoffs is not None:
        live.holdoffs.start(
            SOURCE_OWNER_RETURN,
            seconds=holdoff_seconds(policy)[SOURCE_OWNER_RETURN],
            now=moment,
            reason="owner_returned",
        )
    return step


def note_alarm_wake(
    session: Session,
    *,
    holdoffs: HoldoffRegistry | None = None,
    now: datetime | None = None,
) -> None:
    """An alarm fired: hold off every automatic display-off (spec §2's holdoff sources).

    Called by ``app.alarms.service.fire_alarm`` on the success path and by the local
    fallback's reconcile (ADR-0079 §6 - the hardening run found this function with no
    caller at all, so the alarm holdoff had never once started in production).
    """
    policy = get_policy(session)
    (holdoffs or get_holdoffs()).start(
        SOURCE_ALARM_WAKE,
        seconds=holdoff_seconds(policy)[SOURCE_ALARM_WAKE],
        now=now,
        reason="alarm_fired",
    )


def note_owner_display_command(
    session: Session,
    *,
    holdoffs: HoldoffRegistry | None = None,
    now: datetime | None = None,
    reason: str = "display_command",
) -> None:
    """The owner explicitly woke the screens ("Ekranı aç."): nothing automatic darkens
    them for the command holdoff (ADR-0079 §5). An automatic off thirty seconds after an
    explicit wake would be the system arguing with a person."""
    policy = get_policy(session)
    (holdoffs or get_holdoffs()).start(
        SOURCE_OWNER_COMMAND,
        seconds=holdoff_seconds(policy)[SOURCE_OWNER_COMMAND],
        now=now,
        reason=reason,
    )


# ---------------------------------------------------------- holdoffs after a restart

#: Whether THIS process has rebuilt its holdoffs from the ledger yet (ADR-0079 §11).
_holdoffs_restored = False


def _latest_row(
    session: Session, event_type: str, *, actions: tuple[str, ...] | None = None
) -> ActivityEventRow | None:
    stmt = select(ActivityEventRow).where(ActivityEventRow.event_type == event_type)
    if actions:
        stmt = stmt.where(ActivityEventRow.action.in_(actions))
    stmt = stmt.order_by(ActivityEventRow.occurred_at.desc()).limit(1)
    return session.execute(stmt).scalars().first()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def restore_holdoffs(
    session: Session,
    *,
    holdoffs: HoldoffRegistry | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Rebuild the holdoffs a process restart lost, from durable evidence (ADR-0079 §11).

    Each source has one durable witness: ``owner.input_active`` for input, the newest
    ``ambient.policy_changed`` or a ``display.wake`` receipt for an owner command,
    ``alarm.firing`` for an alarm wake, a ``presence.state_changed`` to ``returned`` for
    the owner's return. A witness inside its window restarts that holdoff for the time
    it has LEFT; an older one restarts nothing. Returns the sources restored and their
    ends. Never raises: a ledger that cannot be read leaves the registry as it was,
    which still fails safe (every other guard applies).
    """
    moment = now or utcnow()
    registry = holdoffs or get_holdoffs()
    restored: dict[str, str] = {}
    try:
        policy = get_policy(session)
        durations = holdoff_seconds(policy)
        witnesses: list[tuple[str, ActivityEventRow | None, str]] = [
            (SOURCE_INPUT, _latest_row(session, EVENT_TYPE_OWNER_INPUT_ACTIVE), "input"),
            (
                SOURCE_OWNER_COMMAND,
                _latest_row(session, EVENT_TYPE_AMBIENT_POLICY_CHANGED),
                "policy_changed",
            ),
            (
                SOURCE_OWNER_COMMAND,
                _latest_row(session, EVENT_TYPE_ACTION_RECEIPT, actions=("display.wake",)),
                "display_wake",
            ),
            (SOURCE_ALARM_WAKE, _latest_row(session, EVENT_TYPE_ALARM_FIRING), "alarm_fired"),
        ]
        returned = _latest_row(session, EVENT_TYPE_PRESENCE_STATE_CHANGED)
        if returned is not None and (returned.detail_json or {}).get("to_state") == "returned":
            witnesses.append((SOURCE_OWNER_RETURN, returned, "owner_returned"))
        for source, row, why in witnesses:
            if row is None or row.occurred_at is None:
                continue
            remaining = durations[source] - (moment - _aware(row.occurred_at)).total_seconds()
            if remaining <= 0:
                continue
            holdoff = registry.start(
                source, seconds=int(remaining), now=moment, reason=f"restored:{why}"
            )
            restored[source] = holdoff.until.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("ambient_holdoff_restore_failed", error=type(exc).__name__)
    if restored:
        logger.info("ambient_holdoffs_restored", sources=sorted(restored))
    return restored


def ensure_holdoffs_restored(
    session: Session,
    *,
    holdoffs: HoldoffRegistry | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """``restore_holdoffs`` exactly once per process (the first ambient tick)."""
    global _holdoffs_restored
    if _holdoffs_restored:
        return {}
    _holdoffs_restored = True
    return restore_holdoffs(session, holdoffs=holdoffs, now=now)


def reset_holdoff_restore() -> None:
    """Tests only: make the next tick restore again, as a fresh process would."""
    global _holdoffs_restored
    _holdoffs_restored = False


# ------------------------------------------------------------- the explanation


def _latest_display_receipt(session: Session) -> dict[str, Any] | None:
    row = _latest_row(
        session, EVENT_TYPE_ACTION_RECEIPT, actions=("display.off", "display.wake")
    )
    if row is None:
        return None
    detail = dict(row.detail_json or {})
    server = (detail.get("observed_after") or {}).get("server") or {}
    return {
        "capability": row.action,
        "reason": str(server.get("reason") or detail.get("reason") or ""),
        "execution_status": detail.get("execution_status"),
        "terminal_status": detail.get("terminal_status"),
        "error_class": detail.get("error_class"),
        "occurred_at": (
            _aware(row.occurred_at).isoformat().replace("+00:00", "Z")
            if row.occurred_at
            else None
        ),
    }


def explain(
    session: Session,
    *,
    runtimes: AmbientRuntimes | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """"Ekranları neden kapattın?" / "Neden açık bıraktın?" / "Şu an ekran politikası
    ne?" answered from the facts and nothing else (ADR-0079 §12): the live decision the
    tick would make right now (the same pure ``decide``), the presence assertion behind
    it, the active holdoffs, the latest keyboard/mouse activity the ledger holds and the
    latest display receipt. The sentence is assembled by ``app.alarms.speech`` from
    these fields; a fact that is absent is said to be absent, never guessed."""
    moment = now or utcnow()
    live = (runtimes or AmbientRuntimes()).resolved()
    policy = get_policy(session)
    inputs = collect_inputs(session, runtimes=live, now=moment)
    decision = decide(inputs, policy, moment)
    last_input = _latest_row(session, EVENT_TYPE_OWNER_INPUT_ACTIVE)
    facts: dict[str, Any] = {
        "display": display_state_summary(statuses=live.statuses),
        "policy": policy.as_dict(),
        "decision": decision.as_dict(),
        "presence": {
            "state": inputs.presence_state.value,
            "confidence": round(inputs.presence_confidence, 3),
            "held_s": round(inputs.presence_held_s, 1),
            "stale": inputs.presence_stale,
            "eye_enabled": inputs.eye_enabled,
            "perception_age_s": inputs.perception_age_s,
        },
        "holdoffs": live.holdoffs.as_dict(now=moment) if live.holdoffs else {},
        "quiet_hours": quiet_hours_state(policy, moment),
        "asleep_needed_s": asleep_needed_s(policy, moment),
        "last_input_at": (
            _aware(last_input.occurred_at).isoformat().replace("+00:00", "Z")
            if last_input is not None and last_input.occurred_at
            else None
        ),
        "last_display_action": _latest_display_receipt(session),
        "observed_at": moment.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    facts["speech"] = alarm_speech.ambient_explain_speech(facts, now=moment)
    return facts


def device_status_dict(
    device_id: uuid.UUID, *, statuses: DeviceStatusRegistry | None = None
) -> dict[str, Any] | None:
    status = (statuses or get_status_registry()).get(device_id)
    return status.as_dict() if status is not None else None


def display_state_summary(*, statuses: DeviceStatusRegistry | None = None) -> str:
    """"on" | "off" | "unknown" across every reporting device — what ``display.status``
    speaks from."""
    on = (statuses or get_status_registry()).displays_on()
    if on is None:
        return "unknown"
    return "on" if on else DISPLAY_OFF


__all__ = [
    "DEFAULT_TEST_DELAY_S",
    "MAX_TEST_DELAY_S",
    "AmbientRuntimes",
    "AmbientTickResult",
    "cancel_display_test",
    "collect_inputs",
    "device_status_dict",
    "display_state_summary",
    "ensure_holdoffs_restored",
    "explain",
    "get_policy",
    "get_policy_row",
    "note_alarm_wake",
    "note_display_refusal",
    "note_owner_display_command",
    "pending_display_test",
    "reset_holdoff_restore",
    "reset_presence_watermark",
    "restore_holdoffs",
    "schedule_display_test",
    "set_policy",
    "tick",
    "utcnow",
    "wake_on_return",
]
