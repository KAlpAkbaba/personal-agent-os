"""``/v1/household/list`` and ``/items`` refuse what cannot be a quantity or an item name.

Test team finding (round t-manual-20261006e, staging 72884b71, tester-2): '-3 paket' and '0'
were kept as quantities, a long Turkish sentence was kept as an item name, and 'sütü' did not
land on the existing 'Süt'. A quantity is a positive amount; a name is a short noun phrase; a
name's case and plural endings fold to the one key the voice path uses (``parse.item_key``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.household import parse
from app.household.models import HouseholdEvent, HouseholdItem
from app.main import create_app
from app.notifications.models import NotificationRow
from tests.identity_support import authenticate, install_identity


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (HouseholdItem.__table__, HouseholdEvent.__table__, NotificationRow.__table__):
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    yield test_client
    engine.dispose()


def _refused(response) -> str:
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "household_refused"
    return detail["message"]


def _items(client: TestClient) -> list[dict]:
    return client.get("/v1/household").json()["items"]


# ------------------------------------------------------------------ (1) the quantity


@pytest.mark.parametrize(
    "quantity",
    ["-3 paket", "-3", "0", "0 paket", "0,0 litre", "sıfır", "−2 kilo", "paket", "çok", "3 4"],
)
def test_a_quantity_that_is_not_a_positive_amount_is_refused(client, quantity) -> None:
    body = {"name": "süt", "quantity": quantity}
    message = _refused(client.post("/v1/household/list", json=body))
    assert "Miktar" in message
    assert _items(client) == []  # nothing was kept


@pytest.mark.parametrize(
    "quantity",
    ["iki paket", "3 litre", "1,5 litre", "500 gr", "2kg", "on iki tane", "yarım kilo", "4", "bir"],
)
def test_a_positive_amount_is_kept(client, quantity) -> None:
    added = client.post("/v1/household/list", json={"name": "süt", "quantity": quantity})
    assert added.status_code == 200, added.text
    assert added.json()["item"]["list_quantity"] == quantity


def test_no_quantity_is_still_fine(client) -> None:
    assert client.post("/v1/household/list", json={"name": "süt"}).status_code == 200


# ------------------------------------------------------------------ (2) the name


LONG_SENTENCE = "Yarın akşam misafir gelecek o yüzden biraz meyve almam gerekiyor"


@pytest.mark.parametrize(
    "name",
    [
        LONG_SENTENCE,
        "bir iki üç dört beş",  # more than four words
        "süper " + "konsantre" * 4 + " deterjan",  # over 40 characters
        "süt bitti",  # a level sentence
        "markete gidiyorum",  # a sentence with a verb ending
        "süt almayı unuttum",
        "ekmek alacağım",
    ],
)
def test_a_sentence_is_not_an_item_name(client, name) -> None:
    for path, body in (
        ("/v1/household/list", {"name": name}),
        ("/v1/household/items", {"name": name, "level": "bitti"}),
    ):
        message = _refused(client.post(path, json=body))
        assert message == parse.NAME_NOT_UNDERSTOOD
    assert _items(client) == []


@pytest.mark.parametrize(
    "name",
    [
        "süt",
        "tuvalet kağıdı",
        "kuru yemiş",  # ends like a past participle (-miş): still a good
        "enerji içeceği",  # ends like a future (-ecek): still a good
        "plastik poşet",
        "bulaşık makinesi tableti",
        "lazer toner",
    ],
)
def test_a_short_noun_phrase_is_kept(client, name) -> None:
    added = client.post("/v1/household/list", json={"name": name})
    assert added.status_code == 200, added.text


# ------------------------------------------------------------------ (3) the suffixes


@pytest.mark.parametrize("first", ["Süt", "süt"])
def test_suffixed_forms_land_on_the_one_row(client, first) -> None:
    created = client.post("/v1/household/list", json={"name": first, "quantity": "iki paket"})
    assert created.status_code == 200, created.text
    item_id = created.json()["item"]["id"]
    for said in ("sütü", "Sütü", "sütleri", "SÜTÜ", "sütü.", "sütün", "süt'ün"):
        landed = client.post("/v1/household/items", json={"name": said, "level": "azaldı"})
        assert landed.status_code == 200, landed.text
        assert landed.json()["item"]["id"] == item_id, said
        again = client.post("/v1/household/list", json={"name": said})
        assert again.json()["item"]["id"] == item_id, said
    rows = _items(client)
    assert [r["name"] for r in rows] == ["süt"]


def test_suffixed_unknown_item_lands_on_its_row(client) -> None:
    """A word outside the vocabulary folds the same way: 'kalemleri' is 'kalem'."""
    first = client.post("/v1/household/list", json={"name": "kalem"}).json()["item"]["id"]
    for said in ("kalemi", "kalemleri", "kalemin"):
        assert client.post("/v1/household/list", json={"name": said}).json()["item"]["id"] == first
    assert len(_items(client)) == 1


def test_the_fold_is_the_voice_parser_s() -> None:
    keys = {parse.item_key(w) for w in ("sütü", "sütleri", "Süt", "sütün", "sütlerin")}
    assert keys == {"sut"}
    # The genitive cut must not merge two vocabulary items or eat a known word.
    assert len({parse.item_key(w) for w in ("un", "unu", "unun")}) == 1
    assert parse.item_key("zeytin") == "zeytin" and parse.item_key("sabun") == "sabun"
