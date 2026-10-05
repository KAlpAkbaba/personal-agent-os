"""gpt-live-provider (d20261004): OpenAI Live as a SECOND realtime candidate, behind a
setting that is off by default, with a per-session ``prefer_provider`` hint.

The default selection must not move: with the setting on and the key present, a session
that names no preference still gets ``openai-realtime``. ``prefer_provider`` only puts a
name at the head of the preference order - selection is still by capability, and an
unknown name is ignored (never a 422) with the reason in ``reasons``.
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.voice import providers_openai_live as live_module
from app.voice import providers_openai_realtime as realtime_module
from app.voice.providers_openai_live import TICKET_HEADER, OpenAILiveProvider
from app.voice.providers_openai_realtime import OpenAIRealtimeProvider
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import RealtimeSessionRow
from app.voice.realtime_sessions.routes import CreateSessionRequest
from app.voice.realtime_sessions.runtime import (
    RealtimeVoiceRuntime,
    default_providers,
    inactive_candidates,
)
from tests.unit.test_voice_realtime_sessions import _create, wired  # noqa: F401

VENDOR_KEY = "unit-test-openai-live-vendor-key-sentinel-never-leaves-the-server"
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


# ----------------------------------------------- through the real application object


def _register_live(runtime: RealtimeVoiceRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both real adapters, as production has them with the setting on: openai-realtime
    (its mint faked) and openai-live. Live ties openai-realtime on every capability
    preference (semantic, WebRTC), so only the preference order can decide."""

    class _Mint:
        def json(self) -> Any:
            return {"value": "ek_fake", "expires_at": 0, "session": {"id": "sess_rt"}}

    monkeypatch.setattr(realtime_module, "_send", lambda *_a, **_k: _Mint())
    for provider in (OpenAIRealtimeProvider(VENDOR_KEY), OpenAILiveProvider(VENDOR_KEY)):
        runtime.providers[provider.name] = provider


def test_route_prefer_provider_picks_live_and_the_sdp_exchange_works(
    wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _identity, runtime, *_ = wired
    _register_live(runtime, monkeypatch)
    sent: list[Any] = []

    def fake_send(req: Any, **_k: Any) -> Any:
        sent.append(req)

        class _R:
            def json(self) -> Any:
                return {"session": {"id": "live_1"}, "transport": {"type": "webrtc", "sdp": "ANS"}}

        return _R()

    monkeypatch.setattr(live_module, "_send", fake_send)

    default = _create(client)
    assert default["provider"] == "openai-realtime"  # no preference -> the default selection

    created = _create(client, prefer_provider=LIVE)
    assert created["provider"] == LIVE
    cred = created["credential"]
    descriptor = cred["transport_descriptor"]
    assert descriptor["dialect"] == "openai-live"
    assert VENDOR_KEY not in json.dumps(created)

    response = client.post(
        descriptor["sdp_exchange_url"],
        content=b"v=0\r\noffer",
        headers={"Content-Type": "application/sdp", descriptor["ticket_header"]: cred["secret"]},
    )
    assert response.status_code == 201, response.text
    assert response.text == "ANS"
    assert response.headers["content-type"].startswith("application/sdp")
    assert sent[0].json_body["transport"] == {"type": "webrtc", "sdp": "v=0\r\noffer"}
    # single use: the same ticket is refused the second time, without a vendor call
    again = client.post(
        descriptor["sdp_exchange_url"],
        content=b"v=0\r\noffer",
        headers={"Content-Type": "application/sdp", descriptor["ticket_header"]: cred["secret"]},
    )
    assert again.status_code == 422
    assert VENDOR_KEY not in again.text
    assert len(sent) == 1


def test_route_sdp_exchange_is_refused_for_a_session_of_another_provider(
    wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _identity, runtime, *_ = wired
    _register_live(runtime, monkeypatch)
    created = _create(client)  # openai-realtime's session
    response = client.post(
        f"/v1/voice/realtime/sessions/{created['session_id']}/live-sdp",
        content=b"v=0",
        headers={"Content-Type": "application/sdp", TICKET_HEADER: "x"},
    )
    assert response.status_code in (404, 409, 422)


def _live_session_with_vendor(
    wired: Any, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Any, dict[str, Any], list[Any]]:
    client, _identity, runtime, *_ = wired
    _register_live(runtime, monkeypatch)
    sent: list[Any] = []
    monkeypatch.setattr(live_module, "_send", lambda req, **_k: sent.append(req))
    return client, runtime, _create(client, prefer_provider=LIVE, session_ttl_s=60), sent


def _post_offer(client: Any, created: dict[str, Any]) -> Any:
    cred = created["credential"]
    descriptor = cred["transport_descriptor"]
    return client.post(
        descriptor["sdp_exchange_url"],
        content=b"v=0\r\noffer",
        headers={"Content-Type": "application/sdp", descriptor["ticket_header"]: cred["secret"]},
    )


def test_route_sdp_exchange_is_refused_for_a_closed_session(
    wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inspector-4: a closed session must not open a vendor session while its ticket lives."""
    client, _runtime, created, sent = _live_session_with_vendor(wired, monkeypatch)
    sid = created["session_id"]
    assert client.post(f"/v1/voice/realtime/sessions/{sid}/close", json={}).status_code == 200
    response = _post_offer(client, created)
    assert response.status_code == 410, response.text
    assert sent == []
    assert VENDOR_KEY not in response.text


def test_route_sdp_exchange_is_refused_for_an_expired_session(
    wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, runtime, created, sent = _live_session_with_vendor(wired, monkeypatch)
    sid = created["session_id"]
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        row.expires_at = service.utcnow() - service.timedelta(seconds=1)
        db.commit()
    response = _post_offer(client, created)
    assert response.status_code == 410, response.text
    assert sent == []
    assert client.get(f"/v1/voice/realtime/sessions/{sid}").json()["state"] == "expired"


def test_route_unknown_prefer_provider_is_ignored_and_a_malformed_one_is_422(wired) -> None:
    client, *_ = wired
    created = _create(client, prefer_provider="no-such-provider")
    assert created["provider"] != "no-such-provider"
    bad = client.post("/v1/voice/realtime/sessions", json={"prefer_provider": "Not Valid"})
    assert bad.status_code == 422
