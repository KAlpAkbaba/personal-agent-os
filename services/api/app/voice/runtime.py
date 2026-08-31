"""Voice runtime: DB session factory + ObjectStore + profile cipher.

Lives on app.state.voice (mirrors ArtifactRuntime/BrokerRuntime). The object
store holds encrypted speaker profiles and generated benchmark reports; Postgres
holds voice_profiles + speaker_profiles rows.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger
from app.object_store import S3ObjectStore
from app.voice.crypto import ProfileCipher

logger = get_logger("app.voice.runtime")


class VoiceRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._store: S3ObjectStore | None = None
        self._cipher: ProfileCipher | None = None

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
    def store(self) -> S3ObjectStore:
        if self._store is None:
            self._store = S3ObjectStore.from_settings(self.settings)
        return self._store

    @property
    def cipher(self) -> ProfileCipher:
        if self._cipher is None:
            self._cipher = ProfileCipher(self.settings.voice_profile_secret)
        return self._cipher

    def health_check(self) -> dict[str, object]:
        """Report which real voice providers are key-activated (owner action).

        Never raises. Absence of keys is the expected default (real providers are
        inert), so this is 'ok' with a per-subsystem activation map rather than a
        failure.
        """
        s = self.settings
        activated = {
            "elevenlabs_tts": bool(s.voice_elevenlabs_api_key),
            "azure_speech": bool(s.voice_azure_speech_key),
            "openai": bool(s.voice_openai_api_key),
        }
        return {
            "status": "ok",
            "latency_ms": 0.0,  # no I/O; keeps the uniform check shape (status+latency)
            "real_providers_activated": activated,
            "note": "empty keys are expected; fakes are deterministic/offline",
        }


__all__ = ["VoiceRuntime"]
