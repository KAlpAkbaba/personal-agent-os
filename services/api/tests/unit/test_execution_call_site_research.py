"""The research run's device comes from the execution_target rule (ADR-0213, order 2b).

``app.execution.wiring.choose`` was reachable and had no caller: ``start_browser_research``
picked its device with ``select_device`` alone, which knows no platform, so the cloud worker
('bulut', platform ``cloud``) was chosen or skipped by health order. These tests drive the
real ``start_browser_research`` over real ``devices`` rows (only presence is a fake broker)
and read the run row and the ledger back; the ``attached`` half drives the real activities.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.broker.models import DEVICE_STATUS_ENROLLED, Device
from app.config import Settings
from app.devices.commands import CommandFailed, CommandSucceeded
from app.devices.types import DeviceView
from app.execution import wiring
from app.execution.rule import Decision, JobKind, Skip, Target
from app.ledger.models import ActivityEventRow
from app.research import browser_activities as ba
from app.research import destination, runs_service
from app.research import service as research_service
from app.research.models import STAGE_FAILED, STAGE_PLANNED
from tests.device_command_support import FakeDeviceCommandClient
from tests.unit.test_research_browser_activities import ALL_TABLES

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
CAPS = ["browser.chrome"]


class FakeBroker:
    """Presence only: which device ids hold a connection."""

    def __init__(self) -> None:
        self.online: set[uuid.UUID] = set()

    def is_online(self, device_id: uuid.UUID) -> bool:
        return device_id in self.online


class Registry:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = create_engine(url)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.broker = FakeBroker()

    def enroll(
        self,
        name: str,
        *,
        platform: str = "windows",
        online: bool = True,
        seen_s_ago: float = 60.0,
        labels: tuple[str, ...] = (),
        aliases: tuple[str, ...] = (),
        status: str = DEVICE_STATUS_ENROLLED,
    ) -> uuid.UUID:
        with self.factory() as db:
            device = Device(
                name=name,
                platform=platform,
                public_key_spki_b64="unit",
                capabilities_json=list(CAPS),
                status=status,
                last_seen_at=datetime.now(UTC) - timedelta(seconds=seen_s_ago),
                metadata_json={"labels": list(labels), "aliases": list(aliases)},
            )
            db.add(device)
            db.commit()
            device_id = device.id
        if online:
            self.broker.online.add(device_id)
        return device_id

    def cloud(self, **kw) -> uuid.UUID:
        # Seen LONGER ago than any machine: the old rule (health order) never prefers it,
        # so a test that expects the cloud is not passing by luck.
        kw.setdefault("seen_s_ago", 300.0)
        return self.enroll("bulut-1", platform="cloud", aliases=("bulut",), **kw)

    def mail(self, **kw) -> uuid.UUID:
        kw.setdefault("seen_s_ago", 1.0)
        return self.enroll("MAIL", aliases=("ev",), **kw)

    def start(self, **kw) -> research_service.StartedResearch:
        with self.factory() as db:
            return research_service.start_browser_research(db, self.broker, input="konu", **kw)

    def run(self, task_id: uuid.UUID):
        with self.factory() as db:
            return runs_service.get_run(db, task_id)

    def ledger(self) -> list[tuple[str, dict]]:
        query = select(ActivityEventRow).order_by(ActivityEventRow.source_ref)
        with self.factory() as db:
            return [(r.event_type, dict(r.detail_json)) for r in db.execute(query).scalars()]


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / f'callsite_{uuid.uuid4().hex}.db'}"
    bootstrap = create_engine(url)
    for table in (*ALL_TABLES, ActivityEventRow.__table__):
        table.create(bootstrap)
    bootstrap.dispose()
    settings = Settings(_env_file=None, database_url=url, research_browser="owner_chrome")
    monkeypatch.setattr(ba, "get_settings", lambda: settings)
    reg = Registry(url)
    try:
        yield reg
    finally:
        reg.engine.dispose()


def _planned(run) -> dict:
    return [e for e in run.events_json if e.get("stage") == STAGE_PLANNED][-1]


# ------------------------------------------------------------- the rule picks the device


def test_with_the_cloud_and_a_machine_online_the_run_is_planned_on_the_cloud(registry) -> None:
    registry.mail()  # enrolled first and seen last: every old order prefers it
    cloud = registry.cloud()

    started = registry.start()

    assert started.error is None
    assert started.device == {"device_id": str(cloud), "name": "bulut-1"}
    run = registry.run(started.task_id)
    assert run.device_id == cloud
    planned = _planned(run)
    assert planned["execution_target"] == "cloud"
    assert planned["execution_chain"] == ["cloud", "owner_chrome", "device"]
    assert planned["execution_skipped"] == []
    ledger = registry.ledger()
    assert [t for t, _ in ledger] == ["execution.selected"]
    assert ledger[0][1]["target"] == "cloud"


def test_the_ledger_rows_name_the_research_they_decided(registry) -> None:
    registry.cloud()
    started = registry.start()
    with registry.factory() as db:
        rows = db.execute(select(ActivityEventRow)).scalars().all()
    assert [r.research_job_id for r in rows] == [started.task_id]
    assert rows[0].source_ref == f"execution:{started.task_id}:0"


def test_with_the_cloud_offline_the_run_falls_to_the_machine_and_says_so(registry) -> None:
    registry.cloud(online=False)
    mail = registry.mail()

    started = registry.start()

    assert started.device == {"device_id": str(mail), "name": "MAIL"}
    assert registry.run(started.task_id).device_id == mail
    ledger = registry.ledger()
    assert [t for t, _ in ledger] == [
        "execution.fallback",
        "execution.fallback",
        "execution.selected",
    ]
    assert ledger[0][1]["skipped_target"] == "cloud"
    assert ledger[0][1]["reason"] == "cloud_offline"
    assert ledger[-1][1]["target"] == "device"
    planned = _planned(registry.run(started.task_id))
    assert planned["execution_target"] == "device"
    assert planned["execution_skipped"][0] == {"target": "cloud", "reason": "cloud_offline"}


def test_a_run_that_needs_a_signed_in_session_never_goes_to_the_cloud(registry) -> None:
    registry.cloud(seen_s_ago=0.5)  # the old rule WOULD pick it
    mail = registry.mail()

    started = registry.start(needs_signed_in_session=True)

    assert started.device["device_id"] == str(mail)
    assert _planned(registry.run(started.task_id))["execution_chain"] == [
        "owner_chrome",
        "device",
    ]
    assert "cloud" not in [d.get("target") for _, d in registry.ledger()]


def test_bulutta_with_the_cloud_offline_is_a_failed_run_and_no_device(registry) -> None:
    registry.cloud(online=False)
    registry.mail()

    started = registry.start(named_devices=("bulutta",))

    assert started.device is None
    assert started.error == "Bulut şu anda çevrimiçi değil."
    run = registry.run(started.task_id)
    assert run.stage == STAGE_FAILED and run.device_id is None
    failed = [e for e in run.events_json if e.get("stage") == STAGE_FAILED][-1]
    assert failed["execution_reason"] == "forced_target_unavailable"
    ledger = registry.ledger()
    assert [t for t, _ in ledger] == ["execution.fallback", "execution.refused"]
    assert ledger[-1][1]["reason"] == "forced_target_unavailable"


def test_bulutta_with_the_cloud_online_runs_there_and_says_why(registry) -> None:
    cloud = registry.cloud()
    registry.mail()

    started = registry.start(named_devices=("bulutta",))

    assert started.device["device_id"] == str(cloud)
    assert started.device["reason_tr"] == research_service.NAMED_DEVICE_TR
    assert registry.ledger()[-1][1]["forced"] is True


def test_a_revoked_cloud_device_is_never_selected(registry) -> None:
    registry.cloud(status="revoked", seen_s_ago=0.5)  # still holding a connection
    mail = registry.mail()

    started = registry.start()

    assert started.device["device_id"] == str(mail)
    assert registry.ledger()[0][1]["reason"] == "cloud_offline"


def test_with_the_cloud_offline_the_enrolled_owner_chrome_machine_is_next(registry) -> None:
    registry.cloud(online=False)
    registry.mail()  # seen most recently: the old rule picks it
    office = registry.enroll("GMKADIRAKBABA", labels=("owner_chrome",), aliases=("ofis",))

    started = registry.start()

    assert started.device["device_id"] == str(office)
    assert _planned(registry.run(started.task_id))["execution_target"] == "owner_chrome"


def test_a_named_machine_is_still_the_machine_and_never_the_cloud(registry) -> None:
    registry.cloud()
    registry.mail()
    office = registry.enroll("GMKADIRAKBABA", aliases=("ofis",))

    started = registry.start(named_devices=("ofis",))

    assert started.device["device_id"] == str(office)
    assert _planned(registry.run(started.task_id))["execution_target"] == "device"


def test_a_named_machine_that_is_offline_keeps_its_own_sentence(registry) -> None:
    registry.cloud()
    registry.mail()
    registry.enroll("GMKADIRAKBABA", aliases=("ofis",), online=False)

    started = registry.start(named_devices=("ofis",))

    assert started.device is None
    assert started.error == "'ofis' cihazı şu anda çevrimiçi değil."
    failed = registry.run(started.task_id).events_json[-1]
    assert failed["execution_reason"] == "forced_target_unavailable"


def test_nothing_online_keeps_the_sentence_it_always_had(registry) -> None:
    registry.cloud(online=False)
    registry.mail(online=False)

    started = registry.start()

    assert started.error == "Şu anda çevrimiçi bir cihaz bulunamadı."
    assert registry.run(started.task_id).events_json[-1]["execution_reason"] == (
        "no_target_available"
    )


def test_a_rest_callers_own_target_device_is_not_overruled_by_the_rule(registry) -> None:
    registry.cloud()
    mail = registry.mail()

    started = registry.start(target_device="MAIL")

    assert started.device["device_id"] == str(mail)
    assert registry.ledger() == []
    assert "execution_target" not in _planned(registry.run(started.task_id))


def test_a_ledger_that_cannot_be_written_does_not_stop_the_research(tmp_path) -> None:
    """The ledger rows are the record of the decision, not the decision: a start is never
    failed because they could not be written, and the run's own PLANNED event still says
    where it went. (The same stance as the task.failed notification on this path.)"""
    url = f"sqlite:///{tmp_path / 'no_ledger.db'}"
    bootstrap = create_engine(url)
    for table in ALL_TABLES:  # no activity_events
        table.create(bootstrap)
    bootstrap.dispose()
    reg = Registry(url)
    try:
        reg.mail()
        cloud = reg.cloud()
        started = reg.start()
        assert started.error is None and started.device["device_id"] == str(cloud)
        run = reg.run(started.task_id)
        assert run.device_id == cloud and _planned(run)["execution_target"] == "cloud"
    finally:
        reg.engine.dispose()


def test_the_rule_itself_still_insists_on_its_ledger(tmp_path) -> None:
    """Best effort is the research start's choice, not the rule's default."""
    from sqlalchemy.exc import SQLAlchemyError

    url = f"sqlite:///{tmp_path / 'no_ledger_strict.db'}"
    bootstrap = create_engine(url)
    for table in ALL_TABLES:
        table.create(bootstrap)
    bootstrap.dispose()
    reg = Registry(url)
    try:
        reg.cloud()
        with reg.factory() as db, pytest.raises(SQLAlchemyError):
            wiring.choose(
                JobKind.RESEARCH,
                spoken_target=None,
                url=None,
                needs_signed_in_session=False,
                acting=False,
                scheduled=False,
                db=db,
                runtime=reg.broker,
            )
    finally:
        reg.engine.dispose()


# ------------------------------------------------------------------- wiring.device_for


def _view(name, *, platform="windows", presence="online", labels=(), status="enrolled"):
    return DeviceView(
        id=uuid.uuid4(),
        name=name,
        platform=platform,
        status=status,
        presence=presence,
        capabilities=tuple(CAPS),
        enrolled_at=NOW,
        last_seen_at=NOW,
        labels=tuple(labels),
    )


def _decision(target: Target | None, outcome: str = "selected") -> Decision:
    chain = (Target.CLOUD, Target.OWNER_CHROME, Target.DEVICE)
    return Decision(JobKind.RESEARCH, outcome, target, chain, (), "default")


def test_device_for_a_cloud_decision_is_the_online_cloud_view_not_the_first_online() -> None:
    mail, cloud = _view("MAIL"), _view("bulut-1", platform="cloud")
    assert wiring.device_for(_decision(Target.CLOUD), [mail, cloud]) is cloud


def test_device_for_skips_a_revoked_and_an_offline_cloud_view() -> None:
    revoked = _view("bulut-0", platform="cloud", status="revoked")
    offline = _view("bulut-1", platform="cloud", presence="offline")
    live = _view("bulut-2", platform="cloud")
    assert wiring.device_for(_decision(Target.CLOUD), [revoked, offline, live]) is live
    assert wiring.device_for(_decision(Target.CLOUD), [revoked, offline]) is None


def test_device_for_owner_chrome_is_the_online_labelled_machine() -> None:
    mail = _view("MAIL")
    cloud = _view("bulut-1", platform="cloud", labels=("owner_chrome",))  # never the cloud
    office = _view("GMKADIRAKBABA", labels=("owner_chrome",))
    assert wiring.device_for(_decision(Target.OWNER_CHROME), [mail, cloud, office]) is office


def test_device_for_a_device_decision_or_a_refusal_names_no_view() -> None:
    views = [_view("MAIL"), _view("bulut-1", platform="cloud")]
    assert wiring.device_for(_decision(Target.DEVICE), views) is None
    refused = Decision(
        JobKind.RESEARCH,
        "refused",
        None,
        (Target.CLOUD,),
        (Skip(Target.CLOUD, "cloud_offline"),),
        "forced_target_unavailable",
    )
    assert wiring.device_for(refused, views) is None


# ------------------------------------------------------- 'attached' on the cloud device


def test_attached_is_false_for_the_cloud_device_and_unchanged_for_a_machine(registry) -> None:
    cloud = registry.cloud()
    mail = registry.mail()
    assert ba._attached("owner_chrome", cloud) is False
    assert ba._attached("owner_chrome", mail) is True
    assert ba._attached("owner_chrome", uuid.uuid4()) is True  # an unknown id is not the cloud
    assert ba._attached("worker", mail) is False
    assert ba._attached("owner", mail) is False


def _cloud_worker_factory():
    """The cloud worker: it has a research profile and refuses the owner's."""
    seen: list[dict] = []

    def factory(*, capability, payload, **_kwargs):
        seen.append({"capability": capability, "payload": payload})
        if capability == "browser.session_open":
            if payload.get("profile") == "owner":
                return CommandFailed(
                    "capability_missing", "no browser is enrolled for attach", retryable=False
                )
            return CommandSucceeded({"created": True})
        if capability == "browser.session_close":
            return CommandSucceeded({"closed": True})
        if capability == "browser.search":
            return CommandSucceeded(
                {
                    "schema_version": 2,
                    "requested_provider": "duckduckgo",
                    "provider": "duckduckgo",
                    "state": "ok",
                    "path": "direct",
                    "page_kind": "ok",
                    "verification_url": None,
                    "results": [
                        {"url": "https://example.com/haber", "title": "Haber", "snippet": "s"}
                    ],
                    "result_count": 1,
                }
            )
        if capability == "browser.fetch_evidence":
            return CommandSucceeded(
                {
                    "url": payload.get("url"),
                    "final_url": payload.get("url"),
                    "title": "Yapay zeka haberleri",
                    "excerpt": "yapay zeka ajanları hakkında bir bulgu burada var. " * 5,
                    "http_status": 200,
                    "extraction_method": "dom_text",
                    "fetched_at": "2026-10-01T12:00:00Z",
                }
            )
        raise AssertionError(capability)  # pragma: no cover

    factory.seen = seen  # type: ignore[attr-defined]
    return factory


def _task(registry) -> str:
    from app.artifacts import service as artifact_service

    with registry.factory() as db:
        return str(artifact_service.create_task(db, intent="yapay zeka ajanları").id)


def _profiles(factory) -> list[str]:
    return [
        c["payload"].get("profile")
        for c in factory.seen
        if c["capability"] == "browser.session_open"
    ]


def _fallbacks(registry, task_id: str) -> list[dict]:
    run = registry.run(uuid.UUID(task_id))
    return [e for e in (run.events_json if run else []) if "browser_fallback" in e]


def test_a_search_on_the_cloud_device_asks_for_the_research_profile_directly(
    registry, monkeypatch
) -> None:
    cloud = registry.cloud()
    task_id = _task(registry)
    factory = _cloud_worker_factory()
    monkeypatch.setattr(ba, "_command_client", lambda: FakeDeviceCommandClient(factory=factory))

    ba.discover_activity(task_id, str(cloud), "news:0", "ai agents", "news", NOW.isoformat())

    assert _profiles(factory) == ["research"]
    assert _fallbacks(registry, task_id) == []


def test_a_page_on_the_cloud_device_is_read_by_the_research_profile_directly(
    registry, monkeypatch
) -> None:
    monkeypatch.setattr(destination, "resolve_hostname", lambda host: ["93.184.216.34"])
    cloud = registry.cloud()
    task_id = _task(registry)
    factory = _cloud_worker_factory()
    monkeypatch.setattr(ba, "_command_client", lambda: FakeDeviceCommandClient(factory=factory))

    outcome = ba.fetch_activity(task_id, str(cloud), "https://example.com/haber", "q", "news")

    assert outcome == "fetched"
    assert _profiles(factory) == ["research"]
    assert _fallbacks(registry, task_id) == []


def test_a_search_on_a_machine_still_attaches_to_the_owners_chrome(registry, monkeypatch) -> None:
    mail = registry.mail()
    task_id = _task(registry)
    factory = _cloud_worker_factory()
    monkeypatch.setattr(ba, "_command_client", lambda: FakeDeviceCommandClient(factory=factory))

    ba.discover_activity(task_id, str(mail), "news:0", "ai agents", "news", NOW.isoformat())

    assert _profiles(factory)[0] == "owner"
