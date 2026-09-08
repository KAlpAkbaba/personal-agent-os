"""M23 end to end, through the REAL application object (docs/DECISIONS.md ADR-0078's own
discipline: a component built, tested and never wired is this repository's recurring
defect class — named again here for the App Factory).

Every other M23 suite proves ONE layer against a fake: test_appfactory_spec.py the
model, test_appfactory_generator.py the templates in isolation, test_appfactory_
validation.py validate() called directly, test_appfactory_service.py the service
against a bare SQLite+FakeDeviceAction harness, test_appfactory_tools.py the tools
through the corpus harness. None of them stand up ``create_app`` itself and drive an
HTTP session/tool-call sequence the way a real owner turn would, with the REAL
``AppFactoryService`` ``app.main.create_app`` builds (never a substituted fake) reading
a REAL fake device port injected exactly the way production reads its real one. This
module does that, once per stage of the lifecycle.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.routines.dispatch import DeviceRunResult
from tests.alarms_support import FakeDeviceAction, happy_device_results
from tests.appfactory_support import appfactory_capability_results
from tests.identity_support import authenticate
from tests.voice_corpus.harness import TABLES as HARNESS_TABLES

# The full realtime-session schema (voice profiles, narration, focus, app_projects, ...)
# — the same discipline test_artifact_wiring.py's own fixture uses, only reusing the
# corpus harness's own comprehensive table list (which already carries app_projects)
# rather than re-deriving it.
TABLES = list(HARNESS_TABLES)


def _client() -> tuple[TestClient, FakeDeviceAction]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)

    # ``create_app`` already builds and registers the REAL ``AppFactoryService`` on the
    # SAME live path production uses (app.main.py, ADR-0078); this test only swaps the
    # ENGINE the app's own runtime reads (the same pattern test_artifact_wiring.py's own
    # fixture uses) and the device port, so the real service reaches a fake device
    # rather than a broker that has nothing enrolled.
    device = FakeDeviceAction(results={**happy_device_results(), **appfactory_capability_results()})

    from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime

    runtime: RealtimeVoiceRuntime = app.state.voice_realtime
    runtime._engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime.register_live(device_action=device)

    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    return test_client, device


def test_create_run_test_stop_through_the_real_application_object() -> None:
    client, device = _client()

    session_id = client.post("/v1/voice/realtime/sessions", json={}).json()["session_id"]

    def say(text: str) -> None:
        client.post(
            f"/v1/voice/realtime/sessions/{session_id}/events",
            json={"events": [{"kind": "utterance", "t_ms": 1000, "turn": 1, "text": text}]},
        )

    def tool(name: str, arguments: dict) -> dict:
        return client.post(
            f"/v1/voice/realtime/sessions/{session_id}/tool-calls",
            json={"call_id": str(uuid.uuid4()), "name": name, "arguments": arguments},
        ).json()

    say("Bana bir görev takip uygulaması yap.")
    created = tool("app.create", {})
    assert created["status"] == "succeeded", created
    assert created["result"]["execution_status"] == "executed"
    assert created["result"]["state"] == "scaffolded"
    project_id = created["result"]["project_id"]

    say("Uygulamayı çalıştır.")
    ran = tool("app.run", {})
    assert ran["status"] == "succeeded", ran
    assert ran["result"]["state"] == "running"

    say("Testleri çalıştır.")
    tested = tool("app.test", {})
    assert tested["status"] == "succeeded", tested
    assert tested["result"]["state"] == "tested"
    assert tested["result"]["passed"] == 5

    say("Uygulamayı durdur.")
    stopped = tool("app.stop", {})
    assert stopped["status"] == "succeeded", stopped
    assert stopped["result"]["state"] == "stopped"

    say("Hangi uygulamaları yaptın?")
    listed = tool("app.list", {})
    assert listed["status"] == "succeeded", listed
    assert any(p["project_id"] == project_id for p in listed["result"]["projects"])

    assert device.capabilities_called() == [
        "project.scaffold",
        "project.run",
        "project.test",
        "project.stop",
    ]


def test_a_device_that_refuses_scaffold_is_a_truthful_refusal() -> None:
    client, device = _client()
    device.results["project.scaffold"] = DeviceRunResult(False, "permission_denied", "no")

    session_id = client.post("/v1/voice/realtime/sessions", json={}).json()["session_id"]
    client.post(
        f"/v1/voice/realtime/sessions/{session_id}/events",
        json={
            "events": [
                {
                    "kind": "utterance",
                    "t_ms": 1000,
                    "turn": 1,
                    "text": "Bana bir görev takip uygulaması yap.",
                }
            ]
        },
    )
    result = client.post(
        f"/v1/voice/realtime/sessions/{session_id}/tool-calls",
        json={"call_id": "c-1", "name": "app.create", "arguments": {}},
    ).json()
    assert result["status"] == "succeeded"
    assert result["result"]["execution_status"] == "refused"
    assert result["result"]["speech"]
