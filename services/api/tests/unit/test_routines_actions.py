"""Unit tests: app.routines.actions.

Covers the closed action vocabulary, the alarm wake-volume ramp invariant (must ramp, never
jolt), and — the task brief's own acceptance case — that a media action preserves the
owner's exact requested item.
"""

from __future__ import annotations

import uuid

import pytest

from app.routines.actions import (
    InvalidActionDescriptor,
    NoopDispatcher,
    validate_action,
    validate_alarm,
    validate_media_playback,
)


def test_validate_action_rejects_unknown_kind() -> None:
    with pytest.raises(InvalidActionDescriptor):
        validate_action({"kind": "make_coffee", "detail": {}})


def test_validate_voice_briefing_requires_text() -> None:
    with pytest.raises(InvalidActionDescriptor):
        validate_action({"kind": "voice_briefing", "detail": {}})
    normalized = validate_action({"kind": "voice_briefing", "detail": {"text": "Günaydın"}})
    assert normalized["detail"]["text"] == "Günaydın"


# --------------------------------------------------------------------------- alarm


def test_alarm_defaults_ramp_rather_than_jumping() -> None:
    normalized = validate_alarm({})
    wake_volume = normalized["wake_volume"]
    assert wake_volume["start"] < wake_volume["end"]
    assert wake_volume["ramp_seconds"] > 0


def test_alarm_rejects_a_start_volume_that_is_basically_a_jolt() -> None:
    with pytest.raises(InvalidActionDescriptor):
        validate_alarm({"wake_volume": {"start": 0.9, "end": 1.0, "ramp_seconds": 1}})


def test_alarm_rejects_start_greater_than_end() -> None:
    with pytest.raises(InvalidActionDescriptor):
        validate_alarm({"wake_volume": {"start": 0.3, "end": 0.1, "ramp_seconds": 30}})


def test_alarm_rejects_non_positive_ramp_seconds() -> None:
    with pytest.raises(InvalidActionDescriptor):
        validate_alarm({"wake_volume": {"start": 0.05, "end": 0.5, "ramp_seconds": 0}})


def test_alarm_accepts_a_configured_ramp() -> None:
    normalized = validate_alarm(
        {"wake_volume": {"start": 0.1, "end": 0.6, "ramp_seconds": 120}, "label": "Sabah alarmı"}
    )
    assert normalized["wake_volume"] == {"start": 0.1, "end": 0.6, "ramp_seconds": 120}
    assert normalized["label"] == "Sabah alarmı"


# ------------------------------------------------------------------ media_playback


def test_media_playback_requires_owner_named_url() -> None:
    with pytest.raises(InvalidActionDescriptor):
        validate_media_playback({"title": "Sabah Podcasti"})


def test_media_playback_preserves_the_owners_requested_item_exactly() -> None:
    """The task brief's own acceptance test: never choose content on the owner's behalf,
    and never rewrite what they DID choose."""
    detail = {
        "url": "https://example.com/owner-chosen-episode?ep=42&Ref=Keep-Me",
        "title": "  Sabah Podcasti — Bölüm 42  ",  # deliberately odd whitespace/casing
    }
    normalized = validate_media_playback(dict(detail))
    assert normalized == detail  # byte-for-byte unchanged, not just "close enough"

    full = validate_action({"kind": "media_playback", "detail": detail})
    assert full["detail"] == detail


# ----------------------------------------------------------------- browser/display


def test_browser_action_requires_action_name() -> None:
    with pytest.raises(InvalidActionDescriptor):
        validate_action({"kind": "browser_action", "detail": {}})
    normalized = validate_action(
        {"kind": "browser_action", "detail": {"action": "open_tab", "url": "https://x"}}
    )
    assert normalized["detail"]["action"] == "open_tab"


def test_display_action_requires_action_name() -> None:
    with pytest.raises(InvalidActionDescriptor):
        validate_action({"kind": "display_action", "detail": {}})


# --------------------------------------------------------------------- dispatcher


def test_noop_dispatcher_never_claims_to_have_acted() -> None:
    outcome = NoopDispatcher().dispatch(
        routine_id=uuid.uuid4(), firing_id=uuid.uuid4(), action={"kind": "alarm", "detail": {}}
    )
    assert outcome.ok is True
    assert outcome.detail["dispatched"] is False
