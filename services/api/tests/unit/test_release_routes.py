"""Unit tests: the version model and the availability report as REST (M18.4 spec §2, §13)."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, Task, TaskRun
from app.artifacts.runtime import ArtifactRuntime
from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime, DeviceConnection
from app.config import Settings
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED,
    EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_ROLLED_BACK,
    EVENT_TYPE_INCIDENT_OPENED,
    SUBSYSTEM_DEPLOYMENT,
)
from app.main import create_app
from app.release.slo import slo_report
from tests.identity_support import authenticate

TABLES = [
    ActivityEventRow.__table__,
    Task.__table__,
    Artifact.__table__,
    TaskRun.__table__,
    AuditEvent.__table__,
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
]


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


@pytest.fixture()
def wired(monkeypatch):
    monkeypatch.setenv("PAGENTOS_RELEASE", "65459a4")
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = factory
    app.state.artifacts = artifacts
    broker = BrokerRuntime(settings)
    broker._engine = engine
    broker._session_factory = factory
    app.state.broker = broker
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    try:
        yield client, factory, broker
    finally:
        client.close()


def test_current_is_the_version_model(wired) -> None:
    client, _, _ = wired
    body = client.get("/v1/release/current").json()
    assert body["component"] == "cloud-core"
    assert body["version"] == "65459a4" and body["version_source"] == "env"
    assert "action" in body["contracts"]


def test_components_name_the_cloud_core_each_device_and_the_web_honestly(wired) -> None:
    client, _, broker = wired
    with broker.session() as db:
        enrolled = broker_service.enroll_device(
            db,
            name="ev-pc",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=["desktop.alarm_arm"],
            trace_id=None,
        )
    broker.connections[enrolled.id] = DeviceConnection(
        device_id=enrolled.id, session_id=uuid.uuid4(), websocket=object()
    )
    body = client.get("/v1/release/components").json()
    kinds = [c["component"] for c in body["components"]]
    assert kinds == ["cloud-core", "windows-agent", "web"]
    agent = body["components"][1]
    assert agent["name"] == "ev-pc" and agent["connected"] is True
    assert agent["capabilities"] == ["desktop.alarm_arm"]
    assert agent["version"] in ("unknown",) or isinstance(agent["version"], str)
    web = body["components"][2]
    assert web["version"] == "unknown_from_server" and web["observed"] == "browser_only"
    assert body["primary"] == "cloud-core"


def test_slo_counts_release_windows_and_claims_no_fraction(wired) -> None:
    client, factory, _ = wired
    now = datetime.now(UTC)
    with factory() as db:
        for event_type, when in (
            (EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED, now - timedelta(hours=2)),
            (EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED, now - timedelta(days=3)),
            (EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_ROLLED_BACK, now - timedelta(days=3)),
            (EVENT_TYPE_INCIDENT_OPENED, now - timedelta(days=9)),
        ):
            ledger_service.record(
                db,
                ledger_service.ActivityEvent(
                    event_type=event_type,
                    subsystem=SUBSYSTEM_DEPLOYMENT,
                    action="test",
                    factual_summary="x",
                    occurred_at=when,
                    source="test",
                    source_ref=f"{event_type}:{when.isoformat()}",
                ),
            )
        db.commit()
        report = slo_report(db, now=now)
    assert report["windows"]["24h"] == {"releases": 1, "rollbacks": 0, "incidents": 0}
    assert report["windows"]["7d"] == {"releases": 2, "rollbacks": 1, "incidents": 0}
    assert report["availability"] is None and report["measurement"] == "none"
    body = client.get("/v1/release/slo").json()
    assert body["windows"]["7d"]["releases"] == 2
    assert body["process_uptime_s"] >= 0


def test_the_surface_is_owner_gated(wired) -> None:
    client, _, _ = wired
    anonymous = TestClient(client.app)
    for path in ("/v1/release/current", "/v1/release/components", "/v1/release/slo"):
        assert anonymous.get(path).status_code == 401, path
