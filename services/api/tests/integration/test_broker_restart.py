"""Broker restart durability test against a real uvicorn subprocess.

Proves: a pending command survives a broker kill/restart; the test agent's
client-side backoff loop reconnects on its own; the command is then delivered
(durability + at-least-once delivery); a clean shutdown is not required.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import httpx
import pytest

from tests.integration import procs
from tests.integration.broker_agent import (
    AgentKey,
    AsyncAgent,
    rest_enroll,
    wait_for_health,
)

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
PORT = 8013
BASE_URL = f"http://127.0.0.1:{PORT}"
WS_URL = f"ws://127.0.0.1:{PORT}/v1/devices/connect"

# M9: the broker REST surface requires an owner session. This test drives a real
# uvicorn subprocess, so it exercises the production credential root as well:
# the server writes/reads a real identity-root FILE in a throwaway directory,
# the owner credential is minted once through POST /v1/identity/bootstrap
# (loopback-only, one-time), and the resulting session lives in PostgreSQL —
# which is why the same bearer token still works after the broker is killed.
IDENTITY_ROOT_DIR = tempfile.mkdtemp(prefix="pagentos-itest-identity-")
_owner_headers: dict[str, str] = {}


def owner_headers() -> dict[str, str]:
    """Bootstrap (once) and exchange the owner credential for a session."""
    if _owner_headers:
        return _owner_headers
    with httpx.Client(base_url=BASE_URL, timeout=10) as http:
        bootstrap = http.post("/v1/identity/bootstrap")
        assert bootstrap.status_code == 201, bootstrap.text
        credential = bootstrap.json()["owner_credential"]
        session = http.post(
            "/v1/identity/sessions",
            json={
                "owner_credential": credential,
                "client_kind": "cli",
                "label": "itest-broker-restart",
            },
        )
        assert session.status_code == 201, session.text
        _owner_headers["Authorization"] = f"Bearer {session.json()['token']}"
    return _owner_headers


def start_broker_process() -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "PAGENTOS_BROKER_HEARTBEAT_INTERVAL_S": "1.0",
            "PAGENTOS_BROKER_SWEEP_INTERVAL_S": "0.5",
            "PAGENTOS_IDENTITY_ROOT_DIR": IDENTITY_ROOT_DIR,
        }
    )
    proc = procs.spawn(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(API_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_health(BASE_URL, timeout_s=45)
    except Exception:
        proc.kill()
        raise
    return proc


def kill(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.kill()
        proc.wait(timeout=15)


async def test_broker_restart_pending_command_survives_and_agent_reconnects() -> None:
    proc = await asyncio.to_thread(start_broker_process)
    proc2: subprocess.Popen | None = None
    key = AgentKey()
    try:
        headers = await asyncio.to_thread(owner_headers)
        with httpx.Client(base_url=BASE_URL, timeout=10, headers=headers) as http:
            device_id = rest_enroll(http, key, name="itest-restart")

        agent = AsyncAgent(WS_URL, device_id, key)

        # 1. Prove initial connectivity, then drop the connection.
        conn = await agent.connect_with_backoff(deadline_s=15)
        await conn.close()

        # 2. Create a command while the device is offline -> stays pending.
        idempotency_key = f"itest-restart-{uuid.uuid4().hex}"
        with httpx.Client(base_url=BASE_URL, timeout=10, headers=headers) as http:
            response = http.post(
                f"/v1/devices/{device_id}/commands",
                json={
                    "capability": "desktop.open_application",
                    "payload": {"application": "notepad"},
                    "idempotency_key": idempotency_key,
                    "timeout_s": 300,
                },
            )
            assert response.status_code == 202
            command_id = response.json()["command_id"]
            assert response.json()["status"] == "pending"

        # 3. Kill the broker hard (no clean shutdown).
        await asyncio.to_thread(kill, proc)

        # 4. Start the agent's reconnect loop while the broker is still down,
        #    so real backoff retries happen against a dead endpoint.
        reconnect_task = asyncio.create_task(agent.connect_with_backoff(deadline_s=60))
        await asyncio.sleep(1.5)
        assert not reconnect_task.done()  # still retrying against a dead broker

        # 5. Restart the broker; the agent must reconnect on its own.
        proc2 = await asyncio.to_thread(start_broker_process)
        conn = await reconnect_task

        try:
            # 6. Durability: the pending command from before the crash is
            #    delivered on reconnect without any re-submission.
            frame = await AsyncAgent.recv_frame(conn, timeout_s=15)
            assert frame["type"] == "command", frame
            assert frame["command"]["command_id"] == command_id
            assert frame["command"]["idempotency_key"] == idempotency_key

            await AsyncAgent.send_frame(
                conn,
                {"type": "command_ack", "command_id": command_id, "status": "accepted"},
            )
            await AsyncAgent.send_frame(
                conn,
                {
                    "type": "command_ack",
                    "command_id": command_id,
                    "status": "succeeded",
                    "result": {"pid": 777},
                },
            )

            async def final_status() -> str:
                async with httpx.AsyncClient(
                    base_url=BASE_URL, timeout=10, headers=headers
                ) as http:
                    body = (
                        await http.get(f"/v1/devices/{device_id}/commands/{command_id}")
                    ).json()
                    return body["status"]

            deadline = asyncio.get_running_loop().time() + 15
            while True:
                if await final_status() == "succeeded":
                    break
                assert asyncio.get_running_loop().time() < deadline, "never reached succeeded"
                await asyncio.sleep(0.2)
        finally:
            await conn.close()
    finally:
        # Cleanup, not the test: a graceful stop lets each broker process close
        # its PostgreSQL connections instead of leaving idle backends behind.
        await asyncio.to_thread(procs.stop, proc)
        await asyncio.to_thread(procs.stop, proc2)
