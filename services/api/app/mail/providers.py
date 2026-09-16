"""Mail providers behind Protocols (docs/M21_MAIL_CALENDAR_SPEC.md §2, ADR-0084 decision 3).

``MailProvider``/``MailSender`` are the whole surface ``app.mail.service.MailService``
ever touches — a third-party mail account stays fully replaceable (CLAUDE.md: "Third-party
services must be behind provider interfaces"). ``ImapMailProvider`` (stdlib ``imaplib``)
and ``SmtpMailSender`` (stdlib ``smtplib``) are the real implementations; ``FakeMailProvider``/
``FakeMailSender`` serve the deterministic fixture mailbox for tests and the corpus.
Production never imports the fakes (``app.main.create_app`` only ever builds the real
providers, from settings, or ``None`` when no account is configured).

No real account, no real send, ever, in this checkout: ``FakeMailSender.send`` writes to
an in-memory list the test/harness reads back — it never opens a socket.
"""

from __future__ import annotations

import imaplib
import json
import re
import smtplib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

#: spec §2 bounds: at most 50 messages per list/search, a body bounded to 32 KB.
MAX_LIST_MESSAGES = 50
MAX_BODY_BYTES = 32 * 1024
#: B45 (req 348): the largest attachment the Cloud Core fetches to hand to the device.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
#: M1 (security review): ``email.message.Message.walk()`` recurses one Python stack frame
#: per MIME nesting level with no bound of its own - a ~3000-level nested multipart
#: message raised ``RecursionError`` there, taking the whole folder's fetch/search/thread
#: loop down with it (verified live). ``_iter_parts_bounded`` below is an ITERATIVE
#: (explicit-stack) drop-in for ``walk()`` that never recurses and gives up past these
#: bounds rather than growing the call stack without limit.
MIME_MAX_DEPTH = 32
MIME_MAX_PARTS = 200


# --------------------------------------------------------------------------- shapes


@dataclass(frozen=True, slots=True)
class MailMessage:
    """What ``get_message``/``list_messages``/``search``/``thread`` return — headers, a
    bounded text body, and attachment METADATA only (never the bytes; spec §2)."""

    message_id: str  # the provider's own Message-ID header: the identity mail_index keys on
    uid: str
    folder: str
    from_name: str
    from_email: str
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    subject: str = ""
    date: datetime | None = None
    unread: bool = False
    body_text: str = ""
    has_attachments: bool = False
    attachments: tuple[dict[str, Any], ...] = ()
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    thread_key: str = ""

    @property
    def snippet(self) -> str:
        return " ".join(self.body_text.split())[:200]

    def as_summary(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "folder": self.folder,
            "from_name": self.from_name,
            "from_email": self.from_email,
            "subject": self.subject,
            "date": self.date.isoformat() if self.date else None,
            "unread": self.unread,
            "snippet": self.snippet,
            "has_attachments": self.has_attachments,
            "thread_key": self.thread_key,
        }

    def as_full(self) -> dict[str, Any]:
        return {
            **self.as_summary(),
            "to": list(self.to),
            "cc": list(self.cc),
            "body_text": self.body_text,
            "attachments": [dict(a) for a in self.attachments],
            "in_reply_to": self.in_reply_to,
            "references": list(self.references),
        }


@dataclass(frozen=True, slots=True)
class DraftInput:
    """Exactly what ``MailSender.send`` needs — assembled by ``MailService`` from a
    ``MailDraftRow`` at send time, never a live ORM row (the sender must not depend on
    the database)."""

    kind: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()


# ------------------------------------------------------------------------ protocols


@dataclass(frozen=True, slots=True)
class MailAttachment:
    """B45 (req 347, 348): one attachment's bytes, fetched only when the owner asks to keep
    it - never part of a listing, a receipt or a ledger row."""

    filename: str
    content_type: str
    data: bytes

    @property
    def sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self.data).hexdigest()


class MailProvider(Protocol):
    def folders(self) -> list[str]: ...

    def list_messages(
        self, folder: str, *, limit: int = MAX_LIST_MESSAGES, since: datetime | None = None
    ) -> list[MailMessage]: ...

    def search(self, query: str, *, limit: int = MAX_LIST_MESSAGES) -> list[MailMessage]: ...

    def get_message(self, message_id: str) -> MailMessage | None: ...

    def get_attachment(self, message_id: str, index: int) -> MailAttachment | None: ...

    def thread(self, message_id: str) -> list[MailMessage]: ...


class MailSender(Protocol):
    def send(self, draft: DraftInput) -> str:
        """Sends ``draft`` and returns the provider's own Message-ID for the sent mail."""
        ...


# --------------------------------------------------------------- HTML -> text reduction


class _TextExtractor(HTMLParser):
    """A minimal, dependency-free HTML-to-text reduction (spec §2: "HTML bodies reduced
    to text") — good enough for a notification-shaped email body, never a renderer."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in ("p", "br", "div", "li", "tr"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        lines = [ln.strip() for ln in joined.splitlines()]
        return "\n".join(ln for ln in lines if ln).strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html or "")
    return parser.text()


#: L1 (security review): a folded header's continuation ("\r\n " per RFC 5322 §2.2.3) is
#: a legitimate long-header device; a bare CR/LF with no fold is not — either way, once
#: this parser hands a value back it must never itself carry a line break a caller could
#: mistake for a header boundary (a reply draft's own "To:" built from a poisoned
#: "From:", the ValueError ``smtplib``/``email`` raise on a header containing one).
_HEADER_LINE_BREAKS = re.compile(r"[\r\n]+")


def _sanitize_header(value: str) -> str:
    """Unfold to a single space (module constant's own docstring) and drop any NUL —
    the ONE place every header value this module hands back passes through, so a
    header-injection attempt surviving into a later mutation is neutralised at the read,
    not hoped to be caught at every later write site."""
    return _HEADER_LINE_BREAKS.sub(" ", value.replace("\x00", "")).strip()


#: L1 (security review): a bounded, deliberately permissive address shape check — this is
#: NOT full RFC 5322 address grammar (that is `email.utils.parseaddr`'s own job below), it
#: only rejects what would make a recipient dangerous to a header/SMTP envelope: no
#: whitespace, no control character, exactly one ``@`` with something on both sides, a
#: length ``smtplib``/RFC 5321 would accept.
_ADDRESS_SHAPE = re.compile(r"^[^\s@\x00-\x1f]+@[^\s@\x00-\x1f]+\.[^\s@\x00-\x1f]+$")
_MAX_ADDRESS_LEN = 320  # RFC 5321 §4.5.3.1.3


def is_valid_email_address(value: str) -> bool:
    """``email.utils.parseaddr`` (module docstring's own reference) plus a bounded shape
    check — used when a draft is CREATED (``app.mail.service.MailService.draft_reply``/
    ``draft_new``) so a recipient a hostile "From:" header injected (L1: CR/LF folded
    into an address the reply's ``to`` inherited) is refused before it is ever assembled
    into an outgoing message, rather than discovered only when ``smtplib`` raises."""
    if not value or len(value) > _MAX_ADDRESS_LEN:
        return False
    _, addr = parseaddr(value)
    if not addr or addr != value.strip():
        return False
    return bool(_ADDRESS_SHAPE.match(addr))


def _decode_header_value(raw: str | None) -> str:
    """RFC 2047 header decoding (spec §2) — "=?UTF-8?B?...?=" -> the real Turkish text."""
    if not raw:
        return ""
    try:
        parts = decode_header(raw)
    except Exception:  # noqa: BLE001 - a malformed header degrades to its raw text
        return _sanitize_header(raw)
    out: list[str] = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(charset or "utf-8", errors="replace"))
            except (LookupError, ValueError):
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return _sanitize_header("".join(out))


def _bounded(text: str, limit: int = MAX_BODY_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def _iter_parts_bounded(
    msg: EmailMessage | Any,
    *,
    max_depth: int = MIME_MAX_DEPTH,
    max_parts: int = MIME_MAX_PARTS,
) -> list[Any]:
    """A bounded, ITERATIVE drop-in for ``Message.walk()`` (M1, module constants' own
    docstring): same pre-order (parent before children, siblings in order) DFS
    ``Message.walk()`` performs, but with an explicit stack instead of a Python call per
    nesting level, so no message can ever grow the interpreter's own call stack. A part
    beyond ``max_depth`` is yielded but not descended into (its own children are never
    visited); the walk stops entirely once ``max_parts`` parts have been visited — a
    hostile ~3000-level nested multipart message costs at most ``max_parts`` bits of work,
    never a ``RecursionError`` that takes the whole fetch/search/thread loop down with it.
    """
    parts: list[Any] = []
    stack: list[tuple[Any, int]] = [(msg, 0)]
    while stack and len(parts) < max_parts:
        part, depth = stack.pop()
        parts.append(part)
        if depth >= max_depth:
            continue
        if part.is_multipart():
            children = part.get_payload()
            if isinstance(children, list):
                stack.extend((child, depth + 1) for child in reversed(children))
    return parts


def _is_attachment_part(part: Any) -> bool:
    """ONE predicate for "this part is an attachment" (B45): the listing below and
    :func:`attachment_from_rfc822` both use it, so the index the owner hears is the index
    the extraction reaches - two copies of this condition would drift."""
    disposition = str(part.get("Content-Disposition") or "")
    content_type = part.get_content_type()
    return "attachment" in disposition or bool(
        part.get_filename() and content_type not in ("text/plain", "text/html")
    )


def attachment_from_rfc822(raw: bytes, index: int) -> MailAttachment | None:
    """B45 (req 347, 348): the ``index``-th attachment part of one full RFC822 message,
    decoded, bounded by :data:`MAX_ATTACHMENT_BYTES`; None when there is no such part."""
    if index < 0:
        return None
    msg = message_from_bytes(raw, policy=policy.compat32)
    if not msg.is_multipart():
        return None
    seen = 0
    for part in _iter_parts_bounded(msg):
        if not _is_attachment_part(part):
            continue
        if seen == index:
            data = part.get_payload(decode=True) or b""
            if len(data) > MAX_ATTACHMENT_BYTES:
                return None
            return MailAttachment(
                filename=_decode_header_value(part.get_filename()) or "attachment",
                content_type=part.get_content_type(),
                data=data,
            )
        seen += 1
    return None


def message_from_rfc822(raw: bytes, *, uid: str, folder: str, unread: bool) -> MailMessage:
    """Parse one full RFC822 message (an IMAP ``BODY[]``/``RFC822`` fetch result) into a
    :class:`MailMessage` — the ONE place header decoding and body reduction happen, so the
    IMAP provider's own parsing is what a test proves (spec §4)."""
    msg = message_from_bytes(raw, policy=policy.compat32)
    from_name, from_email = "", ""
    from_header = _decode_header_value(msg.get("From"))
    if "<" in from_header and from_header.endswith(">"):
        from_name, from_email = from_header.rsplit("<", 1)
        from_name = from_name.strip().strip('"')
        from_email = from_email[:-1].strip()
    else:
        from_email = from_header.strip()

    def _addr_list(header: str) -> tuple[str, ...]:
        raw_value = _decode_header_value(msg.get(header))
        if not raw_value:
            return ()
        out: list[str] = []
        for part in raw_value.split(","):
            part = part.strip()
            if "<" in part and part.endswith(">"):
                out.append(part.rsplit("<", 1)[1][:-1].strip())
            elif part:
                out.append(part)
        return tuple(out)

    body_text = ""
    attachments: list[dict[str, Any]] = []
    if msg.is_multipart():
        plain_part = None
        html_part = None
        for part in _iter_parts_bounded(msg):
            content_type = part.get_content_type()
            if _is_attachment_part(part):
                payload = part.get_payload(decode=True) or b""
                attachments.append(
                    {
                        "filename": part.get_filename() or "attachment",
                        "size": len(payload),
                        "content_type": content_type,
                    }
                )
                continue
            if content_type == "text/plain" and plain_part is None:
                plain_part = part
            elif content_type == "text/html" and html_part is None:
                html_part = part
        if plain_part is not None:
            payload = plain_part.get_payload(decode=True) or b""
            body_text = payload.decode(
                plain_part.get_content_charset() or "utf-8", errors="replace"
            )
        elif html_part is not None:
            payload = html_part.get_payload(decode=True) or b""
            body_text = html_to_text(
                payload.decode(html_part.get_content_charset() or "utf-8", errors="replace")
            )
    else:
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        body_text = html_to_text(text) if content_type == "text/html" else text

    date_hdr = msg.get("Date")
    date_value: datetime | None = None
    if date_hdr:
        try:
            date_value = parsedate_to_datetime(date_hdr)
        except (TypeError, ValueError):
            date_value = None

    references_raw = _decode_header_value(msg.get("References"))
    references = tuple(r for r in references_raw.split() if r) if references_raw else ()
    in_reply_to = _decode_header_value(msg.get("In-Reply-To")) or None
    message_id = _decode_header_value(msg.get("Message-ID")) or f"<{uid}@{folder}>"
    subject = _decode_header_value(msg.get("Subject"))
    thread_key = subject.removeprefix("Re: ").removeprefix("RE: ").strip() or subject

    return MailMessage(
        message_id=message_id,
        uid=uid,
        folder=folder,
        from_name=from_name,
        from_email=from_email,
        to=_addr_list("To"),
        cc=_addr_list("Cc"),
        subject=subject,
        date=date_value,
        unread=unread,
        body_text=_bounded(body_text),
        has_attachments=bool(attachments),
        attachments=tuple(attachments),
        in_reply_to=in_reply_to,
        references=references,
        thread_key=thread_key,
    )


# ------------------------------------------------------------------------- IMAP


class ImapMailProvider:
    """stdlib ``imaplib`` (spec §2): ``IMAP4_SSL`` by default, or plain ``IMAP4`` for a
    host that only speaks STARTTLS/plaintext on a private network (the test's own scripted
    fake server on loopback). ``SEARCH CHARSET UTF-8`` throughout; every header decoded
    through :func:`_decode_header_value`; the body reduced through
    :func:`message_from_rfc822` — the SAME function a real server and the fake server's
    bytes both flow through, so nothing here has a second, untested code path."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        use_ssl: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_ssl = use_ssl
        self._timeout = timeout
        #: M1 (security review): how many messages the LAST list_messages/search/thread
        #: call skipped because they failed to parse — never raised, never silently
        #: dropped either: ``app.mail.service.MailService`` reads this right after calling
        #: a read method and folds it into the receipt/speech ("N ileti okunamadı"). A
        #: plain instance attribute rather than a return-value change because
        #: ``MailProvider`` is a ``Protocol`` every fake also implements; changing the
        #: return shape would ripple through every caller for a count that is zero on the
        #: overwhelmingly common path.
        self.last_unparseable_count = 0

    def _connect(self) -> imaplib.IMAP4:
        conn: imaplib.IMAP4
        if self._use_ssl:
            conn = imaplib.IMAP4_SSL(self._host, self._port, timeout=self._timeout)
        else:
            conn = imaplib.IMAP4(self._host, self._port, timeout=self._timeout)
        conn.login(self._username, self._password)
        return conn

    def folders(self) -> list[str]:
        conn = self._connect()
        try:
            typ, data = conn.list()
            names: list[str] = []
            if typ == "OK":
                for entry in data:
                    if not entry:
                        continue
                    text = (
                        entry.decode("utf-8", errors="replace")
                        if isinstance(entry, bytes)
                        else str(entry)
                    )
                    # '(\\HasNoChildren) "/" "INBOX"' — the mailbox name is the last quoted token.
                    if '"' in text:
                        names.append(text.rsplit('"', 2)[-2])
                    else:
                        names.append(text.rsplit(" ", 1)[-1])
            return names
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

    def _fetch_uids(
        self, conn: imaplib.IMAP4, folder: str, criteria: list[str], *, limit: int
    ) -> list[MailMessage]:
        # imaplib's own ``_command`` ascii-encodes any ``str`` argument and raises on a
        # non-ASCII one (a Turkish folder name, "Gönderilmiş"; a Turkish search term) —
        # passing UTF-8 BYTES instead skips that encode step entirely (imaplib appends
        # bytes args verbatim), which is exactly ``SEARCH CHARSET UTF-8``'s own promise.
        typ, _ = conn.select(folder.encode("utf-8"), readonly=True)
        if typ != "OK":
            return []
        encoded_criteria = [c.encode("utf-8") if isinstance(c, str) else c for c in criteria]
        typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *encoded_criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        uids = uids[-limit:] if limit else uids
        out: list[MailMessage] = []
        for raw_uid in uids:
            uid = raw_uid.decode("ascii") if isinstance(raw_uid, bytes) else str(raw_uid)
            typ, fetch_data = conn.uid("FETCH", uid, "(FLAGS RFC822)")
            if typ != "OK" or not fetch_data:
                continue
            raw_bytes = b""
            flags_blob = b""
            for part in fetch_data:
                if isinstance(part, tuple):
                    flags_blob += part[0]
                    raw_bytes = part[1]
            unread = b"\\Seen" not in flags_blob
            # M1 (security review): one message's parse is isolated from every other's —
            # a single poisoned message (pathological MIME nesting, a malformed header)
            # must never take the rest of the folder down with it. Skipped and counted,
            # never silently dropped: ``self.last_unparseable_count`` (reset by the public
            # caller) is what ``MailService`` folds into the receipt/speech.
            try:
                out.append(message_from_rfc822(raw_bytes, uid=uid, folder=folder, unread=unread))
            except Exception:  # noqa: BLE001 - isolate one bad message, never the folder
                self.last_unparseable_count += 1
                continue
        return out

    def list_messages(
        self, folder: str, *, limit: int = MAX_LIST_MESSAGES, since: datetime | None = None
    ) -> list[MailMessage]:
        self.last_unparseable_count = 0
        conn = self._connect()
        try:
            criteria = ["ALL"] if since is None else [f'SINCE "{since.strftime("%d-%b-%Y")}"']
            return self._fetch_uids(conn, folder, criteria, limit=min(limit, MAX_LIST_MESSAGES))
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def search(self, query: str, *, limit: int = MAX_LIST_MESSAGES) -> list[MailMessage]:
        self.last_unparseable_count = 0
        conn = self._connect()
        try:
            out: list[MailMessage] = []
            for folder in self.folders() or ["INBOX"]:
                out.extend(
                    self._fetch_uids(
                        conn, folder, ["TEXT", f'"{query}"'], limit=min(limit, MAX_LIST_MESSAGES)
                    )
                )
                if len(out) >= limit:
                    break
            out.sort(key=lambda m: m.date or datetime.min.replace(tzinfo=UTC), reverse=True)
            return out[:limit]
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def get_message(self, message_id: str) -> MailMessage | None:
        self.last_unparseable_count = 0
        conn = self._connect()
        try:
            for folder in self.folders() or ["INBOX"]:
                found = self._fetch_uids(
                    conn, folder, ["HEADER", "MESSAGE-ID", f'"{message_id}"'], limit=1
                )
                if found:
                    return found[0]
            return None
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def get_attachment(self, message_id: str, index: int) -> MailAttachment | None:
        """B45 (req 347, 348): the message fetched again by its Message-ID (read-only
        EXAMINE, so nothing is marked seen) and its ``index``-th attachment extracted by the
        same predicate the listing used."""
        conn = self._connect()
        try:
            for folder in self.folders() or ["INBOX"]:
                raw = self._fetch_raw(conn, folder, ["HEADER", "MESSAGE-ID", f'"{message_id}"'])
                if raw is not None:
                    return attachment_from_rfc822(raw, index)
            return None
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _fetch_raw(self, conn: imaplib.IMAP4, folder: str, criteria: list[str]) -> bytes | None:
        typ, _ = conn.select(folder.encode("utf-8"), readonly=True)
        if typ != "OK":
            return None
        encoded_criteria = [c.encode("utf-8") if isinstance(c, str) else c for c in criteria]
        typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *encoded_criteria)
        if typ != "OK" or not data or not data[0]:
            return None
        last = data[0].split()[-1]
        uid = last.decode("ascii") if isinstance(last, bytes) else str(last)
        typ, fetch_data = conn.uid("FETCH", uid, "(RFC822)")
        if typ != "OK" or not fetch_data:
            return None
        for part in fetch_data:
            if isinstance(part, tuple):
                return part[1]
        return None

    def thread(self, message_id: str) -> list[MailMessage]:
        self.last_unparseable_count = 0
        anchor = self.get_message(message_id)
        if anchor is None:
            return []
        conn = self._connect()
        try:
            out: list[MailMessage] = []
            for folder in self.folders() or ["INBOX"]:
                out.extend(self._fetch_uids(conn, folder, ["ALL"], limit=MAX_LIST_MESSAGES))
            related = [m for m in out if m.thread_key == anchor.thread_key]
            related.sort(key=lambda m: m.date or datetime.min.replace(tzinfo=UTC))
            return related
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass


# -------------------------------------------------------------------------- SMTP


class MailSendDisabledError(RuntimeError):
    """Raised by :meth:`SmtpMailSender.__init__` refusing to even construct (spec §3:
    ``send_disabled`` when ``PAGENTOS_MAIL_SEND_ENABLED`` is off) — the autonomous system
    can then never hold a live sender at all, never mind call it."""


class SmtpMailSender:
    """stdlib ``smtplib`` (spec §2): STARTTLS/TLS, UTF-8 subjects/bodies, ``In-Reply-To``/
    ``References`` on a reply. Refuses to even be constructed when the host flag is off —
    ``app.mail.service.MailService`` never holds this object as an alternative to checking
    the flag at send time; there is no live sender to call in that configuration."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        mail_from: str,
        use_tls: bool = True,
        enabled: bool,
        timeout: float = 15.0,
    ) -> None:
        if not enabled:
            raise MailSendDisabledError("PAGENTOS_MAIL_SEND_ENABLED is off")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._mail_from = mail_from
        self._use_tls = use_tls
        self._timeout = timeout

    def send(self, draft: DraftInput) -> str:
        msg = EmailMessage()
        msg["From"] = self._mail_from
        msg["To"] = ", ".join(draft.to)
        if draft.cc:
            msg["Cc"] = ", ".join(draft.cc)
        msg["Subject"] = draft.subject
        if draft.in_reply_to:
            msg["In-Reply-To"] = draft.in_reply_to
        if draft.references:
            msg["References"] = " ".join(draft.references)
        # A real server may assign its own; generating one here (RFC 5322 §3.6.4, via the
        # stdlib) means ``MailService.send`` always has SOMETHING to record as the sent
        # message's identity, never an empty string standing in for "unknown".
        domain = (self._mail_from.rsplit("@", 1)[-1]) or "pagentos.local"
        msg["Message-ID"] = make_msgid(domain=domain)
        msg.set_content(draft.body, charset="utf-8")

        conn = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        try:
            conn.ehlo()
            if self._use_tls:
                conn.starttls()
                conn.ehlo()
            if self._username:
                conn.login(self._username, self._password)
            conn.send_message(msg)
        finally:
            try:
                conn.quit()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
        return msg.get("Message-ID") or ""


# --------------------------------------------------------------------------- fakes


def _load_mailbox_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_to_message(entry: dict[str, Any]) -> MailMessage:
    date_value: datetime | None = None
    if entry.get("date"):
        date_value = datetime.fromisoformat(entry["date"])
    body_text = entry.get("body_text") or ""
    if not body_text and entry.get("body_html"):
        body_text = html_to_text(entry["body_html"])
    return MailMessage(
        message_id=entry["message_id"],
        uid=str(entry["uid"]),
        folder=entry["folder"],
        from_name=(entry.get("from") or {}).get("name", ""),
        from_email=(entry.get("from") or {}).get("email", ""),
        to=tuple(a.get("email", "") for a in entry.get("to") or []),
        cc=tuple(a.get("email", "") for a in entry.get("cc") or []),
        subject=entry.get("subject", ""),
        date=date_value,
        unread="\\Seen" not in (entry.get("flags") or []),
        body_text=_bounded(body_text),
        has_attachments=bool(entry.get("attachments")),
        attachments=tuple(
            {key: value for key, value in a.items() if key != "content_b64"}
            for a in entry.get("attachments") or []
        ),
        in_reply_to=entry.get("in_reply_to"),
        references=tuple(entry.get("references") or ()),
        thread_key=entry.get("thread") or entry.get("subject", ""),
    )


class FakeMailProvider:
    """Loads ``tests/fixtures/mail_calendar/mailbox.json`` verbatim — the deterministic
    fixture mailbox every test and the corpus's fake device use. Never imported by
    production (module docstring)."""

    def __init__(self, fixture_path: Path) -> None:
        raw = _load_mailbox_fixture(fixture_path)
        self._messages: list[MailMessage] = [_fixture_to_message(m) for m in raw["messages"]]
        #: B45 (req 347, 348): the fixture's attachment BYTES, kept apart from the metadata
        #: every listing and receipt carries (a base64 body in a read receipt is a leak).
        self._attachment_bytes: dict[tuple[str, int], bytes] = {}
        for entry in raw["messages"]:
            for index, attachment in enumerate(entry.get("attachments") or []):
                encoded = attachment.get("content_b64")
                if isinstance(encoded, str):
                    import base64

                    self._attachment_bytes[(entry["message_id"], index)] = base64.b64decode(encoded)
        self._folders: list[str] = list(raw["folders"])

    def folders(self) -> list[str]:
        return list(self._folders)

    def list_messages(
        self, folder: str, *, limit: int = MAX_LIST_MESSAGES, since: datetime | None = None
    ) -> list[MailMessage]:
        out = [m for m in self._messages if m.folder == folder]
        if since is not None:
            out = [m for m in out if m.date is not None and m.date >= since]
        out.sort(key=lambda m: m.date or datetime.min.replace(tzinfo=UTC), reverse=True)
        return out[:limit]

    def search(self, query: str, *, limit: int = MAX_LIST_MESSAGES) -> list[MailMessage]:
        needle = query.strip().lower()
        if not needle:
            return []
        out = [
            m
            for m in self._messages
            if needle in m.subject.lower()
            or needle in m.body_text.lower()
            or needle in m.from_email.lower()
            or needle in m.from_name.lower()
        ]
        out.sort(key=lambda m: m.date or datetime.min.replace(tzinfo=UTC), reverse=True)
        return out[:limit]

    def get_message(self, message_id: str) -> MailMessage | None:
        for m in self._messages:
            if m.message_id == message_id:
                return m
        return None

    def get_attachment(self, message_id: str, index: int) -> MailAttachment | None:
        """The fixture's own bytes for this attachment, or None - never invented bytes for an
        attachment the fixture does not carry."""
        message = self.get_message(message_id)
        data = self._attachment_bytes.get((message_id, index))
        if message is None or data is None or not 0 <= index < len(message.attachments):
            return None
        meta = message.attachments[index]
        return MailAttachment(
            filename=str(meta.get("filename") or "attachment"),
            content_type=str(meta.get("content_type") or "application/octet-stream"),
            data=data,
        )

    def thread(self, message_id: str) -> list[MailMessage]:
        anchor = self.get_message(message_id)
        if anchor is None:
            return []
        related = [m for m in self._messages if m.thread_key == anchor.thread_key]
        related.sort(key=lambda m: m.date or datetime.min.replace(tzinfo=UTC))
        return related


@dataclass
class FakeMailSender:
    """Records every send in-memory — reached ONLY by a confirmed ``mail.send``/REST
    confirm, and never by an autonomous test path (module docstring, ADR-0084 decision 1).
    """

    sent: list[DraftInput] = field(default_factory=list)
    _counter: int = 0

    def send(self, draft: DraftInput) -> str:
        self.sent.append(draft)
        self._counter += 1
        return f"<fake-sent-{self._counter}@fixture.example>"


# --------------------------------------------------------------------------- wiring


def build_mail_provider(settings: Any) -> MailProvider | None:
    """``ImapMailProvider`` from settings, or ``None`` with nothing configured — the
    honest ``account_missing`` production answer (spec §2) until the owner sets
    ``PAGENTOS_MAIL_IMAP_*``."""
    if not settings.mail_imap_host or not settings.mail_imap_user:
        return None
    return ImapMailProvider(
        host=settings.mail_imap_host,
        port=settings.mail_imap_port,
        username=settings.mail_imap_user,
        password=settings.mail_imap_password,
        use_ssl=settings.mail_imap_use_ssl,
    )


def build_mail_sender(settings: Any) -> MailSender | None:
    """``SmtpMailSender`` from settings, or ``None`` — either no SMTP account is
    configured, or ``PAGENTOS_MAIL_SEND_ENABLED`` is off (the sender itself refuses to be
    constructed in that case; module docstring). ``app.mail.service.MailService`` decides
    ``account_missing`` vs ``send_disabled`` from ITS OWN read-provider presence and the
    live flag value, never from whether this returned ``None``."""
    if not settings.mail_smtp_host or not settings.mail_from:
        return None
    try:
        return SmtpMailSender(
            host=settings.mail_smtp_host,
            port=settings.mail_smtp_port,
            username=settings.mail_smtp_user,
            password=settings.mail_smtp_password,
            mail_from=settings.mail_from,
            use_tls=settings.mail_smtp_use_tls,
            enabled=settings.mail_send_enabled,
        )
    except MailSendDisabledError:
        return None


__all__ = [
    "MAX_BODY_BYTES",
    "MAX_LIST_MESSAGES",
    "MIME_MAX_DEPTH",
    "MIME_MAX_PARTS",
    "DraftInput",
    "FakeMailProvider",
    "FakeMailSender",
    "ImapMailProvider",
    "MailMessage",
    "MailProvider",
    "MailSendDisabledError",
    "MailSender",
    "SmtpMailSender",
    "build_mail_provider",
    "build_mail_sender",
    "html_to_text",
    "is_valid_email_address",
    "message_from_rfc822",
]
