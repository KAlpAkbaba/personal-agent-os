"""M9 integration: the headless reference client drives the whole contract.

**This test is what stands in for the native mobile app in the M9 gate.** There
is no Android SDK, no Xcode and no device on this machine, so "authenticated
native client connects" is proven by an actual client program — the one in
`clients/reference/mobile_client.py` — running the real sequence against the
real API over HTTP: credential exchange, push registration, artifact-ready
notification, executive summary without the body, narration resume from the
cloud cursor, render download, and revocation handling.

What it does not prove is stated plainly so nobody mistakes the scope: nothing
here exercises FCM/APNs delivery, a real microphone, or app-store
distribution. Those are the owner actions listed in the M9 return.

The client talks to the app through a `TestClient`-backed httpx transport — the
same transport seam a real run uses with `--base-url`. Every request goes
through the actual routing, dependencies and authentication.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.artifacts import service as artifact_service
from app.artifacts.models import (
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
    TASK_STATUS_PLANNED,
    TASK_STATUS_READY,
    TASK_STATUS_RENDERING,
    TASK_STATUS_RUNNING,
)
from app.artifacts.renderers import DEFAULT_RENDER_FORMATS, content_hash
from app.artifacts.runtime import build_artifact_context
from app.config import Settings
from app.identity.root import FileCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.main import create_app
from app.object_store import S3ObjectStore
from tests.integration.broker_agent import CAPABILITIES, AgentKey
from tests.integration.conftest import shared_identity

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    # `clients/` lives at the repository root, beside `services/`; the API test
    # run's working directory is services/api, so the package needs putting on
    # the path explicitly. This is the only coupling between the two trees.
    sys.path.insert(0, str(REPO_ROOT))

from clients.reference.mobile_client import (  # noqa: E402 - needs the path above
    EXIT_SESSION_REVOKED,
    ContractFailed,
    MobileReferenceClient,
    SessionRevoked,
    build_parser,
)

pytestmark = pytest.mark.integration

TOPIC = "referans mobil istemci senaryosu"
BODY = """# Yönetici Özeti

Bu rapor mobil istemci sözleşmesini doğrulamak için üretildi.

# Bulgular

Toplam maliyet 1.250.000 Türk lirası. Bağlantı sağlıklı.

# Sonuç

Rapor telefondan kaldığı yerden okunabilir.
"""


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module", autouse=True)
def _bucket(settings: Settings) -> None:
    S3ObjectStore.from_settings(settings).ensure_bucket()


@pytest.fixture(scope="module")
def ready_artifact(settings: Settings) -> tuple[uuid.UUID, uuid.UUID]:
    """A task + artifact at READY, with renders, through the real service layer.

    The workflow-driven path is covered by `test_mobile_push.py`; here the
    interest is the *client*, so the fixture walks the same service calls the
    render activity makes without paying for a Temporal round trip.
    """
    factory, store = build_artifact_context(settings)
    engine = factory.kw["bind"]
    try:
        return _seed(factory, store)
    finally:
        # Release the pool: the dev PostgreSQL has a finite max_connections.
        engine.dispose()


def _seed(factory, store) -> tuple[uuid.UUID, uuid.UUID]:
    with factory() as session:
        task = artifact_service.create_task(session, intent=TOPIC)
        for status in (
            TASK_STATUS_PLANNED,
            TASK_STATUS_RUNNING,
            TASK_STATUS_RENDERING,
        ):
            artifact_service.transition_task(session, task.id, status)
        artifact = artifact_service.get_or_create_artifact_for_task(
            session, task_id=task.id, title="Referans İstemci Raporu"
        )
        version = artifact_service.add_artifact_version(
            session,
            artifact_id=artifact.id,
            canonical_body=BODY,
            content_hash=content_hash(BODY.encode("utf-8")),
        )
        artifact_service.set_executive_summary(
            session, artifact.id, "Mobil istemci sözleşmesi doğrulandı."
        )
        artifact_service.set_artifact_state(
            session, artifact.id, ARTIFACT_STATE_CANONICAL_READY
        )
        artifact_service.set_artifact_state(
            session, artifact.id, ARTIFACT_STATE_RENDERS_PENDING
        )
        from app.artifacts import render_store

        render_store.ensure_renders(
            session,
            store,
            version=version,
            title=artifact.title,
            formats=DEFAULT_RENDER_FORMATS,
        )
        artifact_service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_READY)
        artifact_service.transition_task(session, task.id, TASK_STATUS_READY)
        return task.id, artifact.id


@pytest.fixture(scope="module")
def api(settings: Settings, tmp_path_factory):
    """An app with a real file-backed identity root, so the client can sign in.

    The owner credential is minted once through the real bootstrap endpoint —
    a loopback-only owner action — and then handed to the client, exactly as
    the owner would paste it into a phone.

    Module-scoped: every `create_app` opens a connection pool per subsystem and
    the dev PostgreSQL has a finite `max_connections`. The identity runtime
    reuses the suite-wide engine; the rest are disposed on the way out.
    """
    root = tmp_path_factory.mktemp("reference-identity")
    keep = shared_identity(settings).engine
    app = create_app(settings)
    app.state.identity = IdentityRuntime(
        settings, engine=keep, root=FileCredentialRoot(root / "identity")
    )
    with TestClient(app) as http:
        credential = http.post("/v1/identity/bootstrap").json()["owner_credential"]
        yield app, http, credential
    for runtime in getattr(app.state, "_state", {}).values():
        engine = getattr(runtime, "_engine", None)
        if engine is not None and engine is not keep:
            engine.dispose()


def make_client(api, **kwargs) -> MobileReferenceClient:
    app, http, credential = api
    return MobileReferenceClient("http://testserver", credential=credential, http=http, **kwargs)


def announce(http: TestClient, app, task_id: uuid.UUID) -> None:
    """The owner-session-holding caller announces READY (see the endpoint doc)."""
    issued = app.state.identity.service.issue_session(client_kind="cli", label="announcer")
    response = http.post(
        "/v1/mobile/notifications/artifact-ready",
        json={"task_id": str(task_id)},
        headers={"Authorization": f"Bearer {issued.token}"},
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------- the whole arc


def test_the_reference_client_runs_the_whole_mobile_contract(
    api, ready_artifact: tuple[uuid.UUID, uuid.UUID], tmp_path: Path
) -> None:
    app, http, _ = api
    task_id, artifact_id = ready_artifact
    client = make_client(api, label="gate-phone")

    # 1-3. sign in, connect, register for push.
    client.sign_in()
    assert client.whoami()["client_kind"] == "mobile"
    registration = client.register_push(platform="headless")
    assert registration["status"] == "active"

    # The push fires only once the task is genuinely READY. In production the
    # workflow gets there on its own clock; here the harness announces it.
    announce(http, app, task_id)

    # The rest of the arc runs on the session the phone already holds — which
    # is what a native app does on every launch after the first.
    report = client.run_flow(
        artifact_id=str(artifact_id),
        share_format="pdf",
        download_dir=tmp_path / "downloads",
        wait_timeout_s=5.0,
        wait_interval_s=0.01,
        reuse_session=True,
    )

    steps = [entry["step"] for entry in report.steps]
    assert steps[:3] == ["session_reused", "connected", "push_registered"]
    assert report.get("finished")["ok"] is True

    notified = report.get("notified")
    assert notified["artifact_id"] == str(artifact_id)
    assert notified["title"] == "Araştırma tamamlandı"

    # 4. The summary arrived; the report body did NOT (constitution §3).
    summary = report.get("summary_fetched")
    assert summary["state"] == "READY"
    assert summary["summary_chars"] > 0
    assert summary["body_withheld"] is True

    # 5. Narration resumed from the cursor the cloud holds.
    resumed = report.get("narration_resumed")
    assert resumed["state"] in {"READING", "PAUSED"}
    assert resumed["resumed_from"] is not None

    # 6. The render is on disk under a name a human can find.
    downloaded = report.get("downloaded")
    saved = Path(downloaded["written_to"])
    assert saved.is_file()
    assert saved.stat().st_size == downloaded["size_bytes"] > 0
    assert saved.suffix == ".pdf"
    assert saved.read_bytes().startswith(b"%PDF")
    # The RFC 5987 name won, so the Turkish title survived the round trip.
    assert "Referans İstemci Raporu" in downloaded["filename"]


def test_the_client_never_pulls_the_body_unless_asked(
    api, ready_artifact: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _, artifact_id = ready_artifact
    client = make_client(api)
    client.sign_in()
    summary = client.executive_summary(str(artifact_id))
    assert "canonical_body" not in summary
    assert summary["executive_summary"]
    # ...and when the owner does ask, the whole report is there.
    assert "Yönetici Özeti" in client.full_body(str(artifact_id))


def test_a_missing_notification_fails_instead_of_hanging(api) -> None:
    client = make_client(api)
    client.sign_in()
    client.register_push()
    with pytest.raises(ContractFailed):
        client.wait_for_artifact_ready(timeout_s=0.05, interval_s=0.01)


# ------------------------------------------------------- revocation handling


def test_a_revoked_session_stops_the_client_cleanly(api) -> None:
    app, _, _ = api
    client = make_client(api)
    session = client.sign_in()
    client.register_push()

    app.state.identity.service.revoke_session(
        uuid.UUID(session["session_id"]), reason="owner_revoked"
    )

    with pytest.raises(SessionRevoked):
        client.whoami()
    # The client dropped the dead token rather than retrying with it.
    assert client.token is None


def test_device_revocation_ends_the_clients_session(api) -> None:
    """The acceptance bullet, from the client's side of the wire."""
    app, http, _ = api
    owner = app.state.identity.service.issue_session(client_kind="cli", label="owner console")
    console = {"Authorization": f"Bearer {owner.token}"}
    # Enrolled inline rather than through `rest_enroll`, because the shared
    # TestClient must stay header-free: the reference client uses it as its
    # transport, and a client-level Authorization would mask a dead token.
    token = http.post("/v1/devices/enrollment-tokens", headers=console).json()["token"]
    enrolled = http.post(
        "/v1/devices/enroll",
        json={
            "token": token,
            "name": "itest-reference-phone",
            "platform": "windows",
            "public_key_spki_b64": AgentKey().spki_b64,
            "capabilities": CAPABILITIES,
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    device_id = uuid.UUID(enrolled.json()["device_id"])

    client = make_client(api, device_id=device_id)
    client.sign_in()
    client.register_push()
    assert client.whoami()["device_id"] == str(device_id)

    revoked = http.post(f"/v1/devices/{device_id}/revoke", headers=console)
    assert revoked.status_code == 200

    with pytest.raises(SessionRevoked):
        client.whoami()


def test_the_client_re_authenticates_when_asked_to(
    api, ready_artifact: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """`--reauth`: a revoked session is a normal event for a phone."""
    app, http, _ = api
    task_id, artifact_id = ready_artifact
    client = make_client(api)
    first = client.sign_in()
    app.state.identity.service.revoke_session(uuid.UUID(first["session_id"]))
    announce(http, app, task_id)

    report = client.run_flow(
        artifact_id=str(artifact_id),
        expect_notification=False,
        share_format="pdf",
        reauth=True,
    )
    assert report.get("finished")["ok"] is True
    assert client.session_id != first["session_id"]


# ------------------------------------------------------------------ the CLI


def test_the_client_is_runnable_as_a_module() -> None:
    """`python -m clients.reference.mobile_client --help` — a real program."""
    result = subprocess.run(
        [sys.executable, "-m", "clients.reference.mobile_client", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "mobile contract" in result.stdout
    for command in ("flow", "connect", "register-push", "notifications", "download"):
        assert command in result.stdout


def test_the_cli_refuses_to_run_without_an_owner_credential(monkeypatch) -> None:
    from clients.reference import mobile_client

    monkeypatch.delenv("PAGENTOS_OWNER_CREDENTIAL", raising=False)
    with pytest.raises(SystemExit) as exc:
        mobile_client.main(["connect"])
    assert exc.value.code == 2  # argparse usage error; it never invents a credential


def test_the_cli_exits_cleanly_on_revocation(api, monkeypatch) -> None:
    """A revoked session is exit code 3, not a traceback."""
    from clients.reference import mobile_client

    app, http, credential = api

    class _Client(MobileReferenceClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("timeout_s", None)
            super().__init__("http://testserver", credential=credential, http=http)

        def sign_in(self):
            raise SessionRevoked("credential exchange refused: 401")

    monkeypatch.setattr(mobile_client, "MobileReferenceClient", _Client)
    assert mobile_client.main(["--credential", credential, "connect"]) == EXIT_SESSION_REVOKED


def test_the_parser_defaults_to_the_flow_command() -> None:
    args = build_parser().parse_args(["--credential", "x"])
    assert args.command is None  # main() resolves None -> "flow"
    assert args.push_provider == "fake"
    assert args.share_format == "pdf"
