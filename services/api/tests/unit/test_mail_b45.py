"""B45 - mail that works once an account exists (docs/DECISIONS.md ADR-0152).

* the reply's References chain (req 343, 346) - ``send`` passed an empty tuple until B45;
* the inbox on the routine clock (req 360), run twice to prove the second pass is quiet;
* the morning briefing's unread-mail clause under its own preference (req 278, 362);
* attachments listed and saved (req 347, 348): one predicate for listing and extraction,
  bytes kept out of every receipt, a single-use token for the device's fetch.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.confirmation_gate import CONFIRM_SOURCE_VOICE, Confirmation
from app.briefing.models import BriefingPreferencesRow
from app.briefing.service import BriefingService, get_preferences_row
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.mail.attachment_fetch import (
    AttachmentFetchStore,
    get_attachment_fetch_store,
    set_attachment_fetch_store,
)
from app.mail.models import DRAFT_KIND_REPLY, MailDraftRow, MailIndexRow
from app.mail.poller import MIN_POLL_INTERVAL_S, MailPoller
from app.mail.providers import (
    ImapMailProvider,
    MailMessage,
    attachment_from_rfc822,
    message_from_rfc822,
)
from app.mail.service import MAX_REFERENCES, MailService
from app.operator.models import ObjectFocusRow
from app.security.step_up import TIER_SENSITIVE, tier_of
from app.voice.intents import Intent, resolve_intent
from tests.alarms_support import FakeDeviceAction
from tests.artifacts_support import file_fetch_ok
from tests.mail_calendar_support import (
    FakeImapServer,
    _ImapMessage,
    build_fake_mail_provider,
    build_fake_mail_sender,
    load_truth,
)

TRUTH = load_truth()["mail"]
MAILBOX = Path(__file__).resolve().parents[1] / "fixtures" / "mail_calendar" / "mailbox.json"
NOW = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        MailIndexRow.__table__,
        MailDraftRow.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
        BriefingPreferencesRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _service() -> MailService:
    return MailService(build_fake_mail_provider(), build_fake_mail_sender())


class _Store:
    """The object store's three verbs, in memory."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.objects[key] = data

    def exists(self, key: str) -> bool:
        return key in self.objects

    def get(self, key: str) -> bytes:
        return self.objects[key]


@pytest.fixture()
def fetch_store():
    original = get_attachment_fetch_store()
    store = AttachmentFetchStore()
    set_attachment_fetch_store(store)
    yield store
    set_attachment_fetch_store(original)


# ------------------------------------------------------------------ References (343, 346)


def test_a_sent_reply_carries_the_originals_references_chain(db) -> None:
    service = _service()
    service.read(db, target="Ali")
    draft = service.draft_reply(db, body="Yarın 10'da uygunum.", target="current")
    service.read_draft(db, session_id="sess-1", turn=1)
    confirmation = Confirmation(
        source=CONFIRM_SOURCE_VOICE, session_id="sess-1", turn=2, owner_intent_ok=True
    )
    result = service.send(
        db, draft_id=draft["draft"]["id"], host_flag_enabled=True, confirmation=confirmation
    )
    assert result["execution_status"] == "executed", result
    sent = service._sender.sent  # type: ignore[attr-defined]
    assert len(sent) == 1
    expected = TRUTH["reply_to_latest_ali"]
    assert sent[0].in_reply_to == expected["in_reply_to"]
    assert list(sent[0].references) == expected["references"]


class _DeepProvider:
    def __init__(self, references: tuple[str, ...]) -> None:
        self._references = references

    def get_message(self, message_id: str) -> MailMessage:
        return MailMessage(
            message_id=message_id,
            uid="1",
            folder="INBOX",
            from_name="Ali",
            from_email="ali@example.com",
            to=("alp@example.com",),
            references=self._references,
        )


def test_a_hundred_deep_thread_keeps_its_most_recent_links_ending_in_the_original() -> None:
    chain = tuple(f"<r{i}@example.com>" for i in range(100))
    service = MailService(_DeepProvider(chain), None)  # type: ignore[arg-type]
    row = SimpleNamespace(kind=DRAFT_KIND_REPLY, in_reply_to="<orig@example.com>")
    got = service._reply_references(row)  # type: ignore[arg-type]
    assert len(got) == MAX_REFERENCES
    assert got[-1] == "<orig@example.com>"
    assert got[-2] == "<r99@example.com>"


def test_an_unreadable_original_still_references_the_message_it_answers() -> None:
    class _Broken:
        def get_message(self, message_id: str) -> None:
            raise OSError("imap down")

    service = MailService(_Broken(), None)  # type: ignore[arg-type]
    row = SimpleNamespace(kind=DRAFT_KIND_REPLY, in_reply_to="<orig@example.com>")
    assert service._reply_references(row) == ("<orig@example.com>",)  # type: ignore[arg-type]


# ------------------------------------------------------------------------- polling (360)


def test_poll_indexes_the_inbox_once_and_a_second_poll_is_quiet(db) -> None:
    service = _service()
    first = service.poll(db, now=NOW)
    assert first["status"] == "polled"
    assert first["new"] == TRUTH["counts"]["INBOX"]
    assert first["new_unread"] == TRUTH["unread_inbox"]
    indexed = db.execute(select(MailIndexRow)).scalars().all()
    assert len(indexed) == TRUTH["counts"]["INBOX"]

    second = service.poll(db, now=NOW + timedelta(minutes=5))
    assert second["new"] == 0 and second["new_unread"] == 0
    assert len(db.execute(select(MailIndexRow)).scalars().all()) == len(indexed)
    polls = [
        r
        for r in db.execute(select(ActivityEventRow)).scalars().all()
        if (r.action or "") == "mail.poll"
    ]
    assert len(polls) == 1, "a quiet poll must not write a ledger row"


class _SinceIsADay:
    """IMAP's SINCE is a DATE, not a moment: a real server hands back everything from that day
    again on the next poll. The poll's own "already indexed" check is what keeps it quiet."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def list_messages(self, folder, *, limit=50, since=None):
        return self._inner.list_messages(folder, limit=limit, since=None)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_a_poll_over_an_overlapping_answer_counts_nothing_twice(db) -> None:
    service = MailService(_SinceIsADay(build_fake_mail_provider()), build_fake_mail_sender())
    first = service.poll(db, now=NOW)
    assert first["new"] == TRUTH["counts"]["INBOX"]
    second = service.poll(db, now=NOW + timedelta(minutes=5))
    assert second["seen"] == TRUTH["counts"]["INBOX"], (
        "the provider really answered with the overlap"
    )
    assert second["new"] == 0 and second["new_unread"] == 0


def test_poll_without_an_account_is_a_quiet_no_op(db) -> None:
    assert MailService(None, None).poll(db, now=NOW)["status"] == "no_account"
    assert db.execute(select(ActivityEventRow)).scalars().all() == []


class _CountingService:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def poll(self, session, *, now):
        self.calls += 1
        if self.fail:
            raise OSError("imap down")
        return {"status": "polled", "new": 0, "new_unread": 0}


def test_the_poller_throttles_to_its_interval_and_clamps_a_tiny_setting() -> None:
    service = _CountingService()
    poller = MailPoller(service, enabled=True, interval_s=1)
    assert poller.interval_s == MIN_POLL_INTERVAL_S
    poller.tick(None, now=NOW)  # type: ignore[arg-type]
    poller.tick(None, now=NOW + timedelta(seconds=10))  # type: ignore[arg-type]
    assert service.calls == 1
    poller.tick(None, now=NOW + timedelta(seconds=MIN_POLL_INTERVAL_S))  # type: ignore[arg-type]
    assert service.calls == 2


def test_a_failing_poll_is_retried_at_the_next_interval_not_every_tick() -> None:
    service = _CountingService(fail=True)
    poller = MailPoller(service, enabled=True, interval_s=300)
    assert poller.tick(None, now=NOW)["status"] == "failed"  # type: ignore[arg-type]
    poller.tick(None, now=NOW + timedelta(seconds=10))  # type: ignore[arg-type]
    assert service.calls == 1
    assert poller.health_check()["last_result"]["status"] == "failed"


def test_a_disabled_poller_never_polls() -> None:
    service = _CountingService()
    poller = MailPoller(service, enabled=False, interval_s=300)
    assert poller.tick(None, now=NOW)["status"] == "disabled"  # type: ignore[arg-type]
    assert service.calls == 0


def test_the_app_puts_the_poller_on_the_routine_clock() -> None:
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    assert app.state.mail_poller is not None
    assert "mail" in [name for name, _ in app.state.routine_clock._sub_ticks()]


# ------------------------------------------------------------------ briefing (278, 362)


def _sections(result: dict) -> dict:
    return result["observed_after"]["server"]["sections"]


def test_the_briefing_names_the_unread_mail(db) -> None:
    result = BriefingService().build(
        db, settings=Settings(), live={"mail_service": _service()}, now=NOW
    )
    mail = _sections(result)["mail"]
    assert f"{TRUTH['unread_inbox']} okunmamış" in mail
    assert mail in result["speech"]


def test_the_mail_clause_is_the_owners_to_switch_off(db) -> None:
    row = get_preferences_row(db)
    row.include_mail = False
    db.commit()
    result = BriefingService().build(
        db, settings=Settings(), live={"mail_service": _service()}, now=NOW
    )
    assert "mail" not in _sections(result)


def test_no_account_is_an_absent_clause_never_a_guess(db) -> None:
    result = BriefingService().build(
        db, settings=Settings(), live={"mail_service": MailService(None, None)}, now=NOW
    )
    assert "mail" not in _sections(result)
    assert "posta hesabı" not in result["speech"]


# ------------------------------------------------------------------ attachments (347, 348)


def test_every_fixture_attachment_carries_bytes_whose_size_it_states() -> None:
    raw = json.loads(MAILBOX.read_text(encoding="utf-8"))
    seen = 0
    for message in raw["messages"]:
        for attachment in message["attachments"]:
            data = base64.b64decode(attachment["content_b64"])
            assert len(data) == attachment["size"], attachment["filename"]
            seen += 1
    assert seen >= 5


def test_attachments_lists_the_focused_messages_files_and_never_their_bytes(db) -> None:
    service = _service()
    read = service.read(db, target="Ali")
    assert "content_b64" not in json.dumps(read, ensure_ascii=False)
    result = service.attachments(db, target="current")
    assert result["execution_status"] == "executed"
    names = [a["filename"] for a in result["attachments"]]
    assert TRUTH["latest_from_ali"]["attachment"] in names
    assert TRUTH["latest_from_ali"]["attachment"] in result["speech"]
    assert "content_b64" not in json.dumps(result, ensure_ascii=False)
    rows = db.execute(select(ActivityEventRow)).scalars().all()
    assert all("content_b64" not in json.dumps(r.detail_json or {}) for r in rows)


def test_save_attachment_hands_the_device_exactly_the_providers_bytes(db, fetch_store) -> None:
    service = _service()
    service.read(db, target="Ali")
    message_id = f"<m{TRUTH['latest_from_ali']['uid']}@fixture.example>"
    expected = service.attachment_bytes(message_id, 0)
    assert expected is not None and expected.data
    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})
    store = _Store()
    result = service.save_attachment(
        db, device, store=store, fetch_store=fetch_store, base_url="https://core.example"
    )
    assert result["execution_status"] == "executed", result
    assert result["sha256"] == hashlib.sha256(expected.data).hexdigest()
    [call] = [c for c in device.calls if c["capability"] == "file.fetch"]
    payload = call["payload"]
    assert payload["sha256"] == result["sha256"]
    assert payload["size"] == len(expected.data)
    assert payload["open"] is False
    token = payload["url"].rsplit("/", 1)[-1]
    assert payload["url"].startswith("https://core.example/v1/mail/attachments/fetch/")
    target = fetch_store.take(token)
    assert target is not None
    assert store.get(target.object_key) == expected.data
    assert fetch_store.take(token) is None, "a fetch token is single-use"


def test_save_attachment_refuses_an_index_the_message_does_not_have(db, fetch_store) -> None:
    service = _service()
    service.read(db, target="Ali")
    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})
    result = service.save_attachment(
        db, device, store=_Store(), fetch_store=fetch_store, base_url="", index=9
    )
    assert result["error_class"] == "attachment_not_found"
    assert device.calls == []


def test_save_attachment_without_a_device_refuses_before_fetching(db, fetch_store) -> None:
    service = _service()
    service.read(db, target="Ali")
    store = _Store()
    result = service.save_attachment(db, None, store=store, fetch_store=fetch_store, base_url="")
    assert result["error_class"] == "capability_missing"
    assert store.objects == {} and fetch_store.size() == 0


def _multipart(message_id: str, pdf: bytes) -> bytes:
    m = EmailMessage()
    m["Message-ID"] = message_id
    m["From"] = "Ali Yılmaz <ali.yilmaz@example.com>"
    m["To"] = "alp@example.com"
    m["Subject"] = "Ekli rapor"
    m["Date"] = "Tue, 15 Sep 2026 09:00:00 +0300"
    m.set_content("Rapor ekte.")
    m.add_attachment(pdf, maintype="application", subtype="pdf", filename="rapor.pdf")
    m.add_attachment(b"a;b\n1;2\n", maintype="text", subtype="csv", filename="veri.csv")
    return bytes(m)


def test_the_listing_and_the_extraction_agree_on_every_index() -> None:
    pdf = b"%PDF-1.4 fixture " + bytes(range(256))
    raw = _multipart("<rapor@example.com>", pdf)
    listed = message_from_rfc822(raw, uid="1", folder="INBOX", unread=True).attachments
    assert [a["filename"] for a in listed] == ["rapor.pdf", "veri.csv"]
    assert attachment_from_rfc822(raw, 0).data == pdf  # type: ignore[union-attr]
    assert attachment_from_rfc822(raw, 1).filename == "veri.csv"  # type: ignore[union-attr]
    assert attachment_from_rfc822(raw, 2) is None
    assert attachment_from_rfc822(raw, -1) is None


def test_the_imap_provider_refetches_the_attachment_bytes_by_message_id() -> None:
    pdf = b"%PDF-1.4 imap " + bytes(range(256)) * 4
    mailbox = {"INBOX": [_ImapMessage(uid=7, raw=_multipart("<rapor@example.com>", pdf))]}
    with FakeImapServer(mailbox) as server:
        provider = ImapMailProvider(
            host="127.0.0.1",
            port=server.port,
            username="owner",
            password="secret-password-never-in-a-receipt",
            use_ssl=False,
        )
        got = provider.get_attachment("<rapor@example.com>", 0)
        assert got is not None and got.data == pdf and got.filename == "rapor.pdf"
        assert provider.get_attachment("<missing@example.com>", 0) is None


def test_the_device_fetch_route_is_single_use_and_hash_checked(fetch_store) -> None:
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    store = _Store()
    app.state.artifacts = SimpleNamespace(store=store)
    data = b"attachment-bytes"
    store.put("mail/attachments/x/a.pdf", data)
    digest = hashlib.sha256(data).hexdigest()
    client = TestClient(app)

    token = fetch_store.put(
        object_key="mail/attachments/x/a.pdf",
        sha256=digest,
        filename="a.pdf",
        content_type="application/pdf",
    )
    first = client.get(f"/v1/mail/attachments/fetch/{token}")
    assert first.status_code == 200 and first.content == data
    assert client.get(f"/v1/mail/attachments/fetch/{token}").status_code == 404

    tampered = fetch_store.put(
        object_key="mail/attachments/x/a.pdf",
        sha256="0" * 64,
        filename="a.pdf",
        content_type="application/pdf",
    )
    response = client.get(f"/v1/mail/attachments/fetch/{tampered}")
    assert response.status_code == 404 and response.content in (b"", b'{"detail":"Not Found"}')
    assert client.get("/v1/mail/attachments/fetch/unknown").status_code == 404


def test_the_owner_download_is_owner_gated() -> None:
    from app.main import create_app

    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/v1/mail/attachments", params={"message_id": "<m104@x>"})
    assert response.status_code in (401, 403)


def test_the_owner_downloads_the_attachment_bytes_with_a_safe_name() -> None:
    from app.main import create_app
    from tests.identity_support import authenticate

    settings = Settings(_env_file=None)
    app = create_app(settings)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    service = _service()
    app.state.mail_service = service
    message_id = f"<m{TRUTH['latest_from_ali']['uid']}@fixture.example>"
    expected = service.attachment_bytes(message_id, 0)
    assert expected is not None

    response = client.get("/v1/mail/attachments", params={"message_id": message_id, "index": 1})
    assert response.status_code == 200, response.text
    assert response.content == expected.data
    assert response.headers["cache-control"] == "no-store"
    assert "attachment;" in response.headers["content-disposition"]
    missing = client.get("/v1/mail/attachments", params={"message_id": message_id, "index": 99})
    assert missing.status_code == 404


def test_a_hostile_attachment_name_cannot_leave_the_downloads_folder() -> None:
    assert MailService._safe_attachment_name("..\\..\\Windows\\evil.exe") == "evil.exe"
    assert MailService._safe_attachment_name("../../etc/passwd") == "passwd"
    assert MailService._safe_attachment_name("") == "attachment"
    assert "/" not in MailService._safe_attachment_name("a/b:c*?.pdf")


def test_a_fetch_token_expires() -> None:
    store = AttachmentFetchStore(ttl_s=600)
    token = store.put(object_key="k", sha256="s", filename="f", content_type="c", now=NOW)
    assert store.take(token, now=NOW + timedelta(seconds=601)) is None


def test_the_fetch_store_is_bounded() -> None:
    store = AttachmentFetchStore(max_entries=3)
    for i in range(10):
        store.put(object_key=f"k{i}", sha256="s", filename="f", content_type="c", now=NOW)
    assert store.size() == 3


# ------------------------------------------------------------------------- voice


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Bu mailin eklerini göster.", Intent.MAIL_ATTACHMENTS),
        ("Ekte ne var?", Intent.MAIL_ATTACHMENTS),
        ("Eki bilgisayarıma kaydet.", Intent.MAIL_SAVE_ATTACHMENT),
        ("Bu maildeki eki indir.", Intent.MAIL_SAVE_ATTACHMENT),
    ],
)
def test_attachment_sentences_route(text: str, intent: Intent) -> None:
    assert resolve_intent(text).intent is intent


@pytest.mark.parametrize(
    "text",
    ["Ekip toplantısı ne zaman?", "Ekranı göster.", "Listeye süt ekle.", "Maillerime bak."],
)
def test_words_that_merely_start_with_ek_do_not_route_to_attachments(text: str) -> None:
    assert resolve_intent(text).intent not in (
        Intent.MAIL_ATTACHMENTS,
        Intent.MAIL_SAVE_ATTACHMENT,
    )


def test_attachment_tools_are_sensitive() -> None:
    assert tier_of("mail.attachments") == TIER_SENSITIVE
    assert tier_of("mail.save_attachment") == TIER_SENSITIVE


def test_the_voice_tools_list_and_save_through_the_real_registry(fetch_store) -> None:
    from tests.voice_corpus.corpus import CTX_MESSAGE_FOCUSED
    from tests.voice_corpus.harness import build_harness

    h = build_harness()
    h.seed(CTX_MESSAGE_FOCUSED)
    sid = h.new_session()
    listed = h.tool(sid, "c-1", "mail.attachments", {})
    assert listed["status"] == "succeeded", listed
    assert TRUTH["latest_from_ali"]["attachment"] in listed["result"]["speech"]

    saved = h.tool(sid, "c-2", "mail.save_attachment", {"index": 1})
    assert saved["status"] == "succeeded", saved
    assert saved["result"]["execution_status"] == "executed", saved
    assert any(c["capability"] == "file.fetch" for c in h.device.calls)
