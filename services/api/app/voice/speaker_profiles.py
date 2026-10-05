"""Named voice profiles and per-conversation voice grouping (conversation-transcripts).

Two things, both on DERIVED speaker embeddings - never on audio:

1. **A person's profile** - the L2-normalised centroid of their voice in one conversation,
   sealed with the voice-profile cipher (:class:`app.voice.crypto.ProfileCipher`) before it
   reaches a row. It is only ever made once the person's consent is recorded (KVKK: a
   voiceprint is biometric data); the conversation service enforces that and the table's
   CHECK constraint enforces it again.
2. **Grouping inside one conversation** - :class:`VoiceClusterer` keeps a running centroid per
   voice in MEMORY only, so 'Konuşmacı 1' and 'Konuşmacı 2' are told apart without keeping a
   profile of anybody who has not consented. It is dropped when the conversation stops.

Cosine similarity and normalisation are the owner verifier's (``app.voice.speaker``), so a
person and the owner are compared on one scale.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.voice.crypto import ProfileCipher
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.speaker import cosine_similarity, l2_normalize

#: A probe this close to a consented person's profile is that person (the owner verifier's
#: accept bar, ``SpeakerThresholds.owner_accept``).
PERSON_MATCH_THRESHOLD = 0.75
#: A probe this close to a group's centroid joins it; below, it is a new voice.
CLUSTER_THRESHOLD = 0.70


@dataclass(frozen=True, slots=True)
class PersonProfile:
    person_id: str
    name: str
    embedding: list[float]


def seal_profile(cipher: ProfileCipher, embedding: list[float], *, model_id: str) -> bytes:
    """The normalised embedding as encrypted JSON: numbers, the dimension and the model id."""
    vector = l2_normalize([float(x) for x in embedding])
    payload = {"model_id": model_id, "embedding": vector, "dim": len(vector)}
    return cipher.encrypt(json.dumps(payload).encode("utf-8"))


def open_profile(cipher: ProfileCipher, sealed: bytes) -> list[float]:
    payload = json.loads(cipher.decrypt(sealed))
    vector = [float(x) for x in payload["embedding"]]
    if len(vector) != int(payload["dim"]):
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "sealed profile dimension mismatch")
    return vector


def best_match(
    probe: list[float], profiles: Iterable[PersonProfile], *, threshold: float
) -> tuple[PersonProfile | None, float]:
    """The closest profile at or above ``threshold``, with its score; ``(None, best)`` if none.

    A profile of another dimension (another embedding model) is skipped, never an error."""
    best: PersonProfile | None = None
    best_score = 0.0
    for profile in profiles:
        if len(profile.embedding) != len(probe):
            continue
        score = cosine_similarity(probe, profile.embedding)
        if score > best_score:
            best, best_score = profile, score
    if best is None or best_score < threshold:
        return None, best_score
    return best, best_score


@dataclass(slots=True)
class VoiceClusterer:
    """Groups the voices of ONE conversation by running centroid. Memory only."""

    threshold: float = CLUSTER_THRESHOLD
    _sums: list[list[float]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self._sums)

    def assign(self, embedding: list[float]) -> tuple[int, bool]:
        """``(group number from 1, is it a new voice)``."""
        probe = l2_normalize([float(x) for x in embedding])
        best_no, best_score = 0, -1.0
        for index, total in enumerate(self._sums):
            if len(total) != len(probe):
                continue
            score = cosine_similarity(probe, total)
            if score > best_score:
                best_no, best_score = index + 1, score
        if best_no and best_score >= self.threshold:
            total = self._sums[best_no - 1]
            self._sums[best_no - 1] = [a + b for a, b in zip(total, probe, strict=True)]
            return best_no, False
        self._sums.append(probe)
        return len(self._sums), True

    def centroid(self, number: int) -> list[float] | None:
        if not 1 <= number <= len(self._sums):
            return None
        return l2_normalize(self._sums[number - 1])


__all__ = [
    "CLUSTER_THRESHOLD",
    "PERSON_MATCH_THRESHOLD",
    "PersonProfile",
    "VoiceClusterer",
    "best_match",
    "open_profile",
    "seal_profile",
]
