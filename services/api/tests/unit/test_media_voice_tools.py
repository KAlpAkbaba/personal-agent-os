"""``media.play`` / ``media.stop`` (ADR-0112): the turn, the receipt and the ledger.

The tools are thin by design, so what is asserted here is the WIRING: that the
owner's own words beat the model's argument, that the receipt's terminal status
carries the difference between proven and unproven playback, and that both tools
are actually in the registry the model is handed -- which is the half that was
missing for the whole feature until now.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.media.models import OwnerMediaPlaybackRow
from app.media.playback_service import CAPABILITY_MEDIA_PLAY, CAPABILITY_SEARCH
from app.routines.dispatch import DeviceRunResult
from app.voice.realtime_sessions.tools import ToolContext, default_registry
from app.voice.realtime_sessions.tools_media import (
    TOOL_MEDIA_PLAY,
    TOOL_MEDIA_STOP,
    media_play,
    media_stop,
)

VIDEO = "dQw4w9WgXcQ"
WATCH = f"https://www.youtube.com/watch?v={VIDEO}"


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (OwnerMediaPlaybackRow.__table__, ActivityEventRow.__table__):
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@dataclass
class FakeDeviceAction:
    results: dict[str, DeviceRunResult | Callable[[dict[str, Any]], DeviceRunResult]] = field(
        default_factory=dict
    )
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def run(
        self, *, capability: str, payload: dict[str, Any], idempotency_key: str, timeout_s: float
    ) -> DeviceRunResult:
        self.calls.append((capability, dict(payload)))
        scripted = self.results.get(capability)
        if scripted is None:
            return DeviceRunResult(True, result={})
        if callable(scripted):
            return scripted(payload)
        return scripted


def _device(*, verified: bool = True) -> FakeDeviceAction:
    return FakeDeviceAction(
        results={
            CAPABILITY_SEARCH: DeviceRunResult(
                True, result={"results": [{"rank": 1, "url": WATCH, "title": "Şarkı"}]}
            ),
            CAPABILITY_MEDIA_PLAY: DeviceRunResult(
                True, result={"playing": True, "verified": verified, "title": "Şarkı"}
            ),
        }
    )


def _ctx(db: Session, device: FakeDeviceAction | None, **turn: Any) -> ToolContext:
    return ToolContext(
        session_id=uuid4(),
        owner_session_id=uuid4(),
        device_id=None,
        client_kind="desktop",
        context={"last_utterance": dict(turn)} if turn else {},
        db=db,
        now=datetime.now(UTC),
        live={"device_action": device},
    )


def test_both_tools_are_in_the_registry_the_model_is_handed() -> None:
    """The half that was missing. The device could open YouTube all along -- the wake
    alarm does it every morning -- and none of the 115 tools let the OWNER ask, so the
    request fell through to ``capability.propose`` and was written down instead."""
    names = set(default_registry().names())
    assert {TOOL_MEDIA_PLAY, TOOL_MEDIA_STOP} <= names


def test_the_owners_words_are_searched_not_the_models_paraphrase(db: Session) -> None:
    device = _device()
    ctx = _ctx(db, device, media_query="Doğum günün kutlu olsun Kadir")

    media_play(ctx, {"query": "happy birthday song"})

    _, search_payload = next(c for c in device.calls if c[0] == CAPABILITY_SEARCH)
    assert search_payload["query"] == "Doğum günün kutlu olsun Kadir youtube"


def test_the_models_argument_is_used_when_the_router_classified_nothing(db: Session) -> None:
    device = _device()
    ctx = _ctx(db, device)

    media_play(ctx, {"query": "Sezen Aksu Gülümse"})

    _, search_payload = next(c for c in device.calls if c[0] == CAPABILITY_SEARCH)
    assert search_payload["query"] == "Sezen Aksu Gülümse youtube"


def test_a_verified_play_is_a_verified_receipt(db: Session) -> None:
    ctx = _ctx(db, _device(verified=True), media_query="Tarkan")

    response = media_play(ctx, {})

    assert response["execution_status"] == "executed"
    assert response["terminal_status"] == "verified"
    assert response["error_class"] is None
    assert "çalıyor" in response["speech"]


def test_an_unverified_play_is_an_unverified_receipt_not_a_verified_one(db: Session) -> None:
    ctx = _ctx(db, _device(verified=False), media_query="Tarkan")

    response = media_play(ctx, {})

    assert response["execution_status"] == "executed"
    assert response["terminal_status"] == "unverified"
    assert response["error_class"] == "playback_unverified"
    assert "doğrulayamadım" in response["speech"]


def test_a_refusal_is_filed_under_its_own_subsystem(db: Session) -> None:
    """ "Bana ne açtın?" must not return the morning's wake song or a news bulletin."""
    ctx = _ctx(db, None, media_query="Tarkan")

    media_play(ctx, {})

    events = db.execute(select(ActivityEventRow)).scalars().all()
    assert {e.subsystem for e in events} == {"media"}
    # the receipt rides the ledger as an action.receipt row, named by its capability
    receipts = [e for e in events if e.event_type == "action.receipt"]
    assert [e.action for e in receipts] == [TOOL_MEDIA_PLAY]
    assert {e.event_type for e in events} == {"action.receipt", "media.playback_failed"}


def test_stop_reports_nothing_playing_rather_than_pretending(db: Session) -> None:
    ctx = _ctx(db, _device())

    response = media_stop(ctx, {})

    assert response["execution_status"] == "refused"
    assert response["error_class"] == "nothing_playing"


def test_play_then_stop(db: Session) -> None:
    device = _device()
    media_play(_ctx(db, device, media_query="Tarkan"), {})

    response = media_stop(_ctx(db, device), {})

    assert response["terminal_status"] == "verified"
    assert "Durdurdum" in response["speech"]
    stopped = db.execute(select(OwnerMediaPlaybackRow)).scalars().one()
    assert stopped.status == "closed"
