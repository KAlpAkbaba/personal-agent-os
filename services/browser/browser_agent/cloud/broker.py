"""The Device Broker client: enroll (REST), then hello -> challenge -> auth -> commands.

Speaks the SAME protocol as the Windows agent (packages/protocol/DEVICE_PROTOCOL.md):
ECDSA P-256 over ``nonce_bytes || device_id_utf8``, ``command_ack`` accepted -> running ->
succeeded|failed, idempotency by ``idempotency_key``, expiry, cancel. ``cryptography`` (the
``cloud`` extra) is imported where it is used, so the pure parts load without it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import sys
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from .. import policy
from ..errors import BrowserError
from ..obs_logging import get_logger
from ..worker import WORKER_VERSION
from . import config
from . import policy as cloud_policy

logger = get_logger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_MS = 60_000

#: The broker's closed error-class list, restricted to what this worker can answer with.
#: Anything else is an internal_bug (a test compares the worker's classes with the api's).
BROKER_ERROR_CLASSES = frozenset(
    {
        "validation_error",
        "capability_missing",
        "dependency_unavailable",
        "provider_rate_limited",
        "ui_target_not_found",
        "ui_state_changed",
        "timeout",
        "command_expired",
        "cancelled",
        "security_scope_error",
        "browser_lifecycle_violation",
        "internal_bug",
    }
)


class AuthRefused(Exception):
    """The broker refused the handshake (unknown/revoked device or bad signature)."""


class WorkerLink(Protocol):
    async def exec(
        self, request_id: str, capability: str, payload: dict[str, Any], timeout_ms: int
    ) -> dict[str, Any]: ...

    def cancel(self, request_id: str) -> None: ...


# ------------------------------------------------------------------ enrollment


def generate_identity() -> tuple[bytes, str]:
    """A fresh P-256 keypair: (private key PEM, base64 DER SPKI public key)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem, base64.b64encode(spki).decode("ascii")


def sign_challenge(private_key_pem: bytes, nonce_b64: str, device_id: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    message = base64.b64decode(nonce_b64) + device_id.encode("utf-8")
    return base64.b64encode(key.sign(message, ec.ECDSA(hashes.SHA256()))).decode("ascii")


def enroll_body(token: str, public_key_spki_b64: str) -> dict[str, Any]:
    return {
        "token": token,
        "name": config.DEVICE_NAME,
        "platform": config.DEVICE_PLATFORM,
        "public_key_spki_b64": public_key_spki_b64,
        "capabilities": list(policy.CAPABILITIES),
    }


def enroll(cfg: config.CloudConfig) -> str:
    """Consume the one-shot token, persist identity + key (mode 600), delete the token."""
    pem, spki = generate_identity()
    request = urllib.request.Request(  # noqa: S310 - the broker URL is operator configuration
        cfg.broker_http_url + "/v1/devices/enroll",
        data=json.dumps(enroll_body(cfg.read_token(), spki)).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        device_id = str(json.loads(response.read())["device_id"])
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.key_path.write_bytes(pem)
    with contextlib.suppress(OSError):
        cfg.key_path.chmod(0o600)
    cfg.identity_path.write_text(json.dumps({"device_id": device_id}), encoding="utf-8")
    with contextlib.suppress(OSError):
        cfg.token_file.unlink()
    return device_id


# ------------------------------------------------------------------ frames


def hello_frame(device_id: str) -> dict[str, Any]:
    return {
        "type": "hello",
        "protocol_version": PROTOCOL_VERSION,
        "device_id": device_id,
        "software_version": f"cloud-browser-{WORKER_VERSION}"[:64],
        "capabilities": list(policy.CAPABILITIES),
    }


def _parse_expiry(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def _error_ack(command_id: str, error_class: str, message: str) -> dict[str, Any]:
    return {
        "type": "command_ack",
        "command_id": command_id,
        "status": "failed",
        "error": {"class": error_class, "message": message[:2000]},
    }


# ------------------------------------------------------------------ the bridge


class CloudBridge:
    def __init__(
        self,
        *,
        device_id: str,
        signer: Callable[[str], str],
        worker: WorkerLink,
        max_concurrent: int = 2,
        on_alive: Callable[[], None] | None = None,
    ) -> None:
        self._on_alive = on_alive or (lambda: None)
        self._device_id = device_id
        self._signer = signer
        self._worker = worker
        self._gate = asyncio.Semaphore(max_concurrent)
        self._done: dict[str, dict[str, Any]] = {}
        self._running: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._seq = 0

    async def _send(self, ws: Any, frame: dict[str, Any]) -> None:
        await ws.send(json.dumps(frame))

    async def serve(self, ws: Any) -> None:
        await self._send(ws, hello_frame(self._device_id))
        challenge = json.loads(await ws.recv())
        if challenge.get("type") != "challenge":
            raise AuthRefused(f"expected a challenge, got {challenge.get('type')!r}")
        await self._send(ws, {"type": "auth", "signature": self._signer(str(challenge["nonce"]))})
        welcome = json.loads(await ws.recv())
        if welcome.get("type") != "welcome":
            raise AuthRefused(str(welcome.get("error", welcome)))
        self._on_alive()
        beat = asyncio.create_task(self._heartbeats(ws, float(welcome["heartbeat_interval_s"])))
        try:
            async for raw in ws:
                frame = json.loads(raw)
                if frame.get("type") == "command":
                    self._spawn(self._run_command(ws, frame["command"]))
                elif frame.get("type") == "cancel":
                    self._worker.cancel(str(frame["command_id"]))
                # heartbeat_ack, error and unknown frames need no answer here
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _heartbeats(self, ws: Any, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            self._seq += 1
            self._on_alive()
            with contextlib.suppress(Exception):
                await self._send(ws, {"type": "heartbeat", "seq": self._seq})

    async def _run_command(self, ws: Any, command: dict[str, Any]) -> None:
        command_id = str(command["command_id"])
        key = str(command["idempotency_key"])
        if key in self._done:
            await self._send(ws, {**self._done[key], "command_id": command_id})
            return
        if key in self._running:
            return
        expires = _parse_expiry(command.get("expires_at"))
        if expires is not None and expires <= datetime.now(UTC):
            await self._finish(ws, key, _error_ack(command_id, "command_expired", "expired"))
            return
        self._running.add(key)
        try:
            await self._send(
                ws, {"type": "command_ack", "command_id": command_id, "status": "accepted"}
            )
            capability = str(command["capability"])
            if capability not in policy.CAPABILITIES:
                ack = _error_ack(command_id, "capability_missing", capability)
            else:
                ack = await self._execute(ws, command_id, capability, command.get("payload"))
            await self._finish(ws, key, ack)
        finally:
            self._running.discard(key)

    async def _execute(
        self, ws: Any, command_id: str, capability: str, payload: Any
    ) -> dict[str, Any]:
        try:
            clamped = cloud_policy.clamp_command(
                capability, payload if isinstance(payload, dict) else {}
            )
        except BrowserError as exc:
            return _error_ack(command_id, str(exc.error_class), str(exc))
        async with self._gate:
            await self._send(
                ws, {"type": "command_ack", "command_id": command_id, "status": "running"}
            )
            envelope = await self._worker.exec(command_id, capability, clamped, DEFAULT_TIMEOUT_MS)
        if envelope.get("ok"):
            return {
                "type": "command_ack",
                "command_id": command_id,
                "status": "succeeded",
                "result": envelope.get("result") or {},
            }
        error = envelope.get("error") or {}
        error_class = str(error.get("class", "internal_bug"))
        if error_class not in BROKER_ERROR_CLASSES:
            error_class = "internal_bug"
        return _error_ack(command_id, error_class, str(error.get("message", "worker failed")))

    async def _finish(self, ws: Any, key: str, ack: dict[str, Any]) -> None:
        self._done[key] = ack
        await self._send(ws, ack)


# ------------------------------------------------------------------ the worker child


class SubprocessWorker:
    """The unchanged worker as a child process, stdio JSON lines (contract section 7).

    Not exercised by the unit suite (it needs a real browser worker): NOT_RUN until the
    Cloud Core run of the measurement script.
    """

    def __init__(self, argv: list[str]) -> None:
        self._argv = argv
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None

    async def start(self) -> dict[str, Any]:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "browser_agent.worker",
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        assert self._proc.stdout is not None
        hello = json.loads(await self._proc.stdout.readline())
        self._reader = asyncio.create_task(self._read_results())
        return hello

    async def _read_results(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while line := await self._proc.stdout.readline():
            with contextlib.suppress(ValueError):
                msg = json.loads(line)
                future = self._pending.pop(str(msg.get("request_id")), None)
                if msg.get("type") == "result" and future is not None and not future.done():
                    future.set_result(msg)
        for future in self._pending.values():
            if not future.done():
                future.set_result(
                    {
                        "ok": False,
                        "error": {
                            "class": "dependency_unavailable",
                            "message": "worker exited",
                            "retryable": True,
                        },
                    }
                )

    async def exec(
        self, request_id: str, capability: str, payload: dict[str, Any], timeout_ms: int
    ) -> dict[str, Any]:
        assert self._proc is not None and self._proc.stdin is not None
        request_id = request_id or str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        line = {
            "type": "exec",
            "request_id": request_id,
            "capability": capability,
            "payload": payload,
            "timeout_ms": timeout_ms,
        }
        self._proc.stdin.write((json.dumps(line) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        return await future

    def cancel(self, request_id: str) -> None:
        if self._proc is not None and self._proc.stdin is not None:
            line = json.dumps({"type": "cancel", "request_id": request_id}) + "\n"
            self._proc.stdin.write(line.encode("utf-8"))

    async def stop(self) -> None:
        if self._proc is None:
            return
        with contextlib.suppress(Exception):
            assert self._proc.stdin is not None
            self._proc.stdin.write((json.dumps({"type": "shutdown"}) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._proc.wait(), timeout=15)
        if self._proc.returncode is None:
            self._proc.kill()
