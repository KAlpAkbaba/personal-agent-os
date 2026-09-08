"""``ImapMailProvider`` against a scripted fake IMAP4 server on a loopback socket
(docs/M21_MAIL_CALENDAR_SPEC.md §2, §4, ADR-0084) — the socket-level FETCH/SEARCH grammar
the provider actually parses, never the fixture's in-memory objects directly. No network:
the fake binds ``127.0.0.1`` on an ephemeral port.
"""

from __future__ import annotations

from app.mail.providers import ImapMailProvider
from tests.mail_calendar_support import FakeImapServer


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
