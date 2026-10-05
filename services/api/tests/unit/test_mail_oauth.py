"""Mail accounts over OAuth 2.0 + PKCE (card mail-accounts-connect, the owner 2026-10-05).

The owner connects Gmail and Microsoft 365 from the web page; no password is ever typed or
stored. What these tests hold:

* the authorization request carries a PKCE S256 challenge and a random ``state``; only the
  state's HASH is stored, the verifier only encrypted;
* a callback whose ``state`` was never issued (a forged callback), was already used, or has
  expired is refused BEFORE any token request leaves the Cloud Core;
* the token exchange sends the verifier that matches the challenge;
* the refresh token is stored encrypted and never appears in a response or a log line;
* an expired access token is refreshed, and a rotated refresh token replaces the old one;
* the dev-default token secret refuses a real (non-loopback) deployment.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounts.models import MailAccountPendingRow, MailAccountRow
from app.accounts.routes import callback_router, router
from app.accounts.service import AccountError, AccountsService
from app.config import Settings
from app.identity.dependencies import require_owner_session

ACCESS = "ya29.ACCESS-SECRET-never-logged-1"  # noqa: S105 - test fixture
REFRESH = "1//REFRESH-SECRET-never-logged-1"  # noqa: S105 - test fixture
ROTATED = "M.R3_ROTATED-REFRESH-SECRET-2"  # noqa: S105 - test fixture
CLIENT_SECRET = "GOCSPX-client-secret-never-logged"  # noqa: S105 - test fixture
BASE = "https://pagentos-core.tail1234.ts.net"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "accounts_public_base_url": BASE,
        "accounts_token_secret": "a-real-production-secret-for-tests",
        "accounts_google_client_id": "google-client.apps.googleusercontent.com",
        "accounts_google_client_secret": CLIENT_SECRET,
        "accounts_microsoft_client_id": "11111111-2222-3333-4444-555555555555",
        "accounts_microsoft_client_secret": CLIENT_SECRET,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class Recorder:
    """A MockTransport handler playing Google's and Microsoft's token/profile endpoints."""

    def __init__(self, *, refresh_reply: str | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.refresh_reply = refresh_reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token") or "/oauth2/v2.0/token" in url:
            form = parse_qs(request.content.decode())
            if form.get("grant_type") == ["refresh_token"]:
                body: dict[str, Any] = {"access_token": "ya29.REFRESHED-ACCESS", "expires_in": 3600}
                if self.refresh_reply:
                    body["refresh_token"] = self.refresh_reply
                return httpx.Response(200, json=body)
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS,
                    "refresh_token": REFRESH,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "granted",
                },
            )
        if url.startswith("https://gmail.googleapis.com/gmail/v1/users/me/profile"):
            return httpx.Response(200, json={"emailAddress": "owner@gmail.com"})
        if url.startswith("https://graph.microsoft.com/v1.0/me"):
            return httpx.Response(200, json={"mail": "owner@aktivra.com.tr"})
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": "unexpected", "url": url})

    def token_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if "token" in r.url.path and "revoke" not in r.url.path]


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    MailAccountRow.__table__.create(engine)
    MailAccountPendingRow.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _service(recorder: Recorder, **overrides: Any) -> AccountsService:
    return AccountsService(_settings(**overrides), transport=httpx.MockTransport(recorder))


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _connect(service: AccountsService, db, provider: str, name: str, *, now=NOW) -> dict:
    started = service.start(db, provider=provider, name=name, now=now)
    state = _query(started["authorize_url"])["state"]
    return service.complete(db, state=state, code="auth-code-1", now=now)


# ------------------------------------------------------------------ the request


def test_google_authorize_url_carries_pkce_s256_state_scopes_and_redirect(factory) -> None:
    rec = Recorder()
    service = _service(rec)
    with factory() as db:
        started = service.start(db, provider="gmail", name="Kişisel", now=NOW)
        url = started["authorize_url"]
        q = _query(url)
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert q["response_type"] == "code"
        assert q["code_challenge_method"] == "S256"
        assert q["redirect_uri"] == f"{BASE}/v1/accounts/oauth/callback"
        assert q["access_type"] == "offline" and q["prompt"] == "consent"
        scopes = q["scope"].split()
        assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
        assert "https://www.googleapis.com/auth/gmail.send" in scopes
        assert "https://www.googleapis.com/auth/calendar" in scopes
        assert len(q["state"]) >= 32
        # The raw state never sits in the database - only its hash.
        pending = db.execute(select(MailAccountPendingRow)).scalars().one()
        assert pending.state_hash == hashlib.sha256(q["state"].encode()).hexdigest()
        assert q["state"] not in json.dumps(
            {c.name: str(getattr(pending, c.name)) for c in MailAccountPendingRow.__table__.columns}
        )
    assert rec.requests == []  # starting a connection calls nobody


def test_microsoft_authorize_url_asks_graph_mail_and_calendar_with_offline_access(factory) -> None:
    service = _service(Recorder())
    with factory() as db:
        url = service.start(db, provider="microsoft", name="İş", now=NOW)["authorize_url"]
    assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")
    scopes = _query(url)["scope"].split()
    for scope in ("offline_access", "Mail.Read", "Mail.Send", "Calendars.ReadWrite"):
        assert scope in scopes


def test_the_token_exchange_sends_the_verifier_that_matches_the_challenge(factory) -> None:
    rec = Recorder()
    service = _service(rec)
    with factory() as db:
        started = service.start(db, provider="gmail", name="Kişisel", now=NOW)
        q = _query(started["authorize_url"])
        account = service.complete(db, state=q["state"], code="auth-code-1", now=NOW)
    exchange = parse_qs(rec.token_calls()[0].content.decode())
    verifier = exchange["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge.decode() == q["code_challenge"]
    assert exchange["code"] == ["auth-code-1"]
    assert exchange["grant_type"] == ["authorization_code"]
    assert exchange["redirect_uri"] == [f"{BASE}/v1/accounts/oauth/callback"]
    assert account["name"] == "Kişisel"
    assert account["address"] == "owner@gmail.com"
    assert account["state"] == "connected"


# --------------------------------------------------------------- forged callbacks


def test_a_forged_callback_is_refused_before_any_token_request(factory) -> None:
    rec = Recorder()
    service = _service(rec)
    with factory() as db:
        service.start(db, provider="gmail", name="Kişisel", now=NOW)
        with pytest.raises(AccountError) as err:
            service.complete(
                db, state="forged-state-the-attacker-made-up-xxxxxxxx", code="c", now=NOW
            )
        assert err.value.code == "state_unknown"
        assert db.execute(select(MailAccountRow)).scalars().all() == []
    assert rec.requests == []


def test_a_used_state_cannot_be_replayed(factory) -> None:
    rec = Recorder()
    service = _service(rec)
    with factory() as db:
        started = service.start(db, provider="gmail", name="Kişisel", now=NOW)
        state = _query(started["authorize_url"])["state"]
        service.complete(db, state=state, code="c", now=NOW)
        calls = len(rec.requests)
        with pytest.raises(AccountError) as err:
            service.complete(db, state=state, code="c2", now=NOW)
        assert err.value.code == "state_unknown"
        assert len(db.execute(select(MailAccountRow)).scalars().all()) == 1
    assert len(rec.requests) == calls


def test_an_expired_state_is_refused(factory) -> None:
    rec = Recorder()
    service = _service(rec)
    with factory() as db:
        started = service.start(db, provider="gmail", name="Kişisel", now=NOW)
        state = _query(started["authorize_url"])["state"]
        with pytest.raises(AccountError) as err:
            service.complete(db, state=state, code="c", now=NOW + timedelta(minutes=16))
        assert err.value.code == "state_expired"
    assert rec.requests == []


def test_the_callback_route_refuses_a_forged_state_with_a_turkish_page(factory) -> None:
    rec = Recorder()
    client = _client(factory, _service(rec))
    response = client.get("/v1/accounts/oauth/callback", params={"state": "x" * 43, "code": "c"})
    assert response.status_code == 400
    assert "Bağlantı kurulamadı" in response.text
    assert rec.requests == []


def test_a_provider_error_on_the_callback_is_reported_and_the_state_consumed(factory) -> None:
    rec = Recorder()
    service = _service(rec)
    client = _client(factory, service)
    url = client.post("/v1/accounts/connect", json={"provider": "gmail", "name": "Kişisel"}).json()[
        "authorize_url"
    ]
    state = _query(url)["state"]
    response = client.get(
        "/v1/accounts/oauth/callback", params={"state": state, "error": "access_denied"}
    )
    assert response.status_code == 400
    assert "izin verilmedi" in response.text
    with factory() as db:
        assert db.execute(select(MailAccountPendingRow)).scalars().all() == []
    assert rec.requests == []


# ------------------------------------------------------------ tokens at rest


def test_the_refresh_token_is_stored_encrypted(factory) -> None:
    service = _service(Recorder())
    with factory() as db:
        _connect(service, db, "gmail", "Kişisel")
        row = db.execute(select(MailAccountRow)).scalars().one()
        stored = bytes(row.refresh_token_enc)
        assert REFRESH.encode() not in stored
        assert ACCESS.encode() not in bytes(row.access_token_enc)
        assert service.decrypt(stored) == REFRESH


def test_no_token_or_client_secret_in_any_response_or_log_line(factory, caplog, capsys) -> None:
    caplog.set_level(logging.DEBUG)
    rec = Recorder()
    service = _service(rec)
    client = _client(factory, service)
    bodies: list[str] = []
    for provider, name in (("gmail", "Kişisel"), ("microsoft", "İş")):
        started = client.post("/v1/accounts/connect", json={"provider": provider, "name": name})
        bodies.append(started.text)
        state = _query(started.json()["authorize_url"])["state"]
        callback = client.get("/v1/accounts/oauth/callback", params={"state": state, "code": "c"})
        assert callback.status_code == 200, callback.text
        bodies.append(callback.text)
    listing = client.get("/v1/accounts")
    bodies.append(listing.text)
    first = listing.json()["accounts"][0]["id"]
    bodies.append(client.patch(f"/v1/accounts/{first}", json={"name": "Aile"}).text)
    with factory() as db:
        later = datetime.now(UTC) + timedelta(hours=2)
        assert service.access_token(db, name="Aile", now=later) == "ya29.REFRESHED-ACCESS"
    bodies.append(client.delete(f"/v1/accounts/{first}").text)
    captured = capsys.readouterr()
    haystack = "\n".join(bodies) + caplog.text + captured.out + captured.err
    for secret in (ACCESS, REFRESH, CLIENT_SECRET, "ya29.REFRESHED-ACCESS"):
        assert secret not in haystack


# ------------------------------------------------------------------- refresh


def test_an_expired_access_token_is_refreshed_and_a_rotated_refresh_token_kept(factory) -> None:
    rec = Recorder(refresh_reply=ROTATED)
    service = _service(rec)
    with factory() as db:
        _connect(service, db, "microsoft", "İş")
        assert service.access_token(db, name="İş", now=NOW + timedelta(minutes=10)) == ACCESS
        assert len(rec.token_calls()) == 1  # still fresh: no refresh
        token = service.access_token(db, name="iş", now=NOW + timedelta(hours=2))
        assert token == "ya29.REFRESHED-ACCESS"
        refresh_form = parse_qs(rec.token_calls()[-1].content.decode())
        assert refresh_form["grant_type"] == ["refresh_token"]
        assert refresh_form["refresh_token"] == [REFRESH]
        row = db.execute(select(MailAccountRow)).scalars().one()
        assert service.decrypt(bytes(row.refresh_token_enc)) == ROTATED


# -------------------------------------------------------------------- guards


def test_the_dev_default_token_secret_refuses_a_real_deployment(factory) -> None:
    service = AccountsService(
        Settings(
            _env_file=None,
            accounts_public_base_url=BASE,
            accounts_google_client_id="id",
            accounts_google_client_secret="s",
        ),
        transport=httpx.MockTransport(Recorder()),
    )
    with factory() as db, pytest.raises(AccountError) as err:
        service.start(db, provider="gmail", name="Kişisel", now=NOW)
    assert err.value.code == "token_secret_default"


def test_an_unconfigured_client_is_refused_with_the_setup_step(factory) -> None:
    service = _service(Recorder(), accounts_microsoft_client_id="")
    with factory() as db, pytest.raises(AccountError) as err:
        service.start(db, provider="microsoft", name="İş", now=NOW)
    assert err.value.code == "client_not_configured"
    assert "PAGENTOS_ACCOUNTS_MICROSOFT_CLIENT_ID" in err.value.speech


def test_setup_names_the_exact_redirect_url_and_never_a_secret() -> None:
    setup = _service(Recorder()).setup_info()
    assert setup["redirect_uri"] == f"{BASE}/v1/accounts/oauth/callback"
    assert setup["google"]["configured"] is True
    assert setup["microsoft"]["configured"] is True
    assert CLIENT_SECRET not in json.dumps(setup)
    assert any("PAGENTOS_ACCOUNTS_GOOGLE_CLIENT_SECRET" in s for s in setup["google"]["steps"])


def test_a_plain_http_public_base_is_refused_before_anything_is_stored(factory) -> None:
    """Inspector finding 8 (3rd return): the HTTPS check had no test. A tailnet host over
    plain http would carry the authorization code in clear; loopback stays allowed."""
    rec = Recorder()
    service = _service(rec, accounts_public_base_url="http://pagentos-core.tail1234.ts.net")
    with factory() as db:
        with pytest.raises(AccountError) as err:
            service.start(db, provider="gmail", name="Kişisel", now=NOW)
        assert err.value.code == "public_base_not_https"
        assert db.execute(select(MailAccountPendingRow)).scalars().all() == []
        loopback = _service(rec, accounts_public_base_url="http://127.0.0.1:8001")
        assert loopback.start(db, provider="gmail", name="Kişisel", now=NOW)["authorize_url"]
    assert rec.requests == []


@pytest.mark.parametrize("name", ["IMAP", "imap", "Takvim", "TAKVİM", " takvim "])
def test_the_env_account_names_are_reserved(factory, name: str) -> None:
    """Inspector finding 6: 'IMAP' / 'Takvim' name the env account beside the connected
    ones; an owner account under the same name would make 'IMAP hesabından gönder' pick
    whichever came first."""
    service = _service(Recorder())
    with factory() as db:
        with pytest.raises(AccountError) as err:
            service.start(db, provider="gmail", name=name, now=NOW)
        assert err.value.code == "name_reserved"
        account = _connect(service, db, "gmail", "Kişisel")
        with pytest.raises(AccountError) as renamed:
            service.rename(db, account["id"], name)
        assert renamed.value.code == "name_reserved"


def test_a_non_json_token_answer_is_a_turkish_page_not_a_500(factory) -> None:
    """Inspector finding 7: a 200 whose body is not JSON (a captive portal, a proxy's HTML)
    raised JSONDecodeError out of the callback."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "token" in request.url.path:
            return httpx.Response(200, content=b"<html>proxy login</html>")
        return httpx.Response(404)

    service = AccountsService(_settings(), transport=httpx.MockTransport(handler))
    client = _client(factory, service)
    url = client.post("/v1/accounts/connect", json={"provider": "gmail", "name": "Kişisel"}).json()[
        "authorize_url"
    ]
    response = client.get(
        "/v1/accounts/oauth/callback", params={"state": _query(url)["state"], "code": "c"}
    )
    assert response.status_code == 400, response.text
    assert "Bağlantı kurulamadı" in response.text
    with factory() as db:
        assert db.execute(select(MailAccountRow)).scalars().all() == []


# ------------------------------------------------------------------- helpers


def _client(factory, service: AccountsService) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.include_router(callback_router)
    app.state.accounts_service = service
    app.state.accounts_session_factory = factory
    app.dependency_overrides[require_owner_session] = lambda: None
    return TestClient(app)
