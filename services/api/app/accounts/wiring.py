"""The connected accounts as live mail/calendar providers (card mail-accounts-connect).

``app.main.create_app`` asks here for the provider ``MailService``/``CalendarService`` read
through. With no OAuth client configured nothing changes: the env account (IMAP/SMTP,
CalDAV/ICS) is returned as it always was. Once the owner has entered a Google or Microsoft
client, the providers become multi-account: the account list is read from ``mail_accounts``
on every call (a newly connected account is live at the next question), each account's
access token comes from ``AccountsService.access_token`` (refreshed when due), and an env
account that is still configured rides along under the name ``IMAP`` / ``Takvim`` - both
reserved, so no owner account can take them.

Every mail account is ``(key, name, provider)``: the key is the row's id (the env account's
is "", the same key it has in the env-only wiring), so the index, the poll watermark and a
draft never depend on the name - renaming an account, or switching OAuth on beside the env
account, makes no old mail new (inspector, 3rd return). One reader and one sender per
account are kept for the life of the process, each holding ONE ``httpx.Client`` - a voice
question over three accounts reuses three connections instead of opening one per request.
"""

from __future__ import annotations

from typing import Any

from app.accounts.models import (
    ENV_CALENDAR_ACCOUNT_NAME,
    ENV_MAIL_ACCOUNT_NAME,
    PROVIDER_GMAIL,
)
from app.accounts.service import AccountsService
from app.calendar.providers import (
    GoogleCalendarProvider,
    GraphCalendarProvider,
    MultiAccountCalendarProvider,
)
from app.logging import get_logger
from app.mail.accounts import MultiAccountMailProvider, MultiAccountMailSender
from app.mail.cloud import (
    GmailApiMailProvider,
    GmailApiMailSender,
    GraphMailProvider,
    GraphMailSender,
)

logger = get_logger(__name__)

#: The env account's key: "" in both wirings (``MailIndexRow.account_key``).
ENV_ACCOUNT_KEY = ""


def oauth_configured(settings: Any) -> bool:
    return bool(settings.accounts_google_client_id or settings.accounts_microsoft_client_id)


class AccountDirectory:
    """Reads the connected accounts and hands out a token callable per account."""

    def __init__(self, service: AccountsService, session_factory: Any) -> None:
        self._service = service
        self._factory = session_factory

    def rows(self) -> list[tuple[str, str, str, str]]:
        """(id, name, provider, address) of every connected account; [] when unreadable."""
        try:
            with self._factory() as db:
                return [(str(r.id), r.name, r.provider, r.address) for r in self._service.rows(db)]
        except Exception as exc:  # noqa: BLE001 - an unreadable table is "no account", logged
            logger.warning("mail_accounts_unreadable", error_class=type(exc).__name__)
            return []

    def token(self, account_id: str) -> Any:
        def current() -> str:
            with self._factory() as db:
                return self._service.access_token(db, account_id=account_id)

        return current

    def mark_synced(self, key: str, error_class: str | None) -> None:
        if key == ENV_ACCOUNT_KEY:
            return
        with self._factory() as db:
            self._service.mark_synced(db, key, error_class)


class _PerAccount:
    """One object per (account id, provider kind), kept while the account stays connected."""

    def __init__(self, directory: Any) -> None:
        self._directory = directory
        self._kept: dict[tuple[str, str], Any] = {}

    def get(self, account_id: str, provider: str, kind: Any, **kwargs: Any) -> Any:
        slot = (account_id, provider)
        if slot not in self._kept:
            self._kept[slot] = kind(token=self._directory.token(account_id), **kwargs)
        return self._kept[slot]

    def forget_all_but(self, live: set[str]) -> None:
        for slot in [s for s in self._kept if s[0] not in live]:
            self._kept.pop(slot, None)


def build_account_mail(
    settings: Any,
    directory: Any,
    env_provider: Any,
    env_sender: Any,
) -> tuple[Any, Any]:
    if not oauth_configured(settings):
        return env_provider, env_sender
    reader_cache = _PerAccount(directory)
    sender_cache = _PerAccount(directory)

    def readers() -> list[tuple[str, str, Any]]:
        rows = directory.rows()
        reader_cache.forget_all_but({r[0] for r in rows})
        out: list[tuple[str, str, Any]] = []
        for account_id, name, provider, _address in rows:
            kind = GmailApiMailProvider if provider == PROVIDER_GMAIL else GraphMailProvider
            out.append((account_id, name, reader_cache.get(account_id, provider, kind)))
        if env_provider is not None:
            out.append((ENV_ACCOUNT_KEY, ENV_MAIL_ACCOUNT_NAME, env_provider))
        return out

    def senders() -> list[tuple[str, str, Any]]:
        rows = directory.rows()
        sender_cache.forget_all_but({r[0] for r in rows})
        out: list[tuple[str, str, Any]] = []
        for account_id, name, provider, address in rows:
            kind = GmailApiMailSender if provider == PROVIDER_GMAIL else GraphMailSender
            sender = sender_cache.get(account_id, f"{provider}:{address}", kind, mail_from=address)
            out.append((account_id, name, sender))
        if env_sender is not None:
            out.append((ENV_ACCOUNT_KEY, ENV_MAIL_ACCOUNT_NAME, env_sender))
        return out

    provider = MultiAccountMailProvider(readers, on_synced=directory.mark_synced)
    return provider, MultiAccountMailSender(senders)


def build_account_calendar(settings: Any, directory: Any, env_provider: Any) -> Any:
    if not oauth_configured(settings):
        return env_provider
    cache = _PerAccount(directory)

    def calendars() -> list[tuple[str, Any]]:
        rows = directory.rows()
        cache.forget_all_but({r[0] for r in rows})
        out: list[tuple[str, Any]] = []
        for account_id, name, provider, _address in rows:
            kind = GoogleCalendarProvider if provider == PROVIDER_GMAIL else GraphCalendarProvider
            out.append((name, cache.get(account_id, provider, kind)))
        if env_provider is not None:
            out.append((ENV_CALENDAR_ACCOUNT_NAME, env_provider))
        return out

    return MultiAccountCalendarProvider(calendars)


__all__ = [
    "ENV_ACCOUNT_KEY",
    "ENV_CALENDAR_ACCOUNT_NAME",
    "ENV_MAIL_ACCOUNT_NAME",
    "AccountDirectory",
    "build_account_calendar",
    "build_account_mail",
    "oauth_configured",
]
