"""``/v1/voice/misheard`` through the REAL application object (misheard-ledger-store).

The four calls of the CONTRACT with their exact paths and shapes, under the owner session:
GET lists (newest first, ``open`` = the unanswered ones, ``retention_days`` = 30), POST
``/{id}/meaning`` answers one, DELETE ``/{id}`` forgets one, DELETE forgets the notebook.
The router is NOT included by the test: ``create_app`` registers it, or these are 404.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.main import create_app
from app.voice.misheard import service
from app.voice.misheard.models import MisheardUtterance
from tests.identity_support import authenticate, install_identity

BASE = "/v1/voice/misheard"
ITEM_KEYS = {
    "id",
    "heard_at",
    "sentence",
    "mode",
    "engine",
    "device_id",
    "band",
    "confidence",
    "reason",
    "resolved_intent",
    "tool",
    "session_id",
    "meant",
    "answered_at",
    "expires_at",
}
TURKISH = set("çğıöşüÇĞİÖŞÜ")


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    MisheardUtterance.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def api(engine):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    return app, TestClient(app), settings


@pytest.fixture()
def owner(api) -> TestClient:
    app, client, settings = api
    authenticate(app, client, settings=settings)
    return client


def _seed(engine, sentence: str, *, minutes_ago: int = 0, **overrides) -> uuid.UUID:
    heard_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    arguments = {
        "sentence": sentence,
        "mode": "paid",
        "reason": "no_intent",
        "session_id": uuid.uuid4(),
        "heard_at": heard_at,
        "now": heard_at,
    }
    arguments.update(overrides)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        row = service.record(session, **arguments)
        session.commit()
        assert row is not None
        return row.id


def _count(engine) -> int:
    with sessionmaker(bind=engine)() as session:
        return session.execute(select(func.count()).select_from(MisheardUtterance)).scalar_one()


def _refusal(response) -> dict[str, str]:
    detail = response.json()["detail"]
    assert set(detail) >= {"code", "message"}, detail
    assert TURKISH & set(detail["message"]), detail
    return detail


# ------------------------------------------------------------------- the session gate


def test_without_the_owner_session_each_of_the_four_calls_is_401(api, engine) -> None:
    _, client, _ = api
    row_id = _seed(engine, "Maillerime bakın")
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/{row_id}/meaning", json={"meant": "postamı oku"}).status_code == 401
    assert client.delete(f"{BASE}/{row_id}").status_code == 401
    assert client.delete(BASE).status_code == 401
    assert _count(engine) == 1


# ------------------------------------------------------------------- the four calls


def test_get_lists_newest_first_with_exactly_the_contract_columns(owner, engine) -> None:
    device_id = uuid.uuid4()
    _seed(engine, "Maillerime bakın", minutes_ago=10)
    newest = _seed(
        engine,
        "Ofisü bilgisayarında hesap makinesini açın",
        mode="local",
        engine="chrome-web-speech",
        device_id=device_id,
        band="low",
        confidence=0.4,
        reason="tool_failed",
        resolved_intent="app.launch",
        tool="app.launch",
    )
    response = owner.get(BASE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "open", "retention_days"}
    assert body["retention_days"] == 30
    assert body["open"] == 2
    assert [item["sentence"] for item in body["items"]] == [
        "Ofisü bilgisayarında hesap makinesini açın",
        "Maillerime bakın",
    ]
    first = body["items"][0]
    assert set(first) == ITEM_KEYS
    assert first["id"] == str(newest)
    assert first["mode"] == "local"
    assert first["engine"] == "chrome-web-speech"
    assert first["device_id"] == str(device_id)
    assert first["band"] == "low"
    assert first["confidence"] == pytest.approx(0.4)
    assert first["reason"] == "tool_failed"
    assert first["resolved_intent"] == "app.launch"
    assert first["tool"] == "app.launch"
    assert first["meant"] is None and first["answered_at"] is None
    heard = datetime.fromisoformat(first["heard_at"])
    assert datetime.fromisoformat(first["expires_at"]) - heard == timedelta(days=30)


def test_get_purges_before_it_lists(owner, engine) -> None:
    _seed(engine, "eski cümle", minutes_ago=31 * 24 * 60)
    _seed(engine, "yeni cümle")
    body = owner.get(BASE).json()
    assert [item["sentence"] for item in body["items"]] == ["yeni cümle"]
    assert _count(engine) == 1


def test_post_meaning_answers_the_row_and_open_counts_unanswered_only(owner, engine) -> None:
    answered = _seed(engine, "Maillerime bakın", minutes_ago=5)
    _seed(engine, "Ekranları kapatın")
    response = owner.post(f"{BASE}/{answered}/meaning", json={"meant": "postamı oku"})
    assert response.status_code == 200, response.text
    item = response.json()
    assert set(item) == ITEM_KEYS
    assert item["id"] == str(answered)
    assert item["meant"] == "postamı oku"
    assert item["answered_at"] is not None
    assert item["sentence"] == "Maillerime bakın"
    body = owner.get(BASE).json()
    assert len(body["items"]) == 2
    assert body["open"] == 1


@pytest.mark.parametrize("meant", ["", "   ", "a" * 2001])
def test_an_empty_or_2001_character_meant_is_422_in_turkish(owner, engine, meant: str) -> None:
    row_id = _seed(engine, "Maillerime bakın")
    response = owner.post(f"{BASE}/{row_id}/meaning", json={"meant": meant})
    assert response.status_code == 422, response.text
    _refusal(response)
    assert owner.get(BASE).json()["items"][0]["meant"] is None


def test_a_2000_character_meant_is_accepted(owner, engine) -> None:
    row_id = _seed(engine, "Maillerime bakın")
    response = owner.post(f"{BASE}/{row_id}/meaning", json={"meant": "a" * 2000})
    assert response.status_code == 200, response.text
    assert len(response.json()["meant"]) == 2000


def test_an_unknown_id_is_404_in_turkish(owner, engine) -> None:
    _seed(engine, "Maillerime bakın")
    unknown = uuid.uuid4()
    answer = owner.post(f"{BASE}/{unknown}/meaning", json={"meant": "postamı oku"})
    assert answer.status_code == 404, answer.text
    _refusal(answer)
    forget = owner.delete(f"{BASE}/{unknown}")
    assert forget.status_code == 404, forget.text
    _refusal(forget)
    assert _count(engine) == 1


def test_delete_one_removes_one_row(owner, engine) -> None:
    gone = _seed(engine, "Maillerime bakın", minutes_ago=5)
    _seed(engine, "Ekranları kapatın")
    response = owner.delete(f"{BASE}/{gone}")
    assert response.status_code == 200, response.text
    assert [item["sentence"] for item in owner.get(BASE).json()["items"]] == ["Ekranları kapatın"]
    assert owner.delete(f"{BASE}/{gone}").status_code == 404


def test_delete_all_forgets_the_notebook_and_returns_the_count(owner, engine) -> None:
    for offset in range(3):
        _seed(engine, f"cümle {offset}", minutes_ago=offset)
    response = owner.delete(BASE)
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 3}
    assert _count(engine) == 0
    assert owner.get(BASE).json() == {"items": [], "open": 0, "retention_days": 30}
    assert owner.delete(BASE).json() == {"deleted": 0}
