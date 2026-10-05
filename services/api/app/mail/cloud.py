"""Gmail API and Microsoft Graph behind the same ``MailProvider``/``MailSender`` Protocols
(card mail-accounts-connect).

A LISTING (``list_messages``, ``search``) reads headers and the provider's own preview only -
Gmail ``format=metadata`` + ``snippet``, Graph ``$select`` with ``bodyPreview`` - never the
message itself: a voice question over three accounts must not download up to 150 messages,
attachments included (inspector, 3rd return). Reading ONE message (``get_message``,
``thread``, an attachment) fetches it raw (Gmail ``format=raw``, Graph ``/$value``) and hands
it to ``app.mail.providers.message_from_rfc822`` - the ONE bounded parser the IMAP provider
already uses, so header decoding, the MIME depth/part bounds and the 32 KB body bound are the
same for every account. Every reader keeps ONE ``httpx.Client`` for its life
(``app.accounts.wiring`` keeps one reader per account). Sending assembles the MIME with
``build_email_message`` and posts it (Gmail ``messages/send`` with ``raw``, Graph
``sendMail`` with a base64 MIME body).

``token`` is a callable returning a current access token (``app.accounts.service.
AccountsService.access_token`` behind it, refreshing as needed); nothing here stores or logs
a token. A provider error raises ``MailApiError`` carrying the HTTP status only - never the
response body, which may echo a header back.
"""

from __future__ import annotations

import base64
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any

import httpx

from app.mail.providers import (
    MAX_LIST_MESSAGES,
    DraftInput,
    MailAttachment,
    MailMessage,
    _bounded,
    _decode_header_value,
    attachment_from_rfc822,
    build_email_message,
    message_from_rfc822,
)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GRAPH_API = "https://graph.microsoft.com/v1.0/me"


class MailApiError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"mail api answered HTTP {status}")
        self.status = status


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _thread_key(subject: str) -> str:
    # The same reduction ``message_from_rfc822`` applies.
    return subject.removeprefix("Re: ").removeprefix("RE: ").strip() or subject


def _header_date(value: str) -> datetime | None:
    try:
        return parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None


def _iso_date(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


#: The headers a Gmail listing asks for - everything a summary, a reply and the index need.
_GMAIL_LIST_HEADERS = ("From", "To", "Cc", "Subject", "Date", "Message-ID", "In-Reply-To",
                       "References")  # fmt: skip
#: The fields a Graph listing selects - the same, plus its own preview.
_GRAPH_LIST_SELECT = (
    "id,isRead,subject,from,toRecipients,ccRecipients,receivedDateTime,internetMessageId,"
    "bodyPreview,hasAttachments,conversationId"
)


class _Api:
    def __init__(
        self,
        *,
        token: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._token = token
        self._transport = transport
        self._timeout = timeout
        self._http: httpx.Client | None = None
        self._http_lock = threading.Lock()
        #: M1 parity with ImapMailProvider: messages the LAST read skipped as unparseable.
        self.last_unparseable_count = 0

    def _client(self) -> httpx.Client:
        with self._http_lock:
            if self._http is None or self._http.is_closed:
                self._http = httpx.Client(transport=self._transport, timeout=self._timeout)
            return self._http

    def close(self) -> None:
        with self._http_lock:
            if self._http is not None:
                self._http.close()
                self._http = None

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._token()}"
        response = self._client().request(method, url, headers=headers, **kwargs)
        if response.status_code >= 300:
            raise MailApiError(response.status_code)
        return response

    def _json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self._request("GET", url, params=params).json()
        return data if isinstance(data, dict) else {}

    def _parse(self, raw: bytes, *, uid: str, folder: str, unread: bool) -> MailMessage | None:
        try:
            return message_from_rfc822(raw, uid=uid, folder=folder, unread=unread)
        except Exception:  # noqa: BLE001 - one bad message never takes the listing down
            self.last_unparseable_count += 1
            return None


# --------------------------------------------------------------------------- Gmail


class GmailApiMailProvider(_Api):
    def _raw(self, gmail_id: str) -> tuple[bytes, list[str], str]:
        data = self._json(f"{GMAIL_API}/messages/{gmail_id}", {"format": "raw"})
        return (
            _b64url_decode(str(data.get("raw") or "")),
            list(data.get("labelIds") or []),
            str(data.get("threadId") or ""),
        )

    def _messages(self, ids: list[str], folder: str | None) -> list[MailMessage]:
        self.last_unparseable_count = 0
        out: list[MailMessage] = []
        for gmail_id in ids:
            raw, labels, _thread = self._raw(gmail_id)
            where = folder or ("INBOX" if "INBOX" in labels else (labels[0] if labels else ""))
            message = self._parse(raw, uid=gmail_id, folder=where, unread="UNREAD" in labels)
            if message is not None:
                out.append(message)
        return out

    def _summary(self, gmail_id: str, folder: str | None) -> MailMessage | None:
        """Headers + Gmail's snippet - a listing never downloads the message."""
        data = self._json(
            f"{GMAIL_API}/messages/{gmail_id}",
            {"format": "metadata", "metadataHeaders": list(_GMAIL_LIST_HEADERS)},
        )
        payload = data.get("payload") or {}
        headers: dict[str, str] = {}
        for header in payload.get("headers") or []:
            name = str(header.get("name") or "").lower()
            if name and name not in headers:
                headers[name] = _decode_header_value(str(header.get("value") or ""))
        labels = list(data.get("labelIds") or [])
        where = folder or ("INBOX" if "INBOX" in labels else (labels[0] if labels else ""))
        from_name, from_email = parseaddr(headers.get("from", ""))
        subject = headers.get("subject", "")
        return MailMessage(
            message_id=headers.get("message-id") or f"<{gmail_id}@{where}>",
            uid=gmail_id,
            folder=where,
            from_name=from_name,
            from_email=from_email,
            to=tuple(a for _n, a in getaddresses([headers.get("to", "")]) if a),
            cc=tuple(a for _n, a in getaddresses([headers.get("cc", "")]) if a),
            subject=subject,
            date=_header_date(headers.get("date", "")),
            unread="UNREAD" in labels,
            body_text=_bounded(str(data.get("snippet") or "")),
            has_attachments=str(payload.get("mimeType") or "") == "multipart/mixed",
            in_reply_to=headers.get("in-reply-to") or None,
            references=tuple(headers.get("references", "").split()),
            thread_key=_thread_key(subject),
        )

    def _summaries(self, ids: list[str], folder: str | None) -> list[MailMessage]:
        self.last_unparseable_count = 0
        out: list[MailMessage] = []
        for gmail_id in ids:
            try:
                message = self._summary(gmail_id, folder)
            except MailApiError:
                raise
            except Exception:  # noqa: BLE001 - one bad message never takes the listing down
                self.last_unparseable_count += 1
                continue
            if message is not None:
                out.append(message)
        return out

    def _ids(self, params: dict[str, Any]) -> list[str]:
        data = self._json(f"{GMAIL_API}/messages", params)
        return [str(m["id"]) for m in data.get("messages") or [] if m.get("id")]

    def folders(self) -> list[str]:
        data = self._json(f"{GMAIL_API}/labels")
        return [str(label.get("name")) for label in data.get("labels") or [] if label.get("name")]

    def list_messages(
        self, folder: str, *, limit: int = MAX_LIST_MESSAGES, since: datetime | None = None
    ) -> list[MailMessage]:
        params: dict[str, Any] = {
            "labelIds": folder.upper() if folder.upper() == "INBOX" else folder,
            "maxResults": max(1, min(limit, MAX_LIST_MESSAGES)),
        }
        if since is not None:
            params["q"] = f"after:{int(since.timestamp())}"
        return self._summaries(self._ids(params), folder)

    def search(self, query: str, *, limit: int = MAX_LIST_MESSAGES) -> list[MailMessage]:
        if not query.strip():
            return []
        ids = self._ids({"q": query, "maxResults": max(1, min(limit, MAX_LIST_MESSAGES))})
        return self._summaries(ids, None)

    def _gmail_id(self, message_id: str) -> str | None:
        ids = self._ids({"q": f"rfc822msgid:{message_id.strip('<>')}", "maxResults": 1})
        return ids[0] if ids else None

    def get_message(self, message_id: str) -> MailMessage | None:
        gmail_id = self._gmail_id(message_id)
        found = self._messages([gmail_id], None) if gmail_id else []
        return found[0] if found else None

    def get_attachment(self, message_id: str, index: int) -> MailAttachment | None:
        gmail_id = self._gmail_id(message_id)
        if gmail_id is None:
            return None
        raw, _labels, _thread = self._raw(gmail_id)
        return attachment_from_rfc822(raw, index)

    def thread(self, message_id: str) -> list[MailMessage]:
        gmail_id = self._gmail_id(message_id)
        if gmail_id is None:
            return []
        meta = self._json(f"{GMAIL_API}/messages/{gmail_id}", {"format": "minimal"})
        thread_id = str(meta.get("threadId") or "")
        if not thread_id:
            return self._messages([gmail_id], None)
        data = self._json(f"{GMAIL_API}/threads/{thread_id}", {"format": "minimal"})
        ids = [str(m["id"]) for m in data.get("messages") or [] if m.get("id")]
        messages = self._messages(ids[:MAX_LIST_MESSAGES], None)
        return sorted(messages, key=lambda m: m.date.timestamp() if m.date else 0.0)


class GmailApiMailSender(_Api):
    def __init__(self, *, mail_from: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._mail_from = mail_from

    def send(self, draft: DraftInput) -> str:
        msg = build_email_message(draft, self._mail_from)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
        self._request("POST", f"{GMAIL_API}/messages/send", json={"raw": raw})
        return msg.get("Message-ID") or ""


# ------------------------------------------------------------------- Microsoft Graph

#: The owner's folder words -> Graph's well-known folder names.
_GRAPH_FOLDERS = {"INBOX": "inbox", "SENT": "sentitems", "DRAFTS": "drafts", "ARCHIVE": "archive"}


def _odata_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class GraphMailProvider(_Api):
    def _mime(self, graph_id: str) -> bytes:
        return self._request("GET", f"{GRAPH_API}/messages/{graph_id}/$value").content

    def _messages(self, items: list[dict[str, Any]], folder: str) -> list[MailMessage]:
        self.last_unparseable_count = 0
        out: list[MailMessage] = []
        for item in items:
            graph_id = str(item.get("id") or "")
            if not graph_id:
                continue
            message = self._parse(
                self._mime(graph_id),
                uid=graph_id,
                folder=folder,
                unread=not item.get("isRead", True),
            )
            if message is not None:
                out.append(message)
        return out

    @staticmethod
    def _summary(item: dict[str, Any], folder: str) -> MailMessage:
        """A listing item as a message - Graph's selected fields, never the MIME."""

        def address(entry: Any) -> tuple[str, str]:
            box = (entry or {}).get("emailAddress") or {}
            return (
                _decode_header_value(str(box.get("name") or "")),
                _decode_header_value(str(box.get("address") or "")),
            )

        graph_id = str(item.get("id") or "")
        subject = _decode_header_value(str(item.get("subject") or ""))
        from_name, from_email = address(item.get("from"))
        return MailMessage(
            message_id=_decode_header_value(str(item.get("internetMessageId") or ""))
            or f"<{graph_id}@{folder}>",
            uid=graph_id,
            folder=folder,
            from_name=from_name,
            from_email=from_email,
            to=tuple(a for _n, a in map(address, item.get("toRecipients") or []) if a),
            cc=tuple(a for _n, a in map(address, item.get("ccRecipients") or []) if a),
            subject=subject,
            date=_iso_date(str(item.get("receivedDateTime") or "")),
            unread=not item.get("isRead", True),
            body_text=_bounded(str(item.get("bodyPreview") or "")),
            has_attachments=bool(item.get("hasAttachments")),
            thread_key=_thread_key(subject),
        )

    def _summaries(self, items: list[dict[str, Any]], folder: str) -> list[MailMessage]:
        self.last_unparseable_count = 0
        out: list[MailMessage] = []
        for item in items:
            if not item.get("id"):
                continue
            try:
                out.append(self._summary(item, folder))
            except Exception:  # noqa: BLE001 - one bad item never takes the listing down
                self.last_unparseable_count += 1
        return out

    def _find(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self._json(f"{GRAPH_API}/messages", params).get("value") or [])

    def folders(self) -> list[str]:
        data = self._json(f"{GRAPH_API}/mailFolders", {"$select": "displayName"})
        return [str(f.get("displayName")) for f in data.get("value") or [] if f.get("displayName")]

    def list_messages(
        self, folder: str, *, limit: int = MAX_LIST_MESSAGES, since: datetime | None = None
    ) -> list[MailMessage]:
        well_known = _GRAPH_FOLDERS.get(folder.upper(), folder)
        params: dict[str, Any] = {
            "$top": max(1, min(limit, MAX_LIST_MESSAGES)),
            "$select": _GRAPH_LIST_SELECT,
            "$orderby": "receivedDateTime desc",
        }
        if since is not None:
            params["$filter"] = f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        data = self._json(f"{GRAPH_API}/mailFolders/{well_known}/messages", params)
        return self._summaries(list(data.get("value") or []), folder)

    def search(self, query: str, *, limit: int = MAX_LIST_MESSAGES) -> list[MailMessage]:
        needle = query.strip().replace('"', " ")
        if not needle:
            return []
        items = self._find(
            {
                "$search": f'"{needle}"',
                "$top": max(1, min(limit, MAX_LIST_MESSAGES)),
                "$select": _GRAPH_LIST_SELECT,
            }
        )
        return self._summaries(items, "INBOX")

    def _by_message_id(self, message_id: str) -> dict[str, Any] | None:
        items = self._find(
            {
                "$filter": f"internetMessageId eq {_odata_quote(message_id)}",
                "$select": "id,isRead,conversationId",
                "$top": 1,
            }
        )
        return items[0] if items else None

    def get_message(self, message_id: str) -> MailMessage | None:
        item = self._by_message_id(message_id)
        found = self._messages([item], "INBOX") if item else []
        return found[0] if found else None

    def get_attachment(self, message_id: str, index: int) -> MailAttachment | None:
        item = self._by_message_id(message_id)
        if item is None:
            return None
        return attachment_from_rfc822(self._mime(str(item["id"])), index)

    def thread(self, message_id: str) -> list[MailMessage]:
        item = self._by_message_id(message_id)
        if item is None:
            return []
        conversation = str(item.get("conversationId") or "")
        if not conversation:
            return self._messages([item], "INBOX")
        items = self._find(
            {
                "$filter": f"conversationId eq {_odata_quote(conversation)}",
                "$select": "id,isRead",
                "$top": MAX_LIST_MESSAGES,
            }
        )
        messages = self._messages(items, "INBOX")
        return sorted(messages, key=lambda m: m.date.timestamp() if m.date else 0.0)


class GraphMailSender(_Api):
    def __init__(self, *, mail_from: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._mail_from = mail_from

    def send(self, draft: DraftInput) -> str:
        msg = build_email_message(draft, self._mail_from)
        self._request(
            "POST",
            f"{GRAPH_API}/sendMail",
            content=base64.b64encode(msg.as_bytes()),
            headers={"Content-Type": "text/plain"},
        )
        return msg.get("Message-ID") or ""


__all__ = [
    "GmailApiMailProvider",
    "GmailApiMailSender",
    "GraphMailProvider",
    "GraphMailSender",
    "MailApiError",
]
