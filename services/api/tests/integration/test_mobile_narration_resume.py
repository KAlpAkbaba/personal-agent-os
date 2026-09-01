"""M9 integration: narration resumes on mobile from the cloud cursor.

M4 proved the cursor is durable. What M9 has to prove is the product claim in
MASTER_SPEC §B: "a task started on PC may be narrated on phone hours later" —
that a *separate authenticated session* picks the report up at the exact
position the first device left it, and that this keeps working after the first
device's session is gone.

The distinction matters. Two `TestClient`s over one database is a weaker claim
than two independent owner sessions: the second one is what a phone actually
holds, and it is the thing "device revocation invalidates session" acts on. So
device A here is a real desktop-kind session and the mobile client is a real
mobile-kind session with its own bearer token, and the resume is asserted after
device A's session has been revoked.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.artifacts import service as artifact_service
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.identity.runtime import IdentityRuntime
from tests.identity_support import bearer
from tests.integration.conftest import owner_client, shared_identity

pytestmark = pytest.mark.integration

BODY = """# Giriş

Toplam maliyet 1.250.000 Türk lirası. Kur farkı yüzde 3,42 seviyesine geriledi.

# İkinci Bölüm

Sunucunun IP adresi 192.168.1.20 olarak ayarlandı. Bağlantı sağlıklı.

# Üçüncü Bölüm

Rapor mobil cihazda kaldığı yerden devam edecek.
"""


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def identity(settings: Settings) -> IdentityRuntime:
    return shared_identity(settings)


@pytest.fixture(scope="module")
def client(settings: Settings) -> TestClient:
    """One app for the module; its pools are released afterwards.

    Each `create_app` opens a connection pool per subsystem and the dev
    PostgreSQL has a finite `max_connections`, so a per-test app here would
    starve the rest of the suite.
    """
    keep = shared_identity(settings).engine
    with owner_client(settings) as test_client:
        yield test_client
        for runtime in getattr(test_client.app.state, "_state", {}).values():
            engine = getattr(runtime, "_engine", None)
            if engine is not None and engine is not keep:
                engine.dispose()


@pytest.fixture(scope="module")
def artifact_id(settings: Settings) -> uuid.UUID:
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    session = factory()
    try:
        artifact = artifact_service.get_or_create_artifact_for_task(
            session, task_id=None, title="Mobil Devam Raporu"
        )
        artifact_service.add_artifact_version(
            session,
            artifact_id=artifact.id,
            canonical_body=BODY,
            content_hash=hashlib.sha256(BODY.encode("utf-8")).hexdigest(),
        )
        return artifact.id
    finally:
        session.close()
        engine.dispose()


def desktop_session(identity: IdentityRuntime) -> tuple[str, uuid.UUID]:
    issued = identity.service.issue_session(client_kind="desktop", label="device A (PC)")
    return issued.token, issued.context.session_id


def mobile_session(identity: IdentityRuntime) -> tuple[str, uuid.UUID]:
    issued = identity.service.issue_session(client_kind="mobile", label="phone")
    return issued.token, issued.context.session_id


# ------------------------------------------------------------------ the claim


def test_a_separate_mobile_session_resumes_from_the_exact_cloud_cursor(
    client: TestClient, identity: IdentityRuntime, artifact_id: uuid.UUID
) -> None:
    desktop_token, _ = desktop_session(identity)
    mobile_token, _ = mobile_session(identity)
    device_a = uuid.uuid4()
    phone = uuid.uuid4()

    # 1. Device A (the PC) starts narrating and advances through the report.
    created = client.post(
        "/v1/narration/sessions",
        json={"artifact_id": str(artifact_id), "device_id": str(device_a)},
        headers=bearer(desktop_token),
    )
    assert created.status_code == 201, created.text
    narration_id = created.json()["session_id"]

    client.post(
        f"/v1/narration/sessions/{narration_id}/command",
        json={"utterance": "oku", "device_id": str(device_a)},
        headers=bearer(desktop_token),
    )
    jumped = client.post(
        f"/v1/narration/sessions/{narration_id}/command",
        json={"command": "maddeye_gec", "target_index": 2, "device_id": str(device_a)},
        headers=bearer(desktop_token),
    ).json()
    paused = client.post(
        f"/v1/narration/sessions/{narration_id}/command",
        json={"utterance": "dur", "device_id": str(device_a)},
        headers=bearer(desktop_token),
    ).json()
    cursor_on_pc = paused["cursor"]
    assert cursor_on_pc == jumped["cursor"]
    assert paused["state"] == "PAUSED"

    # 2. The phone — a *different* owner session — reads the cloud cursor.
    on_phone = client.get(
        f"/v1/narration/sessions/{narration_id}/cursor", headers=bearer(mobile_token)
    )
    assert on_phone.status_code == 200
    assert on_phone.json()["cursor"] == cursor_on_pc

    # 3. ...and resumes from exactly there, from its own device.
    resumed = client.post(
        f"/v1/narration/sessions/{narration_id}/command",
        json={"utterance": "devam et", "device_id": str(phone)},
        headers=bearer(mobile_token),
    ).json()
    assert resumed["state"] == "READING"
    assert resumed["cursor"] == cursor_on_pc
    assert resumed["current_chunk"] is not None
    assert resumed["device_id"] == str(phone)


def test_the_cursor_survives_revocation_of_the_device_that_wrote_it(
    client: TestClient, identity: IdentityRuntime, artifact_id: uuid.UUID
) -> None:
    """The owner's position belongs to the owner, not to the device.

    Revoking the PC's session must kill the PC's access and nothing else — the
    cursor stays in the cloud and the phone continues from it. This is the
    other half of "device revocation invalidates session": what is destroyed is
    authority, never the owner's work.
    """
    desktop_token, desktop_id = desktop_session(identity)
    mobile_token, _ = mobile_session(identity)

    narration_id = client.post(
        "/v1/narration/sessions",
        json={"artifact_id": str(artifact_id)},
        headers=bearer(desktop_token),
    ).json()["session_id"]
    client.post(
        f"/v1/narration/sessions/{narration_id}/command",
        json={"utterance": "oku"},
        headers=bearer(desktop_token),
    )
    cursor = client.patch(
        f"/v1/narration/sessions/{narration_id}/cursor",
        json={
            "cursor": {
                "section_id": "s3",
                "paragraph_id": "p7",
                "sentence_index": 2,
                "char_offset": 0,
            },
            "playback_seconds": 91.5,
        },
        headers=bearer(desktop_token),
    ).json()["cursor"]

    # The owner loses the PC and revokes its session.
    assert identity.service.revoke_session(desktop_id, reason="owner_revoked") is True
    assert (
        client.get(
            f"/v1/narration/sessions/{narration_id}/cursor", headers=bearer(desktop_token)
        ).status_code
        == 401
    )

    # The phone still resumes from the exact cursor the PC left behind.
    on_phone = client.get(
        f"/v1/narration/sessions/{narration_id}/cursor", headers=bearer(mobile_token)
    ).json()
    assert on_phone["cursor"] == cursor
    assert on_phone["playback_seconds"] == 91.5
    resumed = client.post(
        f"/v1/narration/sessions/{narration_id}/command",
        json={"utterance": "devam et"},
        headers=bearer(mobile_token),
    ).json()
    assert resumed["state"] == "READING"
    assert resumed["cursor"] == cursor


def test_a_lifecycle_checkpoint_from_the_phone_is_readable_everywhere(
    client: TestClient, identity: IdentityRuntime, artifact_id: uuid.UUID
) -> None:
    """The mobile lifecycle machine's checkpoints land in the cloud cursor.

    The machine is unit-tested offline; this asserts the flush it prescribes
    (background / interruption / terminate) is a cursor another device reads.
    """
    from app.mobile.lifecycle import MobileLifecycle

    mobile_token, _ = mobile_session(identity)
    desktop_token, _ = desktop_session(identity)
    phone = uuid.uuid4()

    narration_id = client.post(
        "/v1/narration/sessions",
        json={"artifact_id": str(artifact_id), "device_id": str(phone)},
        headers=bearer(mobile_token),
    ).json()["session_id"]
    reading = client.post(
        f"/v1/narration/sessions/{narration_id}/command",
        json={"utterance": "oku", "device_id": str(phone)},
        headers=bearer(mobile_token),
    ).json()

    machine = MobileLifecycle()
    machine.start_narration(session_id=narration_id, cursor=reading["cursor"])
    machine.to_background()          # checkpoint 1
    machine.interrupt(reason="phone_call")  # checkpoint 2
    machine.resume()
    machine.terminate(reason="os_killed")   # checkpoint 3
    assert len(machine.checkpoints) == 3

    # The client flushes the last checkpoint the way the reference client does.
    final = machine.last_checkpoint()
    flushed = client.patch(
        f"/v1/narration/sessions/{narration_id}/cursor",
        json={"cursor": final.cursor, "device_id": str(phone)},
        headers=bearer(mobile_token),
    )
    assert flushed.status_code == 200

    # ...and the PC sees exactly where the phone stopped.
    on_pc = client.get(
        f"/v1/narration/sessions/{narration_id}/cursor", headers=bearer(desktop_token)
    ).json()
    assert on_pc["cursor"] == final.cursor
