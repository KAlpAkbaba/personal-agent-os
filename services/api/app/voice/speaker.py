"""Speaker verification: OWNER / NOT_OWNER / UNCERTAIN (VOICE_SPEC §10).

The classifier fuses two signals:

1. **Voice** — cosine similarity between a probe speaker-embedding and the
   enrolled owner profile (the aggregate of N enrollment sample embeddings).
2. **Device/session trust** — a boolean the caller supplies from the broker's
   trusted-device state. Voice is *never the sole secret* (constitution +
   VOICE_SPEC §10): device trust widens the accept band; an untrusted device
   requires a stronger voice match and can only ever reach UNCERTAIN on its own.

Thresholds (configurable via :class:`SpeakerThresholds`) create an explicit
UNCERTAIN band between clear-accept and clear-reject rather than forcing a hard
binary. Embeddings are plain Python float lists here (fixture vectors in tests);
no numpy dependency is pulled in for this.

Enrollment aggregates the sample embeddings into a mean, L2-normalized owner
profile vector. The persisted artifact is the *derived* profile (never raw
audio), and the service layer stores it encrypted/obfuscated behind an
``embedding_ref`` (see app/voice/service.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from app.voice.errors import VoiceError, VoiceErrorClass


class SpeakerDecision(StrEnum):
    OWNER = "OWNER"
    NOT_OWNER = "NOT_OWNER"
    UNCERTAIN = "UNCERTAIN"


# Must match the migration CHECK values (_SPEAKER_DECISIONS in 0004_voice).
DECISIONS = (SpeakerDecision.OWNER, SpeakerDecision.NOT_OWNER, SpeakerDecision.UNCERTAIN)


@dataclass(frozen=True, slots=True)
class SpeakerThresholds:
    """Similarity bands. accept >= owner_accept; reject <= not_owner_max; the gap
    between is UNCERTAIN. On an untrusted device the accept bar is raised by
    ``untrusted_penalty`` and OWNER is downgraded to UNCERTAIN (device is a
    required second factor)."""

    owner_accept: float = 0.75
    not_owner_max: float = 0.45
    untrusted_penalty: float = 0.10

    def __post_init__(self) -> None:
        if not (0.0 <= self.not_owner_max < self.owner_accept <= 1.0):
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                "require 0 <= not_owner_max < owner_accept <= 1",
            )


@dataclass(frozen=True, slots=True)
class SpeakerVerdict:
    decision: SpeakerDecision
    score: float
    reason: str
    device_trusted: bool
    effective_accept: float

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": str(self.decision),
            "score": round(self.score, 6),
            "reason": self.reason,
            "device_trusted": self.device_trusted,
            "effective_accept": round(self.effective_accept, 6),
        }


# --------------------------------------------------------------- vector helpers


def _validate_vector(vec: list[float], *, name: str = "embedding") -> list[float]:
    if not vec:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, f"{name} must be non-empty")
    try:
        out = [float(x) for x in vec]
    except (TypeError, ValueError) as exc:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, f"{name} must be numeric") from exc
    return out


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "cannot normalize a zero vector")
    return [x / norm for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    a = _validate_vector(a, name="probe")
    b = _validate_vector(b, name="profile")
    if len(a) != len(b):
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR, f"dimension mismatch: {len(a)} != {len(b)}"
        )
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return max(-1.0, min(1.0, dot / (na * nb)))


# ------------------------------------------------------------------- enrollment


@dataclass(frozen=True, slots=True)
class OwnerProfile:
    """Derived owner voice profile: the mean L2-normalized enrollment embedding
    plus non-audio metadata. This is what gets persisted (never raw audio)."""

    model_id: str
    embedding: list[float]
    sample_count: int
    dim: int

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "embedding": list(self.embedding),
            "sample_count": self.sample_count,
            "dim": self.dim,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OwnerProfile:
        return cls(
            model_id=data["model_id"],
            embedding=[float(x) for x in data["embedding"]],
            sample_count=int(data["sample_count"]),
            dim=int(data["dim"]),
        )


def enroll_owner(
    sample_embeddings: list[list[float]], *, model_id: str, min_samples: int = 3
) -> OwnerProfile:
    """Aggregate N sample embeddings into a normalized owner profile.

    VOICE_SPEC §10: enrollment should gather *varied* owner speech; we require a
    minimum number of samples so a single utterance cannot define the owner.
    """
    if len(sample_embeddings) < min_samples:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"enrollment needs >= {min_samples} samples, got {len(sample_embeddings)}",
        )
    vectors = [_validate_vector(v, name="sample") for v in sample_embeddings]
    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR, "all enrollment samples must share one dimension"
        )
    mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
    profile = l2_normalize(mean)
    return OwnerProfile(model_id=model_id, embedding=profile, sample_count=len(vectors), dim=dim)


# ----------------------------------------------------------------- verification


def verify_speaker(
    probe_embedding: list[float],
    profile: OwnerProfile,
    *,
    device_trusted: bool,
    thresholds: SpeakerThresholds | None = None,
) -> SpeakerVerdict:
    """Classify a probe against the enrolled owner profile + device trust."""
    th = thresholds or SpeakerThresholds()
    score = cosine_similarity(probe_embedding, profile.embedding)

    # Device trust is a required second factor: an untrusted device raises the
    # acceptance bar and caps the best outcome at UNCERTAIN.
    effective_accept = th.owner_accept + (0.0 if device_trusted else th.untrusted_penalty)

    if score <= th.not_owner_max:
        return SpeakerVerdict(
            SpeakerDecision.NOT_OWNER,
            score,
            f"similarity {score:.3f} <= reject threshold {th.not_owner_max:.3f}",
            device_trusted,
            effective_accept,
        )

    if score >= effective_accept:
        if not device_trusted:
            # Strong voice but unknown device -> never auto-OWNER on voice alone.
            return SpeakerVerdict(
                SpeakerDecision.UNCERTAIN,
                score,
                (
                    f"voice match {score:.3f} but device untrusted; voice is not the "
                    "sole secret (requires trusted device or step-up)"
                ),
                device_trusted,
                effective_accept,
            )
        return SpeakerVerdict(
            SpeakerDecision.OWNER,
            score,
            f"similarity {score:.3f} >= accept threshold {effective_accept:.3f} on trusted device",
            device_trusted,
            effective_accept,
        )

    return SpeakerVerdict(
        SpeakerDecision.UNCERTAIN,
        score,
        (
            f"similarity {score:.3f} in uncertain band "
            f"({th.not_owner_max:.3f}, {effective_accept:.3f})"
        ),
        device_trusted,
        effective_accept,
    )


__all__ = [
    "DECISIONS",
    "OwnerProfile",
    "SpeakerDecision",
    "SpeakerThresholds",
    "SpeakerVerdict",
    "cosine_similarity",
    "enroll_owner",
    "l2_normalize",
    "verify_speaker",
]
