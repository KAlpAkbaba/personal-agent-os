"""Wake alarm service (M18.3 spec §3): create, arm, fire, snooze, stop, tick.

Same discipline as ``app.routines.service`` / ``app.goals.service``: every function takes
an open ``Session`` and owns its commit, so async routes run it through
``asyncio.to_thread`` and nothing here is coupled to FastAPI.

Three things this module is careful about, each because the failure mode is one a sleeping
owner cannot correct:

**One ring per occurrence, across processes.** The routine engine's ``RoutineFiring``
uniqueness on ``(routine_id, occurrence_key)`` stops a second dispatch inside one process.
``WakeAlarm.last_firing_id`` plus the state machine stops the rest: a second tick, a second
process, or a broker reconnect that redelivers a command all find the alarm already past
``ARMED`` and holding a firing id, and do nothing. That is why the idempotency key lives on
the ALARM and not only on the occurrence — an occurrence is a row another process may not
have committed yet, and an alarm is the thing that is physically making noise.

**The clock is not the schedule.** ``tick`` is called on a cadence but decides nothing from
the cadence: everything it does is derived from stored timestamps (``scheduled_for``,
``greeting_due_at``, ``playing_since`` + ``max_play_seconds``), so a process that was down
for ten minutes catches up correctly on its first tick instead of losing what it missed.
This is what makes "an ARMED row fires after a fresh process's first tick" true.

**Terminal means released.** Every terminal state runs the same cleanup — stop playback,
disarm the device, resolve or re-arm the routine, write ``alarm.cleaned_up`` — so a test
alarm never leaves a device armed, a browser session open or a routine waiting. Not a
special case for test alarms: the same path, with a shorter ``max_play_seconds``.
"""

from __future__ import annotations

import contextlib
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alarms import speech as alarm_speech
from app.alarms import timing
from app.alarms.models import (
    ALARM_ACTIVE_STATES,
    ALARM_PENDING_STATES,
    ALARM_TERMINAL_STATES,
    DEFAULT_MAX_PLAY_SECONDS,
    DEFAULT_SNOOZE_MINUTES,
    DEFAULT_TIMEZONE,
    MAX_SNOOZE_COUNT,
    MAX_SNOOZE_MINUTES,
    MEDIA_KIND_REMEMBERED,
    MEDIA_KIND_TONE,
    MEDIA_KIND_YOUTUBE,
    PLAYED_KIND_LOCAL_FALLBACK,
    STATE_ARMED,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FIRING,
    STATE_PLAYING,
    STATE_SCHEDULED,
    STATE_SNOOZED,
    STATE_STOPPED,
    TEST_MAX_PLAY_SECONDS,
    WakeAlarm,
)
from app.alarms.sequence import (
    DEFAULT_DISPLAY_WAKE_POLICY,
    DEFAULT_GREETING_POLICY,
    DEFAULT_VOLUME_POLICY,
    FireResult,
    WakeSequence,
)
from app.alarms.state import IllegalAlarmTransition, assert_alarm_transition
from app.alarms.tr_time import ParsedWhen, next_occurrence_after
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    ALARM_EVENT_TYPE_BY_STATE,
    EVENT_TYPE_ALARM_CLEANED_UP,
    EVENT_TYPE_ALARM_LOCAL_FALLBACK_RANG,
    EVENT_TYPE_ALARM_LOCAL_SNOOZED,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SUBSYSTEM_ROUTINE,
)
from app.logging import get_logger
from app.routines import service as routines_service
from app.routines.actions import ACTION_KIND_WAKE_ALARM
from app.routines.models import (
    ROUTINE_STATUS_ARMED,
    TRIGGER_KIND_AT,
    TRIGGER_KIND_SCHEDULE,
    Routine,
)
from app.uistate.contract import UiState
from app.uistate.publisher import publish

logger = get_logger("app.alarms.service")

#: How long before a scheduled instant the tick tries to arm the device (spec §3.4: arming
#: is continuous while the alarm is pending, but a device that has been offline for a week
#: does not need an arm attempt on every one of those ticks).
ARM_LEAD_S = 12 * 3600

#: B13 req 284: how late an alarm may still be worth ringing. READ from
#: ``packages/protocol/alarm-timing.json``, not remembered here.
#:
#: This constant said two hours and the device said five minutes - two halves of one
#: decision, each with its own memory of the answer. The device's number was the one chosen
#: by measurement: on 2026-09-10 a 07:30 alarm armed the night before rang at 08:10 because
#: the machine had slept through the alarm time, 39 minutes late, into a room where the
#: owner was already awake. The device learned from that. The cloud did not, because nothing
#: made the two read the same file.
MAX_LATE_FIRE_S = timing.max_late_fire_s()

#: Spec §7's bus TTLs, published as metadata so the renderer never invents one.
_UI_STATE_BY_ALARM_STATE: dict[str, UiState] = {
    STATE_ARMED: UiState.ALARM_ARMED,
    STATE_FIRING: UiState.ALARM_FIRING,
    STATE_PLAYING: UiState.ALARM_PLAYING,
    "GREETING": UiState.ALARM_GREETING,
    STATE_SNOOZED: UiState.ALARM_SNOOZED,
    STATE_STOPPED: UiState.ALARM_STOPPED,
    STATE_COMPLETED: UiState.ALARM_COMPLETED,
    "FAILED": UiState.ALARM_FAILED,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    """A stored timestamp, made comparable.

    SQLite has no timezone type, so a ``DateTime(timezone=True)`` column round-trips as a
    NAIVE datetime there while Postgres returns an aware one. Every comparison in this
    module is between "what the database said" and "now", and mixing the two raises
    ``TypeError`` — on SQLite, in the unit suite, which is exactly where it should surface
    rather than at 07:30 on a real morning. Stored instants are UTC by construction (the
    service only ever writes ``datetime.now(UTC)`` or a ``ParsedWhen.at``), so attaching UTC
    to a naive read is a restatement, not a guess.
    """
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class AlarmNotFoundError(ValueError):
    pass


class InvalidAlarmRequest(ValueError):
    """A create/snooze request this service refuses rather than guesses at."""


# ---------------------------------------------------------------- ledger + uistate


def _record_ledger(
    session: Session,
    *,
    event_type: str,
    alarm: WakeAlarm,
    action: str,
    factual_summary: str,
    source_ref: str,
    severity: str = SEVERITY_INFO,
    detail: dict[str, Any] | None = None,
) -> None:
    """Never fails the caller — the ledger is evidence, not a dependency (the rule
    ``app.routines.service._record_ledger`` already follows)."""
    try:
        ledger_service.record(
            session,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_ROUTINE,
                action=action,
                severity=severity,
                factual_summary=factual_summary,
                source="live",
                source_ref=source_ref,
                occurred_at=utcnow(),
                related_module_id=f"alarm:{alarm.id}",
                detail_json=detail or {},
            ),
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("alarm_ledger_failed", alarm_id=str(alarm.id), error=type(exc).__name__)


def _publish(alarm: WakeAlarm, state: str, **metadata: Any) -> None:
    """Never fails the caller — a UI signal must not fail a wake alarm."""
    ui_state = _UI_STATE_BY_ALARM_STATE.get(state)
    if ui_state is None:
        return
    publish(
        ui_state,
        subsystem=SUBSYSTEM_ROUTINE,
        severity="critical" if state == "FAILED" else "info",
        status=state.lower(),
        label=alarm.local_time,
        metadata={
            "alarm_id": str(alarm.id),
            "is_test": alarm.is_test,
            **{k: v for k, v in metadata.items() if v is not None},
        },
    )


def _occurrence(alarm: WakeAlarm) -> str:
    """The occurrence this alarm is currently on: the scheduled instant plus the snooze
    counter, so a snooze is a genuinely new occurrence rather than a re-run of the old one
    (which the ledger's ``(source, source_ref)`` idempotency would otherwise swallow)."""
    return f"{int(_aware(alarm.scheduled_for).timestamp())}:{alarm.snooze_count}"


def transition(
    session: Session, alarm: WakeAlarm, state: str, *, now: datetime | None = None, **detail: Any
) -> WakeAlarm:
    """Move the alarm to ``state``: guard, write, ledger, publish, commit.

    Exactly one ledger event per transition (spec §3.2), idempotent per
    ``(alarm_id, state, occurrence)`` through the ledger's own ``(source, source_ref)``
    uniqueness — so a re-entered state does not multiply rows.
    """
    moment = now or utcnow()
    assert_alarm_transition(alarm.state, state)
    changed = alarm.state != state
    alarm.state = state
    alarm.updated_at = moment
    if state == STATE_ARMED and alarm.armed_at is None:
        alarm.armed_at = moment
    if state == STATE_FIRING and alarm.triggered_at is None:
        alarm.triggered_at = moment
    if detail.get("firing_id"):
        alarm.last_firing_id = uuid.UUID(str(detail["firing_id"]))
    if state in ALARM_TERMINAL_STATES:
        alarm.terminal_at = moment
        alarm.terminal_state = state
        alarm.terminal_reason = str(detail.get("reason") or "")[:200] or None
    session.commit()

    _record_ledger(
        session,
        event_type=ALARM_EVENT_TYPE_BY_STATE[state],
        alarm=alarm,
        action=f"alarm_{state.lower()}",
        severity=SEVERITY_CRITICAL if state == "FAILED" else SEVERITY_INFO,
        factual_summary=f"Alarm {state.lower()}: {alarm.local_time} ({alarm.timezone})",
        source_ref=f"alarms:{alarm.id}:{state.lower()}:{_occurrence(alarm)}",
        detail={"is_test": alarm.is_test, **{k: str(v)[:200] for k, v in detail.items()}},
    )
    if changed or state in ALARM_ACTIVE_STATES:
        _publish(alarm, state, **{k: v for k, v in detail.items() if isinstance(v, str | int)})
    return alarm


# ---------------------------------------------------------------------- creation


def get_wake_song(session: Session) -> dict[str, Any] | None:
    """The owner's approved wake song, or None. Lives on the ambient policy row (one row,
    one owner); the alarms package reads it, the owner sets it through
    :func:`set_wake_song` / ``PUT /v1/alarms/wake-song``."""
    from app.alarms.models import AMBIENT_POLICY_ID, AmbientPolicyRow

    row = session.get(AmbientPolicyRow, AMBIENT_POLICY_ID)
    if row is None or not isinstance(row.wake_song, dict):
        return None
    url = row.wake_song.get("url")
    if not isinstance(url, str) or not url:
        return None
    return {"url": url, "title": str(row.wake_song.get("title") or "")[:200]}


def set_wake_song(session: Session, *, url: str, title: str | None = None) -> dict[str, Any]:
    """Remember the wake song the owner named. Only an http(s) URL the owner gave is ever
    stored — this is the one place a "remembered" title becomes a playable item."""
    from app.alarms.models import AMBIENT_POLICY_ID, AmbientPolicyRow

    url = url.strip()
    if not (url.startswith("https://") or url.startswith("http://")):
        raise InvalidAlarmRequest("the wake song must be an http(s) URL the owner named")
    row = session.get(AmbientPolicyRow, AMBIENT_POLICY_ID)
    if row is None:
        row = AmbientPolicyRow(policy_id=AMBIENT_POLICY_ID)
        session.add(row)
    row.wake_song = {"url": url[:2000], "title": str(title or "")[:200]}
    row.updated_at = utcnow()
    session.commit()
    return dict(row.wake_song)


def _resolved_from_wake_song(wake_song: dict[str, Any] | None) -> dict[str, Any] | None:
    """The approved wake song, shaped as a ``resolved_media_identity`` — or ``None`` when
    the owner has never approved one (:func:`set_wake_song`)."""
    if not wake_song or not isinstance(wake_song.get("url"), str) or not wake_song["url"]:
        return None
    resolved = {"kind": MEDIA_KIND_YOUTUBE, "url": wake_song["url"]}
    if wake_song.get("title"):
        resolved["title"] = str(wake_song["title"])[:200]
    return resolved


def _media_source(
    media: dict[str, Any] | None, *, wake_song: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(what the owner asked for, what is actually resolved).

    A title with no url resolves to NOTHING — this system never picks a video on the
    owner's behalf, the rule ``app.routines.actions.validate_media_playback`` already
    states — UNLESS the owner has approved a wake song (:func:`set_wake_song`): then
    "seçtiğim müzik" / a remembered title resolves to that one item, which the owner
    named. The caller turns an unresolved media source into either a tone alarm with a
    follow-up question or a refusal, depending on whether the alarm recurs (spec §3.8).

    Naming NO media at all resolves the SAME way (owner directive 2026-09-08): "Yarın
    07:30'da beni uyandır." must not repeat the tone every single morning just because the
    owner did not re-say the URL — YouTube-primary/tone-fallback (``app.alarms.sequence``)
    is only as real as what it is asked to play. ``media_source`` still says the owner
    named nothing (``{"kind": "tone"}`` — the only vocabulary word for that, spec §3.1's
    ``MEDIA_SOURCE_KINDS``; there is no wire field for "I explicitly want the tone" today,
    see ``CreateAlarmRequest``/``MediaIn``), so the two fields keep meaning what they always
    meant: what was asked, and what will actually play. With no approved wake song either,
    the tone is exactly what plays — nothing has changed for that owner.
    """
    media = media or {}
    url = media.get("url")
    title = media.get("title") or media.get("remembered")
    if isinstance(url, str) and url:
        source = {"kind": MEDIA_KIND_YOUTUBE, "url": url}
        if title:
            source["title"] = str(title)[:200]
        return source, dict(source)
    if title:
        source = {"kind": MEDIA_KIND_REMEMBERED, "name": str(title)[:200]}
        return source, _resolved_from_wake_song(wake_song)
    return {"kind": MEDIA_KIND_TONE}, _resolved_from_wake_song(wake_song)


def create_alarm(
    session: Session,
    *,
    when: ParsedWhen,
    media: dict[str, Any] | None = None,
    is_test: bool = False,
    label: str | None = None,
    greeting_policy: dict[str, Any] | None = None,
    volume_policy: dict[str, Any] | None = None,
    display_wake_policy: dict[str, Any] | None = None,
    snooze_minutes: int = DEFAULT_SNOOZE_MINUTES,
    source_ref: str | None = None,
) -> WakeAlarm:
    """Create the alarm AND the routine that will fire it (spec §3.1).

    The routine is created first-class through ``app.routines.service.create_routine``, so
    a wake alarm is visible in ``/v1/routines`` exactly like every other routine and the
    engine's own idempotency and ledger rows apply to it unchanged.
    """
    source, resolved = _media_source(media, wake_song=get_wake_song(session))
    alarm = WakeAlarm(
        id=uuid.uuid4(),
        timezone=when.timezone or DEFAULT_TIMEZONE,
        scheduled_for=when.at,
        local_time=when.local_time,
        recurrence={"weekdays": list(when.weekdays)} if when.weekdays else None,
        state=STATE_SCHEDULED,
        media_source=source,
        resolved_media_identity=resolved,
        volume_policy={**DEFAULT_VOLUME_POLICY, **(volume_policy or {})},
        greeting_policy={**DEFAULT_GREETING_POLICY, **(greeting_policy or {})},
        display_wake_policy={**DEFAULT_DISPLAY_WAKE_POLICY, **(display_wake_policy or {})},
        is_test=is_test,
        label=label,
        snooze_minutes=max(1, min(int(snooze_minutes), MAX_SNOOZE_MINUTES)),
        max_play_seconds=TEST_MAX_PLAY_SECONDS if is_test else DEFAULT_MAX_PLAY_SECONDS,
        detail_json={},
    )
    session.add(alarm)
    session.commit()

    routine = _create_trigger_routine(session, alarm, source_ref=source_ref)
    alarm.routine_id = routine.routine_id
    session.commit()

    _record_ledger(
        session,
        event_type=ALARM_EVENT_TYPE_BY_STATE[STATE_SCHEDULED],
        alarm=alarm,
        action="alarm_scheduled",
        factual_summary=(
            f"Alarm kuruldu: {alarm.local_time} ({alarm.timezone})"
            + (" [test]" if is_test else "")
        ),
        source_ref=f"alarms:{alarm.id}:scheduled:{_occurrence(alarm)}",
        detail={
            "is_test": is_test,
            "recurring": bool(when.weekdays),
            "media_kind": source.get("kind"),
            "routine_id": str(routine.routine_id),
        },
    )
    return alarm


def _create_trigger_routine(
    session: Session, alarm: WakeAlarm, *, source_ref: str | None = None
) -> Routine:
    """One-shot alarms get an ``at`` routine, recurring ones a ``schedule`` routine
    (spec §3.1). Both carry the single ``wake_alarm`` action and nothing else."""
    action = {"kind": ACTION_KIND_WAKE_ALARM, "detail": {"alarm_id": str(alarm.id)}}
    name = ("Test alarmı " if alarm.is_test else "Alarm ") + alarm.local_time
    if alarm.recurrence:
        return routines_service.create_routine(
            session,
            name=name,
            trigger_kind=TRIGGER_KIND_SCHEDULE,
            trigger={
                "weekdays": list(alarm.recurrence["weekdays"]),
                "time": alarm.local_time,
                "timezone": alarm.timezone,
            },
            actions=[action],
            source="alarm",
            source_ref=source_ref or f"alarm:{alarm.id}",
            detail_json={"alarm_id": str(alarm.id)},
        )
    return routines_service.create_routine(
        session,
        name=name,
        trigger_kind=TRIGGER_KIND_AT,
        trigger={"at": alarm.scheduled_for.astimezone(UTC).isoformat()},
        actions=[action],
        source="alarm",
        source_ref=source_ref or f"alarm:{alarm.id}:{alarm.snooze_count}",
        detail_json={"alarm_id": str(alarm.id)},
    )


# ------------------------------------------------------------------------ reads


def get_alarm(session: Session, alarm_id: uuid.UUID) -> WakeAlarm | None:
    return session.get(WakeAlarm, alarm_id)


def require_alarm(session: Session, alarm_id: uuid.UUID) -> WakeAlarm:
    alarm = get_alarm(session, alarm_id)
    if alarm is None:
        raise AlarmNotFoundError(f"unknown alarm: {alarm_id}")
    return alarm


def list_alarms(
    session: Session, *, state: str | None = None, include_terminal: bool = False, limit: int = 100
) -> list[WakeAlarm]:
    stmt = select(WakeAlarm)
    if state is not None:
        stmt = stmt.where(WakeAlarm.state == state)
    elif not include_terminal:
        stmt = stmt.where(WakeAlarm.state.not_in(tuple(sorted(ALARM_TERMINAL_STATES))))
    stmt = stmt.order_by(WakeAlarm.scheduled_for.asc()).limit(max(1, min(limit, 200)))
    return list(session.execute(stmt).scalars().all())


def next_alarm(session: Session, *, now: datetime | None = None) -> WakeAlarm | None:
    """The next alarm the owner will hear: the ringing one if there is one, else the
    soonest pending one. "Sabah alarmım kaçta?" is answered from this."""
    moment = now or utcnow()
    active = [a for a in list_alarms(session, limit=200) if a.state in ALARM_ACTIVE_STATES]
    if active:
        return active[0]
    pending = [
        a
        for a in list_alarms(session, limit=200)
        if a.state in ALARM_PENDING_STATES
        and _aware(a.scheduled_for) >= moment - timedelta(minutes=1)
    ]
    return pending[0] if pending else None


def alarms_ringing(session: Session) -> list[WakeAlarm]:
    return [a for a in list_alarms(session, limit=200) if a.state in ALARM_ACTIVE_STATES]


# ------------------------------------------------------------------ owner commands


def cancel_alarm(
    session: Session,
    alarm_id: uuid.UUID,
    *,
    sequence: WakeSequence | None = None,
    reason: str = "owner",
    now: datetime | None = None,
) -> WakeAlarm:
    """Idempotent: cancelling an already-cancelled alarm is a no-op success."""
    alarm = require_alarm(session, alarm_id)
    if alarm.state == STATE_CANCELLED:
        return alarm
    if alarm.state in ALARM_TERMINAL_STATES:
        raise IllegalAlarmTransition(f"alarm is already {alarm.state}")
    transition(session, alarm, STATE_CANCELLED, now=now, reason=reason)
    _release(session, alarm, sequence=sequence, reason=reason, now=now)
    return alarm


def stop_alarm(
    session: Session,
    alarm_id: uuid.UUID,
    *,
    sequence: WakeSequence | None = None,
    reason: str = "owner",
    now: datetime | None = None,
) -> WakeAlarm:
    """"Alarmı kapat." Idempotent (spec §3.8): stopping a stopped alarm succeeds."""
    alarm = require_alarm(session, alarm_id)
    if alarm.state in ALARM_TERMINAL_STATES:
        return alarm
    if sequence is not None:
        sequence.stop_playback(session, alarm, reason=reason, now=now)
    transition(session, alarm, STATE_STOPPED, now=now, reason=reason)
    _release(session, alarm, sequence=sequence, reason=reason, now=now)
    return alarm


def snooze_alarm(
    session: Session,
    alarm_id: uuid.UUID,
    *,
    minutes: int | None = None,
    sequence: WakeSequence | None = None,
    now: datetime | None = None,
    resume_at: datetime | None = None,
) -> WakeAlarm:
    """Stop the playback, push the alarm forward, arm a fresh one-shot routine (spec §3.5).

    Only meaningful while the alarm is physically happening; a snooze on an idle alarm is
    refused rather than silently rescheduling something the owner did not ask about.

    ``resume_at`` (B47) is an instant another party already chose - the device that snoozed
    the alarm on its own while this cloud was unreachable. It is adopted as the new
    ``scheduled_for`` instead of being re-derived from ``now``: the report may arrive minutes
    after the snooze, and the device will ring at ITS instant.
    """
    moment = now or utcnow()
    alarm = require_alarm(session, alarm_id)
    if alarm.state not in ALARM_ACTIVE_STATES:
        raise InvalidAlarmRequest("only a ringing alarm can be snoozed")
    span = int(minutes if minutes is not None else alarm.snooze_minutes)
    if span < 1 or span > MAX_SNOOZE_MINUTES:
        raise InvalidAlarmRequest(f"snooze minutes must be 1..{MAX_SNOOZE_MINUTES}")
    # B13 req 259: a wake-up cannot be deferred for ever. Without this an alarm never
    # reached a terminal state - it stayed live in the row, in the world model and on the
    # device's arm for as long as somebody kept pressing, and one morning wrote an unbounded
    # run of SNOOZED transitions into the ledger. The refusal is explicit: the alarm keeps
    # ringing and the owner is told why, rather than the system quietly going silent.
    if alarm.snooze_count >= MAX_SNOOZE_COUNT:
        raise InvalidAlarmRequest(
            f"this alarm has been snoozed {alarm.snooze_count} times "
            f"(the limit is {MAX_SNOOZE_COUNT}); stop it or get up"
        )

    if sequence is not None:
        sequence.stop_playback(session, alarm, reason="snooze", now=moment)
    alarm.scheduled_for = (
        _aware(resume_at) if resume_at is not None else moment + timedelta(minutes=span)
    )
    alarm.local_time = _aware(alarm.scheduled_for).astimezone(
        ZoneInfo(alarm.timezone)
    ).strftime("%H:%M")
    alarm.snooze_count += 1
    alarm.snooze_minutes = span
    alarm.last_firing_id = None
    alarm.media_kind = None
    alarm.media_session_id = None
    alarm.greeting_due_at = None
    alarm.greeted_at = None
    alarm.playing_since = None
    alarm.armed_at = None
    alarm.triggered_at = None
    # A snooze starts a fresh occurrence (module docstring): a media-failure reason from
    # the ring just stopped must not linger on ``GET /v1/alarms/{id}`` describing a ring
    # that has not happened yet.
    if "media_failure_reason" in (alarm.detail_json or {}):
        alarm.detail_json = {
            k: v for k, v in alarm.detail_json.items() if k != "media_failure_reason"
        }
    transition(session, alarm, STATE_SNOOZED, now=moment, minutes=span)

    # A fresh one-shot routine for the new moment; the old one is resolved by the engine
    # (it already fired) or cancelled here if it is still armed (a recurring alarm's
    # schedule routine stays armed and is NOT touched — tomorrow still happens).
    routine = _create_trigger_routine(
        session, alarm, source_ref=f"alarm:{alarm.id}:snooze:{alarm.snooze_count}"
    )
    alarm.routine_id = routine.routine_id
    session.commit()
    if sequence is not None:
        sequence.arm(session, alarm, now=moment)
        transition(session, alarm, STATE_ARMED, now=moment)
    else:
        transition(session, alarm, STATE_SCHEDULED, now=moment)
    return alarm


# ------------------------------------------------------------------------- firing


@dataclass(frozen=True, slots=True)
class FireDecision:
    """Why a fire request did or did not start a ring — the honest answer a dispatcher
    turns into a ``DispatchOutcome``."""

    fired: bool
    reason: str
    result: FireResult | None = None


def fire_alarm(
    session: Session,
    alarm_id: uuid.UUID,
    *,
    sequence: WakeSequence,
    firing_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> FireDecision:
    """Start the wake sequence for one alarm, ONCE (module docstring).

    Every refusal below is a real fact about the alarm, not a lock: a terminal alarm, an
    alarm already ringing, or a re-delivery of the firing that started the current ring.
    """
    moment = now or utcnow()
    alarm = get_alarm(session, alarm_id)
    if alarm is None:
        return FireDecision(False, "alarm_not_found")
    if alarm.state in ALARM_TERMINAL_STATES:
        return FireDecision(False, f"alarm_{alarm.state.lower()}")
    if alarm.state in ALARM_ACTIVE_STATES:
        # A second tick, a second process, or a redelivered command. One ring.
        return FireDecision(False, "already_firing")
    if firing_id is not None and alarm.last_firing_id == firing_id:
        return FireDecision(False, "firing_already_handled")
    # B14 req 261, at the one door. The lateness check below asks "is this row's occurrence
    # too old to ring?", and for a RECURRING alarm the row is not the authority on which
    # occurrence this is - the schedule routine is, and `check_schedule_due` already refuses
    # anything outside its own grace window. A row left pointing at a morning the machine
    # slept through would otherwise make every later morning "expired", so a recurring alarm
    # that missed one day was dead. Catching up here means the refusal never happens; the
    # same helper runs on the tick so the row is honest between occurrences too.
    _catch_up_recurring(alarm, moment)
    late_by = (moment - _aware(alarm.scheduled_for)).total_seconds()
    if late_by > MAX_LATE_FIRE_S:
        transition(session, alarm, STATE_STOPPED, now=moment, reason="expired_while_down")
        _release(session, alarm, sequence=sequence, reason="expired", now=moment)
        return FireDecision(False, "expired")

    result = sequence.fire(
        session, alarm, firing_id=firing_id, now=moment, transition=transition
    )
    session.commit()
    if result.state == "FAILED":
        publish(
            UiState.ERROR,
            subsystem=SUBSYSTEM_ROUTINE,
            severity="critical",
            status="alarm_failed",
            label=alarm.local_time,
            metadata={"alarm_id": str(alarm.id), "reason": result.reason[:64]},
        )
        _release(session, alarm, sequence=sequence, reason="audio_failed", now=moment)
        # B12 req 386: an alarm that did not fire is otherwise discovered by oversleeping,
        # and the UI-state event above only reaches somebody already looking at the panel.
        # Best-effort: the alarm has already failed and a notification fault must not make
        # that worse.
        try:
            from app.notifications import events

            events.alarm_failed(
                session,
                alarm_id=alarm.id,
                label=alarm.local_time or "",
                reason=result.reason[:120] if result.reason else "",
                now=moment,
            )
        except Exception as exc:  # noqa: BLE001 - see above
            with contextlib.suppress(Exception):
                session.rollback()  # a swallowed error leaves the session unusable
            logger.warning(
                "alarm_failure_notification_failed", error=f"{type(exc).__name__}: {exc}"
            )
        return FireDecision(False, "audio_failed", result)
    # ADR-0079 §6: an alarm that fired holds off every automatic display-off for the
    # alarm holdoff. Wired here, on the success path - the hardening run of 2026-09-07
    # found ``note_alarm_wake`` with no caller, so the holdoff had never once started.
    _note_alarm_wake(session, now=moment)
    return FireDecision(True, result.media_kind or "", result)


def _note_alarm_wake(session: Session, *, now: datetime) -> None:
    """Best effort, lazily imported: ``app.ambient.service`` imports this module."""
    try:
        from app.ambient.service import note_alarm_wake

        note_alarm_wake(session, now=now)
    except Exception as exc:  # noqa: BLE001 - a holdoff is protection, never a dependency
        logger.warning("alarm_wake_holdoff_failed", error=type(exc).__name__)


# --------------------------------------------------------------------------- tick


@dataclass(frozen=True, slots=True)
class TickResult:
    armed: int = 0
    greeted: int = 0
    completed: int = 0
    checked: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "armed": self.armed,
            "greeted": self.greeted,
            "completed": self.completed,
        }


def tick(
    session: Session,
    *,
    sequence: WakeSequence | None = None,
    now: datetime | None = None,
) -> TickResult:
    """One pass over every non-terminal alarm (spec §3.3's second step).

    Arms what needs arming, speaks a greeting that has come due, and completes an alarm
    that has played long enough. Every decision comes from a stored timestamp compared to
    ``now`` — never from "this is the Nth tick" — so a process that missed ten ticks does
    the right thing on its first one.
    """
    moment = now or utcnow()
    armed = greeted = completed = 0
    alarms = list_alarms(session, limit=200)
    for alarm in alarms:
        try:
            if alarm.state in ALARM_PENDING_STATES:
                # B14 req 261: a recurring alarm whose occurrence went by unrung catches up.
                #
                # `scheduled_for` on a recurring alarm means "the next occurrence", and only
                # `_release` was keeping that true - so one missed morning (the machine was
                # off; the process was down) left the row pointing at a day in the past for
                # ever. The schedule routine kept firing on time and `fire_alarm` refused
                # every one of them as expired, because the row said the occurrence was
                # yesterday. A recurring alarm that misses one morning was dead.
                #
                # Only occurrences already too late to ring are moved (`MAX_LATE_FIRE_S`,
                # the shared horizon of req 284): an occurrence that is one second past is
                # about to ring, and pushing it to tomorrow would be the silent alarm this
                # exists to prevent.
                if _catch_up_recurring(alarm, moment):
                    # Committed here, not left for whatever the tick does next: with no
                    # sequence wired nothing below this line writes, and a row correction
                    # that is rolled back at the end of the request is not a correction.
                    session.commit()
                if sequence is not None and _should_arm(alarm, moment):
                    step = sequence.arm(session, alarm, now=moment)
                    if step.ok:
                        transition(session, alarm, STATE_ARMED, now=moment)
                        armed += 1
                continue
            if alarm.state == STATE_PLAYING:
                if _greeting_due(alarm, moment) and sequence is not None:
                    local_now = moment.astimezone(ZoneInfo(alarm.timezone))
                    sequence.speak_greeting(
                        session,
                        alarm,
                        local_now=local_now,
                        now=moment,
                        transition=transition,
                    )
                    session.commit()
                    greeted += 1
                    continue
                if _play_expired(alarm, moment):
                    complete_alarm(session, alarm, sequence=sequence, now=moment)
                    completed += 1
        except Exception as exc:  # noqa: BLE001 - one alarm's failure must not skip the rest
            logger.warning(
                "alarm_tick_failed",
                alarm_id=str(alarm.id),
                state=alarm.state,
                error=f"{type(exc).__name__}: {exc}",
            )
    return TickResult(armed=armed, greeted=greeted, completed=completed, checked=len(alarms))


def _catch_up_recurring(alarm: WakeAlarm, now: datetime) -> bool:
    """Move a recurring alarm past an occurrence that is now too late to ring (req 261).

    Returns whether it moved. Never touches a one-shot alarm: a one-shot whose moment went
    by IS finished, and moving it would invent a wake-up the owner never asked for.
    """
    if not alarm.recurrence:
        return False
    late_by = (now - _aware(alarm.scheduled_for)).total_seconds()
    if late_by <= MAX_LATE_FIRE_S:
        return False
    try:
        moved_to = next_occurrence_after(
            local_time=alarm.local_time,
            weekdays=alarm.recurrence.get("weekdays") or [],
            after=now,
            timezone=alarm.timezone,
        )
    except Exception as exc:  # noqa: BLE001 - a broken recurrence must not stop the tick
        logger.warning(
            "alarm_catch_up_failed", alarm_id=str(alarm.id), error=type(exc).__name__
        )
        return False
    logger.info(
        "alarm_recurrence_caught_up",
        alarm_id=str(alarm.id),
        was=_aware(alarm.scheduled_for).isoformat(),
        now_at=moved_to.isoformat(),
        missed_by_s=int(late_by),
    )
    alarm.scheduled_for = moved_to
    # The arm on the device is for an instant that has passed; re-arming happens below on
    # the same tick, and `_should_arm` sees ARMED only as "already armed for the OLD one".
    alarm.armed_at = None
    if alarm.state == STATE_ARMED:
        alarm.state = STATE_SCHEDULED
    return True


def _should_arm(alarm: WakeAlarm, now: datetime) -> bool:
    if alarm.state == STATE_ARMED:
        return False
    return (_aware(alarm.scheduled_for) - now).total_seconds() <= ARM_LEAD_S


def _greeting_due(alarm: WakeAlarm, now: datetime) -> bool:
    if alarm.greeted_at is not None or alarm.greeting_due_at is None:
        return False
    if not (alarm.greeting_policy or {}).get("enabled", True):
        return False
    return _aware(alarm.greeting_due_at) <= now


def _play_expired(alarm: WakeAlarm, now: datetime) -> bool:
    if alarm.playing_since is None:
        return False
    return (now - _aware(alarm.playing_since)).total_seconds() >= alarm.max_play_seconds


def complete_alarm(
    session: Session,
    alarm: WakeAlarm,
    *,
    sequence: WakeSequence | None = None,
    now: datetime | None = None,
) -> WakeAlarm:
    moment = now or utcnow()
    if sequence is not None:
        sequence.stop_playback(session, alarm, reason="completed", now=moment)
    transition(session, alarm, STATE_COMPLETED, now=moment, reason="max_play_seconds")
    _release(session, alarm, sequence=sequence, reason="completed", now=moment)
    return alarm


# ------------------------------------------------------------- device reconciliation


def reconcile_local_fired(
    session: Session, alarm_ids: list[str], *, now: datetime | None = None
) -> list[WakeAlarm]:
    """The device rang its own armed fallback (spec §3.6d).

    The cloud did not reach it in time and the fallback did exactly its job. The alarm is
    marked PLAYING with ``media_kind=local_fallback`` so a later stop/snooze addresses the
    real thing, and the ledger says what happened — never a second ring on top.
    """
    moment = now or utcnow()
    touched: list[WakeAlarm] = []
    for raw in alarm_ids:
        try:
            alarm = get_alarm(session, uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            continue
        if alarm is None:
            continue
        if alarm.state in ALARM_TERMINAL_STATES:
            # The cloud already closed this alarm - it gave up on an unreachable device
            # (FAILED), or the owner cancelled it and the disarm never arrived - and the
            # device rang its fallback anyway. The state stays terminal (nothing is
            # resurrected, nothing rings twice), but the FACT is recorded, once: a wake-up
            # that happened must not vanish from the record because the cloud was not
            # there to see it (owner day plan 2026-09-07 §11: no silent loss).
            _record_ledger(
                session,
                event_type=EVENT_TYPE_ALARM_LOCAL_FALLBACK_RANG,
                alarm=alarm,
                action="alarm_local_fallback_rang",
                factual_summary=(
                    f"Cihaz kendi yedek alarmını çaldı: {alarm.local_time} "
                    f"(bulut kaydı {alarm.state.lower()} durumundaydı)"
                ),
                source_ref=f"alarms:{alarm.id}:local_fallback:{_occurrence(alarm)}",
                detail={
                    "media_kind": PLAYED_KIND_LOCAL_FALLBACK,
                    "state_at_reconcile": alarm.state,
                    "terminal_reason": alarm.terminal_reason,
                },
            )
            continue
        if alarm.state not in ALARM_ACTIVE_STATES:
            transition(session, alarm, STATE_FIRING, now=moment, reason="local_fallback")
        alarm.media_kind = PLAYED_KIND_LOCAL_FALLBACK
        alarm.playing_since = alarm.playing_since or moment
        transition(session, alarm, STATE_PLAYING, now=moment, media_kind=PLAYED_KIND_LOCAL_FALLBACK)
        _record_ledger(
            session,
            event_type=EVENT_TYPE_ALARM_LOCAL_FALLBACK_RANG,
            alarm=alarm,
            action="alarm_local_fallback_rang",
            factual_summary=f"Cihaz kendi yedek alarmını çaldı: {alarm.local_time}",
            source_ref=f"alarms:{alarm.id}:local_fallback:{_occurrence(alarm)}",
            detail={"media_kind": PLAYED_KIND_LOCAL_FALLBACK},
        )
        touched.append(alarm)
        # ADR-0079 §6: the device's own ring is a wake too; the screens stay.
        _note_alarm_wake(session, now=moment)
    return touched


def reconcile_local_snoozed(
    session: Session,
    entries: list[tuple[str, datetime]],
    *,
    now: datetime | None = None,
) -> list[WakeAlarm]:
    """The device snoozed a ringing alarm on its own (B47; B13 requirement 259).

    The owner said "ertele" to the device while this cloud could not be reached; the device
    stopped the ring, armed the same alarm again and reported ``until``, the instant it will
    ring. The cloud adopts that instant through the ordinary snooze, so the count, the limit
    and the ledger are the same as for a snooze the cloud performed itself. An alarm that is
    no longer active, already at its limit, or unknown is left alone and the fact recorded -
    the device's own ring at ``until`` still happens, and ``reconcile_local_fired`` records it.
    """
    moment = now or utcnow()
    touched: list[WakeAlarm] = []
    for raw, until in entries:
        try:
            alarm = get_alarm(session, uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            continue
        if alarm is None:
            continue
        if alarm.state not in ALARM_ACTIVE_STATES:
            _record_ledger(
                session,
                event_type=EVENT_TYPE_ALARM_LOCAL_SNOOZED,
                alarm=alarm,
                action="alarm_local_snooze_not_adopted",
                factual_summary=(
                    f"Cihaz alarmı kendi erteledi ({_aware(until).isoformat()}); "
                    f"bulut kaydı {alarm.state.lower()} durumundaydı, benimsenmedi"
                ),
                source_ref=f"alarms:{alarm.id}:local_snooze:{int(_aware(until).timestamp())}",
                detail={"until": _aware(until).isoformat(), "state_at_reconcile": alarm.state},
            )
            continue
        span = max(1, math.ceil((_aware(until) - moment).total_seconds() / 60))
        try:
            snooze_alarm(
                session,
                alarm.id,
                minutes=min(span, MAX_SNOOZE_MINUTES),
                now=moment,
                resume_at=until,
            )
        except InvalidAlarmRequest as exc:
            _record_ledger(
                session,
                event_type=EVENT_TYPE_ALARM_LOCAL_SNOOZED,
                alarm=alarm,
                action="alarm_local_snooze_not_adopted",
                factual_summary=f"Cihazın yerel ertelemesi benimsenmedi: {exc}",
                source_ref=f"alarms:{alarm.id}:local_snooze:{int(_aware(until).timestamp())}",
                detail={"until": _aware(until).isoformat(), "reason": str(exc)},
            )
            continue
        _record_ledger(
            session,
            event_type=EVENT_TYPE_ALARM_LOCAL_SNOOZED,
            alarm=alarm,
            action="alarm_local_snoozed",
            factual_summary=f"Cihaz alarmı çevrimdışıyken erteledi; yeni saat {alarm.local_time}",
            source_ref=f"alarms:{alarm.id}:local_snooze:{int(_aware(until).timestamp())}",
            detail={"until": _aware(until).isoformat(), "snooze_count": alarm.snooze_count},
        )
        touched.append(alarm)
    return touched


# -------------------------------------------------------------------- the release


def _release(
    session: Session,
    alarm: WakeAlarm,
    *,
    sequence: WakeSequence | None,
    reason: str,
    now: datetime | None = None,
) -> None:
    """Everything a terminal alarm must let go of (spec §8.1), on EVERY terminal state.

    Disarm the device, close the media session, resolve or re-schedule the routine, write
    ``alarm.cleaned_up``. Not a test-alarm special case: a real alarm that leaves a device
    armed would ring again tomorrow for no reason.
    """
    moment = now or utcnow()
    disarmed = False
    if sequence is not None:
        try:
            disarmed = sequence.disarm(session, alarm, reason=reason, now=moment).ok
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort, never a new failure
            logger.warning("alarm_disarm_failed", alarm_id=str(alarm.id), error=type(exc).__name__)
    alarm.media_session_id = None
    alarm.greeting_due_at = None

    rescheduled = False
    if alarm.recurrence and alarm.state not in (STATE_CANCELLED,):
        # A recurring alarm's next occurrence: the schedule routine stays armed, and the
        # aggregate follows it forward so "Sabah alarmım kaçta?" answers about tomorrow.
        try:
            alarm.scheduled_for = next_occurrence_after(
                local_time=alarm.local_time,
                weekdays=alarm.recurrence.get("weekdays") or [],
                after=moment,
                timezone=alarm.timezone,
            )
            alarm.state = STATE_SCHEDULED
            alarm.terminal_at = None
            alarm.terminal_state = None
            alarm.terminal_reason = None
            alarm.last_firing_id = None
            alarm.media_kind = None
            alarm.greeted_at = None
            alarm.playing_since = None
            alarm.armed_at = None
            alarm.triggered_at = None
            rescheduled = True
        except Exception as exc:  # noqa: BLE001 - a broken recurrence must not break cleanup
            logger.warning(
                "alarm_reschedule_failed", alarm_id=str(alarm.id), error=type(exc).__name__
            )
    elif alarm.routine_id is not None:
        try:
            routine = session.get(Routine, alarm.routine_id)
            if routine is not None and routine.status == ROUTINE_STATUS_ARMED:
                routines_service.cancel_routine(
                    session, routine.routine_id, reason=f"alarm_{alarm.state.lower()}"
                )
        except Exception as exc:  # noqa: BLE001 - see above
            logger.warning(
                "alarm_routine_cancel_failed", alarm_id=str(alarm.id), error=type(exc).__name__
            )
    session.commit()

    _record_ledger(
        session,
        event_type=EVENT_TYPE_ALARM_CLEANED_UP,
        alarm=alarm,
        action="alarm_cleaned_up",
        factual_summary=f"Alarm serbest bırakıldı: {alarm.local_time} ({reason})",
        source_ref=f"alarms:{alarm.id}:cleaned_up:{_occurrence(alarm)}:{reason}",
        detail={
            "reason": reason,
            "device_disarmed": disarmed,
            "rescheduled": rescheduled,
            "is_test": alarm.is_test,
        },
    )


# ------------------------------------------------------------------------- speech


def status_speech(session: Session, *, now: datetime | None = None) -> tuple[str, WakeAlarm | None]:
    """The "Sabah alarmım kaçta?" answer and the alarm it is about (spec §6)."""
    alarm = next_alarm(session, now=now)
    return alarm_speech.alarm_query_speech(alarm.local_time if alarm else None), alarm


def alarm_dict(alarm: WakeAlarm) -> dict[str, Any]:
    return {
        "alarm_id": str(alarm.id),
        "state": alarm.state,
        "timezone": alarm.timezone,
        "scheduled_for": _aware(alarm.scheduled_for).astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        ),
        "local_time": alarm.local_time,
        "recurrence": alarm.recurrence,
        "media_source": alarm.media_source,
        "resolved_media_identity": alarm.resolved_media_identity,
        "media_kind": alarm.media_kind,
        # Directive 2026-09-08 item C ("never silently fall back"): WHY the tone played
        # instead of the owner's media, readable from this row alone — set by
        # ``WakeSequence.fire`` only when a youtube attempt genuinely failed, never
        # invented for an alarm that simply asked for the tone.
        "media_failure_reason": (alarm.detail_json or {}).get("media_failure_reason"),
        "volume_policy": alarm.volume_policy,
        "greeting_policy": alarm.greeting_policy,
        "display_wake_policy": alarm.display_wake_policy,
        "is_test": alarm.is_test,
        "label": alarm.label,
        "routine_id": str(alarm.routine_id) if alarm.routine_id else None,
        "snooze_count": alarm.snooze_count,
        "snooze_minutes": alarm.snooze_minutes,
        "terminal_state": alarm.terminal_state,
        "terminal_reason": alarm.terminal_reason,
        "max_play_seconds": alarm.max_play_seconds,
        "greeted_at": _aware(alarm.greeted_at).isoformat() if alarm.greeted_at else None,
        # B13 req 267: why the greeting was not spoken, readable from this row alone.
        # `no_tts_key` is the one that used to be invisible - with no key the provider
        # produced a 110 Hz sine wave and the system played it AS the greeting, so a buzz
        # in a bedroom at 07:30 was indistinguishable from a fault and the row said nothing.
        "greeting_failure": (alarm.detail_json or {}).get("greeting_failure"),
        # B15 req 281, by the same reasoning one batch later. The briefing is spoken in
        # CLIPS, so "it did not happen" is not the only bad outcome - "the owner heard two
        # of four and cannot tell which two are missing" is the one this batch went out of
        # its way to make visible, and it would be invisible again if the receipt stopped
        # at `detail_json`. `{clips, spoken, failure, complete}`, or None on a morning with
        # no briefing at all.
        "briefing": (alarm.detail_json or {}).get("briefing"),
        # The lifecycle instants and the device, so an owner harness can prove "armed on
        # the device", "fired at the scheduled instant" and "cleaned up" from this row alone.
        "device_id": str(alarm.device_id) if alarm.device_id else None,
        "last_firing_id": str(alarm.last_firing_id) if alarm.last_firing_id else None,
        "created_at": _aware(alarm.created_at).isoformat() if alarm.created_at else None,
        "armed_at": _aware(alarm.armed_at).isoformat() if alarm.armed_at else None,
        "triggered_at": _aware(alarm.triggered_at).isoformat() if alarm.triggered_at else None,
        "terminal_at": _aware(alarm.terminal_at).isoformat() if alarm.terminal_at else None,
    }


__all__ = [
    "ARM_LEAD_S",
    "MAX_LATE_FIRE_S",
    "AlarmNotFoundError",
    "FireDecision",
    "InvalidAlarmRequest",
    "TickResult",
    "alarm_dict",
    "alarms_ringing",
    "cancel_alarm",
    "complete_alarm",
    "create_alarm",
    "fire_alarm",
    "get_alarm",
    "list_alarms",
    "next_alarm",
    "reconcile_local_fired",
    "reconcile_local_snoozed",
    "require_alarm",
    "snooze_alarm",
    "status_speech",
    "stop_alarm",
    "tick",
    "transition",
    "utcnow",
]
