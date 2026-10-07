"""``/v1/web-tasks`` (card cloud-task-loop-core): the REST path that starts a browser task.

The router is mounted on a test application of its own: a SQLite database with the task
and ledger tables, a fake device registry, and a recording Temporal client. One case
connects to a CLOSED port instead, because the local dev stack has a Temporal and CI
does not (memory: the local dev stack masks CI). The last case reads ``app/main.py``:
the router is mounted by the lead at merge time, and from then on it must answer on the
real ``create_app()``.
"""

from __future__ import annotations

import socket
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.devices.types import DeviceView
from app.execution import allowlist_store, wiring
from app.identity.dependencies import require_owner_session
from app.ledger.models import ActivityEventRow
from app.webtask import routes, service
from app.webtask.models import SOURCE_REST, WebTaskRow
from app.webtask.target import TASK_OPERATIONS
from app.webtask.types import (
    ASK_CONFIRM,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WAITING_OWNER,
    Pending,
    Step,
)
from app.webtask.workflow import BrowserTaskWorkflow

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
GOAL = "bugünkü yapay zeka haberlerinden birini bul ve özetle"
OWNER_SESSION = "owner-session-1"


class FakeHandle:
    def __init__(self, client: FakeTemporal, workflow_id: str) -> None:
        self.client = client
        self.workflow_id = workflow_id

    async def signal(self, signal: Any, *args: Any) -> None:
        self.client.signals.append((self.workflow_id, getattr(signal, "__name__", str(signal))))


class FakeTemporal:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.signals: list[tuple[str, str]] = []

    async def start_workflow(self, run: Any, request: Any, *, id: str, task_queue: str) -> None:
        self.started.append({"run": run, "request": request, "id": id, "task_queue": task_queue})

    def get_workflow_handle(self, workflow_id: str) -> FakeHandle:
        return FakeHandle(self, workflow_id)


def _device(name: str, *, platform: str = "windows", labels=(), capabilities=None) -> DeviceView:
    return DeviceView(
        id=uuid.uuid4(),
        name=name,
        platform=platform,
        status="enrolled",
        presence="online",
        capabilities=tuple(capabilities or ("browser.chrome",)),
        enrolled_at=NOW,
        last_seen_at=NOW,
        labels=tuple(labels),
        aliases=(),
    )


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    WebTaskRow.__table__.create(engine)
    ActivityEventRow.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def registry(monkeypatch):
    devices: list[DeviceView] = []
    monkeypatch.setattr(wiring, "list_device_views", lambda db, runtime: list(devices))
    monkeypatch.setattr(allowlist_store, "effective_sites", lambda: ())
    return devices


def _app(factory, *, temporal_address: str = "127.0.0.1:7233", owner: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)

    @contextmanager
    def session():
        with factory() as db:
            yield db

    app.state.artifacts = SimpleNamespace(
        session=session,
        settings=SimpleNamespace(
            temporal_address=temporal_address,
            temporal_namespace="default",
            temporal_task_queue="pagentos-main",
        ),
    )
    app.state.broker = None
    if owner:
        app.dependency_overrides[require_owner_session] = lambda: SimpleNamespace(
            session_id=OWNER_SESSION, scopes=()
        )
    return app


@pytest.fixture()
def temporal(monkeypatch) -> FakeTemporal:
    fake = FakeTemporal()

    async def connect(request: Any) -> FakeTemporal:
        return fake

    monkeypatch.setattr(routes, "_temporal_client", connect)
    return fake


def _row(factory, task_id: str) -> WebTaskRow:
    with factory() as db:
        return service.get_task(db, uuid.UUID(task_id))


def test_a_cloud_task_starts_unattended_and_its_workflow_is_started(
    factory, registry, temporal
) -> None:
    cloud = _device("cloud-worker", platform="cloud", capabilities=TASK_OPERATIONS)
    registry.append(cloud)
    client = TestClient(_app(factory))

    answer = client.post("/v1/web-tasks", json={"goal": GOAL, "target_word": "bulutta"})

    assert answer.status_code == 202, answer.text
    body = answer.json()
    assert body["target"] == "cloud" and body["attended"] is False
    assert body["device_id"] == str(cloud.id)
    row = _row(factory, body["task_id"])
    assert row.status == STATUS_RUNNING and row.source == SOURCE_REST
    assert row.attended is False and row.device_id == cloud.id
    (started,) = temporal.started
    assert started["id"] == service.workflow_id_for(row.id)
    assert started["request"].task_id == body["task_id"]
    assert started["task_queue"] == "pagentos-main"
    assert started["run"] == BrowserTaskWorkflow.run


def test_a_second_task_while_one_is_in_flight_is_409(factory, registry, temporal) -> None:
    registry.append(_device("pc", labels=("owner_chrome",)))
    client = TestClient(_app(factory))
    first = client.post("/v1/web-tasks", json={"goal": GOAL})
    assert first.status_code == 202 and first.json()["attended"] is True
    assert first.json()["target"] == "owner_chrome"

    second = client.post("/v1/web-tasks", json={"goal": "Başka bir şey"})

    assert second.status_code == 409
    assert second.json()["detail"]["error_class"] == "task_in_flight"
    assert len(temporal.started) == 1


def test_nowhere_to_run_is_409_in_turkish_and_starts_nothing(factory, registry, temporal) -> None:
    client = TestClient(_app(factory))
    answer = client.post("/v1/web-tasks", json={"goal": GOAL, "target_word": "bulutta"})
    assert answer.status_code == 409
    assert answer.json()["detail"] == {
        "error_class": "no_capable_device",
        "detail": "Bulut şu anda çevrimiçi değil.",
    }
    assert temporal.started == []


def test_an_empty_goal_is_refused_before_anything_is_chosen(factory, registry, temporal) -> None:
    client = TestClient(_app(factory))
    assert client.post("/v1/web-tasks", json={"goal": ""}).status_code == 422
    assert client.post("/v1/web-tasks", json={"goal": GOAL, "extra": 1}).status_code == 422


def test_get_answers_with_the_task_dict(factory, registry, temporal) -> None:
    registry.append(_device("pc", labels=("owner_chrome",)))
    client = TestClient(_app(factory))
    task_id = client.post("/v1/web-tasks", json={"goal": GOAL}).json()["task_id"]

    answer = client.get(f"/v1/web-tasks/{task_id}")

    assert answer.status_code == 200
    assert answer.json() == service.task_dict(_row(factory, task_id))
    assert client.get(f"/v1/web-tasks/{uuid.uuid4()}").status_code == 404


def _park_for_confirmation(factory, task_id: str) -> None:
    """The row as a round leaves it when it reads a step back to the owner."""
    with factory() as db:
        row = service.get_task(db, uuid.UUID(task_id))
        state = service.load(row)
        state.status = STATUS_WAITING_OWNER
        state.pending = Pending(
            kind=ASK_CONFIRM,
            message="Onaylıyor musunuz?",
            step=Step(action="click", ref="e1"),
            step_digest="d1",
            facts={"element": "Gönder"},
        )
        service._write(row, state)
        db.commit()


def _started(factory, registry, temporal) -> tuple[TestClient, str]:
    registry.append(_device("pc", labels=("owner_chrome",)))
    client = TestClient(_app(factory))
    return client, client.post("/v1/web-tasks", json={"goal": GOAL}).json()["task_id"]


def test_confirm_needs_the_read_back_then_applies_and_wakes_the_workflow(
    factory, registry, temporal
) -> None:
    client, task_id = _started(factory, registry, temporal)
    _park_for_confirmation(factory, task_id)

    early = client.post(f"/v1/web-tasks/{task_id}/confirm", json={})
    assert early.status_code == 409
    assert early.json()["detail"]["error_class"] == "not_read_back"
    assert temporal.signals == []

    shown = client.post(f"/v1/web-tasks/{task_id}/read-back")
    assert shown.status_code == 200
    assert _row(factory, task_id).read_back_session_id == OWNER_SESSION

    answer = client.post(f"/v1/web-tasks/{task_id}/confirm", json={"source": "rest"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == STATUS_RUNNING
    assert service.load(_row(factory, task_id)).grant == {"source": "rest", "step_digest": "d1"}
    assert temporal.signals == [(service.workflow_id_for(uuid.UUID(task_id)), "woken")]


def test_decline_and_continue_change_the_row_and_wake_the_workflow(
    factory, registry, temporal
) -> None:
    client, task_id = _started(factory, registry, temporal)
    _park_for_confirmation(factory, task_id)
    declined = client.post(f"/v1/web-tasks/{task_id}/decline")
    assert declined.status_code == 200 and declined.json()["status"] == STATUS_RUNNING
    assert "onaylamadı" in service.load(_row(factory, task_id)).answers[-1]

    # "devam" on a running task is not a word that applies.
    refused = client.post(f"/v1/web-tasks/{task_id}/continue", json={"answer": "x"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["error_class"] == "not_waiting"

    with factory() as db:
        row = service.get_task(db, uuid.UUID(task_id))
        state = service.load(row)
        state.status = STATUS_WAITING_OWNER
        state.pending = Pending(kind="login", message="Giriş yapın.")
        service._write(row, state)
        db.commit()
    continued = client.post(f"/v1/web-tasks/{task_id}/continue", json={"answer": "girdim"})
    assert continued.status_code == 200 and continued.json()["status"] == STATUS_RUNNING
    assert service.load(_row(factory, task_id)).answers[-1] == "girdim"
    wid = service.workflow_id_for(uuid.UUID(task_id))
    assert temporal.signals == [(wid, "woken"), (wid, "woken")]


def test_cancel_writes_the_owners_word_and_signals_cancel(factory, registry, temporal) -> None:
    client, task_id = _started(factory, registry, temporal)
    _park_for_confirmation(factory, task_id)
    answer = client.post(f"/v1/web-tasks/{task_id}/cancel")
    assert answer.status_code == 200 and answer.json()["status"] == STATUS_CANCELLED
    assert _row(factory, task_id).cancel_requested is True
    assert temporal.signals == [(service.workflow_id_for(uuid.UUID(task_id)), "cancel")]


def test_without_an_owner_session_nothing_is_reachable(factory, registry, temporal) -> None:
    client = TestClient(_app(factory, owner=False))
    assert client.post("/v1/web-tasks", json={"goal": GOAL}).status_code in (401, 403)
    assert client.get(f"/v1/web-tasks/{uuid.uuid4()}").status_code in (401, 403)
    assert client.post(f"/v1/web-tasks/{uuid.uuid4()}/cancel").status_code in (401, 403)
    assert temporal.started == []


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_temporal_unreachable_is_an_honest_503_and_the_row_says_failed(factory, registry) -> None:
    registry.append(_device("pc", labels=("owner_chrome",)))
    client = TestClient(_app(factory, temporal_address=f"127.0.0.1:{_closed_port()}"))

    answer = client.post("/v1/web-tasks", json={"goal": GOAL})

    assert answer.status_code == 503, answer.text
    detail = answer.json()["detail"]
    assert detail["error_class"] == "temporal_unavailable"
    assert detail["detail"] == "Görev başlatılamadı: iş akışı sunucusuna ulaşılamıyor."
    row = _row(factory, detail["task_id"])
    assert row.status == STATUS_FAILED and row.failure == "temporal_unavailable"
    # ...and it does not refuse the next task as "in flight".
    with factory() as db:
        assert service.active_task(db) is None


MAIN = Path(__file__).resolve().parents[2] / "app" / "main.py"
INCLUDE = "app.include_router(web_task_router)"


@pytest.mark.parametrize("mounted", [INCLUDE in MAIN.read_text(encoding="utf-8")])
def test_the_router_answers_on_the_real_application_once_it_is_mounted(mounted: bool) -> None:
    """The lead adds ONE line to ``create_app`` at merge time (the ADR carries it). Until
    then this case passes as 'not mounted yet'; from then on the real application must
    answer on ``/v1/web-tasks`` - behind the owner session, never a 404."""
    if not mounted:
        assert "web_task_router" not in MAIN.read_text(encoding="utf-8")
        return
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    app.state.identity = None
    client = TestClient(app)
    # A path no router has is a 404 before any session is asked for; ours is mounted and
    # refuses without the owner's session. (``app.routes`` does not list every included
    # router of ``create_app``, so the answer is the evidence, not the route table.)
    assert client.post("/v1/web-tasks-not-a-route", json={}).status_code == 404
    assert client.post("/v1/web-tasks", json={"goal": GOAL}).status_code in (401, 403)
    assert client.get(f"/v1/web-tasks/{uuid.uuid4()}").status_code in (401, 403)
