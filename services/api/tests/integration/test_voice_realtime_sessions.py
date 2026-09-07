"""M12 integration: realtime session records + tool relay against the real
PostgreSQL schema (migration 0011) through the authenticated API.

Requires the compose stack and the schema at head. Offline otherwise: the
selected ConversationRealtime provider is the deterministic simulator, so no
vendor key, no network, no audio. What this adds over the unit suite is the
real schema (JSONB context, check constraints, the per-session ``call_id``
uniqueness enforced by PostgreSQL) and real ``owner_sessions`` rows.
"""

from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.broker import service as broker_service
from app.broker.runtime import DeviceConnection
from app.config import Settings
from app.db import build_engine
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from tests.integration.conftest import owner_client, shared_identity

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    return owner_client(settings)


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def test_schema_has_the_m12_tables(settings: Settings) -> None:
    engine = build_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            names = {
                r[0] for r in conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('realtime_sessions', 'realtime_tool_calls')"
                ))
            }
        assert names == {"realtime_sessions", "realtime_tool_calls"}
    finally:
        engine.dispose()


def test_session_lifecycle_on_postgres(settings: Settings, client: TestClient) -> None:
    created = client.post("/v1/voice/realtime/sessions", json={"client_kind": "cli"})
    assert created.status_code == 201, created.text
    data = created.json()
    sid = data["session_id"]
    assert data["provider"] == "simulator"
    assert data["credential"]["secret"].startswith("sim_")

    # idempotent relay, enforced by the database's unique constraint
    body = {"call_id": "pg_call_1", "name": "clock.now", "arguments": {}}
    first = client.post(f"/v1/voice/realtime/sessions/{sid}/tool-calls", json=body).json()
    second = client.post(f"/v1/voice/realtime/sessions/{sid}/tool-calls", json=body).json()
    assert first["status"] == "succeeded" and second["replayed"] is True

    # long-running tool + completion. research.start runs the REAL device selection
    # (ADR-0067 amendment): with no online browser.chrome device it is a truthful,
    # immediate failure, so this test - which is about the relay's long-running path on
    # the real schema - enrolls one, marks it online in the broker runtime, and patches
    # the Temporal client the way the unit suite does. Nothing is started on the stack.
    broker = client.app.state.broker
    with broker.session() as db:
        device = broker_service.enroll_device(
            db,
            name=f"integration-research-{uuid.uuid4().hex[:8]}",
            platform="windows",
            public_key_spki_b64=_spki(),
            capabilities=["browser.chrome"],
            trace_id=None,
        )
    broker.connections[device.id] = DeviceConnection(
        device_id=device.id, session_id=uuid.uuid4(), websocket=object()
    )
    fake_temporal = AsyncMock()
    fake_temporal.start_workflow = AsyncMock(return_value=None)
    try:
        with patch("app.research.service.Client.connect", AsyncMock(return_value=fake_temporal)):
            running = client.post(f"/v1/voice/realtime/sessions/{sid}/tool-calls", json={
                "call_id": "pg_call_2", "name": "research.start",
                "arguments": {"topic": "Hetzner"},
            }).json()
    finally:
        broker.connections.pop(device.id, None)
    assert running["status"] == "running" and running["preamble"], running
    done = client.post(f"/v1/voice/realtime/sessions/{sid}/tool-calls/pg_call_2/complete",
                       json={"result": {"summary": "tamam"}})
    assert done.status_code == 200 and done.json()["status"] == "succeeded"

    # client events -> benchmark from the client's timestamps
    events = client.post(f"/v1/voice/realtime/sessions/{sid}/events", json={"events": [
        {"kind": "end_of_turn", "t_ms": 100, "turn": 1},
        {"kind": "first_audio", "t_ms": 620, "turn": 1},
        {"kind": "utterance", "t_ms": 700, "turn": 1, "text": "biraz daha yavaş"},
    ]})
    assert events.status_code == 200, events.text
    assert events.json()["resolved_intents"][0]["intent"] == "slower"
    report = client.get(f"/v1/voice/realtime/sessions/{sid}/benchmark").json()
    assert report["metrics"]["eot_to_first_audio_ms"]["p50"] == 520

    # continuity: a second owner session (another client) attaches
    identity = shared_identity(settings)
    phone = identity.service.issue_session(client_kind="mobile", label="integration-phone")
    attached = client.post(f"/v1/voice/realtime/sessions/{sid}/attach", json={},
                           headers={"Authorization": f"Bearer {phone.token}"})
    assert attached.status_code == 200, attached.text
    assert attached.json()["state"]["legs"] == 2
    assert attached.json()["credential"]["secret"] != data["credential"]["secret"]
    # the old leg is refused
    assert client.post(f"/v1/voice/realtime/sessions/{sid}/tool-calls",
                       json={"call_id": "pg_call_3", "name": "clock.now"}).status_code == 409

    closed = client.post(f"/v1/voice/realtime/sessions/{sid}/close",
                         headers={"Authorization": f"Bearer {phone.token}"})
    assert closed.status_code == 200 and closed.json()["state"] == "closed"

    engine = build_engine(settings.database_url)
    try:
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            row = db.get(RealtimeSessionRow, uuid.UUID(sid))
            assert row is not None and row.state == "closed"
            assert row.owner_session_id == phone.context.session_id
            assert row.context_json["plan"]["status"] == "completed"
            calls = list(db.execute(
                select(RealtimeToolCall).where(RealtimeToolCall.session_id == row.id)
            ).scalars())
            assert {c.call_id for c in calls} == {"pg_call_1", "pg_call_2"}
            audit = db.execute(text(
                "SELECT action FROM audit_events WHERE category = 'voice_realtime' "
                "AND subject_ref = :sid ORDER BY id"
            ), {"sid": sid}).fetchall()
            actions = [a[0] for a in audit]
            assert actions[0] == "voice_session_created"
            assert "voice_session_attached" in actions and actions[-1] == "voice_session_closed"
            # the minted secrets ("sim_...") never reach an audit row; the
            # non-secret session ref ("sim:<id>") does. Checked in Python:
            # "_" is a LIKE wildcard and would match the ref.
            blobs = [r[0] for r in db.execute(text(
                "SELECT metadata_json::text FROM audit_events "
                "WHERE category = 'voice_realtime' AND subject_ref = :sid"
            ), {"sid": sid}).fetchall()]
            assert blobs and all("sim_" not in blob for blob in blobs)
            assert any(f"sim:{sid}" in blob for blob in blobs)
    finally:
        engine.dispose()
