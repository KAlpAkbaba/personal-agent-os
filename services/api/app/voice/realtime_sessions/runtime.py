"""Realtime voice runtime: DB session factory + realtime provider registry +
tool registry + sideband pusher. Lives on ``app.state.voice_realtime``.

Providers are ``ConversationRealtime`` candidates only (the M4 TTS/STT
providers are a different subsystem, constitution Voice rule). The registry
holds the real OpenAI Realtime adapter when its key is provisioned, and the
deterministic simulator in ``environment=dev`` (or behind an explicit setting);
selection between them is by capability, never by being named in code outside
the adapter layer. Outside dev the simulator is not a candidate at all
(ADR-0038): a production session is answered by a real provider or refused
with the reason, never by the gate.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Iterator
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger
from app.voice.errors import VoiceError
from app.voice.providers import TRANSPORT_SIMULATED, ProviderCapabilities, RealtimeProvider
from app.voice.providers_openai_realtime import (
    OPENAI_REALTIME_PROVIDER_NAME,
    OpenAIRealtimeProvider,
)
from app.voice.realtime_sessions.contract_version import CONTRACT_VERSION
from app.voice.realtime_sessions.sideband import BrokerSideband, RecordingSideband, SidebandPusher
from app.voice.realtime_sessions.tools import ToolRegistry, default_registry
from app.voice.selection import SelectionResult, select_conversation_provider
from app.voice.simulator import SIMULATOR_PROVIDER_NAME, SimulatedRealtimeProvider

logger = get_logger("app.voice.realtime_sessions.runtime")

#: Rejection reason reported for a simulated-transport candidate outside dev.
REJECT_SIMULATED_OUTSIDE_DEV = "simulated_transport_disabled_outside_dev"


def simulator_allowed(settings: Settings) -> bool:
    """The simulator is a gate (ADR-0036 §2): a candidate in dev, or only when
    ``PAGENTOS_VOICE_REALTIME_SIMULATOR_ENABLED`` says so explicitly."""
    return settings.environment == "dev" or bool(settings.voice_realtime_simulator_enabled)


def default_providers(settings: Settings) -> dict[str, RealtimeProvider]:
    """The realtime candidates that can actually serve a session right now:
    the real adapter only when its key is provisioned (otherwise minting would
    fail on every session), the simulator only where it is allowed."""
    providers: dict[str, RealtimeProvider] = {}
    if settings.voice_openai_api_key:
        openai = OpenAIRealtimeProvider.from_settings(settings)
        providers[openai.name] = openai
    if simulator_allowed(settings):
        sim = SimulatedRealtimeProvider(credential_ttl_s=settings.voice_realtime_credential_ttl_s)
        providers[sim.name] = sim
    return providers


def inactive_candidates(settings: Settings) -> dict[str, str]:
    """Known adapters that are NOT registered, with the reason (health check
    output; no secrets). This is how an operator learns *why* production has
    no ConversationRealtime provider instead of "it wasn't picked"."""
    out: dict[str, str] = {}
    if not settings.voice_openai_api_key:
        out[OPENAI_REALTIME_PROVIDER_NAME] = (
            "provider_auth_missing (owner action: set PAGENTOS_VOICE_OPENAI_API_KEY)"
        )
    if not simulator_allowed(settings):
        out[SIMULATOR_PROVIDER_NAME] = (
            f"disabled outside environment=dev (environment={settings.environment!r}; "
            "PAGENTOS_VOICE_REALTIME_SIMULATOR_ENABLED=true overrides)"
        )
    return out


def _is_simulated_only(caps: ProviderCapabilities) -> bool:
    return bool(caps.transports) and all(t == TRANSPORT_SIMULATED for t in caps.transports)


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
        self._providers: dict[str, RealtimeProvider] = (
            providers if providers is not None else default_providers(settings)
        )
        self._inactive: dict[str, str] = inactive_candidates(settings)
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

    @property
    def inactive(self) -> dict[str, str]:
        return dict(self._inactive)

    def select(self, *, language: str = "tr-TR") -> tuple[RealtimeProvider, SelectionResult]:
        """Capability-driven choice among the registered candidates (spec §2).

        Defence in depth for ADR-0038: even if a simulated-only provider was
        registered explicitly, it is barred outside dev and reported as
        rejected with the reason, so a real adapter is never outranked by —
        or silently replaced with — the gate in production."""
        allow_sim = simulator_allowed(self.settings)
        candidates: list[ProviderCapabilities] = []
        barred: dict[str, tuple[str, ...]] = {}
        for provider in self._providers.values():
            caps = provider.capabilities()
            if not allow_sim and _is_simulated_only(caps):
                barred[caps.name] = (REJECT_SIMULATED_OUTSIDE_DEV,)
                continue
            candidates.append(caps)
        try:
            result = select_conversation_provider(
                candidates,
                self.settings.voice_realtime_provider_preference,
                language=language,
                require_ephemeral_credentials=True,
            )
        except VoiceError as exc:
            rejected = dict(exc.details.get("rejected") or {})
            rejected.update({name: list(why) for name, why in barred.items()})
            exc.details["rejected"] = rejected
            exc.details["inactive"] = dict(self._inactive)
            raise
        if barred:
            result = dataclasses.replace(result, rejected={**result.rejected, **barred})
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
            "environment": self.settings.environment,
            # The served wire-contract version: the release qualification refuses an
            # unexpectedly old one (ADR-0045), and a client can tell v1 from v2 here.
            "contract_version": CONTRACT_VERSION,
            "providers": sorted(self._providers),
            "inactive": dict(self._inactive),
            "selection": selection,
            "tools": self._registry.names(),
            "note": (
                "real adapters are key-gated; the simulator is a dev-only gate "
                "(PAGENTOS_VOICE_REALTIME_SIMULATOR_ENABLED overrides)"
            ),
        }


__all__ = ["RealtimeVoiceRuntime", "default_providers"]
