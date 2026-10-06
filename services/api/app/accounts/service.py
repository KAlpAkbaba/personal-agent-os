"""Connecting the owner's Gmail and Microsoft 365 accounts (card mail-accounts-connect).

OAuth 2.0 authorization code + PKCE (RFC 7636, S256) against Google and the Microsoft
identity platform, over plain ``httpx`` - no SDK, no relay; the two token endpoints are
three form posts (ADR in team/plans/mail-accounts-connect-adr.md says why not google-auth /
msal). The owner's browser goes to the provider and comes back to
``{accounts_public_base_url}/v1/accounts/oauth/callback``; that callback carries no owner
session (it is a top-level navigation from the provider), so the ``state`` is its whole
authority:

* ``start`` makes a random ``state`` and verifier, stores ONLY the state's SHA-256 and the
  verifier encrypted, and returns the authorization URL;
* ``complete`` finds the pending row by the hash, deletes it before anything else (one use),
  refuses it once expired, and only then sends the code with the verifier.

A forged callback (a state never issued), a replayed one and a late one are refused before
any request leaves this process. Tokens are Fernet-encrypted at rest with a key derived from
``accounts_token_secret`` by ``app.voice.crypto.derive_fernet_key`` - the same machinery the
speaker profiles use - and never logged or returned: ``_public`` is the only shape a caller
ever sees. Errors log the exception CLASS or the HTTP status, never a body.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.accounts.models import (
    PROVIDER_GMAIL,
    PROVIDER_MICROSOFT,
    PROVIDERS,
    RESERVED_NAMES,
    STATE_CONNECTED,
    STATE_ERROR,
    MailAccountPendingRow,
    MailAccountRow,
)
from app.logging import get_logger
from app.voice.crypto import derive_fernet_key

logger = get_logger(__name__)

CALLBACK_PATH = "/v1/accounts/oauth/callback"
DEV_TOKEN_SECRET = "pagentos-dev-accounts-token-secret"  # noqa: S105 - the refused default
#: How long the owner has between pressing the button and coming back from the provider.
PENDING_TTL = timedelta(minutes=15)
#: An access token is refreshed this long before the provider says it expires.
REFRESH_MARGIN = timedelta(seconds=60)
#: The owner asked for three; room for a few more, never an unbounded list.
MAX_ACCOUNTS = 10
NAME_MAX = 40

GOOGLE_SCOPES: tuple[str, ...] = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
)
MICROSOFT_SCOPES: tuple[str, ...] = (
    "offline_access",
    "openid",
    "email",
    "User.Read",
    "Mail.Read",
    "Mail.Send",
    "Calendars.ReadWrite",
)
GOOGLE_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GRAPH_PROFILE_URL = "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
MICROSOFT_CONSENT_URL = "https://myapps.microsoft.com"


class AccountError(Exception):
    """A refusal with a stable ``code`` and the Turkish sentence the page shows."""

    def __init__(self, code: str, speech: str, *, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.speech = speech
        self.status = status


@dataclass(frozen=True, slots=True)
class _Client:
    provider: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    extra: tuple[tuple[str, str], ...]


def turkish_key(name: str) -> str:
    """Turkish casefold: "İş" and "iş", "IŞIK" and "ışık" are one name."""
    return name.replace("I", "ı").replace("İ", "i").lower()


def clean_name(raw: str) -> str:
    name = " ".join(str(raw or "").split())
    if not name or len(name) > NAME_MAX or any(ord(c) < 32 for c in name):
        raise AccountError(
            "name_invalid", f"Hesap adı 1 ile {NAME_MAX} karakter arasında olmalı efendim."
        )
    # Both foldings: "imap" is "IMAP" to the owner, though Turkish folds "I" to "ı".
    reserved_keys = {turkish_key(r) for r in RESERVED_NAMES} | {
        r.casefold() for r in RESERVED_NAMES
    }
    if turkish_key(name) in reserved_keys or name.casefold() in reserved_keys:
        raise AccountError(
            "name_reserved",
            f"'{name}' adı ortam hesabına ayrılmış; başka bir ad seçin efendim.",
        )
    return name


def _aware(value: datetime | None) -> datetime | None:
    # SQLite hands a DateTime(timezone=True) back naive; the stored value is UTC.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _is_loopback(base: str) -> bool:
    host = (urlparse(base).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class AccountsService:
    def __init__(
        self,
        settings: Any,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._timeout = timeout
        self._fernet = Fernet(derive_fernet_key(settings.accounts_token_secret))

    # ------------------------------------------------------------------ plumbing

    @property
    def redirect_uri(self) -> str:
        base = (self._settings.accounts_public_base_url or "").rstrip("/")
        return f"{base}{CALLBACK_PATH}" if base else ""

    def _http(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=self._timeout)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(bytes(token)).decode("utf-8")
        except InvalidToken as exc:
            raise AccountError(
                "token_unreadable",
                "Kayıtlı hesap anahtarı çözülemedi; hesabı yeniden bağlayın efendim.",
                status=409,
            ) from exc

    def _client(self, provider: str) -> _Client:
        s = self._settings
        if provider == PROVIDER_GMAIL:
            return _Client(
                provider=provider,
                client_id=s.accounts_google_client_id,
                client_secret=s.accounts_google_client_secret,
                authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
                token_url="https://oauth2.googleapis.com/token",
                scopes=GOOGLE_SCOPES,
                # offline + consent: Google returns a refresh token only on a consent screen.
                extra=(("access_type", "offline"), ("prompt", "consent")),
            )
        if provider == PROVIDER_MICROSOFT:
            tenant = s.accounts_microsoft_tenant or "common"
            root = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
            return _Client(
                provider=provider,
                client_id=s.accounts_microsoft_client_id,
                client_secret=s.accounts_microsoft_client_secret,
                authorize_url=f"{root}/authorize",
                token_url=f"{root}/token",
                scopes=MICROSOFT_SCOPES,
                extra=(("response_mode", "query"), ("prompt", "select_account")),
            )
        raise AccountError(
            "provider_unknown", "Yalnız Gmail ve Microsoft 365 bağlanabilir efendim."
        )

    def _require_ready(self, client: _Client) -> None:
        base = self._settings.accounts_public_base_url or ""
        if not base:
            raise AccountError(
                "public_base_missing",
                "Cloud Core'un adresi ayarlı değil: PAGENTOS_ACCOUNTS_PUBLIC_BASE_URL "
                "(tailnet HTTPS adresi) girilmeli efendim.",
            )
        loopback = _is_loopback(base)
        if not loopback and urlparse(base).scheme != "https":
            raise AccountError(
                "public_base_not_https", "Dönüş adresi HTTPS olmalı efendim (tailnet adresi)."
            )
        if not loopback and self._settings.accounts_token_secret == DEV_TOKEN_SECRET:
            raise AccountError(
                "token_secret_default",
                "Anahtarları şifreleyecek gizli değer ayarlı değil: "
                "PAGENTOS_ACCOUNTS_TOKEN_SECRET girilmeli efendim.",
            )
        if not client.client_id or not client.client_secret:
            env = "GOOGLE" if client.provider == PROVIDER_GMAIL else "MICROSOFT"
            raise AccountError(
                "client_not_configured",
                f"Uygulama kaydı girilmemiş: PAGENTOS_ACCOUNTS_{env}_CLIENT_ID ve "
                f"PAGENTOS_ACCOUNTS_{env}_CLIENT_SECRET ayarlanmalı efendim.",
            )

    def _by_name(self, db: Session, name: str) -> MailAccountRow | None:
        return (
            db.execute(select(MailAccountRow).where(MailAccountRow.name_key == turkish_key(name)))
            .scalars()
            .first()
        )

    def _by_id(self, db: Session, account_id: str) -> MailAccountRow:
        try:
            key = uuid.UUID(str(account_id))
        except ValueError:
            key = None
        row = db.get(MailAccountRow, key) if key else None
        if row is None:
            raise AccountError("account_not_found", "Böyle bir hesap yok efendim.", status=404)
        return row

    def _post_token(self, client: _Client, form: dict[str, str]) -> dict[str, Any]:
        body = {**form, "client_id": client.client_id, "client_secret": client.client_secret}
        if client.provider == PROVIDER_MICROSOFT:
            body["scope"] = " ".join(client.scopes)
        try:
            with self._http() as http:
                response = http.post(client.token_url, data=body)
        except httpx.HTTPError as exc:
            logger.warning("mail_account_token_failed", error_class=type(exc).__name__)
            raise AccountError(
                "token_request_failed", "Sağlayıcıya ulaşılamadı efendim.", status=502
            ) from exc
        if response.status_code != 200:
            logger.warning("mail_account_token_refused", status=response.status_code)
            raise AccountError(
                "token_refused",
                f"Sağlayıcı anahtarı vermedi (HTTP {response.status_code}) efendim.",
                status=502,
            )
        try:
            data = response.json()
        except ValueError:  # a captive portal's or a proxy's HTML page under a 200
            logger.warning("mail_account_token_not_json", status=response.status_code)
            data = None
        if not isinstance(data, dict) or not data.get("access_token"):
            raise AccountError("token_refused", "Sağlayıcı anahtar döndürmedi efendim.", status=502)
        return data

    def _address(self, provider: str, access_token: str) -> str:
        url = GOOGLE_PROFILE_URL if provider == PROVIDER_GMAIL else GRAPH_PROFILE_URL
        try:
            with self._http() as http:
                response = http.get(url, headers={"Authorization": f"Bearer {access_token}"})
            data = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("mail_account_profile_failed", error_class=type(exc).__name__)
            data = {}
        value = data.get("emailAddress") or data.get("mail") or data.get("userPrincipalName")
        return str(value or "")[:320]

    @staticmethod
    def _public(row: MailAccountRow) -> dict[str, Any]:
        """The ONLY shape an account leaves this module in - no token column, ever."""
        return {
            "id": str(row.id),
            "name": row.name,
            "provider": row.provider,
            "address": row.address,
            "scopes": list(row.scopes_json or []),
            "state": row.state,
            "connected_at": _aware(row.connected_at).isoformat() if row.connected_at else None,
            "last_sync_at": _aware(row.last_sync_at).isoformat() if row.last_sync_at else None,
            "last_error": row.last_error,
        }

    # ------------------------------------------------------------------ the page

    def setup_info(self) -> dict[str, Any]:
        """What the page shows the owner: the redirect URL to register and the steps."""
        redirect = self.redirect_uri or "(önce PAGENTOS_ACCOUNTS_PUBLIC_BASE_URL ayarlanmalı)"
        s = self._settings
        return {
            "redirect_uri": self.redirect_uri,
            "public_base_configured": bool(s.accounts_public_base_url),
            "google": {
                "configured": bool(s.accounts_google_client_id and s.accounts_google_client_secret),
                "console_url": "https://console.cloud.google.com/apis/credentials",
                "steps": [
                    "Google Cloud Console'da bir proje açın; 'APIs & Services > Library' "
                    "altından Gmail API ve Google Calendar API'yi etkinleştirin.",
                    "'OAuth consent screen': User type External; kendi adresinizi Test users'a "
                    "ekleyin, sonra 'Publish app' ile In production yapın (Testing kipinde "
                    "Google anahtarı 7 günde düşürür; doğrulanmamış uygulama uyarısı kendi "
                    "uygulamanız için beklenen bir uyarıdır).",
                    "'Credentials > Create credentials > OAuth client ID': Web application; "
                    f"Authorized redirect URI olarak tam şunu girin: {redirect}",
                    "Client ID ve Client secret'ı Cloud Core ortamına girin: "
                    "PAGENTOS_ACCOUNTS_GOOGLE_CLIENT_ID ve PAGENTOS_ACCOUNTS_GOOGLE_CLIENT_SECRET; "
                    "Cloud Core'u yeniden başlatın.",
                ],
            },
            "microsoft": {
                "configured": bool(
                    s.accounts_microsoft_client_id and s.accounts_microsoft_client_secret
                ),
                "console_url": "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps",
                "steps": [
                    "Microsoft Entra yönetim merkezinde 'App registrations > New registration'; "
                    "Supported account types: 'Accounts in any organizational directory and "
                    "personal Microsoft accounts'.",
                    f"Redirect URI: platform 'Web', adres tam şu: {redirect}",
                    "'API permissions > Microsoft Graph > Delegated': Mail.Read, Mail.Send, "
                    "Calendars.ReadWrite, User.Read, offline_access ekleyin (iş hesabında "
                    "kuruluş yöneticisi onayı istenebilir).",
                    "'Certificates & secrets > New client secret' (süresi dolunca yenilenmeli); "
                    "Application (client) ID ve secret değerini girin: "
                    "PAGENTOS_ACCOUNTS_MICROSOFT_CLIENT_ID ve "
                    "PAGENTOS_ACCOUNTS_MICROSOFT_CLIENT_SECRET; Cloud Core'u yeniden başlatın.",
                ],
            },
            "token_secret_env": "PAGENTOS_ACCOUNTS_TOKEN_SECRET",
            "public_base_env": "PAGENTOS_ACCOUNTS_PUBLIC_BASE_URL",
        }

    def list(self, db: Session) -> list[dict[str, Any]]:
        rows = db.execute(select(MailAccountRow).order_by(MailAccountRow.connected_at)).scalars()
        return [self._public(r) for r in rows]

    def rows(self, db: Session) -> list[MailAccountRow]:
        return list(
            db.execute(select(MailAccountRow).order_by(MailAccountRow.connected_at)).scalars()
        )

    # ------------------------------------------------------------------ connect

    def start(
        self, db: Session, *, provider: str, name: str, now: datetime | None = None
    ) -> dict[str, Any]:
        if provider not in PROVIDERS:
            raise AccountError(
                "provider_unknown", "Yalnız Gmail ve Microsoft 365 bağlanabilir efendim."
            )
        name = clean_name(name)
        client = self._client(provider)
        self._require_ready(client)
        if self._by_name(db, name) is not None:
            raise AccountError("name_taken", f"'{name}' adında bir hesap zaten var efendim.")
        if len(self.rows(db)) >= MAX_ACCOUNTS:
            raise AccountError("too_many", f"En fazla {MAX_ACCOUNTS} hesap bağlanabilir efendim.")
        now = now or datetime.now(UTC)
        db.execute(delete(MailAccountPendingRow).where(MailAccountPendingRow.expires_at < now))
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        db.add(
            MailAccountPendingRow(
                id=uuid.uuid4(),
                state_hash=hashlib.sha256(state.encode("ascii")).hexdigest(),
                provider=provider,
                name=name,
                code_verifier_enc=self.encrypt(verifier),
                created_at=now,
                expires_at=now + PENDING_TTL,
            )
        )
        db.commit()
        params = {
            "client_id": client.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(client.scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **dict(client.extra),
        }
        return {
            "authorize_url": f"{client.authorize_url}?{urlencode(params)}",
            "provider": provider,
            "name": name,
        }

    def complete(
        self,
        db: Session,
        *,
        state: str,
        code: str | None,
        error: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        unknown = AccountError(
            "state_unknown",
            "Bu bağlantı isteği tanınmadı ya da zaten kullanıldı; "
            "sayfadan yeniden başlatın efendim.",
        )
        if not state or len(state) > 512:
            raise unknown
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        pending = (
            db.execute(
                select(MailAccountPendingRow).where(MailAccountPendingRow.state_hash == state_hash)
            )
            .scalars()
            .first()
        )
        if pending is None:
            raise unknown
        # One use: the row is gone before the provider is asked anything.
        provider, name = pending.provider, pending.name
        verifier_enc, expires_at = bytes(pending.code_verifier_enc), _aware(pending.expires_at)
        db.delete(pending)
        db.commit()
        if expires_at is None or now > expires_at:
            raise AccountError(
                "state_expired", "Bağlantı isteğinin süresi doldu; yeniden başlatın efendim."
            )
        if error:
            raise AccountError(
                "consent_denied", "Sağlayıcıda izin verilmedi; hesap bağlanmadı efendim."
            )
        if not code:
            raise AccountError("code_missing", "Sağlayıcı bir yetki kodu döndürmedi efendim.")
        client = self._client(provider)
        self._require_ready(client)
        tokens = self._post_token(
            client,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": self.decrypt(verifier_enc),
            },
        )
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise AccountError(
                "no_refresh_token",
                "Sağlayıcı kalıcı izin vermedi; bağlantıyı yeniden deneyin efendim.",
                status=502,
            )
        if self._by_name(db, name) is not None:
            raise AccountError("name_taken", f"'{name}' adında bir hesap zaten var efendim.")
        access = str(tokens["access_token"])
        row = MailAccountRow(
            id=uuid.uuid4(),
            name=name,
            name_key=turkish_key(name),
            provider=provider,
            address=self._address(provider, access),
            scopes_json=str(tokens.get("scope") or " ".join(client.scopes)).split(),
            refresh_token_enc=self.encrypt(str(refresh)),
            access_token_enc=self.encrypt(access),
            access_expires_at=now + timedelta(seconds=int(tokens.get("expires_in") or 3600)),
            state=STATE_CONNECTED,
            last_error=None,
            connected_at=now,
            last_sync_at=None,
        )
        db.add(row)
        db.commit()
        logger.info("mail_account_connected", provider=provider, account=name)
        return self._public(row)

    # ------------------------------------------------------------------ tokens

    def access_token(
        self,
        db: Session,
        *,
        name: str | None = None,
        account_id: str | None = None,
        now: datetime | None = None,
    ) -> str:
        now = now or datetime.now(UTC)
        row = self._by_id(db, account_id) if account_id else self._by_name(db, name or "")
        if row is None:
            raise AccountError("account_not_found", "Böyle bir hesap yok efendim.", status=404)
        expires = _aware(row.access_expires_at)
        if row.access_token_enc and expires is not None and now + REFRESH_MARGIN < expires:
            return self.decrypt(row.access_token_enc)
        client = self._client(row.provider)
        try:
            tokens = self._post_token(
                client,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self.decrypt(row.refresh_token_enc),
                },
            )
        except AccountError as exc:
            row.state = STATE_ERROR
            row.last_error = exc.code
            db.commit()
            raise
        access = str(tokens["access_token"])
        row.access_token_enc = self.encrypt(access)
        row.access_expires_at = now + timedelta(seconds=int(tokens.get("expires_in") or 3600))
        if tokens.get("refresh_token"):  # Microsoft rotates it; keep the newest
            row.refresh_token_enc = self.encrypt(str(tokens["refresh_token"]))
        row.state = STATE_CONNECTED
        row.last_error = None
        db.commit()
        return access

    def mark_synced(
        self, db: Session, account_id: str, error_class: str | None, *, now: datetime | None = None
    ) -> None:
        """Bookkeeping for the page's "son eşitleme" - by the account's id (the key the
        readers report under; "" is the env account, which has no row)."""
        try:
            row = self._by_id(db, account_id)
        except AccountError:
            return
        if error_class is None:
            row.last_sync_at = now or datetime.now(UTC)
            row.state = STATE_CONNECTED
            row.last_error = None
        else:
            row.state = STATE_ERROR
            row.last_error = error_class[:120]
        db.commit()

    # ------------------------------------------------------------------ manage

    def rename(self, db: Session, account_id: str, name: str) -> dict[str, Any]:
        row = self._by_id(db, account_id)
        name = clean_name(name)
        other = self._by_name(db, name)
        if other is not None and other.id != row.id:
            raise AccountError("name_taken", f"'{name}' adında bir hesap zaten var efendim.")
        row.name = name
        row.name_key = turkish_key(name)
        db.commit()
        return self._public(row)

    def disconnect(self, db: Session, account_id: str) -> dict[str, Any]:
        """Revoke at the provider where it can be revoked, then delete the row and its
        tokens. Google revokes a refresh token by itself; Microsoft offers no per-app
        revocation of a delegated token (its revokeSignInSessions signs the owner out of
        EVERY app), so the tokens are deleted here and the answer says where the consent
        itself is removed."""
        row = self._by_id(db, account_id)
        name, provider = row.name, row.provider
        revoked = False
        if provider == PROVIDER_GMAIL:
            try:
                with self._http() as http:
                    response = http.post(
                        GOOGLE_REVOKE_URL, data={"token": self.decrypt(row.refresh_token_enc)}
                    )
                revoked = response.status_code == 200
            except (httpx.HTTPError, AccountError) as exc:
                logger.warning("mail_account_revoke_failed", error_class=type(exc).__name__)
        db.delete(row)
        db.commit()
        if provider == PROVIDER_GMAIL:
            speech = (
                f"'{name}' hesabının bağlantısı kesildi ve Google'daki izni geri alındı efendim."
                if revoked
                else f"'{name}' hesabı silindi; Google izni geri alınamadı, "
                "myaccount.google.com/permissions adresinden kaldırabilirsiniz efendim."
            )
        else:
            speech = (
                f"'{name}' hesabı ve anahtarları silindi. Microsoft tarafındaki izni "
                f"{MICROSOFT_CONSENT_URL} adresinden kaldırabilirsiniz efendim."
            )
        logger.info("mail_account_disconnected", provider=provider, account=name, revoked=revoked)
        return {"id": str(account_id), "name": name, "revoked": revoked, "speech": speech}


__all__ = [
    "CALLBACK_PATH",
    "DEV_TOKEN_SECRET",
    "AccountError",
    "AccountsService",
    "clean_name",
    "turkish_key",
]
