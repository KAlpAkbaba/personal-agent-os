"""Broker integration tests over the in-process app (TestClient WS + REST).

Requires the compose Postgres (scripts/dev-up.ps1); schema is migrated by the
session fixture in conftest.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.main import create_app
from tests.integration.broker_agent import (
    AgentKey,
    hello_frame,
    poll_until,
    rest_enroll,
    ws_handshake,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(
        _env_file=None,
        broker_heartbeat_interval_s=1.0,
        broker_sweep_interval_s=0.3,
        broker_default_command_timeout_s=60,
    )


@pytest.fixture(scope="module")
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def audit_engine(settings: Settings):
    engine = create_engine(settings.database_url)
    yield engine
    engine.dispose()


def audit_actions_for_command(audit_engine, command_id: str) -> list[str]:
    with audit_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT action, trace_id FROM audit_events "
                "WHERE command_id = :cid ORDER BY id"
            ),
            {"cid": command_id},
        ).all()
    return [row.action for row in rows]


def enroll_and_connect(client: TestClient):
    key = AgentKey()
    device_id = rest_enroll(client, key)
    return key, device_id


def get_device_status(client: TestClient, device_id: str) -> str:
    body = client.get("/v1/devices").json()
    matches = [d for d in body["devices"] if d["device_id"] == device_id]
    assert matches, f"device {device_id} not listed"
    return matches[0]["status"]


def test_happy_path_full_command_lifecycle(client: TestClient, audit_engine) -> None:
    key, device_id = enroll_and_connect(client)
    trace_id = f"itest-trace-{uuid.uuid4().hex[:12]}"

    with client.websocket_connect("/v1/devices/connect") as ws:
        welcome = ws_handshake(ws, device_id, key)
        assert welcome["heartbeat_interval_s"] == 1.0

        assert get_device_status(client, device_id) == "online"

        response = client.post(
            f"/v1/devices/{device_id}/commands",
            json={
                "capability": "desktop.open_application",
                "payload": {"application": "notepad"},
                "idempotency_key": f"itest-{uuid.uuid4().hex}",
            },
            headers={"X-Trace-Id": trace_id},
        )
        assert response.status_code == 202
        command_id = response.json()["command_id"]

        frame = ws.receive_json()
        assert frame["type"] == "command"
        command = frame["command"]
        assert command["command_id"] == command_id
        assert command["capability"] == "desktop.open_application"
        assert command["payload"] == {"application": "notepad"}
        assert command["trace_id"] == trace_id
        assert command["expires_at"].endswith("Z")

        ws.send_json({"type": "command_ack", "command_id": command_id, "status": "accepted"})
        ws.send_json({"type": "command_ack", "command_id": command_id, "status": "running"})
        ws.send_json(
            {
                "type": "command_ack",
                "command_id": command_id,
                "status": "succeeded",
                "result": {"pid": 4242},
            }
        )

        final = poll_until(
            lambda: (
                lambda body: body if body["status"] == "succeeded" else None
            )(client.get(f"/v1/devices/{device_id}/commands/{command_id}").json()),
            timeout_s=10,
        )
        assert final["result"] == {"pid": 4242}
        assert final["error"] is None
        assert final["trace_id"] == trace_id
        assert final["delivered_at"] is not None
        assert final["terminal_at"] is not None

    actions = audit_actions_for_command(audit_engine, command_id)
    for expected in (
        "command_created",
        "command_delivered",
        "command_ack_accepted",
        "command_ack_running",
        "command_ack_succeeded",
    ):
        assert expected in actions, f"missing audit action {expected}: {actions}"
    with audit_engine.connect() as conn:
        trace_ids = {
            row.trace_id
            for row in conn.execute(
                text("SELECT trace_id FROM audit_events WHERE command_id = :cid"),
                {"cid": command_id},
            )
        }
    assert trace_ids == {trace_id}


def test_duplicate_command_creation_same_idempotency_key(client: TestClient) -> None:
    key, device_id = enroll_and_connect(client)
    idempotency_key = f"itest-dup-{uuid.uuid4().hex}"
    body = {
        "capability": "desktop.open_application",
        "payload": {"application": "calc"},
        "idempotency_key": idempotency_key,
    }

    with client.websocket_connect("/v1/devices/connect") as ws:
        ws_handshake(ws, device_id, key)

        first = client.post(f"/v1/devices/{device_id}/commands", json=body)
        second = client.post(f"/v1/devices/{device_id}/commands", json=body)
        assert first.status_code == 202 and second.status_code == 202
        command_id = first.json()["command_id"]
        assert second.json()["command_id"] == command_id  # same command returned

        frame = ws.receive_json()
        assert frame["type"] == "command"
        assert frame["command"]["command_id"] == command_id

        # No second delivery for the deduplicated creation: the next frame the
        # broker sends must be the heartbeat ack, not another command.
        ws.send_json({"type": "heartbeat", "seq": 7})
        next_frame = ws.receive_json()
        assert next_frame == {"type": "heartbeat_ack", "seq": 7}

        ws.send_json(
            {
                "type": "command_ack",
                "command_id": command_id,
                "status": "succeeded",
                "result": {"pid": 1},
            }
        )
        poll_until(
            lambda: client.get(
                f"/v1/devices/{device_id}/commands/{command_id}"
            ).json()["status"]
            == "succeeded",
            timeout_s=10,
        )


def test_duplicate_ws_delivery_tolerated_via_reack(client: TestClient) -> None:
    key, device_id = enroll_and_connect(client)
    with client.websocket_connect("/v1/devices/connect") as ws:
        ws_handshake(ws, device_id, key)
        response = client.post(
            f"/v1/devices/{device_id}/commands",
            json={"capability": "desktop.open_application", "payload": {}},
        )
        command_id = response.json()["command_id"]
        frame = ws.receive_json()
        assert frame["command"]["command_id"] == command_id

        terminal_ack = {
            "type": "command_ack",
            "command_id": command_id,
            "status": "succeeded",
            "result": {"pid": 9},
        }
        ws.send_json(terminal_ack)
        poll_until(
            lambda: client.get(
                f"/v1/devices/{device_id}/commands/{command_id}"
            ).json()["status"]
            == "succeeded",
            timeout_s=10,
        )
        # duplicate delivery happened -> agent re-sends the recorded terminal ack
        ws.send_json(terminal_ack)
        ws.send_json({"type": "heartbeat", "seq": 1})
        # no error frame: the very next frame is the heartbeat ack
        assert ws.receive_json() == {"type": "heartbeat_ack", "seq": 1}

    final = client.get(f"/v1/devices/{device_id}/commands/{command_id}").json()
    assert final["status"] == "succeeded"
    assert final["result"] == {"pid": 9}


def test_malformed_frame_validation_error_connection_survives(client: TestClient) -> None:
    key, device_id = enroll_and_connect(client)
    before = client.get("/v1/devices/stats").json()["counters"].get("malformed_frames", 0)
    with client.websocket_connect("/v1/devices/connect") as ws:
        ws_handshake(ws, device_id, key)

        ws.send_text("this is {not json")
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["class"] == "validation_error"

        ws.send_json({"type": "command_ack", "command_id": str(uuid.uuid4()), "status": "warp"})
        error2 = ws.receive_json()
        assert error2["error"]["class"] == "validation_error"

        # connection is still alive and serving
        ws.send_json({"type": "heartbeat", "seq": 3})
        assert ws.receive_json() == {"type": "heartbeat_ack", "seq": 3}

    after = client.get("/v1/devices/stats").json()["counters"]["malformed_frames"]
    assert after >= before + 2


def test_command_to_offline_device_pending_then_redelivered(client: TestClient) -> None:
    key, device_id = enroll_and_connect(client)
    assert get_device_status(client, device_id) == "offline"

    response = client.post(
        f"/v1/devices/{device_id}/commands",
        json={"capability": "desktop.open_application", "payload": {"application": "calc"}},
    )
    assert response.status_code == 202
    command_id = response.json()["command_id"]
    assert response.json()["status"] == "pending"
    detail = client.get(f"/v1/devices/{device_id}/commands/{command_id}").json()
    assert detail["status"] == "pending"
    assert detail["delivered_at"] is None

    with client.websocket_connect("/v1/devices/connect") as ws:
        ws_handshake(ws, device_id, key)
        frame = ws.receive_json()  # redelivery straight after welcome
        assert frame["type"] == "command"
        assert frame["command"]["command_id"] == command_id
        ws.send_json(
            {
                "type": "command_ack",
                "command_id": command_id,
                "status": "succeeded",
                "result": {"pid": 5},
            }
        )
        poll_until(
            lambda: client.get(
                f"/v1/devices/{device_id}/commands/{command_id}"
            ).json()["status"]
            == "succeeded",
            timeout_s=10,
        )


def test_agent_disconnect_marks_device_offline(client: TestClient) -> None:
    key, device_id = enroll_and_connect(client)
    with client.websocket_connect("/v1/devices/connect") as ws:
        ws_handshake(ws, device_id, key)
        poll_until(lambda: get_device_status(client, device_id) == "online", timeout_s=5)
    poll_until(lambda: get_device_status(client, device_id) == "offline", timeout_s=5)
    devices = client.get("/v1/devices").json()["devices"]
    me = next(d for d in devices if d["device_id"] == device_id)
    assert me["last_seen_at"] is not None


def test_undelivered_command_expires_server_side(client: TestClient, audit_engine) -> None:
    _, device_id = enroll_and_connect(client)  # never connects
    response = client.post(
        f"/v1/devices/{device_id}/commands",
        json={
            "capability": "desktop.open_application",
            "payload": {},
            "timeout_s": 0.2,
        },
    )
    assert response.status_code == 202
    command_id = response.json()["command_id"]

    final = poll_until(
        lambda: (
            lambda body: body if body["status"] == "expired" else None
        )(client.get(f"/v1/devices/{device_id}/commands/{command_id}").json()),
        timeout_s=10,
    )
    assert final["error"]["class"] == "command_expired"
    assert final["terminal_at"] is not None
    assert "command_expired" in audit_actions_for_command(audit_engine, command_id)


def test_cancel_undelivered_command(client: TestClient) -> None:
    _, device_id = enroll_and_connect(client)
    response = client.post(
        f"/v1/devices/{device_id}/commands",
        json={"capability": "desktop.open_application", "payload": {}},
    )
    command_id = response.json()["command_id"]
    cancel = client.post(f"/v1/devices/{device_id}/commands/{command_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    assert cancel.json()["cancel_forwarded"] is False
    detail = client.get(f"/v1/devices/{device_id}/commands/{command_id}").json()
    assert detail["status"] == "cancelled"
    assert detail["error"]["class"] == "cancelled"


def test_revoked_device_handshake_rejected(client: TestClient) -> None:
    key, device_id = enroll_and_connect(client)
    revoke = client.post(f"/v1/devices/{device_id}/revoke")
    assert revoke.status_code == 200
    assert get_device_status(client, device_id) == "revoked"

    with client.websocket_connect("/v1/devices/connect") as ws:
        ws.send_json(hello_frame(device_id))
        challenge = ws.receive_json()  # identical flow: challenge is still issued
        assert challenge["type"] == "challenge"
        ws.send_json(
            {"type": "auth", "signature": key.sign_challenge(challenge["nonce"], device_id)}
        )
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["class"] == "auth_error"

    # commands to a revoked device are refused
    response = client.post(
        f"/v1/devices/{device_id}/commands",
        json={"capability": "desktop.open_application", "payload": {}},
    )
    assert response.status_code == 409


def test_wrong_signature_rejected_identically(client: TestClient) -> None:
    _, device_id = enroll_and_connect(client)
    wrong_key = AgentKey()  # enrolled with a different key
    with client.websocket_connect("/v1/devices/connect") as ws:
        ws.send_json(hello_frame(device_id))
        challenge = ws.receive_json()
        ws.send_json(
            {
                "type": "auth",
                "signature": wrong_key.sign_challenge(challenge["nonce"], device_id),
            }
        )
        error = ws.receive_json()
        assert error["error"]["class"] == "auth_error"


def test_unknown_device_rejected_identically(client: TestClient) -> None:
    key = AgentKey()
    ghost_device_id = str(uuid.uuid4())
    with client.websocket_connect("/v1/devices/connect") as ws:
        ws.send_json(hello_frame(ghost_device_id))
        challenge = ws.receive_json()  # same step order as the valid flow
        assert challenge["type"] == "challenge"
        ws.send_json(
            {
                "type": "auth",
                "signature": key.sign_challenge(challenge["nonce"], ghost_device_id),
            }
        )
        error = ws.receive_json()
        assert error["error"]["class"] == "auth_error"


def test_unsupported_protocol_version_rejected(client: TestClient) -> None:
    key, device_id = enroll_and_connect(client)
    with client.websocket_connect("/v1/devices/connect") as ws:
        ws.send_json(hello_frame(device_id, protocol_version=2))
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["class"] == "auth_error"


def test_enrollment_token_single_use_over_rest(client: TestClient) -> None:
    key = AgentKey()
    token = client.post("/v1/devices/enrollment-tokens").json()["token"]
    body = {
        "token": token,
        "name": "itest-single-use",
        "platform": "windows",
        "public_key_spki_b64": key.spki_b64,
        "capabilities": [],
    }
    first = client.post("/v1/devices/enroll", json=body)
    assert first.status_code == 201
    second = client.post("/v1/devices/enroll", json=body)
    assert second.status_code == 400
