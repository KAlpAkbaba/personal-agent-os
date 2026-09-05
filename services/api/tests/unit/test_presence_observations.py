"""Unit tests: app.presence.observations.

The critical privacy invariant (M18_HOLOGRAPHIC_CORE_SPEC.md §2): no image,
frame, video, base64 blob or any binary may be accepted, stored or logged.
Every test that proves a payload is REFUSED is proving that invariant; every
test that proves a normal payload is ACCEPTED is proving the boundary is not
so aggressive it breaks the one shape it must allow through.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.presence.observations import (
    Observation,
    ObservationRejected,
    is_forbidden_observation_key,
    parse_observation,
    screen_observation_payload,
)

VALID_PAYLOAD = {
    "person_present": True,
    "presence_confidence": 0.87,
    "activity_level": "low",
    "posture": "upright",
    "awake_state": "awake",
    "observed_at": "2026-09-05T08:00:00Z",
    "source": "camera",
}


def test_a_valid_payload_is_accepted() -> None:
    observation = parse_observation(VALID_PAYLOAD)
    assert isinstance(observation, Observation)
    assert observation.person_present is True
    assert observation.presence_confidence == pytest.approx(0.87)
    assert observation.activity_level == "low"
    assert observation.posture == "upright"
    assert observation.awake_state == "awake"
    assert observation.source == "camera"
    assert observation.observed_at == datetime(2026, 9, 5, 8, 0, tzinfo=UTC)


# --------------------------------------------------------- the privacy invariant


@pytest.mark.parametrize(
    "bad_key",
    [
        "image",
        "image_base64",
        "frame",
        "camera_frame",
        "frameBase64",
        "photo",
        "snapshot",
        "video_clip",
        "thumbnail_png",
        "screenshot",
        "data_url",
    ],
)
def test_a_fake_image_field_is_refused_by_key_shape(bad_key: str) -> None:
    payload = dict(VALID_PAYLOAD)
    payload[bad_key] = "irrelevant"
    with pytest.raises(ObservationRejected):
        parse_observation(payload)
    with pytest.raises(ObservationRejected):
        screen_observation_payload(payload)


def test_a_fake_base64_image_smuggled_under_one_of_the_seven_allowed_keys_is_refused() -> None:
    """The literal scenario the task calls out: a payload carrying a fake
    base64 image must be refused even when it hides under a key that IS part
    of the real schema (``source`` is a legitimate field name) — this is the
    VALUE screen catching it, independent of the key-shape screen above."""
    payload = dict(VALID_PAYLOAD)
    payload["source"] = "data:image/png;base64," + ("A" * 500)
    with pytest.raises(ObservationRejected):
        parse_observation(payload)
    with pytest.raises(ObservationRejected):
        screen_observation_payload(payload)


def test_a_long_base64_looking_string_under_an_allowed_key_is_refused() -> None:
    payload = dict(VALID_PAYLOAD)
    payload["posture"] = "A" * 400
    with pytest.raises(ObservationRejected):
        parse_observation(payload)


def test_is_forbidden_observation_key_normalizes_before_matching() -> None:
    assert is_forbidden_observation_key("frame_base64")
    assert is_forbidden_observation_key("FrameBase64")
    assert is_forbidden_observation_key("FRAME-BASE64")
    assert is_forbidden_observation_key("camera-Snapshot-jpg")
    assert not is_forbidden_observation_key("presence_confidence")
    assert not is_forbidden_observation_key("observed_at")


def test_an_unknown_non_imagery_field_is_still_refused() -> None:
    payload = dict(VALID_PAYLOAD)
    payload["extra_field"] = "hello"
    with pytest.raises(ObservationRejected):
        parse_observation(payload)


def test_a_non_dict_payload_is_refused() -> None:
    with pytest.raises(ObservationRejected):
        screen_observation_payload(["not", "a", "dict"])


# --------------------------------------------------------------- ordinary schema


def test_missing_field_is_refused() -> None:
    payload = dict(VALID_PAYLOAD)
    del payload["posture"]
    with pytest.raises(ObservationRejected):
        parse_observation(payload)


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("person_present", "yes"),
        ("presence_confidence", "high"),
        ("presence_confidence", 1.5),
        ("presence_confidence", -0.1),
        ("activity_level", "extreme"),
        ("posture", "lying_down"),
        ("awake_state", "sleeping"),
        ("source", "microphone"),
        ("observed_at", "not-a-timestamp"),
    ],
)
def test_out_of_vocabulary_or_wrong_type_values_are_refused(field: str, bad_value) -> None:
    payload = dict(VALID_PAYLOAD)
    payload[field] = bad_value
    with pytest.raises(ObservationRejected):
        parse_observation(payload)


def test_a_naive_datetime_is_treated_as_utc() -> None:
    payload = dict(VALID_PAYLOAD)
    payload["observed_at"] = datetime(2026, 9, 5, 8, 0)  # naive
    observation = parse_observation(payload)
    assert observation.observed_at.tzinfo is not None
