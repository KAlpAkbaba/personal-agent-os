"""inbound-calls-wire: the inbound line is part of the REAL application (``create_app``).

``test_telephony_inbound_routes`` drives the routes on their own test app; this file proves the
binding: ``app.telephony.routes.ROUTERS`` carries the inbound router (found by
``app.registry``), ``app.state.telephony_inbound`` is an ``InboundLine`` built from ``Settings``
and the dispatch session factory, and the line is OFF unless ``PAGENTOS_TELEPHONY_INBOUND_ENABLED``
says otherwise.

The recorder's session scope is swapped for an in-memory SQLite one before a request: the
default ``Settings`` point at a PostgreSQL this unit run does not have.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.telephony import inbound_twilio as tw
from app.telephony.inbound_bridge import InboundLine
from app.telephony.inbound_records import InboundRecorder
from tests.unit.test_telephony_inbound_bridge import CALLER, make_scope

AUTH = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
PUBLIC = "https://jarvis.tail1234.ts.net:8443"
CALL = "CA" + "c" * 32


def _sign(url: str, fields: dict[str, str]) -> str:
    data = url + "".join(k + fields[k] for k in sorted(fields))
    return base64.b64encode(hmac.new(AUTH.encode(), data.encode(), hashlib.sha1).digest()).decode()


def _fields() -> dict[str, str]:
    return {
        "CallSid": CALL,
        "AccountSid": "AC" + "1" * 32,
        "From": CALLER,
        "To": "+908501234567",
        "CallStatus": "ringing",
        "Direction": "inbound",
    }


def _app(**overrides: Any):
    settings = Settings(
        _env_file=None,
        telephony_public_base_url=PUBLIC,
        telephony_twilio_auth_token=AUTH,
        voice_openai_api_key="sk-test",
        **overrides,
    )
    app = create_app(settings)
    engine, scope = make_scope()
    app.state.telephony_inbound.recorder = InboundRecorder(scope)
    return app, engine


@pytest.fixture()
def wired():
    made: list[Any] = []

    def build(**overrides: Any) -> TestClient:
        app, engine = _app(**overrides)
        client = TestClient(app)
        made.append((client, engine))
        return client

    yield build
    for client, engine in made:
        client.close()
        engine.dispose()


def _signed_voice(client: TestClient):
    fields = _fields()
    headers = {tw.SIGNATURE_HEADER: _sign(PUBLIC + tw.VOICE_PATH, fields)}
    return client.post(tw.VOICE_PATH, data=fields, headers=headers)


def test_create_app_builds_an_inbound_line_that_is_off_by_default() -> None:
    app = create_app(Settings(_env_file=None))
    line = app.state.telephony_inbound
    assert isinstance(line, InboundLine)
    assert line.settings.enabled is False
    assert line.settings.daily_minutes_cap == 30
    # the same dedicated session factory the outbound caller and the push rung use
    assert line.recorder._session_scope is app.state.accounts_session_factory


def test_the_settings_reach_the_line() -> None:
    settings = Settings(
        _env_file=None, telephony_inbound_enabled=True, telephony_inbound_daily_minutes=12
    )
    line = create_app(settings).state.telephony_inbound
    assert line.settings.enabled is True
    assert line.settings.daily_minutes_cap == 12


def test_the_env_names_are_the_documented_ones(monkeypatch) -> None:
    monkeypatch.setenv("PAGENTOS_TELEPHONY_INBOUND_ENABLED", "true")
    monkeypatch.setenv("PAGENTOS_TELEPHONY_INBOUND_DAILY_MINUTES", "7")
    settings = Settings(_env_file=None)
    assert settings.telephony_inbound_enabled is True
    assert settings.telephony_inbound_daily_minutes == 7


def test_an_unsigned_voice_webhook_is_a_bodiless_403(wired) -> None:
    client = wired(telephony_inbound_enabled=True)
    response = client.post(tw.VOICE_PATH, data=_fields())
    assert response.status_code == 403
    assert response.content == b""


def test_a_signed_call_is_answered_with_the_kvkk_notice_when_enabled(wired) -> None:
    client = wired(telephony_inbound_enabled=True)
    response = _signed_voice(client)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert tw.KVKK_ANNOUNCEMENT_TR in response.text
    assert "<Stream" in response.text


def test_a_signed_call_is_refused_when_the_line_is_off(wired) -> None:
    client = wired()  # PAGENTOS_TELEPHONY_INBOUND_ENABLED unset
    response = _signed_voice(client)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert tw.REFUSE_TEXT_TR in response.text
    assert "<Hangup" in response.text
    assert tw.KVKK_ANNOUNCEMENT_TR not in response.text
