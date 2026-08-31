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
