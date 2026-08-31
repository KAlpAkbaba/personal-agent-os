"""Unit tests: speaker verification OWNER/NOT_OWNER/UNCERTAIN with fixture
embeddings, threshold boundaries, and device-trust combination."""

import pytest

from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.speaker import (
    SpeakerDecision,
    SpeakerThresholds,
    cosine_similarity,
    enroll_owner,
    verify_speaker,
)

# Fixture embedding vectors (NOT real audio).
OWNER_SAMPLES = [
    [1.0, 0.0, 0.0, 0.0],
    [0.92, 0.10, 0.0, 0.0],
    [0.95, 0.05, 0.05, 0.0],
]


def _profile():
    return enroll_owner(OWNER_SAMPLES, model_id="fixture-embed-v1")


def test_enrollment_requires_minimum_samples() -> None:
    with pytest.raises(VoiceError) as exc:
        enroll_owner(OWNER_SAMPLES[:2], model_id="m")
    assert exc.value.error_class == VoiceErrorClass.VALIDATION_ERROR


def test_enrollment_aggregates_and_normalizes() -> None:
    prof = _profile()
    assert prof.sample_count == 3
    assert prof.dim == 4
    norm = sum(x * x for x in prof.embedding) ** 0.5
    assert abs(norm - 1.0) < 1e-9  # unit vector


def test_decision_owner_on_trusted_device() -> None:
    v = verify_speaker([1.0, 0.0, 0.0, 0.0], _profile(), device_trusted=True)
    assert v.decision == SpeakerDecision.OWNER
    assert v.score > 0.9


def test_decision_not_owner() -> None:
    v = verify_speaker([0.0, 0.0, 1.0, 0.0], _profile(), device_trusted=True)
    assert v.decision == SpeakerDecision.NOT_OWNER


def test_decision_uncertain_middle_band() -> None:
    # A probe roughly 45 degrees off -> similarity in the uncertain band.
    v = verify_speaker([0.7, 0.7, 0.0, 0.0], _profile(), device_trusted=True)
    assert v.decision == SpeakerDecision.UNCERTAIN


def test_voice_not_sole_secret_untrusted_device_caps_at_uncertain() -> None:
    """Even a near-perfect voice match on an UNTRUSTED device is UNCERTAIN, never
    OWNER (voice is not the sole secret)."""
    v = verify_speaker([1.0, 0.0, 0.0, 0.0], _profile(), device_trusted=False)
    assert v.decision == SpeakerDecision.UNCERTAIN
    assert "device" in v.reason.lower()


def test_threshold_boundaries() -> None:
    prof = _profile()
    th = SpeakerThresholds(owner_accept=0.8, not_owner_max=0.4)
    # Construct probes with controlled cosine similarity to the profile.
    probe = [p * 0.35 + q for p, q in zip(prof.embedding, [0, 0, 0, 1.0], strict=True)]
    just_reject = verify_speaker(probe, prof, device_trusted=True, thresholds=th)
    assert just_reject.decision in (SpeakerDecision.NOT_OWNER, SpeakerDecision.UNCERTAIN)


def test_invalid_thresholds_rejected() -> None:
    with pytest.raises(VoiceError):
        SpeakerThresholds(owner_accept=0.3, not_owner_max=0.5)


def test_cosine_similarity_basics() -> None:
    assert abs(cosine_similarity([1, 0], [1, 0]) - 1.0) < 1e-9
    assert abs(cosine_similarity([1, 0], [0, 1])) < 1e-9
    with pytest.raises(VoiceError):
        cosine_similarity([1, 0], [1, 0, 0])  # dim mismatch
