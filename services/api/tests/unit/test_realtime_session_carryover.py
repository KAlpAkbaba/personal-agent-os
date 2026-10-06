"""A new voice session carries the previous one on (card conversation-carryover).

The owner moves from the house PC to the office, opens the phone shell, or speaks again in
the morning: a NEW realtime session used to open with ``transcript_summary=''`` and knew
nothing of the conversation a minute earlier. These tests run through the real
``create_app`` (the ``wired`` fixture of the realtime suite): the previous session writes
its summary through the client's own ``summary`` event and closes through its own route,
and the new session's create payload is what a client actually receives.

Time is never slept: "31 minutes later" is the previous row's own timestamps moved back
against the one clock ``create_session`` reads.
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.voice.realtime_sessions import carryover, service
from app.voice.realtime_sessions.models import RealtimeSessionRow
from tests.unit.test_voice_realtime_sessions import _audit_rows, _create, wired  # noqa: F401

SUMMARY = "Sahip yarınki toplantının gündemini konuştu; sunum taslağı bekleniyor."
PLAN = {"plan_id": "p-1", "topic": "yarınki toplantı", "scope": "gündem", "status": "running"}


def _summarize_and_close(client, sid: str, text: str = SUMMARY) -> None:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "summary", "t_ms": 10, "text": text}]},
    )
    assert response.status_code == 200, response.text
    assert client.post(f"/v1/voice/realtime/sessions/{sid}/close", json={}).status_code == 200


def _row(runtime, sid: str) -> RealtimeSessionRow:
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        db.expunge(row)
        return row


def _edit(runtime, sid: str, **fields) -> None:
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        for key, value in fields.items():
            setattr(row, key, value)
        db.commit()


def _age(runtime, sid: str, minutes: float) -> None:
    """Move the session's last activity back by ``minutes`` against the session clock."""
    moment = service.SESSION_CLOCK.next() - timedelta(minutes=minutes)
    _edit(runtime, sid, updated_at=moment, closed_at=moment, created_at=moment)


# ------------------------------------------------------------------ through the real app


def test_a_new_session_within_the_window_carries_the_previous_summary(wired) -> None:
    client, _identity, runtime, *_ = wired
    first = _create(client)["session_id"]
    _summarize_and_close(client, first)

    created = _create(client)
    second = created["session_id"]
    instructions = created["instructions"]
    assert "Önceki konuşmanın özeti: " + SUMMARY in instructions
    assert "devam ediyor" in instructions
    # the continuation line comes BEFORE the summary block it introduces
    assert instructions.index("devam ediyor") < instructions.index("Önceki konuşmanın özeti:")

    row = _row(runtime, second)
    assert row.transcript_summary == SUMMARY
    carried = row.context_json["carried_from"]
    assert carried["session_id"] == first
    previous = _row(runtime, first)
    assert carried["device_id"] == str(previous.device_id)
    assert carried["device"]  # a label is always named, never empty
    audit = _audit_rows(runtime, second, service.ACTION_SESSION_CREATED)
    assert audit and audit[0].metadata_json["carried_from"]["session_id"] == first
    # the summary TEXT never reaches the audit row
    assert "toplantı" not in repr(audit[0].metadata_json)


def test_a_session_past_the_window_carries_nothing(wired) -> None:
    client, _identity, runtime, *_ = wired
    first = _create(client)["session_id"]
    _summarize_and_close(client, first)
    _age(runtime, first, 31)

    created = _create(client)
    assert "devam ediyor" not in created["instructions"]
    assert SUMMARY not in created["instructions"]
    row = _row(runtime, created["session_id"])
    assert row.transcript_summary == "" and "carried_from" not in row.context_json


def test_a_session_just_inside_the_window_still_carries(wired) -> None:
    client, _identity, runtime, *_ = wired
    first = _create(client)["session_id"]
    _summarize_and_close(client, first)
    _age(runtime, first, 29)

    created = _create(client)
    assert SUMMARY in created["instructions"]
    assert _row(runtime, created["session_id"]).context_json["carried_from"]["session_id"] == first


def test_empty_summaries_are_skipped_and_the_newest_summarised_session_wins(wired) -> None:
    client, _identity, runtime, *_ = wired
    older = _create(client)["session_id"]
    _summarize_and_close(client, older, "Eski konu: tatil planı.")
    _age(runtime, older, 20)
    newer = _create(client)["session_id"]
    _summarize_and_close(client, newer, "Yeni konu: araba bakımı.")
    _age(runtime, newer, 10)
    # the most recent session said nothing worth summarising
    silent = _create(client)["session_id"]
    _edit(runtime, silent, transcript_summary="", context_json={})
    assert client.post(f"/v1/voice/realtime/sessions/{silent}/close", json={}).status_code == 200

    created = _create(client)
    assert "Yeni konu: araba bakımı." in created["instructions"]
    assert "tatil" not in created["instructions"]
    carried = _row(runtime, created["session_id"]).context_json["carried_from"]
    assert carried["session_id"] == newer


def test_the_open_plan_is_carried_and_no_plan_stays_no_plan(wired) -> None:
    client, _identity, runtime, *_ = wired
    first = _create(client)["session_id"]
    ctx = dict(_row(runtime, first).context_json)
    ctx["plan"] = dict(PLAN)
    _edit(runtime, first, context_json=ctx)
    _summarize_and_close(client, first)

    created = _create(client)
    assert "Açık plan: yarınki toplantı" in created["instructions"]
    assert _row(runtime, created["session_id"]).context_json["plan"] == PLAN


def test_no_plan_in_the_previous_session_is_no_plan_in_the_new_one(wired) -> None:
    client, _identity, runtime, *_ = wired
    first = _create(client)["session_id"]
    _summarize_and_close(client, first, "Plansız konu.")
    after = _create(client)
    assert "Plansız konu." in after["instructions"]
    assert "Açık plan" not in after["instructions"]
    assert _row(runtime, after["session_id"]).context_json.get("plan") is None


def _bind_preferences_route(client, engine) -> None:
    """The real ``/v1/voice/preferences`` route on the test's own engine (the realtime
    fixture binds the realtime runtime; the voice runtime is bound the same way)."""
    voice = client.app.state.voice
    voice._engine = engine
    voice._session_factory = sessionmaker(bind=engine, expire_on_commit=False)


def test_the_preference_off_carries_nothing(wired) -> None:
    client, _identity, runtime, _sideband, _issued, engine = wired
    _bind_preferences_route(client, engine)
    response = client.patch("/v1/voice/preferences", json={"conversation_carryover": False})
    assert response.status_code == 200, response.text
    assert response.json()["conversation_carryover"] is False
    first = _create(client)["session_id"]
    _summarize_and_close(client, first)

    created = _create(client)
    assert "devam ediyor" not in created["instructions"]
    assert SUMMARY not in created["instructions"]
    row = _row(runtime, created["session_id"])
    assert row.transcript_summary == "" and "carried_from" not in row.context_json
    audit = _audit_rows(runtime, created["session_id"], service.ACTION_SESSION_CREATED)
    assert "carried_from" not in audit[0].metadata_json

    # and on again, through the same route, it carries
    assert (
        client.patch("/v1/voice/preferences", json={"conversation_carryover": True}).json()[
            "conversation_carryover"
        ]
        is True
    )
    again = _create(client)
    assert "devam ediyor" in again["instructions"] and SUMMARY in again["instructions"]


def test_an_over_long_summary_is_cut_to_the_bound(wired) -> None:
    client, _identity, runtime, *_ = wired
    first = _create(client)["session_id"]
    assert client.post(f"/v1/voice/realtime/sessions/{first}/close", json={}).status_code == 200
    # a row written before the bound existed (or by any other writer) is still cut
    _edit(runtime, first, transcript_summary="a" * (service.MAX_SUMMARY_CHARS + 500))

    created = _create(client)
    row = _row(runtime, created["session_id"])
    assert len(row.transcript_summary) == service.MAX_SUMMARY_CHARS


# ------------------------------------------------------------------------ the pure rule

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


def _fake(*, summary="x", minutes_ago=1.0, state="closed", closed=True, expires_in=None):
    at = NOW - timedelta(minutes=minutes_ago)
    return SimpleNamespace(
        id=uuid.uuid4(),
        transcript_summary=summary,
        state=state,
        updated_at=at,
        closed_at=at if closed else None,
        expires_at=(NOW + timedelta(minutes=expires_in)) if expires_in is not None else None,
        device_id=None,
        client_kind="web",
        context_json={},
    )


def test_the_rule_never_picks_the_session_being_created() -> None:
    me = _fake(minutes_ago=0, state="created", closed=False)
    other = _fake(minutes_ago=5)
    assert carryover.pick_previous([me, other], exclude_id=me.id, now=NOW) is other
    assert carryover.pick_previous([me], exclude_id=me.id, now=NOW) is None


def test_the_live_pick_never_picks_the_session_that_started_the_work() -> None:
    origin = _fake(state="active", closed=False, minutes_ago=0)
    live = _fake(state="active", closed=False, minutes_ago=3)
    assert carryover.pick_live([origin, live], exclude_id=origin.id, now=NOW) is live
    assert carryover.pick_live([origin], exclude_id=origin.id, now=NOW) is None


def test_the_live_pick_skips_closed_expired_and_stale_sessions() -> None:
    closed = _fake(state="closed", minutes_ago=1)
    expired = _fake(state="active", closed=False, minutes_ago=1, expires_in=-1)
    stale = _fake(state="active", closed=False, minutes_ago=45)
    fresh = _fake(state="created", closed=False, minutes_ago=2, expires_in=30)
    rows = [closed, expired, stale, fresh]
    assert carryover.pick_live(rows, exclude_id=uuid.uuid4(), now=NOW) is fresh


def test_the_window_is_thirty_minutes() -> None:
    assert carryover.CARRYOVER_WINDOW == timedelta(minutes=30)
    edge = _fake(minutes_ago=30)
    assert carryover.pick_previous([edge], exclude_id=uuid.uuid4(), now=NOW) is edge


def test_the_continuation_line_names_the_device_and_the_owner_clock() -> None:
    line = carryover.continuation_line({"device": "ev-pc", "at": "11:58"})
    assert line == (
        "Bu konuşma az önce 'ev-pc' cihazındaki oturumdan devam ediyor (11:58); "
        "sahip isterse kaldığınız yerden sürdür."
    )


def test_a_query_reads_only_rows_of_the_window(wired) -> None:
    """The DB half reads through the same pure rule (no second definition of the window)."""
    client, _identity, runtime, *_ = wired
    first = _create(client)["session_id"]
    _summarize_and_close(client, first)
    with runtime.session() as db:
        now = service.SESSION_CLOCK.next()
        found = carryover.find_previous(db, exclude_id=uuid.uuid4(), now=now)
        assert found is not None and str(found.id) == first
        assert db.execute(select(RealtimeSessionRow)).scalars().first() is not None
