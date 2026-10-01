"""Routines, their firings and the wake alarms on the REAL database (ADR-0214 addendum 4).

``routines``, ``routine_firings`` and ``wake_alarms`` are what the owner's mornings are
written to, and until 2026-10-01 no test under ``tests/integration`` named any of them:
every write had only met SQLite, which does not enforce a VARCHAR's length, has no JSONB and
hands back naive datetimes. These tests take the same writes to the dev stack's PostgreSQL
through the production functions that make them - ``app.routines.service``,
``app.alarms.service``, the production dispatcher and wake sequence (in front of a fake
device), the voice tools and the REST surface in front of them, never hand-written SQL -
with the values SQLite forgives: the longest string each column's own
validation allows (and one more where the code claims to refuse it), JSONB documents with
nested Turkish text, timezone-aware timestamps that are not UTC, and a NULL in every
nullable column.

Every instant here is in 2001, so an alarm "rung" by a test starts a display holdoff that
ended long ago. The year protects nothing else: ``evaluate_due`` looks at every armed routine
in the database and a weekday schedule is due at its minute in 2001 as in any year. What
keeps these tests off a developer's own routines is ``_refuse_foreign_armed``, in front of
every ``evaluate_due`` and every alarm ``tick`` in this file: it fails the test rather than
run the engine while an armed routine exists that the test did not create.

Rows are namespaced with a per-test token and deleted when the test ends. The ledger rows
the services write along the way stay: the ledger is append-only.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, or_, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DataError, PendingRollbackError
from sqlalchemy.orm import Session, sessionmaker

from app.alarms import history as alarm_history
from app.alarms import service as alarms_service
from app.alarms.audio_store import AudioStore
from app.alarms.models import WakeAlarm
from app.alarms.routine_port import WakeAlarmRunner
from app.alarms.sequence import WakeSequence, media_session_id
from app.alarms.tr_time import ParsedWhen
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.ledger.vocabulary import EVENT_TYPE_ALARM_CLEANED_UP
from app.routines import conditions as conditions_mod
from app.routines import service as routines_service
from app.routines.actions import NoopDispatcher
from app.routines.conditions import RoutineConditionContext
from app.routines.dispatch import ActionDispatcher
from app.routines.models import ROUTINE_STATUS_ARMED, Routine, RoutineFiring
from app.voice.errors import VoiceError
from app.voice.providers import FakeTTSProvider
from app.voice.realtime_sessions import tools_routines
from app.voice.realtime_sessions.tools import ToolContext
from tests.alarms_support import FakeDeviceAction, happy_device_results
from tests.integration.conftest import owner_client, shared_identity

pytestmark = pytest.mark.integration

ISTANBUL = ZoneInfo("Europe/Istanbul")
#: Aware, and three hours from UTC: 04:46:40 in Istanbul is 01:46:40Z.
MOMENT = datetime(2001, 9, 9, 4, 46, 40, tzinfo=ISTANBUL)

NESTED = {
    "başlık": "Sabah rutini — İstanbul",
    "ayrıntı": {
        "şehir": "Iğdır",
        "ölçü": [1, 2.5, None, True],
        "notlar": ["ğ", {"iç içe": "öğle üstü, çay demli"}],
    },
    "boş": None,
}


def _exactly(length: int, prefix: str) -> str:
    """Exactly ``length`` CHARACTERS, most of them Turkish (two bytes each in UTF-8, so a
    column or a validation that counted bytes would not survive this)."""
    filler = "ığüşöçİĞÜŞÖÇ"
    text = (prefix + filler * (length // len(filler) + 1))[:length]
    assert len(text) == length
    return text


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def db(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = build_engine(settings.database_url)
    # The whole point of this file. A run that reached SQLite would prove nothing here.
    assert engine.dialect.name == "postgresql"
    try:
        yield build_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture()
def token() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture()
def made(db: sessionmaker[Session], token: str) -> Iterator[SimpleNamespace]:
    """What a test created, gone afterwards whether it passed or not.

    No production path deletes a routine or an alarm. The firings go with their routine by
    the table's own ON DELETE CASCADE - which only PostgreSQL enforces, so a missing cascade
    fails here, loudly, as a foreign-key error.
    """
    created = SimpleNamespace(routines=[], alarms=[])
    try:
        yield created
    finally:
        with db() as session:
            mine = [Routine.name.like(f"pgcov-{token}%"), Routine.routine_id.in_(created.routines)]
            mine += [Routine.source_ref.like(f"alarm:{alarm_id}%") for alarm_id in created.alarms]
            session.execute(delete(Routine).where(or_(*mine)))
            session.execute(delete(WakeAlarm).where(WakeAlarm.id.in_(created.alarms)))
            session.commit()


def _refuse_foreign_armed(session: Session, made: SimpleNamespace, token: str) -> None:
    """Fail, loudly, rather than run the production engine over somebody else's routine.

    ``evaluate_due`` has no filter: it takes every armed routine in the database, and the
    year of ``now`` protects none of them but a one-shot in the future. A weekday schedule
    is due at its wall-clock minute in 2001 as in any year, a presence trigger does not look
    at ``now`` at all, and a condition trigger answers to the context the test passes. With
    the production dispatcher that means a developer's own 07:30 alarm rung by a test.

    Compared in Python, not in the WHERE clause: ``NOT (source_ref LIKE ...)`` is NULL for a
    NULL ``source_ref``, and a guard that loses a row to a NULL is not one.
    """
    armed = session.execute(
        select(Routine.routine_id, Routine.name, Routine.source_ref).where(
            Routine.status == ROUTINE_STATUS_ARMED
        )
    ).all()
    alarm_refs = tuple(f"alarm:{alarm_id}" for alarm_id in made.alarms)
    foreign = [
        row
        for row in armed
        if row.routine_id not in made.routines
        and not (row.name or "").startswith(f"pgcov-{token}")
        and not (row.source_ref or "").startswith(alarm_refs or ("\x00",))
    ]
    if foreign:
        listed = ", ".join(f"{row.routine_id} ({(row.name or '')[:40]!r})" for row in foreign[:5])
        pytest.fail(
            f"refusing to run the routine engine: {len(foreign)} armed routine(s) in this "
            f"database are not this test's own - {listed}. evaluate_due and the alarm tick "
            "act on every armed row whatever the year of `now`; pause or cancel them (or use "
            "a clean dev database) and run again."
        )


def _evaluate_due(
    session: Session, made: SimpleNamespace, token: str, **kwargs: Any
) -> routines_service.EvaluateDueResult:
    """``routines_service.evaluate_due``, the only way this file calls it."""
    _refuse_foreign_armed(session, made, token)
    return routines_service.evaluate_due(session, **kwargs)


def _tick(
    session: Session, made: SimpleNamespace, token: str, **kwargs: Any
) -> alarms_service.TickResult:
    """``alarms_service.tick``, the only way this file calls it."""
    _refuse_foreign_armed(session, made, token)
    return alarms_service.tick(session, **kwargs)


@contextmanager
def _client(settings: Settings) -> Iterator[TestClient]:
    """``owner_client``, with its application's connections given back when it closes.

    ``create_app`` builds a connection pool per runtime and nothing in the application
    disposes one: every client left about four connections open until the process ended,
    in a suite that already runs at the dev database's limit. The engines are found by
    listening for the ones that connect while the client is open, not by naming the
    runtimes, so a runtime added tomorrow is covered. The suite's shared identity runtime
    is not this file's to close.
    """
    connected: set[Engine] = set()

    def note(connection: Connection) -> None:
        connected.add(connection.engine)

    event.listen(Engine, "engine_connect", note)
    try:
        with owner_client(settings) as client:
            yield client
    finally:
        event.remove(Engine, "engine_connect", note)
        for engine in connected - {shared_identity(settings).engine}:
            engine.dispose()


def _tool_context(session: Session) -> ToolContext:
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={},
        db=session,
    )


def _far_future_trigger() -> dict[str, Any]:
    return {"at": "2099-01-01T07:30:00+03:00"}


# ---------------------------------------------------------------------- routines


def test_a_routine_is_written_at_the_longest_values_its_surface_allows(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """``routines_service.create_routine`` / ``pause_routine`` / ``resume_routine`` /
    ``cancel_routine``. ``name`` VARCHAR(200), ``source`` VARCHAR(32), ``source_ref``
    VARCHAR(256), the two reasons VARCHAR(500): each at exactly the length the REST surface
    allows (``source`` at what the column holds - the surface names no limit for it)."""
    name = _exactly(200, f"pgcov-{token} sabah rutini: ")
    source = _exactly(32, "pgcov-")
    source_ref = _exactly(256, f"pgcov-{token}:")
    reason = _exactly(500, f"{token} bu hafta tatildeyim: ")
    trigger = {"weekdays": [4, 0, 2], "time": "07:30", "timezone": "Europe/Istanbul"}
    conditions = [{"kind": "policy_permission", "detail": {"policy": "sabah.brifingi", **NESTED}}]
    actions = [
        {"kind": "voice_briefing", "detail": {"text": "Günaydın efendim; çayınız demlendi."}}
    ]
    with db() as session:
        routine = routines_service.create_routine(
            session,
            name=name,
            trigger_kind="schedule",
            trigger=trigger,
            conditions=conditions,
            actions=actions,
            source=source,
            source_ref=source_ref,
            detail_json=NESTED,
        )
        routine_id = routine.routine_id
        made.routines.append(routine_id)
        again = routines_service.create_routine(
            session,
            name="pgcov başka bir ad",
            trigger_kind="schedule",
            trigger=trigger,
            source=source,
            source_ref=source_ref,
        )
        assert again.routine_id == routine_id, "idempotent on (source, source_ref)"

    with db() as fresh:
        stored = fresh.get(Routine, routine_id)
        assert stored.name == name and len(stored.name) == 200
        assert stored.source == source and stored.source_ref == source_ref
        assert (stored.status, stored.trigger_kind) == ("armed", "schedule")
        assert stored.trigger_json == {
            "weekdays": [0, 2, 4],
            "time": "07:30",
            "timezone": "Europe/Istanbul",
            "grace_minutes": 5,
        }
        assert stored.conditions_json == conditions
        assert stored.actions_json == actions
        assert stored.detail_json == NESTED
        assert stored.armed_at.utcoffset() is not None
        assert stored.created_at.utcoffset() is not None
        # A NULL in every nullable column but armed_at, and the two server-side defaults.
        assert (
            stored.last_condition_at,
            stored.paused_at,
            stored.pause_reason,
            stored.cancelled_at,
            stored.cancel_reason,
        ) == (None,) * 5
        assert (stored.last_condition_met, stored.last_presence_sequence) == (False, 0)

        paused = routines_service.pause_routine(fresh, routine_id, reason=reason)
        assert paused.status == "paused"

    with db() as fresh:
        stored = fresh.get(Routine, routine_id)
        assert stored.pause_reason == reason and len(stored.pause_reason) == 500
        assert stored.paused_at.utcoffset() is not None
        # The subtraction SQLite could not do without help: a stored instant against now.
        resumed = routines_service.resume_routine(fresh, routine_id)
        assert resumed.status == "armed"

    with db() as fresh:
        stored = fresh.get(Routine, routine_id)
        assert (stored.paused_at, stored.pause_reason) == (None, None), "a value, then NULL"
        routines_service.cancel_routine(fresh, routine_id, reason=reason)

    with db() as fresh:
        stored = fresh.get(Routine, routine_id)
        assert stored.status == "cancelled"
        assert stored.cancel_reason == reason and len(stored.cancel_reason) == 500
        assert stored.cancelled_at.utcoffset() is not None

        # A routine cancelled without a word: the reason stays NULL.
        silent = routines_service.create_routine(
            fresh,
            name=f"pgcov-{token} sessiz",
            trigger_kind="at",
            trigger=_far_future_trigger(),
        )
        made.routines.append(silent.routine_id)
        routines_service.cancel_routine(fresh, silent.routine_id)
        fresh.expire_all()
        assert fresh.get(Routine, silent.routine_id).cancel_reason is None


def test_one_character_too_many_is_refused_by_the_routine_surface_not_by_postgres(
    settings: Settings, db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    body = {"name": f"pgcov-{token} REST", "trigger_kind": "at", "trigger": _far_future_trigger()}
    with _client(settings) as client:
        long_name = client.post(
            "/v1/routines", json={**body, "name": _exactly(201, f"pgcov-{token} ")}
        )
        assert long_name.status_code == 422, long_name.text
        long_ref = client.post(
            "/v1/routines", json={**body, "source_ref": _exactly(257, f"pgcov-{token}:")}
        )
        assert long_ref.status_code == 422, long_ref.text

        created = client.post(
            "/v1/routines",
            json={
                **body,
                "name": _exactly(200, f"pgcov-{token} "),
                "source_ref": _exactly(256, f"pgcov-{token}:"),
                "detail_json": NESTED,
            },
        )
        assert created.status_code == 201, created.text
        routine_id = created.json()["routine_id"]
        made.routines.append(uuid.UUID(routine_id))
        assert created.json()["detail_json"] == NESTED

        for step in ("pause", "cancel"):
            refused = client.post(
                f"/v1/routines/{routine_id}/{step}", json={"reason": _exactly(501, token)}
            )
            assert refused.status_code == 422, (step, refused.text)
        assert client.get(f"/v1/routines/{routine_id}").json()["status"] == "armed"

    with db() as fresh:
        count = fresh.scalar(
            select(func.count()).select_from(Routine).where(Routine.name.like(f"pgcov-{token}%"))
        )
        assert count == 1, "only the accepted routine was written"


@pytest.mark.xfail(
    strict=True,
    raises=DataError,
    reason=(
        "DEFECT (queued for the lead): POST /v1/routines answers 500 for a 33-character "
        "source - psycopg.errors.StringDataRightTruncation: value too long for type "
        "character varying(32)"
    ),
)
def test_a_source_longer_than_its_column_is_refused_by_the_surface_not_by_postgres(
    settings: Settings, made: SimpleNamespace, token: str
) -> None:
    """``POST /v1/routines`` bounds ``name`` and ``source_ref`` and says nothing about
    ``source``, which is VARCHAR(32)."""
    with _client(settings) as client:
        answer = client.post(
            "/v1/routines",
            json={
                "name": f"pgcov-{token} kaynak",
                "trigger_kind": "at",
                "trigger": _far_future_trigger(),
                "source": _exactly(33, "pgcov-"),
            },
        )
        assert answer.status_code == 422, answer.text


@pytest.mark.xfail(
    strict=True,
    raises=DataError,
    reason=(
        "DEFECT (queued for the lead): the routine.create voice tool raises for a "
        "201-character name - psycopg.errors.StringDataRightTruncation: value too long for "
        "type character varying(200)"
    ),
)
def test_a_spoken_routine_name_longer_than_its_column_is_refused_in_words(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """``routine.create`` by voice passes the model's ``name`` straight to the service; the
    column is VARCHAR(200). Either answer is the owner's to hear - a refusal in the tool's
    own words, or a routine whose name fits - but not a database error."""
    with db() as session:
        try:
            created = tools_routines.routine_create(
                _tool_context(session),
                {
                    "name": _exactly(201, f"pgcov-{token} her sabah haberleri oku ve "),
                    "trigger_kind": "at",
                    "trigger": _far_future_trigger(),
                },
            )
        except VoiceError:
            return
        made.routines.append(uuid.UUID(created["routine_id"]))
        assert len(created["name"]) <= 200


@pytest.mark.xfail(
    strict=True,
    raises=DataError,
    reason=(
        "DEFECT (queued for the lead): the routine.pause voice tool (and routine.cancel, the "
        "same code path) raises for a 501-character reason - "
        "psycopg.errors.StringDataRightTruncation: value too long for type character "
        "varying(500)"
    ),
)
def test_a_spoken_pause_reason_longer_than_its_column_is_refused_in_words(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """``routine.pause`` / ``routine.cancel`` by voice pass the model's ``reason`` straight
    to the service; both columns are VARCHAR(500)."""
    with db() as session:
        routine = routines_service.create_routine(
            session,
            name=f"pgcov-{token} sesle duraklat",
            trigger_kind="at",
            trigger=_far_future_trigger(),
            source="voice",
        )
        made.routines.append(routine.routine_id)
        try:
            paused = tools_routines.routine_pause(
                _tool_context(session),
                {
                    "routine_id": str(routine.routine_id),
                    "reason": _exactly(501, f"{token} sahibi tatilde: "),
                },
            )
        except VoiceError:
            return
        assert paused["status"] == "paused"
        session.expire_all()
        assert len(session.get(Routine, routine.routine_id).pause_reason) <= 500


# --------------------------------------------------------------- routine_firings


class _BrokenSpeaker:
    def dispatch(self, *, routine_id, firing_id, action, now=None):
        raise RuntimeError("hoparlör yanıt vermedi: çıkış aygıtı bulunamadı")


def test_routine_firings_record_a_fired_a_skipped_and_a_failed_occurrence(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """``routines_service.evaluate_due`` is the only writer. Three one-shot routines due at
    the same aware instant: one fires, one is skipped with its reason, one's action fails."""
    at = {"at": (MOMENT - timedelta(minutes=1)).isoformat()}
    briefing = [{"kind": "voice_briefing", "detail": {"text": "Günaydın; İstanbul'da hava açık."}}]
    with db() as session:
        fired = routines_service.create_routine(
            session,
            name=f"pgcov-{token} çalışan",
            trigger_kind="at",
            trigger=at,
            actions=briefing,
        )
        skipped = routines_service.create_routine(
            session,
            name=f"pgcov-{token} atlanan",
            trigger_kind="at",
            trigger=at,
            conditions=[{"kind": "quiet_hours", "detail": {}}],
            actions=briefing,
        )
        ids = {"fired": fired.routine_id, "skipped": skipped.routine_id}
        made.routines.extend(ids.values())

        result = _evaluate_due(
            session,
            made,
            token,
            now=MOMENT,
            context=RoutineConditionContext(),
            dispatcher=NoopDispatcher(),
        )
        outcomes = {o.routine_id: o for o in result.outcomes}
        assert outcomes[ids["fired"]].status == "triggered"
        assert outcomes[ids["skipped"]].status == "skipped"

        # The same instant again: the occurrence is resolved, the unique constraint holds.
        repeat = _evaluate_due(session, made, token, now=MOMENT, dispatcher=NoopDispatcher())
        assert not [o for o in repeat.outcomes if o.routine_id in ids.values()]

        failed = routines_service.create_routine(
            session,
            name=f"pgcov-{token} bozulan",
            trigger_kind="at",
            trigger=at,
            actions=briefing,
        )
        ids["failed"] = failed.routine_id
        made.routines.append(failed.routine_id)
        _evaluate_due(session, made, token, now=MOMENT, dispatcher=_BrokenSpeaker())

    with db() as fresh:
        rows = {
            name: routines_service.list_firings(fresh, routine_id)
            for name, routine_id in ids.items()
        }
        assert {name: len(found) for name, found in rows.items()} == {
            "fired": 1,
            "skipped": 1,
            "failed": 1,
        }
        for found in rows.values():
            assert isinstance(found[0], RoutineFiring)
            assert found[0].occurrence_key == "once"
            # The engine's own instant, stored as an instant: equal across the two zones.
            assert found[0].occurred_at == MOMENT
            assert found[0].occurred_at.utcoffset() is not None
            assert found[0].created_at.utcoffset() is not None

        triggered = rows["fired"][0]
        assert (triggered.status, triggered.dispatch_status) == ("triggered", "succeeded")
        assert triggered.skip_reason is None
        assert triggered.conditions_result == []
        assert triggered.actions_snapshot == briefing
        assert triggered.dispatch_results == [
            {
                "kind": "voice_briefing",
                "status": "succeeded",
                "ok": True,
                "reason": "",
                "detail": {"dispatched": False, "reason": "noop_dispatcher"},
            }
        ]

        skip = rows["skipped"][0]
        assert (skip.status, skip.skip_reason) == ("skipped", "quiet_hours:quiet_hours_unknown")
        assert skip.dispatch_status is None, "never dispatched is NULL, not 'none'"
        assert skip.actions_snapshot == [] and skip.dispatch_results == []
        assert skip.conditions_result == [
            {"kind": "quiet_hours", "passed": False, "reason": "quiet_hours_unknown"}
        ]

        broken = rows["failed"][0]
        assert (broken.status, broken.dispatch_status) == ("triggered", "failed")
        assert broken.dispatch_results[0]["reason"] == "dispatcher_exception:RuntimeError"
        assert broken.dispatch_results[0]["detail"] == {
            "error": "RuntimeError: hoparlör yanıt vermedi: çıkış aygıtı bulunamadı"
        }

        # A one-shot whose moment was decided is completed, whichever way it went.
        statuses = {fresh.get(Routine, routine_id).status for routine_id in ids.values()}
        assert statuses == {"completed"}


def test_a_condition_trigger_writes_its_edge_and_one_firing_per_crossing(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """The occurrence key of a condition trigger is the crossing instant itself, offset and
    all; ``routines.last_condition_at`` is the same instant, in a timestamptz."""
    idle = RoutineConditionContext(device_idle_s=900.0)
    with db() as session:
        routine = routines_service.create_routine(
            session,
            name=f"pgcov-{token} boşta kalınca",
            trigger_kind="condition",
            trigger={"kind": "device_idle", "min_seconds": 600},
        )
        routine_id = routine.routine_id
        made.routines.append(routine_id)

        def tick(moment: datetime, context: RoutineConditionContext) -> list[str]:
            result = _evaluate_due(
                session, made, token, now=moment, context=context, dispatcher=NoopDispatcher()
            )
            return [o.occurrence_key for o in result.outcomes if o.routine_id == routine_id]

        assert tick(MOMENT, idle) == [MOMENT.isoformat()]
        assert tick(MOMENT + timedelta(minutes=1), idle) == [], "still idle is not a crossing"
        assert tick(MOMENT + timedelta(minutes=2), RoutineConditionContext(device_idle_s=5.0)) == []

    with db() as fresh:
        stored = fresh.get(Routine, routine_id)
        assert stored.status == "armed"
        assert stored.last_condition_met is False, "the falling edge was written too"
        assert stored.last_condition_at == MOMENT
        assert stored.last_condition_at.utcoffset() is not None
        firings = routines_service.list_firings(fresh, routine_id)
        assert [f.occurrence_key for f in firings] == ["2001-09-09T04:46:40+03:00"]
        assert firings[0].dispatch_status == "none", "triggered, with nothing to dispatch"


def test_the_engine_is_never_run_over_an_armed_routine_that_is_not_the_tests_own(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """A ``now`` in 2001 protects nobody's weekday alarm: ``check_schedule_due`` compares the
    weekday and the wall clock and never the year. So ``_evaluate_due`` and ``_tick`` refuse
    to start while an armed routine exists that the calling test did not create - here, the
    same routine seen from a test that does not own it - and nothing is written for it."""
    wednesday = datetime(2001, 9, 12, 7, 30, 20, tzinfo=ISTANBUL)
    stranger = SimpleNamespace(routines=[], alarms=[])
    not_mine = uuid.uuid4().hex[:10]
    with db() as session:
        routine = routines_service.create_routine(
            session,
            name=f"pgcov-{token} başkasının hafta içi alarmı",
            trigger_kind="schedule",
            trigger={"weekdays": [2], "time": "07:30", "timezone": "Europe/Istanbul"},
        )
        routine_id = routine.routine_id
        made.routines.append(routine_id)

        with pytest.raises(pytest.fail.Exception, match=str(routine_id)):
            _evaluate_due(session, stranger, not_mine, now=wednesday, dispatcher=NoopDispatcher())
        with pytest.raises(pytest.fail.Exception, match=str(routine_id)):
            _tick(session, stranger, not_mine, now=wednesday)
        assert routines_service.list_firings(session, routine_id) == []

        # Why the guard exists: the test that DOES own it fires it, at an instant in 2001.
        result = _evaluate_due(session, made, token, now=wednesday, dispatcher=NoopDispatcher())
        assert [(o.status, o.occurrence_key) for o in result.outcomes] == [
            ("triggered", "2001-09-12")
        ]


@pytest.mark.xfail(
    strict=True,
    raises=DataError,
    reason=(
        "DEFECT (queued for the lead): evaluate_due raises out of the tick when the joined "
        "skip reason passes 500 characters, on the INSERT into routine_firings - "
        "psycopg.errors.StringDataRightTruncation: value too long for type character "
        "varying(500)"
    ),
)
def test_a_skip_with_many_unmet_conditions_is_still_recorded(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """``skip_reason`` names EVERY unmet condition, joined; the column is VARCHAR(500) and
    nothing bounds the join. A skip must be recorded - "never silently dropped" - and one
    routine's reason must not be what stops the tick for every routine after it."""
    conditions = [
        {
            "kind": "policy_permission",
            "detail": {"policy": f"ev.otomasyonu.{n:02d}.gece_modunda_sesli_bildirim_izni"},
        }
        for n in range(8)
    ]
    _, verdicts = conditions_mod.evaluate_conditions(conditions, RoutineConditionContext())
    assert len("; ".join(f"{v['kind']}:{v['reason']}" for v in verdicts)) > 500

    with db() as session:
        routine = routines_service.create_routine(
            session,
            name=f"pgcov-{token} sekiz koşullu",
            trigger_kind="at",
            trigger={"at": (MOMENT - timedelta(minutes=1)).isoformat()},
            conditions=conditions,
        )
        routine_id = routine.routine_id
        made.routines.append(routine_id)
        result = _evaluate_due(
            session,
            made,
            token,
            now=MOMENT,
            context=RoutineConditionContext(),
            dispatcher=NoopDispatcher(),
        )
        assert [o.status for o in result.outcomes if o.routine_id == routine_id] == ["skipped"]

    with db() as fresh:
        firings = routines_service.list_firings(fresh, routine_id)
        assert [f.status for f in firings] == ["skipped"]
        assert len(firings[0].conditions_result) == 8
        assert firings[0].skip_reason


# -------------------------------------------------------------------- wake_alarms


#: ``alarms:<36-character id>:cleaned_up:<10-digit instant>:<snooze count>:<reason>`` in a
#: VARCHAR(256): 68 characters before the reason, 188 left for it.
LONGEST_CANCEL_REASON_THAT_WORKS = 188


def _when(moment: datetime, weekdays: tuple[int, ...] = ()) -> ParsedWhen:
    return ParsedWhen(
        at=moment,
        local_time=moment.strftime("%H:%M"),
        timezone="Europe/Istanbul",
        weekdays=weekdays,
    )


def test_wake_alarms_are_written_with_their_documents_and_a_null_everywhere_else(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """``alarms_service.create_alarm``: ``label`` VARCHAR(200) at the length the REST surface
    and the voice tool both allow, four JSONB documents with Turkish text, an aware
    ``scheduled_for`` that is not UTC, and every column the alarm has not reached yet NULL."""
    ring_at = datetime(2001, 9, 10, 7, 30, tzinfo=ISTANBUL)
    label = _exactly(200, f"pgcov-{token} sabah: ")
    media = {"url": "https://www.youtube.com/watch?v=pgcov", "title": "Güneş Doğarken — şarkı"}
    with db() as session:
        alarm = alarms_service.create_alarm(
            session,
            when=_when(ring_at),
            media=media,
            label=label,
            greeting_policy={"enabled": True, "text": "Günaydın efendim, çay hazır."},
            display_wake_policy={"enabled": False, "not": {"oda": "çalışma odası"}},
            snooze_minutes=7,
        )
        alarm_id = alarm.id
        made.alarms.append(alarm_id)
        recurring = alarms_service.create_alarm(
            session, when=_when(ring_at, weekdays=(0, 1, 2, 3, 4)), is_test=True
        )
        recurring_id = recurring.id
        made.alarms.append(recurring_id)

    with db() as fresh:
        stored = fresh.get(WakeAlarm, alarm_id)
        assert stored.label == label and len(stored.label) == 200
        assert stored.scheduled_for == ring_at and stored.scheduled_for.utcoffset() is not None
        assert (stored.local_time, stored.timezone) == ("07:30", "Europe/Istanbul")
        assert (stored.state, stored.owner_id, stored.is_test) == ("SCHEDULED", "owner", False)
        assert stored.media_source == {"kind": "youtube", **media}
        assert stored.resolved_media_identity == {"kind": "youtube", **media}
        assert stored.greeting_policy["text"] == "Günaydın efendim, çay hazır."
        assert stored.display_wake_policy["not"] == {"oda": "çalışma odası"}
        assert stored.display_wake_policy["enabled"] is False
        assert (stored.snooze_minutes, stored.snooze_count, stored.max_play_seconds) == (7, 0, 600)
        assert stored.detail_json == {}
        assert stored.created_at.utcoffset() is not None
        assert (
            stored.device_id,
            stored.recurrence,
            stored.armed_at,
            stored.triggered_at,
            stored.terminal_at,
            stored.terminal_state,
            stored.terminal_reason,
            stored.last_firing_id,
            stored.media_session_id,
            stored.media_kind,
            stored.greeting_due_at,
            stored.greeted_at,
            stored.playing_since,
        ) == (None,) * 13

        # The trigger is a first-class routine, written by the same call.
        routine = fresh.get(Routine, stored.routine_id)
        assert (routine.name, routine.source, routine.trigger_kind) == (
            "Alarm 07:30",
            "alarm",
            "at",
        )
        assert routine.source_ref == f"alarm:{alarm_id}:0"
        assert routine.trigger_json == {"at": "2001-09-10T04:30:00+00:00"}
        assert routine.actions_json == [
            {"kind": "wake_alarm", "detail": {"alarm_id": str(alarm_id)}}
        ]

        every_weekday = fresh.get(WakeAlarm, recurring_id)
        assert every_weekday.label is None
        assert every_weekday.recurrence == {"weekdays": [0, 1, 2, 3, 4]}
        assert every_weekday.media_source == {"kind": "tone"}
        assert (every_weekday.is_test, every_weekday.max_play_seconds) == (True, 120)
        schedule = fresh.get(Routine, every_weekday.routine_id)
        assert (schedule.name, schedule.trigger_kind) == ("Test alarmı 07:30", "schedule")
        assert schedule.trigger_json["timezone"] == "Europe/Istanbul"


def test_a_wake_alarm_rings_is_snoozed_and_is_cancelled_on_postgres(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """``reconcile_local_fired`` -> ``snooze_alarm`` -> ``cancel_alarm``: every column the
    lifecycle writes, set and then NULL again, with instants the device chose in its own
    zone.

    ``terminal_reason`` is VARCHAR(200) and is proven here at 188 characters, not 200: 188 is
    the longest reason a cancel survives today (the cleanup row's ``source_ref`` spends 68
    of its 256 characters before the reason starts). 189 to 200 is the strict-xfail test
    further down; no passing test stores more than 188."""
    ring_at = datetime(2001, 9, 10, 7, 30, tzinfo=ISTANBUL)
    with db() as session:
        alarm = alarms_service.create_alarm(
            session, when=_when(ring_at), label=f"pgcov-{token} yaşam döngüsü"
        )
        alarm_id = alarm.id
        made.alarms.append(alarm_id)
        first_routine = alarm.routine_id

        rang = alarms_service.reconcile_local_fired(session, [str(alarm_id)], now=ring_at)
        assert [a.id for a in rang] == [alarm_id]

    with db() as fresh:
        stored = fresh.get(WakeAlarm, alarm_id)
        assert (stored.state, stored.media_kind) == ("PLAYING", "local_fallback")
        assert stored.triggered_at == ring_at and stored.playing_since == ring_at
        assert stored.playing_since.utcoffset() is not None

        until = ring_at + timedelta(minutes=10)
        alarms_service.snooze_alarm(
            fresh, alarm_id, now=ring_at + timedelta(minutes=1), resume_at=until
        )

    with db() as fresh:
        stored = fresh.get(WakeAlarm, alarm_id)
        assert (stored.state, stored.snooze_count, stored.local_time) == ("SCHEDULED", 1, "07:40")
        assert stored.scheduled_for == until, "the device's instant, not one derived from now"
        assert (
            stored.media_kind,
            stored.playing_since,
            stored.triggered_at,
            stored.armed_at,
            stored.last_firing_id,
        ) == (None,) * 5
        assert stored.routine_id != first_routine
        assert fresh.get(Routine, stored.routine_id).source_ref == f"alarm:{alarm_id}:snooze:1"

        reason = _exactly(LONGEST_CANCEL_REASON_THAT_WORKS, f"{token} sahibi çoktan uyandı: ")
        alarms_service.cancel_alarm(
            fresh, alarm_id, reason=reason, now=ring_at + timedelta(minutes=2)
        )

    with db() as fresh:
        stored = fresh.get(WakeAlarm, alarm_id)
        assert (stored.state, stored.terminal_state) == ("CANCELLED", "CANCELLED")
        assert stored.terminal_reason == reason and len(stored.terminal_reason) == 188
        # The row the 189-character cancel never gets to write.
        story = alarm_history.alarm_history(fresh, alarm_id=alarm_id)
        assert EVENT_TYPE_ALARM_CLEANED_UP in [entry["event_type"] for entry in story], story
        assert stored.terminal_at == ring_at + timedelta(minutes=2)
        assert stored.terminal_at.utcoffset() is not None
        trigger = fresh.get(Routine, stored.routine_id)
        assert (trigger.status, trigger.cancel_reason) == ("cancelled", "alarm_cancelled")


class _NoBriefing:
    def narrate(self, *, text, routine_id, firing_id, briefing_ids=()):
        raise AssertionError("a wake alarm says nothing through the briefing port")


def test_the_cloud_rings_a_wake_alarm_through_the_routine_engine_on_postgres(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """The path the device-local test above never takes: ``alarms_service.tick`` arms the
    alarm, ``routines_service.evaluate_due`` fires its routine, the production dispatcher
    hands the ``wake_alarm`` action to ``WakeAlarmRunner`` -> ``fire_alarm`` ->
    ``WakeSequence.fire`` (in its own session, as in production), a later tick speaks the
    greeting and ``stop_alarm`` ends it. Only the device and the voice are fakes: every row
    is written by the production objects ``app.main`` wires. These are the columns that path
    alone writes: ``armed_at``, ``last_firing_id``, ``media_session_id``,
    ``greeting_due_at``, ``greeted_at``."""
    ring_at = datetime(2001, 9, 12, 7, 30, tzinfo=ISTANBUL)
    armed_at = ring_at - timedelta(hours=1)
    fired_at = ring_at + timedelta(seconds=20)
    greeted_at = fired_at + timedelta(seconds=30)
    device = FakeDeviceAction(results=happy_device_results())
    sequence = WakeSequence(
        device_action=device,
        # A provider that "really speaks": the plain fake is refused as a synthetic tone.
        tts=FakeTTSProvider(synthetic_speech=False),
        audio_store=AudioStore(),
    )
    dispatcher = ActionDispatcher(
        briefing=_NoBriefing(),
        device_action=device,
        wake_alarm=WakeAlarmRunner(session_factory=db, sequence=sequence),
    )
    with db() as session:
        alarm = alarms_service.create_alarm(
            session,
            when=_when(ring_at),
            media={"url": "https://www.youtube.com/watch?v=pgcov", "title": "Güneş Doğarken"},
            label=f"pgcov-{token} bulut çaldırır",
            greeting_policy={"enabled": True, "text": "Günaydın efendim, çayınız demlendi."},
        )
        alarm_id, routine_id = alarm.id, alarm.routine_id
        made.alarms.append(alarm_id)
        _tick(session, made, token, sequence=sequence, now=armed_at)

    with db() as fresh:
        stored = fresh.get(WakeAlarm, alarm_id)
        assert stored.state == "ARMED"
        assert stored.armed_at == armed_at and stored.armed_at.utcoffset() is not None
        assert device.payload_for("desktop.alarm_arm")["fire_at"] == "2001-09-12T04:30:00Z"

        result = _evaluate_due(fresh, made, token, now=fired_at, dispatcher=dispatcher)
        assert [o.status for o in result.outcomes if o.routine_id == routine_id] == ["triggered"]

    with db() as fresh:
        firing = routines_service.list_firings(fresh, routine_id)[0]
        assert firing.dispatch_status == "succeeded", firing.dispatch_results
        assert firing.dispatch_results[0]["detail"]["state"] == "PLAYING"

        stored = fresh.get(WakeAlarm, alarm_id)
        assert (stored.state, stored.media_kind) == ("PLAYING", "youtube")
        assert stored.last_firing_id == firing.firing_id
        # The longest value production writes here: "alarm-" and the id, 42 of VARCHAR(128).
        assert stored.media_session_id == media_session_id(alarm_id)
        assert stored.triggered_at == fired_at and stored.playing_since == fired_at
        assert stored.greeting_due_at == fired_at + timedelta(seconds=22)
        assert stored.greeting_due_at.utcoffset() is not None
        assert stored.greeted_at is None
        assert "media_failure_reason" not in stored.detail_json

        assert _tick(fresh, made, token, sequence=sequence, now=greeted_at).greeted == 1

    with db() as fresh:
        stored = fresh.get(WakeAlarm, alarm_id)
        assert stored.state == "PLAYING"
        assert stored.greeted_at == greeted_at and stored.greeted_at.utcoffset() is not None
        assert stored.greeting_due_at is None, "a value, then NULL"
        assert "greeting_failure" not in stored.detail_json
        assert device.count("desktop.play_audio") == 1

        alarms_service.stop_alarm(
            fresh, alarm_id, sequence=sequence, now=greeted_at + timedelta(minutes=1)
        )

    with db() as fresh:
        stored = fresh.get(WakeAlarm, alarm_id)
        assert (stored.state, stored.terminal_state) == ("STOPPED", "STOPPED")
        assert stored.terminal_reason == "owner"
        assert stored.media_session_id is None, "the media session is released"
        assert stored.last_firing_id == firing.firing_id
        assert device.count("browser.media_stop") == 1
        assert fresh.get(Routine, routine_id).status == "completed"


def test_one_character_too_many_is_refused_by_the_alarm_surface_not_by_postgres(
    settings: Settings, db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    when = {"date": "2099-01-01", "time": "07:30"}
    with _client(settings) as client:
        long_label = client.post(
            "/v1/alarms", json={"when": when, "label": _exactly(201, f"pgcov-{token} ")}
        )
        assert long_label.status_code == 422, long_label.text
        long_zone = client.post("/v1/alarms", json={"when": when, "timezone": "Europe/" + "x" * 58})
        assert long_zone.status_code == 422, long_zone.text
        # Refused for its LENGTH (65 > 64), before anything asks whether the zone exists.
        assert [(e["type"], e["loc"]) for e in long_zone.json()["detail"]] == [
            ("string_too_long", ["body", "timezone"])
        ]

        created = client.post(
            "/v1/alarms",
            json={"when": when, "test": True, "label": _exactly(200, f"pgcov-{token} ")},
        )
        assert created.status_code == 201, created.text
        alarm_id = uuid.UUID(created.json()["alarm_id"])
        made.alarms.append(alarm_id)

        long_reason = client.post(
            f"/v1/alarms/{alarm_id}/cancel", json={"reason": _exactly(201, token)}
        )
        assert long_reason.status_code == 422, long_reason.text
        assert client.get(f"/v1/alarms/{alarm_id}").json()["state"] == "SCHEDULED"

    with db() as fresh:
        count = fresh.scalar(
            select(func.count())
            .select_from(WakeAlarm)
            .where(WakeAlarm.label.like(f"pgcov-{token}%"))
        )
        assert count == 1, "only the accepted alarm was written"


@pytest.mark.xfail(
    strict=True,
    raises=PendingRollbackError,
    reason=(
        "DEFECT (queued for the lead): cancel_alarm raises for a reason of 189 characters or "
        "more (the surface allows 200). The alarm.cleaned_up ledger row's source_ref carries "
        "the reason and is VARCHAR(256) - psycopg.errors.StringDataRightTruncation: value too "
        "long for type character varying(256) - and the handler that swallows that then reads "
        "alarm.id on the rolled-back session: sqlalchemy.exc.PendingRollbackError. The alarm "
        "is already CANCELLED; the caller gets a 500 and the cleanup row is never written"
    ),
)
def test_a_cancel_at_the_longest_reason_the_surface_allows_keeps_its_cleanup_record(
    db: sessionmaker[Session], made: SimpleNamespace, token: str
) -> None:
    """``POST /v1/alarms/{id}/cancel`` allows a 200-character reason. The alarm's story is
    read back from the ledger (``app.alarms.history``), and "cleaned up" is the row that says
    the device was released: it must be there for the longest reason the surface accepts."""
    ring_at = datetime(2001, 9, 11, 7, 30, tzinfo=ISTANBUL)
    reason = _exactly(200, f"{token} iptal: ")
    with db() as session:
        alarm = alarms_service.create_alarm(session, when=_when(ring_at))
        alarm_id = alarm.id
        made.alarms.append(alarm_id)
        cancelled = alarms_service.cancel_alarm(session, alarm_id, reason=reason, now=ring_at)
        assert cancelled.state == "CANCELLED"

    with db() as fresh:
        assert fresh.get(WakeAlarm, alarm_id).terminal_reason == reason
        story = alarm_history.alarm_history(fresh, alarm_id=alarm_id)
        assert EVENT_TYPE_ALARM_CLEANED_UP in [entry["event_type"] for entry in story], story


# --------------------------------------------------- what PostgreSQL refuses alone


@pytest.mark.xfail(
    strict=True,
    raises=DataError,
    reason=(
        "DEFECT (queued for the lead): POST /v1/routines answers 500 for a U+0000 in the "
        "name - psycopg.DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes"
    ),
)
def test_a_nul_character_in_a_routine_is_refused_by_the_surface_not_by_postgres(
    settings: Settings, made: SimpleNamespace, token: str
) -> None:
    """PostgreSQL stores no U+0000, in a VARCHAR or inside JSONB; SQLite stores both. JSON
    allows the character, so the request validates and the database is what says no.
    Refusing it and storing the name without it are both answers; a 500 is not."""
    with _client(settings) as client:
        answer = client.post(
            "/v1/routines",
            json={
                "name": f"pgcov-{token} sıfır\x00bayt",
                "trigger_kind": "at",
                "trigger": _far_future_trigger(),
            },
        )
        if answer.status_code == 201:
            made.routines.append(uuid.UUID(answer.json()["routine_id"]))
            assert "\x00" not in answer.json()["name"]
        else:
            assert answer.status_code == 422, answer.text


@pytest.mark.xfail(
    strict=True,
    raises=DataError,
    reason=(
        "DEFECT (queued for the lead): POST /v1/alarms answers 500 for a U+0000 in the "
        "label, on the INSERT into wake_alarms - psycopg.DataError: PostgreSQL text fields "
        "cannot contain NUL (0x00) bytes"
    ),
)
def test_a_nul_character_in_an_alarm_label_is_refused_by_the_surface_not_by_postgres(
    settings: Settings, made: SimpleNamespace, token: str
) -> None:
    """The same character through ``POST /v1/alarms``: ``label`` is bounded at 200
    characters and nothing says which characters."""
    with _client(settings) as client:
        answer = client.post(
            "/v1/alarms",
            json={
                "when": {"date": "2099-01-01", "time": "07:30"},
                "test": True,
                "label": f"pgcov-{token} sıfır\x00bayt",
            },
        )
        if answer.status_code == 201:
            made.alarms.append(uuid.UUID(answer.json()["alarm_id"]))
            assert "\x00" not in (answer.json()["label"] or "")
        else:
            assert answer.status_code == 422, answer.text
