"""B14 req 287-292: the owner's voice reaches their own routines.

The matrix called 287 "the biggest invisible feature", and that was exact. The engine has
been complete since M18 — triggers, conditions, actions, firings, a ledger row per
transition, a REST surface, a clock driving it every ten seconds — and every one of the
seven routines in production was created by the ALARM subsystem on the owner's behalf.
Nothing the owner said could make an eighth.

Requirement 292 comes along for the ride and is the reason these tests do not stop at "the
tool returned ok": the presence trigger has been implemented and tested since M18 and had no
creator anywhere in `app/`. A tool that can make one is what turns "defined" into "used", so
the presence test here goes all the way through `evaluate_due` to a dispatch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.routines import service as routines_service
from app.routines.models import (
    ROUTINE_STATUS_ARMED,
    ROUTINE_STATUS_CANCELLED,
    ROUTINE_STATUS_PAUSED,
    Routine,
    RoutineFiring,
)
from app.uistate.contract import UiState, UiStateEvent
from app.uistate.publisher import UiStatePublisher, set_publisher
from app.voice.errors import VoiceError
from app.voice.realtime_sessions import tools_routines
from app.voice.realtime_sessions.tools import ToolContext, default_registry

NOW = datetime(2026, 9, 9, 4, 0, tzinfo=UTC)
TABLES = [Routine.__table__, RoutineFiring.__table__, ActivityEventRow.__table__]

BRIEFING = {"kind": "voice_briefing", "detail": {"text": "Günaydın efendim."}}


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_publisher():
    """`app.uistate.publisher` is a process-wide singleton, so a presence test that read a
    leftover tail from another file would pass for the wrong reason."""
    set_publisher(UiStatePublisher())


def _ctx(session) -> ToolContext:
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="test",
        context={},
        db=session,
        now=NOW,
    )


class _Counting:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, *, routine_id, action, firing_id, now=None):  # noqa: ANN001, ARG002
        from app.routines.actions import DispatchOutcome

        self.calls += 1
        return DispatchOutcome.succeeded({})


# ------------------------------------------------------------- 287: the owner can create


def test_the_owner_can_create_a_routine_by_speaking(session):
    result = tools_routines.routine_create(
        _ctx(session),
        {
            "name": "Sabah haberleri",
            "trigger_kind": "schedule",
            "trigger": {
                "weekdays": [0, 1, 2, 3, 4],
                "time": "08:00",
                "timezone": "Europe/Istanbul",
            },
            "actions": [BRIEFING],
        },
    )

    routine = session.get(Routine, uuid.UUID(result["routine_id"]))
    assert routine.status == ROUTINE_STATUS_ARMED
    assert routine.trigger_json["weekdays"] == [0, 1, 2, 3, 4]
    # The row records that the OWNER asked for this, not the alarm subsystem.
    assert routine.source == "voice"


def test_the_spoken_confirmation_says_when_it_will_run(session):
    """"Rutin kuruldu" alone is not a confirmation the owner can check. If the weekday set
    was mis-heard, the only place they can catch it is in what is read back."""
    result = tools_routines.routine_create(
        _ctx(session),
        {
            "name": "Sabah haberleri",
            "trigger_kind": "schedule",
            "trigger": {
                "weekdays": [0, 1, 2, 3, 4],
                "time": "08:00",
                "timezone": "Europe/Istanbul",
            },
            "actions": [BRIEFING],
        },
    )

    assert "hafta içi 08:00" in result["speech"]


def test_a_routine_the_engine_would_refuse_is_never_reported_as_created(session):
    """The tool re-checks nothing and invents nothing. A trigger it approved and the engine
    then rejected would be a routine the owner was told they had and does not."""
    with pytest.raises(VoiceError):
        tools_routines.routine_create(
            _ctx(session),
            {
                "name": "Bozuk",
                "trigger_kind": "schedule",
                "trigger": {"weekdays": [9], "time": "08:00", "timezone": "Europe/Istanbul"},
                "actions": [BRIEFING],
            },
        )

    assert routines_service.list_routines(session) == []


def test_an_unknown_trigger_kind_is_refused_by_name(session):
    with pytest.raises(VoiceError) as refused:
        tools_routines.routine_create(
            _ctx(session),
            {"name": "X", "trigger_kind": "telepathy", "trigger": {}, "actions": [BRIEFING]},
        )

    assert "telepathy" in str(refused.value)


# ------------------------------------------------------------------- 288: the owner can ask


def test_the_owner_is_told_they_have_none_rather_than_nothing(session):
    assert tools_routines.routine_list(_ctx(session), {})["speech"] == (
        "Kurulu rutininiz yok efendim."
    )


def test_the_list_includes_a_paused_routine_and_says_it_is_paused(session):
    """A paused routine is one the owner still HAS. Leaving it out would answer "you have
    three" to somebody who has four and turned one off."""
    ctx = _ctx(session)
    created = tools_routines.routine_create(
        ctx,
        {
            "name": "Sabah haberleri",
            "trigger_kind": "schedule",
            "trigger": {"weekdays": [0], "time": "08:00", "timezone": "Europe/Istanbul"},
            "actions": [BRIEFING],
        },
    )
    tools_routines.routine_pause(ctx, {"routine_id": created["routine_id"]})

    listed = tools_routines.routine_list(ctx, {})

    assert listed["count"] == 1
    assert "duraklatılmış" in listed["speech"]


def test_a_long_list_is_counted_in_full_and_read_in_part(session):
    """A spoken answer that runs to twenty items is an answer nobody hears the end of - but
    the COUNT must still be the truth, or the owner is told they have five."""
    ctx = _ctx(session)
    for i in range(tools_routines.SPOKEN_LIST_MAX + 3):
        tools_routines.routine_create(
            ctx,
            {
                "name": f"Rutin {i}",
                "trigger_kind": "schedule",
                "trigger": {"weekdays": [i % 7], "time": "08:00", "timezone": "Europe/Istanbul"},
                "actions": [BRIEFING],
            },
        )

    listed = tools_routines.routine_list(ctx, {})

    assert listed["count"] == tools_routines.SPOKEN_LIST_MAX + 3
    assert "3 tane daha" in listed["speech"]


# --------------------------------------------------------- 289/290/291: cancel, pause, resume


def _one(session, ctx) -> str:
    return tools_routines.routine_create(
        ctx,
        {
            "name": "Sabah rutini",
            "trigger_kind": "schedule",
            "trigger": {"weekdays": [0], "time": "08:00", "timezone": "Europe/Istanbul"},
            "actions": [BRIEFING],
        },
    )["routine_id"]


def test_the_owner_can_cancel_by_voice(session):
    ctx = _ctx(session)
    routine_id = _one(session, ctx)

    result = tools_routines.routine_cancel(ctx, {"routine_id": routine_id})

    assert result["status"] == ROUTINE_STATUS_CANCELLED


def test_the_owner_can_pause_and_resume_by_voice(session):
    ctx = _ctx(session)
    routine_id = _one(session, ctx)

    paused = tools_routines.routine_pause(ctx, {"routine_id": routine_id})
    resumed = tools_routines.routine_resume(ctx, {"routine_id": routine_id})

    assert paused["status"] == ROUTINE_STATUS_PAUSED
    assert resumed["status"] == ROUTINE_STATUS_ARMED


def test_the_pause_sentence_promises_what_pausing_actually_does(session):
    """It says the routine will not run until the owner says so. It must not say
    "cancelled", and it must not imply the missed occurrences will be caught up."""
    ctx = _ctx(session)
    routine_id = _one(session, ctx)

    speech = tools_routines.routine_pause(ctx, {"routine_id": routine_id})["speech"]

    assert "duraklatıldı" in speech
    assert "iptal" not in speech


def test_a_routine_that_is_not_there_is_refused_with_its_id(session):
    with pytest.raises(VoiceError) as refused:
        tools_routines.routine_cancel(_ctx(session), {"routine_id": str(uuid.uuid4())})

    assert "routine" in str(refused.value).lower()


def test_something_that_is_not_an_id_is_refused_before_the_database(session):
    with pytest.raises(VoiceError) as refused:
        tools_routines.routine_pause(_ctx(session), {"routine_id": "sabah rutini"})

    assert "sabah rutini" in str(refused.value)


# ---------------------------------------------------- 292: a presence trigger with a creator


def test_a_presence_routine_created_by_voice_actually_fires(session):
    """req 292, and the whole point of it. The presence trigger has been implemented and
    tested since M18 and had NO creator anywhere in `app/` - it was "defined, never used",
    which is a working mechanism nobody can reach. So this goes past the tool's answer, all
    the way through `evaluate_due` to a dispatch."""
    created = tools_routines.routine_create(
        _ctx(session),
        {
            "name": "Eve gelince",
            "trigger_kind": "presence",
            "trigger": {"event": "owner.returned"},
            "actions": [BRIEFING],
        },
    )
    assert created["status"] == ROUTINE_STATUS_ARMED

    get_publisher = __import__(
        "app.uistate.publisher", fromlist=["get_publisher"]
    ).get_publisher
    get_publisher().publish(UiStateEvent(state=UiState.OWNER_RETURNED, subsystem="presence"))
    dispatcher = _Counting()

    routines_service.evaluate_due(
        session, now=NOW + timedelta(seconds=1), dispatcher=dispatcher
    )

    assert dispatcher.calls == 1


# ------------------------------------------------------------------------ registration


def test_all_five_tools_are_registered_on_the_real_registry():
    """A tool module nothing calls is the defect this repository keeps paying for. The
    registry is what the realtime session actually offers the model."""
    names = set(default_registry().names())

    assert set(tools_routines.ROUTINE_TOOL_NAMES) <= names


def test_every_routine_tool_has_a_step_up_tier():
    """B05's gate covers every registered tool and fails closed. Named here too, because a
    tool that installs something which acts on the owner's behalf UNATTENDED, every morning,
    is exactly the kind that must not be OPEN by accident."""
    from app.security.step_up import TIER_OPEN, tier_of

    assert tier_of(tools_routines.TOOL_ROUTINE_CREATE) != TIER_OPEN
    assert tier_of(tools_routines.TOOL_ROUTINE_PAUSE) != TIER_OPEN
    assert tier_of(tools_routines.TOOL_ROUTINE_LIST) == TIER_OPEN


# ----------------------------------------- the one routine the voice path cannot create


def test_a_text_carrying_routine_cannot_travel_over_the_tool_wire():
    """Not a bug, and not worked around: a rule this batch found and left standing.

    The relay refuses any argument key matching `text` — a transcript must never travel as
    a tool argument. `voice_briefing`'s detail IS a free-text payload, so a routine carrying
    one is refused before `routine.create` is ever reached. The five other action kinds
    carry no free text and work by voice.

    Pinned here so a future change to either side is a decision rather than a surprise: if
    the blocklist changes, or if `voice_briefing` grows a selector instead of a payload,
    this test says so.
    """
    from app.voice.realtime_sessions.service import is_forbidden_key

    assert is_forbidden_key("text") is True
    for wire_safe in ("action", "url", "alarm_id", "wake_volume", "trigger_kind"):
        assert is_forbidden_key(wire_safe) is False, wire_safe


def test_the_wire_safe_action_kinds_really_are_creatable_by_voice(session):
    """The other half: it would be easy to "document a limitation" that was actually total."""
    result = tools_routines.routine_create(
        _ctx(session),
        {
            "name": "Boşta kalınca ekranı kapat",
            "trigger_kind": "condition",
            "trigger": {"kind": "device_idle", "min_seconds": 600},
            "actions": [{"kind": "display_action", "detail": {"action": "off"}}],
        },
    )

    assert result["status"] == ROUTINE_STATUS_ARMED
    assert "bilgisayar 10 dakika boşta kalınca" in result["speech"]
