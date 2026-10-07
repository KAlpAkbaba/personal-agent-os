"""An item keeps the name the owner said: 'taze süt' is not 'taze sütü', and 'iki şişe süt' is
süt with an amount.

Test team round t-r10070152 (staging da3e26b9, tester-4, ev-stoku), re-run on main b1f8ef94:

* POST /v1/household/list {"name": "taze süt"} stored "taze sütü" and POST /items
  {"name": "esmer şeker"} stored "esmer şekeri": the compound (possessive) head form of
  "kedi MAMASI" was put on an adjective + noun. Said in the nominative, the head stays so;
  after a known adjective ("taze sütü listeye") it is the accusative, folded back.
* {"name": "iki şişe süt"} made one item called "iki şişe sütü". It is süt, two bottles.
* A deeply nested JSON body answered 500 (``json.loads`` raises RecursionError, not
  ValueError) - the body reader is ``routes.py``, outside this card's area: strict xfail.
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
    test_client = TestClient(app, raise_server_exceptions=False)
    authenticate(app, test_client, settings=settings)
    yield test_client
    engine.dispose()


def _items(client: TestClient) -> list[dict]:
    return client.get("/v1/household").json()["items"]


# ------------------------------------------------------------------ an adjective is the name


@pytest.mark.parametrize(
    ("said", "kept"),
    [
        ("taze süt", "taze süt"),
        ("Taze Süt", "taze süt"),
        ("esmer şeker", "esmer şeker"),
        ("tam yağlı süt", "tam yağlı süt"),
        ("taze sütü", "taze süt"),
    ],
)
def test_an_adjective_and_its_noun_are_kept_as_said_on_the_list(client, said, kept) -> None:
    answer = client.post("/v1/household/list", json={"name": said})
    assert answer.status_code == 200, answer.text
    assert answer.json()["item"]["name"] == kept
    assert [i["name"] for i in _items(client)] == [kept]


def test_esmer_seker_keeps_its_name_when_its_level_is_said(client) -> None:
    answer = client.post("/v1/household/items", json={"name": "esmer şeker", "level": "azaldı"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["item"]["name"] == "esmer şeker"


@pytest.mark.parametrize(
    ("said", "kept"),
    [
        ("kedi mamasını", "kedi maması"),
        ("portakal suyu", "portakal suyu"),
        ("bulaşık deterjanını", "bulaşık deterjanı"),
    ],
)
def test_a_noun_compound_still_takes_its_compound_head(said, kept) -> None:
    assert parse.display_name(parse.turkish_lower(said).split()) == kept


def test_the_voice_path_keeps_the_adjective_too() -> None:
    command = parse.parse_sentence("taze süt bitti")
    assert command is not None and command.item == "taze süt"


# ------------------------------------------------------------------ an amount said in the name


@pytest.mark.parametrize(
    ("said", "amount"),
    [("iki şişe süt", "iki şişe"), ("iki sise sut", "iki sise"), ("2 litre süt", "2 litre")],
)
def test_an_amount_said_in_the_name_is_one_item_with_that_amount(client, said, amount) -> None:
    answer = client.post("/v1/household/list", json={"name": said})
    assert answer.status_code == 200, answer.text
    item = answer.json()["item"]
    assert (item["name"], item["list_quantity"]) == ("süt", amount)
    again = client.post("/v1/household/list", json={"name": "süt"})
    assert again.json()["already"] is True
    assert [(i["name"], i["list_quantity"]) for i in _items(client)] == [("süt", amount)]


def test_a_given_quantity_wins_over_the_amount_in_the_name(client) -> None:
    body = {"name": "iki şişe süt", "quantity": "3 litre"}
    item = client.post("/v1/household/list", json=body).json()["item"]
    assert (item["name"], item["list_quantity"]) == ("süt", "3 litre")


def test_a_level_said_with_an_amount_lands_on_the_item(client) -> None:
    client.post("/v1/household/list", json={"name": "süt"})
    answer = client.post("/v1/household/items", json={"name": "iki şişe süt", "level": "bitti"})
    assert answer.status_code == 200, answer.text
    assert [(i["name"], i["level"]) for i in _items(client)] == [("süt", "bitti")]


def test_a_zero_amount_in_the_name_is_refused(client) -> None:
    answer = client.post("/v1/household/list", json={"name": "0 litre süt"})
    assert answer.status_code == 422, answer.text
    assert answer.json()["detail"]["code"] == "household_refused"
    assert _items(client) == []


def test_a_count_without_a_unit_is_the_amount(client) -> None:
    answer = client.post("/v1/household/list", json={"name": "on yumurta"})
    item = answer.json()["item"]
    assert (item["name"], item["list_quantity"]) == ("yumurta", "on")


def test_a_name_that_is_only_an_amount_is_not_split() -> None:
    assert parse.split_amount("iki şişe") == ("iki şişe", None)
    assert parse.split_amount("süt") == ("süt", None)


# ------------------------------------------------------------------ raw bodies (routes.py)


@pytest.mark.xfail(
    strict=True,
    reason="routes._payload catches ValueError only; json.loads raises RecursionError on a "
    "deep body -> 500. routes.py is outside household-forget-race-and-names (ALAN_ISTEGI).",
)
@pytest.mark.parametrize("path", ["/v1/household/list", "/v1/household/items"])
def test_a_deeply_nested_body_is_body_invalid_not_500(client, path) -> None:
    deep = "[" * 100_000 + "]" * 100_000
    answer = client.post(path, content=deep, headers={"Content-Type": "application/json"})
    assert answer.status_code == 422, answer.status_code
    assert answer.json()["detail"]["code"] == "body_invalid"
