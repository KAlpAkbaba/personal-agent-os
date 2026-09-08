"""``SmtpMailSender`` against a scripted fake SMTP server on a loopback socket
(docs/M21_MAIL_CALENDAR_SPEC.md §2, §4, ADR-0084). The message never leaves this process;
``send_disabled`` when the host flag is off (the sender refuses to even be constructed)."""

from __future__ import annotations

import pytest

from app.mail.providers import DraftInput, MailSendDisabledError, SmtpMailSender
from tests.mail_calendar_support import FakeSmtpServer


def _sender(server: FakeSmtpServer, **overrides) -> SmtpMailSender:
    kwargs = {
        "host": "127.0.0.1",
        "port": server.port,
        "username": "",
        "password": "",
        "mail_from": "alp@example.com",
        # The fake server accepts a plaintext STARTTLS handshake (module docstring: a
        # loopback test double, never a real TLS stack) — ``use_tls=False`` here proves
        # the sender's OWN header/body composition and the confirmation-gate boundary,
        # which is the custom code this suite is responsible for; the STARTTLS wrap
        # itself is stdlib ``ssl``/``smtplib``, exercised against a real host in
        # production, not re-tested against a fake TLS peer here.
        "use_tls": False,
        "enabled": True,
    }
    kwargs.update(overrides)
    return SmtpMailSender(**kwargs)


def test_refuses_to_construct_when_the_host_flag_is_off() -> None:
    with pytest.raises(MailSendDisabledError):
        SmtpMailSender(
            host="127.0.0.1",
            port=1,
            username="",
            password="",
            mail_from="alp@example.com",
            enabled=False,
        )


def test_send_reaches_the_fake_server_only() -> None:
    with FakeSmtpServer() as server:
        sender = _sender(server)
        draft = DraftInput(
            kind="reply",
            to=("ali.yilmaz@example.com",),
            cc=(),
            subject="Re: Proje planı",
            body="Yarın 10'da uygunum.",
            in_reply_to="<m104@fixture.example>",
            references=("<m101@fixture.example>", "<m104@fixture.example>"),
        )
        message_id = sender.send(draft)
        assert message_id
        import time

        time.sleep(0.05)
        assert len(server.received) == 1
        received = server.received[0]
        assert received["To"] == "ali.yilmaz@example.com"
        assert received["Subject"] == "Re: Proje planı"
        assert received["In-Reply-To"] == "<m104@fixture.example>"
        assert "<m101@fixture.example>" in received["References"]
        assert "<m104@fixture.example>" in received["References"]
        body = received.get_content()
        assert "Yarın 10'da uygunum." in body


def test_utf8_subject_and_body_survive() -> None:
    with FakeSmtpServer() as server:
        sender = _sender(server)
        draft = DraftInput(
            kind="new",
            to=("ayse.kaya@example.com",),
            cc=(),
            subject="Toplantı",
            body="Yarın gelemiyorum, üzgünüm.",
        )
        sender.send(draft)
        import time

        time.sleep(0.05)
        received = server.received[-1]
        assert received["Subject"] == "Toplantı"
        assert "üzgünüm" in received.get_content()
