"""Minimal protocol-v1 test device agent used by broker integration tests.

Two client flavors:
- sync helpers driving a starlette TestClient WebSocket session (in-process);
- AsyncAgent driving a real socket via `websockets` against a uvicorn
  subprocess, with the exponential-backoff reconnect loop the protocol
  requires of real agents.
"""

import asyncio
import base64
import json
import time
import uuid
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SOFTWARE_VERSION = "itest-agent/0.1"
CAPABILITIES = ["desktop.open_application"]


class AgentKey:
    """ECDSA P-256 device keypair (protocol §2)."""

    def __init__(self) -> None:
        self.private_key = ec.generate_private_key(ec.SECP256R1())

    @property
    def spki_b64(self) -> str:
        return base64.b64encode(
            self.private_key.public_key().public_bytes(
                Encoding.DER, PublicFormat.SubjectPublicKeyInfo
            )
        ).decode("ascii")

    def sign_challenge(self, nonce_b64: str, device_id: str) -> str:
        message = base64.b64decode(nonce_b64) + device_id.encode("utf-8")
        der = self.private_key.sign(message, ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(der).decode("ascii")


def hello_frame(device_id: str, protocol_version: int = 1) -> dict[str, Any]:
    return {
        "type": "hello",
        "protocol_version": protocol_version,
        "device_id": device_id,
        "software_version": SOFTWARE_VERSION,
        "capabilities": CAPABILITIES,
    }


def rest_enroll(client: Any, key: AgentKey, name: str | None = None) -> str:
    """Enroll a device over REST (works for TestClient and httpx.Client)."""
    token_response = client.post("/v1/devices/enrollment-tokens")
    assert token_response.status_code == 201, token_response.text
    token = token_response.json()["token"]
    enroll_response = client.post(
        "/v1/devices/enroll",
        json={
            "token": token,
            "name": name or f"itest-{uuid.uuid4().hex[:8]}",
            "platform": "windows",
            "public_key_spki_b64": key.spki_b64,
            "capabilities": CAPABILITIES,
        },
    )
    assert enroll_response.status_code == 201, enroll_response.text
    return enroll_response.json()["device_id"]


def ws_handshake(ws: Any, device_id: str, key: AgentKey) -> dict[str, Any]:
    """Run hello -> challenge -> auth -> welcome on a TestClient WS session."""
    ws.send_json(hello_frame(device_id))
    challenge = ws.receive_json()
    assert challenge["type"] == "challenge", challenge
    ws.send_json(
        {"type": "auth", "signature": key.sign_challenge(challenge["nonce"], device_id)}
    )
    welcome = ws.receive_json()
    assert welcome["type"] == "welcome", welcome
    return welcome


def poll_until(fn, timeout_s: float = 10.0, interval_s: float = 0.05):
    """Poll fn() until it returns a truthy value; raise on timeout."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(f"poll_until timed out after {timeout_s}s")
        time.sleep(interval_s)


def wait_for_health(base_url: str, timeout_s: float = 30.0) -> None:
    def probe() -> bool:
        try:
            return httpx.get(f"{base_url}/v1/system/health", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    poll_until(probe, timeout_s=timeout_s, interval_s=0.25)


class AsyncAgent:
    """Real-socket agent with the protocol's reconnect-with-backoff behavior."""

    def __init__(self, ws_url: str, device_id: str, key: AgentKey) -> None:
        self.ws_url = ws_url
        self.device_id = device_id
        self.key = key

    async def connect_once(self):
        """One connection attempt incl. handshake. Returns the open websocket."""
        import websockets

        conn = await websockets.connect(self.ws_url, open_timeout=3)
        try:
            await conn.send(json.dumps(hello_frame(self.device_id)))
            challenge = json.loads(await asyncio.wait_for(conn.recv(), timeout=5))
            if challenge.get("type") != "challenge":
                raise ConnectionError(f"expected challenge, got {challenge}")
            signature = self.key.sign_challenge(challenge["nonce"], self.device_id)
            await conn.send(json.dumps({"type": "auth", "signature": signature}))
            welcome = json.loads(await asyncio.wait_for(conn.recv(), timeout=5))
            if welcome.get("type") != "welcome":
                raise ConnectionError(f"expected welcome, got {welcome}")
            return conn
        except BaseException:
            await conn.close()
            raise

    async def connect_with_backoff(self, deadline_s: float = 30.0):
        """Reconnect loop: exponential backoff 0.2s -> 2s until deadline."""
        deadline = asyncio.get_running_loop().time() + deadline_s
        delay = 0.2
        while True:
            try:
                return await self.connect_once()
            except Exception:
                if asyncio.get_running_loop().time() + delay > deadline:
                    raise
                await asyncio.sleep(delay)
                delay = min(delay * 2, 2.0)

    @staticmethod
    async def recv_frame(conn, timeout_s: float = 10.0) -> dict[str, Any]:
        return json.loads(await asyncio.wait_for(conn.recv(), timeout=timeout_s))

    @staticmethod
    async def send_frame(conn, frame: dict[str, Any]) -> None:
        await conn.send(json.dumps(frame))
