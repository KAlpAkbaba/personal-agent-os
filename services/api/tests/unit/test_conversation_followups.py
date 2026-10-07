"""Follow-ups taken from a finished conversation (conversation-followups), on SQLite.

The owner, 2026-10-05: from a conversation, the promises ('yarın ararım', 'cuma
göndereceğim'), the dates and the people go into (a) a person card - name, how they relate to
him, the last talk, the open promises both ways - and (b) a calendar item, PROPOSED and not
written until he says 'tamam' (one batched question after the conversation). 'Ahmet'e ne söz
vermiştim', 'Ayşe ile en son ne konuştuk' answer from the cards with the date. Nothing is
invented: every item cites its transcript line, and an item whose quote is not in that line is
dropped.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.calendar.models import CalendarIndexRow, CalendarProposalRow
from app.calendar.service import CalendarService
from app.config import Settings
from app.conversations import followups, service
from app.conversations.models import (
    ConversationRow,
    ConversationSettingRow,
    PersonRow,
    SegmentRow,
)
from app.conversations.service import LiveConversations
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.operator.models import ObjectFocusRow
from app.people import service as people
from app.people.models import FollowupRow, PersonCardRow
from tests.identity_support import authenticate, install_identity
from tests.mail_calendar_support import (
    build_fake_calendar_provider,
    build_fake_calendar_writer,
)

#: Monday 5 October 2026, 15:00 in Istanbul.
NOON = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
TABLES = (
    ConversationRow,
    SegmentRow,
    PersonRow,
    ConversationSettingRow,
    PersonCardRow,
    FollowupRow,
    CalendarIndexRow,
    CalendarProposalRow,
    ObjectFocusRow,
    ActivityEventRow,
)

LINES = [
    (True, "Ahmet, raporu cuma sana göndereceğim, iş arkadaşım olarak sana söz."),
    (False, "Tamam, ben de yarın seni ararım."),
    (True, "Ayşe ile perşembe 14:00'te toplantımız var, unutmayalım."),
]

#: What a model answered for LINES: three good items and two it made up.
SCRIPTED = [
    {
        "kind": "promise",
        "person": "Ahmet",
        "relation": "iş arkadaşı",
        "direction": "owner",
        "what": "raporu göndermek",
        "due": "2026-10-09",
        "segment": 1,
        "quote": "raporu cuma sana göndereceğim",
    },
    {
        "kind": "promise",
        "person": "Ahmet",
        "relation": "",
        "direction": "them",
        "what": "seni arayacak",
        "due": "2026-10-06",
        "segment": 2,
        "quote": "yarın seni ararım",
    },
    {
        "kind": "date",
        "person": "Ayşe",
        "relation": "",
        "direction": "",
        "what": "Ayşe ile toplantı",
        "due": "2026-10-08T14:00:00+03:00",
        "segment": 3,
        "quote": "perşembe 14:00'te toplantımız var",
    },
    {  # invented: nobody said this
        "kind": "promise",
        "person": "Mehmet",
        "relation": "",
        "direction": "owner",
        "what": "para vermek",
        "due": "2026-10-07",
        "segment": 2,
        "quote": "Mehmet'e para vereceğim",
    },
    {  # a line the conversation does not have
        "kind": "date",
        "person": "Ahmet",
        "relation": "",
        "direction": "",
        "what": "doğum günü",
        "due": "2026-10-20",
        "segment": 9,
        "quote": "doğum günü",
    },
]


@pytest.fixture()
def factory():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.__table__.create(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    eng.dispose()


def _calendar() -> CalendarService:
    return CalendarService(build_fake_calendar_provider(), build_fake_calendar_writer())


def _conversation(db, lines=LINES) -> uuid.UUID:
    live = LiveConversations()
    view = service.start_conversation(db, live, now=NOON)
    for i, (owner, text) in enumerate(lines):
        service.add_segment(
            db,
            live,
            None,  # type: ignore[arg-type] - no embeddings, the cipher is never read
            view.id,
            text=text,
            is_owner=owner,
            now=NOON + timedelta(seconds=10 * i),
        )
    service.stop_conversation(db, live, view.id, now=NOON + timedelta(minutes=5))
    db.commit()
    return view.id


def _process(db, cid, items=SCRIPTED):
    batch = followups.process_conversation(
        db, cid, followups.FakeFollowupExtractor(items), now=NOON + timedelta(minutes=6)
    )
    db.commit()
    return batch


# ------------------------------------------------------------------ extraction


def test_extraction_writes_cards_and_promises_with_their_segment(factory) -> None:
    with factory() as db:
        cid = _conversation(db)
        batch = _process(db, cid)
        assert batch.dropped == 2
        cards = {c.name: c for c in db.execute(select(PersonCardRow)).scalars()}
        assert set(cards) == {"Ahmet", "Ayşe"}  # Mehmet was never said
        assert cards["Ahmet"].relation == "iş arkadaşı"
        assert cards["Ahmet"].last_conversation_id == cid
        rows = db.execute(select(FollowupRow).order_by(FollowupRow.segment_seq)).scalars().all()
        assert [(r.kind, r.direction, r.segment_seq) for r in rows] == [
            ("promise", "owner", 1),
            ("promise", "them", 2),
            ("date", None, 3),
        ]
        segment_text = {
            s.seq: s.text
            for s in db.execute(
                select(SegmentRow).where(SegmentRow.conversation_id == cid)
            ).scalars()
        }
        for row in rows:
            assert row.quote in segment_text[row.segment_seq]
            assert row.conversation_id == cid


def test_a_quote_must_be_in_the_line_it_cites(factory) -> None:
    moved = [dict(SCRIPTED[0], segment=2)]  # the quote is line 1's
    with factory() as db:
        cid = _conversation(db)
        batch = _process(db, cid, moved)
        assert batch.dropped == 1
        assert db.execute(select(func.count(FollowupRow.id))).scalar_one() == 0
        assert db.execute(select(func.count(PersonCardRow.id))).scalar_one() == 0


def test_a_person_nobody_named_gets_no_card(factory) -> None:
    # The quote is real, the name is not: nobody in the conversation said 'Zeynep'.
    guessed = [dict(SCRIPTED[1], person="Zeynep")]
    with factory() as db:
        cid = _conversation(db)
        batch = _process(db, cid, guessed)
        assert (batch.created, batch.dropped) == (0, 1)
        assert db.execute(select(func.count(PersonCardRow.id))).scalar_one() == 0


def test_a_relation_nobody_said_is_not_kept(factory) -> None:
    claimed = [dict(SCRIPTED[0], relation="kardeşi")]
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid, claimed)
        card = db.execute(select(PersonCardRow)).scalar_one()
        assert card.relation is None


def test_processing_twice_adds_nothing(factory) -> None:
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid)
        second = _process(db, cid)
        assert second.created == 0
        assert db.execute(select(func.count(FollowupRow.id))).scalar_one() == 3
        assert db.execute(select(func.count(PersonCardRow.id))).scalar_one() == 2


def test_an_open_conversation_is_not_processed(factory) -> None:
    with factory() as db:
        view = service.start_conversation(db, LiveConversations(), now=NOON)
        db.commit()
        with pytest.raises(followups.FollowupRefused):
            _process(db, view.id)


# ------------------------------------------- the question, and nothing before 'tamam'


def test_calendar_items_are_proposed_and_one_question_asked(factory) -> None:
    with factory() as db:
        cid = _conversation(db)
        batch = _process(db, cid)
        # Two calendar items: his own promise with a date, and the meeting. Ahmet's promise
        # to call stays on the card.
        assert batch.proposed == 2
        assert batch.question.endswith("?")
        assert "raporu göndermek" in batch.question and "Ayşe ile toplantı" in batch.question
        assert "seni arayacak" not in batch.question
        states = sorted(
            r.calendar_state
            for r in db.execute(select(FollowupRow)).scalars()
            if r.calendar_state is not None
        )
        assert states == ["proposed", "proposed"]


def test_nothing_reaches_the_calendar_before_tamam(factory) -> None:
    calendar = _calendar()
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid)
        assert calendar._writer.created == []  # type: ignore[attr-defined]
        assert db.execute(select(func.count(CalendarProposalRow.id))).scalar_one() == 0
        answer = followups.answer_followups(
            db, cid, accepted=False, calendar=calendar, host_flag_enabled=True, session_id="s1"
        )
        db.commit()
        assert answer.written == 0
        assert calendar._writer.created == []  # type: ignore[attr-defined]
        assert db.execute(select(func.count(CalendarProposalRow.id))).scalar_one() == 0
        assert {
            r.calendar_state
            for r in db.execute(select(FollowupRow)).scalars()
            if r.calendar_state is not None
        } == {"declined"}


def test_tamam_writes_the_proposed_items_into_the_calendar(factory) -> None:
    calendar = _calendar()
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid)
        answer = followups.answer_followups(
            db, cid, accepted=True, calendar=calendar, host_flag_enabled=True, session_id="s1"
        )
        db.commit()
        assert answer.written == 2
        created = calendar._writer.created  # type: ignore[attr-defined]
        assert sorted(p.summary for p in created) == ["Ayşe ile toplantı", "raporu göndermek"]
        meeting = next(p for p in created if p.summary == "Ayşe ile toplantı")
        assert meeting.start == datetime(2026, 10, 8, 11, 0, tzinfo=UTC)
        rows = [r for r in db.execute(select(FollowupRow)).scalars() if r.calendar_state]
        assert {r.calendar_state for r in rows} == {"written"}
        assert all(r.calendar_proposal_id is not None for r in rows)
        # A second 'tamam' writes nothing twice.
        again = followups.answer_followups(
            db, cid, accepted=True, calendar=calendar, host_flag_enabled=True, session_id="s1"
        )
        assert again.written == 0
        assert len(calendar._writer.created) == 2  # type: ignore[attr-defined]


def test_tamam_without_a_calendar_keeps_them_proposed(factory) -> None:
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid)
        answer = followups.answer_followups(
            db,
            cid,
            accepted=True,
            calendar=CalendarService(None, None),
            host_flag_enabled=True,
            session_id="s1",
        )
        assert answer.written == 0
        assert "takvim" in answer.speech.lower()
        assert {
            r.calendar_state
            for r in db.execute(select(FollowupRow)).scalars()
            if r.calendar_state is not None
        } == {"proposed"}


# --------------------------------------------------------------------- recall


def test_what_did_i_promise_ahmet(factory) -> None:
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid)
        recall = people.recall_promises(db, "ahmet")
        assert recall.found
        assert "raporu göndermek" in recall.speech
        assert "5 Ekim" in recall.speech  # when it was promised
        assert "9 Ekim" in recall.speech  # when it is due
        assert "seni arayacak" in recall.speech  # his promise, said as his
        assert [p.direction for p in recall.promises] == ["owner", "them"]
        assert recall.promises[0].quote == "raporu cuma sana göndereceğim"


def test_last_talk_with_ayse_has_the_date(factory) -> None:
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid)
        talk = people.last_talk(db, "AYŞE")
        assert talk.found
        assert "5 Ekim 2026" in talk.speech
        assert talk.conversation_id == cid


def test_last_talk_reads_the_topic_from_the_line_the_card_keeps_no_text(factory) -> None:
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid)
        talk = people.last_talk(db, "Ahmet")
        assert talk.topic == LINES[0][1]  # read from the transcript's line
        assert LINES[0][1] in talk.speech
        for card in db.scalars(select(PersonCardRow)):
            kept = " ".join(str(v) for v in vars(card).values() if isinstance(v, str))
            assert not any(text[:12] in kept for _, text in LINES), kept


def _their_words_left(db) -> list[str]:
    """Every stored value that still holds a piece of the conversation's lines."""
    left = []
    for model in (FollowupRow, PersonCardRow, SegmentRow, ConversationRow):
        for row in db.scalars(select(model)):
            for value in vars(row).values():
                if isinstance(value, str) and any(
                    piece in value
                    for _, text in LINES
                    for piece in (text[:12], "seni arayacak", "yarın seni ararım")
                ):
                    left.append(f"{model.__tablename__}: {value}")
    return left


def _foreign_keys_on(factory) -> None:
    # SQLite enforces ON DELETE only with this pragma; the one StaticPool connection keeps it.
    with factory.kw["bind"].connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def test_unut_leaves_none_of_their_words(factory) -> None:
    _foreign_keys_on(factory)
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid)
        assert service.forget_all(db, LiveConversations()) == 1
        db.commit()
        db.expire_all()
        assert db.execute(select(func.count(FollowupRow.id))).scalar_one() == 0
        assert _their_words_left(db) == []
        talk = people.last_talk(db, "Ahmet")  # the card stays: the name and the day
        assert talk.found and "5 Ekim 2026" in talk.speech and talk.topic is None
        assert not people.recall_promises(db, "Ahmet").promises


def test_deleting_the_conversation_takes_its_followups(factory) -> None:
    _foreign_keys_on(factory)
    with factory() as db:
        cid = _conversation(db)
        _process(db, cid)
        assert service.delete_conversation(db, LiveConversations(), cid)
        db.commit()
        db.expire_all()
        assert db.execute(select(func.count(FollowupRow.id))).scalar_one() == 0
        assert _their_words_left(db) == []


def test_unknown_person_is_said_so(factory) -> None:
    with factory() as db:
        assert not people.recall_promises(db, "Mehmet").found
        assert "Mehmet" in people.last_talk(db, "Mehmet").speech


# ------------------------------------------------------------ the model provider


def test_anthropic_extractor_forces_the_schema_and_sends_the_transcript_as_data() -> None:
    sent: list[dict] = []

    def send(url, headers, body, timeout_s):
        sent.append(body)
        return 200, {
            "content": [
                {"type": "tool_use", "name": followups.TOOL_NAME, "input": {"items": SCRIPTED[:1]}}
            ]
        }

    extractor = followups.AnthropicFollowupExtractor("k", send=send)
    lines = [followups.TranscriptLine(1, "Sen", LINES[0][1])]
    items = extractor.extract(lines, spoken_on=NOON)
    assert items == SCRIPTED[:1]
    body = sent[0]
    assert body["model"] == followups.DEFAULT_MODEL
    assert body["tool_choice"] == {"type": "tool", "name": followups.TOOL_NAME}
    assert LINES[0][1] not in body["system"]
    assert LINES[0][1] in json.dumps(body["messages"], ensure_ascii=False)


def test_no_key_extracts_nothing_and_says_so(factory) -> None:
    extractor = followups.build_followup_extractor(Settings(anthropic_api_key=""))
    with factory() as db:
        cid = _conversation(db)
        batch = followups.process_conversation(db, cid, extractor, now=NOON)
        assert batch.created == 0
        assert batch.error == followups.ERROR_NOT_CONFIGURED


# --------------------------------------------------------- through create_app


def test_routes_through_the_application(factory) -> None:
    settings = Settings(_env_file=None, calendar_write_enabled=True)
    app = create_app(settings)  # the router comes from people/routes.py's ROUTERS
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = factory.kw["bind"]
    artifacts._session_factory = factory
    app.state.artifacts = artifacts
    app.state.followup_extractor = followups.FakeFollowupExtractor(SCRIPTED)
    calendar = _calendar()
    app.state.calendar_service = calendar
    with factory() as db:
        cid = _conversation(db)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    made = client.post(f"/v1/conversations/{cid}/followups")
    assert made.status_code == 200, made.text
    assert made.json()["proposed"] == 2
    assert calendar._writer.created == []  # type: ignore[attr-defined]
    recall = client.get("/v1/people/recall", params={"name": "Ahmet"})
    assert recall.status_code == 200
    assert "raporu göndermek" in recall.json()["speech"]
    talk = client.get("/v1/people/last-talk", params={"name": "Ayşe"})
    assert "5 Ekim 2026" in talk.json()["speech"]
    cards = client.get("/v1/people/cards").json()["cards"]
    assert {c["name"] for c in cards} == {"Ahmet", "Ayşe"}
    ok = client.post(f"/v1/conversations/{cid}/followups/answer", json={"tamam": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["written"] == 2
    assert len(calendar._writer.created) == 2  # type: ignore[attr-defined]
