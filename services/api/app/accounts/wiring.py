"""The connected accounts as live mail/calendar providers (card mail-accounts-connect).

``app.main.create_app`` asks here for the provider ``MailService``/``CalendarService`` read
through. With no OAuth client configured nothing changes: the env account (IMAP/SMTP,
CalDAV/ICS) is returned as it always was. Once the owner has entered a Google or Microsoft
client, the providers become multi-account: the account list is read from ``mail_accounts``
on every call (a newly connected account is live at the next question), each account's
access token comes from ``AccountsService.access_token`` (refreshed when due), and an env
account that is still configured rides along under the name ``IMAP`` / ``Takvim``.
"""

from __future__ import annotations

from typing import Any

from app.accounts.models import PROVIDER_GMAIL
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

ENV_MAIL_ACCOUNT_NAME = "IMAP"
ENV_CALENDAR_ACCOUNT_NAME = "Takvim"


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

    def mark_synced(self, name: str, error_class: str | None) -> None:
        with self._factory() as db:
            self._service.mark_synced(db, name, error_class)


def build_account_mail(
    settings: Any,
    directory: AccountDirectory,
    env_provider: Any,
    env_sender: Any,
) -> tuple[Any, Any]:
    if not oauth_configured(settings):
        return env_provider, env_sender

    def readers() -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for account_id, name, provider, _address in directory.rows():
            reader = GmailApiMailProvider if provider == PROVIDER_GMAIL else GraphMailProvider
            out.append((name, reader(token=directory.token(account_id))))
        if env_provider is not None:
            out.append((ENV_MAIL_ACCOUNT_NAME, env_provider))
        return out

    def senders() -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for account_id, name, provider, address in directory.rows():
            sender = GmailApiMailSender if provider == PROVIDER_GMAIL else GraphMailSender
            out.append((name, sender(token=directory.token(account_id), mail_from=address)))
        if env_sender is not None:
            out.append((ENV_MAIL_ACCOUNT_NAME, env_sender))
        return out

    provider = MultiAccountMailProvider(readers, on_synced=directory.mark_synced)
    return provider, MultiAccountMailSender(senders)


def build_account_calendar(settings: Any, directory: AccountDirectory, env_provider: Any) -> Any:
    if not oauth_configured(settings):
        return env_provider

    def calendars() -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for account_id, name, provider, _address in directory.rows():
            reader = GoogleCalendarProvider if provider == PROVIDER_GMAIL else GraphCalendarProvider
            out.append((name, reader(token=directory.token(account_id))))
        if env_provider is not None:
            out.append((ENV_CALENDAR_ACCOUNT_NAME, env_provider))
        return out

    return MultiAccountCalendarProvider(calendars)


__all__ = [
    "ENV_CALENDAR_ACCOUNT_NAME",
    "ENV_MAIL_ACCOUNT_NAME",
    "AccountDirectory",
    "build_account_calendar",
    "build_account_mail",
    "oauth_configured",
]
