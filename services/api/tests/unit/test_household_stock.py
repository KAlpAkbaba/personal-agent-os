"""The house's stock and the shopping list (home-stock-list), on SQLite and through ``create_app``.

What the owner says ("tuvalet kağıdı azaldı", "listeye süt ekle", "ne almam lazım"), what the
router makes of it (anchored words, never a stem that swallows a neighbour), what the store
keeps (a level per item, a list flag, two kinds of dated events), the rhythm learnt from those
events under an injected clock, the reminder it leads to (once per cycle), the voice tools and
``/v1/household``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.household import parse, reminders, service
from app.household.models import HouseholdEvent, HouseholdItem
from app.main import create_app
from app.notifications.models import NotificationRow
from app.voice.intents import CAPABILITY_BY_INTENT, QUERY_TOOL_BY_INTENT, Intent, resolve_intent
from app.voice.realtime_sessions import tools_household
from app.voice.realtime_sessions.tools import ToolContext, default_registry, terminal_status_for
from tests.identity_support import authenticate, install_identity

DAY0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (HouseholdItem.__table__, HouseholdEvent.__table__, NotificationRow.__table__):
        table.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(factory):
    with factory() as session:
        yield session


# ------------------------------------------------------------------ (1) the words


@pytest.mark.parametrize(
    ("sentence", "action", "item", "level", "quantity"),
    [
        ("Tuvalet kağıdı azaldı.", "level", "tuvalet kağıdı", "azaldı", None),
        ("tuvalet kagidi azaldi", "level", "tuvalet kağıdı", "azaldı", None),
        ("Bulaşık deterjanı bitmek üzere", "level", "bulaşık deterjanı", "azaldı", None),
        ("Deterjan bitti", "level", "deterjan", "bitti", None),
        ("Evde yumurta kalmadı", "level", "yumurta", "bitti", None),
        ("Süt bitmiş", "level", "süt", "bitti", None),
        ("Süt aldım", "level", "süt", "var", None),
        ("Ekmek aldık", "level", "ekmek", "var", None),
        ("Evde mercimek tükendi", "level", "mercimek", "bitti", None),
        ("Evde lazer toner bitti", "level", "lazer toner", "bitti", None),
        ("Listeye süt ekle", "add", "süt", None, None),
        ("Süt'ü listeye ekle", "add", "süt", None, None),
        ("Listeye iki paket makarna yaz", "add", "makarna", None, "iki paket"),
        ("Alışveriş listesine 3 litre su ekler misin", "add", "su", None, "3 litre"),
        ("Kedi mamasını listeye ekle", "add", "kedi maması", None, None),
        ("Listeden sütü çıkar", "remove", "süt", None, None),
        ("Çayı listeden sil", "remove", "çay", None, None),
        ("Ne almam lazım?", "read", None, None, None),
        ("Neler almam gerekiyor", "read", None, None, None),
        ("Listeyi oku", "read", None, None, None),
        ("Alışveriş listesini oku", "read", None, None, None),
        ("Listede ne var", "read", None, None, None),
        ("Markete gidiyorum", "read", None, None, None),
        ("Evde ne eksik", "read", None, None, None),
    ],
)
def test_the_owner_s_sentences_parse(sentence, action, item, level, quantity) -> None:
    command = parse.parse_sentence(sentence)
    assert command is not None, sentence
    assert (command.action, command.item, command.level, command.quantity) == (
        action,
        item,
        level,
        quantity,
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "Toplantı bitti",  # a meeting, not a stock
        "Mesajını aldım",  # a message
        "Not aldım",
        "Telefonun şarjı azaldı",
        "Film bitti mi?",
        "Süt bitti mi?",  # a question, not an update
        "Bitti",  # nothing named
        "Listeyi kaydet",
        "Bir liste oluştur",  # the artifact factory's dataset
        "Ne aldın?",
        "Markete ne zaman gidelim",
        "Beni unutma",
    ],
)
def test_the_neighbours_do_not_parse(sentence) -> None:
    assert parse.parse_sentence(sentence) is None


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("tuvalet kağıdı", "Tuvalet kağıdını"),
        ("tuvalet kağıdı", "tuvalet kagidi"),
        ("süt", "sütü"),
        ("su", "suyu"),
        ("çay", "çayı"),
        ("un", "unu"),
        ("ekmek", "ekmeği"),
        ("kedi maması", "kedi mamasını"),
        ("yumurta", "yumurtaları"),
    ],
)
def test_one_item_under_its_suffixes(a, b) -> None:
    assert parse.item_key(a) == parse.item_key(b)


def test_different_items_keep_different_keys() -> None:
    keys = {parse.item_key(w) for w in ("su", "süt", "sure", "çay", "un", "yağ", "yağmur")}
    assert len(keys) == 7


# ------------------------------------------------------------------ (2) the store


def test_a_low_item_goes_on_the_list_and_is_read_back(db) -> None:
    change = service.set_level(db, "tuvalet kağıdı", "azaldı", now=DAY0)
    assert change.item.level == "azaldı" and change.item.on_list is True
    assert "tuvalet kağıdı" in change.speech
    listed = service.shopping_list(db, now=DAY0)
    assert [i.name for i in listed.items] == ["tuvalet kağıdı"]
    speech = service.list_speech(listed)
    assert "tuvalet kağıdı" in speech and "azaldı" in speech


def test_bought_takes_it_off_the_list_and_sets_it_full(db) -> None:
    service.set_level(db, "süt", "bitti", now=DAY0)
    change = service.set_level(db, "sütü", "var", now=DAY0 + timedelta(hours=3))
    assert change.item.level == "var" and change.item.on_list is False
    assert service.shopping_list(db, now=DAY0).items == []
    assert db.scalars(select(HouseholdItem)).one().name == "süt"  # one row, not two


def test_list_add_remove_and_quantity(db) -> None:
    added = service.add_to_list(db, "makarna", quantity="iki paket", now=DAY0)
    assert added.item.on_list and added.item.list_quantity == "iki paket"
    again = service.add_to_list(db, "makarnayı", quantity=None, now=DAY0)
    assert again.already is True
    assert "iki paket makarna" in service.list_speech(service.shopping_list(db, now=DAY0))
    removed = service.remove_from_list(db, "makarnayı", now=DAY0)
    assert removed is not None and removed.item.on_list is False
    assert service.remove_from_list(db, "süt", now=DAY0) is None
    assert service.list_speech(service.shopping_list(db, now=DAY0)) == service.SPEECH_LIST_EMPTY


def test_the_list_reads_in_order_with_and(db) -> None:
    service.set_level(db, "deterjan", "bitti", now=DAY0)
    service.set_level(db, "tuvalet kağıdı", "azaldı", now=DAY0)
    service.add_to_list(db, "ekmek", quantity=None, now=DAY0)
    speech = service.list_speech(service.shopping_list(db, now=DAY0))
    assert speech.startswith("Listede 3 şey var:")
    assert speech.index("deterjan") < speech.index("tuvalet kağıdı") < speech.index("ekmek")
    assert " ve ekmek" in speech


@pytest.mark.parametrize("bad", ["", "   ", "a\x00b", "x" * 61, "!!!", "12"])
def test_a_name_that_cannot_be_an_item_is_refused(db, bad) -> None:
    with pytest.raises(service.HouseholdRefused):
        service.add_to_list(db, bad, quantity=None, now=DAY0)


def test_the_level_must_be_one_of_three(db) -> None:
    with pytest.raises(service.HouseholdRefused):
        service.set_level(db, "süt", "yarım", now=DAY0)


def test_the_item_count_is_bounded(db, monkeypatch) -> None:
    monkeypatch.setattr(service, "MAX_ITEMS", 2)
    service.add_to_list(db, "süt", quantity=None, now=DAY0)
    service.add_to_list(db, "çay", quantity=None, now=DAY0)
    with pytest.raises(service.HouseholdRefused):
        service.add_to_list(db, "un", quantity=None, now=DAY0)


# ------------------------------------------------------------------ (3) the rhythm


def _cycle(db, name: str, starts: list[datetime]) -> None:
    """Each start: the item runs low, and is bought back the next day."""
    for at in starts:
        service.set_level(db, name, "bitti", now=at)
        service.set_level(db, name, "var", now=at + timedelta(days=1))


def test_the_rhythm_is_learnt_from_the_depletions(db) -> None:
    _cycle(db, "tuvalet kağıdı", [DAY0, DAY0 + timedelta(days=20), DAY0 + timedelta(days=42)])
    item = service.find_item(db, "tuvalet kağıdı")
    assert item is not None
    assert item.cycle_days == pytest.approx(21.0)


def test_one_gap_is_not_a_rhythm(db) -> None:
    _cycle(db, "süt", [DAY0, DAY0 + timedelta(days=5)])
    assert service.find_item(db, "süt").cycle_days is None


def test_azaldi_then_bitti_is_one_depletion(db) -> None:
    for start in (DAY0, DAY0 + timedelta(days=10), DAY0 + timedelta(days=20)):
        service.set_level(db, "çay", "azaldı", now=start)
        service.set_level(db, "çay", "bitti", now=start + timedelta(days=2))
        service.set_level(db, "çay", "var", now=start + timedelta(days=3))
    item = service.find_item(db, "çay")
    assert item.cycle_days == pytest.approx(10.0)
    kinds = [e.kind for e in db.scalars(select(HouseholdEvent).order_by(HouseholdEvent.at))]
    assert kinds.count("depleted") == 3


def test_recomputing_the_rhythm_twice_changes_nothing(db) -> None:
    _cycle(db, "deterjan", [DAY0, DAY0 + timedelta(days=30), DAY0 + timedelta(days=58)])
    item = service.find_item(db, "deterjan")
    first = item.cycle_days
    service.recompute_cycle(db, item)
    service.recompute_cycle(db, item)
    assert item.cycle_days == first == pytest.approx(29.0)


def test_the_reminder_comes_a_few_days_before_and_only_once(db) -> None:
    starts = [DAY0, DAY0 + timedelta(days=21), DAY0 + timedelta(days=42)]
    _cycle(db, "tuvalet kağıdı", starts)
    predicted = starts[-1] + timedelta(days=21)
    lead = service.lead_days(21.0)
    assert lead == 3
    early = predicted - timedelta(days=lead, hours=1)
    assert service.due_reminders(db, now=early) == []
    due_at = predicted - timedelta(days=lead) + timedelta(hours=1)
    due = service.due_reminders(db, now=due_at)
    assert [i.name for i in due] == ["tuvalet kağıdı"]
    sent = reminders.remind_due(db, now=due_at)
    assert sent == 1
    assert reminders.remind_due(db, now=due_at + timedelta(hours=6)) == 0  # once per cycle
    note = db.scalars(select(NotificationRow)).one()
    assert note.kind == "household.reminder"
    assert "tuvalet kağıdı" in note.body.lower() and "21" in note.body
    # The list read says it too, without putting it on the list.
    listed = service.shopping_list(db, now=due_at)
    assert listed.items == [] and [i.name for i in listed.soon] == ["tuvalet kağıdı"]
    assert "yakında" in service.list_speech(listed).lower()


def test_the_next_cycle_reminds_again(db) -> None:
    starts = [DAY0 + timedelta(days=21 * k) for k in range(3)]
    _cycle(db, "süt", starts)
    first = starts[-1] + timedelta(days=19)
    assert reminders.remind_due(db, now=first) == 1
    _cycle(db, "süt", [starts[-1] + timedelta(days=21)])
    second = starts[-1] + timedelta(days=21 + 19)
    assert reminders.remind_due(db, now=second) == 1


def test_no_reminder_while_it_is_already_on_the_list_or_low(db) -> None:
    starts = [DAY0 + timedelta(days=21 * k) for k in range(3)]
    _cycle(db, "kahve", starts)
    due_at = starts[-1] + timedelta(days=20)
    service.add_to_list(db, "kahve", quantity=None, now=due_at - timedelta(days=2))
    assert service.due_reminders(db, now=due_at) == []
    service.remove_from_list(db, "kahve", now=due_at)
    service.set_level(db, "kahve", "azaldı", now=due_at)
    assert service.due_reminders(db, now=due_at) == []


def test_a_long_overdue_rhythm_does_not_nag(db) -> None:
    starts = [DAY0 + timedelta(days=7 * k) for k in range(3)]
    _cycle(db, "ekmek", starts)
    stale = starts[-1] + timedelta(days=7 * 3)
    assert service.due_reminders(db, now=stale) == []


def test_the_reminder_loop_counts_its_passes(factory) -> None:
    with factory() as db:
        _cycle(db, "süt", [DAY0 + timedelta(days=10 * k) for k in range(3)])
    moment = DAY0 + timedelta(days=29)
    loop = reminders.ReminderLoop(factory, clock=lambda: moment)
    assert loop.remind_once() == 1
    assert loop.remind_once() == 0
    health = loop.health_check()
    assert health["last_sent"] == 0


# ------------------------------------------------------------------ (4) the router


@pytest.mark.parametrize(
    ("sentence", "intent", "tool"),
    [
        ("Tuvalet kağıdı azaldı", Intent.HOUSEHOLD_LEVEL, "household.level"),
        ("Deterjan bitti", Intent.HOUSEHOLD_LEVEL, "household.level"),
        ("Süt aldım", Intent.HOUSEHOLD_LEVEL, "household.level"),
        ("Listeye süt ekle", Intent.HOUSEHOLD_LIST_ADD, "household.list_add"),
        ("Listeye iki paket makarna yaz", Intent.HOUSEHOLD_LIST_ADD, "household.list_add"),
        ("Listeden sütü çıkar", Intent.HOUSEHOLD_LIST_REMOVE, "household.list_remove"),
        ("Ne almam lazım?", Intent.HOUSEHOLD_LIST_READ, "household.list_read"),
        ("Listeyi oku", Intent.HOUSEHOLD_LIST_READ, "household.list_read"),
        ("Markete gidiyorum", Intent.HOUSEHOLD_LIST_READ, "household.list_read"),
    ],
)
def test_the_router_names_the_household_tool(sentence, intent, tool) -> None:
    resolved = resolve_intent(sentence)
    assert resolved.intent is intent
    assert (CAPABILITY_BY_INTENT.get(intent) or QUERY_TOOL_BY_INTENT.get(intent)) == tool


def test_the_router_carries_the_owner_s_words() -> None:
    resolved = resolve_intent("Listeye iki paket makarna yaz")
    assert (resolved.household_item, resolved.household_quantity) == ("makarna", "iki paket")
    level = resolve_intent("Tuvalet kağıdı azaldı")
    assert (level.household_item, level.household_level) == ("tuvalet kağıdı", "azaldı")
    assert resolve_intent("Ne almam lazım").household_item is None


@pytest.mark.parametrize(
    "sentence", ["Toplantı bitti", "Mesajını aldım", "Telefonun şarjı azaldı", "Süt bitti mi?"]
)
def test_the_router_leaves_the_neighbours_alone(sentence) -> None:
    assert resolve_intent(sentence).intent not in (
        Intent.HOUSEHOLD_LEVEL,
        Intent.HOUSEHOLD_LIST_ADD,
        Intent.HOUSEHOLD_LIST_REMOVE,
        Intent.HOUSEHOLD_LIST_READ,
    )


def test_read_is_a_query_and_the_three_writes_are_actions() -> None:
    assert Intent.HOUSEHOLD_LIST_READ in QUERY_TOOL_BY_INTENT
    for intent in (Intent.HOUSEHOLD_LEVEL, Intent.HOUSEHOLD_LIST_ADD, Intent.HOUSEHOLD_LIST_REMOVE):
        assert intent in CAPABILITY_BY_INTENT


# ------------------------------------------------------------------ (5) the voice tools


def _ctx(db, turn: dict | None = None) -> ToolContext:
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={"last_utterance": turn or {}},
        db=db,
        now=DAY0,
    )


def test_the_tools_are_registered() -> None:
    names = set(default_registry().names())
    assert set(tools_household.HOUSEHOLD_TOOL_NAMES) <= names


def test_the_owner_s_words_win_over_the_model_s_argument(db) -> None:
    turn = {"household_item": "tuvalet kağıdı", "household_level": "azaldı"}
    result = tools_household.household_level(_ctx(db, turn), {"item": "şampuan", "level": "bitti"})
    assert result["status"] == "ok"
    assert result["item"] == "tuvalet kağıdı" and result["level"] == "azaldı"
    assert service.find_item(db, "şampuan") is None


def test_the_model_s_argument_is_used_when_the_router_named_nothing(db) -> None:
    result = tools_household.household_list_add(_ctx(db), {"item": "Süt", "quantity": "2 litre"})
    assert result["status"] == "ok" and result["quantity"] == "2 litre"
    read = tools_household.household_list_read(_ctx(db), {})
    assert "2 litre süt" in read["speech"].lower()
    removed = tools_household.household_list_remove(_ctx(db), {"item": "sütü"})
    assert removed["status"] == "ok"
    assert tools_household.household_list_read(_ctx(db), {})["count"] == 0


def test_no_item_is_a_question_not_a_receipt(db) -> None:
    result = tools_household.household_level(_ctx(db), {})
    assert result["status"] == "needs_clarification" and result["speech"]
    status, _ = terminal_status_for("household.level", result)
    assert status == "needs_clarification"
    assert db.scalars(select(HouseholdItem)).all() == []


def test_a_refused_name_is_said_not_raised(db) -> None:
    result = tools_household.household_list_add(_ctx(db), {"item": "\x00"})
    assert result["status"] == "refused" and result["speech"]


# ------------------------------------------------------------------ (6) /v1/household


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


def test_the_routes_set_levels_and_keep_the_list(api) -> None:
    _, client = api
    low = client.post("/v1/household/items", json={"name": "tuvalet kağıdı", "level": "azaldı"})
    assert low.status_code == 200, low.text
    item_id = low.json()["item"]["id"]
    added = client.post("/v1/household/list", json={"name": "makarna", "quantity": "iki paket"})
    assert added.status_code == 200, added.text
    body = client.get("/v1/household").json()
    assert [i["name"] for i in body["list"]] == ["tuvalet kağıdı", "makarna"]
    assert body["list"][1]["list_quantity"] == "iki paket"
    assert body["speech"].startswith("Listede 2 şey var")
    assert client.delete(f"/v1/household/list/{item_id}").json()["item"]["on_list"] is False
    assert client.delete(f"/v1/household/items/{item_id}").json() == {"deleted": 1}
    assert client.delete(f"/v1/household/items/{item_id}").status_code == 404
    assert [i["name"] for i in client.get("/v1/household").json()["items"]] == ["makarna"]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "süt", "level": "yarım"},
        {"name": "", "level": "bitti"},
        {"name": "a\x00b", "level": "bitti"},
        {"name": 5, "level": "bitti"},
        {"name": "süt", "level": None},
    ],
)
def test_the_routes_refuse_in_turkish(api, payload) -> None:
    _, client = api
    refused = client.post("/v1/household/items", json=payload)
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "household_refused"
    assert refused.json()["detail"]["message"]


def test_a_body_that_is_not_json_is_a_422(api) -> None:
    _, client = api
    bad = client.post(
        "/v1/household/list", content=b"{nope", headers={"content-type": "application/json"}
    )
    assert bad.status_code == 422


def test_the_routes_need_the_owner(engine) -> None:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    assert TestClient(app).get("/v1/household").status_code == 401


def test_the_reminder_loop_shows_on_the_health_surface(api) -> None:
    app, client = api
    checks = client.get("/v1/system/health").json()["checks"]
    assert "household_reminders" in checks
    assert app.state.household_reminders is not None
