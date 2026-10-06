"""JARVIS's own money ledger (money-ledger): amounts, bank mails, matching, the answers, the routes.

Never the bank: a balance and a card spend come from the bank's NOTIFICATION mails already in
``mail_index`` (read-only, per-bank small parsers); nothing here moves money, holds a bank
password or opens a connection to a bank. A spend the owner agreed to in a conversation is a
TENTATIVE row until a bank mail with the same amount in the time window confirms it - the same
spend is never counted twice. Cash is apart: no bank mail ever confirms it.
"""

from __future__ import annotations

import ast
import socket
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.conversations.models import ConversationRow, SegmentRow
from app.mail.models import MailIndexRow
from app.mail.poller import MailPoller
from app.main import create_app
from app.money import amounts, banks, ledger, pending, service
from app.money.models import (
    MONEY_TABLES,
    MoneyBalance,
    MoneyBankNotice,
    MoneyEntry,
)
from app.notifications.models import NotificationRow
from app.voice.intents import CAPABILITY_BY_INTENT, QUERY_TOOL_BY_INTENT, Intent, resolve_intent
from app.voice.realtime_sessions import tools_money
from app.voice.realtime_sessions.tools import ToolContext, default_registry
from tests.identity_support import authenticate, install_identity

API = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 6, 11, 0, tzinfo=UTC)


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
def factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(factory):
    with factory() as session:
        yield session


@pytest.fixture(autouse=True)
def _fresh_pending():
    pending.reset()
    yield
    pending.reset()


# ------------------------------------------------------------------ (1) Turkish amounts


@pytest.mark.parametrize(
    ("raw", "kurus"),
    [
        ("1.234,56 TL", 123456),
        ("1.234,56", 123456),
        ("12.500 TL", 1250000),
        ("750 TL", 75000),
        ("750,00 TL", 75000),
        ("TL 1.234,56", 123456),
        ("₺300", 30000),
        ("0,99 TL", 99),
        ("1.000.000,00 TL", 100000000),
        ("TRY 1,234.56", 123456),
        ("45,5 TL", 4550),
    ],
)
def test_turkish_digit_amounts_parse_to_kurus(raw, kurus) -> None:
    assert amounts.parse_amount(raw) == kurus


@pytest.mark.parametrize("raw", ["", "TL", "abc", "1.2.3,4,5", "-", ",", "12,345,67"])
def test_a_shape_that_is_not_an_amount_is_none(raw) -> None:
    assert amounts.parse_amount(raw) is None


@pytest.mark.parametrize(
    ("words", "kurus"),
    [
        ("yedi yüz elli", 75000),
        ("bin iki yüz elli", 125000),
        ("iki bin beş yüz", 250000),
        ("on iki bin beş yüz", 1250000),
        ("bin buçuk", 150000),
        ("iki bin buçuk", 250000),
        ("yüz", 10000),
        ("elli", 5000),
        ("bin iki yüz elli virgül elli", 125050),
        ("bir milyon", 100000000),
    ],
)
def test_turkish_number_words_parse_to_kurus(words, kurus) -> None:
    assert amounts.words_amount(words.split()) == kurus


def test_the_money_said_in_a_sentence_needs_a_currency_word() -> None:
    assert amounts.money_in("Bu yedi yüz elli lira abi") == [75000]
    assert amounts.money_in("1.250 TL olur") == [125000]
    assert amounts.money_in("iki kilo domates") == []
    assert amounts.money_in("saat üçte gel") == []
    # A bare number counts only when the caller says it is an answer to a price question.
    assert amounts.money_in("yedi yüz elli", bare=True) == [75000]
    assert amounts.money_in("iki tane", bare=True) == []


@pytest.mark.parametrize(
    ("kurus", "said"),
    [(75000, "750 TL"), (123456, "1.234,56 TL"), (100000000, "1.000.000 TL"), (99, "0,99 TL")],
)
def test_amounts_are_said_back_the_turkish_way(kurus, said) -> None:
    assert amounts.format_tl(kurus) == said


# ------------------------------------------------------------------ (2) bank notification mails


def _mail(
    db,
    *,
    sender: str,
    subject: str,
    body: str,
    at: datetime,
    mid: str | None = None,
    account: str = "",
) -> MailIndexRow:
    row = MailIndexRow(
        id=uuid.uuid4(),
        provider_message_id=mid or f"<{uuid.uuid4()}@bank>",
        account_key=account,
        folder="INBOX",
        from_name="Banka",
        from_email=sender,
        to_json=[],
        subject=subject,
        date=at,
        snippet=body,
        has_attachments=False,
        thread_key="",
        unread=True,
        last_used_at=at,
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize(
    ("sender", "subject", "body", "kind", "amount", "balance", "merchant"),
    [
        (
            "bilgilendirme@garantibbva.com.tr",
            "Kartınızdan harcama yapıldı",
            "Bonus kartınızla 06.10.2026 14:05'te MIGROS ATASEHIR işyerinde 1.234,56 TL "
            "harcama yapılmıştır. Kullanılabilir limitiniz 8.765,44 TL.",
            "spend",
            123456,
            None,
            "MIGROS ATASEHIR",
        ),
        (
            "bilgi@ileti.isbank.com.tr",
            "Hesap hareketi",
            "Hesabınızdan 750,00 TL tutarında harcama yapılmıştır. İşyeri: A101 KADIKOY. "
            "Güncel bakiyeniz 12.345,67 TL'dir.",
            "spend",
            75000,
            1234567,
            "A101 KADIKOY",
        ),
        (
            "info@akbank.com",
            "Hesabınıza para geldi",
            "Hesabınıza 5.000,00 TL tutarında FAST ile gelen transfer yapılmıştır. "
            "Bakiyeniz: 17.345,67 TL",
            "income",
            500000,
            1734567,
            None,
        ),
        (
            "bildirim@yapikredi.com.tr",
            "Bakiye bilgisi",
            "Vadesiz hesabınızın güncel bakiyesi 2.500,00 TL'dir.",
            "balance",
            None,
            250000,
            None,
        ),
    ],
)
def test_each_bank_s_notification_parses(
    sender, subject, body, kind, amount, balance, merchant
) -> None:
    notice = banks.parse_notice(sender, subject, body, at=T0)
    assert notice is not None
    assert notice.kind == kind
    assert notice.amount_kurus == amount
    assert notice.balance_kurus == balance
    assert notice.merchant == merchant


@pytest.mark.parametrize(
    ("sender", "subject", "body"),
    [
        # Not a bank: a shop that says "TL" is not a statement about the owner's account.
        ("kampanya@migros.com.tr", "Size özel", "1.234,56 TL harcama yapın, 100 TL kazanın"),
        # A look-alike domain is not the bank.
        ("bilgi@garantibbva.com.tr.evil.example", "Harcama", "500,00 TL harcama yapılmıştır."),
        ("bilgi@notgarantibbva.com.tr", "Harcama", "500,00 TL harcama yapılmıştır."),
        # The bank's own advertising is not a movement.
        ("kampanya@garantibbva.com.tr", "Fırsat", "Bu ay 2.000 TL harcamanıza 200 TL bonus!"),
    ],
)
def test_what_is_not_a_bank_notification_is_nothing(sender, subject, body) -> None:
    assert banks.parse_notice(sender, subject, body, at=T0) is None


def test_the_balance_is_kept_with_its_time_and_said_with_it(db) -> None:
    _mail(
        db,
        sender="bildirim@yapikredi.com.tr",
        subject="Bakiye bilgisi",
        body="Vadesiz hesabınızın güncel bakiyesi 2.500,00 TL'dir.",
        at=T0,
    )
    assert ledger.ingest_mail(db, now=T0 + timedelta(minutes=1))["balances"] == 1
    row = db.scalars(select(MoneyBalance)).one()
    assert row.balance_kurus == 250000
    assert row.as_of.replace(tzinfo=UTC) == T0
    said = service.balance_speech(db, now=T0 + timedelta(hours=3))
    assert "2.500 TL" in said and "Yapı Kredi" in said and "14:00" in said
    # An OLDER mail never overwrites a newer balance.
    _mail(
        db,
        sender="bildirim@yapikredi.com.tr",
        subject="Bakiye bilgisi",
        body="Vadesiz hesabınızın güncel bakiyesi 9.999,00 TL'dir.",
        at=T0 - timedelta(days=1),
    )
    ledger.ingest_mail(db, now=T0 + timedelta(minutes=2))
    assert db.scalars(select(MoneyBalance)).one().balance_kurus == 250000


def test_no_balance_known_is_said_plainly(db) -> None:
    assert "bilmiyorum" in service.balance_speech(db, now=T0)


def test_a_mail_is_read_once_however_many_polls_see_it(db) -> None:
    _mail(
        db,
        sender="bilgilendirme@garantibbva.com.tr",
        subject="Harcama",
        body="MIGROS ATASEHIR işyerinde 100,00 TL harcama yapılmıştır.",
        at=T0,
    )
    first = ledger.ingest_mail(db, now=T0)
    second = ledger.ingest_mail(db, now=T0 + timedelta(minutes=5))
    assert first["booked"] == 1 and second["booked"] == 0
    assert len(db.scalars(select(MoneyEntry)).all()) == 1
    assert len(db.scalars(select(MoneyBankNotice)).all()) == 1


# ------------------------------------------------------------------ (3) matching, never twice


def _tentative(db, kurus: int, at: datetime, *, method: str = "card") -> MoneyEntry:
    return service.book_spend(
        db,
        kurus,
        status=service.STATUS_TENTATIVE,
        source=service.SOURCE_CONVERSATION,
        method=method,
        description="Konuşmadan",
        occurred_at=at,
        now=at,
    )


def _garanti(db, kurus_text: str, at: datetime, merchant: str = "MIGROS ATASEHIR") -> None:
    _mail(
        db,
        sender="bilgilendirme@garantibbva.com.tr",
        subject="Kartınızdan harcama yapıldı",
        body=f"{merchant} işyerinde {kurus_text} TL harcama yapılmıştır.",
        at=at,
    )


def test_a_bank_mail_confirms_the_tentative_spend_and_is_not_counted_again(db) -> None:
    spend = _tentative(db, 75000, T0)
    _garanti(db, "750,00", T0 + timedelta(minutes=3))
    result = ledger.ingest_mail(db, now=T0 + timedelta(minutes=4))
    assert result["matched"] == 1 and result["booked"] == 0
    rows = db.scalars(select(MoneyEntry)).all()
    assert len(rows) == 1
    db.refresh(spend)
    assert spend.status == service.STATUS_CONFIRMED and spend.bank_ref
    assert spend.category == "market"
    total = service.spent(db, category=None, now=T0 + timedelta(hours=1))
    assert total.total_kurus == 75000 and total.count == 1


def test_a_different_amount_or_a_far_time_is_its_own_spend(db) -> None:
    _tentative(db, 75000, T0)
    _garanti(db, "760,00", T0 + timedelta(minutes=3))
    _garanti(db, "750,00", T0 + timedelta(days=3))
    result = ledger.ingest_mail(db, now=T0 + timedelta(days=3, minutes=1))
    assert result["matched"] == 0 and result["booked"] == 2
    statuses = sorted(r.status for r in db.scalars(select(MoneyEntry)).all())
    assert statuses == ["confirmed", "confirmed", "tentative"]


def test_one_bank_mail_confirms_one_spend_only(db) -> None:
    a = _tentative(db, 75000, T0)
    b = _tentative(db, 75000, T0 + timedelta(minutes=1))
    _garanti(db, "750,00", T0 + timedelta(minutes=2))
    ledger.ingest_mail(db, now=T0 + timedelta(minutes=3))
    db.refresh(a)
    db.refresh(b)
    assert sorted([a.status, b.status]) == ["confirmed", "tentative"]


def test_cash_is_apart_and_never_matched_to_a_bank_mail(db) -> None:
    cash = _tentative(db, 75000, T0, method="cash")
    _garanti(db, "750,00", T0 + timedelta(minutes=3))
    result = ledger.ingest_mail(db, now=T0 + timedelta(minutes=4))
    assert result["matched"] == 0 and result["booked"] == 1
    db.refresh(cash)
    assert cash.status == service.STATUS_TENTATIVE and cash.bank_ref is None
    summary = service.spent(db, category=None, now=T0 + timedelta(hours=1))
    assert summary.total_kurus == 150000 and summary.cash_kurus == 75000


def test_a_cancelled_spend_is_neither_counted_nor_matched(db) -> None:
    spend = _tentative(db, 75000, T0)
    assert service.cancel(db, spend.id, now=T0) is not None
    _garanti(db, "750,00", T0 + timedelta(minutes=3))
    result = ledger.ingest_mail(db, now=T0 + timedelta(minutes=4))
    assert result["matched"] == 0 and result["booked"] == 1
    assert service.spent(db, category=None, now=T0 + timedelta(hours=1)).count == 1


# ------------------------------------------------------------------ (4) the questions he asks


def test_this_month_by_category(db) -> None:
    _garanti(db, "100,00", T0 - timedelta(days=2), merchant="A101 KADIKOY")
    _garanti(db, "50,00", T0 - timedelta(days=1), merchant="SHELL MODA")
    _garanti(db, "300,00", T0 - timedelta(days=10), merchant="MIGROS ATASEHIR")  # last month
    ledger.ingest_mail(db, now=T0)
    market = service.spent(db, category="market", now=T0)
    assert market.total_kurus == 10000 and market.count == 1
    said = service.spent_speech(market)
    assert "markete" in said and "100 TL" in said
    assert service.spent(db, category=None, now=T0).total_kurus == 15000


@pytest.mark.parametrize(
    ("sentence", "intent"),
    [
        ("Hesabımda ne kadar var?", Intent.MONEY_BALANCE),
        ("Bakiyem ne kadar?", Intent.MONEY_BALANCE),
        ("Bu ay markete ne harcadım?", Intent.MONEY_SPENT),
        ("Bu ay ne kadar harcadım?", Intent.MONEY_SPENT),
        ("Harcamayı geri al", Intent.MONEY_UNDO),
        ("Markete iki yüz lira nakit verdim", Intent.MONEY_CASH),
        ("Evet harcadım", Intent.MONEY_SPEND_YES),
        ("Hayır harcamadım", Intent.MONEY_SPEND_NO),
    ],
)
def test_the_router_names_the_money_intent(sentence, intent) -> None:
    assert resolve_intent(sentence).intent == intent


def test_the_router_maps_the_money_intents_to_tools() -> None:
    assert QUERY_TOOL_BY_INTENT[Intent.MONEY_BALANCE] == "money.balance"
    assert QUERY_TOOL_BY_INTENT[Intent.MONEY_SPENT] == "money.spent"
    assert CAPABILITY_BY_INTENT[Intent.MONEY_SPEND_YES] == "money.spend_yes"
    assert CAPABILITY_BY_INTENT[Intent.MONEY_SPEND_NO] == "money.spend_no"
    assert CAPABILITY_BY_INTENT[Intent.MONEY_UNDO] == "money.undo"
    assert CAPABILITY_BY_INTENT[Intent.MONEY_CASH] == "money.cash"


def test_the_router_carries_the_amount_and_the_category() -> None:
    spent = resolve_intent("Bu ay markete ne harcadım?")
    assert spent.money_category == "market"
    cash = resolve_intent("Markete iki yüz lira nakit verdim")
    assert cash.money_amount_kurus == 20000 and cash.money_category == "market"


def test_bare_answers_are_money_only_while_his_question_is_open() -> None:
    assert resolve_intent("Evet").intent != Intent.MONEY_SPEND_YES
    assert resolve_intent("Geri al").intent != Intent.MONEY_UNDO
    pending.note_question(datetime.now(UTC))
    assert resolve_intent("Evet").intent == Intent.MONEY_SPEND_YES
    assert resolve_intent("Hayır").intent == Intent.MONEY_SPEND_NO
    yes_but = resolve_intent("Evet ama 750")
    assert yes_but.intent == Intent.MONEY_SPEND_YES and yes_but.money_amount_kurus == 75000
    # Something else waiting for a yes keeps it.
    assert resolve_intent("Evet", draft_pending=True).intent != Intent.MONEY_SPEND_YES
    pending.note_booking(datetime.now(UTC))
    assert resolve_intent("Geri al").intent == Intent.MONEY_UNDO
    assert resolve_intent("Geri al", document_focused=True).intent != Intent.MONEY_UNDO


def test_an_old_question_closes(db) -> None:
    pending.note_question(datetime.now(UTC) - timedelta(minutes=pending.OPEN_MINUTES + 1))
    assert resolve_intent("Evet").intent != Intent.MONEY_SPEND_YES


@pytest.mark.parametrize(
    "sentence",
    [
        "Ne kadar sürer?",
        "Hesabımda kaç mail var?",
        "Saat kaç?",
        "Evet",
        "Bu ay hava nasıl olacak?",
        "Ahmet'e iki yüz lira gönder",
    ],
)
def test_the_router_leaves_the_neighbours_alone(sentence) -> None:
    assert not resolve_intent(sentence).intent.value.startswith("money_")


# ------------------------------------------------------------------ (5) the voice tools


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


def test_the_tools_are_registered() -> None:
    assert set(tools_money.MONEY_TOOL_NAMES) <= set(default_registry().names())


def test_the_tool_argument_keys_avoid_the_relay_s_filtered_words() -> None:
    manifest = {entry["name"]: entry for entry in default_registry().manifest()}
    for name in tools_money.MONEY_TOOL_NAMES:
        keys = manifest[name]["parameters"].get("properties", {})
        for key in keys:
            assert not any(bad in key for bad in ("text", "audio", "token")), (name, key)


def test_balance_and_spent_tools_say_the_ledger(db) -> None:
    _garanti(db, "100,00", T0 - timedelta(days=1), merchant="A101 KADIKOY")
    ledger.ingest_mail(db, now=T0)
    said = tools_money.money_spent(_ctx(db, {"money_category": "market"}), {})
    assert said["status"] == "ok" and said["total_kurus"] == 10000
    assert "markete" in said["speech"]
    assert "bilmiyorum" in tools_money.money_balance(_ctx(db), {})["speech"]


def test_cash_tool_books_cash_apart(db) -> None:
    result = tools_money.money_cash(
        _ctx(db, {"money_amount_kurus": 20000, "money_category": "market"}), {}
    )
    assert result["status"] == "ok"
    row = db.scalars(select(MoneyEntry)).one()
    assert row.method == "cash" and row.amount_kurus == 20000 and row.category == "market"
    assert row.status == service.STATUS_CONFIRMED


def test_cash_without_an_amount_is_a_question(db) -> None:
    result = tools_money.money_cash(_ctx(db), {})
    assert result["status"] == "needs_clarification" and result["speech"]
    assert db.scalars(select(MoneyEntry)).all() == []


def test_undo_takes_back_the_last_tentative_spend(db) -> None:
    spend = _tentative(db, 75000, T0)
    result = tools_money.money_undo(_ctx(db, now=T0 + timedelta(minutes=1)), {})
    assert result["status"] == "ok"
    db.refresh(spend)
    assert spend.status == service.STATUS_CANCELLED


def test_undo_with_nothing_recent_says_so(db) -> None:
    _tentative(db, 75000, T0 - timedelta(hours=2))
    result = tools_money.money_undo(_ctx(db), {})
    assert result["status"] == "nothing_to_undo"


# ------------------------------------------------------------------ (6) never the bank


_NETWORK_MODULES = {"httpx", "requests", "urllib", "urllib3", "aiohttp", "socket", "http"}


def test_the_money_code_imports_no_network_client() -> None:
    files = sorted((API / "app" / "money").glob("*.py")) + [
        API / "app" / "conversations" / "spend.py",
        API / "app" / "voice" / "realtime_sessions" / "tools_money.py",
    ]
    assert len(files) >= 6
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in _NETWORK_MODULES, (path.name, name)


def test_no_bank_host_is_ever_contacted(db, monkeypatch) -> None:
    attempts: list[object] = []

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        attempts.append(args[:2])
        raise AssertionError("money ledger opened a connection")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    _tentative(db, 75000, T0)
    _garanti(db, "750,00", T0 + timedelta(minutes=3))
    _mail(
        db,
        sender="bildirim@yapikredi.com.tr",
        subject="Bakiye",
        body="Vadesiz hesabınızın güncel bakiyesi 2.500,00 TL'dir.",
        at=T0,
    )
    ledger.ingest_mail(db, now=T0 + timedelta(minutes=5))
    service.balance_speech(db, now=T0)
    service.spent(db, category="market", now=T0)
    tools_money.money_balance(_ctx(db), {})
    tools_money.money_spent(_ctx(db), {})
    tools_money.money_undo(_ctx(db), {})
    assert attempts == []


# ------------------------------------------------------------------ (7) the mail poll hook


class _FakeMail:
    def poll(self, db, *, now):  # noqa: ANN001, ANN202
        return {"status": "polled", "new": 1, "new_unread": 1, "seen": 1}


def test_the_poller_hands_each_successful_poll_to_the_ledger(db) -> None:
    seen: list[datetime] = []
    poller = MailPoller(
        _FakeMail(), enabled=True, interval_s=60, after_poll=lambda s, now: seen.append(now)
    )
    poller.tick(db, now=T0)
    assert seen == [T0]
    poller.tick(db, now=T0 + timedelta(seconds=10))  # not due: no poll, no hook
    assert seen == [T0]


def test_a_failing_ledger_hook_never_breaks_the_poll(db) -> None:
    def boom(session, now):  # noqa: ANN001, ANN202
        raise RuntimeError("x")

    poller = MailPoller(_FakeMail(), enabled=True, interval_s=60, after_poll=boom)
    assert poller.tick(db, now=T0)["status"] == "polled"


# ------------------------------------------------------------------ (8) /v1/money


@pytest.fixture()
def api(engine):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    return app, client


def test_the_routes_read_the_ledger_and_cancel_and_book_cash(api, factory) -> None:
    _, client = api
    with factory() as session:
        spend = _tentative(session, 75000, datetime.now(UTC))
    body = client.get("/v1/money").json()
    assert body["entries"][0]["amount_kurus"] == 75000
    assert body["entries"][0]["status"] == "tentative"
    assert "bilmiyorum" in body["balance_speech"]
    cancelled = client.post(f"/v1/money/entries/{spend.id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["entry"]["status"] == "cancelled"
    assert client.post(f"/v1/money/entries/{uuid.uuid4()}/cancel").status_code == 404
    cash = client.post("/v1/money/cash", json={"amount": "200,00", "category": "market"})
    assert cash.status_code == 200, cash.text
    assert cash.json()["entry"]["method"] == "cash"
    assert client.post("/v1/money/cash", json={"amount": "yok"}).status_code == 422


def test_the_health_page_shows_the_spend_loop(api) -> None:
    app, client = api
    assert hasattr(app.state, "money_spend_loop")
    assert "money_spend_loop" in client.get("/v1/system/health").json()["checks"]
