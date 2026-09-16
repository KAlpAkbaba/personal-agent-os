"""Mail's voice tools (docs/M21_MAIL_CALENDAR_SPEC.md §3, §4, ADR-0084). Nine tools,
registered from ``tools.default_registry()`` by ONE added line
(:func:`register_mail_tools`), the same discipline ``tools_documents``/``tools_operator``
already establish for their own families.

No delete, no move, no mass action (ADR-0084 decision 2): "Tüm mailleri sil" reaches none
of these — the corpus proves it. Every result is a receipt-shaped dict whose ``speech`` is
read verbatim; a target that cannot be resolved is an honest ``needs_clarification``,
never a guess. ``mail.send`` reaches the real ``MailSender`` ONLY after
``app.actions.confirmation_gate.check_gate`` says yes — see ``app.mail.service.MailService.
send``'s own docstring.

``MailService`` comes from ``ctx.live`` (``mail_service``) — ``app.main.create_app``
registers the real service on the SAME object every request shares; a test injects a fake
the same way (docs/M18_ACTION_CONTRACT.md §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.actions.confirmation_gate import CONFIRM_SOURCE_VOICE, Confirmation
from app.mail.service import MailService
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import Intent

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_MAIL_INBOX: Final = "mail.inbox"
TOOL_MAIL_SEARCH: Final = "mail.search"
TOOL_MAIL_READ: Final = "mail.read"
TOOL_MAIL_THREAD: Final = "mail.thread"
TOOL_MAIL_DRAFT: Final = "mail.draft"
TOOL_MAIL_EDIT_DRAFT: Final = "mail.edit_draft"
TOOL_MAIL_READ_DRAFT: Final = "mail.read_draft"
TOOL_MAIL_SEND: Final = "mail.send"
TOOL_MAIL_DISCARD: Final = "mail.discard"
#: B45 (req 347, 348): a message's attachments, listed and saved.
TOOL_MAIL_ATTACHMENTS: Final = "mail.attachments"
TOOL_MAIL_SAVE_ATTACHMENT: Final = "mail.save_attachment"

MAIL_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_MAIL_INBOX,
    TOOL_MAIL_SEARCH,
    TOOL_MAIL_READ,
    TOOL_MAIL_THREAD,
    TOOL_MAIL_DRAFT,
    TOOL_MAIL_EDIT_DRAFT,
    TOOL_MAIL_READ_DRAFT,
    TOOL_MAIL_SEND,
    TOOL_MAIL_DISCARD,
    TOOL_MAIL_ATTACHMENTS,
    TOOL_MAIL_SAVE_ATTACHMENT,
)


def _service(ctx: ToolContext, tool: str) -> MailService:
    service = ctx.live.get("mail_service")
    if service is None:
        raise VoiceError(VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the mail service")
    return service


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    """The ONE router's record of this turn (``mail_ref`` — the owner's WORDS, preferred
    over the model's own ``target`` argument, the same rule the document family follows)."""
    return dict(ctx.context.get("last_utterance") or {})


def _target(ctx: ToolContext, arguments: dict[str, Any], *, default: str = "current") -> str:
    turn = _turn_record(ctx)
    ref = turn.get("mail_ref")
    if isinstance(ref, str) and ref:
        return ref
    raw = arguments.get("target")
    return str(raw) if isinstance(raw, str) and raw else default


# --------------------------------------------------------------------- READ tools


def mail_inbox(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Gelen kutumda ne var?" / "Okunmamış maillerim var mı?" (spec §3)."""
    db = _require_db(ctx, TOOL_MAIL_INBOX)
    service = _service(ctx, TOOL_MAIL_INBOX)
    folder = str(arguments.get("folder") or "INBOX")
    return service.inbox_summary(db, folder=folder, session_id=str(ctx.session_id))


def mail_search(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Fatura maillerini bul." (spec §3)."""
    db = _require_db(ctx, TOOL_MAIL_SEARCH)
    service = _service(ctx, TOOL_MAIL_SEARCH)
    query = str(arguments.get("query") or "")
    if not query:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "mail.search needs a query")
    return service.search(db, query, session_id=str(ctx.session_id))


def mail_read(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Ali'den gelen son maili oku." (spec §3)."""
    db = _require_db(ctx, TOOL_MAIL_READ)
    service = _service(ctx, TOOL_MAIL_READ)
    return service.read(db, target=_target(ctx, arguments), session_id=str(ctx.session_id))


def mail_thread(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu konuşmanın tamamını oku." (spec §3)."""
    db = _require_db(ctx, TOOL_MAIL_THREAD)
    service = _service(ctx, TOOL_MAIL_THREAD)
    return service.thread(db, target=_target(ctx, arguments), session_id=str(ctx.session_id))


# ------------------------------------------------------------------ PREPARE tools


def mail_draft(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Buna cevap yaz: ..." / "Yeni mail: ..." (spec §3) — ONE tool, two shapes: a
    REPLY (``mail_ref`` resolved to "current" by the router) or a NEW mail (an explicit
    ``to``). The body/subject text is the model's own argument (module docstring)."""
    db = _require_db(ctx, TOOL_MAIL_DRAFT)
    service = _service(ctx, TOOL_MAIL_DRAFT)
    body = str(arguments.get("body") or "")
    if not body:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "mail.draft needs a body")
    turn = _turn_record(ctx)
    is_reply = turn.get("intent") == "mail_draft_reply" or bool(arguments.get("in_reply_to"))
    if is_reply or not arguments.get("to"):
        return service.draft_reply(
            db, body=body, target=_target(ctx, arguments), session_id=str(ctx.session_id)
        )
    return service.draft_new(
        db,
        to=str(arguments.get("to") or ""),
        subject=str(arguments.get("subject") or ""),
        body=body,
        session_id=str(ctx.session_id),
    )


def mail_edit_draft(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Konuyu 'Plan onayı' yap." (spec §3)."""
    db = _require_db(ctx, TOOL_MAIL_EDIT_DRAFT)
    service = _service(ctx, TOOL_MAIL_EDIT_DRAFT)
    subject = arguments.get("subject")
    body = arguments.get("body")
    to = arguments.get("to")
    return service.edit_draft(
        db,
        subject=str(subject) if isinstance(subject, str) else None,
        body=str(body) if isinstance(body, str) else None,
        to=str(to) if isinstance(to, str) else None,
        session_id=str(ctx.session_id),
    )


def mail_read_draft(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Cevabı oku." (spec §3) — the EXPLICIT read-back act (H1): binds
    ``read_back_session_id``/``read_back_turn`` to THIS session/turn so a later
    confirmation can be checked against them (``app.actions.confirmation_gate``)."""
    del arguments
    db = _require_db(ctx, TOOL_MAIL_READ_DRAFT)
    service = _service(ctx, TOOL_MAIL_READ_DRAFT)
    turn = _turn_record(ctx).get("turn")
    return service.read_draft(
        db,
        session_id=str(ctx.session_id),
        turn=turn if isinstance(turn, int) else None,
    )


# ---------------------------------------------------------- EXTERNAL MUTATION tools


def mail_send(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Gönder." (spec §3) — reaches the real ``MailSender`` ONLY after the confirmation
    gate says yes; with nothing prepared, or the gate refusing, the sender is never
    touched (module docstring).

    H1 (ADR-0084 addendum 2): the gate does not trust that this handler was called at
    all as proof of the owner's word — a model can call any tool it likes on its own
    initiative, or steered by a hostile document it just read. The claim this call makes
    is built HERE, from the ONE router's own resolution of the CURRENT turn
    (``ctx.context["last_utterance"]``), never from the mere fact of being dispatched:
    ``owner_intent_ok`` is true only when the router itself resolved this turn to
    ``MAIL_SEND`` — the same field the persona is instructed never to call this tool
    without (module docstring's own ASLA)."""
    del arguments
    db = _require_db(ctx, TOOL_MAIL_SEND)
    service = _service(ctx, TOOL_MAIL_SEND)
    settings = ctx.live.get("settings")
    host_flag = bool(getattr(settings, "mail_send_enabled", False))
    turn = _turn_record(ctx)
    confirmation = Confirmation(
        source=CONFIRM_SOURCE_VOICE,
        session_id=str(ctx.session_id),
        turn=turn.get("turn") if isinstance(turn.get("turn"), int) else None,
        owner_intent_ok=turn.get("intent") == Intent.MAIL_SEND,
    )
    return service.send(
        db,
        host_flag_enabled=host_flag,
        session_id=str(ctx.session_id),
        confirmation=confirmation,
    )


def mail_discard(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Gönderme." / "Vazgeç." (spec §3) — the current prepared draft."""
    del arguments
    db = _require_db(ctx, TOOL_MAIL_DISCARD)
    service = _service(ctx, TOOL_MAIL_DISCARD)
    return service.discard(db, session_id=str(ctx.session_id))


# ------------------------------------------------------------------ registration


# ---------------------------------------------------------- ATTACHMENTS (B45)


def mail_attachments(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu mailin eklerini göster." (B45 req 347)."""
    db = _require_db(ctx, TOOL_MAIL_ATTACHMENTS)
    service = _service(ctx, TOOL_MAIL_ATTACHMENTS)
    return service.attachments(db, target=_target(ctx, arguments), session_id=str(ctx.session_id))


def mail_save_attachment(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Eki bilgisayarıma kaydet." (B45 req 348): ``index`` is the 1-based number the owner
    heard in the listing (the first when unsaid)."""
    db = _require_db(ctx, TOOL_MAIL_SAVE_ATTACHMENT)
    service = _service(ctx, TOOL_MAIL_SAVE_ATTACHMENT)
    runtime = ctx.live.get("artifacts_runtime")
    if runtime is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, "mail.save_attachment needs the object store"
        )
    from app.mail.attachment_fetch import get_attachment_fetch_store

    raw_index = arguments.get("index")
    index = int(raw_index) if isinstance(raw_index, int | float) and raw_index >= 1 else 1
    return service.save_attachment(
        db,
        ctx.live.get("device_action"),
        store=runtime.store,
        fetch_store=get_attachment_fetch_store(),
        base_url=getattr(runtime.settings, "artifact_download_origin", "") or "",
        target=_target(ctx, arguments),
        index=index,
        session_id=str(ctx.session_id),
    )


def register_mail_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all nine tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_MAIL_INBOX,
            description=(
                "Gelen kutusu ÖZETİ verir: 'Gelen kutumda ne var?', 'Okunmamış "
                "maillerim var mı?'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"folder": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=mail_inbox,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_SEARCH,
            description=(
                "Mail kutusunda ARAR: 'Fatura maillerini bul'. 'query' alanına sahibin "
                "aradığı şeyi aynen ver. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=mail_search,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_READ,
            description=(
                "Bir MAİLİ OKUR: 'Ali'den gelen son maili oku'. Hangi mail olduğunu "
                "SUNUCU çözer (odaktaki mail, ya da bir isim söylenmişse 'target' alanına "
                "o ismi aynen ver). Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=mail_read,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_THREAD,
            description=(
                "Bir KONUŞMANIN TAMAMINI okur: 'Bu konuşmanın tamamını oku'. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=mail_thread,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_DRAFT,
            description=(
                "Bir mail TASLAĞI hazırlar (GÖNDERMEZ): 'Buna cevap yaz: ...' (odaktaki "
                "maile bir CEVAP - 'to' alanını BOŞ bırak) ya da 'Yeni mail: Ayşe'ye, "
                "konu ..., ...' (YENİ bir mail - 'to' alanına alıcıyı ver). 'body' alanına "
                "sahibin söylediği mesaj metnini aynen ver; 'subject' sadece yeni mail "
                "için. Taslak GÖNDERİLMEZ - sadece hazırlanır ve okunur. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "maxLength": 320},
                    "subject": {"type": "string", "maxLength": 200},
                    "body": {"type": "string", "maxLength": 4000},
                },
                "additionalProperties": False,
            },
            handler=mail_draft,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_EDIT_DRAFT,
            description=(
                "ODAKTAKİ TASLAĞI düzenler: 'Konuyu Plan onayı yap.'. Sadece söylenen "
                "alanı ver ('subject'/'body'/'to'). Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "maxLength": 200},
                    "body": {"type": "string", "maxLength": 4000},
                    "to": {"type": "string", "maxLength": 320},
                },
                "additionalProperties": False,
            },
            handler=mail_edit_draft,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_READ_DRAFT,
            description=(
                "ODAKTAKİ TASLAĞI aynen okur: 'Cevabı oku'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=mail_read_draft,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_SEND,
            description=(
                "ODAKTAKİ TASLAĞI GÖNDERİR - GERÇEK bir dış etkidir, SADECE taslak "
                "sahibe okunduktan SONRA sahip 'Gönder.' dediğinde çağrılır. Sahip taslağı "
                "duymadan bunu ASLA çağırma. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=mail_send,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_DISCARD,
            description=(
                "ODAKTAKİ TASLAĞI siler (göndermeden vazgeçer): 'Gönderme.', 'Vazgeç.'. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=mail_discard,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_ATTACHMENTS,
            description=(
                "ODAKTAKİ mailin EKLERİNİ listeler (ad, tür, boyut): 'bu mailin eklerini göster', "
                "'ekte ne var'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=mail_attachments,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MAIL_SAVE_ATTACHMENT,
            description=(
                "ODAKTAKİ mailin bir EKİNİ sahibin bilgisayarına (İndirilenler) KAYDEDER: 'eki "
                "bilgisayarıma kaydet', 'ikinci eki indir' (index=2). 'index' listede duyulan "
                "sıra numarası. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 1, "maximum": 100},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=mail_save_attachment,
        )
    )
    return reg


__all__ = [
    "MAIL_TOOL_NAMES",
    "TOOL_MAIL_DISCARD",
    "TOOL_MAIL_DRAFT",
    "TOOL_MAIL_EDIT_DRAFT",
    "TOOL_MAIL_INBOX",
    "TOOL_MAIL_READ",
    "TOOL_MAIL_READ_DRAFT",
    "TOOL_MAIL_SEARCH",
    "TOOL_MAIL_SEND",
    "TOOL_MAIL_THREAD",
    "mail_discard",
    "mail_draft",
    "mail_edit_draft",
    "mail_inbox",
    "mail_read",
    "mail_read_draft",
    "mail_search",
    "mail_send",
    "mail_thread",
    "register_mail_tools",
]
