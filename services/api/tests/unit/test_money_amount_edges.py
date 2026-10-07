"""The money amount's input edges (money-amount-input-edges, the test team's round t-r10070152).

Two improvised para-defteri cases came back wrong on staging da3e26b9:

* ``POST /v1/money/cash {"amount": "<5000 nines>"}`` answered 500: Python's int-from-string
  refuses more than 4300 digits with a ValueError the route never caught (4301 digits: 500 too);
* ``{"amount": "٧٥٠"}`` answered 200 by accident: ``\\d`` and ``int()`` take any Unicode digit.

Decided (ADR draft team/plans/money-amount-input-edges-adr.md): Arabic-Indic and Persian
digits are READ (٧٥٠ -> 750 TL), any other non-ASCII digit is refused, and an amount longer
than a bank ever writes is refused before any int conversion - a Turkish 422, never a 500.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.main import create_app
from app.money import pending
from app.money.amounts import money_in, parse_amount
from app.money.models import MONEY_TABLES
from tests.identity_support import authenticate, install_identity

FIVE_THOUSAND = "9" * 5000
JUST_OVER_INT_LIMIT = "9" * 4301


@pytest.fixture(autouse=True)
def _fresh_pending():
    pending.reset()
    yield
    pending.reset()


# ------------------------------------------------------------------ the reader


@pytest.mark.parametrize(
    ("raw", "kurus"),
    [
        ("٧٥٠", 75000),
        ("٧٥٠ TL", 75000),
        ("١٬٢٣٤,٥٦", None),  # the Arabic group mark is not a Turkish one: refused, not guessed
        ("١.٢٣٤,٥٦", 123456),
        ("۷۵۰", 75000),  # Persian (extended Arabic-Indic) digits
        ("₺٣٠٠", 30000),
    ],
)
def test_arabic_indic_digits_read_as_the_amount(raw, kurus) -> None:
    assert parse_amount(raw) == kurus


@pytest.mark.parametrize("raw", ["７５０", "७५०", "৭৫০", "750٫5"])
def test_other_unicode_digits_are_refused_not_guessed(raw) -> None:
    assert parse_amount(raw) is None


@pytest.mark.parametrize(
    "raw",
    [FIVE_THOUSAND, JUST_OVER_INT_LIMIT, FIVE_THOUSAND + ",00", "1" + ".999" * 1500, "9" * 65],
)
def test_an_absurd_length_is_refused_before_any_int(raw) -> None:
    assert parse_amount(raw) is None


def test_a_long_but_sane_amount_still_reads() -> None:
    # 30 digits still parse; the ledger's own MAX_KURUS refuses it with "fazla büyük".
    assert parse_amount("9" * 30) == int("9" * 30) * 100
    assert parse_amount("99.999.999,99 TL") == 9_999_999_999


def test_a_sentence_with_an_absurd_number_finds_nothing_and_does_not_raise() -> None:
    assert money_in(f"{FIVE_THOUSAND} lira verdim") == []
    assert money_in(f"{FIVE_THOUSAND} nakit verdim", bare=True) == []
    assert money_in("٧٥٠ lira verdim") == [75000]


# ------------------------------------------------------------------ the route


@pytest.fixture()
def client():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in MONEY_TABLES:
        table.create(eng, checkfirst=True)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = eng
    artifacts._session_factory = sessionmaker(bind=eng, expire_on_commit=False)
    app.state.artifacts = artifacts
    test_client = TestClient(app, raise_server_exceptions=False)
    authenticate(app, test_client, settings=settings)
    yield test_client
    eng.dispose()


@pytest.mark.parametrize("amount", [FIVE_THOUSAND, JUST_OVER_INT_LIMIT])
def test_the_cash_route_refuses_an_absurd_length_in_turkish(client, amount) -> None:
    answer = client.post("/v1/money/cash", json={"amount": amount})
    assert answer.status_code == 422, answer.text
    detail = answer.json()["detail"]
    assert detail["code"] == "money_refused"
    assert "Tutarı anlayamadım" in detail["message"]


def test_the_cash_route_books_arabic_indic_digits_as_750(client) -> None:
    answer = client.post("/v1/money/cash", json={"amount": "٧٥٠"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["entry"]["amount_kurus"] == 75000


def test_the_cash_route_refuses_fullwidth_digits_in_turkish(client) -> None:
    answer = client.post("/v1/money/cash", json={"amount": "７５０"})
    assert answer.status_code == 422, answer.text
    assert answer.json()["detail"]["code"] == "money_refused"
