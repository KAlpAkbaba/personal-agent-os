"""gpt-live-provider (d20261004): OpenAI Live as a SECOND realtime candidate, behind a
setting that is off by default, with a per-session ``prefer_provider`` hint.

The default selection must not move: with the setting on and the key present, a session
that names no preference still gets ``openai-realtime``. ``prefer_provider`` only puts a
name at the head of the preference order - selection is still by capability, and an
unknown name is ignored (never a 422) with the reason in ``reasons``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.voice.realtime_sessions.routes import CreateSessionRequest
from app.voice.realtime_sessions.runtime import (
    RealtimeVoiceRuntime,
    default_providers,
    inactive_candidates,
)

VENDOR_KEY = "sk-test-live-0000000000000000"
LIVE = "openai-live"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"environment": "prod", "voice_openai_api_key": VENDOR_KEY}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


# ------------------------------------------------------------- registration


def test_live_is_off_by_default_and_says_why_in_turkish() -> None:
    settings = _settings()
    assert settings.voice_realtime_openai_live_enabled is False
    assert LIVE not in default_providers(settings)
    reason = inactive_candidates(settings)[LIVE]
    assert "kapalı" in reason
    assert "PAGENTOS_VOICE_REALTIME_OPENAI_LIVE_ENABLED" in reason


def test_live_enabled_without_key_is_not_registered() -> None:
    settings = _settings(voice_realtime_openai_live_enabled=True, voice_openai_api_key="")
    assert LIVE not in default_providers(settings)
    assert LIVE in inactive_candidates(settings)


def test_live_enabled_with_key_is_registered_but_not_the_default_choice() -> None:
    settings = _settings(voice_realtime_openai_live_enabled=True)
    assert settings.voice_realtime_openai_live_model == "gpt-live-1"
    runtime = RealtimeVoiceRuntime(settings)
    assert LIVE in runtime.providers
    assert LIVE not in runtime.inactive
    provider, result = runtime.select()
    assert provider.name == "openai-realtime"
    assert LIVE in result.ranked


# -------------------------------------------------------------- preference


def test_prefer_provider_live_selects_live() -> None:
    runtime = RealtimeVoiceRuntime(_settings(voice_realtime_openai_live_enabled=True))
    provider, result = runtime.select(prefer_provider=LIVE)
    assert provider.name == LIVE
    assert result.selected.name == LIVE


def test_unknown_prefer_provider_is_ignored_and_reasons_say_so() -> None:
    runtime = RealtimeVoiceRuntime(_settings(voice_realtime_openai_live_enabled=True))
    provider, result = runtime.select(prefer_provider="no-such-provider")
    assert provider.name == "openai-realtime"
    assert any("no-such-provider" in r for r in result.reasons)


def test_prefer_provider_live_while_off_is_ignored() -> None:
    runtime = RealtimeVoiceRuntime(_settings())
    provider, result = runtime.select(prefer_provider=LIVE)
    assert provider.name == "openai-realtime"
    assert any(LIVE in r for r in result.reasons)


# ------------------------------------------------------------------ route body


def test_create_session_body_accepts_prefer_provider() -> None:
    body = CreateSessionRequest.model_validate({"prefer_provider": LIVE})
    assert body.prefer_provider == LIVE
    assert CreateSessionRequest.model_validate({}).prefer_provider is None


@pytest.mark.parametrize("bad", ["OpenAI", "1abc", "a", "a_b", "x" * 40, "openai live"])
def test_create_session_body_rejects_a_malformed_prefer_provider(bad: str) -> None:
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate({"prefer_provider": bad})
