"""The Creative Tools Operator's voice tools (docs/M27_CREATIVE_TOOLS_SPEC.md §5).
Seven tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_creative_tools`), the same discipline ``tools_scene``/``tools_apps``
already establish for their own families.

The TOOL WORD (paint/photoshop/illustrator/figma) is resolved the same way every other
family's target is (spec §5): the owner's WORDS first (``ctx.context["last_utterance"]
["creative_tool"]``, set by the ONE router in ``app.voice.intents``), else the CURRENT
creative focus's own tool, else a family-specific default - never a guess and never the
model's own default silently overriding what the owner said. Every mutating tool's
result already carries the read-back speech (``CreativeService``'s own ``_describe``/
``_mismatch_speech``); the tool functions here only resolve arguments and call the
service, the same thin-adapter shape ``tools_scene.py`` documents for its own family.

``CreativeService`` comes from ``ctx.live`` (``creative_service``) - ``app.main.
create_app`` registers the real service on the SAME object every request shares; a test
injects a fake the same way (docs/M18_ACTION_CONTRACT.md §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.creative.service import CreativeService
from app.creative.spec import EXPORT_FORMATS, TOOL_FIGMA, TOOL_PAINT, TOOLS
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_CREATIVE_REDRAW: Final = "creative.redraw"
TOOL_CREATIVE_OPEN: Final = "creative.open"
TOOL_CREATIVE_BACKGROUND: Final = "creative.background"
TOOL_CREATIVE_ADJUST: Final = "creative.adjust"
TOOL_CREATIVE_CLEANUP: Final = "creative.cleanup"
TOOL_CREATIVE_DESIGN: Final = "creative.design"
TOOL_CREATIVE_EXPORT: Final = "creative.export"

CREATIVE_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_CREATIVE_REDRAW,
    TOOL_CREATIVE_OPEN,
    TOOL_CREATIVE_BACKGROUND,
    TOOL_CREATIVE_ADJUST,
    TOOL_CREATIVE_CLEANUP,
    TOOL_CREATIVE_DESIGN,
    TOOL_CREATIVE_EXPORT,
)

SPEECH_NO_TOOL = "Hangi programda efendim: Paint mi, Photoshop mu, Illustrator mı, Figma mı?"
SPEECH_NO_SOURCE = "Hangi resmi çizeyim efendim?"


def _service(ctx: ToolContext, tool: str) -> CreativeService:
    service = ctx.live.get("creative_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the creative tools service"
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
    """The ONE router's record of this turn (``creative_tool``/``creative_ref``/
    ``creative_format`` - the owner's WORDS, preferred over the model's own argument,
    the same rule ``tools_scene.py``'s own ``_turn_record`` follows)."""
    return dict(ctx.context.get("last_utterance") or {})


def _target(ctx: ToolContext, arguments: dict[str, Any], *, default: str = "current") -> str:
    turn = _turn_record(ctx)
    ref = turn.get("creative_ref")
    if isinstance(ref, str) and ref:
        return ref
    raw = arguments.get("target")
    return str(raw) if isinstance(raw, str) and raw else default


def _resolve_tool(
    ctx: ToolContext, arguments: dict[str, Any], *, default: str | None = None
) -> str | None:
    """The tool word the owner's WORDS carried, else the model's own ``tool``
    argument, else the CURRENT creative focus's own tool, else ``default`` (spec §5:
    "the owner's word wins ... otherwise ... the installed set decides")."""
    turn = _turn_record(ctx)
    word = turn.get("creative_tool")
    if isinstance(word, str) and word in TOOLS:
        return word
    arg = arguments.get("tool")
    if isinstance(arg, str) and arg in TOOLS:
        return arg
    db = ctx.db
    if db is not None:
        service = ctx.live.get("creative_service")
        if service is not None:
            row = service.resolve_run(db, "current")
            if row is not None:
                return row.tool
    return default


def _default_name(ctx: ToolContext, prefix: str) -> str:
    """A deterministic fixture name when the owner named none (the same
    "pagentos-<short session id>" shape ``tools_scene._default_project_scene``
    already uses)."""
    session_suffix = str(ctx.session_id).replace("-", "")[:8].lower() or "default"
    return f"{prefix}-{session_suffix}"


# ------------------------------------------------------------------ creative.redraw


def creative_redraw(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu resmi Paint'te yeniden çiz." (spec §5) - "bu resmi" the CURRENT creative
    focus's own output when the model names no explicit source, the same "owner's
    words/context win, else a clarification" fallback ``creative_open`` already uses."""
    db = _require_db(ctx, TOOL_CREATIVE_REDRAW)
    service = _service(ctx, TOOL_CREATIVE_REDRAW)
    tool = _resolve_tool(ctx, arguments, default=TOOL_PAINT)
    source = str(arguments.get("source")) if isinstance(arguments.get("source"), str) else None
    if not source:
        current = service.resolve_run(db, "current")
        source = current.output_object_key if current is not None else None
    if not source:
        return service.clarification(SPEECH_NO_SOURCE)
    name = (
        str(arguments.get("name"))
        if isinstance(arguments.get("name"), str) and arguments.get("name")
        else _default_name(ctx, "redraw")
    )
    plan = {
        "tool": tool,
        "name": name,
        "source": source,
        "operations": [{"op": "open"}, {"op": "export", "format": "png"}],
    }
    return service.create(db, plan=plan, session_id=str(ctx.session_id))


# -------------------------------------------------------------------- creative.open


def creative_open(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bunu Photoshop'ta aç." (spec §5) - refuses honestly, naming the installed
    alternative, when the named tool is not on this machine."""
    db = _require_db(ctx, TOOL_CREATIVE_OPEN)
    service = _service(ctx, TOOL_CREATIVE_OPEN)
    tool = _resolve_tool(ctx, arguments)
    if tool is None:
        return service.clarification(SPEECH_NO_TOOL)
    target = _target(ctx, arguments)
    row = service.resolve_run(db, target)
    source = str(arguments.get("source")) if isinstance(arguments.get("source"), str) else None
    if row is not None and source is None:
        source = row.output_object_key
    name = (
        str(arguments.get("name"))
        if isinstance(arguments.get("name"), str) and arguments.get("name")
        else _default_name(ctx, "open")
    )
    if source:
        operations = [{"op": "open"}, {"op": "export", "format": "png"}]
    else:
        operations = [{"op": "new", "width": 800, "height": 600}]
    plan = {"tool": tool, "name": name, "source": source, "operations": operations}
    return service.create(db, plan=plan, session_id=str(ctx.session_id))


# -------------------------------------------------------------- creative.background


def creative_background(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Arka planını kaldır." (spec §5)."""
    db = _require_db(ctx, TOOL_CREATIVE_BACKGROUND)
    service = _service(ctx, TOOL_CREATIVE_BACKGROUND)
    tolerance = (
        int(arguments["tolerance"]) if isinstance(arguments.get("tolerance"), int | float) else 32
    )
    op = {"op": "background_remove", "method": "threshold", "tolerance": tolerance}
    return service.apply(
        db,
        target=_target(ctx, arguments),
        operations=[op],
        capability=TOOL_CREATIVE_BACKGROUND,
        session_id=str(ctx.session_id),
    )


# ------------------------------------------------------------------ creative.adjust


def creative_adjust(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Renkleri biraz düzelt." (spec §5)."""
    db = _require_db(ctx, TOOL_CREATIVE_ADJUST)
    service = _service(ctx, TOOL_CREATIVE_ADJUST)
    valid_fields = ("brightness", "contrast", "saturation", "levels")
    field = arguments.get("field") if arguments.get("field") in valid_fields else "levels"
    value = float(arguments["value"]) if isinstance(arguments.get("value"), int | float) else 1.15
    op = {"op": "color_adjust", "field": field, "value": value}
    return service.apply(
        db,
        target=_target(ctx, arguments),
        operations=[op],
        capability=TOOL_CREATIVE_ADJUST,
        session_id=str(ctx.session_id),
    )


# ----------------------------------------------------------------- creative.cleanup


def creative_cleanup(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Logoyu daha temiz hale getir." (spec §5) - a bounded, deterministic
    combination (background threshold + a contrast nudge), never a learned/opaque
    "cleanup" model."""
    db = _require_db(ctx, TOOL_CREATIVE_CLEANUP)
    service = _service(ctx, TOOL_CREATIVE_CLEANUP)
    ops = [
        {"op": "background_remove", "method": "threshold", "tolerance": 24},
        {"op": "color_adjust", "field": "contrast", "value": 1.2},
    ]
    return service.apply(
        db,
        target=_target(ctx, arguments),
        operations=ops,
        capability=TOOL_CREATIVE_CLEANUP,
        session_id=str(ctx.session_id),
    )


# ------------------------------------------------------------------ creative.design


def creative_design(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Figma'da buna benzeyen bir arayüz tasarla." (spec §5)."""
    db = _require_db(ctx, TOOL_CREATIVE_DESIGN)
    service = _service(ctx, TOOL_CREATIVE_DESIGN)
    tool = _resolve_tool(ctx, arguments, default=TOOL_FIGMA)
    name = (
        str(arguments.get("name"))
        if isinstance(arguments.get("name"), str) and arguments.get("name")
        else _default_name(ctx, "tasarim")
    )
    width = int(arguments["width"]) if isinstance(arguments.get("width"), int | float) else 800
    height = int(arguments["height"]) if isinstance(arguments.get("height"), int | float) else 600
    plan = {
        "tool": tool,
        "name": name,
        "source": None,
        "operations": [{"op": "new", "width": width, "height": height}],
    }
    return service.create(db, plan=plan, session_id=str(ctx.session_id))


# ------------------------------------------------------------------ creative.export


def creative_export(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bunu PNG olarak dışa aktar." (spec §5)."""
    db = _require_db(ctx, TOOL_CREATIVE_EXPORT)
    service = _service(ctx, TOOL_CREATIVE_EXPORT)
    turn = _turn_record(ctx)
    fmt = turn.get("creative_format")
    if not (isinstance(fmt, str) and fmt in EXPORT_FORMATS):
        fmt = arguments.get("format") if arguments.get("format") in EXPORT_FORMATS else "png"
    op = {"op": "export", "format": fmt}
    return service.apply(
        db,
        target=_target(ctx, arguments),
        operations=[op],
        capability=TOOL_CREATIVE_EXPORT,
        session_id=str(ctx.session_id),
    )


def register_creative_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_REDRAW,
            description=(
                "Var olan bir görseli belirtilen araçta (varsayılan Paint) YENİDEN "
                "ÇİZER: 'Bu resmi Paint'te yeniden çiz' denince bu araç çağrılır. "
                "'source' owner'ın belirttiği görsel referansıdır. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": list(TOOLS)},
                    "source": {"type": "string", "maxLength": 256},
                    "name": {"type": "string", "maxLength": 64},
                },
                "additionalProperties": False,
            },
            handler=creative_redraw,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_OPEN,
            description=(
                "Belirtilen yaratıcı araçta (Paint/Photoshop/Illustrator/Figma) bir "
                "görseli AÇAR: 'Bunu Photoshop'ta aç' denince bu araç çağrılır. Araç "
                "kurulu değilse dürüstçe reddeder ve alternatif önerir. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": list(TOOLS)},
                    "source": {"type": "string", "maxLength": 256},
                    "name": {"type": "string", "maxLength": 64},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=creative_open,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_BACKGROUND,
            description=(
                "ODAKTAKİ görselin ARKA PLANINI KALDIRIR: 'Arka planını kaldır'. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tolerance": {"type": "integer", "minimum": 0, "maximum": 255},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=creative_background,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_ADJUST,
            description=(
                "ODAKTAKİ görselin RENKLERİNİ (parlaklık/kontrast/doygunluk/tonlar) "
                "DÜZELTİR: 'Renkleri biraz düzelt'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": ["brightness", "contrast", "saturation", "levels"],
                    },
                    "value": {"type": "number", "minimum": 0.0, "maximum": 4.0},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=creative_adjust,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_CLEANUP,
            description=(
                "ODAKTAKİ görseli (ör. bir logo) DAHA TEMİZ hale getirir - arka plan "
                "kaldırma + kontrast düzeltmenin sabit bir birleşimi: 'Logoyu daha "
                "temiz hale getir'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=creative_cleanup,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_DESIGN,
            description=(
                "Figma'da (varsayılan) YENİ bir tasarım/arayüz tuvali OLUŞTURUR: "
                "'Figma'da buna benzeyen bir arayüz tasarla'. Figma bağlı değilse "
                "dürüstçe reddeder. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": list(TOOLS)},
                    "name": {"type": "string", "maxLength": 64},
                    "width": {"type": "integer", "minimum": 1, "maximum": 8192},
                    "height": {"type": "integer", "minimum": 1, "maximum": 8192},
                },
                "additionalProperties": False,
            },
            handler=creative_design,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_EXPORT,
            description=(
                "ODAKTAKİ çalışmayı istenen biçimde (PNG/JPG/SVG/PDF) DIŞA AKTARIR: "
                "'Bunu PNG olarak dışa aktar'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": list(EXPORT_FORMATS)},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=creative_export,
        )
    )
    return reg


__all__ = [
    "CREATIVE_TOOL_NAMES",
    "TOOL_CREATIVE_ADJUST",
    "TOOL_CREATIVE_BACKGROUND",
    "TOOL_CREATIVE_CLEANUP",
    "TOOL_CREATIVE_DESIGN",
    "TOOL_CREATIVE_EXPORT",
    "TOOL_CREATIVE_OPEN",
    "TOOL_CREATIVE_REDRAW",
    "register_creative_tools",
]
