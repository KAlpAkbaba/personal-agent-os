"""Narration persistence (synchronous, one transaction per call).

Same discipline as app/artifacts/service.py: every function takes an open
Session and commits before returning, so async callers run them via
``asyncio.to_thread``. The narration *cursor* is cloud-persisted here so a
session started on device A can be resumed on device B (VOICE_SPEC §3,
ACCEPTANCE_TESTS M4 "cross-device narration cursor persists").
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.narration.models import (
    NARRATION_STATE_IDLE,
    NarrationSession,
    PronunciationEntry,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


# ----------------------------------------------------------- narration sessions


def create_session(
    session: Session,
    *,
    artifact_id: uuid.UUID,
    artifact_version: int = 1,
    device_id: uuid.UUID | None = None,
    voice_profile_id: uuid.UUID | None = None,
    speed: float = 1.0,
    initial_cursor: dict[str, Any] | None = None,
) -> NarrationSession:
    row = NarrationSession(
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        device_id=device_id,
        voice_profile_id=voice_profile_id,
        speed=speed,
        state=NARRATION_STATE_IDLE,
        semantic_cursor_json=initial_cursor or {},
    )
    session.add(row)
    session.commit()
    return row


def get_session(session: Session, session_id: uuid.UUID) -> NarrationSession | None:
    return session.get(NarrationSession, session_id)


def list_sessions_for_artifact(
    session: Session, artifact_id: uuid.UUID, *, limit: int = 50
) -> list[NarrationSession]:
    return list(
        session.execute(
            select(NarrationSession)
            .where(NarrationSession.artifact_id == artifact_id)
            .order_by(NarrationSession.updated_at.desc())
            .limit(limit)
        ).scalars()
    )


def update_cursor(
    session: Session,
    session_id: uuid.UUID,
    *,
    cursor: dict[str, Any] | None = None,
    playback_seconds: float | None = None,
    state: str | None = None,
    speed: float | None = None,
    device_id: uuid.UUID | None = None,
) -> NarrationSession | None:
    """Patch the cursor / playback metadata. This is the cross-device write:
    device A persists the semantic cursor; device B reads it back verbatim."""
    row = session.get(NarrationSession, session_id)
    if row is None:
        return None
    if cursor is not None:
        row.semantic_cursor_json = cursor
    if playback_seconds is not None:
        row.playback_seconds = playback_seconds
    if state is not None:
        row.state = state
    if speed is not None:
        row.speed = speed
    if device_id is not None:
        row.device_id = device_id
    row.updated_at = utcnow()
    session.commit()
    return row


# ------------------------------------------------------- pronunciation dictionary


def upsert_pronunciation(
    session: Session,
    *,
    token: str,
    spoken_form: str,
    context: str | None = None,
    explicit: bool = True,
    confidence: float | None = None,
    provider_payload: dict[str, Any] | None = None,
) -> PronunciationEntry:
    """Insert or update a pronunciation entry, keyed on (token, context).

    Explicit owner corrections default to the highest confidence (1.0) unless a
    caller overrides it; inferred entries should pass ``explicit=False`` and a
    lower confidence with evidence in ``provider_payload`` (VOICE_SPEC §5).
    """
    if confidence is None:
        confidence = 1.0 if explicit else 0.5
    existing = session.execute(
        select(PronunciationEntry).where(
            PronunciationEntry.token == token,
            PronunciationEntry.context.is_(context) if context is None
            else PronunciationEntry.context == context,
        )
    ).scalar_one_or_none()
    if existing is not None:
        # An explicit correction always wins; an inferred write never downgrades
        # an existing explicit entry.
        if existing.explicit and not explicit:
            return existing
        existing.spoken_form = spoken_form
        existing.explicit = explicit
        existing.confidence = confidence
        existing.provider_payload_json = provider_payload
        existing.updated_at = utcnow()
        session.commit()
        return existing
    entry = PronunciationEntry(
        token=token,
        spoken_form=spoken_form,
        context=context,
        explicit=explicit,
        confidence=confidence,
        provider_payload_json=provider_payload,
    )
    session.add(entry)
    session.commit()
    return entry


def get_pronunciation(session: Session, entry_id: uuid.UUID) -> PronunciationEntry | None:
    return session.get(PronunciationEntry, entry_id)


def list_pronunciations(session: Session, *, limit: int = 500) -> list[PronunciationEntry]:
    return list(
        session.execute(
            select(PronunciationEntry)
            .order_by(PronunciationEntry.token, PronunciationEntry.context)
            .limit(limit)
        ).scalars()
    )


def delete_pronunciation(session: Session, entry_id: uuid.UUID) -> bool:
    row = session.get(PronunciationEntry, entry_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def pronunciation_map(session: Session, *, context: str | None = None) -> dict[str, str]:
    """The token -> spoken_form dict fed to the normalizer. Higher-confidence and
    explicit entries win on collision."""
    entries = list_pronunciations(session)
    # Sort so the winning entry is applied last: explicit + higher confidence.
    entries.sort(key=lambda e: (e.explicit, e.confidence))
    out: dict[str, str] = {}
    for e in entries:
        if context is not None and e.context not in (None, context):
            continue
        out[e.token] = e.spoken_form
    return out


__all__ = [
    "create_session",
    "get_session",
    "list_sessions_for_artifact",
    "update_cursor",
    "upsert_pronunciation",
    "get_pronunciation",
    "list_pronunciations",
    "delete_pronunciation",
    "pronunciation_map",
]
