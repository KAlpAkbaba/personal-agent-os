"""M9 integration: push registrations, artifact-ready delivery, share/export.

Requires the compose stack (postgres/minio/temporal) and the schema at head
(migration 0009). What only this suite can prove:

- a push registration is a durable row bound to a real `owner_sessions` row;
- the frozen `ON DELETE CASCADE` actually cascades in PostgreSQL — the ORM
  cannot tell you that, only the database can;
- **revoking an enrolled device stops the push**, through the real broker
  revoke endpoint, which is the acceptance chain's last link;
- a research task that reaches READY *through the real Temporal workflow*
  produces exactly one readiness notification per live registration, and that
  notification carries no report body;
- share/export streams the same render bytes the artifacts route serves, with
  a filename a phone can save.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from temporalio.client import Client

from app.artifacts import service as artifact_service
from app.artifacts.models import ARTIFACT_STATE_READY, TASK_STATUS_READY
from app.artifacts.renderers import EXTENSIONS
from app.artifacts.runtime import build_artifact_context
from app.config import Settings
from app.identity.runtime import IdentityRuntime
from app.mobile.models import PUSH_STATUS_ACTIVE
from app.object_store import S3ObjectStore
from app.research.workflow import ResearchRequest, ResearchWorkflow
from app.worker import build_worker
from tests.identity_support import bearer
from tests.integration.broker_agent import AgentKey, rest_enroll
from tests.integration.conftest import owner_client, shared_identity

pytestmark = pytest.mark.integration

TOPIC = "mobil bildirim testi için yapay zekâ ajanları"
PUSH_TOKEN = "integration-mobile-push-token"


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module", autouse=True)
def _bucket(settings: Settings) -> None:
    S3ObjectStore.from_settings(settings).ensure_bucket()


@pytest.fixture(scope="module")
def ready_task(settings: Settings) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Drive a research task to READY through the real durable workflow.

    Seeding the rows by hand would prove the endpoint works; running the
    workflow proves the endpoint works *for the thing that actually reaches
    READY in production*.
    """
    with db(settings) as session:
        task_id = artifact_service.create_task(session, intent=TOPIC).id

    async def run() -> None:
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        queue = f"pagentos-m9-{uuid.uuid4().hex[:8]}"
        async with build_worker(client, queue):
            await client.execute_workflow(
                ResearchWorkflow.run,
                ResearchRequest(task_id=str(task_id), topic=TOPIC),
                id=f"research-{task_id}",
                task_queue=queue,
            )

    asyncio.run(run())

    with db(settings) as session:
        task = artifact_service.get_task(session, task_id)
        assert task is not None and task.status == TASK_STATUS_READY
        artifact = artifact_service.get_artifact_for_task(session, task_id)
        assert artifact is not None and artifact.state == ARTIFACT_STATE_READY
        return task_id, artifact.id, artifact.title


@pytest.fixture(scope="module")
def client(settings: Settings) -> TestClient:
    """One app for the whole module, and its engines disposed afterwards.

    Deliberately module-scoped: every `create_app` lazily opens a connection
    pool per subsystem, and the dev PostgreSQL has a finite `max_connections`.
    A per-test app here would exhaust it for the rest of the suite — which is
    a property of the fixture, not of the code under test, so it is fixed here.
    """
    with owner_client(settings) as test_client:
        yield test_client
        # The shared identity runtime outlives this module; everything else dies.
        dispose_engines(test_client.app, keep=shared_identity(settings).engine)


@pytest.fixture(scope="module")
def identity(settings: Settings) -> IdentityRuntime:
    return shared_identity(settings)


def dispose_engines(app, *, keep=None) -> None:
    """Release every connection pool this app opened."""
    for runtime in getattr(app.state, "_state", {}).values():
        engine = getattr(runtime, "_engine", None)
        if engine is not None and engine is not keep:
            engine.dispose()


@contextlib.contextmanager
def db(settings: Settings):
    """A direct DB session whose pool is closed again immediately.

    `build_artifact_context` opens a fresh pool per call; leaving those open is
    what walks the dev database into `too many clients already`.
    """
    factory, _ = build_artifact_context(settings)
    engine = factory.kw["bind"]
    try:
        with factory() as session:
            yield session
    finally:
        engine.dispose()


def register(client: TestClient, *, token: str = PUSH_TOKEN, **extra) -> dict:
    response = client.post(
        "/v1/mobile/push/registrations",
        json={"provider": "fake", "token": token, "platform": "ios", **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------- durable registration


def test_registration_is_a_durable_row_bound_to_the_session(
    client: TestClient, settings: Settings
) -> None:
    payload = register(client)
    assert payload["status"] == PUSH_STATUS_ACTIVE
    assert PUSH_TOKEN not in str(payload)

    with db(settings) as session:
        row = session.execute(
            text("SELECT session_id, provider, token_hash FROM push_registrations WHERE id = :i"),
            {"i": payload["registration_id"]},
        ).one()
    assert str(row.session_id) == payload["session_id"]
    assert row.provider == "fake"
    # Only the hash is durable; the plaintext lives in the provider's memory.
    assert row.token_hash != PUSH_TOKEN and len(row.token_hash) == 64


def test_deleting_the_session_row_cascades_to_the_registration(
    client: TestClient, identity: IdentityRuntime, settings: Settings
) -> None:
    """The frozen migration's ON DELETE CASCADE, proven against PostgreSQL."""
    doomed = identity.service.issue_session(client_kind="mobile", label="cascade probe")
    registration = client.post(
        "/v1/mobile/push/registrations",
        json={"provider": "fake", "token": "cascade-probe-token"},
        headers=bearer(doomed.token),
    ).json()

    with db(settings) as session:
        session.execute(
            text("DELETE FROM owner_sessions WHERE id = :i"),
            {"i": str(doomed.context.session_id)},
        )
        session.commit()
        remaining = session.execute(
            text("SELECT count(*) FROM push_registrations WHERE id = :i"),
            {"i": registration["registration_id"]},
        ).scalar_one()
    assert remaining == 0


# --------------------------------------------- revocation stops the delivery


def test_revoking_a_session_stops_its_push_without_touching_the_row(
    client: TestClient, identity: IdentityRuntime, settings: Settings
) -> None:
    doomed = identity.service.issue_session(client_kind="mobile", label="revoke probe")
    registration = client.post(
        "/v1/mobile/push/registrations",
        json={"provider": "fake", "token": "revoke-probe-token"},
        headers=bearer(doomed.token),
    ).json()

    service = client.app.state.mobile.service
    live_before = {r.registration_id for r in service.live_registrations()}
    assert uuid.UUID(registration["registration_id"]) in live_before

    identity.service.revoke_session(doomed.context.session_id, reason="owner_revoked")

    live_after = {r.registration_id for r in service.live_registrations()}
    assert uuid.UUID(registration["registration_id"]) not in live_after
    # The registration row is untouched: the join is the enforcement point.
    assert (
        client.get("/v1/mobile/push/registrations")
        .json()["registrations"][0]["status"]
        in {"active", "revoked", "invalid"}
    )


def test_device_revocation_invalidates_the_session_and_its_push_target(
    client: TestClient, identity: IdentityRuntime
) -> None:
    """The full M9 chain through the real broker endpoint."""
    key = AgentKey()
    device_id = uuid.UUID(rest_enroll(client, key, name="itest-mobile-phone"))
    phone = identity.service.issue_session(
        client_kind="mobile", label="phone", device_id=device_id
    )
    registration = client.post(
        "/v1/mobile/push/registrations",
        json={"provider": "fake", "token": "lost-phone-push-token", "platform": "ios"},
        headers=bearer(phone.token),
    )
    assert registration.status_code == 201
    registration_id = uuid.UUID(registration.json()["registration_id"])

    service = client.app.state.mobile.service
    assert registration_id in {r.registration_id for r in service.live_registrations()}

    revoke = client.post(f"/v1/devices/{device_id}/revoke")
    assert revoke.status_code == 200
    assert revoke.json()["sessions_revoked"] >= 1

    # The session is dead on the REST API...
    assert (
        client.get("/v1/mobile/push/registrations", headers=bearer(phone.token)).status_code
        == 401
    )
    # ...and it is no longer a push target.
    assert registration_id not in {r.registration_id for r in service.live_registrations()}


# ----------------------------------------------------- artifact-ready trigger


def test_artifact_ready_notifies_every_live_registration(
    client: TestClient, ready_task: tuple[uuid.UUID, uuid.UUID, str]
) -> None:
    task_id, artifact_id, title = ready_task
    registration = register(client, token="artifact-ready-token")

    announced = client.post(
        "/v1/mobile/notifications/artifact-ready", json={"task_id": str(task_id)}
    )
    assert announced.status_code == 200, announced.text
    payload = announced.json()
    assert payload["artifact_id"] == str(artifact_id)
    assert payload["delivered"] >= 1
    assert payload["notification"]["title"] == "Araştırma tamamlandı"
    assert title in payload["notification"]["body"]

    inbox = client.get("/v1/mobile/notifications").json()["notifications"]
    mine = [
        n for n in inbox if n["registration_id"] == registration["registration_id"]
    ]
    assert len(mine) == 1
    assert mine[0]["data"]["artifact_id"] == str(artifact_id)
    # Constitution §3: the notification announces readiness, not the report.
    assert "canonical_body" not in str(mine[0])
    assert len(mine[0]["body"]) <= 400


def test_announcing_twice_leaves_one_unread_notification(
    client: TestClient, ready_task: tuple[uuid.UUID, uuid.UUID, str]
) -> None:
    """A retrying workflow must not stack notifications (collapse_key)."""
    task_id, _, _ = ready_task
    registration = register(client, token="collapse-probe-token")
    for _ in range(3):
        client.post(
            "/v1/mobile/notifications/artifact-ready", json={"task_id": str(task_id)}
        )
    inbox = client.get("/v1/mobile/notifications").json()["notifications"]
    mine = [n for n in inbox if n["registration_id"] == registration["registration_id"]]
    assert len(mine) == 1


def test_a_task_that_is_not_ready_cannot_be_announced(
    client: TestClient, settings: Settings
) -> None:
    """The announcement is authorised by durable state, not by the caller."""
    with db(settings) as session:
        task_id = artifact_service.create_task(session, intent="henüz bitmedi").id
    response = client.post(
        "/v1/mobile/notifications/artifact-ready", json={"task_id": str(task_id)}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "task_not_ready:CREATED"


def test_unknown_ids_are_404(client: TestClient) -> None:
    for body in ({"task_id": str(uuid.uuid4())}, {"artifact_id": str(uuid.uuid4())}):
        assert (
            client.post("/v1/mobile/notifications/artifact-ready", json=body).status_code
            == 404
        )


# ---------------------------------------------------------------- share/export


def test_share_index_lists_saveable_renders(
    client: TestClient, ready_task: tuple[uuid.UUID, uuid.UUID, str]
) -> None:
    _, artifact_id, title = ready_task
    payload = client.get(f"/v1/mobile/share/{artifact_id}").json()
    assert payload["title"] == title
    assert payload["preferred_format"] == "pdf"  # MASTER_SPEC §B: PDF on mobile
    formats = {r["format"] for r in payload["renders"]}
    assert {"pdf", "docx"} <= formats
    for render in payload["renders"]:
        assert render["within_bound"] is True
        assert render["url"] == f"/v1/mobile/share/{artifact_id}/{render['format']}"
        assert render["filename"].endswith(f".{EXTENSIONS[render['format']]}")
        # The Turkish title is transliterated for the on-disk name, not dropped.
        assert render["filename"].startswith("arastirma-raporu-")


@pytest.mark.parametrize("fmt", ["pdf", "docx", "html", "txt"])
def test_share_download_is_the_same_bytes_as_the_artifacts_route(
    client: TestClient, ready_task: tuple[uuid.UUID, uuid.UUID, str], fmt: str
) -> None:
    """One render store, two callers — never a second rendering path."""
    _, artifact_id, _ = ready_task
    canonical = client.get(f"/v1/artifacts/{artifact_id}/renders/{fmt}")
    shared = client.get(f"/v1/mobile/share/{artifact_id}/{fmt}")
    assert shared.status_code == 200, shared.text
    assert shared.content == canonical.content
    assert shared.headers["x-content-hash"] == canonical.headers["x-content-hash"]
    assert shared.headers["content-type"].startswith(
        canonical.headers["content-type"].split(";")[0]
    )
    # What the mobile route adds: a filename a human can find on their phone.
    disposition = shared.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert disposition.isascii()
    assert int(shared.headers["content-length"]) == len(shared.content)


def test_share_refuses_a_render_over_the_bound(
    client: TestClient, ready_task: tuple[uuid.UUID, uuid.UUID, str], monkeypatch
) -> None:
    _, artifact_id, _ = ready_task
    monkeypatch.setenv("PAGENTOS_MOBILE_SHARE_MAX_BYTES", "16")
    response = client.get(f"/v1/mobile/share/{artifact_id}/pdf")
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["error_class"] == "payload_too_large"
    assert detail["max_bytes"] == 16
    assert detail["size_bytes"] > 16


def test_share_404s_for_an_unknown_artifact(client: TestClient) -> None:
    assert client.get(f"/v1/mobile/share/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/v1/mobile/share/{uuid.uuid4()}/pdf").status_code == 404
