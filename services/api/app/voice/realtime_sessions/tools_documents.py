"""File & Document Intelligence's voice tools (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md
§3, §4). Eight tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_documents_tools`), the same discipline ``tools_operator`` already
establishes for its own family.

There is NO delete/move/write tool here (ADR-0083 decision 7): "Bu dosyayı sil" reaches
none of these — the corpus proves it. Every result is a receipt-shaped dict whose
``speech`` is read verbatim; a target that cannot be resolved (no document focused, no
previous document) is an honest ``needs_clarification``, never a guess.

``DocumentService`` and the fake/real device port both come from ``ctx.live``
(``document_service``, ``device_action``) — ``app.main.create_app`` registers the real
service on the SAME object every request shares; a test injects fakes the same way
(docs/M18_ACTION_CONTRACT.md §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

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

DOCUMENT_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_FILE_SEARCH,
    TOOL_DOCUMENT_READ,
    TOOL_DOCUMENT_SUMMARIZE,
    TOOL_DOCUMENT_ANSWER,
    TOOL_DOCUMENT_COMPARE,
    TOOL_DOCUMENT_INSPECT,
    TOOL_DOCUMENT_COMMON_POINTS,
    TOOL_DOCUMENT_PREVIOUS,
)


def _service(ctx: ToolContext, tool: str) -> DocumentService:
    service = ctx.live.get("document_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the document service"
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
    return service.read(
        db, device_action, target=_target(ctx, arguments), session_id=str(ctx.session_id)
    )


def document_summarize(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bunu özetle." / "Bu belgeyi özetle." / "Bu PDF'i özetle." (spec §3)."""
    db = _require_db(ctx, TOOL_DOCUMENT_SUMMARIZE)
    service = _service(ctx, TOOL_DOCUMENT_SUMMARIZE)
    device_action = ctx.live.get("device_action")
    return service.summarize(
        db, device_action, target=_target(ctx, arguments), session_id=str(ctx.session_id)
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
    return service.answer(
        db,
        device_action,
        target=_target(ctx, arguments),
        question=question,
        session_id=str(ctx.session_id),
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
    return service.inspect(
        db, device_action, target=_target(ctx, arguments), session_id=str(ctx.session_id)
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


def register_documents_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all eight tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

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
