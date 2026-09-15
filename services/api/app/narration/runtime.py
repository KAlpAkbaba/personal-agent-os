"""Narration runtime: DB session factory for the narration service.

Mirrors ArtifactRuntime but needs no object store (narration persists only the
cursor + pronunciation dictionary; audio bytes are ephemeral and live in the
in-process ``ChunkCache``). Lives on ``app.state.narration``; the routes create
it lazily on first use so app wiring in main.py stays a single import + one
``include_router`` line.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.logging import get_logger
from app.narration.engine import NarrationEngine
from app.narration.synth import build_synthesizer

logger = get_logger("app.narration.runtime")

#: How many chunks ahead of the one being read are synthesised (req 226). Three sentences
#: is a few seconds of speech - enough that the next one is ready when this one ends, and
#: little enough that a jump or a "dur" has not paid for a paragraph nobody will hear.
NARRATION_LOOKAHEAD = 3


class NarrationRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._narrator: NarrationEngine | None = None
        self._narrator_built = False
        self._no_voice_reason = ""

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

    # ------------------------------------------------------- B21: the voice

    @property
    def narrator(self) -> NarrationEngine | None:
        """The read-ahead synthesis pipeline for this process, or None with no voice.

        Built once and kept, because the point of `NarrationEngine` is its CACHE: the
        chunk the owner is about to hear was synthesised while they were listening to the
        previous one, and a pipeline rebuilt per request would throw that away and pay for
        every sentence twice (req 225/226). The audio itself is ephemeral by design — a
        restart re-synthesises, and the semantic cursor, which is the source of truth,
        survives in the database (VOICE_SPEC §3).

        None when this deployment has no provider that speaks. The caller then delivers
        the chunk's TEXT, which is req 233's rule reaching the third delivery path.
        """
        if self._narrator_built:
            return self._narrator
        self._narrator_built = True
        synthesizer = build_synthesizer(self.settings)
        if synthesizer is None:
            self._no_voice_reason = "no_tts_key"
            return None
        self._narrator = NarrationEngine(synthesizer, lookahead=NARRATION_LOOKAHEAD)
        logger.info("narration_voice_ready", provider=synthesizer.name)
        return self._narrator

    @property
    def no_voice_reason(self) -> str:
        """Why there is no narrator, in the vocabulary `app.voice.text_fallback` speaks."""
        _ = self.narrator
        return self._no_voice_reason


__all__ = ["NARRATION_LOOKAHEAD", "NarrationRuntime"]
