"""Several named accounts behind ONE ``MailProvider``/``MailSender`` (card
mail-accounts-connect).

``MailService`` keeps talking to one provider; this one fans out to every account the owner
connected and tags each message with the account's name, so an answer can say "İş hesabında
3 yeni posta". The account list is read through ``loader`` on every call (an account
connected on the page is live at the next question, no restart). One failing account never
silences the others: it is skipped and reported through ``on_synced``; only when EVERY
account fails does the call raise.

The sender picks the account by the draft's ``account`` name - the name the read-back spoke
- and refuses a name it does not know rather than sending from another account.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.logging import get_logger
from app.mail.providers import MAX_LIST_MESSAGES, DraftInput, MailAttachment, MailMessage

logger = get_logger(__name__)

AccountList = Sequence[tuple[str, Any]]


def account_key(name: str) -> str:
    """Turkish casefold ("İş" == "iş", "IŞIK" == "ışık")."""
    return name.replace("I", "ı").replace("İ", "i").lower().strip()


class MailAccountUnknownError(LookupError):
    """A draft names an account that is not connected (any more)."""


def _newest_first(messages: list[MailMessage]) -> list[MailMessage]:
    floor = datetime.min.replace(tzinfo=UTC)
    return sorted(messages, key=lambda m: m.date or floor, reverse=True)


class MultiAccountMailProvider:
    def __init__(
        self,
        loader: Callable[[], AccountList],
        *,
        on_synced: Callable[[str, str | None], None] | None = None,
    ) -> None:
        self._loader = loader
        self._on_synced = on_synced
        self.last_unparseable_count = 0

    # ------------------------------------------------------------------ accounts

    def accounts(self) -> list[tuple[str, Any]]:
        return list(self._loader())

    def account_names(self) -> list[str]:
        return [name for name, _ in self.accounts()]

    def has_accounts(self) -> bool:
        return bool(self.accounts())

    def resolve(self, name: str) -> str | None:
        """The connected account's own spelling of ``name``, or None."""
        key = account_key(name)
        for known in self.account_names():
            if account_key(known) == key:
                return known
        return None

    def report(self, name: str, error_class: str | None) -> None:
        if self._on_synced is None:
            return
        try:
            self._on_synced(name, error_class)
        except Exception:  # noqa: BLE001 - bookkeeping never breaks a read
            logger.warning("mail_account_report_failed", account=name)

    @staticmethod
    def tag(name: str, messages: list[MailMessage]) -> list[MailMessage]:
        return [replace(m, account=name) for m in messages]

    def _each(self, call: Callable[[Any], Any]) -> list[tuple[str, Any]]:
        """``call`` on every account; failures skipped and logged, all failing raises."""
        results: list[tuple[str, Any]] = []
        errors: list[BaseException] = []
        accounts = self.accounts()
        unparseable = 0
        for name, provider in accounts:
            try:
                results.append((name, call(provider)))
            except Exception as exc:  # noqa: BLE001 - one account never silences another
                logger.warning(
                    "mail_account_read_failed", account=name, error_class=type(exc).__name__
                )
                errors.append(exc)
                continue
            unparseable += int(getattr(provider, "last_unparseable_count", 0) or 0)
        self.last_unparseable_count = unparseable
        if accounts and errors and not results:
            raise errors[0]
        return results

    # ------------------------------------------------------------- MailProvider

    def folders(self) -> list[str]:
        seen: list[str] = []
        for _name, folders in self._each(lambda p: p.folders()):
            seen.extend(f for f in folders if f not in seen)
        return seen

    def list_messages(
        self, folder: str, *, limit: int = MAX_LIST_MESSAGES, since: datetime | None = None
    ) -> list[MailMessage]:
        merged: list[MailMessage] = []
        for name, found in self._each(lambda p: p.list_messages(folder, limit=limit, since=since)):
            merged.extend(self.tag(name, found))
        return _newest_first(merged)[:limit]

    def search(self, query: str, *, limit: int = MAX_LIST_MESSAGES) -> list[MailMessage]:
        merged: list[MailMessage] = []
        for name, found in self._each(lambda p: p.search(query, limit=limit)):
            merged.extend(self.tag(name, found))
        return _newest_first(merged)[:limit]

    def _owner_of(self, message_id: str) -> tuple[str, Any, MailMessage] | None:
        for name, provider in self.accounts():
            try:
                found = provider.get_message(message_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mail_account_read_failed", account=name, error_class=type(exc).__name__
                )
                continue
            if found is not None:
                return name, provider, replace(found, account=name)
        return None

    def get_message(self, message_id: str) -> MailMessage | None:
        owner = self._owner_of(message_id)
        return owner[2] if owner else None

    def get_attachment(self, message_id: str, index: int) -> MailAttachment | None:
        owner = self._owner_of(message_id)
        return owner[1].get_attachment(message_id, index) if owner else None

    def thread(self, message_id: str) -> list[MailMessage]:
        owner = self._owner_of(message_id)
        if owner is None:
            return []
        name, provider, _message = owner
        return self.tag(name, provider.thread(message_id))


class MultiAccountMailSender:
    def __init__(self, loader: Callable[[], AccountList]) -> None:
        self._loader = loader

    def send(self, draft: DraftInput) -> str:
        accounts = list(self._loader())
        if not accounts:
            raise MailAccountUnknownError("no account connected")
        if not draft.account:
            return accounts[0][1].send(draft)
        key = account_key(draft.account)
        for name, sender in accounts:
            if account_key(name) == key:
                return sender.send(draft)
        raise MailAccountUnknownError("draft account is not connected")


__all__ = [
    "MailAccountUnknownError",
    "MultiAccountMailProvider",
    "MultiAccountMailSender",
    "account_key",
]
