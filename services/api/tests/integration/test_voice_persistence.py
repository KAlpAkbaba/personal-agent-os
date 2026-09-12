"""M4 integration: voice persistence against the real Postgres + MinIO stack.

Requires the compose stack (postgres/minio) and the schema at head (0004_voice).
Everything here is offline: fixture embedding vectors, deterministic fakes.
Speaker enrollment stores only an ENCRYPTED derived profile in the object store
(never raw audio); verification loads + decrypts it round-trip.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.object_store import S3ObjectStore
from app.voice import service
from app.voice.crypto import ProfileCipher
from app.voice.runtime import VoiceRuntime
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module", autouse=True)
def _ensure_bucket(settings: Settings) -> None:
    S3ObjectStore.from_settings(settings).ensure_bucket()


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    # M9: /v1/voice requires an owner session.
    return owner_client(settings)


OWNER_SAMPLES = [
    [1.0, 0.0, 0.0, 0.0],
    [0.92, 0.10, 0.0, 0.0],
    [0.95, 0.05, 0.05, 0.0],
]


def _unique_label() -> str:
    return f"owner-test-{uuid.uuid4().hex[:8]}"


def test_speaker_enroll_persists_and_verify_roundtrip(settings: Settings) -> None:
    """Enroll from fixture embeddings -> encrypted blob in MinIO + speaker_profiles
    row in Postgres; then verify OWNER/NOT_OWNER/UNCERTAIN through a FRESH runtime
    (independent engine + store + cipher), proving durable + decryptable."""
    label = _unique_label()
    runtime = VoiceRuntime(settings)
    with runtime.session() as session:
        row = service.enroll_and_store_owner(
            session, runtime.store, runtime.cipher,
            sample_embeddings=OWNER_SAMPLES, model_id="fixture-embed-v1",
            prefix=settings.voice_speaker_object_prefix, label=label,
        )
        assert row.embedding_ref
        ref = row.embedding_ref

    # The stored blob is ciphertext, not the plaintext embedding.
    raw = runtime.store.get(ref)
    assert b"1.0" not in raw and b"embedding" not in raw

    # Fresh runtime = simulated restart; must decrypt + classify.
    fresh = VoiceRuntime(settings)
    with fresh.session() as session:
        owner = service.verify_owner(
            session, fresh.store, fresh.cipher,
            probe_embedding=[1.0, 0.0, 0.0, 0.0], device_trusted=True, label=label,
        )
        assert owner.decision == "OWNER"

        not_owner = service.verify_owner(
            session, fresh.store, fresh.cipher,
            probe_embedding=[0.0, 0.0, 1.0, 0.0], device_trusted=True, label=label,
        )
        assert not_owner.decision == "NOT_OWNER"

        uncertain = service.verify_owner(
            session, fresh.store, fresh.cipher,
            probe_embedding=[1.0, 0.0, 0.0, 0.0], device_trusted=False, label=label,
        )
        assert uncertain.decision == "UNCERTAIN"  # voice not sole secret


def test_wrong_secret_cannot_decrypt(settings: Settings) -> None:
    label = _unique_label()
    runtime = VoiceRuntime(settings)
    with runtime.session() as session:
        service.enroll_and_store_owner(
            session, runtime.store, runtime.cipher,
            sample_embeddings=OWNER_SAMPLES, model_id="m",
            prefix=settings.voice_speaker_object_prefix, label=label,
        )
    wrong_cipher = ProfileCipher("a-different-secret")
    with runtime.session() as session:
        from app.voice.errors import VoiceError
        with pytest.raises(VoiceError):
            service.verify_owner(
                session, runtime.store, wrong_cipher,
                probe_embedding=[1.0, 0.0, 0.0, 0.0], device_trusted=True, label=label,
            )


def test_preferences_persist_and_override(settings: Settings) -> None:
    label = _unique_label()
    runtime = VoiceRuntime(settings)
    with runtime.session() as session:
        service.update_preferences(session, {"read_urls": True, "narration_speed": 1.5},
                                   source="owner", label=label)
    # Fresh runtime reads the persisted values back.
    fresh = VoiceRuntime(settings)
    with fresh.session() as session:
        prefs = service.load_preferences(session, label=label)
        assert prefs.read_urls is True
        assert prefs.narration_speed == 1.5
        # Inferred update must not override the explicit owner value.
        service.update_preferences(session, {"read_urls": False}, source="inferred",
                                   label=label)
        prefs2 = service.load_preferences(session, label=label)
        assert prefs2.read_urls is True


def test_benchmark_report_persists_and_retrievable(client: TestClient) -> None:
    gen = client.post("/v1/voice/benchmark/run")
    assert gen.status_code == 200
    body = gen.json()
    assert body["tts"]["compares_provider_count"] >= 2
    assert body["stt"]["compares_provider_count"] >= 2

    got = client.get("/v1/voice/benchmark/reports")
    assert got.status_code == 200
    reports = got.json()
    assert reports["tts"]["kind"] == "tts"
    assert reports["stt"]["kind"] == "stt"
    assert reports["tts"]["compares_provider_count"] >= 2
    assert len(reports["stt"]["cases"]) >= 1


def test_speaker_and_preferences_via_http(client: TestClient) -> None:
    # Enroll via the REST surface (fixture embeddings, never raw audio).
    enroll = client.post("/v1/voice/speaker/enroll",
                         json={"sample_embeddings": OWNER_SAMPLES, "model_id": "http-embed"})
    assert enroll.status_code == 201
    assert enroll.json()["embedding_ref"]

    # B05 req 246/663: `device_trusted` is no longer the caller's to send. This request
    # used to claim a trusted device and be believed; the route derives it from the
    # authenticated session's device binding now, and the body is refused if it tries.
    refused = client.post("/v1/voice/speaker/verify",
                          json={"probe_embedding": [1.0, 0.0, 0.0, 0.0], "device_trusted": True})
    assert refused.status_code == 422

    verify = client.post("/v1/voice/speaker/verify",
                         json={"probe_embedding": [1.0, 0.0, 0.0, 0.0]})
    assert verify.status_code == 200
    assert verify.json()["decision"] in ("OWNER", "NOT_OWNER", "UNCERTAIN")
    # This client's session carries no device binding, so a perfect voice match is capped
    # at UNCERTAIN - voice is never the sole secret.
    assert verify.json()["device_trusted"] is False

    patch = client.patch("/v1/voice/preferences",
                         json={"barge_in": False, "source": "owner"})
    assert patch.status_code == 200
    assert patch.json()["barge_in"] is False

    providers = client.get("/v1/voice/providers")
    assert providers.status_code == 200
    assert providers.json()["count"] >= 2
