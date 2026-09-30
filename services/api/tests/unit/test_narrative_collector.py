"""Unit tests: app.narrative.collector - deterministic facts from the ledger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_BROWSER_SEARCH,
    EVENT_TYPE_MAIL_DRAFTED,
    EVENT_TYPE_MAIL_SENT,
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_RESEARCH_FAILED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_STARTED,
    SUBSYSTEM_BROWSER,
    SUBSYSTEM_MAIL,
    SUBSYSTEM_RESEARCH,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

FAIL_RESEARCH = "Kaynak taraması zaman aşımına uğradı"
FAIL_MAIL = "Posta gönderilemedi"


def make_session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def add_row(session, ref, when, etype, sub, status, summary, device=None, result=None, detail=None):
    d = dict(detail or {})
    if device is not None:
        d["device"] = device
    ledger_service.record(
        session,
        ledger_service.ActivityEvent(
            event_type=etype,
            subsystem=sub,
            action="x",
            factual_summary=summary,
            source="live",
            source_ref=ref,
            status=status,
            result=result,
            occurred_at=when,
            detail_json=d,
        ),
    )


def _at(day: int, hour: int) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=UTC)


def seed(session) -> None:
    add_row(
        session,
        "r1",
        _at(29, 10),
        EVENT_TYPE_RESEARCH_FAILED,
        SUBSYSTEM_RESEARCH,
        STATUS_FAILED,
        FAIL_RESEARCH,
        "ofis",
        "timeout",
    )
    add_row(
        session,
        "r2",
        _at(29, 11),
        EVENT_TYPE_RESEARCH_COMPLETED,
        SUBSYSTEM_RESEARCH,
        STATUS_COMPLETED,
        "Rapor hazırlandı",
        "ofis",
    )
    add_row(
        session,
        "r3",
        _at(30, 8),
        EVENT_TYPE_BROWSER_SEARCH,
        SUBSYSTEM_BROWSER,
        STATUS_COMPLETED,
        "Sayfa açıldı",
        "ev",
    )
    add_row(
        session,
        "r4",
        _at(26, 9),
        EVENT_TYPE_MAIL_SENT,
        SUBSYSTEM_MAIL,
        STATUS_FAILED,
        FAIL_MAIL,
        "ev",
        "no_capable_device",
        {"error_class": "no_capable_device"},
    )
    add_row(
        session,
        "r5",
        _at(27, 9),
        EVENT_TYPE_MAIL_DRAFTED,
        SUBSYSTEM_MAIL,
        STATUS_COMPLETED,
        "Taslak kaydedildi",
    )
    add_row(
        session,
        "r6",
        _at(20, 9),
        EVENT_TYPE_RESEARCH_COMPLETED,
        SUBSYSTEM_RESEARCH,
        STATUS_COMPLETED,
        "Eski rapor",
        "ev",
    )
    add_row(
        session,
        "r7",
        _at(29, 9),
        EVENT_TYPE_RESEARCH_COMPLETED,
        SUBSYSTEM_RESEARCH,
        STATUS_STARTED,
        "Araştırma başladı",
        "ev",
    )
    session.commit()


from app.narrative.collector import Period, collect, resolve_period  # noqa: E402


@pytest.fixture()
def db():
    s = make_session()
    seed(s)
    yield s
    s.close()


def test_failures_come_first_newest_first_with_subsystem_and_reason(db):
    facts = collect(db, "bu hafta", now=NOW)
    assert [f.summary for f in facts.failed] == [FAIL_RESEARCH, FAIL_MAIL]
    assert facts.failed[0].subsystem == "research"
    assert facts.failed[0].reason == "timeout"


def test_completed_events_are_grouped_by_subsystem(db):
    facts = collect(db, "bu hafta", now=NOW)
    groups = {sub: [e.summary for e in evs] for sub, evs in facts.completed}
    assert groups == {
        "browser": ["Sayfa açıldı"],
        "mail": ["Taslak kaydedildi"],
        "research": ["Rapor hazırlandı"],
    }


def test_a_started_row_is_neither_failed_nor_completed(db):
    facts = collect(db, "bu hafta", now=NOW)
    assert dict(facts.counts_by_subsystem)["research"] == 2  # r1 + r2, not r7
    assert facts.total == 5


def test_counts_per_subsystem_and_per_device(db):
    facts = collect(db, "bu hafta", now=NOW)
    assert dict(facts.counts_by_subsystem) == {"browser": 1, "mail": 2, "research": 2}
    assert dict(facts.counts_by_device) == {"bulut": 1, "ev": 2, "ofis": 2}


def test_no_capable_device_events_are_listed_apart(db):
    facts = collect(db, "bu hafta", now=NOW)
    assert [e.summary for e in facts.no_capable_device] == [FAIL_MAIL]


def test_a_device_word_in_the_locative_selects_that_device_only(db):
    facts = collect(db, "bu hafta", "ofiste", now=NOW)
    assert facts.device == "ofis"
    assert facts.total == 2
    assert [f.summary for f in facts.failed] == [FAIL_RESEARCH]


def test_a_near_miss_device_word_selects_nothing_rather_than_everything(db):
    facts = collect(db, "bu hafta", "kütüphane", now=NOW)
    assert facts.total == 0 and facts.is_empty


def test_bulut_selects_rows_that_name_no_device(db):
    facts = collect(db, "bu hafta", "bulut", now=NOW)
    assert [e.summary for _, evs in facts.completed for e in evs] == ["Taslak kaydedildi"]


def test_bu_hafta_covers_exactly_the_last_seven_days(db):
    p = resolve_period("bu hafta", NOW)
    assert p.end == NOW and p.end - p.start == timedelta(days=7)
    assert collect(db, "bu hafta", now=NOW).total == 5  # r6 (10 days old) is out


def test_a_row_just_before_the_seven_day_window_is_left_out():
    s = make_session()
    edge = NOW - timedelta(days=7)
    for ref, when in (("in", edge), ("out", edge - timedelta(seconds=1))):
        add_row(s, ref, when, EVENT_TYPE_BROWSER_SEARCH, SUBSYSTEM_BROWSER, "completed", ref)
    s.commit()
    assert collect(s, "bu hafta", now=NOW).total == 1


def test_dun_covers_exactly_yesterday_in_utc(db):
    facts = collect(db, "dün", now=NOW)
    assert facts.covered == Period(
        datetime(2026, 9, 29, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC), "dün"
    )
    assert facts.total == 2  # r1, r2 - r3 is today, r7 is 'started'


def test_bugun_excludes_yesterday_and_ends_now(db):
    facts = collect(db, "bugün", now=NOW)
    assert facts.total == 1 and facts.covered.end == NOW


def test_an_explicit_window_is_start_inclusive_end_exclusive(db):
    p = Period(datetime(2026, 9, 29, 10, tzinfo=UTC), datetime(2026, 9, 29, 11, tzinfo=UTC), "x")
    facts = collect(db, p, now=NOW)
    assert facts.total == 1 and facts.failed[0].summary == FAIL_RESEARCH


def test_an_empty_period_yields_empty_facts(db):
    p = Period(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), "x")
    facts = collect(db, p, now=NOW)
    assert facts.is_empty and facts.failed == () and facts.completed == ()


def test_an_unknown_period_word_is_refused_not_guessed(db):
    with pytest.raises(ValueError):
        collect(db, "geçen ay", now=NOW)


def test_the_same_rows_twice_give_equal_facts(db):
    assert collect(db, "bu hafta", now=NOW) == collect(db, "bu hafta", now=NOW)


def test_more_rows_than_one_query_page_are_all_counted():
    s = make_session()
    for i in range(230):
        add_row(
            s,
            f"p{i}",
            NOW - timedelta(minutes=i + 1),
            EVENT_TYPE_BROWSER_SEARCH,
            SUBSYSTEM_BROWSER,
            "completed",
            f"olay {i}",
        )
    s.commit()
    assert collect(s, "bu hafta", now=NOW).total == 230
