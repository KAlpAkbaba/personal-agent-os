"""Self-healing runtime: DB session factory + backend selection + paths.

Lives on app.state.selfhealing (mirrors MemoryRuntime/VoiceRuntime). Tests may
inject an ``engine`` (SQLite StaticPool).

Configuration is read from environment variables directly (documented here, no
app.config change — config.py is outside this module's ownership):

    PAGENTOS_SELFHEALING_WORKSPACE_ROOT   root under which supervised release
                                          workspaces must live (path-restricts
                                          pipeline/deploy inputs);
                                          default: <repo>/staging/workspaces
    PAGENTOS_SELFHEALING_SUPERVISOR       supervisor.py path override
    PAGENTOS_SELFHEALING_TARGET_SERVICE   demo target service.py path override
    PAGENTOS_SELFHEALING_BACKEND          "deterministic" (default) | "anthropic" | "claude"
    PAGENTOS_SELFHEALING_CLAUDE_CLI       Claude CLI path; without it the
                                          claude backend stays inert (typed
                                          backend_not_configured error).

The "anthropic" backend is the one that actually writes patches (ADR-0107). It needs no
path and no CLI: it speaks the Messages API over HTTP and takes its key from
``Settings.anthropic_api_key`` (PAGENTOS_ANTHROPIC_API_KEY). Without that key it is inert
in exactly the same way, and says which owner action installs one. It is deliberately NOT
the default: turning on a loop that writes code is an owner decision, not a deployment
side effect.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger
from app.selfhealing.anthropic_backend import AnthropicCodingBackend
from app.selfhealing.backends import (
    ClaudeCodingBackend,
    CodingBackend,
    DeterministicCodingBackend,
)
from app.selfhealing.service import SelfHealingService

logger = get_logger("app.selfhealing.runtime")

_REPO_ROOT = Path(__file__).resolve().parents[4]


class SelfHealingRuntime:
    def __init__(self, settings: Settings, *, engine: Engine | None = None) -> None:
        self.settings = settings
        self._engine: Engine | None = engine
        self._session_factory: sessionmaker[Session] | None = (
            build_session_factory(engine) if engine is not None else None
        )
        self._service: SelfHealingService | None = None

        self.workspace_root = Path(
            os.environ.get(
                "PAGENTOS_SELFHEALING_WORKSPACE_ROOT",
                str(_REPO_ROOT / "staging" / "workspaces"),
            )
        ).resolve()
        self.supervisor_script = Path(
            os.environ.get(
                "PAGENTOS_SELFHEALING_SUPERVISOR",
                str(_REPO_ROOT / "services" / "recovery-supervisor" / "supervisor.py"),
            )
        )
        self.target_service_script = Path(
            os.environ.get(
                "PAGENTOS_SELFHEALING_TARGET_SERVICE",
                str(_REPO_ROOT / "staging" / "target-service" / "service.py"),
            )
        )
        self._backend_name = os.environ.get("PAGENTOS_SELFHEALING_BACKEND", "deterministic")
        self._claude_cli = os.environ.get("PAGENTOS_SELFHEALING_CLAUDE_CLI", "")

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

    @property
    def service(self) -> SelfHealingService:
        if self._service is None:
            self._service = SelfHealingService(self.session)
        return self._service

    @property
    def backend(self) -> CodingBackend:
        if self._backend_name == "anthropic":
            # Inert without PAGENTOS_ANTHROPIC_API_KEY (typed error naming the fix).
            return AnthropicCodingBackend(self.settings.anthropic_api_key)
        if self._backend_name == "claude":
            # Inert without PAGENTOS_SELFHEALING_CLAUDE_CLI (typed error on use).
            return ClaudeCodingBackend(cli_path=self._claude_cli)
        return DeterministicCodingBackend()

    def health_check(self) -> dict[str, object]:
        """Backend identity + supervisor availability for /v1/system/health.

        No I/O beyond two path stats: DB reachability is covered by the 'db'
        check. The supervisor itself runs OUT of process by design; this only
        reports whether its script is where we expect it.
        """
        return {
            "status": "ok",
            "latency_ms": 0.0,  # no I/O; keeps the uniform check shape
            "coding_backend": self.backend.name,
            "supervisor_script_present": self.supervisor_script.is_file(),
            "target_service_present": self.target_service_script.is_file(),
        }


__all__ = ["SelfHealingRuntime"]
