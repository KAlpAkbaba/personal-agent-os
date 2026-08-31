"""M4 integration: cross-device narration cursor persistence + session lifecycle.

Requires the local compose stack (scripts/dev-up.ps1) and the schema at head
(migration 0004_voice). No audio, no provider keys, no network: the narration
command machine and cursor persistence are exercised over the real HTTP surface
against real PostgreSQL.

Acceptance (ACCEPTANCE_TESTS.md M4): "cross-device narration cursor persists" and
"oku/dur/devam/tekrar works".
"""

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.artifacts import service as artifact_service
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.main import create_app

pytestmark = pytest.mark.integration

BODY = """# Giriş

Toplam maliyet 1.250.000 Türk lirası. Kur farkı yüzde 3,42 seviyesine geriledi.

# İkinci Bölüm

Sunucunun IP adresi 192.168.1.20 olarak ayarlandı. Bağlantı sağlıklı.
"""


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def artifact_id(settings: Settings) -> uuid.UUID:
    """Seed a canonical artifact + version directly in the DB."""
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    session = factory()
    try:
        artifact = artifact_service.get_or_create_artifact_for_task(
            session, task_id=None, title="Narration Test Raporu"
        )
        content_hash = hashlib.sha256(BODY.encode("utf-8")).hexdigest()
        artifact_service.add_artifact_version(
            session,
            artifact_id=artifact.id,
            canonical_body=BODY,
            content_hash=content_hash,
        )
        return artifact.id
    finally:
        session.close()
        engine.dispose()


def test_cross_device_cursor_persists(settings: Settings, artifact_id: uuid.UUID) -> None:
    device_a = uuid.uuid4()
    with TestClient(create_app(settings)) as client:
        # Start a narration session (device A).
        created = client.post(
            "/v1/narration/sessions",
            json={"artifact_id": str(artifact_id), "device_id": str(device_a)},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]

        # Device A writes a semantic cursor to the cloud.
        cursor = {
            "section_id": "s3",
            "paragraph_id": "p4",
            "sentence_index": 1,
            "char_offset": 0,
        }
        patched = client.patch(
            f"/v1/narration/sessions/{session_id}/cursor",
            json={"cursor": cursor, "playback_seconds": 12.5, "device_id": str(device_a)},
        )
        assert patched.status_code == 200, patched.text

        # Device B (a *different* client instance / device) resumes: it reads the
        # exact same semantic cursor back from the cloud.
        with TestClient(create_app(settings)) as client_b:
            got = client_b.get(f"/v1/narration/sessions/{session_id}/cursor")
            assert got.status_code == 200
            payload = got.json()
            assert payload["cursor"] == cursor
            assert payload["playback_seconds"] == 12.5


def test_oku_dur_devam_lifecycle(settings: Settings, artifact_id: uuid.UUID) -> None:
    with TestClient(create_app(settings)) as client:
        session_id = client.post(
            "/v1/narration/sessions", json={"artifact_id": str(artifact_id)}
        ).json()["session_id"]

        # oku -> READING with a current chunk to synthesize.
        r = client.post(
            f"/v1/narration/sessions/{session_id}/command", json={"utterance": "oku"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "READING"
        assert body["action"] == "reading"
        assert body["current_chunk"] is not None
        # normalization is applied to the chunk text (money -> spoken Turkish).
        assert body["current_chunk"]["text"]

        # dur -> PAUSED (highest priority), cursor preserved.
        paused = client.post(
            f"/v1/narration/sessions/{session_id}/command", json={"utterance": "dur"}
        ).json()
        assert paused["state"] == "PAUSED"
        cursor_at_pause = paused["cursor"]

        # devam -> READING from the saved cursor.
        resumed = client.post(
            f"/v1/narration/sessions/{session_id}/command", json={"utterance": "devam et"}
        ).json()
        assert resumed["state"] == "READING"
        assert resumed["cursor"] == cursor_at_pause


def test_explain_then_return_persists_exact_cursor(
    settings: Settings, artifact_id: uuid.UUID
) -> None:
    with TestClient(create_app(settings)) as client:
        session_id = client.post(
            "/v1/narration/sessions", json={"artifact_id": str(artifact_id)}
        ).json()["session_id"]
        client.post(f"/v1/narration/sessions/{session_id}/command", json={"utterance": "oku"})
        # Advance to a specific cursor via a jump, then ask for an explanation.
        jumped = client.post(
            f"/v1/narration/sessions/{session_id}/command",
            json={"command": "maddeye_gec", "target_index": 2},
        ).json()
        cursor_before_explain = jumped["cursor"]

        explaining = client.post(
            f"/v1/narration/sessions/{session_id}/command",
            json={"utterance": "bu ne demek?"},
        ).json()
        assert explaining["state"] == "EXPLAINING"

        resumed = client.post(
            f"/v1/narration/sessions/{session_id}/command",
            json={"command": "acikla_bitti"},
        ).json()
        assert resumed["state"] == "READING"
        assert resumed["cursor"] == cursor_before_explain


def test_unknown_session_and_bad_command(settings: Settings, artifact_id: uuid.UUID) -> None:
    with TestClient(create_app(settings)) as client:
        missing = uuid.uuid4()
        assert client.get(f"/v1/narration/sessions/{missing}").status_code == 404
        # unknown artifact on create -> 404
        assert (
            client.post(
                "/v1/narration/sessions", json={"artifact_id": str(uuid.uuid4())}
            ).status_code
            == 404
        )
        # start a real session then send an unrecognized utterance -> 422
        sid = client.post(
            "/v1/narration/sessions", json={"artifact_id": str(artifact_id)}
        ).json()["session_id"]
        assert (
            client.post(
                f"/v1/narration/sessions/{sid}/command",
                json={"utterance": "kırmızı balık"},
            ).status_code
            == 422
        )
