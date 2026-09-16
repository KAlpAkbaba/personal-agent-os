"""B27 req 731-735: the four tools the ten sentences reach, and what each really does.

The router half is ``test_intent_daily_coverage.py``. This file is the other half: an
intent that resolves to a tool nobody wrote acts on nothing, so each new tool is held to
what it claims — a device call that actually happens, a receipt whose terminal status is
earned by a read-back, a refusal that is a receipt with a sentence rather than a crash,
and no image bytes anywhere near the ledger.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import (
    TASK_STATUS_FAILED_TERMINAL,
    TASK_STATUS_RUNNING,
    Artifact,
    Task,
    TaskRun,
)
from app.calendar.models import CalendarIndexRow, CalendarProposalRow
from app.calendar.service import ERROR_DELETE_NOT_PERMITTED, CalendarService
from app.ledger.models import ActivityEventRow
from app.media.models import PLAYBACK_STATUS_PLAYING, OwnerMediaPlaybackRow
from app.media.playback_service import (
    CAPABILITY_MEDIA_STATUS,
    CAPABILITY_MEDIA_VOLUME,
    ERROR_NOTHING_PLAYING,
    SESSION_PREFIX,
    set_volume,
)
from app.object_store import InMemoryObjectStore
from app.operator import focus as focus_module
from app.operator.capabilities import CAPABILITY_SCREENSHOT, OPERATOR_CAPABILITIES
from app.operator.models import FOCUS_KIND_EVENT, ObjectFocusRow
from app.research import runs_service
from app.research import service as research_service
from app.research.models import STAGE_CANCELLED, STAGE_DISCOVERING, STAGE_READY, ResearchRunRow
from app.routines.dispatch import DeviceRunResult
from app.security import step_up
from app.voice.realtime_sessions.tools import (
    RESEARCH_CANCEL_TOOL_NAME,
    ToolContext,
    default_registry,
    research_cancel,
)
from app.voice.realtime_sessions.tools_assistant import assistant_capabilities
from app.voice.realtime_sessions.tools_calendar import (
    RANGE_NEXT_WEEK,
    RANGE_THIS_WEEK,
    TOOL_CALENDAR_CANCEL,
    _range_bounds,
    calendar_cancel,
)
from app.voice.realtime_sessions.tools_media import TOOL_MEDIA_VOLUME, media_volume
from app.voice.realtime_sessions.tools_operator import (
    DEVICE_SCREEN_CAPTURE,
    operator_screenshot,
)
from tests.alarms_support import ONE_PIXEL_PNG_B64
from tests.mail_calendar_support import build_fake_calendar_provider, build_fake_calendar_writer

TZ = ZoneInfo("Europe/Istanbul")


# ------------------------------------------------------------------------ fixtures


def _factory(*tables):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in tables:
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


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

    def called(self) -> list[str]:
        return [name for name, _ in self.calls]


def _ctx(db: Session | None, live: dict[str, Any] | None = None, **turn: Any) -> ToolContext:
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="desktop",
        context={"last_utterance": dict(turn)} if turn else {},
        db=db,
        now=datetime.now(UTC),
        call_id=f"c-{uuid.uuid4()}",
        live=dict(live or {}),
    )


def _receipts(db: Session) -> list[ActivityEventRow]:
    return list(db.execute(select(ActivityEventRow)).scalars())


# ------------------------------------------------------------------ the registry


def test_the_four_tools_are_in_the_registry_the_model_is_handed() -> None:
    names = set(default_registry().names())
    assert {
        RESEARCH_CANCEL_TOOL_NAME,
        TOOL_MEDIA_VOLUME,
        CAPABILITY_SCREENSHOT,
        TOOL_CALENDAR_CANCEL,
    } <= names


def test_the_four_tools_are_governed_not_open() -> None:
    """A screenshot reads the screen, a cancel ends a workflow, a deletion is asked for,
    a level moves on the device: none of them is OPEN."""
    for name in (
        RESEARCH_CANCEL_TOOL_NAME,
        TOOL_MEDIA_VOLUME,
        CAPABILITY_SCREENSHOT,
        TOOL_CALENDAR_CANCEL,
    ):
        assert step_up.tier_of(name) == step_up.TIER_SENSITIVE, name


def test_the_screenshot_is_a_declared_operator_capability() -> None:
    assert CAPABILITY_SCREENSHOT in OPERATOR_CAPABILITIES


# ------------------------------------------------------------- 733: media.volume


@pytest.fixture()
def media_db() -> Session:
    factory = _factory(OwnerMediaPlaybackRow.__table__, ActivityEventRow.__table__)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _playing(db: Session) -> OwnerMediaPlaybackRow:
    row = OwnerMediaPlaybackRow(
        id=uuid.uuid4(),
        request_text="Tarkan",
        query="Tarkan youtube",
        video_id="abc",
        video_title="Şarkı",
        url="https://www.youtube.com/watch?v=abc",
        session_id="",
        status=PLAYBACK_STATUS_PLAYING,
        receipt_json={"playing": True},
    )
    row.session_id = f"{SESSION_PREFIX}{row.id}"
    db.add(row)
    db.commit()
    return row


def _volume_device(*, current: float = 0.5) -> FakeDeviceAction:
    return FakeDeviceAction(
        results={
            CAPABILITY_MEDIA_STATUS: DeviceRunResult(
                True, result={"present": True, "playing": True, "volume": current}
            ),
            CAPABILITY_MEDIA_VOLUME: lambda payload: DeviceRunResult(
                True,
                result={
                    "applied": True,
                    "level_from": current,
                    "level_to": payload["level"],
                    "ramp_seconds": 0,
                },
            ),
        }
    )


def test_a_step_down_reads_the_level_first_then_moves_it(media_db: Session) -> None:
    row = _playing(media_db)
    device = _volume_device(current=0.5)

    outcome = set_volume(media_db, device, direction="down")

    assert outcome.ok and outcome.level_from == 0.5 and outcome.level_to == 0.25
    assert device.called() == [CAPABILITY_MEDIA_STATUS, CAPABILITY_MEDIA_VOLUME]
    _, payload = device.calls[1]
    assert payload["session_id"] == row.session_id
    assert payload["level"] == 0.25


def test_mute_goes_to_zero_and_up_never_passes_one(media_db: Session) -> None:
    _playing(media_db)
    assert set_volume(media_db, _volume_device(current=0.7), direction="mute").level_to == 0.0
    assert set_volume(media_db, _volume_device(current=0.9), direction="up").level_to == 1.0


def test_nothing_playing_is_a_refusal_and_no_device_call(media_db: Session) -> None:
    device = _volume_device()
    outcome = set_volume(media_db, device, direction="down")
    assert not outcome.ok and outcome.error_class == ERROR_NOTHING_PLAYING
    assert device.calls == []


def test_the_owners_verb_beats_the_models_direction(media_db: Session) -> None:
    """"Sesi aç." with a model that passed direction=down still goes UP."""
    _playing(media_db)
    device = _volume_device(current=0.5)
    ctx = _ctx(media_db, {"device_action": device}, media_volume_direction="up")

    receipt = media_volume(ctx, {"direction": "down"})

    assert receipt["execution_status"] == "executed"
    assert receipt["terminal_status"] == "verified"
    assert receipt["requested_state"] == "up"
    assert device.calls[1][1]["level"] == 0.75
    assert "açtım" in receipt["speech"]
    events = {e.event_type for e in _receipts(media_db)}
    assert {"action.receipt", "media.volume_changed"} <= events


def test_a_device_that_did_not_apply_is_a_failed_receipt(media_db: Session) -> None:
    _playing(media_db)
    device = FakeDeviceAction(
        results={
            CAPABILITY_MEDIA_STATUS: DeviceRunResult(True, result={"volume": 0.5}),
            CAPABILITY_MEDIA_VOLUME: DeviceRunResult(True, result={"applied": False}),
        }
    )
    receipt = media_volume(_ctx(media_db, {"device_action": device}), {"direction": "down"})
    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "volume_failed"
    assert receipt["speech"]


# ------------------------------------------------------ 735: operator.screenshot


@pytest.fixture()
def ledger_db() -> Session:
    session = _factory(ActivityEventRow.__table__)()
    try:
        yield session
    finally:
        session.close()


class _Artifacts:
    def __init__(self) -> None:
        self.store = InMemoryObjectStore()


def test_a_screenshot_is_one_device_call_and_a_stored_hashed_image(ledger_db: Session) -> None:
    device = FakeDeviceAction(
        results={
            DEVICE_SCREEN_CAPTURE: DeviceRunResult(
                True, result={"width": 1, "height": 1, "png_base64": ONE_PIXEL_PNG_B64}
            )
        }
    )
    artifacts = _Artifacts()
    ctx = _ctx(ledger_db, {"device_action": device, "artifacts_runtime": artifacts})

    receipt = operator_screenshot(ctx, {})

    assert device.called() == [DEVICE_SCREEN_CAPTURE]
    assert receipt["execution_status"] == "executed"
    assert receipt["terminal_status"] == "verified"
    server = receipt["observed_after"]["server"]
    assert server["width"] == 1 and server["bytes"] == len(base64.b64decode(ONE_PIXEL_PNG_B64))
    assert len(server["sha256"]) == 64
    assert artifacts.store.exists(server["stored_key"])
    assert "1×1" in receipt["speech"] and "Kaydettim" in receipt["speech"]
    # The image never travels in the receipt or the ledger row.
    assert "png_base64" not in str(receipt)
    rows = _receipts(ledger_db)
    assert rows and all("png_base64" not in str(r.detail_json) for r in rows)


def test_no_device_port_is_a_capability_missing_receipt(ledger_db: Session) -> None:
    receipt = operator_screenshot(_ctx(ledger_db, {}), {})
    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "capability_missing"


def test_a_device_that_does_not_advertise_the_capability_is_refused_by_the_registry(
    ledger_db: Session,
) -> None:
    """The honest production answer today (matrix row 104): the registry says no device
    has `screen.capture`, and the receipt says exactly that."""
    device = FakeDeviceAction(
        results={
            DEVICE_SCREEN_CAPTURE: DeviceRunResult(
                False,
                "no_capable_device",
                "'screen.capture' yeteneğine sahip çevrimiçi bir cihaz bulunamadı.",
            )
        }
    )
    receipt = operator_screenshot(_ctx(ledger_db, {"device_action": device}), {})
    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "capability_missing"
    assert "bildirmiyor" in receipt["speech"]


def test_a_device_that_returns_no_image_is_an_unverified_failure(ledger_db: Session) -> None:
    device = FakeDeviceAction(results={DEVICE_SCREEN_CAPTURE: DeviceRunResult(True, result={})})
    receipt = operator_screenshot(_ctx(ledger_db, {"device_action": device}), {})
    assert receipt["execution_status"] == "failed"
    assert receipt["terminal_status"] == "failed"
    assert receipt["error_class"] == "unverified"


# ------------------------------------------------------ 732: research.cancel


@pytest.fixture()
def research_db() -> Session:
    from app.notifications.models import NotificationRow
    from app.research.models import (
        ResearchCandidateRow,
        ResearchEvidenceRow,
        ResearchReportRow,
    )

    session = _factory(
        Task.__table__,
        TaskRun.__table__,
        Artifact.__table__,
        ResearchRunRow.__table__,
        ResearchReportRow.__table__,
        ResearchCandidateRow.__table__,
        ResearchEvidenceRow.__table__,
        NotificationRow.__table__,
        ActivityEventRow.__table__,
    )()
    try:
        yield session
    finally:
        session.close()


def _run(db: Session, *, stage: str) -> Task:
    task = Task(
        id=uuid.uuid4(), intent="araştır", status=TASK_STATUS_RUNNING, created_at=datetime.now(UTC)
    )
    task.workflow_id = research_service.workflow_id_for(task.id)
    db.add(task)
    db.flush()
    runs_service.get_or_create_run(db, task.id)
    runs_service.update_run(db, task.id, stage=stage, event={"stage": stage})
    db.commit()
    return task


def test_cancelling_the_running_research_closes_run_task_ledger_and_workflow(
    research_db: Session,
) -> None:
    from app.config import Settings

    class _Runtime:
        settings = Settings(_env_file=None)

    task = _run(research_db, stage=STAGE_DISCOVERING)
    ctx = _ctx(research_db, {"artifacts_runtime": _Runtime()})

    receipt = research_cancel(ctx, {})

    assert receipt["execution_status"] == "executed"
    assert receipt["terminal_status"] == "verified"
    assert research_db.get(ResearchRunRow, task.id).stage == STAGE_CANCELLED
    research_db.refresh(task)
    assert task.status == TASK_STATUS_FAILED_TERMINAL
    assert task.error_class == research_service.ERROR_RESEARCH_CANCELLED
    events = {e.event_type for e in _receipts(research_db)}
    assert {"action.receipt", "research.cancelled"} <= events

    # The Temporal half, awaited by the route after commit: the workflow handle is
    # cancelled by the id the task carries.
    assert len(ctx.followups) == 1
    handle = AsyncMock()
    client = AsyncMock()
    client.get_workflow_handle = (
        lambda workflow_id: handle if workflow_id == task.workflow_id else None
    )
    with patch("app.research.service.Client.connect", AsyncMock(return_value=client)):
        asyncio.run(ctx.followups[0]())
    handle.cancel.assert_awaited_once()


def test_nothing_running_is_a_refused_receipt_never_a_success(research_db: Session) -> None:
    _run(research_db, stage=STAGE_READY)  # finished, not in flight
    receipt = research_cancel(_ctx(research_db), {})
    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == "nothing_running"
    assert "yok" in receipt["speech"]


def test_a_finished_research_is_never_rewritten(research_db: Session) -> None:
    task = _run(research_db, stage=STAGE_READY)
    assert research_service.mark_research_cancelled(research_db, task.id) is False
    assert research_db.get(ResearchRunRow, task.id).stage == STAGE_READY


def test_the_rest_route_and_the_tool_share_one_function() -> None:
    """The two halves of req 202/732 cannot record a cancellation differently."""
    from pathlib import Path

    routes = Path(research_service.__file__).with_name("routes.py").read_text("utf-8")
    assert "research_service.mark_research_cancelled(" in routes
    tools = Path(research_cancel.__code__.co_filename).read_text("utf-8")
    assert "research_service.mark_research_cancelled(" in tools


# ------------------------------------------------------ 731: calendar.cancel


@pytest.fixture()
def calendar_db() -> Session:
    session = _factory(
        CalendarIndexRow.__table__,
        CalendarProposalRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
    )()
    try:
        yield session
    finally:
        session.close()


def _calendar_ctx(db: Session, service: CalendarService) -> ToolContext:
    return _ctx(db, {"calendar_service": service})


def test_cancelling_the_focused_event_is_an_honest_refusal_with_a_receipt(
    calendar_db: Session,
) -> None:
    service = CalendarService(build_fake_calendar_provider(), build_fake_calendar_writer())
    focus_module.set_focus(
        calendar_db, FOCUS_KIND_EVENT, "ev-dis@fixture.example", label="Diş hekimi", source="t"
    )

    receipt = calendar_cancel(_calendar_ctx(calendar_db, service), {})

    assert receipt["execution_status"] == "refused"
    assert receipt["error_class"] == ERROR_DELETE_NOT_PERMITTED
    assert receipt["event"]["uid"] == "ev-dis@fixture.example"
    assert receipt["event"]["summary"] == "Diş hekimi"
    assert "silme yetkim yok" in receipt["speech"]
    assert [e.event_type for e in _receipts(calendar_db)] == ["action.receipt"]


def test_no_focused_event_is_a_question_not_a_guess(calendar_db: Session) -> None:
    service = CalendarService(build_fake_calendar_provider(), build_fake_calendar_writer())
    out = calendar_cancel(_calendar_ctx(calendar_db, service), {})
    assert out["status"] == "needs_clarification"
    assert out["speech"] == "Hangi etkinlik?"


def test_no_calendar_account_is_the_family_refusal(calendar_db: Session) -> None:
    out = calendar_cancel(_calendar_ctx(calendar_db, CalendarService(None, None)), {})
    assert out["error_class"] == "account_missing"


# --------------------------------------------------- 730: the week the owner asked for


def _clock_ctx(now: datetime) -> ToolContext:
    ctx = _ctx(None)
    ctx.now = now.astimezone(UTC)
    return ctx


def test_this_week_runs_from_today_through_sunday() -> None:
    wednesday = datetime(2026, 9, 16, 10, 0, tzinfo=TZ)
    start, end, label = _range_bounds(_clock_ctx(wednesday), "Bu hafta ne var?")
    assert label == RANGE_THIS_WEEK
    assert start == datetime(2026, 9, 16, 0, 0, tzinfo=TZ)
    assert end == datetime(2026, 9, 21, 0, 0, tzinfo=TZ)  # Monday 00:00 (exclusive)


def test_next_week_is_the_coming_monday_to_sunday() -> None:
    wednesday = datetime(2026, 9, 16, 10, 0, tzinfo=TZ)
    start, end, label = _range_bounds(_clock_ctx(wednesday), "Haftaya ne var?")
    assert label == RANGE_NEXT_WEEK
    assert start == datetime(2026, 9, 21, 0, 0, tzinfo=TZ)
    assert end - start == timedelta(days=7)


def test_a_day_is_still_a_day() -> None:
    wednesday = datetime(2026, 9, 16, 10, 0, tzinfo=TZ)
    start, end, label = _range_bounds(_clock_ctx(wednesday), "Yarın ne var?")
    assert label == "2026-09-17"
    assert end - start == timedelta(days=1)


# ------------------------------------------------ 734: the family the owner named


def test_the_owners_family_beats_the_models_argument() -> None:
    ctx = _ctx(None, capability_family="mail")
    out = assistant_capabilities(ctx, {"family": "alarm"})
    assert out["family"] == "mail" and out["known"] is True
    assert out["count"] >= 1


def test_without_a_spoken_family_the_argument_still_narrows() -> None:
    out = assistant_capabilities(_ctx(None), {"family": "alarm"})
    assert out["family"] == "alarm" and out["known"] is True


# ------------------------------------------- the fields TRAVEL through the session


def test_the_spoken_family_and_direction_reach_the_tools_through_the_real_session() -> None:
    """The gap the session's own comment warns about, and the one this batch fell into:
    ``record_client_events`` copies the resolved intent into the turn record field by
    field, and a field added to ``ResolvedIntent`` and not added there never reaches the
    tool. The two unit tests above build the turn record by hand and cannot see that.
    Found by B28's relay test for ``key_press``; both B27 fields were missing too."""
    from tests.voice_corpus.corpus import CTX_MEDIA_PLAYING
    from tests.voice_corpus.harness import build_harness

    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Mail konusunda neler yapabilirsin?")
    answer = h.tool(sid, "c-1", "assistant.capabilities", {"family": "alarm"})
    assert answer["status"] == "succeeded", answer
    assert answer["result"]["family"] == "mail", "the owner said mail; the model said alarm"

    h.seed(CTX_MEDIA_PLAYING)
    h.say(sid, "Sesi biraz aç.", turn=2)
    volume = h.tool(sid, "c-2", "media.volume", {"direction": "down"})
    assert volume["status"] == "succeeded", volume
    assert volume["result"]["requested_state"] == "up", "the owner said up; the model said down"
