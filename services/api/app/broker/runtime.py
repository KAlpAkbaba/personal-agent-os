"""Broker runtime state: live connections, counters, expiry sweeper.

One BrokerRuntime instance lives on app.state.broker. Presence is process-local
(the broker is a single in-process module by architecture decision); durable
state (devices, sessions, commands, audit) is PostgreSQL.
"""

import asyncio
import contextlib
import uuid
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.broker import service
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger

logger = get_logger("app.broker.runtime")


@dataclass
class DeviceConnection:
    device_id: uuid.UUID
    session_id: uuid.UUID
    websocket: Any  # starlette WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, frame: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(frame)


class BrokerRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.connections: dict[uuid.UUID, DeviceConnection] = {}
        self.counters: Counter[str] = Counter()
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._sweeper_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ db

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = build_engine(self.settings.database_url)
            self._session_factory = build_session_factory(self._engine)
        return self._engine

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        _ = self.engine  # ensure factory
        assert self._session_factory is not None
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    # ---------------------------------------------------------- connections

    def register_connection(self, connection: DeviceConnection) -> None:
        self.connections[connection.device_id] = connection
        self.counters["sessions_started"] += 1

    def unregister_connection(self, connection: DeviceConnection) -> None:
        current = self.connections.get(connection.device_id)
        if current is connection:
            del self.connections[connection.device_id]
        self.counters["sessions_ended"] += 1

    def is_online(self, device_id: uuid.UUID) -> bool:
        return device_id in self.connections

    def get_connection(self, device_id: uuid.UUID) -> DeviceConnection | None:
        return self.connections.get(device_id)

    async def send_frame(self, device_id: uuid.UUID, frame: dict[str, Any]) -> bool:
        """Best-effort send to a connected device. False when offline/failed."""
        connection = self.connections.get(device_id)
        if connection is None:
            return False
        try:
            await connection.send_json(frame)
            return True
        except Exception as exc:  # noqa: BLE001 - socket may die mid-send
            logger.warning(
                "broker_send_failed", device_id=str(device_id), error=f"{type(exc).__name__}: {exc}"
            )
            return False

    async def close_device_connection(self, device_id: uuid.UUID, code: int = 1000) -> None:
        connection = self.connections.get(device_id)
        if connection is None:
            return
        with contextlib.suppress(Exception):
            await connection.websocket.close(code=code)

    # -------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._sweeper_task is None or self._sweeper_task.done():
            self._sweeper_task = asyncio.create_task(self._sweep_loop(), name="broker-sweeper")
        try:
            ended = await asyncio.to_thread(self._end_orphan_sessions)
            if ended:
                logger.info("broker_orphan_sessions_ended", count=ended)
        except Exception as exc:  # noqa: BLE001 - DB may be down; broker must still start
            logger.warning(
                "broker_orphan_cleanup_failed", error=f"{type(exc).__name__}: {exc}"
            )

    async def stop(self) -> None:
        if self._sweeper_task is not None:
            self._sweeper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper_task
            self._sweeper_task = None
        for device_id in list(self.connections):
            await self.close_device_connection(device_id)

    def _end_orphan_sessions(self) -> int:
        with self.session() as db:
            return service.end_orphan_sessions(db)

    # ---------------------------------------------------------------- sweeper

    def sweeper_alive(self) -> bool:
        return self._sweeper_task is not None and not self._sweeper_task.done()

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.broker_sweep_interval_s)
            try:
                expired = await asyncio.to_thread(self._expire_due)
                if expired:
                    self.counters["commands_expired"] += len(expired)
                    for command_id, device_id, trace_id in expired:
                        logger.info(
                            "broker_command_expired",
                            command_id=command_id,
                            device_id=device_id,
                            command_trace_id=trace_id,
                        )
                await self._deliver_pending_for_connected()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - sweeper must survive DB outages
                logger.warning("broker_sweep_failed", error=f"{type(exc).__name__}: {exc}")

    def _expire_due(self) -> list[tuple[str, str, str]]:
        with self.session() as db:
            due = service.expire_due_commands(db)
            return [(str(c.id), str(c.device_id), c.trace_id) for c in due]

    async def _deliver_pending_for_connected(self) -> None:
        """M13 spec §5: every sweep also delivers PENDING commands for devices
        that are connected to THIS process — not only on (re)connect — so a
        command row created by another process (e.g. a Temporal activity
        talking to the broker via app.devices.commands) reaches an
        already-connected device within one sweep interval."""
        device_ids = list(self.connections)
        if not device_ids:
            return
        pending = await asyncio.to_thread(self._load_pending, device_ids)
        if not pending:
            return
        # Local import: app.broker.ws imports BrokerRuntime/DeviceConnection
        # from this module, so a top-level import here would be circular.
        from app.broker.ws import deliver_command

        for command in pending:
            connection = self.connections.get(command.device_id)
            if connection is None:
                continue
            delivered = await deliver_command(self, connection, command)
            if delivered:
                self.counters["commands_sweep_delivered"] += 1

    def _load_pending(self, device_ids: list[uuid.UUID]) -> list[Any]:
        with self.session() as db:
            return service.pending_undelivered_commands(db, device_ids)

    # ----------------------------------------------------------------- health

    def health_check(self) -> dict[str, Any]:
        alive = self.sweeper_alive()
        return {
            "status": "ok" if alive else "fail",
            "latency_ms": 0.0,
            "sweeper_alive": alive,
            "active_sessions": len(self.connections),
        }

    def stats(self) -> dict[str, Any]:
        return {
            "active_sessions": len(self.connections),
            "connected_device_ids": sorted(str(d) for d in self.connections),
            "sweeper_alive": self.sweeper_alive(),
            "counters": dict(self.counters),
        }
