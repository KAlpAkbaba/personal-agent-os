"""Several named mail/calendar accounts (card mail-accounts-connect, the owner 2026-10-05:
"Gmail ve M365 bağlayayım, 3 hesap, isimlendirmek istiyorum").

* three accounts with the owner's names round-trip; a name is unique (Turkish casefold);
* disconnect revokes at Google and deletes the row (Microsoft has no per-app revoke: the
  row and its tokens go, and the answer says where to remove the consent);
* the poller reads two fake accounts and every indexed message carries its account's name;
* the inbox answer names the account - "İş hesabımda yeni posta var mı?" through the real
  voice router and tool;
* a draft names the account it will go from, a reply inherits the original's account, and
  the confirmed send leaves through THAT account's sender only;
* the Gmail API and Microsoft Graph readers parse the raw message with the shared bounded
  parser, and the calendar readers tag every event with the account's name.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounts.models import MailAccountPendingRow, MailAccountRow
from app.accounts.service import AccountError, AccountsService
from app.actions.confirmation_gate import CONFIRM_SOURCE_REST, Confirmation
from app.calendar.providers import (
    GoogleCalendarProvider,
    GraphCalendarProvider,
    MultiAccountCalendarProvider,
)
from app.calendar.syncer import CalendarSyncer
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.mail.accounts import MultiAccountMailProvider, MultiAccountMailSender
from app.mail.cloud import GmailApiMailProvider, GraphMailProvider
from app.mail.models import MailDraftRow, MailIndexRow
from app.mail.poller import MailPoller
from app.mail.providers import FakeMailProvider, FakeMailSender
from app.mail.service import MailService
from app.operator.models import ObjectFocusRow

BASE = "https://pagentos-core.tail1234.ts.net"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ fixtures


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        MailAccountRow.__table__,
        MailAccountPendingRow.__table__,
        MailIndexRow.__table__,
        MailDraftRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _mailbox(
    tmp_path: Path, tag: str, *, unread: int, read: int = 1, age_h: int = 0
) -> FakeMailProvider:
    messages = []
    for i in range(unread + read):
        messages.append(
            {
                "message_id": f"<{tag}-{i}@example.com>",
                "uid": str(i + 1),
                "folder": "INBOX",
                "from": {"name": f"Gönderen {tag}", "email": f"{tag}{i}@example.com"},
                "to": [{"email": "owner@example.com"}],
                "subject": f"{tag} konu {i}",
                "date": (NOW - timedelta(hours=i + 1 + age_h)).isoformat(),
                "flags": [] if i < unread else ["\\Seen"],
                "body_text": f"{tag} gövde {i}",
            }
        )
    path = tmp_path / f"{tag}.json"
    path.write_text(json.dumps({"owner": {}, "folders": ["INBOX"], "messages": messages}), "utf-8")
    return FakeMailProvider(path)


class Tokens:
    """A MockTransport handler for the token, profile and revoke endpoints."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.addresses = iter(["kisisel@gmail.com", "is@aktivra.com.tr", "aktivra@gmail.com"])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if "token" in request.url.path and "revoke" not in url:
            return httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt-secret", "expires_in": 3600}
            )
        if "profile" in url:
            return httpx.Response(200, json={"emailAddress": next(self.addresses)})
        if url.startswith("https://graph.microsoft.com/v1.0/me"):
            return httpx.Response(200, json={"mail": next(self.addresses)})
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200, json={})
        return httpx.Response(404)


def _accounts(tokens: Tokens) -> AccountsService:
    return AccountsService(
        Settings(
            _env_file=None,
            accounts_public_base_url=BASE,
            accounts_token_secret="test-secret",
            accounts_google_client_id="g",
            accounts_google_client_secret="gs",
            accounts_microsoft_client_id="m",
            accounts_microsoft_client_secret="ms",
        ),
        transport=httpx.MockTransport(tokens),
    )


def _connect(service: AccountsService, db, provider: str, name: str, *, now=NOW) -> dict[str, Any]:
    url = service.start(db, provider=provider, name=name, now=now)["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    return service.complete(db, state=state, code="c", now=now)


# ------------------------------------------------------------------ accounts


def test_three_named_accounts_round_trip(factory) -> None:
    service = _accounts(Tokens())
    with factory() as db:
        _connect(service, db, "gmail", "Kişisel")
        _connect(service, db, "microsoft", "İş", now=NOW + timedelta(minutes=1))
        _connect(service, db, "gmail", "Aktivra", now=NOW + timedelta(minutes=2))
        listed = service.list(db)
    assert [(a["name"], a["provider"], a["address"], a["state"]) for a in listed] == [
        ("Kişisel", "gmail", "kisisel@gmail.com", "connected"),
        ("İş", "microsoft", "is@aktivra.com.tr", "connected"),
        ("Aktivra", "gmail", "aktivra@gmail.com", "connected"),
    ]
    assert set(listed[0]) == {
        "id", "name", "provider", "address", "scopes", "state", "connected_at",
        "last_sync_at", "last_error",
    }  # fmt: skip


def test_a_name_is_unique_under_turkish_casefold_and_rename_keeps_it_so(factory) -> None:
    service = _accounts(Tokens())
    with factory() as db:
        _connect(service, db, "microsoft", "İş")
        with pytest.raises(AccountError) as err:
            service.start(db, provider="gmail", name="iş", now=NOW)
        assert err.value.code == "name_taken"
        second = _connect(service, db, "gmail", "Kişisel")
        with pytest.raises(AccountError):
            service.rename(db, second["id"], "İŞ")  # Turkish: "İŞ" is "iş"; "IŞ" would be "ış"
        renamed = service.rename(db, second["id"], "Aile")
        assert renamed["name"] == "Aile"
        with pytest.raises(AccountError) as empty:
            service.rename(db, second["id"], "   ")
        assert empty.value.code == "name_invalid"


def test_disconnect_revokes_at_google_and_deletes_the_row(factory) -> None:
    tokens = Tokens()
    service = _accounts(tokens)
    with factory() as db:
        account = _connect(service, db, "gmail", "Kişisel")
        result = service.disconnect(db, account["id"])
        assert result["revoked"] is True
        assert db.execute(select(MailAccountRow)).scalars().all() == []
    revoke = [r for r in tokens.requests if "revoke" in str(r.url)]
    assert len(revoke) == 1
    assert parse_qs(revoke[0].content.decode())["token"] == ["rt-secret"]


def test_disconnect_at_microsoft_deletes_and_says_where_to_remove_the_consent(factory) -> None:
    service = _accounts(Tokens())
    with factory() as db:
        account = _connect(service, db, "microsoft", "İş")
        result = service.disconnect(db, account["id"])
        assert result["revoked"] is False
        assert "myapps.microsoft.com" in result["speech"]
        assert db.execute(select(MailAccountRow)).scalars().all() == []


# ------------------------------------------------------------------ the poller


def test_the_poller_reads_two_accounts_and_tags_every_message(factory, tmp_path) -> None:
    synced: list[tuple[str, str | None]] = []
    provider = MultiAccountMailProvider(
        lambda: [
            ("İş", _mailbox(tmp_path, "is", unread=2)),
            ("Kişisel", _mailbox(tmp_path, "ev", unread=1)),
        ],
        on_synced=lambda name, error: synced.append((name, error)),
    )
    poller = MailPoller(MailService(provider, None), enabled=True, interval_s=60)
    with factory() as db:
        result = poller.tick(db, now=NOW)
        rows = db.execute(select(MailIndexRow)).scalars().all()
    assert result["accounts"]["İş"]["new_unread"] == 2
    assert result["accounts"]["Kişisel"]["new_unread"] == 1
    assert result["new"] == 5
    by_account = {r.provider_message_id: r.account_key for r in rows}
    assert by_account["<is-0@example.com>"] == "İş"
    assert by_account["<ev-0@example.com>"] == "Kişisel"
    assert synced == [("İş", None), ("Kişisel", None)]


def test_one_failing_account_does_not_stop_the_other(factory, tmp_path) -> None:
    class Broken:
        def list_messages(self, *a: Any, **k: Any) -> list:
            raise ConnectionError("down")

    synced: list[tuple[str, str | None]] = []
    provider = MultiAccountMailProvider(
        lambda: [("İş", Broken()), ("Kişisel", _mailbox(tmp_path, "ev", unread=1))],
        on_synced=lambda name, error: synced.append((name, error)),
    )
    with factory() as db:
        result = MailService(provider, None).poll(db, now=NOW)
    assert result["accounts"]["İş"]["status"] == "failed"
    assert result["accounts"]["Kişisel"]["new_unread"] == 1
    assert synced == [("İş", "ConnectionError"), ("Kişisel", None)]


def test_the_same_message_in_two_accounts_is_indexed_once_per_account(factory, tmp_path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    same_a = _mailbox(tmp_path / "a", "x", unread=1, read=0)
    same_b = _mailbox(tmp_path / "b", "x", unread=1, read=0)
    provider = MultiAccountMailProvider(lambda: [("İş", same_a), ("Kişisel", same_b)])
    with factory() as db:
        MailService(provider, None).poll(db, now=NOW)
        rows = db.execute(select(MailIndexRow)).scalars().all()
    assert sorted(r.account_key for r in rows) == ["Kişisel", "İş"]


def test_no_accounts_is_account_missing(factory) -> None:
    provider = MultiAccountMailProvider(lambda: [])
    with factory() as db:
        service = MailService(provider, None)
        assert service.inbox_summary(db)["error_class"] == "account_missing"
        assert service.poll(db, now=NOW)["status"] == "no_account"


# ------------------------------------------------------------- the answer


def test_the_inbox_answer_names_each_account(factory, tmp_path) -> None:
    provider = MultiAccountMailProvider(
        lambda: [
            ("İş", _mailbox(tmp_path, "is", unread=3)),
            ("Kişisel", _mailbox(tmp_path, "ev", unread=0)),
        ]
    )
    with factory() as db:
        result = MailService(provider, None).inbox_summary(db)
    assert "İş hesabında 3 okunmamış posta" in result["speech"]
    assert "Kişisel hesabında okunmamış posta yok" in result["speech"]
    assert {m["account"] for m in result["messages"]} == {"İş", "Kişisel"}


def test_voice_is_hesabimda_yeni_posta_var_mi_answers_by_name(tmp_path) -> None:
    from app.voice.intents import Intent, resolve_intent
    from tests.voice_corpus.harness import build_harness

    assert resolve_intent("İş hesabımda yeni posta var mı?").intent == Intent.MAIL_INBOX
    with build_harness(temp_root=tmp_path) as h:
        h.mail._provider = MultiAccountMailProvider(  # type: ignore[attr-defined]
            lambda: [
                ("İş", _mailbox(tmp_path, "is", unread=2)),
                ("Kişisel", _mailbox(tmp_path, "ev", unread=1)),
            ]
        )
        sid = h.new_session()
        h.say(sid, "İş hesabımda yeni posta var mı?")
        answer = h.tool(sid, "c-1", "mail.inbox", {})
    assert answer["status"] == "succeeded", answer
    assert "İş hesabında 2 okunmamış posta" in answer["result"]["speech"]


# ------------------------------------------------------------------- sending


def _two_account_service(tmp_path: Path) -> tuple[MailService, FakeMailSender, FakeMailSender]:
    is_sender, ev_sender = FakeMailSender(), FakeMailSender()
    mailboxes = [
        ("İş", _mailbox(tmp_path, "is", unread=1)),
        ("Kişisel", _mailbox(tmp_path, "ev", unread=1)),
    ]
    provider = MultiAccountMailProvider(lambda: mailboxes)
    sender = MultiAccountMailSender(lambda: [("İş", is_sender), ("Kişisel", ev_sender)])
    return MailService(provider, sender), is_sender, ev_sender


def _confirm(service: MailService, db, draft_id: str) -> dict[str, Any]:
    service.read_draft(db, session_id="rest:s1")
    return service.send(
        db,
        draft_id=draft_id,
        host_flag_enabled=True,
        confirmation=Confirmation(source=CONFIRM_SOURCE_REST, session_id="rest:s1"),
    )


def test_a_new_draft_goes_from_the_named_account_and_says_so(factory, tmp_path) -> None:
    service, is_sender, ev_sender = _two_account_service(tmp_path)
    with factory() as db:
        draft = service.draft_new(
            db, to="ali@example.com", subject="Teklif", body="Merhaba", account="kişisel"
        )
        assert draft["draft"]["account"] == "Kişisel"
        assert "Kişisel hesabından" in draft["speech"]
        sent = _confirm(service, db, draft["draft"]["id"])
    assert sent["execution_status"] == "executed", sent
    assert len(ev_sender.sent) == 1 and is_sender.sent == []
    assert ev_sender.sent[0].account == "Kişisel"


def test_an_unknown_account_name_is_refused_never_guessed(factory, tmp_path) -> None:
    service, is_sender, ev_sender = _two_account_service(tmp_path)
    with factory() as db:
        result = service.draft_new(db, to="ali@example.com", subject="x", body="y", account="Okul")
    assert result["error_class"] == "account_unknown"
    assert "İş" in result["speech"] and "Kişisel" in result["speech"]


def test_a_reply_goes_from_the_account_the_message_came_to(factory, tmp_path) -> None:
    service, is_sender, ev_sender = _two_account_service(tmp_path)
    with factory() as db:
        service.read(db, target="is konu 0")
        draft = service.draft_reply(db, body="Tamam")
        assert draft["draft"]["account"] == "İş"
        _confirm(service, db, draft["draft"]["id"])
    assert len(is_sender.sent) == 1 and ev_sender.sent == []


# --------------------------------------------------------- cloud readers


def _raw(subject: str, message_id: str) -> bytes:
    return (
        f"From: Ali <ali@example.com>\r\nTo: owner@example.com\r\nSubject: {subject}\r\n"
        f"Message-ID: {message_id}\r\nDate: Mon, 05 Oct 2026 09:00:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\nMerhaba efendim\r\n"
    ).encode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _count_clients(monkeypatch) -> list[int]:
    created: list[int] = []
    real = httpx.Client

    def counting(*args: Any, **kwargs: Any) -> httpx.Client:
        created.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr("app.mail.cloud.httpx.Client", counting)
    return created


def _gmail_meta(gmail_id: str, subject: str, labels: list[str], *, mixed: bool = False) -> dict:
    return {
        "id": gmail_id,
        "threadId": f"t-{gmail_id}",
        "labelIds": labels,
        "snippet": f"{subject} özeti",
        "payload": {
            "mimeType": "multipart/mixed" if mixed else "text/plain",
            "headers": [
                {"name": "From", "value": "Ali Yılmaz <ali@example.com>"},
                {"name": "To", "value": "owner@example.com, Ayşe <ayse@example.com>"},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": f"<{gmail_id}@x>"},
                {"name": "Date", "value": "Mon, 05 Oct 2026 09:00:00 +0000"},
                {"name": "References", "value": "<a@x> <b@x>"},
            ],
        },
    }


def test_a_gmail_listing_reads_metadata_never_the_raw_message_on_one_client(monkeypatch) -> None:
    """Inspector finding 9 (3rd return): a listing fetched every message RAW - attachments
    and all - each on a fresh httpx.Client. Now: headers + snippet only, one client."""
    created = _count_clients(monkeypatch)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer tok"
        path = request.url.path
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "g1"}, {"id": "g2"}]})
        if request.url.params.get("format") != "metadata":
            return httpx.Response(500)  # a raw (or full) fetch in a listing is the bug
        if path.endswith("/messages/g1"):
            return httpx.Response(200, json=_gmail_meta("g1", "Fatura", ["INBOX", "UNREAD"]))
        if path.endswith("/messages/g2"):
            return httpx.Response(200, json=_gmail_meta("g2", "Selam", ["INBOX"], mixed=True))
        return httpx.Response(404)

    reader = GmailApiMailProvider(token=lambda: "tok", transport=httpx.MockTransport(handler))
    messages = reader.list_messages("INBOX", limit=10, since=NOW - timedelta(days=1))
    reader.list_messages("INBOX", limit=10)
    assert [(m.subject, m.message_id, m.unread) for m in messages] == [
        ("Fatura", "<g1@x>", True),
        ("Selam", "<g2@x>", False),
    ]
    first = messages[0]
    assert (first.from_name, first.from_email) == ("Ali Yılmaz", "ali@example.com")
    assert first.to == ("owner@example.com", "ayse@example.com")
    assert first.references == ("<a@x>", "<b@x>")
    assert first.snippet == "Fatura özeti" and first.date == datetime(2026, 10, 5, 9, tzinfo=UTC)
    assert [m.has_attachments for m in messages] == [False, True]
    q = parse_qs(urlparse(str(seen[0].url)).query)
    assert q["labelIds"] == ["INBOX"] and "after:" in q["q"][0]
    assert len(created) == 1, "one client per account, reused across requests"


def test_a_graph_listing_selects_the_fields_and_never_downloads_the_mime(monkeypatch) -> None:
    created = _count_clients(monkeypatch)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/$value"):
            return httpx.Response(500)
        if path.endswith("/mailFolders/inbox/messages"):
            item = {
                "id": "m1",
                "isRead": False,
                "subject": "Toplantı",
                "from": {"emailAddress": {"name": "Ayşe", "address": "ayse@example.com"}},
                "toRecipients": [{"emailAddress": {"address": "owner@example.com"}}],
                "receivedDateTime": "2026-10-05T09:00:00Z",
                "internetMessageId": "<m1@x>",
                "bodyPreview": "Yarın saat 10",
                "hasAttachments": True,
                "conversationId": "c1",
            }
            return httpx.Response(200, json={"value": [item]})
        return httpx.Response(404)

    reader = GraphMailProvider(token=lambda: "tok", transport=httpx.MockTransport(handler))
    [message] = reader.list_messages("INBOX", limit=5)
    reader.list_messages("INBOX", limit=5)
    assert (message.subject, message.message_id, message.unread) == ("Toplantı", "<m1@x>", True)
    assert (message.from_name, message.from_email) == ("Ayşe", "ayse@example.com")
    assert message.snippet == "Yarın saat 10" and message.has_attachments is True
    assert message.date == datetime(2026, 10, 5, 9, tzinfo=UTC)
    select_ = parse_qs(urlparse(str(seen[0].url)).query)["$select"][0]
    assert "bodyPreview" in select_ and "internetMessageId" in select_
    assert len(created) == 1


def test_the_gmail_sender_posts_base64url_mime() -> None:
    from app.mail.cloud import GmailApiMailSender
    from app.mail.providers import DraftInput

    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "sent-1"})

    sender = GmailApiMailSender(
        token=lambda: "tok", mail_from="owner@gmail.com", transport=httpx.MockTransport(handler)
    )
    message_id = sender.send(
        DraftInput(kind="new", to=("ali@example.com",), cc=(), subject="Konu", body="Gövde")
    )
    raw = base64.urlsafe_b64decode(posted[0]["raw"] + "==").decode()
    assert "To: ali@example.com" in raw and "From: owner@gmail.com" in raw
    assert message_id.startswith("<") and message_id in raw


# --------------------------------------------------------------- calendar


def test_calendar_events_from_two_accounts_carry_their_names() -> None:
    def google(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "e1",
                        "summary": "Diş hekimi",
                        "start": {"dateTime": "2026-10-06T10:00:00+03:00"},
                        "end": {"dateTime": "2026-10-06T11:00:00+03:00"},
                        "reminders": {"useDefault": False, "overrides": [{"minutes": 30}]},
                    },
                    {
                        "id": "e2",
                        "summary": "Bayram",
                        "start": {"date": "2026-10-07"},
                        "end": {"date": "2026-10-08"},
                    },
                ]
            },
        )

    def graph(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "o1",
                        "subject": "Proje toplantısı",
                        "start": {"dateTime": "2026-10-06T12:00:00.0000000", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-10-06T13:00:00.0000000", "timeZone": "UTC"},
                        "isAllDay": False,
                        "isReminderOn": True,
                        "reminderMinutesBeforeStart": 15,
                        "type": "occurrence",
                    }
                ]
            },
        )

    multi = MultiAccountCalendarProvider(
        lambda: [
            (
                "Kişisel",
                GoogleCalendarProvider(token=lambda: "t", transport=httpx.MockTransport(google)),
            ),
            ("İş", GraphCalendarProvider(token=lambda: "t", transport=httpx.MockTransport(graph))),
        ]
    )
    events = multi.events(NOW, NOW + timedelta(days=3))
    by_uid = {e.uid: e for e in events}
    assert by_uid["e1"].account == "Kişisel" and by_uid["e1"].reminders == (30,)
    assert by_uid["e2"].all_day is True
    assert by_uid["o1"].account == "İş" and by_uid["o1"].recurring is True
    assert by_uid["o1"].reminders == (15,)
    assert by_uid["o1"].as_dict()["account"] == "İş"
    assert [e.start for e in events] == sorted(e.start for e in events)


def test_a_failing_calendar_account_marks_the_pass_truncated_so_nothing_is_removed() -> None:
    class Broken:
        def events(self, start: datetime, end: datetime) -> list:
            raise ConnectionError("down")

    ok = GoogleCalendarProvider(
        token=lambda: "t",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []})),
    )
    multi = MultiAccountCalendarProvider(lambda: [("İş", Broken()), ("Kişisel", ok)])
    assert multi.events(NOW, NOW + timedelta(days=1)) == []
    assert multi.last_truncated is True
    only_broken = MultiAccountCalendarProvider(lambda: [("İş", Broken())])
    with pytest.raises(ConnectionError):
        only_broken.events(NOW, NOW + timedelta(days=1))


def test_the_calendar_syncer_mirrors_every_account_and_reports_each_by_name() -> None:
    from app.calendar.models import CalendarIndexRow, CalendarProposalRow
    from app.calendar.service import CalendarService
    from app.notifications.models import NotificationRow

    class Broken:
        def events(self, start: datetime, end: datetime) -> list:
            raise ConnectionError("down")

    def google(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "e1",
                        "summary": "Diş hekimi",
                        "start": {"dateTime": "2026-10-06T10:00:00+03:00"},
                        "end": {"dateTime": "2026-10-06T11:00:00+03:00"},
                    }
                ]
            },
        )

    synced: list[tuple[str, str | None]] = []
    multi = MultiAccountCalendarProvider(
        lambda: [
            (
                "Kişisel",
                GoogleCalendarProvider(token=lambda: "t", transport=httpx.MockTransport(google)),
            ),
            ("İş", Broken()),
        ],
        on_synced=lambda name, error: synced.append((name, error)),
    )
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        CalendarIndexRow.__table__,
        CalendarProposalRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
        NotificationRow.__table__,
    ):
        table.create(engine)
    syncer = CalendarSyncer(
        CalendarService(multi, None), enabled=True, interval_s=60, accounts=multi
    )
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        result = syncer.tick(db, now=NOW)
        uids = [r.uid for r in db.execute(select(CalendarIndexRow)).scalars()]
    engine.dispose()
    assert result["sync"]["status"] == "synced" and uids == ["e1"]
    assert result["accounts"] == {"Kişisel": "ok", "İş": "ConnectionError"}
    assert ("Kişisel", None) in synced and ("İş", "ConnectionError") in synced
    assert syncer.health_check()["accounts"] == {"Kişisel": "ok", "İş": "ConnectionError"}


# ------------------------------------------------- inspector's 3rd return


def test_per_account_unread_is_counted_before_the_merged_cut(factory, tmp_path) -> None:
    """Finding 3: İş has two old unread, Kişisel sixty newer read ones; the merged listing
    keeps the newest 50, and the answer used to count İş from what was left: none."""
    provider = MultiAccountMailProvider(
        lambda: [
            ("İş", _mailbox(tmp_path, "is", unread=2, read=0, age_h=100)),
            ("Kişisel", _mailbox(tmp_path, "ev", unread=0, read=60)),
        ]
    )
    with factory() as db:
        result = MailService(provider, None).inbox_summary(db)
    assert "İş hesabında 2 okunmamış posta var" in result["speech"], result["speech"]
    assert "Kişisel hesabında okunmamış posta yok" in result["speech"]
    assert result["unread"] == 2
    assert result["unread_by_account"] == {"İş": 2, "Kişisel": 0}


def test_a_rename_does_not_announce_old_mail_again(factory, tmp_path) -> None:
    """Finding 4: the index and the watermark were keyed by the account's NAME."""
    names = {"acc-1": "İş"}
    box = _mailbox(tmp_path, "is", unread=3, read=0)
    provider = MultiAccountMailProvider(lambda: [("acc-1", names["acc-1"], box)])
    service = MailService(provider, None)
    with factory() as db:
        assert service.poll(db, now=NOW)["new"] == 3
        assert service.poll(db, now=NOW)["new"] == 0
        names["acc-1"] = "Ofis"
        again = service.poll(db, now=NOW)
        rows = db.execute(select(MailIndexRow)).scalars().all()
    assert again["new"] == 0, again
    assert "Ofis" in again["accounts"]
    assert len(rows) == 3 and {r.account_key for r in rows} == {"acc-1"}


def test_switching_oauth_on_does_not_announce_the_env_accounts_old_mail(factory, tmp_path) -> None:
    """Finding 4: the env account was '' alone and 'IMAP' beside the OAuth accounts."""
    from app.accounts.wiring import build_account_mail

    class NoAccounts:
        def rows(self) -> list:
            return []

        def mark_synced(self, key: str, error_class: str | None) -> None:
            return None

    box = _mailbox(tmp_path, "env", unread=2)
    with factory() as db:
        assert MailService(box, None).poll(db, now=NOW)["new"] == 3
        provider, _sender = build_account_mail(
            Settings(_env_file=None, accounts_google_client_id="g"), NoAccounts(), box, None
        )
        result = MailService(provider, None).poll(db, now=NOW)
        rows = db.execute(select(MailIndexRow)).scalars().all()
    assert result["new"] == 0, result
    assert "IMAP" in result["accounts"]
    assert len(rows) == 3


def _renamable(tmp_path: Path):
    names = {"id-is": "İş", "id-ev": "Kişisel"}
    senders = {"id-is": FakeMailSender(), "id-ev": FakeMailSender()}
    boxes = {
        "id-is": _mailbox(tmp_path, "is", unread=1),
        "id-ev": _mailbox(tmp_path, "ev", unread=1),
    }
    provider = MultiAccountMailProvider(lambda: [(k, names[k], boxes[k]) for k in names])
    sender = MultiAccountMailSender(lambda: [(k, names[k], senders[k]) for k in names])
    return MailService(provider, sender), names, senders


def _send(service: MailService, db, draft_id: str) -> dict[str, Any]:
    return service.send(
        db,
        draft_id=draft_id,
        host_flag_enabled=True,
        confirmation=Confirmation(source=CONFIRM_SOURCE_REST, session_id="rest:s1"),
    )


def test_a_draft_made_before_a_rename_leaves_from_the_same_account(factory, tmp_path) -> None:
    """Finding 5: the draft stored the NAME; after a rename send found no such account."""
    service, names, senders = _renamable(tmp_path)
    with factory() as db:
        draft = service.draft_new(
            db, to="ali@example.com", subject="Teklif", body="Merhaba", account="İş"
        )
        assert draft["draft"]["account"] == "İş"
        names["id-is"] = "Ofis"
        read_back = service.read_draft(db, session_id="rest:s1")
        assert "Ofis hesabından" in read_back["speech"]
        assert read_back["draft"]["account"] == "Ofis"
        sent = _send(service, db, draft["draft"]["id"])
    assert sent["execution_status"] == "executed", sent
    assert len(senders["id-is"].sent) == 1 and senders["id-ev"].sent == []


def test_a_draft_whose_account_was_disconnected_says_so_and_stays(factory, tmp_path) -> None:
    import uuid

    service, names, senders = _renamable(tmp_path)
    with factory() as db:
        draft = service.draft_new(
            db, to="ali@example.com", subject="Teklif", body="Merhaba", account="İş"
        )
        del names["id-is"]
        read_back = service.read_draft(db, session_id="rest:s1")
        assert "bağlı olmayan" in read_back["speech"]
        sent = _send(service, db, draft["draft"]["id"])
        row = db.get(MailDraftRow, uuid.UUID(draft["draft"]["id"]))
        assert row is not None and row.state == "read_back"
    assert sent["error_class"] == "account_unknown", sent
    assert "bağlı değil" in sent["speech"]
    assert senders["id-ev"].sent == []


def test_voice_new_draft_goes_from_the_spoken_account(tmp_path) -> None:
    """Finding 10: the voice tool never passed the account to draft_new."""
    from tests.voice_corpus.harness import build_harness

    with build_harness(temp_root=tmp_path) as h:
        h.mail._provider = MultiAccountMailProvider(  # type: ignore[attr-defined]
            lambda: [
                ("İş", _mailbox(tmp_path, "is", unread=1)),
                ("Kişisel", _mailbox(tmp_path, "ev", unread=1)),
            ]
        )
        sid = h.new_session()
        h.say(sid, "Kişisel hesabımdan Ali'ye yeni mail yaz")
        answer = h.tool(
            sid,
            "c-1",
            "mail.draft",
            {"to": "ali@example.com", "subject": "Teklif", "body": "Merhaba", "account": "Kişisel"},
        )
    assert answer["status"] == "succeeded", answer
    assert answer["result"]["draft"]["account"] == "Kişisel"
    assert "Kişisel hesabından" in answer["result"]["speech"]


def test_the_agenda_names_the_account_of_every_event() -> None:
    """Finding 10: calendar/service.py dropped the account in the spoken agenda."""
    from app.calendar.models import CalendarIndexRow, CalendarProposalRow
    from app.calendar.service import CalendarService
    from app.notifications.models import NotificationRow

    def google(request: httpx.Request) -> httpx.Response:
        item = {
            "id": "e1",
            "summary": "Diş hekimi",
            "start": {"dateTime": "2026-10-06T10:00:00+03:00"},
            "end": {"dateTime": "2026-10-06T11:00:00+03:00"},
        }
        return httpx.Response(200, json={"items": [item]})

    def graph(request: httpx.Request) -> httpx.Response:
        item = {
            "id": "o1",
            "subject": "Proje toplantısı",
            "start": {"dateTime": "2026-10-06T12:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-10-06T13:00:00.0000000", "timeZone": "UTC"},
        }
        return httpx.Response(200, json={"value": [item]})

    multi = MultiAccountCalendarProvider(
        lambda: [
            ("Kişisel", GoogleCalendarProvider(token=lambda: "t", transport=httpx.MockTransport(google))),
            ("İş", GraphCalendarProvider(token=lambda: "t", transport=httpx.MockTransport(graph))),
        ]
    )  # fmt: skip
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        CalendarIndexRow.__table__,
        CalendarProposalRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
        NotificationRow.__table__,
    ):
        table.create(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        result = CalendarService(multi, None).agenda(db, start=NOW, end=NOW + timedelta(days=2))
    engine.dispose()
    assert "Diş hekimi (10:00, Kişisel)" in result["speech"], result["speech"]
    assert "Proje toplantısı (12:00, İş)" in result["speech"]
    assert {e["account"] for e in result["events"]} == {"Kişisel", "İş"}
