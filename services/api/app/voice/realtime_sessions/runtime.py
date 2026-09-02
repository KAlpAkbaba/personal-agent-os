"""Realtime voice runtime: DB session factory + realtime provider registry +
tool registry + sideband pusher. Lives on ``app.state.voice_realtime``.

Providers are ``ConversationRealtime`` candidates only (the M4 TTS/STT
providers are a different subsystem, constitution Voice rule). Today the
registry holds the deterministic simulator; a real speech-to-speech adapter
(track B) registers here and is selected — or not — by capability, never by
being named in code outside the adapter layer.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger
from app.voice.errors import VoiceError
from app.voice.providers import RealtimeProvider
from app.voice.realtime_sessions.sideband import BrokerSideband, RecordingSideband, SidebandPusher
from app.voice.realtime_sessions.tools import ToolRegistry, default_registry
from app.voice.selection import SelectionResult, select_conversation_provider
from app.voice.simulator import SimulatedRealtimeProvider

logger = get_logger("app.voice.realtime_sessions.runtime")


def default_providers(settings: Settings) -> dict[str, RealtimeProvider]:
    """The realtime candidates. The simulator needs no key and is always
    present; real adapters are added by their track behind the abstraction."""
    sim = SimulatedRealtimeProvider(credential_ttl_s=settings.voice_realtime_credential_ttl_s)
    return {sim.name: sim}


class RealtimeVoiceRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        engine: Engine | None = None,
        providers: dict[str, RealtimeProvider] | None = None,
        registry: ToolRegistry | None = None,
        sideband: SidebandPusher | None = None,
        broker: Any = None,
    ) -> None:
        self.settings = settings
        self._engine: Engine | None = engine
        self._session_factory: sessionmaker[Session] | None = (
            build_session_factory(engine) if engine is not None else None
        )
        self._providers: dict[str, RealtimeProvider] = providers or default_providers(settings)
        self._registry: ToolRegistry = registry or default_registry()
        if sideband is not None:
            self._sideband: SidebandPusher = sideband
        elif broker is not None:
            self._sideband = BrokerSideband(broker)
        else:
            self._sideband = RecordingSideband(deliver=False)

    # ------------------------------------------------------------------ db

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = build_engine(self.settings.database_url)
            self._session_factory = build_session_factory(self._engine)
        return self._engine

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        _ = self.engine
        assert self._session_factory is not None
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    # ----------------------------------------------------------- providers

    @property
    def providers(self) -> dict[str, RealtimeProvider]:
        return self._providers

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def sideband(self) -> SidebandPusher:
        return self._sideband

    def select(self, *, language: str = "tr-TR") -> tuple[RealtimeProvider, SelectionResult]:
        """Capability-driven choice among the registered candidates (spec §2)."""
        result = select_conversation_provider(
            [p.capabilities() for p in self._providers.values()],
            self.settings.voice_realtime_provider_preference,
            language=language,
            require_ephemeral_credentials=True,
        )
        return self._providers[result.selected.name], result

    def provider(self, name: str) -> RealtimeProvider | None:
        return self._providers.get(name)

    # ----------------------------------------------------------------- health

    def health_check(self) -> dict[str, Any]:
        """Which ConversationRealtime provider a session would get, and why.
        No I/O, no secrets, never raises."""
        try:
            _, result = self.select()
            selection: dict[str, Any] | None = result.to_dict()
            status = "ok"
        except VoiceError as exc:
            selection = {"error": exc.to_dict()}
            status = "fail"
        return {
            "status": status,
            "latency_ms": 0.0,
            "providers": sorted(self._providers),
            "selection": selection,
            "tools": self._registry.names(),
            "note": "the simulator is deterministic/offline; real adapters are key-gated",
        }


__all__ = ["RealtimeVoiceRuntime", "default_providers"]
