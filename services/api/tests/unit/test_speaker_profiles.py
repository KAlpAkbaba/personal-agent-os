"""Named voice profiles and the per-conversation voice grouping (conversation-transcripts).

A person's profile is a DERIVED embedding, sealed with the voice-profile cipher - never audio,
never a plaintext vector. Grouping inside one conversation keeps running centroids in memory
only; two different fake voices land in two groups, the same voice comes back to its own group.
"""

from __future__ import annotations

import json

import pytest

from app.voice.crypto import ProfileCipher
from app.voice.errors import VoiceError
from app.voice.speaker_profiles import (
    CLUSTER_THRESHOLD,
    PERSON_MATCH_THRESHOLD,
    PersonProfile,
    VoiceClusterer,
    best_match,
    open_profile,
    seal_profile,
)

CIPHER = ProfileCipher("test-secret")

# Two fake voices far apart, and a little jitter around each.
VOICE_A = [1.0, 0.1, 0.0, 0.0]
VOICE_A2 = [0.97, 0.15, 0.02, 0.0]
VOICE_B = [0.0, 0.1, 1.0, 0.0]
VOICE_B2 = [0.02, 0.0, 0.96, 0.1]


def test_seal_is_encrypted_and_opens_back_to_the_same_vector() -> None:
    sealed = seal_profile(CIPHER, VOICE_A, model_id="fake-ecapa")
    assert isinstance(sealed, bytes)
    # Not a plaintext vector: no JSON digits of the embedding in the sealed bytes.
    assert b"embedding" not in sealed and b"0.1" not in sealed
    opened = open_profile(CIPHER, sealed)
    assert opened == pytest.approx([x / sum(v * v for v in VOICE_A) ** 0.5 for x in VOICE_A])


def test_a_profile_sealed_under_another_secret_does_not_open() -> None:
    sealed = seal_profile(ProfileCipher("other"), VOICE_A, model_id="m")
    with pytest.raises(VoiceError):
        open_profile(CIPHER, sealed)


def test_the_sealed_payload_holds_only_numbers_and_the_model_id() -> None:
    sealed = seal_profile(CIPHER, VOICE_B, model_id="fake-ecapa")
    payload = json.loads(CIPHER.decrypt(sealed))
    assert set(payload) == {"model_id", "embedding", "dim"}
    assert all(isinstance(x, float) for x in payload["embedding"])


def test_two_fake_voices_cluster_apart_and_come_back_to_their_own_group() -> None:
    clusterer = VoiceClusterer()
    assert clusterer.assign(VOICE_A) == (1, True)
    assert clusterer.assign(VOICE_B) == (2, True)
    assert clusterer.assign(VOICE_A2) == (1, False)
    assert clusterer.assign(VOICE_B2) == (2, False)
    assert clusterer.count == 2
    centroid = clusterer.centroid(1)
    assert centroid is not None
    assert best_match(centroid, [PersonProfile("a", "A", VOICE_A)], threshold=0.9)[0] is not None


def test_the_cluster_threshold_decides_a_new_voice() -> None:
    # VOICE_A2 is ~0.998 from VOICE_A: one group at 0.99, two at 0.999.
    loose = VoiceClusterer(threshold=0.99)
    loose.assign(VOICE_A)
    assert loose.assign(VOICE_A2) == (1, False)
    clusterer = VoiceClusterer(threshold=0.999)
    clusterer.assign(VOICE_A)
    assert clusterer.assign(VOICE_A2) == (2, True)


def test_best_match_names_a_person_above_the_threshold_and_nobody_below() -> None:
    people = [PersonProfile("p1", "Ahmet", VOICE_A), PersonProfile("p2", "Ayşe", VOICE_B)]
    found, score = best_match(VOICE_A2, people, threshold=PERSON_MATCH_THRESHOLD)
    assert found is not None and found.name == "Ahmet" and score > PERSON_MATCH_THRESHOLD
    # A voice between the two is nobody's.
    middle = [0.6, 0.0, 0.6, 0.5]
    assert best_match(middle, people, threshold=PERSON_MATCH_THRESHOLD)[0] is None
    assert best_match(VOICE_A2, [], threshold=PERSON_MATCH_THRESHOLD) == (None, 0.0)


def test_thresholds_are_the_documented_ones() -> None:
    assert PERSON_MATCH_THRESHOLD == 0.75
    assert CLUSTER_THRESHOLD == 0.70
