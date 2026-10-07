"""The test team's para-defteri finding (test-fail-para-defteri-4ac2d0e8ae, staging b1f8ef94).

A cash amount of more than 4300 digits answered 500: ``parse_amount`` handed the digits to
``int()``, which raises ``ValueError`` past Python's int-str limit (``sys.get_int_max_str_digits``).
The same 500 on ``/v1/money/questions/{id}/answer`` - the amount is read before the question
is looked up. A number that long is not one amount: ``None`` -> 422 ``money_refused``.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.main import create_app
from app.money import amounts
from app.money.models import MONEY_TABLES
from app.notifications.models import NotificationRow
from tests.identity_support import authenticate, install_identity

HUGE = [
    "9" * 4301,
    "9" * 5000,
    "1." + ".".join(["999"] * 1500),
    "9" * 5000 + ",50",
    "9" * 5000 + " TL",
]


@pytest.mark.parametrize("raw", HUGE, ids=["4301", "5000", "grouped", "frac", "tl"])
def test_an_amount_past_the_int_digit_limit_is_not_an_amount(raw: str) -> None:
    assert amounts.parse_amount(raw) is None


def test_a_sentence_with_a_huge_digit_word_has_no_amount() -> None:
    assert amounts.money_in("9" * 5000 + " lira harcadım") == []


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (*MONEY_TABLES, NotificationRow.__table__):
        table.create(engine, checkfirst=True)
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


@pytest.mark.parametrize("digits", [4301, 5000])
def test_cash_with_a_huge_amount_is_refused_not_500(client, digits: int) -> None:
    response = client.post("/v1/money/cash", json={"amount": "9" * digits})
    assert response.status_code == 422, response.text
    assert "money_refused" in response.text


def test_a_question_answer_with_a_huge_amount_is_refused_not_500(client) -> None:
    response = client.post(
        f"/v1/money/questions/{uuid.uuid4()}/answer", json={"answer": "yes", "amount": "9" * 5000}
    )
    assert response.status_code == 422, response.text
