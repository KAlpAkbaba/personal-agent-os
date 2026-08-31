"""Voice persistence (synchronous, one transaction per call).

Same discipline as app/artifacts/service.py: each function takes an open Session
and commits before returning, so async callers run them via
``asyncio.to_thread``. Three concerns:

- **Preferences** round-trip to ``voice_profiles`` (locale + narration settings +
  tts provider preference).
- **Speaker profiles** persist an *encrypted derived* owner embedding to the
  object store and keep only the object key (``embedding_ref``) + non-audio
  metadata in ``speaker_profiles``. Verification loads + decrypts it.
- **Benchmark reports** are written to the object store (JSON + Markdown) and
  retrieved as the "last generated report".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.object_store import S3ObjectStore
from app.voice.benchmark import BenchmarkReport, report_to_markdown
from app.voice.crypto import ProfileCipher
from app.voice.models import SpeakerProfile, VoiceProfile
from app.voice.preferences import VoicePreferences
from app.voice.speaker import (
    OwnerProfile,
    SpeakerThresholds,
    SpeakerVerdict,
    enroll_owner,
    verify_speaker,
)

OWNER_LABEL = "owner"


def utcnow() -> datetime:
    return datetime.now(UTC)


# ----------------------------------------------------------------- preferences


def get_or_create_voice_profile(session: Session, *, label: str = OWNER_LABEL) -> VoiceProfile:
    existing = session.execute(
        select(VoiceProfile).where(VoiceProfile.label == label)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    profile = VoiceProfile(label=label)
    session.add(profile)
    session.commit()
    return profile


def load_preferences(session: Session, *, label: str = OWNER_LABEL) -> VoicePreferences:
    profile = get_or_create_voice_profile(session, label=label)
    return VoicePreferences.from_row(
        locale=profile.locale, narration_settings=profile.narration_settings_json
    )


def save_preferences(
    session: Session, prefs: VoicePreferences, *, label: str = OWNER_LABEL
) -> VoicePreferences:
    prefs.validate()
    profile = get_or_create_voice_profile(session, label=label)
    profile.locale = prefs.locale
    profile.narration_settings_json = prefs.to_narration_settings()
    profile.updated_at = utcnow()
    session.commit()
    return prefs


def update_preferences(
    session: Session, updates: dict, *, source: str = "owner", label: str = OWNER_LABEL
) -> VoicePreferences:
    prefs = load_preferences(session, label=label)
    prefs.apply_update(updates, source=source)
    return save_preferences(session, prefs, label=label)


def set_tts_provider_preference(
    session: Session, preference: dict, *, label: str = OWNER_LABEL
) -> VoiceProfile:
    profile = get_or_create_voice_profile(session, label=label)
    profile.tts_provider_preference_json = dict(preference)
    profile.updated_at = utcnow()
    session.commit()
    return profile


# ------------------------------------------------------------- speaker profiles


def _speaker_object_key(prefix: str, label: str) -> str:
    return f"{prefix.rstrip('/')}/{label}.profile.enc"


def enroll_and_store_owner(
    session: Session,
    store: S3ObjectStore,
    cipher: ProfileCipher,
    *,
    sample_embeddings: list[list[float]],
    model_id: str,
    prefix: str,
    min_samples: int = 3,
    label: str = OWNER_LABEL,
) -> SpeakerProfile:
    """Aggregate enrollment samples -> derived profile -> encrypt -> object store,
    then upsert the speaker_profiles row. Raw audio is never accepted or stored."""
    profile = enroll_owner(sample_embeddings, model_id=model_id, min_samples=min_samples)
    key = _speaker_object_key(prefix, label)
    ciphertext = cipher.encrypt(json.dumps(profile.to_dict()).encode("utf-8"))
    store.put(key, ciphertext, content_type="application/octet-stream")

    row = session.execute(
        select(SpeakerProfile).where(SpeakerProfile.label == label)
    ).scalar_one_or_none()
    metadata = {
        "sample_count": profile.sample_count,
        "dim": profile.dim,
        "enrolled_at": utcnow().isoformat().replace("+00:00", "Z"),
    }
    if row is None:
        row = SpeakerProfile(
            label=label, model_id=model_id, embedding_ref=key,
            enrollment_metadata_json=metadata,
        )
        session.add(row)
    else:
        row.model_id = model_id
        row.embedding_ref = key
        row.enrollment_metadata_json = metadata
        row.updated_at = utcnow()
    session.commit()
    return row


def load_owner_profile(
    session: Session, store: S3ObjectStore, cipher: ProfileCipher, *, label: str = OWNER_LABEL
) -> OwnerProfile | None:
    row = session.execute(
        select(SpeakerProfile).where(SpeakerProfile.label == label)
    ).scalar_one_or_none()
    if row is None or not row.embedding_ref:
        return None
    ciphertext = store.get(row.embedding_ref)
    data = json.loads(cipher.decrypt(ciphertext).decode("utf-8"))
    return OwnerProfile.from_dict(data)


def verify_owner(
    session: Session,
    store: S3ObjectStore,
    cipher: ProfileCipher,
    *,
    probe_embedding: list[float],
    device_trusted: bool,
    thresholds: SpeakerThresholds | None = None,
    label: str = OWNER_LABEL,
) -> SpeakerVerdict | None:
    """Load the enrolled owner profile and classify a probe. None if no profile
    has been enrolled yet."""
    profile = load_owner_profile(session, store, cipher, label=label)
    if profile is None:
        return None
    return verify_speaker(
        probe_embedding, profile, device_trusted=device_trusted, thresholds=thresholds
    )


def get_speaker_profile_row(
    session: Session, *, label: str = OWNER_LABEL
) -> SpeakerProfile | None:
    return session.execute(
        select(SpeakerProfile).where(SpeakerProfile.label == label)
    ).scalar_one_or_none()


# ------------------------------------------------------------- benchmark reports


def _report_keys(prefix: str, kind: str) -> tuple[str, str]:
    base = f"{prefix.rstrip('/')}/{kind}/latest"
    return f"{base}.json", f"{base}.md"


def store_benchmark_report(
    store: S3ObjectStore, report: BenchmarkReport, *, prefix: str
) -> dict[str, str]:
    json_key, md_key = _report_keys(prefix, report.kind)
    store.put(json_key, json.dumps(report.to_dict(), ensure_ascii=False).encode("utf-8"),
              content_type="application/json")
    store.put(md_key, report_to_markdown(report).encode("utf-8"),
              content_type="text/markdown; charset=utf-8")
    return {"json_key": json_key, "markdown_key": md_key}


def load_benchmark_report(
    store: S3ObjectStore, *, kind: str, prefix: str
) -> dict | None:
    json_key, _ = _report_keys(prefix, kind)
    try:
        raw = store.get(json_key)
    except KeyError:
        return None
    return json.loads(raw.decode("utf-8"))


def load_benchmark_markdown(store: S3ObjectStore, *, kind: str, prefix: str) -> str | None:
    _, md_key = _report_keys(prefix, kind)
    try:
        return store.get(md_key).decode("utf-8")
    except KeyError:
        return None


__all__ = [
    "OWNER_LABEL",
    "enroll_and_store_owner",
    "get_or_create_voice_profile",
    "get_speaker_profile_row",
    "load_benchmark_markdown",
    "load_benchmark_report",
    "load_owner_profile",
    "load_preferences",
    "save_preferences",
    "set_tts_provider_preference",
    "store_benchmark_report",
    "update_preferences",
    "verify_owner",
]
