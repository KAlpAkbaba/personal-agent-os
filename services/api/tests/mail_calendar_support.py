"""Shared fixtures for the M21 Mail & Calendar test suites (docs/M21_MAIL_CALENDAR_SPEC.md
§4, ADR-0084).

Loads the oracle (``tests/fixtures/mail_calendar/{mailbox.json,calendar.ics,truth.json}``)
into the FAKE providers ``app.mail.providers``/``app.calendar.providers`` already define —
never a second copy of that data. Also provides two SCRIPTED PROTOCOL SERVERS on a loopback
socket (``FakeImapServer``, ``FakeSmtpServer``) so ``ImapMailProvider``/``SmtpMailSender``
are proven against the real wire protocol, not just against the fake's in-memory objects —
the parsing they do is what a test proves (spec §4). Both bind ``127.0.0.1`` on an
ephemeral port and never touch a network.
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from dataclasses import dataclass, field
from email import message_from_bytes
from email import policy as email_policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from app.calendar.providers import FakeCalendarProvider, FakeCalendarWriter
from app.mail.providers import FakeMailProvider, FakeMailSender

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "mail_calendar"
MAILBOX_PATH = FIXTURES_DIR / "mailbox.json"
CALENDAR_PATH = FIXTURES_DIR / "calendar.ics"
TRUTH_PATH = FIXTURES_DIR / "truth.json"


def load_truth() -> dict[str, Any]:
    return json.loads(TRUTH_PATH.read_text(encoding="utf-8"))


def build_fake_mail_provider() -> FakeMailProvider:
    return FakeMailProvider(MAILBOX_PATH)


def build_fake_mail_sender() -> FakeMailSender:
    return FakeMailSender()


def build_fake_calendar_provider() -> FakeCalendarProvider:
    return FakeCalendarProvider(CALENDAR_PATH)


def build_fake_calendar_writer() -> FakeCalendarWriter:
    return FakeCalendarWriter()


# ------------------------------------------------------------- fake IMAP4 server


@dataclass
class _ImapMessage:
    uid: int
    raw: bytes
    seen: bool = True


def _rfc822(
    *,
    message_id: str,
    from_header: str,
    to_header: str,
    subject: str,
    date: str,
    body: str,
    content_type: str = "text/plain; charset=utf-8",
    in_reply_to: str | None = None,
    references: str | None = None,
) -> bytes:
    lines = [
        f"Message-ID: {message_id}",
        f"From: {from_header}",
        f"To: {to_header}",
        f"Subject: {subject}",
        f"Date: {date}",
        "MIME-Version: 1.0",
        f"Content-Type: {content_type}",
        "Content-Transfer-Encoding: 8bit",
    ]
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        lines.append(f"References: {references}")
    lines.append("")
    lines.append(body)
    return ("\r\n".join(lines)).encode("utf-8")


#: A small SYNTHETIC mailbox built specifically to exercise the wire protocol: an
#: RFC-2047-encoded Turkish subject (``=?UTF-8?B?...?=``), an HTML-only body reduced to
#: text, a plain-text body, and a message findable by FROM/TEXT search — never the full
#: 37-message fixture (that oracle is proven at the service layer through
#: :class:`app.mail.providers.FakeMailProvider`, which loads it directly; this server
#: proves the PROVIDER's own wire parsing, a different claim).
def default_imap_fixture() -> dict[str, list[_ImapMessage]]:
    # "Eylül 2026 elektrik faturanız", RFC 2047 base64-encoded.
    encoded_subject = "=?UTF-8?B?RXlsw7xsIDIwMjYgZWxla3RyaWsgZmF0dXJhbsSxeg==?="
    html_body = (
        "<html><body><p>Sayın Alp Akbaba,</p>"
        "<p>Eylül 2026 dönemi elektrik faturanız <b>1.284,50 TL</b> olarak düzenlenmiştir.</p>"
        "</body></html>"
    )
    inbox = [
        _ImapMessage(
            uid=201,
            raw=_rfc822(
                message_id="<t201@fixture.example>",
                from_header="Ali Yılmaz <ali.yilmaz@example.com>",
                to_header="Alp Akbaba <alp@example.com>",
                subject=encoded_subject,
                date="Tue, 08 Sep 2026 10:00:00 +0300",
                body="Merhaba, ekte fatura var.",
            ),
            seen=False,
        ),
        _ImapMessage(
            uid=202,
            raw=_rfc822(
                message_id="<t202@fixture.example>",
                from_header="Fatura Servisi <fatura@ornekenerji.example>",
                to_header="Alp Akbaba <alp@example.com>",
                subject="Eylul faturaniz hazir",
                date="Wed, 09 Sep 2026 08:00:00 +0300",
                body="",
                content_type="text/html; charset=utf-8",
            ),
            seen=True,
        ),
        _ImapMessage(
            uid=203,
            raw=_rfc822(
                message_id="<t203@fixture.example>",
                from_header="Zeynep Arslan <zeynep.arslan@example.com>",
                to_header="Alp Akbaba <alp@example.com>",
                subject="Merhaba",
                date="Wed, 09 Sep 2026 09:00:00 +0300",
                body="Nasılsın?",
            ),
            seen=True,
        ),
    ]
    # The HTML-only message's body is written directly (no plain-text alternative) so the
    # provider's own html_to_text reduction is what a test proves.
    inbox[1] = _ImapMessage(
        uid=202,
        raw=_rfc822(
            message_id="<t202@fixture.example>",
            from_header="Fatura Servisi <fatura@ornekenerji.example>",
            to_header="Alp Akbaba <alp@example.com>",
            subject="Eylul faturaniz hazir",
            date="Wed, 09 Sep 2026 08:00:00 +0300",
            body=html_body,
            content_type="text/html; charset=utf-8",
        ),
        seen=True,
    )
    return {"INBOX": inbox, "Gönderilmiş": [], "Arşiv": []}


class _ImapHandler(socketserver.StreamRequestHandler):
    server: FakeImapServer

    def handle(self) -> None:
        self._send("* OK IMAP4rev1 fake ready")
        selected: str | None = None
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip("\r\n")
            if not text:
                continue
            parts = text.split(" ", 2)
            if len(parts) < 2:
                continue
            tag, cmd = parts[0], parts[1].upper()
            rest = parts[2] if len(parts) > 2 else ""
            if cmd == "CAPABILITY":
                self._send("* CAPABILITY IMAP4rev1")
                self._send(f"{tag} OK CAPABILITY completed")
            elif cmd == "LOGIN":
                self._send(f"{tag} OK LOGIN completed")
            elif cmd == "LIST":
                for folder in self.server.mailbox:
                    self._send(f'* LIST (\\HasNoChildren) "/" "{folder}"')
                self._send(f"{tag} OK LIST completed")
            elif cmd in ("SELECT", "EXAMINE"):
                folder = rest.strip().strip('"')
                selected = folder
                msgs = self.server.mailbox.get(folder, [])
                self._send(f"* {len(msgs)} EXISTS")
                self._send("* 0 RECENT")
                self._send(f"{tag} OK [READ-ONLY] {cmd} completed")
            elif cmd == "UID":
                self._handle_uid(tag, rest, selected)
            elif cmd == "LOGOUT":
                self._send("* BYE logging out")
                self._send(f"{tag} OK LOGOUT completed")
                return
            else:
                self._send(f"{tag} BAD unrecognised command")

    def _handle_uid(self, tag: str, rest: str, selected: str | None) -> None:
        sub_parts = rest.split(" ", 1)
        subcmd = sub_parts[0].upper()
        subrest = sub_parts[1] if len(sub_parts) > 1 else ""
        msgs = self.server.mailbox.get(selected or "", [])
        if subcmd == "SEARCH":
            uids = self._search(msgs, subrest)
            self._send("* SEARCH " + " ".join(str(u) for u in uids))
            self._send(f"{tag} OK UID SEARCH completed")
        elif subcmd == "FETCH":
            uid_str = subrest.split(" ", 1)[0]
            uid = int(uid_str)
            msg = next((m for m in msgs if m.uid == uid), None)
            if msg is None:
                self._send(f"{tag} NO not found")
                return
            flags = "\\Seen" if msg.seen else ""
            header = f"* 1 FETCH (UID {uid} FLAGS ({flags}) RFC822 {{{len(msg.raw)}}}"
            self.wfile.write(header.encode("utf-8") + b"\r\n")
            self.wfile.write(msg.raw)
            self.wfile.write(b")\r\n")
            self.wfile.flush()
            self._send(f"{tag} OK UID FETCH completed")
        else:
            self._send(f"{tag} BAD unrecognised UID subcommand")

    def _search(self, msgs: list[_ImapMessage], criteria: str) -> list[int]:
        upper = criteria.upper()
        # "CHARSET UTF-8 <rest>" — the charset token itself carries no filtering meaning.
        if upper.startswith("CHARSET"):
            _, _, criteria = criteria.partition(" ")
            criteria = criteria.split(" ", 1)[1] if " " in criteria else ""
            upper = criteria.upper()
        if upper.startswith("UNSEEN"):
            return [m.uid for m in msgs if not m.seen]
        if upper.startswith("FROM"):
            needle = criteria.split('"')[1].lower() if '"' in criteria else ""
            return [m.uid for m in msgs if needle in m.raw.decode("utf-8", "replace").lower()]
        if upper.startswith("TEXT"):
            needle = criteria.split('"')[1].lower() if '"' in criteria else ""
            return [m.uid for m in msgs if needle in m.raw.decode("utf-8", "replace").lower()]
        if upper.startswith("HEADER"):
            # "HEADER MESSAGE-ID "<id>""
            needle = criteria.split('"')[1] if '"' in criteria else ""
            return [m.uid for m in msgs if needle.encode("utf-8") in m.raw]
        return [m.uid for m in msgs]  # ALL

    def _send(self, line: str) -> None:
        self.wfile.write(line.encode("utf-8") + b"\r\n")
        self.wfile.flush()


class FakeImapServer(socketserver.ThreadingTCPServer):
    """A scripted fake IMAP4 server on ``127.0.0.1`` (an ephemeral port) — the socket-level
    FETCH/SEARCH grammar an :class:`app.mail.providers.ImapMailProvider` speaks, so its own
    parsing is what a test proves (spec §4)."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, mailbox: dict[str, list[_ImapMessage]] | None = None) -> None:
        super().__init__(("127.0.0.1", 0), _ImapHandler)
        self.mailbox = mailbox if mailbox is not None else default_imap_fixture()

    def __enter__(self) -> FakeImapServer:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
        self.server_close()

    @property
    def port(self) -> int:
        return self.server_address[1]


# -------------------------------------------------------------- fake SMTP server


@dataclass
class FakeSmtpServer:
    """A scripted fake SMTP server on ``127.0.0.1`` (an ephemeral port): accepts EHLO/
    STARTTLS(no-op)/MAIL/RCPT/DATA and records every message it received — the message
    NEVER leaves this process (spec §4)."""

    received: list[EmailMessage] = field(default_factory=list)
    _sock: socket.socket | None = None
    _thread: threading.Thread | None = None
    _port: int = 0
    _stop: bool = False

    def __enter__(self) -> FakeSmtpServer:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self._port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop = True
        try:
            # Unblock accept() with a dummy connection.
            socket.create_connection(("127.0.0.1", self._port), timeout=1).close()
        except OSError:
            pass
        if self._sock is not None:
            self._sock.close()

    @property
    def port(self) -> int:
        return self._port

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                self._handle(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def _handle(self, conn: socket.socket) -> None:
        rfile = conn.makefile("rb")
        wfile = conn.makefile("wb")

        def send(line: str) -> None:
            wfile.write((line + "\r\n").encode("ascii"))
            wfile.flush()

        send("220 fake.smtp ready")
        data_lines: list[bytes] = []
        in_data = False
        while True:
            line = rfile.readline()
            if not line:
                return
            if self._stop:
                return
            if in_data:
                if line.strip() == b".":
                    in_data = False
                    raw = b"".join(data_lines)
                    parsed = message_from_bytes(raw, policy=email_policy.default)
                    self.received.append(parsed)  # type: ignore[arg-type]
                    send("250 OK message queued")
                    data_lines = []
                    continue
                data_lines.append(line.replace(b"\r\n", b"\n"))
                continue
            text = line.decode("ascii", errors="replace").strip()
            upper = text.upper()
            if upper.startswith("EHLO") or upper.startswith("HELO"):
                send("250-fake.smtp")
                send("250 STARTTLS")
            elif upper.startswith("STARTTLS"):
                send("220 go ahead")
                # Deliberately no real TLS handshake — a loopback test fake (module
                # docstring); the provider's own STARTTLS call simply proceeds in plaintext.
            elif upper.startswith("MAIL FROM"):
                send("250 OK")
            elif upper.startswith("RCPT TO"):
                send("250 OK")
            elif upper.startswith("DATA"):
                send("354 send data")
                in_data = True
            elif upper.startswith("QUIT"):
                send("221 bye")
                return
            else:
                send("250 OK")


__all__ = [
    "CALENDAR_PATH",
    "FIXTURES_DIR",
    "MAILBOX_PATH",
    "TRUTH_PATH",
    "FakeImapServer",
    "FakeSmtpServer",
    "build_fake_calendar_provider",
    "build_fake_calendar_writer",
    "build_fake_mail_provider",
    "build_fake_mail_sender",
    "default_imap_fixture",
    "load_truth",
]
