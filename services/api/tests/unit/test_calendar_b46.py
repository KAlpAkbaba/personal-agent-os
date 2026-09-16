"""B46 - the calendar that works once an account exists (docs/DECISIONS.md ADR-0153).

* recurrence and reminders written as the owner heard them, and read back by the same
  parser the providers use (req 356, 357);
* a VALARM's properties never leak into the event;
* a recurring event is never moved or cancelled one occurrence at a time;
* cancel under the owner's policy (req 354): refuse by default, a proposal under confirm;
* the index written by agenda reads and by the clock's mirror, stale rows removed, and a
  truncated answer never taken as a deletion (req 359, 361);
* reminders raised once, not held back by quiet hours (req 358);
* the owner's words reach the proposal even when the model drops them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.confirmation_gate import CONFIRM_SOURCE_VOICE, Confirmation
from app.briefing.models import BriefingPreferencesRow
from app.briefing.service import BriefingService
from app.calendar import tr_time
from app.calendar.ics import (
    MAX_REMINDER_MINUTES,
    RRuleError,
    build_vevent,
    expand_events,
    parse_calendar,
    parse_trigger_minutes,
    validate_rrule,
)
from app.calendar.models import CalendarIndexRow, CalendarProposalRow
from app.calendar.providers import FakeCalendarProvider
from app.calendar.service import CalendarService
from app.calendar.syncer import MIN_SYNC_INTERVAL_S, CalendarSyncer
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.location.models import LocationContextRow
from app.news.models import NewsResolutionRow, NewsSourceRow
from app.notifications import events as notification_events
from app.notifications.models import NotificationRow
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_EVENT, ObjectFocusRow
from app.voice.intents import Intent, resolve_intent
from app.weather.models import WeatherQueryEvidenceRow
from tests.mail_calendar_support import (
    build_fake_calendar_provider,
    build_fake_calendar_writer,
)

TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=TZ)
NIGHT = datetime(2026, 9, 13, 23, 30, tzinfo=UTC)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        CalendarIndexRow.__table__,
        CalendarProposalRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
        NotificationRow.__table__,
        BriefingPreferencesRow.__table__,
        LocationContextRow.__table__,
        WeatherQueryEvidenceRow.__table__,
        NewsSourceRow.__table__,
        NewsResolutionRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _service(**kwargs) -> CalendarService:
    return CalendarService(build_fake_calendar_provider(), build_fake_calendar_writer(), **kwargs)


def _confirm(turn: int = 2) -> Confirmation:
    return Confirmation(
        source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=turn, owner_intent_ok=True
    )


# ------------------------------------------------------------------ writing a rule (356)


@pytest.mark.parametrize(
    ("rule", "canonical"),
    [
        ("FREQ=WEEKLY;BYDAY=FR,MO", "FREQ=WEEKLY;BYDAY=MO,FR"),
        ("rrule:freq=daily;interval=1", "FREQ=DAILY"),
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=FR", "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR"),
        ("FREQ=MONTHLY;COUNT=12", "FREQ=MONTHLY;COUNT=12"),
        ("FREQ=YEARLY;UNTIL=20300101T000000Z", "FREQ=YEARLY;UNTIL=20300101T000000Z"),
    ],
)
def test_a_rule_the_owner_can_say_is_canonicalised(rule: str, canonical: str) -> None:
    assert validate_rrule(rule) == canonical


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=HOURLY",
        "FREQ=DAILY;BYDAY=MO",
        "FREQ=WEEKLY;COUNT=3;UNTIL=20300101T000000Z",
        "FREQ=WEEKLY;BYSETPOS=1",
        "FREQ=WEEKLY;BYDAY=XX",
        "FREQ=WEEKLY;INTERVAL=0",
        "garbage",
        "",
    ],
)
def test_a_rule_nobody_could_hear_read_back_is_refused(rule: str) -> None:
    with pytest.raises(RRuleError):
        validate_rrule(rule)


def test_what_the_writer_sends_the_reader_reads_back_as_the_same_series() -> None:
    body = build_vevent(
        uid="ev-r@fixture.example",
        summary="Stand-up",
        start=datetime(2026, 9, 14, 9, 0, tzinfo=TZ),
        end=datetime(2026, 9, 14, 9, 15, tzinfo=TZ),
        rrule="FREQ=WEEKLY;BYDAY=FR,MO",
        reminder_minutes=10,
    )
    [event] = parse_calendar(body)
    assert event.rrule == "FREQ=WEEKLY;BYDAY=MO,FR"
    assert event.alarms == (10,)
    occurrences = expand_events(
        [event],
        start=datetime(2026, 9, 14, 0, 0, tzinfo=TZ),
        end=datetime(2026, 9, 28, 0, 0, tzinfo=TZ),
    )
    assert len(occurrences) == 4
    assert {o.start.astimezone(TZ).weekday() for o in occurrences} == {0, 4}
    assert all(o.recurring and o.reminders == (10,) for o in occurrences)


def test_a_reminder_out_of_range_is_never_written() -> None:
    with pytest.raises(RRuleError):
        build_vevent(
            uid="x",
            summary="x",
            start=NOW,
            end=NOW + timedelta(hours=1),
            reminder_minutes=MAX_REMINDER_MINUTES + 1,
        )


# ------------------------------------------------------------------- reading an alarm (357)


def test_a_valarm_summary_never_becomes_the_events_title() -> None:
    text = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "BEGIN:VEVENT",
            "UID:ev-leak@fixture.example",
            "DTSTART:20260914T070000Z",
            "DTEND:20260914T080000Z",
            "SUMMARY:Gerçek başlık",
            "BEGIN:VALARM",
            "ACTION:EMAIL",
            "SUMMARY:Alarm e-postasının başlığı",
            "DESCRIPTION:hatırlatma",
            "TRIGGER:-PT30M",
            "END:VALARM",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )
    [event] = parse_calendar(text)
    assert event.summary == "Gerçek başlık"
    assert event.alarms == (30,)
    [occ] = expand_events(
        [event],
        start=datetime(2026, 9, 14, 0, 0, tzinfo=UTC),
        end=datetime(2026, 9, 15, 0, 0, tzinfo=UTC),
    )
    assert occ.reminders == (30,) and occ.recurring is False


@pytest.mark.parametrize(
    ("value", "params", "minutes"),
    [
        ("-PT15M", {}, 15),
        ("-PT1H", {}, 60),
        ("-P1D", {}, 1440),
        ("-PT1H30M", {}, 90),
        ("PT0S", {}, 0),
        ("PT15M", {}, None),
        ("-PT15M", {"RELATED": "END"}, None),
        ("20260914T063000Z", {"VALUE": "DATE-TIME"}, None),
        ("-P8D", {}, None),
    ],
)
def test_a_trigger_becomes_minutes_before_the_start_or_nothing(
    value: str, params: dict[str, str], minutes: int | None
) -> None:
    assert parse_trigger_minutes(value, params) == minutes


# ------------------------------------------------------------------- the owner's words


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Her gün 9'da", "FREQ=DAILY"),
        ("Her hafta pazartesi 10'da", "FREQ=WEEKLY;BYDAY=MO"),
        ("Her pazartesi ve çarşamba", "FREQ=WEEKLY;BYDAY=MO,WE"),
        ("Hafta içi her gün 9'da", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("İki haftada bir cuma", "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR"),
        ("Her ay ilk gün", "FREQ=MONTHLY"),
        ("Her yıl", "FREQ=YEARLY"),
        ("Pazartesileri 8'de", "FREQ=WEEKLY;BYDAY=MO"),
        ("Her cumartesi", "FREQ=WEEKLY;BYDAY=SA"),
        ("Her pazar", "FREQ=WEEKLY;BYDAY=SU"),
        ("Her gün, 10 kez", "FREQ=DAILY;COUNT=10"),
        ("Perşembe 15'e", None),
        ("Yarın sabah", None),
    ],
)
def test_recurrence_words_become_the_rule_and_a_day_name_alone_does_not(
    text: str, rule: str | None
) -> None:
    assert tr_time.extract_recurrence(text) == rule


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("15 dakika önce hatırlat", 15),
        ("bir saat önce hatırlat", 60),
        ("yarım saat önce haber ver", 30),
        ("bir gün önce hatırlat", 1440),
        ("2 saat önce hatirlat", 120),
        ("başlarken hatırlat", 0),
        ("bana hatırlat", tr_time.DEFAULT_REMINDER_MINUTES),
        ("Perşembe 15'e diş hekimi", None),
    ],
)
def test_reminder_words_become_minutes_before(text: str, minutes: int | None) -> None:
    assert tr_time.extract_reminder_minutes(text) == minutes


def test_a_reminder_clause_is_neither_the_clock_nor_the_duration() -> None:
    timing = tr_time.without_reminder("Yarın 15'e, 15 dakika önce hatırlat")
    assert tr_time.extract_duration_minutes(timing) is None
    assert tr_time.extract_clock(timing) == (15, 0)
    assert tr_time.extract_duration_minutes(tr_time.without_reminder("bir saat önce")) is None


# ------------------------------------------------------------------- proposals (356, 357)


def test_a_recurring_reminder_proposal_is_read_back_and_written_as_said(db) -> None:
    service = _service()
    start = datetime(2026, 9, 14, 10, 0, tzinfo=TZ)
    prepared = service.propose(
        db,
        summary="Ekip toplantısı",
        start=start,
        end=start + timedelta(hours=1),
        rrule="FREQ=WEEKLY;BYDAY=MO",
        reminder_minutes=15,
        session_id="sess-1",
    )
    assert prepared["execution_status"] == "executed", prepared
    assert "her hafta pazartesi" in prepared["speech"]
    assert "15 dakika önce hatırlatarak" in prepared["speech"]
    service.read_proposal(db, session_id="sess-1", turn=1)
    committed = service.commit(
        db, host_flag_enabled=True, session_id="sess-1", confirmation=_confirm()
    )
    assert committed["execution_status"] == "executed", committed
    [sent] = service._writer.created  # type: ignore[attr-defined]
    assert sent.rrule == "FREQ=WEEKLY;BYDAY=MO" and sent.reminder_minutes == 15
    body = build_vevent(
        uid="ev-sent@fixture.example",
        summary=sent.summary,
        start=sent.start,
        end=sent.end,
        rrule=sent.rrule,
        reminder_minutes=sent.reminder_minutes,
    )
    [event] = parse_calendar(body)
    assert event.rrule == sent.rrule and event.alarms == (15,)


def test_a_rule_the_writer_would_refuse_is_refused_before_the_owner_hears_it(db) -> None:
    service = _service()
    result = service.propose(
        db,
        summary="x",
        start=NOW,
        end=NOW + timedelta(hours=1),
        rrule="FREQ=HOURLY",
        session_id="sess-1",
    )
    assert result["error_class"] == "invalid_recurrence"
    assert db.execute(select(CalendarProposalRow)).first() is None


def test_one_occurrence_of_a_recurring_event_is_never_moved_alone(db) -> None:
    service = _service()
    refused = service.propose_reschedule(db, minutes_delta=60, event_uid="ev-ekip@fixture.example")
    assert refused["error_class"] == "recurring_series"
    assert db.execute(select(CalendarProposalRow)).first() is None
    moved = service.propose_reschedule(db, minutes_delta=60, event_uid="ev-dis@fixture.example")
    assert moved["execution_status"] == "executed"


# --------------------------------------------------------------------------- cancel (354)


def test_by_default_a_cancel_is_still_an_honest_refusal(db) -> None:
    service = _service()
    focus_module.set_focus(
        db, FOCUS_KIND_EVENT, "ev-dis@fixture.example", label="Diş hekimi", source="test"
    )
    result = service.cancel_event(db, session_id="sess-1")
    assert result["error_class"] == "deletion_not_permitted"
    assert service._writer.deleted == []  # type: ignore[attr-defined]
    assert db.execute(select(CalendarProposalRow)).first() is None


def test_under_the_confirm_policy_a_cancel_is_a_proposal_then_one_delete(db) -> None:
    service = _service(cancel_policy="confirm")
    service.agenda(
        db,
        start=datetime(2026, 9, 10, 0, 0, tzinfo=TZ),
        end=datetime(2026, 9, 11, 0, 0, tzinfo=TZ),
    )
    assert db.execute(
        select(CalendarIndexRow).where(CalendarIndexRow.uid == "ev-dis@fixture.example")
    ).first()
    focus_module.set_focus(
        db, FOCUS_KIND_EVENT, "ev-dis@fixture.example", label="Diş hekimi", source="test"
    )
    prepared = service.cancel_event(db, session_id="sess-1")
    assert prepared["execution_status"] == "executed", prepared
    assert "silmeyi öneriyorum" in prepared["speech"]
    assert service._writer.deleted == []  # type: ignore[attr-defined]
    # Nothing is deleted without the read-back and the owner's next-turn confirmation.
    early = service.commit(db, host_flag_enabled=True, session_id="sess-1", confirmation=_confirm())
    assert early["execution_status"] == "refused"
    service.read_proposal(db, session_id="sess-1", turn=1)
    done = service.commit(db, host_flag_enabled=True, session_id="sess-1", confirmation=_confirm())
    assert done["execution_status"] == "executed", done
    assert done["speech"] == "Takvimden sildim efendim."
    assert service._writer.deleted == ["ev-dis@fixture.example"]  # type: ignore[attr-defined]
    assert (
        db.execute(
            select(CalendarIndexRow).where(CalendarIndexRow.uid == "ev-dis@fixture.example")
        ).first()
        is None
    )


def test_a_recurring_series_is_never_cancelled_by_one_sentence(db) -> None:
    service = _service(cancel_policy="confirm")
    focus_module.set_focus(
        db, FOCUS_KIND_EVENT, "ev-ekip@fixture.example", label="Ekip", source="test"
    )
    result = service.cancel_event(db, session_id="sess-1")
    assert result["error_class"] == "recurring_series"
    assert db.execute(select(CalendarProposalRow)).first() is None


def test_an_unknown_policy_word_keeps_the_refusal(db) -> None:
    service = _service(cancel_policy="yes please")
    focus_module.set_focus(
        db, FOCUS_KIND_EVENT, "ev-dis@fixture.example", label="Diş hekimi", source="test"
    )
    assert service.cancel_event(db, session_id="sess-1")["error_class"] == "deletion_not_permitted"


# ---------------------------------------------------------------------- index + sync (359, 361)


def test_an_agenda_read_indexes_what_the_owner_heard_once(db) -> None:
    service = _service()
    start, end = NOW.replace(hour=0), NOW.replace(hour=0) + timedelta(days=1)
    first = service.agenda(db, start=start, end=end)
    rows = db.execute(select(CalendarIndexRow)).scalars().all()
    assert len(rows) == len(first["events"]) > 0
    assert {r.source for r in rows} == {"read"}
    service.agenda(db, start=start, end=end)
    assert len(db.execute(select(CalendarIndexRow)).scalars().all()) == len(rows)


class _Dropping:
    """The fixture calendar with one event deleted upstream (or a capped expansion)."""

    def __init__(self, drop_uid: str | None = None, truncated: bool = False) -> None:
        self._inner = build_fake_calendar_provider()
        self._drop = drop_uid
        self._truncated = truncated
        self.last_window_clamped = False
        self.last_truncated = False

    def events(self, start, end):
        self.last_truncated = self._truncated
        return [o for o in self._inner.events(start, end) if o.uid != self._drop]

    def get_event(self, uid):
        return self._inner.get_event(uid)


def test_sync_mirrors_the_horizon_and_a_second_pass_changes_nothing(db) -> None:
    service = _service()
    expected = build_fake_calendar_provider().events(NOW, NOW + timedelta(days=14))
    first = service.sync(db, now=NOW)
    assert first["added"] == len(expected) and first["removed"] == 0
    assert {r.source for r in db.execute(select(CalendarIndexRow)).scalars()} == {"sync"}
    second = service.sync(db, now=NOW)
    assert second["added"] == 0 and second["removed"] == 0
    syncs = [
        r
        for r in db.execute(select(ActivityEventRow)).scalars().all()
        if r.action == "calendar.sync"
    ]
    assert len(syncs) == 1, "a quiet pass must not write a ledger row"


def test_an_event_deleted_upstream_leaves_the_index(db) -> None:
    _service().sync(db, now=NOW)
    service = CalendarService(_Dropping("ev-dis@fixture.example"), build_fake_calendar_writer())
    result = service.sync(db, now=NOW)
    assert result["removed"] == 1
    uids = {r.uid for r in db.execute(select(CalendarIndexRow)).scalars()}
    assert "ev-dis@fixture.example" not in uids


def test_a_truncated_answer_is_never_taken_as_a_deletion(db) -> None:
    _service().sync(db, now=NOW)
    service = CalendarService(
        _Dropping("ev-dis@fixture.example", truncated=True), build_fake_calendar_writer()
    )
    assert service.sync(db, now=NOW)["removed"] == 0
    uids = {r.uid for r in db.execute(select(CalendarIndexRow)).scalars()}
    assert "ev-dis@fixture.example" in uids


def test_sync_without_an_account_is_a_quiet_no_op(db) -> None:
    assert CalendarService(None, None).sync(db, now=NOW)["status"] == "no_account"
    assert db.execute(select(ActivityEventRow)).first() is None


# ----------------------------------------------------------------------------- reminders (358)


def _alarm_provider(tmp_path: Path, start: datetime) -> FakeCalendarProvider:
    path = tmp_path / "alarm.ics"
    path.write_text(
        build_vevent(
            uid="ev-hat@fixture.example",
            summary="Proje toplantısı",
            start=start,
            end=start + timedelta(hours=1),
            reminder_minutes=15,
        ),
        encoding="utf-8",
    )
    return FakeCalendarProvider(path)


def test_a_reminder_is_raised_once_at_its_moment_even_at_night(db, tmp_path) -> None:
    start = NIGHT + timedelta(minutes=20)
    service = CalendarService(_alarm_provider(tmp_path, start), build_fake_calendar_writer())
    assert service.remind_due(db, now=NIGHT)["sent"] == 0
    assert service.remind_due(db, now=NIGHT + timedelta(minutes=6))["sent"] == 1
    [row] = db.execute(select(NotificationRow)).scalars().all()
    assert row.kind == notification_events.CALENDAR_REMINDER
    assert row.deferred_until is None, "the owner's own reminder is not held for morning"
    assert "Proje toplantısı" in row.body
    assert service.remind_due(db, now=NIGHT + timedelta(minutes=7))["sent"] == 0
    assert service.remind_due(db, now=start + timedelta(minutes=1))["sent"] == 0
    assert len(db.execute(select(NotificationRow)).scalars().all()) == 1


def test_a_reminder_whose_event_already_began_is_not_raised(db, tmp_path) -> None:
    start = NIGHT + timedelta(minutes=20)
    service = CalendarService(_alarm_provider(tmp_path, start), build_fake_calendar_writer())
    assert service.remind_due(db, now=start + timedelta(minutes=1))["sent"] == 0
    assert db.execute(select(NotificationRow)).first() is None


def test_other_notifications_still_wait_for_morning(db) -> None:
    row = notification_events.task_completed(db, task_id="t", what="Rapor", now=NIGHT)
    assert row.deferred_until is not None


# --------------------------------------------------------------------------------- the clock


class _Counting:
    def __init__(self, fail: bool = False) -> None:
        self.sync_calls = 0
        self.remind_calls = 0
        self.fail = fail

    def sync(self, session, *, now):
        self.sync_calls += 1
        if self.fail:
            raise OSError("caldav down")
        return {"status": "synced"}

    def remind_due(self, session, *, now):
        self.remind_calls += 1
        return {"status": "checked", "sent": 0}


class _Session:
    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


def test_the_syncer_throttles_and_a_failed_mirror_still_checks_reminders() -> None:
    service = _Counting(fail=True)
    syncer = CalendarSyncer(service, enabled=True, interval_s=1)
    assert syncer.interval_s == MIN_SYNC_INTERVAL_S
    session = _Session()
    result = syncer.tick(session, now=NIGHT)  # type: ignore[arg-type]
    assert result["sync"]["status"] == "failed" and service.remind_calls == 1
    assert session.rollbacks == 1
    syncer.tick(session, now=NIGHT + timedelta(seconds=10))  # type: ignore[arg-type]
    assert service.sync_calls == 1


def test_the_app_puts_the_calendar_on_the_clock_after_mail() -> None:
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    assert app.state.calendar_syncer is not None
    names = [name for name, _ in app.state.routine_clock._sub_ticks()]
    assert "calendar" in names and names.index("mail") < names.index("calendar")


# ------------------------------------------------------------------------------ the briefing


def test_the_briefing_names_todays_events_from_the_calendar(db) -> None:
    result = BriefingService().build(
        db, settings=Settings(), live={"calendar_service": _service()}, now=NOW
    )
    calendar = result["observed_after"]["server"]["sections"]["calendar"]
    assert "Spor" in calendar and "Öğle yemeği" in calendar


# ----------------------------------------------------------------------------------- voice


def test_recurring_and_reminder_sentences_route_with_the_owners_words() -> None:
    resolved = resolve_intent(
        "Her hafta pazartesi 10'da ekip toplantısı ekle, 15 dakika önce hatırlat."
    )
    assert resolved.intent is Intent.CALENDAR_PROPOSE
    assert resolved.calendar_rrule == "FREQ=WEEKLY;BYDAY=MO"
    assert resolved.calendar_reminder_minutes == 15
    weekdays = resolve_intent("Hafta içi her gün 9'da stand-up ekle.")
    assert weekdays.intent is Intent.CALENDAR_PROPOSE
    assert weekdays.calendar_rrule == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    single = resolve_intent("Perşembe 15'e diş hekimi ekle.")
    assert single.intent is Intent.CALENDAR_PROPOSE and single.calendar_rrule is None


@pytest.mark.parametrize(
    "text",
    ["Her gün alışveriş listeme süt ekle.", "Bir hedef ekle: bu ay kitabı bitir."],
)
def test_a_list_or_a_goal_with_every_day_is_not_an_appointment(text: str) -> None:
    assert resolve_intent(text).intent is not Intent.CALENDAR_PROPOSE


def test_the_owners_words_reach_the_proposal_even_when_the_model_drops_them() -> None:
    from tests.voice_corpus.harness import build_harness

    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, "Her hafta pazartesi 10'da ekip toplantısı ekle, 15 dakika önce hatırlat.")
    assert said["resolved_intents"][-1]["intent"] == "calendar_propose"
    call = h.tool(
        sid,
        "c-1",
        "calendar.propose",
        {"when_spoken": "Her hafta pazartesi 10'da", "summary": "Ekip toplantısı"},
    )
    assert call["status"] == "succeeded", call
    proposal = call["result"]["proposal"]
    assert proposal["rrule"] == "FREQ=WEEKLY;BYDAY=MO"
    assert proposal["reminder_minutes"] == 15
    start = datetime.fromisoformat(proposal["start"])
    assert start.weekday() == 0 and start.hour == 10
    assert datetime.fromisoformat(proposal["end"]) - start == timedelta(hours=1)

    h.say(sid, "Yarın 15'e diş hekimi ekle, 15 dakika önce hatırlat.", turn=2)
    call = h.tool(
        sid,
        "c-2",
        "calendar.propose",
        {"when_spoken": "Yarın 15'e, 15 dakika önce hatırlat", "summary": "Diş hekimi"},
    )
    proposal = call["result"]["proposal"]
    start = datetime.fromisoformat(proposal["start"])
    assert start.hour == 15 and proposal["rrule"] is None
    assert datetime.fromisoformat(proposal["end"]) - start == timedelta(hours=1), (
        "15 dakika önce hatırlat is a reminder, not a 15-minute event"
    )
    assert proposal["reminder_minutes"] == 15
