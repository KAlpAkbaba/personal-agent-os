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
from app.creative.spec import EXPORT_FORMATS, TOOL_PAINT, TOOLS
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
    # The bytes, not just the key. `CreativeService.create` never dereferences
    # `plan.source` itself (ADR-0094 decision 1: no implicit object-store fetch inside
    # `create`), so a caller that names a source and does not hand over its bytes gets
    # `source_bytes=None`, the `open` operation falls through to a blank 1x1 canvas, and
    # the run reports an empty-output mismatch. Which is to say: "Bu resmi Paint'te
    # yeniden cizr" - THE sentence this whole capability is named for - could never
    # actually reopen the owner's image. Found by the M27 security review; `apply()` had
    # been doing it correctly on the continuation path all along.
    return service.create(
        db,
        plan=plan,
        source_bytes=service.fetch_source_bytes(source),
        session_id=str(ctx.session_id),
    )


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
    """ "Figma'da buna benzeyen bir arayüz tasarla." (spec §5).

    The default tool is Paint, not Figma (B03 req 505). ``FigmaProvider.token_present`` is a
    hard-coded ``False`` with nothing wired to set it - an honest placeholder for a credential
    store that does not exist yet - so defaulting to Figma made this tool fail by construction
    for every owner who did not name a tool: ``dependency_unavailable``, every time, with no
    path through it. Naming Figma still reaches that refusal, which is the point of the
    refusal; not naming anything now reaches the one tool that actually does the work.
    """
    db = _require_db(ctx, TOOL_CREATIVE_DESIGN)
    service = _service(ctx, TOOL_CREATIVE_DESIGN)
    tool = _resolve_tool(ctx, arguments, default=TOOL_PAINT)
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


# ------------------------------------------------------------ B43: the lifecycle tools

TOOL_CREATIVE_GENERATE: Final = "creative.generate"
TOOL_CREATIVE_ENHANCE: Final = "creative.enhance"
TOOL_CREATIVE_UNDO: Final = "creative.undo"
TOOL_CREATIVE_REDO: Final = "creative.redo"
TOOL_CREATIVE_DELIVER: Final = "creative.deliver"
TOOL_CREATIVE_DRIVE: Final = "creative.drive"
DELIVER_APPLICATIONS: Final[tuple[str, ...]] = ("mspaint",)


def creative_generate(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bana bir logo üret: mavi bir dalga." (req 492) - the owner's own sentence is the
    prompt (the router's ``creative_prompt``); the model's ``prompt`` argument only fills
    in when the router carried none."""
    db = _require_db(ctx, TOOL_CREATIVE_GENERATE)
    service = _service(ctx, TOOL_CREATIVE_GENERATE)
    turn = _turn_record(ctx)
    prompt = turn.get("creative_prompt")
    if not (isinstance(prompt, str) and prompt.strip()):
        prompt = arguments.get("prompt") if isinstance(arguments.get("prompt"), str) else ""
    if not prompt.strip():
        return service.clarification("Neyi üreteyim efendim? Bir cümleyle anlatın.")
    name = (
        str(arguments.get("name"))
        if isinstance(arguments.get("name"), str) and arguments.get("name")
        else _default_name(ctx, "uretim")
    )
    width = int(arguments["width"]) if isinstance(arguments.get("width"), int | float) else 1024
    height = int(arguments["height"]) if isinstance(arguments.get("height"), int | float) else 1024
    expectation = (
        arguments.get("expectation") if isinstance(arguments.get("expectation"), str) else None
    )
    return service.generate(
        db,
        # The owner's own sentence, without its closing punctuation ("...: mavi bir dalga.").
        prompt=prompt.strip().rstrip(".!?…").strip(),
        name=name,
        width=width,
        height=height,
        tool=_resolve_tool(ctx, arguments, default=TOOL_PAINT) or TOOL_PAINT,
        expectation=expectation,
        session_id=str(ctx.session_id),
    )


def creative_enhance(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu fotoğrafı düzelt." (req 512 / 494)."""
    db = _require_db(ctx, TOOL_CREATIVE_ENHANCE)
    service = _service(ctx, TOOL_CREATIVE_ENHANCE)
    kind = (
        arguments.get("kind")
        if arguments.get("kind") in ("auto", "sharpen", "denoise", "autocontrast")
        else "auto"
    )
    return service.enhance(
        db, target=_target(ctx, arguments), kind=kind, session_id=str(ctx.session_id)
    )


def creative_undo(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Geri al." with a creative run in focus (req 511)."""
    db = _require_db(ctx, TOOL_CREATIVE_UNDO)
    service = _service(ctx, TOOL_CREATIVE_UNDO)
    return service.undo(db, target=_target(ctx, arguments), session_id=str(ctx.session_id))


def creative_redo(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Yinele." / "İleri al." with a creative run in focus (req 511)."""
    db = _require_db(ctx, TOOL_CREATIVE_REDO)
    service = _service(ctx, TOOL_CREATIVE_REDO)
    return service.redo(db, target=_target(ctx, arguments), session_id=str(ctx.session_id))


def creative_deliver(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bunu bilgisayarıma indir." / "Paint'te göster." (req 509 / 500): the output
    becomes an image artifact and is fetched onto the owner's disk through the artifact
    open path - opened in Paint when the owner named it."""
    db = _require_db(ctx, TOOL_CREATIVE_DELIVER)
    service = _service(ctx, TOOL_CREATIVE_DELIVER)
    turn = _turn_record(ctx)
    application = turn.get("creative_application")
    if not (isinstance(application, str) and application in DELIVER_APPLICATIONS):
        arg = arguments.get("application")
        application = arg if isinstance(arg, str) and arg in DELIVER_APPLICATIONS else None
    runtime = ctx.live.get("artifacts_runtime")
    base_url = getattr(getattr(runtime, "settings", None), "artifact_download_origin", "") or ""
    return service.deliver(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        base_url=base_url,
        session_id=str(ctx.session_id),
        application=application,
    )


def creative_drive(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """The delivered file driven in the real application by its own shortcuts (req 500,
    502, 504): the actions are a closed list, the path is the delivery's own."""
    db = _require_db(ctx, TOOL_CREATIVE_DRIVE)
    service = _service(ctx, TOOL_CREATIVE_DRIVE)
    actions = arguments.get("actions")
    if not isinstance(actions, list) or not actions:
        return service.clarification("Uygulamada hangi adımları uygulayayım efendim?")
    return service.drive(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        actions=[a for a in actions if isinstance(a, dict | str)][:12],
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
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_GENERATE,
            description=(
                "Sahibin tarif ettiği bir GÖRSELİ ÜRETİR: 'bana bir logo üret: mavi bir dalga', "
                "'bir afiş oluştur'. Sağlayıcı tanımlı değilse adıyla reddeder. 'prompt' alanına "
                "sahibin tarifini, 'expectation' alanına görselde görünmesi gerekeni yaz. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "maxLength": 1000},
                    "name": {"type": "string", "maxLength": 64},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                    "expectation": {"type": "string", "maxLength": 200},
                    "tool": {"type": "string", "enum": list(TOOLS)},
                },
                "additionalProperties": False,
            },
            handler=creative_generate,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_ENHANCE,
            description=(
                "ODAKTAKİ fotoğrafı/görseli İYİLEŞTİRİR (otomatik kontrast + netlik): "
                "'bu fotoğrafı düzelt', 'resmi netleştir'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["auto", "sharpen", "denoise", "autocontrast"],
                    },
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=creative_enhance,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_UNDO,
            description=(
                "ODAKTAKİ görsel çalışmasında son adımı GERİ ALIR: 'geri al'. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=creative_undo,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_REDO,
            description=(
                "ODAKTAKİ görsel çalışmasında geri alınan adımı YİNELER: 'yinele', 'ileri al'. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=creative_redo,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_DELIVER,
            description=(
                "ODAKTAKİ görsel çalışmasını sahibin BİLGİSAYARINA İNDİRİR (artefakt olarak, "
                "İndirilenler klasörüne) ve açar: 'bunu bilgisayarıma indir', 'diskime kaydet', "
                "'Paint'te göster' (application=mspaint). Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "application": {"type": "string", "enum": list(DELIVER_APPLICATIONS)},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=creative_deliver,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_CREATIVE_DRIVE,
            description=(
                "İndirilmiş görseli GERÇEK uygulamada (Paint/Photoshop/Illustrator) açar ve "
                "uygulamanın kendi kısayollarıyla adımları uygular: actions = "
                "[{action: resize|invert|clear|undo|redo|save|select_all|capture, percent?}]. "
                "Önce creative.deliver çağrılmış olmalı. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "actions": {"type": "array", "items": {"type": "object"}, "maxItems": 12},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=creative_drive,
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
