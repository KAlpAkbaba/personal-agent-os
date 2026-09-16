"""File & Document Intelligence's voice tools (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md
§3, §4). Eight tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_documents_tools`), the same discipline ``tools_operator`` already
establishes for its own family.

Until B34 there was NO delete/move/write tool here (ADR-0083 decision 7) and the corpus
proved "Bu dosyayı sil" reached none of these. B34 (req 153-167, 170, 674) adds the
managed mutations - ``document.write | append | edit | rename | move | copy | delete |
apply | discard | undo | versions`` - every one journaled and reversible through
``app.documents.mutations`` and the device's own undo store, and every risky one a
PROPOSAL the owner confirms ("Uygula.") or the Cockpit approves, never an act on the first
word. Every result is a receipt-shaped dict whose ``speech`` is read verbatim; a target
that cannot be resolved (no document focused, no previous document) is an honest
``needs_clarification``, never a guess.

``DocumentService`` and the fake/real device port both come from ``ctx.live``
(``document_service``, ``device_action``) — ``app.main.create_app`` registers the real
service on the SAME object every request shares; a test injects fakes the same way
(docs/M18_ACTION_CONTRACT.md §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.actions.confirmation_gate import CONFIRM_SOURCE_VOICE, Confirmation
from app.documents.mutations import MutationService
from app.documents.service import DocumentService
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_FILE_SEARCH: Final = "file.search"
TOOL_DOCUMENT_READ: Final = "document.read"
TOOL_DOCUMENT_SUMMARIZE: Final = "document.summarize"
TOOL_DOCUMENT_ANSWER: Final = "document.answer"
TOOL_DOCUMENT_COMPARE: Final = "document.compare"
TOOL_DOCUMENT_INSPECT: Final = "document.inspect"
TOOL_DOCUMENT_COMMON_POINTS: Final = "document.common_points"
TOOL_DOCUMENT_PREVIOUS: Final = "document.previous"

# B32 req 148/150/151/152.
TOOL_DOCUMENT_PREVIEW: Final = "document.preview"
TOOL_DOCUMENT_FIND_TEXT: Final = "document.find_text"
TOOL_DOCUMENT_DUPLICATES: Final = "document.duplicates"
TOOL_DOCUMENT_DEDUP: Final = "document.dedup"
# B34 req 153-167, 170: the managed mutations.
TOOL_DOCUMENT_WRITE: Final = "document.write"
TOOL_DOCUMENT_APPEND: Final = "document.append"
TOOL_DOCUMENT_EDIT: Final = "document.edit"
TOOL_DOCUMENT_RENAME: Final = "document.rename"
TOOL_DOCUMENT_MOVE: Final = "document.move"
TOOL_DOCUMENT_COPY: Final = "document.copy"
TOOL_DOCUMENT_DELETE: Final = "document.delete"
TOOL_DOCUMENT_APPLY: Final = "document.apply"
TOOL_DOCUMENT_DISCARD: Final = "document.discard"
TOOL_DOCUMENT_UNDO: Final = "document.undo"
TOOL_DOCUMENT_VERSIONS: Final = "document.versions"

DOCUMENT_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_FILE_SEARCH,
    TOOL_DOCUMENT_READ,
    TOOL_DOCUMENT_SUMMARIZE,
    TOOL_DOCUMENT_ANSWER,
    TOOL_DOCUMENT_COMPARE,
    TOOL_DOCUMENT_INSPECT,
    TOOL_DOCUMENT_COMMON_POINTS,
    TOOL_DOCUMENT_PREVIOUS,
    TOOL_DOCUMENT_PREVIEW,
    TOOL_DOCUMENT_FIND_TEXT,
    TOOL_DOCUMENT_DUPLICATES,
    TOOL_DOCUMENT_DEDUP,
    TOOL_DOCUMENT_WRITE,
    TOOL_DOCUMENT_APPEND,
    TOOL_DOCUMENT_EDIT,
    TOOL_DOCUMENT_RENAME,
    TOOL_DOCUMENT_MOVE,
    TOOL_DOCUMENT_COPY,
    TOOL_DOCUMENT_DELETE,
    TOOL_DOCUMENT_APPLY,
    TOOL_DOCUMENT_DISCARD,
    TOOL_DOCUMENT_UNDO,
    TOOL_DOCUMENT_VERSIONS,
)

#: The mutations, for the confirmation gate's "did the router hear a confirmation" check.
MUTATION_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_DOCUMENT_WRITE,
    TOOL_DOCUMENT_APPEND,
    TOOL_DOCUMENT_EDIT,
    TOOL_DOCUMENT_RENAME,
    TOOL_DOCUMENT_MOVE,
    TOOL_DOCUMENT_COPY,
    TOOL_DOCUMENT_DELETE,
)


def _service(ctx: ToolContext, tool: str) -> DocumentService:
    service = ctx.live.get("document_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the document service"
        )
    return service


def _mutations(ctx: ToolContext, tool: str) -> MutationService:
    service = ctx.live.get("document_mutations")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the mutation service"
        )
    return service


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    """The ONE router's record of this turn (``document_ref``/``question``/``pattern``/
    ``folder``/``extensions`` — the owner's WORDS, preferred over the model's own argument,
    the same rule M19's operator tools already follow)."""
    return dict(ctx.context.get("last_utterance") or {})


def _target(ctx: ToolContext, arguments: dict[str, Any], *, default: str = "current") -> str:
    turn = _turn_record(ctx)
    ref = turn.get("document_ref")
    if isinstance(ref, str) and ref:
        return ref
    raw = arguments.get("target")
    return str(raw) if isinstance(raw, str) and raw else default


#: The focus kinds a document tool can read through a deictic reference.
_REFERENCE_KINDS: Final[frozenset[str]] = frozenset({"document", "file"})


def _reference(ctx: ToolContext, target: str) -> dict[str, Any] | None:
    """B51 req 745: what the owner's "bunu / şunu / bu dosya" pointed at, from the ONE
    router's turn record (``deictic_reference``: the freshest focused object, within its
    freshness window) - only for a "current" target and only when it is a document or a
    file. Never from the model's arguments: the referent is what the owner's words and the
    durable focus say, not what the model chose to pass."""
    if target != "current":
        return None
    ref = _turn_record(ctx).get("deictic_reference")
    if not isinstance(ref, dict) or ref.get("kind") not in _REFERENCE_KINDS:
        return None
    object_id = ref.get("object_id")
    if not isinstance(object_id, str) or not object_id:
        return None
    return {"kind": ref["kind"], "object_id": object_id}


# --------------------------------------------------------------------- file.search


def file_search(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu klasördeki PDF'leri bul", "Masaüstündeki sözleşmeyi bul", "İndirilenler'de
    bütçe dosyasını ara" (spec §3)."""
    db = _require_db(ctx, TOOL_FILE_SEARCH)
    service = _service(ctx, TOOL_FILE_SEARCH)
    turn = _turn_record(ctx)
    pattern = (
        turn.get("pattern") if isinstance(turn.get("pattern"), str) else arguments.get("pattern")
    )
    folder = turn.get("folder") if isinstance(turn.get("folder"), str) else arguments.get("folder")
    extensions = turn.get("extensions") or arguments.get("extensions")
    device_action = ctx.live.get("device_action")
    return service.search(
        db,
        device_action,
        pattern=pattern if isinstance(pattern, str) else None,
        folder=folder if isinstance(folder, str) else None,
        extensions=list(extensions) if isinstance(extensions, list) else None,
        session_id=str(ctx.session_id),
    )


# -------------------------------------------------------------------- document.read


def document_read(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu dosyayı oku." (spec §3)."""
    db = _require_db(ctx, TOOL_DOCUMENT_READ)
    service = _service(ctx, TOOL_DOCUMENT_READ)
    device_action = ctx.live.get("device_action")
    target = _target(ctx, arguments)
    return service.read(
        db,
        device_action,
        target=target,
        session_id=str(ctx.session_id),
        reference=_reference(ctx, target),
    )


def document_summarize(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bunu özetle." / "Bu belgeyi özetle." / "Bu PDF'i özetle." (spec §3)."""
    db = _require_db(ctx, TOOL_DOCUMENT_SUMMARIZE)
    service = _service(ctx, TOOL_DOCUMENT_SUMMARIZE)
    device_action = ctx.live.get("device_action")
    target = _target(ctx, arguments)
    return service.summarize(
        db,
        device_action,
        target=target,
        session_id=str(ctx.session_id),
        reference=_reference(ctx, target),
    )


def document_answer(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Ödeme süresi kaç gün?", "Üçüncü sayfada ne yazıyor?" (spec §3)."""
    db = _require_db(ctx, TOOL_DOCUMENT_ANSWER)
    service = _service(ctx, TOOL_DOCUMENT_ANSWER)
    turn = _turn_record(ctx)
    question = (
        turn.get("question")
        if isinstance(turn.get("question"), str) and turn.get("question")
        else None
    )
    question = question or str(arguments.get("question") or "")
    if not question:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "document.answer needs a question")
    device_action = ctx.live.get("device_action")
    target = _target(ctx, arguments)
    return service.answer(
        db,
        device_action,
        target=target,
        question=question,
        session_id=str(ctx.session_id),
        reference=_reference(ctx, target),
    )


def document_compare(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bir önceki belgeyle karşılaştır." = current vs previous (spec §3)."""
    db = _require_db(ctx, TOOL_DOCUMENT_COMPARE)
    service = _service(ctx, TOOL_DOCUMENT_COMPARE)
    device_action = ctx.live.get("device_action")
    a = str(arguments.get("a") or "current")
    b = str(arguments.get("b") or "previous")
    return service.compare(
        db, device_action, target_a=a, target_b=b, session_id=str(ctx.session_id)
    )


def document_inspect(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu Excel'de ne var?", "Bu sunumda kaç slayt var?" (spec §3)."""
    db = _require_db(ctx, TOOL_DOCUMENT_INSPECT)
    service = _service(ctx, TOOL_DOCUMENT_INSPECT)
    device_action = ctx.live.get("device_action")
    target = _target(ctx, arguments)
    return service.inspect(
        db,
        device_action,
        target=target,
        session_id=str(ctx.session_id),
        reference=_reference(ctx, target),
    )


def document_common_points(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bunların ortak noktalarını çıkar." (spec §3)."""
    db = _require_db(ctx, TOOL_DOCUMENT_COMMON_POINTS)
    service = _service(ctx, TOOL_DOCUMENT_COMMON_POINTS)
    device_action = ctx.live.get("device_action")
    raw = arguments.get("targets")
    targets: list[str] | str = list(raw) if isinstance(raw, list) and raw else "recent"
    return service.common_points(db, device_action, targets=targets, session_id=str(ctx.session_id))


def document_previous(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Az önceki sunuma geri dön.", "Bir önceki belgeye dön." (spec §3)."""
    del arguments
    db = _require_db(ctx, TOOL_DOCUMENT_PREVIOUS)
    service = _service(ctx, TOOL_DOCUMENT_PREVIOUS)
    return service.previous(db, session_id=str(ctx.session_id))


# ------------------------------------------------------------------ registration


# ------------------------------------------------------------------- B32 tools

#: The session-context key the duplicate proposal lives under until the owner confirms.
DEDUP_PLAN_KEY: Final = "dedup_plan"
SPEECH_NO_TEXT_QUERY: Final = "Hangi kelimeyi arayayım efendim?"
SPEECH_NO_DEDUP_PLAN: Final = (
    "Önce yinelenen dosyaları bulmamı isteyin efendim; ne bulduğumu duymadan hiçbir "
    "şeyi çöp kutusuna göndermem."
)


def document_preview(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu belgeyi önizle." (B32 req 152) - the kind, its size in its own units, its
    first words; an archive previews from its directory."""
    db = _require_db(ctx, TOOL_DOCUMENT_PREVIEW)
    service = _service(ctx, TOOL_DOCUMENT_PREVIEW)
    target = _target(ctx, arguments)
    return service.preview(
        db,
        ctx.live.get("device_action"),
        target=target,
        session_id=str(ctx.session_id),
        reference=_reference(ctx, target),
    )


def document_find_text(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "İçinde bütçe geçen belgeyi bul." (B32 req 148) - the owner's words looked for in
    every document already read, from the index alone (no crawl)."""
    db = _require_db(ctx, TOOL_DOCUMENT_FIND_TEXT)
    service = _service(ctx, TOOL_DOCUMENT_FIND_TEXT)
    turn = _turn_record(ctx)
    query = turn.get("text_query") if isinstance(turn.get("text_query"), str) else None
    query = (query or str(arguments.get("query") or "")).strip()
    if not query:
        return {"status": "needs_clarification", "speech": SPEECH_NO_TEXT_QUERY}
    return service.find_text(db, query=query, session_id=str(ctx.session_id))


def document_duplicates(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Yinelenen dosyaları bul." (B32 req 151) - a proposal, kept on the session until
    the owner says what to do with it."""
    db = _require_db(ctx, TOOL_DOCUMENT_DUPLICATES)
    service = _service(ctx, TOOL_DOCUMENT_DUPLICATES)
    turn = _turn_record(ctx)
    folder = turn.get("folder") if isinstance(turn.get("folder"), str) else None
    folder = folder or (str(arguments.get("folder")) if arguments.get("folder") else None)
    pattern = str(arguments.get("pattern") or "*")
    receipt = service.duplicates(
        db,
        ctx.live.get("device_action"),
        folder=folder,
        pattern=pattern,
        session_id=str(ctx.session_id),
    )
    if receipt.get("execution_status") == "executed":
        ctx.context[DEDUP_PLAN_KEY] = {
            "groups": receipt.get("groups") or [],
            "removable": receipt.get("removable") or 0,
        }
    return receipt


def document_dedup(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Kopyaları çöp kutusuna gönder." (B32 req 150) - ONLY the proposal this session
    heard, every copy to the Recycle Bin (reversible), each move read back."""
    del arguments
    db = _require_db(ctx, TOOL_DOCUMENT_DEDUP)
    service = _service(ctx, TOOL_DOCUMENT_DEDUP)
    plan = ctx.context.get(DEDUP_PLAN_KEY)
    if not isinstance(plan, dict) or not plan.get("groups"):
        return {"status": "needs_clarification", "speech": SPEECH_NO_DEDUP_PLAN}
    receipt = service.dedup(
        db, ctx.live.get("device_action"), plan=plan, session_id=str(ctx.session_id)
    )
    if receipt.get("execution_status") == "executed":
        ctx.context.pop(DEDUP_PLAN_KEY, None)
    return receipt


# ------------------------------------------------------------------- B34 tools


def _turn_number(ctx: ToolContext) -> int | None:
    turn = _turn_record(ctx).get("turn")
    return int(turn) if isinstance(turn, int) else None


def _text_argument(ctx: ToolContext, arguments: dict[str, Any], *keys: str) -> str | None:
    """The owner's OWN words from the router first (``text_to_type`` for an append, the
    edit's find/replace, the new name), the model's argument otherwise."""
    turn = _turn_record(ctx)
    for key in keys:
        value = turn.get(key)
        if isinstance(value, str) and value.strip():
            return value
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _folder_argument(ctx: ToolContext, arguments: dict[str, Any]) -> str | None:
    turn = _turn_record(ctx)
    folder = turn.get("folder")
    if isinstance(folder, str) and folder:
        return folder
    raw = arguments.get("folder")
    return str(raw) if isinstance(raw, str) and raw else None


def document_write(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "X adında bir dosya oluştur, içine şunu yaz." (B34 req 154) - a NEW text file in
    the owner's Documents (or the spoken folder); low risk, applied at once, undoable."""
    db = _require_db(ctx, TOOL_DOCUMENT_WRITE)
    service = _mutations(ctx, TOOL_DOCUMENT_WRITE)
    return service.write(
        db,
        ctx.live.get("device_action"),
        name=_text_argument(ctx, arguments, "new_name", "name"),
        text=_text_argument(ctx, arguments, "content"),
        folder=_folder_argument(ctx, arguments),
        session_id=str(ctx.session_id),
        turn=_turn_number(ctx),
    )


def document_append(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu dosyanın sonuna şunu ekle." (B34 req 155) - low risk, applied at once, undoable."""
    db = _require_db(ctx, TOOL_DOCUMENT_APPEND)
    service = _mutations(ctx, TOOL_DOCUMENT_APPEND)
    return service.append(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        text=_text_argument(ctx, arguments, "text_to_type", "content"),
        session_id=str(ctx.session_id),
        turn=_turn_number(ctx),
    )


def document_edit(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu dosyada X yerine Y yaz." (153, 167) / "Bu belgeyi güncelle ve kaydet." with the
    new text (170) - always a proposal the owner confirms with "Uygula." / "Kaydet."."""
    db = _require_db(ctx, TOOL_DOCUMENT_EDIT)
    service = _mutations(ctx, TOOL_DOCUMENT_EDIT)
    turn = _turn_record(ctx)
    find = turn.get("find_text") if isinstance(turn.get("find_text"), str) else None
    replace = turn.get("replace_text") if isinstance(turn.get("replace_text"), str) else None
    if find is None:
        find = str(arguments.get("find")) if isinstance(arguments.get("find"), str) else None
        replace = (
            str(arguments.get("replace")) if isinstance(arguments.get("replace"), str) else None
        )
    return service.edit(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        find=find,
        replace=replace,
        text=_text_argument(ctx, arguments, "content"),
        session_id=str(ctx.session_id),
        turn=_turn_number(ctx),
    )


def document_rename(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu dosyanın adını X yap." (156) - a proposal."""
    db = _require_db(ctx, TOOL_DOCUMENT_RENAME)
    service = _mutations(ctx, TOOL_DOCUMENT_RENAME)
    return service.rename(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        new_name=_text_argument(ctx, arguments, "new_name"),
        session_id=str(ctx.session_id),
        turn=_turn_number(ctx),
    )


def document_move(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu dosyayı Masaüstüne taşı." (157) - a proposal (critical)."""
    db = _require_db(ctx, TOOL_DOCUMENT_MOVE)
    service = _mutations(ctx, TOOL_DOCUMENT_MOVE)
    return service.move(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        folder=_folder_argument(ctx, arguments),
        session_id=str(ctx.session_id),
        turn=_turn_number(ctx),
    )


def document_copy(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu dosyayı kopyala." / "... Masaüstüne kopyala." / "... X adıyla kopyala." (158)."""
    db = _require_db(ctx, TOOL_DOCUMENT_COPY)
    service = _mutations(ctx, TOOL_DOCUMENT_COPY)
    return service.copy(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        new_name=_text_argument(ctx, arguments, "new_name"),
        folder=_folder_argument(ctx, arguments),
        session_id=str(ctx.session_id),
        turn=_turn_number(ctx),
    )


def document_delete(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu dosyayı sil." (159, 161) - a proposal (critical): the Recycle Bin with a
    backup, never a permanent delete."""
    db = _require_db(ctx, TOOL_DOCUMENT_DELETE)
    service = _mutations(ctx, TOOL_DOCUMENT_DELETE)
    return service.delete(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        session_id=str(ctx.session_id),
        turn=_turn_number(ctx),
    )


def document_apply(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Uygula." / "Kaydet." (166): the proposal this session heard, through the same
    read-back + confirmation gate a mail draft passes. The router's own verdict on THIS
    turn is the ``owner_intent_ok`` claim - never the model's argument."""
    db = _require_db(ctx, TOOL_DOCUMENT_APPLY)
    service = _mutations(ctx, TOOL_DOCUMENT_APPLY)
    turn = _turn_record(ctx)
    mutation_id = str(arguments.get("mutation_id")) if arguments.get("mutation_id") else None
    confirmation = Confirmation(
        source=CONFIRM_SOURCE_VOICE,
        session_id=str(ctx.session_id),
        turn=_turn_number(ctx),
        owner_intent_ok=ctx.context.get("last_intent") == "document_apply",
    )
    del turn
    return service.apply(
        db,
        ctx.live.get("device_action"),
        mutation_id=mutation_id,
        confirmation=confirmation,
        session_id=str(ctx.session_id),
    )


def document_discard(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Vazgeç." with a file change pending."""
    db = _require_db(ctx, TOOL_DOCUMENT_DISCARD)
    service = _mutations(ctx, TOOL_DOCUMENT_DISCARD)
    mutation_id = str(arguments.get("mutation_id")) if arguments.get("mutation_id") else None
    return service.discard(db, mutation_id=mutation_id, session_id=str(ctx.session_id))


def document_undo(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Son değişikliği geri al." (160): the inverse plan, run on the device and read back."""
    db = _require_db(ctx, TOOL_DOCUMENT_UNDO)
    service = _mutations(ctx, TOOL_DOCUMENT_UNDO)
    mutation_id = str(arguments.get("mutation_id")) if arguments.get("mutation_id") else None
    return service.undo(
        db, ctx.live.get("device_action"), mutation_id=mutation_id, session_id=str(ctx.session_id)
    )


def document_versions(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu dosyanın sürüm geçmişini göster." (164): the journal rows for its path."""
    db = _require_db(ctx, TOOL_DOCUMENT_VERSIONS)
    service = _mutations(ctx, TOOL_DOCUMENT_VERSIONS)
    return service.versions(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        session_id=str(ctx.session_id),
    )


def register_documents_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register every documents tool (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    target = {"type": "string", "maxLength": 200}
    # B34 req 153-167, 170.
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_WRITE,
            description=(
                "YENİ bir metin dosyası oluşturur ('X adında bir dosya oluştur, içine şunu "
                "yaz'): Belgeler'de (ya da söylenen klasörde), verilen metinle; hemen "
                "uygulanır, günlüğe yazılır, geri alınabilir. 'content' = dosyanın içeriği, "
                "'new_name' = dosya adı (uzantılı). Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "new_name": {"type": "string", "maxLength": 120},
                    "content": {"type": "string", "maxLength": 200000},
                    "folder": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=document_write,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_APPEND,
            description=(
                "Odaktaki metin dosyasının SONUNA ekler ('bu dosyanın sonuna şunu ekle'); "
                "hemen uygulanır, yedeği alınır, geri alınabilir. Dönen 'speech' metnini "
                "aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": target,
                    "content": {"type": "string", "maxLength": 200000},
                },
                "additionalProperties": False,
            },
            handler=document_append,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_EDIT,
            description=(
                "Odaktaki metin dosyasını DÜZENLEMEYİ ÖNERİR ('bu dosyada X yerine Y yaz', "
                "'bu belgeyi güncelle ve kaydet'): 'find'/'replace' ile bul-değiştir ya da "
                "'text' ile yeni içerik. Hiçbir şey yazılmaz; sahip 'uygula' deyince "
                "uygulanır. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": target,
                    "find": {"type": "string", "maxLength": 2000},
                    "replace": {"type": "string", "maxLength": 20000},
                    "content": {"type": "string", "maxLength": 200000},
                },
                "additionalProperties": False,
            },
            handler=document_edit,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_RENAME,
            description=(
                "Odaktaki dosyanın ADINI DEĞİŞTİRMEYİ önerir ('bu dosyanın adını X yap'); "
                "sahip 'uygula' deyince uygulanır. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": target, "new_name": {"type": "string", "maxLength": 120}},
                "additionalProperties": False,
            },
            handler=document_rename,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_MOVE,
            description=(
                "Odaktaki dosyayı başka klasöre TAŞIMAYI önerir ('bu dosyayı Masaüstüne "
                "taşı'); sahip 'uygula' deyince uygulanır. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": target, "folder": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=document_move,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_COPY,
            description=(
                "Odaktaki dosyayı KOPYALAMAYI önerir ('bu dosyayı kopyala', '... Masaüstüne "
                "kopyala', '... X adıyla kopyala'); sahip 'uygula' deyince uygulanır. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": target,
                    "new_name": {"type": "string", "maxLength": 120},
                    "folder": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=document_copy,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_DELETE,
            description=(
                "Odaktaki dosyayı ÇÖP KUTUSUNA göndermeyi önerir ('bu dosyayı sil'); asla "
                "kalıcı silme; yedeği alınır; sahip 'uygula' deyince uygulanır. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": target},
                "additionalProperties": False,
            },
            handler=document_delete,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_APPLY,
            description=(
                "Bekleyen dosya değişikliğini UYGULAR ('uygula', 'kaydet', 'onaylıyorum'); "
                "yalnız bu oturumda okunmuş bir öneri, cihazda uygulanır ve geri okunur. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"mutation_id": {"type": "string", "maxLength": 64}},
                "additionalProperties": False,
            },
            handler=document_apply,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_DISCARD,
            description=(
                "Bekleyen dosya değişikliğinden VAZGEÇER ('vazgeç'); hiçbir şeye dokunulmaz. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"mutation_id": {"type": "string", "maxLength": 64}},
                "additionalProperties": False,
            },
            handler=document_discard,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_UNDO,
            description=(
                "Son uygulanan dosya değişikliğini GERİ ALIR ('son değişikliği geri al'): "
                "yedekten geri yüklenir ya da tersi yapılır, cihazdan doğrulanır. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"mutation_id": {"type": "string", "maxLength": 64}},
                "additionalProperties": False,
            },
            handler=document_undo,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_VERSIONS,
            description=(
                "Odaktaki dosyanın SÜRÜM GEÇMİŞİNİ söyler ('bu dosyanın sürüm geçmişi'): "
                "değişiklik günlüğündeki kayıtlar, özetleriyle. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": target},
                "additionalProperties": False,
            },
            handler=document_versions,
        )
    )

    # B32 req 148/150/151/152.
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_PREVIEW,
            description=(
                "Bir belgenin ÖNİZLEMESİ: 'bu belgeyi önizle', 'bu dosyanın önizlemesini "
                "göster' - türü, boyutu (sayfa/sayfa/slayt/satır/piksel) ve ilk sözleri; bir "
                "arşiv için içindeki öğeler. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=document_preview,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_FIND_TEXT,
            description=(
                "METNİNDE bir kelime geçen belgeleri bulur: 'içinde bütçe geçen belgeyi bul', "
                "'Hetzner yazan dosya hangisi'. Yalnız daha önce okunmuş belgelere bakar; "
                "cihazda tarama yapmaz. 'query' sahibin aradığı kelime(ler). Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=document_find_text,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_DUPLICATES,
            description=(
                "YİNELENEN dosyaları bulur (aynı içerik, farklı yer): 'yinelenen dosyaları "
                "bul', 'kopya dosyaları bul'. Hiçbir şeyi silmez; hangi kopyanın kalacağını "
                "ve hangilerinin çöp kutusuna gidebileceğini SÖYLER. Dönen 'speech' metnini "
                "aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "maxLength": 200},
                    "pattern": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=document_duplicates,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_DEDUP,
            description=(
                "Sahip açıkça isteyince, az önce bulunan KOPYALARI ÇÖP KUTUSUNA gönderir "
                "('kopyaları çöp kutusuna gönder', 'yinelenenleri temizle'). Kalıcı silme "
                "değildir; önce yinelenen dosyalar bulunmuş olmalı. Dönen 'speech' metnini "
                "aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=document_dedup,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_FILE_SEARCH,
            description=(
                "Sahibin bilgisayarında bir DOSYA ARAR: 'bu klasördeki PDF'leri bul', "
                "'Masaüstündeki sözleşmeyi bul', 'İndirilenler'de bütçe dosyasını ara'. "
                "Sunucu sahibin izin verdiği klasörlerde arar; sen bir yol UYDURMA. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "maxLength": 200},
                    "folder": {"type": "string", "maxLength": 200},
                    "extensions": {"type": "array", "items": {"type": "string", "maxLength": 16}},
                },
                "additionalProperties": False,
            },
            handler=file_search,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_READ,
            description=(
                "Bir BELGEYİ OKUR ve içeriğini dizine ekler: 'bu dosyayı oku', 'bu belgeyi "
                "oku'. Hangi belge olduğunu SUNUCU çözer (odaktaki belge/dosya, ya da az "
                "önce bulunan). Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=document_read,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_SUMMARIZE,
            description=(
                "ODAKTAKİ BELGEYİ özetler: 'bunu özetle', 'bu belgeyi özetle', 'bu PDF'i "
                "özetle'. SADECE bir belge/dosya odaktayken çağır; bir ARAŞTIRMA "
                "özetleniyorsa bu aracı DEĞİL, research.explain'i çağır. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=document_summarize,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_ANSWER,
            description=(
                "ODAKTAKİ BELGE hakkında bir SORUYU belgenin içeriğinden yanıtlar: "
                "'Ödeme süresi kaç gün?', 'Üçüncü sayfada ne yazıyor?'. 'question' alanına "
                "sahibin sorusunu aynen ver. Sunucu yanıtı belgeden alır; kendi bilginle "
                "cevap uydurma. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"question": {"type": "string", "maxLength": 500}},
                "additionalProperties": False,
            },
            handler=document_answer,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_COMPARE,
            description=(
                "İki belgeyi KARŞILAŞTIRIR: 'bir önceki belgeyle karşılaştır', 'önceki "
                "dosyayla karşılaştır' — odaktaki belge ile bir öncekini karşılaştırır. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "string", "maxLength": 200},
                    "b": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=document_compare,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_INSPECT,
            description=(
                "ODAKTAKİ dosyanın YAPISINI söyler (kaç sayfa/slayt/sayfa sekmesi var, "
                "hangi başlıklar var): 'bu Excel'de ne var?', 'bu sunumda kaç slayt var?'. "
                "İçeriği OKUMAZ, yapıyı söyler. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=document_inspect,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_COMMON_POINTS,
            description=(
                "Son okunan belgelerin ORTAK NOKTALARINI çıkarır: 'bunların ortak "
                "noktalarını çıkar'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "targets": {"type": "array", "items": {"type": "string", "maxLength": 200}}
                },
                "additionalProperties": False,
            },
            handler=document_common_points,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_DOCUMENT_PREVIOUS,
            description=(
                "ÖNCEKİ belgeye/dosyaya geri döner: 'az önceki sunuma geri dön', 'bir "
                "önceki belgeye dön'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=document_previous,
        )
    )
    return reg


__all__ = [
    "DOCUMENT_TOOL_NAMES",
    "MUTATION_TOOL_NAMES",
    "TOOL_DOCUMENT_ANSWER",
    "TOOL_DOCUMENT_COMMON_POINTS",
    "TOOL_DOCUMENT_COMPARE",
    "TOOL_DOCUMENT_INSPECT",
    "TOOL_DOCUMENT_PREVIOUS",
    "TOOL_DOCUMENT_READ",
    "TOOL_DOCUMENT_SUMMARIZE",
    "TOOL_FILE_SEARCH",
    "document_answer",
    "document_common_points",
    "document_compare",
    "document_inspect",
    "document_previous",
    "document_read",
    "document_summarize",
    "file_search",
    "register_documents_tools",
]
