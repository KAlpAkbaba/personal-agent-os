"""The wake alarm aggregate (M18.3 spec §3.1) and the ambient policy row (§3.9).

Canonical schema: ``alembic/versions/20260907_0021_wake_alarms.py``. Same discipline as
``app.routines.models``: portable column types (generic ``Uuid``, ``JSON`` with a ``JSONB``
variant) so the service layer unit-tests on SQLite, plus ``CheckConstraint``s spelling out
the closed state vocabulary so an invalid state can never reach the database even from a
path that skips the Python-side validation.

``WakeAlarm`` is the aggregate; the TRIGGER is a routine (``app.routines``) carrying a
single ``wake_alarm`` action — a one-shot ``at`` routine, a recurring ``schedule`` routine,
or a fresh ``at`` routine per snooze. The routine engine's ``RoutineFiring`` uniqueness on
``(routine_id, occurrence_key)`` therefore stays the FIRST line against a double ring;
``WakeAlarm.last_firing_id`` is the second, and it is the one that survives a process
restart or a broker reconnect, because it lives on the alarm rather than on the occurrence.

``AmbientPolicyRow`` is a single-row table (``policy_id`` is the constant
:data:`AMBIENT_POLICY_ID`): the owner has one ambient display policy, and a table with a
fixed primary key is how this codebase already models "exactly one of these exists" without
inventing a preferences blob whose shape nothing validates (ADR-0071).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


# ------------------------------------------------------------------- the lifecycle

STATE_SCHEDULED = "SCHEDULED"
STATE_ARMED = "ARMED"
STATE_FIRING = "FIRING"
STATE_DISPLAY_WAKING = "DISPLAY_WAKING"
STATE_MEDIA_STARTING = "MEDIA_STARTING"
STATE_PLAYING = "PLAYING"
STATE_GREETING = "GREETING"
STATE_SNOOZED = "SNOOZED"
STATE_STOPPED = "STOPPED"
STATE_COMPLETED = "COMPLETED"
STATE_CANCELLED = "CANCELLED"
STATE_FAILED = "FAILED"

ALARM_STATES: tuple[str, ...] = (
    STATE_SCHEDULED,
    STATE_ARMED,
    STATE_FIRING,
    STATE_DISPLAY_WAKING,
    STATE_MEDIA_STARTING,
    STATE_PLAYING,
    STATE_GREETING,
    STATE_SNOOZED,
    STATE_STOPPED,
    STATE_COMPLETED,
    STATE_CANCELLED,
    STATE_FAILED,
)

#: No further transition follows one of these; the routine is resolved, the device is
#: disarmed and (for a test alarm) the media session is closed.
ALARM_TERMINAL_STATES: frozenset[str] = frozenset(
    {STATE_STOPPED, STATE_COMPLETED, STATE_CANCELLED, STATE_FAILED}
)

#: The same set, ordered, for the DDL CHECK (a frozenset's iteration order is not stable
#: across processes and a migration's SQL text must be).
TERMINAL_STATE_VALUES: tuple[str, ...] = (
    STATE_STOPPED,
    STATE_COMPLETED,
    STATE_CANCELLED,
    STATE_FAILED,
)

#: While the alarm is physically happening: the owner is being woken right now. Used by
#: the ambient policy (``alarm_context`` never produces a display-off) and by
#: ``alarm.snooze`` / ``alarm.stop``, which are only meaningful during these.
ALARM_ACTIVE_STATES: frozenset[str] = frozenset(
    {
        STATE_FIRING,
        STATE_DISPLAY_WAKING,
        STATE_MEDIA_STARTING,
        STATE_PLAYING,
        STATE_GREETING,
    }
)

#: Waiting for its moment. An alarm in one of these is what "Sabah alarmım kaçta?" answers
#: from, and what the tick tries to (re-)arm on the device.
ALARM_PENDING_STATES: frozenset[str] = frozenset({STATE_SCHEDULED, STATE_ARMED, STATE_SNOOZED})

# ------------------------------------------------------------------- media kinds

MEDIA_KIND_YOUTUBE = "youtube"
MEDIA_KIND_TONE = "tone"
MEDIA_KIND_REMEMBERED = "remembered"
MEDIA_SOURCE_KINDS: tuple[str, ...] = (MEDIA_KIND_YOUTUBE, MEDIA_KIND_TONE, MEDIA_KIND_REMEMBERED)

#: What actually played, once the sequence has decided (never what was asked for).
PLAYED_KIND_YOUTUBE = "youtube"
PLAYED_KIND_TONE_FALLBACK = "tone_fallback"
PLAYED_KIND_LOCAL_FALLBACK = "local_fallback"
PLAYED_KINDS: tuple[str, ...] = (
    PLAYED_KIND_YOUTUBE,
    PLAYED_KIND_TONE_FALLBACK,
    PLAYED_KIND_LOCAL_FALLBACK,
)

DEFAULT_TIMEZONE = "Europe/Istanbul"
DEFAULT_MAX_PLAY_SECONDS = 600
TEST_MAX_PLAY_SECONDS = 120
DEFAULT_SNOOZE_MINUTES = 5
MAX_SNOOZE_MINUTES = 60

#: B13 req 259: how many times one wake-up may be pushed forward before the system stops
#: agreeing to it.
#:
#: There was no cap. An alarm could be snoozed indefinitely and would never reach a terminal
#: state, so a wake-up nobody ever got up for stayed live in the row, in the world model and
#: on the device's arm for as long as somebody kept pressing - and the ledger recorded an
#: unbounded run of SNOOZED transitions for one morning.
#:
#: Five, because five is already 25 minutes past the moment the owner asked to be woken at
#: the default span, and a system that keeps agreeing after that is not helping. The refusal
#: is explicit and says so; it does NOT silently stop ringing.
MAX_SNOOZE_COUNT = 5


class WakeAlarm(Base):
    """One wake alarm. ``id`` is the ``alarm_id`` used everywhere: the routine action
    detail, the device arm/disarm/start payloads, the media session id, every receipt."""

    __tablename__ = "wake_alarms"
    __table_args__ = (
        CheckConstraint(_in_list("state", ALARM_STATES), name="ck_wake_alarms_state"),
        CheckConstraint(
            f"terminal_state IS NULL OR {_in_list('terminal_state', TERMINAL_STATE_VALUES)}",
            name="ck_wake_alarms_terminal_state",
        ),
        Index("ix_wake_alarms_state", "state"),
        Index("ix_wake_alarms_scheduled_for", "scheduled_for"),
        Index("ix_wake_alarms_routine_id", "routine_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="owner")
    #: The device armed for this alarm. Nullable until armed, and re-selected by capability
    #: at fire time when that device is offline — an alarm is never lost because the machine
    #: it was armed on went away.
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: IANA zone; never UTC for an owner-facing schedule (spec §3.1).
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default=DEFAULT_TIMEZONE)
    #: The NEXT occurrence as a tz-aware instant (stored UTC, spoken local).
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: "HH:MM" exactly as the owner said it, in ``timezone``.
    local_time: Mapped[str] = mapped_column(String(5), nullable=False)
    #: None for a one-shot; {"weekdays": [0..6]} for a recurring alarm.
    recurrence: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default=STATE_SCHEDULED)
    #: What the owner ASKED for: {"kind": "youtube"|"tone"|"remembered", ...}.
    media_source: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: {"kind","url","title","video_id"} once resolved, else NULL. Never invented: a title
    #: with no url stays unresolved and the alarm rings the tone.
    resolved_media_identity: Mapped[dict[str, Any] | None] = mapped_column(
        JSONColumn, nullable=True
    )
    volume_policy: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    greeting_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    display_wake_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The routine currently carrying this alarm's trigger (one-shot ``at`` or ``schedule``;
    #: replaced by a fresh ``at`` routine on every snooze).
    routine_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    snooze_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snooze_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_SNOOZE_MINUTES
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The ``RoutineFiring`` that started the CURRENT ring. Set once, at the top of the fire
    #: sequence, and the reason a second tick / a second process / a reconnect produce ONE
    #: firing and ONE ring: the sequence refuses to start again while a firing id is held
    #: for a non-terminal alarm.
    last_firing_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: The browser worker media session ("alarm-<alarm_id>"), while one is open.
    media_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: youtube | tone_fallback | local_fallback — what actually played.
    media_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    greeting_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    greeted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    playing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_play_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MAX_PLAY_SECONDS
    )
    #: Free-form audit/sequencing detail (arm acknowledgements, failure reasons per step).
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state in ALARM_TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        return self.state in ALARM_ACTIVE_STATES


# --------------------------------------------------------------- the ambient policy

#: The single row's primary key. One owner, one ambient policy (module docstring).
AMBIENT_POLICY_ID = "owner"


class AmbientPolicyRow(Base):
    """The owner's persisted ambient display policy (spec §3.9).

    Defaults are the conservative ones the spec fixes: automatic display-off is OFF until
    the owner turns it on, and every threshold is long. "Uncertain means ON" is enforced in
    ``app.ambient.policy.decide``, not here — this row only carries what the owner chose.
    """

    __tablename__ = "ambient_policy"

    policy_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=AMBIENT_POLICY_ID)
    auto_off_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    off_when_away: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    off_when_asleep: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    wake_on_return: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    away_after_s: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    asleep_after_s: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    asleep_min_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    input_holdoff_s: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    command_holdoff_s: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    alarm_holdoff_s: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    return_holdoff_s: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    #: None, or {"start": "HH:MM", "end": "HH:MM", "timezone"?: IANA} (ADR-0079 §8): the
    #: owner's quiet window. Inside it LIKELY_ASLEEP needs ``asleep_after_s``; outside it
    #: ``asleep_after_outside_quiet_s``. Never used to force an off.
    quiet_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: ADR-0079 §7: "Ekranı açık tut." — outranks every inference and holdoff until lifted.
    keep_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: ADR-0079 §8: how long LIKELY_ASLEEP must hold outside the quiet hours.
    asleep_after_outside_quiet_s: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1800
    )
    #: ADR-0079 §3: the newest camera observation may be at most this old for an off.
    camera_unknown_grace_s: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    #: B48 (rows 300, 331, 671): the owner's device-camera choice - "off" | "periodic" |
    #: "continuous". Off until the owner says otherwise; Cloud Core relays it to every device
    #: that reports a camera path, and only while the Active Eye is enabled.
    camera_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="off")
    #: The owner's approved wake song — {"url": ..., "title": ...} — the one item
    #: "seçtiğim müzik" / a remembered title resolves to (spec §3.8). Set only by the owner
    #: (a URL they named); never chosen by the system. None until the owner picks one.
    wake_song: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "ALARM_ACTIVE_STATES",
    "ALARM_PENDING_STATES",
    "ALARM_STATES",
    "ALARM_TERMINAL_STATES",
    "AMBIENT_POLICY_ID",
    "DEFAULT_MAX_PLAY_SECONDS",
    "DEFAULT_SNOOZE_MINUTES",
    "DEFAULT_TIMEZONE",
    "MAX_SNOOZE_COUNT",
    "MAX_SNOOZE_MINUTES",
    "MEDIA_KIND_REMEMBERED",
    "MEDIA_KIND_TONE",
    "MEDIA_KIND_YOUTUBE",
    "MEDIA_SOURCE_KINDS",
    "PLAYED_KINDS",
    "PLAYED_KIND_LOCAL_FALLBACK",
    "PLAYED_KIND_TONE_FALLBACK",
    "PLAYED_KIND_YOUTUBE",
    "STATE_ARMED",
    "STATE_CANCELLED",
    "STATE_COMPLETED",
    "STATE_DISPLAY_WAKING",
    "STATE_FAILED",
    "STATE_FIRING",
    "STATE_GREETING",
    "STATE_MEDIA_STARTING",
    "STATE_PLAYING",
    "STATE_SCHEDULED",
    "STATE_SNOOZED",
    "STATE_STOPPED",
    "TEST_MAX_PLAY_SECONDS",
    "AmbientPolicyRow",
    "WakeAlarm",
]
