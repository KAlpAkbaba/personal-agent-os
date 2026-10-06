"""Two devices, the same write, the same moment - the eleven routes built beside the ratchet.

Card two-devices-tests-late-write-routes. ``inbound-calls-bridge`` (Twilio's two webhooks),
``money-ledger`` (cash, cancel, a spend question's answer) and ``cloud-task-loop-core`` (a web
task's start and the owner's five words) were built in parallel with the 2026-10-06 ratchet
(``tests/unit/test_write_routes_concurrency_ratchet.py``) and reached it in its baseline. Each
is raced here with ``fire_together`` on the real PostgreSQL of the integration suite: no 500,
no lost row, no duplicate, and the device that loses gets a refusal or the idempotent answer.

Where a route's check-then-write is the race, ``meet_after`` names its read so the window is
held open on every run (the bare race lost a line in 4 of 5 runs on 2026-10-06). A second
round, sent after the first has landed, is the late device: it meets the state the winner
left, which is where an idempotent answer or a refusal is decided.

Two routers are not mounted by ``create_app`` yet (each module says so): the web-task router
is included on the real application here, the inbound router on its own app as in its unit
test - Twilio holds no owner session, the webhooks' authority is the signature. Temporal and
the target choice of a web task are stubbed: neither is what races.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.ledger.models import ActivityEventRow
from app.money.models import MoneyBankNotice, MoneyEntry, MoneyQuestion
from app.notifications.models import NotificationRow
from app.telephony import inbound_bridge as br
from app.telephony import inbound_routes
from app.telephony import inbound_twilio as tw
from app.telephony.inbound_records import NOTIFICATION_KIND, InboundRecorder
from app.telephony.inbound_settings import InboundSettings
from app.telephony.inbound_twilio import STATUS_PATH, VOICE_PATH
from app.webtask import routes as webtask_routes
from app.webtask import service as webtask_service
from app.webtask.models import WebTaskRow
from app.webtask.target import TaskTarget
from app.webtask.types import (
    ASK_CONFIRM,
    ASK_QUESTION,
    STATUS_CANCELLED,
    STATUS_RUNNING,
    STATUS_WAITING_OWNER,
    TARGET_OWNER_CHROME,
    Pending,
    Step,
)
from tests.integration.concurrency import fire_together
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = build_engine(settings.database_url)
    sessions = build_session_factory(engine)
    try:
        yield sessions
    finally:
        engine.dispose()


def _count(sessions: sessionmaker[Session], model: type, *where: Any) -> int:
    with sessions() as session:
        return session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def _codes(responses) -> list[int]:  # noqa: ANN001
    return [r.status_code for r in responses]


# ------------------------------------------------------------------------------- the money


def _clear_money(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        for model in (MoneyBankNotice, MoneyQuestion, MoneyEntry):
            session.execute(delete(model))
        session.commit()


@pytest.fixture()
def money(factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    _clear_money(factory)
    try:
        yield factory
    finally:
        _clear_money(factory)


def test_two_devices_book_cash_at_once_and_both_spends_are_kept(money, settings) -> None:
    client = owner_client(settings)
    amounts = ["120", "35,50", "7", "1.250"]

    responses = fire_together(
        client,
        "POST",
        "/v1/money/cash",
        json=lambda i: {"amount": amounts[i], "category": "market"},
        n=len(amounts),
    )

    assert _codes(responses) == [200] * len(amounts), [r.text for r in responses]
    with money() as session:
        kept = sorted(session.execute(select(MoneyEntry.amount_kurus)).scalars().all())
    assert kept == sorted([12000, 3550, 700, 125000])


def test_two_devices_cancel_one_entry_and_it_is_cancelled_once(money, settings) -> None:
    client = owner_client(settings)
    booked = client.post("/v1/money/cash", json={"amount": "80"})
    assert booked.status_code == 200, booked.text
    entry_id = booked.json()["entry"]["id"]
    first = fire_together(
        client,
        "POST",
        f"/v1/money/entries/{entry_id}/cancel",
        n=3,
        meet_after=r"FROM money_entries\b",
    )
    # The late device: the entry is cancelled already - the answer is the same, never a refusal.
    late = fire_together(client, "POST", f"/v1/money/entries/{entry_id}/cancel", n=2)

    for responses in (first, late):
        assert _codes(responses) == [200] * len(responses), [r.text for r in responses]
        assert {r.json()["entry"]["status"] for r in responses} == {"cancelled"}
    assert _count(money, MoneyEntry) == 1
    with money() as session:
        row = session.execute(select(MoneyEntry)).scalar_one()
    assert row.status == "cancelled" and row.cancelled_at is not None


def _question(sessions: sessionmaker[Session], *, kurus: int = 75000) -> str:
    now = datetime.now(UTC)
    question = MoneyQuestion(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        anchor_seq=1,
        amount_kurus=kurus,
        method="card",
        reason="no_acceptance",
        occurred_at=now,
        created_at=now,
        asked_at=now,
    )
    with sessions() as session:
        session.add(question)
        session.commit()
    return str(question.id)


def test_two_devices_answer_yes_to_one_question_and_one_spend_is_booked(money, settings) -> None:
    client = owner_client(settings)
    question_id = _question(money)

    responses = fire_together(
        client,
        "POST",
        f"/v1/money/questions/{question_id}/answer",
        json={"answer": "yes"},
        n=3,
        meet_after=r"FROM money_questions\b",
    )
    late = client.post(f"/v1/money/questions/{question_id}/answer", json={"answer": "yes"})

    assert _codes(responses) == [200] * 3, [r.text for r in responses]
    assert late.status_code == 200 and late.json().get("already") is True, late.text
    # One spend, said once: the second "evet" must not count 750 TL twice.
    assert _count(money, MoneyEntry) == 1
    booked = [r.json()["entry"] for r in responses if r.json().get("entry")]
    assert len(booked) == 1, [r.json() for r in responses]
    with money() as session:
        question = session.get(MoneyQuestion, uuid.UUID(question_id))
        entry = session.execute(select(MoneyEntry)).scalar_one()
    assert question is not None and question.answer == "yes"
    assert question.entry_id == entry.id


def test_one_device_says_yes_and_another_no_and_the_question_has_one_answer(
    money, settings
) -> None:
    client = owner_client(settings)
    question_id = _question(money)

    responses = fire_together(
        client,
        "POST",
        f"/v1/money/questions/{question_id}/answer",
        json=lambda i: {"answer": "yes" if i == 0 else "no"},
        n=2,
        meet_after=r"FROM money_questions\b",
    )

    assert _codes(responses) == [200, 200], [r.text for r in responses]
    with money() as session:
        question = session.get(MoneyQuestion, uuid.UUID(question_id))
        entries = session.execute(select(MoneyEntry)).scalars().all()
    assert question is not None
    # Whichever word won, the row and the book agree: "yes" with its one entry, or "no" and none.
    if question.answer == "yes":
        assert len(entries) == 1 and question.entry_id == entries[0].id
    else:
        assert question.answer == "no" and entries == [] and question.entry_id is None
    winners = [r.json() for r in responses if not r.json().get("already")]
    assert len(winners) == 1, [r.json() for r in responses]


# --------------------------------------------------------------------------- the web tasks


class _FakeHandle:
    async def signal(self, signal: Any) -> None:
        return None


class _FakeTemporal:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def start_workflow(self, *args: Any, id: str, **kwargs: Any) -> None:  # noqa: A002
        self.started.append(id)

    def get_workflow_handle(self, workflow_id: str) -> _FakeHandle:
        return _FakeHandle()


DEVICE = uuid.UUID("00000000-0000-4000-8000-00000000cafe")


def _clear_web_tasks(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        session.execute(delete(WebTaskRow))
        session.execute(
            delete(ActivityEventRow).where(ActivityEventRow.source_ref.like("web_task:%"))
        )
        session.commit()


@pytest.fixture()
def web(factory, settings, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    temporal = _FakeTemporal()

    async def client_for(request: Any) -> _FakeTemporal:
        return temporal

    monkeypatch.setattr(webtask_routes, "_temporal_client", client_for)
    monkeypatch.setattr(
        webtask_routes,
        "choose_task_target",
        lambda db, broker, **kw: TaskTarget(
            target=TARGET_OWNER_CHROME,
            device_id=DEVICE,
            decision=None,  # type: ignore[arg-type]
        ),
    )
    _clear_web_tasks(factory)
    client = owner_client(settings)
    client.app.include_router(webtask_routes.router)
    try:
        yield client, factory, temporal
    finally:
        _clear_web_tasks(factory)


def _waiting(sessions: sessionmaker[Session], kind: str) -> uuid.UUID:
    """A task the loop left waiting for the owner (``kind``), as ``run_round_db`` writes it."""
    with sessions() as session:
        row = webtask_service.start_task_db(
            session, goal="kargo takip sayfasını aç", device_id=DEVICE, target=TARGET_OWNER_CHROME
        )
        state = webtask_service.load(row)
        state.status = STATUS_WAITING_OWNER
        step = (
            Step(action="click", ref="e7", why="Siparişi onayla") if kind == ASK_CONFIRM else None
        )
        state.pending = Pending(
            kind=kind, message="Onaylıyor musunuz?", step=step, step_digest="d" * 16
        )
        webtask_service._write(row, state)
        session.commit()
        return row.id


def _row(sessions: sessionmaker[Session], task_id: uuid.UUID) -> WebTaskRow:
    with sessions() as session:
        row = session.get(WebTaskRow, task_id)
        assert row is not None
        session.expunge(row)
        return row


def test_two_devices_start_a_web_task_at_once_and_one_task_runs(web) -> None:
    client, sessions, temporal = web

    responses = fire_together(
        client,
        "POST",
        "/v1/web-tasks",
        json=lambda i: {"goal": f"cihaz {i}: hava durumuna bak"},
        n=3,
        meet_after=r"FROM web_tasks\s+WHERE web_tasks\.status IN",
    )

    codes = sorted(_codes(responses))
    assert codes == [202, 409, 409], [r.text for r in responses]
    refused = [r.json()["detail"] for r in responses if r.status_code == 409]
    assert {d["error_class"] for d in refused} == {"task_in_flight"}
    assert _count(sessions, WebTaskRow) == 1
    assert len(temporal.started) == 1


#: The owner-word routes read their task by id first: the window a check-then-write leaves.
#: (Each call writes its path out: the ratchet reads the path from the call site.)
BY_ID = r"FROM web_tasks\s+WHERE web_tasks\.id\b"


def _one_applies(responses, refusal: set[str]) -> None:  # noqa: ANN001
    codes = sorted(_codes(responses))
    assert codes == [200, 409, 409], [r.text for r in responses]
    refused = {r.json()["detail"]["error_class"] for r in responses if r.status_code == 409}
    assert refused <= refusal, refused


def test_two_devices_confirm_one_step_and_it_is_confirmed_once(web) -> None:
    client, sessions, _ = web
    task_id = _waiting(sessions, ASK_CONFIRM)
    shown = client.post(f"/v1/web-tasks/{task_id}/read-back")
    assert shown.status_code == 200, shown.text

    responses = fire_together(
        client, "POST", f"/v1/web-tasks/{task_id}/confirm", n=3, meet_after=BY_ID
    )

    # The second word meets a task that no longer waits: refused, never a second grant.
    _one_applies(responses, {"not_read_back", "already_sent", "nothing_to_confirm", "not_waiting"})
    row = _row(sessions, task_id)
    assert row.status == STATUS_RUNNING and row.state_json["grant"]["source"] == "rest"


def test_two_devices_decline_one_step_and_the_planner_is_told_once(web) -> None:
    client, sessions, _ = web
    task_id = _waiting(sessions, ASK_CONFIRM)

    responses = fire_together(
        client, "POST", f"/v1/web-tasks/{task_id}/decline", n=3, meet_after=BY_ID
    )

    _one_applies(responses, {"not_waiting"})
    row = _row(sessions, task_id)
    assert row.status == STATUS_RUNNING
    assert row.state_json["answers"].count("Sahip bu adımı onaylamadı; o adımı atma.") == 1


def test_two_devices_answer_a_question_and_no_answer_is_lost(web) -> None:
    client, sessions, _ = web
    task_id = _waiting(sessions, ASK_QUESTION)

    responses = fire_together(
        client,
        "POST",
        f"/v1/web-tasks/{task_id}/continue",
        json=lambda i: {"answer": f"cihaz {i}: sipariş no 4471{i}"},
        n=3,
        meet_after=BY_ID,
    )

    # A device that was told 200 has its answer on the task; the others were refused.
    _one_applies(responses, {"not_waiting"})
    row = _row(sessions, task_id)
    accepted = [i for i, r in enumerate(responses) if r.status_code == 200]
    assert row.state_json["answers"] == [f"cihaz {i}: sipariş no 4471{i}" for i in accepted]


def test_two_devices_cancel_a_waiting_task_and_it_finishes_once(web) -> None:
    client, sessions, _ = web
    task_id = _waiting(sessions, ASK_QUESTION)

    responses = fire_together(
        client, "POST", f"/v1/web-tasks/{task_id}/cancel", n=3, meet_after=BY_ID
    )
    late = fire_together(client, "POST", f"/v1/web-tasks/{task_id}/cancel", n=2)

    for batch in (responses, late):
        assert _codes(batch) == [200] * len(batch), [r.text for r in batch]
        assert {r.json()["status"] for r in batch} == {STATUS_CANCELLED}
    finished = _count(
        sessions,
        ActivityEventRow,
        ActivityEventRow.source_ref.like(f"web_task:{task_id}:web_task.finished%"),
    )
    assert finished == 1


def test_two_surfaces_read_back_one_step_and_a_late_one_is_refused(web) -> None:
    client, sessions, _ = web
    task_id = _waiting(sessions, ASK_CONFIRM)

    shown = fire_together(
        client, "POST", f"/v1/web-tasks/{task_id}/read-back", n=3, meet_after=BY_ID
    )
    assert _codes(shown) == [200] * 3, [r.text for r in shown]
    confirmed = client.post(f"/v1/web-tasks/{task_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    late = fire_together(client, "POST", f"/v1/web-tasks/{task_id}/read-back", n=2)

    # Read back after the step was confirmed: nothing to read back, and the grant stands.
    assert _codes(late) == [409, 409], [r.text for r in late]
    assert {r.json()["detail"]["error_class"] for r in late} == {"nothing_to_confirm"}
    row = _row(sessions, task_id)
    assert row.status == STATUS_RUNNING and row.read_back_session_id is None


# ----------------------------------------------------------------------- the inbound line

AUTH = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
PUBLIC = "https://jarvis.tail1234.ts.net:8443"
CALLER = "+905321112233"


def _sign(path: str, fields: dict[str, str]) -> str:
    data = PUBLIC + path + "".join(k + fields[k] for k in sorted(fields))
    return base64.b64encode(hmac.new(AUTH.encode(), data.encode(), hashlib.sha1).digest()).decode()


class _AsTwilio:
    """Twilio posts form fields with a signature over them; ``fire_together`` sends JSON. This
    ASGI shim re-encodes each JSON body as the form Twilio would send and signs it - the route,
    its signature check and everything behind it are the real ones."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = b""
        while True:
            message = await receive()
            raw += message.get("body", b"")
            if not message.get("more_body"):
                break
        fields = {str(k): str(v) for k, v in json.loads(raw or b"{}").items()}
        body = urllib.parse.urlencode(fields).encode()
        headers = [
            (k, v) for k, v in scope["headers"] if k not in (b"content-type", b"content-length")
        ]
        headers += [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body)).encode()),
            (b"x-twilio-signature", _sign(scope["path"], fields).encode()),
        ]
        sent = False

        async def once() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app({**scope, "headers": headers}, once, send)


@pytest.fixture()
def line(factory) -> Iterator[tuple[_AsTwilio, br.InboundLine, str]]:  # noqa: ANN001
    import contextlib

    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        with factory() as db:
            yield db

    tag = uuid.uuid4().hex[:12]
    inbound = br.InboundLine(
        settings=InboundSettings(
            enabled=True, public_base_url=PUBLIC, twilio_auth_token=AUTH, openai_api_key="sk-test"
        ),
        provider=tw.TwilioInbound(AUTH),
        recorder=InboundRecorder(scope),
        leg_factory=lambda: None,  # type: ignore[arg-type,return-value]
    )
    app = FastAPI()
    app.include_router(inbound_routes.router)
    app.state.telephony_inbound = inbound
    try:
        yield _AsTwilio(app), inbound, tag
    finally:
        with factory() as db:
            db.execute(
                delete(ActivityEventRow).where(
                    ActivityEventRow.source == "telephony",
                    ActivityEventRow.source_ref.like(f"inbound:CA{tag}%"),
                )
            )
            db.execute(
                delete(NotificationRow).where(
                    NotificationRow.kind == NOTIFICATION_KIND,
                    NotificationRow.data_json["call_sid"].as_string().like(f"CA{tag}%"),
                )
            )
            db.commit()


def _call(tag: str, n: int, caller: str = CALLER) -> dict[str, str]:
    return {
        "CallSid": f"CA{tag}{n:020d}",
        "AccountSid": "AC" + "1" * 32,
        "From": caller,
        "To": "+908501234567",
        "CallStatus": "ringing",
        "Direction": "inbound",
    }


def test_two_calls_ring_at_once_on_a_one_call_line_and_one_is_answered(line) -> None:
    app, inbound, tag = line

    responses = fire_together(
        app,
        "POST",
        VOICE_PATH,
        json=lambda i: _call(tag, i, caller=f"+90532111223{i}"),
        n=3,
        meet_after=r"FROM activity_events\b",
    )

    assert _codes(responses) == [200] * 3, [r.text for r in responses]
    answered = [r.text for r in responses if "<Stream" in r.text]
    refused = [r.text for r in responses if tw.REFUSE_TEXT_TR in r.text]
    assert len(answered) == 1 and len(refused) == 2, [r.text for r in responses]
    assert inbound.tokens.pending() == 1


def test_twilio_says_the_call_ended_twice_at_once_and_it_is_written_once(line, factory) -> None:
    app, _, tag = line
    ringing = _call(tag, 1)
    first = fire_together(app, "POST", VOICE_PATH, json=lambda i: _call(tag, 1 + 10 * i), n=2)
    assert sum("<Stream" in r.text for r in first) == 1, [r.text for r in first]
    answered_sid = next(
        _call(tag, 1 + 10 * i)["CallSid"] for i, r in enumerate(first) if "<Stream" in r.text
    )
    ended = {**ringing, "CallSid": answered_sid, "CallStatus": "completed", "CallDuration": "42"}

    responses = fire_together(
        app,
        "POST",
        STATUS_PATH,
        json=ended,
        n=3,
        meet_after=r"SELECT activity_events\.event_id\b",
    )

    assert _codes(responses) == [204] * 3, [r.text for r in responses]
    ledger = _count(
        factory, ActivityEventRow, ActivityEventRow.source_ref == f"inbound:{answered_sid}"
    )
    notes = _count(
        factory,
        NotificationRow,
        NotificationRow.data_json["call_sid"].as_string() == answered_sid,
    )
    assert (ledger, notes) == (1, 1)
