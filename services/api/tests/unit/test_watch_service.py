"""The watch store and ``/v1/watches`` (watch-engine), on SQLite and through ``create_app``.

What a watch is allowed to be (a public http(s) page, one of four conditions, 1..168 hours,
at most 20), what the tables may hold (a hash and one value - never page text), how a watch
is forgotten, and the whole path from POST to a notification and a ledger row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.devices.types import DeviceView
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.notifications.models import NotificationRow
from app.research.browser_gateway import PageDigest
from app.watch import compare, runner, service
from app.watch.models import Watch, WatchReading
from app.watch.reader import CloudReader, Observation
from tests.identity_support import authenticate, install_identity

NOON = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
PAGE_TEXT = "Gizli sayfa metni: Home Assistant 2026.10 KARARLI SÜRÜM, fiyat 19.499 TL"
URL = "https://www.home-assistant.io/blog/"


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        Watch.__table__,
        WatchReading.__table__,
        NotificationRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(host: str) -> list[str]:
        return ["100.90.158.26"] if host == "tailnet.example" else ["93.184.216.34"]

    monkeypatch.setattr("app.research.destination.resolve_hostname", resolve)


def _create(db, **kw):
    arguments = {"url": URL, "condition": "changed", "label": "HA", "now": NOON}
    arguments.update(kw)
    return service.create_watch(db, **arguments)


# ------------------------------------------------------------------ creation


def test_create_list_and_remove(factory) -> None:
    with factory() as db:
        view = _create(db, condition="number_below:20000", selector=".price", every_hours=2)
        db.commit()
    assert view.condition == "number_below:20000"
    assert (view.every_hours, view.selector, view.consecutive_failures) == (2, ".price", 0)
    assert view.last_read_at is None and view.last_outcome is None and view.last_value is None
    with factory() as db:
        listed = service.list_watches(db)
        assert [w.id for w in listed] == [view.id]
        assert service.remove_watch(db, uuid.UUID(view.id)) is True
        assert service.remove_watch(db, uuid.UUID(view.id)) is False
        db.commit()
        assert service.list_watches(db) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://100.90.158.26:8001/v1/system/health",
        "http://100.64.0.1/",
        "http://127.0.0.1:8000/",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "https://tailnet.example/",  # a public-looking name that resolves into the tailnet
        "https://user:pw@example.com/",
    ],
)
def test_a_non_public_or_non_http_url_is_refused_in_turkish(factory, url: str) -> None:
    with factory() as db, pytest.raises(service.WatchRefused) as refused:
        _create(db, url=url)
    assert refused.value.reason_tr
    assert (
        any(ch in refused.value.reason_tr for ch in "çğıöşüÇĞİÖŞÜ")
        or " " in refused.value.reason_tr
    )
    with factory() as db:
        assert db.execute(select(func.count()).select_from(Watch)).scalar_one() == 0


@pytest.mark.parametrize(
    "kw",
    [
        {"every_hours": 0},
        {"every_hours": 169},
        {"condition": "whenever"},
        {"condition": "number_below:çok"},
        {"selector": "x" * 201},
        {"label": "   "},
    ],
)
def test_bad_fields_are_refused(factory, kw) -> None:
    with factory() as db, pytest.raises(service.WatchRefused):
        _create(db, **kw)


def test_at_most_twenty_watches(factory) -> None:
    with factory() as db:
        for n in range(20):
            _create(db, label=f"w{n}")
        db.commit()
        with pytest.raises(service.WatchRefused) as refused:
            _create(db, label="w20")
    assert "20" in refused.value.reason_tr


# ------------------------------------------------------------------ storage


EXPECTED_WATCH_COLUMNS = {
    "id",
    "label",
    "url",
    "condition",
    "every_hours",
    "selector",
    "created_at",
    "next_due_at",
    "last_read_at",
    "last_outcome",
    "last_value",
    "baseline_sha256",
    "condition_met",
    "consecutive_failures",
}
EXPECTED_READING_COLUMNS = {
    "id",
    "watch_id",
    "read_at",
    "text_sha256",
    "value",
    "outcome",
    "reason",
    "notified",
}


def test_no_column_of_either_table_holds_page_text(engine, factory) -> None:
    columns = inspect(engine)
    assert {c["name"] for c in columns.get_columns("watches")} == EXPECTED_WATCH_COLUMNS
    assert {c["name"] for c in columns.get_columns("watch_readings")} == EXPECTED_READING_COLUMNS
    # And behaviourally: a reading of a page leaves no piece of its text in either table.
    with factory() as db:
        view = _create(db, condition="contains:kararlı sürüm")
        db.commit()
        watch = db.get(Watch, uuid.UUID(view.id))
        runner.apply_observation(
            db,
            watch,
            Observation(ok=True, text_sha256=compare.text_sha256(PAGE_TEXT), text=PAGE_TEXT),
            now=NOON,
        )
        stored = [
            value
            for model in (Watch, WatchReading)
            for row in db.execute(select(model)).scalars()
            for value in vars(row).values()
            if isinstance(value, str)
        ]
    assert stored
    assert not any("Gizli sayfa metni" in v or "19.499" in v for v in stored)


def test_forget_all_leaves_zero_rows_in_both(factory) -> None:
    with factory() as db:
        view = _create(db)
        db.commit()
        runner.apply_observation(
            db,
            db.get(Watch, uuid.UUID(view.id)),
            Observation(ok=True, text_sha256=compare.text_sha256("a"), text="a"),
            now=NOON,
        )
        assert service.forget_all(db) == 1
        db.commit()
        assert db.execute(select(func.count()).select_from(Watch)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(WatchReading)).scalar_one() == 0


def test_changes_since_lists_what_the_owner_was_told(factory) -> None:
    with factory() as db:
        view = _create(db, condition="number_below:20000", selector=".p")
        db.commit()
        wid = uuid.UUID(view.id)
        for hours, text in ((0, "21.000 TL"), (6, "20.500 TL"), (12, "19.999 TL")):
            runner.apply_observation(
                db,
                db.get(Watch, wid),
                Observation(ok=True, text_sha256=compare.text_sha256(text), text=text),
                now=NOON + timedelta(hours=hours),
            )
        changes = service.changes_since(db, NOON - timedelta(hours=1))
    assert [c.outcome for c in changes] == ["condition_met"]
    change = changes[0]
    assert (change.value, change.previous_value) == (19999.0, 20500.0)
    assert change.watch_id == view.id and change.label == "HA"
    assert len(change.line_tr) <= 120


# ------------------------------------------------------------------ (7) through create_app


class FakeGateway:
    """Honours the fetch_evidence/selector CONTRACT: a digest of the matched text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, str | None]] = []

    def fetch_page_digest(self, url: str, *, selector: str | None = None) -> PageDigest:
        self.calls.append((url, selector))
        return PageDigest(
            url=url,
            final_url=url,
            excerpt=self.text,
            text_sha256=compare.text_sha256(self.text),
            selector_matched=True if selector else None,
            page_kind="ok",
            http_status=200,
        )

    def close_session(self) -> None:
        pass


def _cloud() -> DeviceView:
    return DeviceView(
        id=uuid.UUID(int=7),
        name="pagentos-cloud-browser",
        platform="cloud",
        status="enrolled",
        presence="online",
        capabilities=("browser.fetch_evidence", "browser.session_open"),
        enrolled_at=NOON,
        last_seen_at=NOON,
    )


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


def test_post_then_one_reading_gives_a_notification_and_a_ledger_row(api, factory) -> None:
    app, client = api
    created = client.post(
        "/v1/watches",
        json={
            "url": URL,
            "condition": "number_below:20000",
            "label": "Fiyat",
            "every_hours": 6,
            "selector": ".price",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["condition"] == "number_below:20000"
    gateway = FakeGateway("19.499 TL")
    app.state.watch_runner.reader = CloudReader(
        views=lambda db: [_cloud()], gateway_factory=lambda device_id, task_id: gateway
    )
    assert app.state.watch_runner.run_due(datetime.now(UTC) + timedelta(seconds=1)) == 1
    assert gateway.calls == [(URL, ".price")]
    with factory() as db:
        notes = list(db.execute(select(NotificationRow)).scalars())
        events = list(db.execute(select(ActivityEventRow)).scalars())
    assert [n.kind for n in notes] == ["watch.condition_met"]
    assert notes[0].group_key == f"watch:{body['id']}"
    assert len(notes[0].body) <= 120 and "\n" not in notes[0].body
    assert [(e.event_type, e.subsystem) for e in events] == [("watch.condition_met", "watch")]
    listed = client.get("/v1/watches").json()["items"]
    assert listed[0]["last_outcome"] == "condition_met"
    assert listed[0]["last_value"] == pytest.approx(19499)


def test_rest_refusal_delete_and_forget(api) -> None:
    _, client = api
    refused = client.post(
        "/v1/watches", json={"url": "http://127.0.0.1/", "condition": "changed", "label": "x"}
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "watch_refused"
    one = client.post("/v1/watches", json={"url": URL, "condition": "changed", "label": "a"}).json()
    client.post("/v1/watches", json={"url": URL, "condition": "changed", "label": "b"})
    assert client.delete(f"/v1/watches/{one['id']}").json() == {"deleted": 1}
    assert client.delete(f"/v1/watches/{one['id']}").status_code == 404
    assert client.delete("/v1/watches").json() == {"deleted": 1}
    assert client.get("/v1/watches").json()["items"] == []


def test_the_routes_need_the_owner(engine) -> None:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    assert TestClient(app).get("/v1/watches").status_code == 401


def test_the_loops_show_on_the_health_surface(api) -> None:
    app, client = api
    checks = client.get("/v1/system/health").json()["checks"]
    assert checks["watch_purge"]["retention_days"] == 30
    assert checks["watch_runner"]["status"] == "skipped"  # off until browser-redirect-guard
    assert app.state.watch_runner.enabled is False
