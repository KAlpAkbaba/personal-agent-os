"""M25 end to end, through the REAL application object (docs/DECISIONS.md ADR-0078's
own discipline: a component built, tested and never wired is this repository's
recurring defect class — named again here for 3D Creation).

Every other M25 suite proves ONE layer against a fake: test_scene_spec.py the model,
test_blender_driver.py the driver in isolation, test_scene_compare.py the comparison,
test_scene_service.py the service against a bare SQLite+fake-device harness,
test_scene_tools.py the tools through the corpus harness. None of them stand up
``create_app`` itself and drive an HTTP session/tool-call sequence the way a real
owner turn would, with the REAL ``SceneService`` ``app.main.create_app`` builds (never
a substituted fake) reading a REAL fake device port injected exactly the way
production reads its real one. This module does that, once per stage of the lifecycle.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.creative3d.models import STATE_APPLIED
from app.main import create_app
from app.routines.dispatch import DeviceRunResult
from tests.alarms_support import FakeDeviceAction, happy_device_results
from tests.creative3d_support import UNITY_LICENSE_MESSAGE, FakeCreative3DDevice
from tests.identity_support import authenticate
from tests.voice_corpus.harness import TABLES as HARNESS_TABLES

TABLES = list(HARNESS_TABLES)


def _client(*, unity_available: bool = False):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)

    fake_device = FakeCreative3DDevice(unity_available=unity_available)
    device = FakeDeviceAction(
        results={**happy_device_results(), **fake_device.capability_results()}
    )

    from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime: RealtimeVoiceRuntime = app.state.voice_realtime
    runtime._engine = engine
    runtime._session_factory = session_factory
    runtime.register_live(device_action=device)
    # The REST surface (app/creative3d/routes.py) reads ``app.state.artifacts`` for its
    # own db session — point it at the SAME test engine, the same swap
    # ``tests.voice_corpus.harness.build_harness`` performs for the full corpus.
    app.state.artifacts._engine = engine
    app.state.artifacts._session_factory = session_factory

    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    return test_client, device


def test_create_add_render_inspect_through_the_real_application_object() -> None:
    client, device = _client()

    session_id = client.post("/v1/voice/realtime/sessions", json={}).json()["session_id"]

    def say(text: str, turn: int = 1) -> None:
        client.post(
            f"/v1/voice/realtime/sessions/{session_id}/events",
            json={"events": [{"kind": "utterance", "t_ms": 1000, "turn": turn, "text": text}]},
        )

    def tool(name: str, arguments: dict, call_id: str) -> dict:
        return client.post(
            f"/v1/voice/realtime/sessions/{session_id}/tool-calls",
            json={"call_id": call_id, "name": name, "arguments": arguments},
        ).json()

    say("Blender'da yeni sahne aç.")
    created = tool("scene.create", {}, "c-1")
    assert created["status"] == "succeeded", created
    assert created["result"]["execution_status"] == "executed"
    assert created["result"]["state"] == STATE_APPLIED
    scene_id = created["result"]["scene_id"]

    say("Bir küp ekle.", turn=2)
    added = tool("scene.add", {}, "c-2")
    assert added["status"] == "succeeded", added
    assert added["result"]["objects"] == 1

    say("Sahnede ne var?", turn=3)
    inspected = tool("scene.inspect", {}, "c-3")
    assert inspected["status"] == "succeeded", inspected
    assert inspected["result"]["objects"] == 1

    resp = client.get(f"/v1/scenes/{scene_id}")
    assert resp.status_code == 200
    assert resp.json()["state"] == STATE_APPLIED

    assert device.capabilities_called() == [
        "project.scaffold",
        "project.run",
        "scene.inspect",
        "project.scaffold",
        "project.run",
        "scene.inspect",
        "scene.inspect",
    ]


def test_unity_licence_refusal_through_the_real_application_object() -> None:
    client, device = _client(unity_available=False)
    session_id = client.post("/v1/voice/realtime/sessions", json={}).json()["session_id"]
    client.post(
        f"/v1/voice/realtime/sessions/{session_id}/events",
        json={
            "events": [
                {
                    "kind": "utterance",
                    "t_ms": 1000,
                    "turn": 1,
                    "text": "Unity'de boş bir sahne oluştur.",
                }
            ]
        },
    )
    result = client.post(
        f"/v1/voice/realtime/sessions/{session_id}/tool-calls",
        json={"call_id": "c-1", "name": "scene.create", "arguments": {}},
    ).json()
    assert result["status"] == "succeeded"
    assert result["result"]["execution_status"] == "refused"
    assert result["result"]["error_class"] == "dependency_unavailable"
    assert UNITY_LICENSE_MESSAGE in result["result"]["speech"]


def test_a_device_that_refuses_scaffold_is_a_truthful_refusal() -> None:
    client, device = _client()
    device.results["project.scaffold"] = DeviceRunResult(False, "permission_denied", "no")

    session_id = client.post("/v1/voice/realtime/sessions", json={}).json()["session_id"]
    client.post(
        f"/v1/voice/realtime/sessions/{session_id}/events",
        json={
            "events": [
                {"kind": "utterance", "t_ms": 1000, "turn": 1, "text": "Blender'da yeni sahne aç."}
            ]
        },
    )
    result = client.post(
        f"/v1/voice/realtime/sessions/{session_id}/tool-calls",
        json={"call_id": "c-1", "name": "scene.create", "arguments": {}},
    ).json()
    assert result["status"] == "succeeded"
    assert result["result"]["execution_status"] == "refused"
    assert result["result"]["speech"]
