"""Several named accounts behind ONE ``MailProvider``/``MailSender`` (card
mail-accounts-connect).

``MailService`` keeps talking to one provider; this one fans out to every account the owner
connected and tags each message with the account's name, so an answer can say "İş hesabında
3 yeni posta". The account list is read through ``loader`` on every call (an account
connected on the page is live at the next question, no restart). One failing account never
silences the others: it is skipped and reported through ``on_synced``; only when EVERY
account fails does the call raise.

An account is ``(key, name, provider)``: ``key`` is its stable identity (``mail_accounts.id``;
"" the env account) and is what the index, the poll watermark and a draft store - a rename
changes the name the owner hears, never which mail is "new" or where a draft leaves from
(inspector, 3rd return). A two-tuple ``(name, provider)`` is an account whose key is its name
(the test fakes).

The sender picks the account by the draft's ``account`` key and refuses a key it does not
know rather than sending from another account.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.logging import get_logger
from app.mail.providers import MAX_LIST_MESSAGES, DraftInput, MailAttachment, MailMessage

logger = get_logger(__name__)

AccountList = Sequence[tuple[Any, ...]]
Account = tuple[str, str, Any]


def account_key(name: str) -> str:
    """Turkish casefold ("İş" == "iş", "IŞIK" == "ışık")."""
    return name.replace("I", "ı").replace("İ", "i").lower().strip()


def _entries(raw: AccountList) -> list[Account]:
    out: list[Account] = []
    for entry in raw:
        if len(entry) == 2:
            out.append((str(entry[0]), str(entry[0]), entry[1]))
        else:
            out.append((str(entry[0]), str(entry[1]), entry[2]))
    return out


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
        #: name -> unread count of the LAST ``list_messages``, counted per account BEFORE the
        #: merged listing is cut to ``limit`` (an old unread in a quiet account is still
        #: unread when a busy account fills the newest 50).
        self.last_unread_by_account: dict[str, int] = {}

    # ------------------------------------------------------------------ accounts

    def entries(self) -> list[Account]:
        return _entries(self._loader())

    def accounts(self) -> list[tuple[str, Any]]:
        return [(name, provider) for _key, name, provider in self.entries()]

    def account_names(self) -> list[str]:
        return [name for _key, name, _p in self.entries()]

    def has_accounts(self) -> bool:
        return bool(self.entries())

    def resolve(self, name: str) -> str | None:
        """The connected account's own spelling of ``name``, or None."""
        found = self.resolve_key(name)
        return found[1] if found else None

    def resolve_key(self, name: str) -> tuple[str, str] | None:
        """(key, name) of the account the owner called ``name``, or None."""
        wanted = account_key(name)
        for key, known, _p in self.entries():
            if account_key(known) == wanted:
                return key, known
        return None

    def name_of(self, key: str) -> str | None:
        """The CURRENT name of the account ``key``, or None when it is not connected."""
        for known_key, name, _p in self.entries():
            if known_key == key:
                return name
        return None

    def report(self, key: str, error_class: str | None) -> None:
        if self._on_synced is None:
            return
        try:
            self._on_synced(key, error_class)
        except Exception:  # noqa: BLE001 - bookkeeping never breaks a read
            logger.warning("mail_account_report_failed", account=key)

    @staticmethod
    def tag(key: str, name: str, messages: list[MailMessage]) -> list[MailMessage]:
        return [replace(m, account=name, account_key=key) for m in messages]

    def _each(self, call: Callable[[Any], Any]) -> list[tuple[str, str, Any]]:
        """``call`` on every account; failures skipped and logged, all failing raises."""
        results: list[tuple[str, str, Any]] = []
        errors: list[BaseException] = []
        accounts = self.entries()
        unparseable = 0
        for key, name, provider in accounts:
            try:
                results.append((key, name, call(provider)))
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
        for _key, _name, folders in self._each(lambda p: p.folders()):
            seen.extend(f for f in folders if f not in seen)
        return seen

    def list_messages(
        self, folder: str, *, limit: int = MAX_LIST_MESSAGES, since: datetime | None = None
    ) -> list[MailMessage]:
        merged: list[MailMessage] = []
        unread: dict[str, int] = {}
        for key, name, found in self._each(
            lambda p: p.list_messages(folder, limit=limit, since=since)
        ):
            unread[name] = sum(1 for m in found if m.unread)
            merged.extend(self.tag(key, name, found))
        self.last_unread_by_account = unread
        return _newest_first(merged)[:limit]

    def search(self, query: str, *, limit: int = MAX_LIST_MESSAGES) -> list[MailMessage]:
        merged: list[MailMessage] = []
        for key, name, found in self._each(lambda p: p.search(query, limit=limit)):
            merged.extend(self.tag(key, name, found))
        return _newest_first(merged)[:limit]

    def _owner_of(self, message_id: str) -> tuple[str, Any, MailMessage] | None:
        for key, name, provider in self.entries():
            try:
                found = provider.get_message(message_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mail_account_read_failed", account=name, error_class=type(exc).__name__
                )
                continue
            if found is not None:
                return key, provider, replace(found, account=name, account_key=key)
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
        key, provider, message = owner
        return self.tag(key, message.account, provider.thread(message_id))


class MultiAccountMailSender:
    def __init__(self, loader: Callable[[], AccountList]) -> None:
        self._loader = loader

    def send(self, draft: DraftInput) -> str:
        accounts = _entries(self._loader())
        if not accounts:
            raise MailAccountUnknownError("no account connected")
        # None: a draft from before accounts existed - it was made on the env account.
        wanted = "" if draft.account is None else draft.account
        for key, _name, sender in accounts:
            if key == wanted:
                return sender.send(draft)
        raise MailAccountUnknownError("draft account is not connected")


__all__ = [
    "MailAccountUnknownError",
    "MultiAccountMailProvider",
    "MultiAccountMailSender",
    "account_key",
]
