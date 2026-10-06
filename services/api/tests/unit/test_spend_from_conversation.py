"""A spend heard in a conversation (money-ledger): booked when sure, asked when not.

A price asked ("ne kadar"), an amount said ("yedi yüz elli lira") and the OWNER's acceptance
("tamam alayım", "olur veriyorum") is booked at once as TENTATIVE and said in one line with
"geri al". Anything less sure - no clear acceptance, two prices, an acceptance nobody can tell
was his - books nothing; 1-2 minutes after the conversation ENDS he is asked "X liralık bir
harcama yaptınız mı?" and answers "evet", "hayır" or "evet ama 750". "Nakit verdim" books cash.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.conversations import spend
from app.conversations.models import ConversationRow, SegmentRow
from app.mail.models import MailIndexRow
from app.money import pending, service
from app.money.loop import ASK_DELAY, SpendLoop, scan_conversations
from app.money.models import MONEY_TABLES, MoneyEntry, MoneyQuestion
from app.notifications.models import NotificationRow
from app.voice.realtime_sessions import tools_money
from app.voice.realtime_sessions.tools import ToolContext

T0 = datetime(2026, 10, 6, 11, 0, tzinfo=UTC)

ME = True  # the owner said it
THEM = False  # someone else said it


def _seg(seq: int, owner: bool, words: str) -> spend.Line:
    return spend.Line(seq=seq, is_owner=owner, said=words, at=T0 + timedelta(seconds=10 * seq))


def _decide(lines: list[tuple[bool, str]], *, ended: bool = True, owner_known: bool = True):
    segs = [_seg(i + 1, who, words) for i, (who, words) in enumerate(lines)]
    return spend.decide(segs, ended=ended, owner_known=owner_known)


# ------------------------------------------------------------------ (1) the decision table


def test_price_amount_and_his_acceptance_is_booked() -> None:
    decisions = _decide(
        [(ME, "Bu ceket ne kadar?"), (THEM, "Yedi yüz elli lira abi."), (ME, "Tamam alayım.")]
    )
    assert [(d.kind, d.amount_kurus, d.method) for d in decisions] == [(spend.BOOK, 75000, "card")]
    assert decisions[0].anchor_seq == 3


@pytest.mark.parametrize("accept", ["Olur veriyorum.", "Tamam alıyorum.", "Anlaştık, alıyorum."])
def test_the_acceptance_words(accept) -> None:
    decisions = _decide([(ME, "Kaç para?"), (THEM, "1.250 TL"), (ME, accept)])
    assert [(d.kind, d.amount_kurus) for d in decisions] == [(spend.BOOK, 125000)]


def test_a_bare_number_answering_the_price_question_is_the_price() -> None:
    decisions = _decide([(ME, "Bu ne kadar?"), (THEM, "Yedi yüz elli."), (ME, "Tamam alayım.")])
    assert [(d.kind, d.amount_kurus) for d in decisions] == [(spend.BOOK, 75000)]


def test_cash_said_is_booked_as_cash() -> None:
    decisions = _decide(
        [(ME, "Ne kadar?"), (THEM, "Üç yüz lira."), (ME, "Tamam alayım, nakit verdim.")]
    )
    assert [(d.kind, d.method) for d in decisions] == [(spend.BOOK, "cash")]


@pytest.mark.parametrize(
    ("lines", "reason", "amount"),
    [
        # A price and an amount, then no acceptance: asked after the conversation.
        (
            [(ME, "Bu ne kadar?"), (THEM, "Yedi yüz elli lira."), (ME, "Bir düşüneyim.")],
            spend.REASON_NO_ACCEPTANCE,
            75000,
        ),
        # Two prices: which one? Asked with the last.
        (
            [
                (ME, "Ne kadar?"),
                (THEM, "Sekiz yüz lira."),
                (ME, "Çok pahalı."),
                (THEM, "Yedi yüz elli olsun."),
                (ME, "Tamam alayım."),
            ],
            spend.REASON_TWO_PRICES,
            75000,
        ),
        # The acceptance was not HIS (someone else said "tamam alayım").
        (
            [(ME, "Ne kadar?"), (THEM, "Yedi yüz elli lira."), (THEM, "Tamam alayım.")],
            spend.REASON_NO_ACCEPTANCE,
            75000,
        ),
    ],
)
def test_not_sure_is_asked_not_booked(lines, reason, amount) -> None:
    decisions = _decide(lines)
    assert [(d.kind, d.reason, d.amount_kurus) for d in decisions] == [(spend.ASK, reason, amount)]


def test_an_owner_nobody_can_tell_apart_is_asked_not_booked() -> None:
    decisions = _decide(
        [(THEM, "Bu ceket ne kadar?"), (THEM, "Yedi yüz elli lira."), (THEM, "Tamam alayım.")],
        owner_known=False,
    )
    assert [(d.kind, d.reason) for d in decisions] == [(spend.ASK, spend.REASON_UNKNOWN_SPEAKER)]


def test_an_open_episode_is_not_asked_while_the_conversation_goes_on() -> None:
    lines = [(ME, "Bu ne kadar?"), (THEM, "Yedi yüz elli lira.")]
    assert _decide(lines, ended=False) == []
    assert [d.kind for d in _decide(lines, ended=True)] == [spend.ASK]


@pytest.mark.parametrize(
    "lines",
    [
        [(ME, "Maaşım elli bin lira oldu."), (THEM, "Tamam.")],
        [(ME, "Saat kaçta geliyorsun?"), (THEM, "Üçte.")],
        [(ME, "Tamam alayım."), (THEM, "Peki.")],
        [(ME, "Ne kadar sürer?"), (THEM, "İki saat.")],
        [],
    ],
)
def test_no_purchase_is_nothing(lines) -> None:
    assert _decide(lines) == []


def test_two_purchases_in_one_conversation_are_two() -> None:
    decisions = _decide(
        [
            (ME, "Domates ne kadar?"),
            (THEM, "Kırk lira."),
            (ME, "Tamam alayım."),
            (ME, "Peynir kaç para?"),
            (THEM, "İki yüz elli lira."),
            (ME, "Olur veriyorum."),
        ]
    )
    assert [(d.kind, d.amount_kurus, d.anchor_seq) for d in decisions] == [
        (spend.BOOK, 4000, 3),
        (spend.BOOK, 25000, 6),
    ]


# ------------------------------------------------------------------ (2) the loop, on a clock


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        *MONEY_TABLES,
        NotificationRow.__table__,
        MailIndexRow.__table__,
        ConversationRow.__table__,
        SegmentRow.__table__,
    ):
        table.create(eng, checkfirst=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture(autouse=True)
def _fresh_pending():
    pending.reset()
    yield
    pending.reset()


def _conversation(db, lines: list[tuple[bool, str]], *, ended_at: datetime | None) -> uuid.UUID:
    cid = uuid.uuid4()
    db.add(ConversationRow(id=cid, mode="home", started_at=T0, ended_at=ended_at))
    for i, (owner, words) in enumerate(lines, start=1):
        db.add(
            SegmentRow(
                id=uuid.uuid4(),
                conversation_id=cid,
                seq=i,
                spoken_at=T0 + timedelta(seconds=10 * i),
                text=words,
                is_owner=owner,
            )
        )
    db.commit()
    return cid


SURE = [(ME, "Bu ceket ne kadar?"), (THEM, "Yedi yüz elli lira abi."), (ME, "Tamam alayım.")]
UNSURE = [(ME, "Bu ne kadar?"), (THEM, "Yedi yüz elli lira."), (ME, "Bir düşüneyim.")]


def test_a_sure_spend_is_booked_while_the_conversation_still_runs(db) -> None:
    _conversation(db, SURE, ended_at=None)
    scan_conversations(db, now=T0 + timedelta(seconds=40))
    row = db.scalars(select(MoneyEntry)).one()
    assert row.status == service.STATUS_TENTATIVE and row.amount_kurus == 75000
    note = db.scalars(select(NotificationRow)).one()
    assert "750 TL" in note.body and "geri al" in note.body
    # Scanning again books nothing more.
    scan_conversations(db, now=T0 + timedelta(seconds=60))
    assert len(db.scalars(select(MoneyEntry)).all()) == 1
    assert len(db.scalars(select(NotificationRow)).all()) == 1
    assert pending.booking_open(T0 + timedelta(seconds=60))


def test_the_question_waits_for_the_end_and_then_one_to_two_minutes(db) -> None:
    assert timedelta(minutes=1) <= ASK_DELAY <= timedelta(minutes=2)
    end = T0 + timedelta(minutes=1)
    cid = _conversation(db, UNSURE, ended_at=None)
    scan_conversations(db, now=end)
    assert db.scalars(select(MoneyQuestion)).all() == []
    conv = db.get(ConversationRow, cid)
    conv.ended_at = end
    db.commit()
    scan_conversations(db, now=end + ASK_DELAY - timedelta(seconds=1))
    assert db.scalars(select(NotificationRow)).all() == []
    scan_conversations(db, now=end + ASK_DELAY)
    question = db.scalars(select(MoneyQuestion)).one()
    assert question.asked_at is not None and question.amount_kurus == 75000
    note = db.scalars(select(NotificationRow)).one()
    assert "750 liralık bir harcama yaptınız mı?" in note.body
    assert db.scalars(select(MoneyEntry)).all() == []
    scan_conversations(db, now=end + ASK_DELAY + timedelta(minutes=5))
    assert len(db.scalars(select(NotificationRow)).all()) == 1
    assert pending.question_open(end + ASK_DELAY + timedelta(minutes=1))


def test_the_loop_runs_on_its_injected_clock(engine) -> None:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _conversation(session, SURE, ended_at=None)
    loop = SpendLoop(factory, clock=lambda: T0 + timedelta(minutes=1))
    assert loop.scan_once() == {"booked": 1, "asked": 0}
    assert loop.health_check()["last_result"] == {"booked": 1, "asked": 0}


# ------------------------------------------------------------------ (3) his answers


def _ctx(db, turn: dict | None = None, now: datetime = T0) -> ToolContext:
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={"last_utterance": turn or {}},
        db=db,
        now=now,
    )


def _asked(db) -> datetime:
    end = T0 + timedelta(minutes=1)
    _conversation(db, UNSURE, ended_at=end)
    scan_conversations(db, now=end + ASK_DELAY)
    return end + ASK_DELAY + timedelta(seconds=30)


def test_evet_books_the_asked_amount(db) -> None:
    now = _asked(db)
    result = tools_money.money_spend_yes(_ctx(db, now=now), {})
    assert result["status"] == "ok"
    row = db.scalars(select(MoneyEntry)).one()
    assert row.amount_kurus == 75000 and row.status == service.STATUS_CONFIRMED
    assert row.source == service.SOURCE_QUESTION
    assert db.scalars(select(MoneyQuestion)).one().answer == "yes"
    assert not pending.question_open(now)


def test_evet_ama_books_the_amount_he_said(db) -> None:
    now = _asked(db)
    result = tools_money.money_spend_yes(_ctx(db, {"money_amount_kurus": 50000}, now=now), {})
    assert result["status"] == "ok"
    assert db.scalars(select(MoneyEntry)).one().amount_kurus == 50000


def test_the_model_s_amount_is_used_when_the_words_carried_none(db) -> None:
    now = _asked(db)
    tools_money.money_spend_yes(_ctx(db, now=now), {"amount": "600"})
    assert db.scalars(select(MoneyEntry)).one().amount_kurus == 60000


def test_hayir_books_nothing(db) -> None:
    now = _asked(db)
    result = tools_money.money_spend_no(_ctx(db, now=now), {})
    assert result["status"] == "ok"
    assert db.scalars(select(MoneyEntry)).all() == []
    assert db.scalars(select(MoneyQuestion)).one().answer == "no"


def test_an_answer_with_no_open_question_says_so(db) -> None:
    result = tools_money.money_spend_yes(_ctx(db), {})
    assert result["status"] == "no_question"
    assert db.scalars(select(MoneyEntry)).all() == []


def test_a_question_is_answered_once(db) -> None:
    now = _asked(db)
    tools_money.money_spend_yes(_ctx(db, now=now), {})
    again = tools_money.money_spend_yes(_ctx(db, now=now + timedelta(seconds=5)), {})
    assert again["status"] == "no_question"
    assert len(db.scalars(select(MoneyEntry)).all()) == 1
