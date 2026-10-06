"""Unit tests: voice preferences + explicit-overrides-inferred logic."""

import pytest

from app.voice.errors import VoiceError
from app.voice.preferences import VoicePreferences


def test_defaults_match_spec() -> None:
    p = VoicePreferences()
    assert p.locale == "tr-TR"
    assert p.executive_summary_first is True
    assert p.narration_speed == 1.0
    assert p.read_urls is False
    assert p.barge_in is True


def test_owner_update_marks_explicit() -> None:
    p = VoicePreferences()
    p.apply_update({"read_urls": True}, source="owner")
    assert p.read_urls is True
    assert "read_urls" in p.owner_set


def test_explicit_owner_value_overrides_inferred() -> None:
    p = VoicePreferences()
    p.apply_update({"read_urls": True}, source="owner")
    p.apply_update({"read_urls": False}, source="inferred")  # must be ignored
    assert p.read_urls is True


def test_inferred_applies_when_not_owner_set() -> None:
    p = VoicePreferences()
    p.apply_update({"narration_speed": 1.25}, source="inferred")
    assert p.narration_speed == 1.25
    assert "narration_speed" not in p.owner_set


def test_roundtrip_narration_settings() -> None:
    p = VoicePreferences()
    p.apply_update({"read_footnotes": True, "narration_speed": 1.5}, source="owner")
    settings = p.to_narration_settings()
    assert "locale" not in settings
    restored = VoicePreferences.from_row(locale="tr-TR", narration_settings=settings)
    assert restored.read_footnotes is True
    assert restored.narration_speed == 1.5
    assert "read_footnotes" in restored.owner_set


def test_validation_rejects_bad_speed_and_locale() -> None:
    with pytest.raises(VoiceError):
        VoicePreferences(narration_speed=9.0).validate()
    with pytest.raises(VoiceError):
        VoicePreferences(locale="turkish").validate()


def test_unknown_field_rejected() -> None:
    with pytest.raises(VoiceError):
        VoicePreferences().apply_update({"nope": 1}, source="owner")


# ------------------------------------------------------------ humor (persona-dry-wit)


def test_humor_defaults_to_dry() -> None:
    p = VoicePreferences()
    assert p.humor == "dry"
    assert p.to_narration_settings()["humor"] == "dry"


def test_owner_turns_humor_off_and_it_is_marked_explicit() -> None:
    p = VoicePreferences()
    p.apply_update({"humor": "off"}, source="owner")
    assert p.humor == "off"
    assert "humor" in p.owner_set


def test_inferred_humor_never_overrides_the_owners_off() -> None:
    p = VoicePreferences()
    p.apply_update({"humor": "off"}, source="owner")
    p.apply_update({"humor": "dry"}, source="inferred")
    assert p.humor == "off"


@pytest.mark.parametrize("value", ["kahkaha", "", None, 1, "DRY"])
def test_an_unknown_humor_value_falls_back_to_dry(value) -> None:
    assert VoicePreferences(humor=value).humor == "dry"
    p = VoicePreferences()
    p.apply_update({"humor": value}, source="owner")
    assert p.humor == "dry"
    restored = VoicePreferences.from_row(locale="tr-TR", narration_settings={"humor": value})
    assert restored.humor == "dry"


def test_humor_round_trips_through_the_row() -> None:
    p = VoicePreferences()
    p.apply_update({"humor": "off"}, source="owner")
    restored = VoicePreferences.from_row(
        locale="tr-TR", narration_settings=p.to_narration_settings()
    )
    assert restored.humor == "off"
    assert "humor" in restored.owner_set


def test_a_row_written_before_humor_existed_reads_as_dry() -> None:
    restored = VoicePreferences.from_row(
        locale="tr-TR", narration_settings={"read_urls": True, "owner_set": ["read_urls"]}
    )
    assert restored.humor == "dry"


def test_the_preferences_route_accepts_the_humor_switch() -> None:
    """The owner's switch must be writable through PATCH /voice/preferences (inspector, 2026-10-06).

    ``PreferencesUpdate`` forbids extra keys, so without a ``humor`` field the route answers
    422 and the dataclass switch is unreachable for the owner.
    """
    from pydantic import ValidationError

    from app.voice.routes import PreferencesUpdate

    body = PreferencesUpdate(humor="off")
    assert body.model_dump()["humor"] == "off"
    assert PreferencesUpdate(humor="dry").model_dump()["humor"] == "dry"
    assert PreferencesUpdate().model_dump()["humor"] is None
    with pytest.raises(ValidationError):
        PreferencesUpdate(humor="kahkaha")
