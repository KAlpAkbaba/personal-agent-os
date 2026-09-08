"""``ImapMailProvider`` against a scripted fake IMAP4 server on a loopback socket
(docs/M21_MAIL_CALENDAR_SPEC.md §2, §4, ADR-0084) — the socket-level FETCH/SEARCH grammar
the provider actually parses, never the fixture's in-memory objects directly. No network:
the fake binds ``127.0.0.1`` on an ephemeral port.
"""

from __future__ import annotations

import time

from app.mail.providers import ImapMailProvider
from tests.mail_calendar_support import (
    FakeImapServer,
    _ImapMessage,
    default_imap_fixture,
    deeply_nested_rfc822,
    folded_from_header_rfc822,
)


def _provider(server: FakeImapServer) -> ImapMailProvider:
    return ImapMailProvider(
        host="127.0.0.1",
        port=server.port,
        username="owner",
        password="secret-password-never-in-a-receipt",
        use_ssl=False,
    )


def test_login_select_and_list_messages() -> None:
    with FakeImapServer() as server:
        provider = _provider(server)
        folders = provider.folders()
        assert "INBOX" in folders
        messages = provider.list_messages("INBOX", limit=50)
        assert len(messages) == 3
        uids = {m.uid for m in messages}
        assert uids == {"201", "202", "203"}


def test_rfc2047_subject_is_decoded() -> None:
    with FakeImapServer() as server:
        provider = _provider(server)
        messages = provider.list_messages("INBOX", limit=50)
        m201 = next(m for m in messages if m.uid == "201")
        assert m201.subject == "Eylül 2026 elektrik faturanız"
        assert m201.from_name == "Ali Yılmaz"
        assert m201.from_email == "ali.yilmaz@example.com"


def test_html_only_body_is_reduced_to_text() -> None:
    with FakeImapServer() as server:
        provider = _provider(server)
        messages = provider.list_messages("INBOX", limit=50)
        m202 = next(m for m in messages if m.uid == "202")
        assert "1.284,50 TL" in m202.body_text
        assert "<" not in m202.body_text  # no HTML tags leaked through


def test_unseen_flag_is_read_from_the_wire() -> None:
    with FakeImapServer() as server:
        provider = _provider(server)
        messages = provider.list_messages("INBOX", limit=50)
        unread = {m.uid for m in messages if m.unread}
        assert unread == {"201"}


def test_search_unseen_charset_utf8() -> None:
    with FakeImapServer() as server:
        provider = _provider(server)
        results = provider.search("faturanız", limit=50)
        uids = {m.uid for m in results}
        assert "201" in uids or "202" in uids  # TEXT search across the raw wire bytes


def test_get_message_by_message_id() -> None:
    with FakeImapServer() as server:
        provider = _provider(server)
        found = provider.get_message("<t203@fixture.example>")
        assert found is not None
        assert found.subject == "Merhaba"


def test_get_message_unknown_id_returns_none() -> None:
    with FakeImapServer() as server:
        provider = _provider(server)
        assert provider.get_message("<no-such-id@fixture.example>") is None


def test_body_is_bounded_and_never_carries_the_password() -> None:
    with FakeImapServer() as server:
        provider = _provider(server)
        messages = provider.list_messages("INBOX", limit=50)
        for m in messages:
            assert "secret-password-never-in-a-receipt" not in m.body_text
            assert "secret-password-never-in-a-receipt" not in m.subject


# --------------------------------------------------------------- M1 (security review)


def _mailbox_with_a_poisoned_message() -> dict[str, list[_ImapMessage]]:
    """The default fixture's own three good messages, plus ONE poisoned one (a
    ~3000-level nested multipart) at a distinct uid — the real wire-level shape M1's
    ``ImapMailProvider._fetch_uids`` must isolate."""
    mailbox = default_imap_fixture()
    mailbox["INBOX"] = [
        *mailbox["INBOX"],
        _ImapMessage(uid=299, raw=deeply_nested_rfc822(3000, uid=299), seen=True),
    ]
    return mailbox


def test_a_poisoned_message_does_not_break_the_folders_own_fetch_loop() -> None:
    """M1: one poisoned message used to raise ``RecursionError`` out of the fetch loop,
    taking ``mail.inbox``/``mail.search``/``mail.thread`` down with it for the WHOLE
    folder (verified live). Isolated per-message now: the folder still lists its three
    good messages, in bounded time, and the poisoned one is counted, never silently
    dropped and never crashing the caller."""
    with FakeImapServer(mailbox=_mailbox_with_a_poisoned_message()) as server:
        provider = _provider(server)
        started = time.monotonic()
        messages = provider.list_messages("INBOX", limit=50)
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"took {elapsed:.2f}s - the folder must stay bounded"
        uids = {m.uid for m in messages}
        assert uids == {"201", "202", "203"}  # the three good ones, poisoned one excluded
        assert provider.last_unparseable_count == 1


def test_a_poisoned_message_is_isolated_in_search_and_thread_too() -> None:
    with FakeImapServer(mailbox=_mailbox_with_a_poisoned_message()) as server:
        provider = _provider(server)
        results = provider.search("faturanız", limit=50)
        assert provider.last_unparseable_count == 0  # the poisoned message never matches
        assert {m.uid for m in results} <= {"201", "202"}

        anchor = provider.get_message("<t203@fixture.example>")
        assert anchor is not None
        related = provider.thread(anchor.message_id)
        assert "203" in {m.uid for m in related}


# --------------------------------------------------------------- L1 (security review)


def test_a_folded_from_header_is_sanitized_on_the_real_wire() -> None:
    """L1, through the REAL IMAP wire path this time (the pure-function version lives in
    ``test_mail_security_hardening.py``)."""
    mailbox = default_imap_fixture()
    mailbox["INBOX"] = [
        *mailbox["INBOX"],
        _ImapMessage(uid=298, raw=folded_from_header_rfc822(uid=298), seen=True),
    ]
    with FakeImapServer(mailbox=mailbox) as server:
        provider = _provider(server)
        found = provider.get_message("<t298@fixture.example>")
        assert found is not None
        assert "\r" not in found.from_email
        assert "\n" not in found.from_email
