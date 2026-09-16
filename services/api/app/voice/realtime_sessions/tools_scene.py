"""3D creation's voice tools (docs/M25_CREATIVE_3D_SPEC.md §5). Eight tools, registered
from ``tools.default_registry()`` by ONE added line (:func:`register_scene_tools`), the
same discipline ``tools_apps``/``tools_genesis`` already establish for their own
families.

The TOOL WORD (``unity``/``blender``) is resolved the same way every other family's
target is (spec §5): the owner's WORDS first (``ctx.context["last_utterance"]
["scene_tool"]``, set by the ONE router in ``app.voice.intents``), else the CURRENT
scene focus's own tool (``object_focus`` kind ``scene``), else an honest clarification —
never a guess and never the model's own default. Every mutating tool's result already
carries the inspection read-back (``SceneService._finish``'s own ``speech``); the tool
functions here only resolve arguments and call the service, the same thin-adapter shape
``tools_apps.py`` documents for its own family.

``SceneService`` and the device port both come from ``ctx.live``
(``creative3d_service``, ``device_action``) — ``app.main.create_app`` registers the real
service on the SAME object every request shares; a test injects a fake the same way
(docs/M18_ACTION_CONTRACT.md §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.creative3d.service import SceneService
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_SCENE_CREATE: Final = "scene.create"
TOOL_SCENE_ADD: Final = "scene.add"
TOOL_SCENE_TRANSFORM: Final = "scene.transform"
TOOL_SCENE_MATERIAL: Final = "scene.material"
TOOL_SCENE_LIGHT: Final = "scene.light"
TOOL_SCENE_CAMERA: Final = "scene.camera"
TOOL_SCENE_RENDER: Final = "scene.render"
TOOL_SCENE_INSPECT: Final = "scene.inspect"
#: B44 (req 526, 527): the production path's motion and export.
TOOL_SCENE_ANIMATE: Final = "scene.animate"
TOOL_SCENE_EXPORT: Final = "scene.export"

SCENE_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_SCENE_CREATE,
    TOOL_SCENE_ADD,
    TOOL_SCENE_TRANSFORM,
    TOOL_SCENE_MATERIAL,
    TOOL_SCENE_LIGHT,
    TOOL_SCENE_CAMERA,
    TOOL_SCENE_RENDER,
    TOOL_SCENE_INSPECT,
    TOOL_SCENE_ANIMATE,
    TOOL_SCENE_EXPORT,
)

SPEECH_NO_TOOL = "Hangi araçla efendim: Blender mi, Unity mi?"


def _service(ctx: ToolContext, tool: str) -> SceneService:
    service = ctx.live.get("creative3d_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the 3D creation service"
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
    """The ONE router's record of this turn (``scene_tool``/``scene_ref`` - the
    owner's WORDS, preferred over the model's own argument, the same rule M20/M23's
    own tools already follow)."""
    return dict(ctx.context.get("last_utterance") or {})


def _target(ctx: ToolContext, arguments: dict[str, Any], *, default: str = "current") -> str:
    turn = _turn_record(ctx)
    ref = turn.get("scene_ref")
    if isinstance(ref, str) and ref:
        return ref
    raw = arguments.get("target")
    return str(raw) if isinstance(raw, str) and raw else default


def _resolve_tool(ctx: ToolContext, arguments: dict[str, Any]) -> str | None:
    """The tool word the owner's WORDS carried, else the model's own ``tool``
    argument, else the CURRENT scene focus's own tool, else ``None`` (spec §5: "the
    tool word resolved from the utterance, else the current focus, else a
    clarification")."""
    turn = _turn_record(ctx)
    word = turn.get("scene_tool")
    if isinstance(word, str) and word in ("blender", "unity"):
        return word
    arg = arguments.get("tool")
    if isinstance(arg, str) and arg in ("blender", "unity"):
        return arg
    db = ctx.db
    if db is not None:
        service = ctx.live.get("creative3d_service")
        if service is not None:
            row = service.resolve_scene(db, "current")
            if row is not None:
                return row.tool
    return None


def _default_project_scene(ctx: ToolContext) -> tuple[str, str]:
    """A deterministic fixture project/scene slug when the owner named neither
    (spec §2: ``project`` is a slug under the 3D root) - "pagentos" is the fixed
    default project, "sahne" plus the session's own short id keeps two concurrent
    sessions from colliding on the same scene slug."""
    session_suffix = str(ctx.session_id).replace("-", "")[:8].lower() or "default"
    return "pagentos", f"sahne-{session_suffix}"


# --------------------------------------------------------------------- scene.create


def scene_create(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Unity'de boş bir sahne oluştur", "Blender'da yeni sahne aç" (spec §5)."""
    db = _require_db(ctx, TOOL_SCENE_CREATE)
    service = _service(ctx, TOOL_SCENE_CREATE)
    tool = _resolve_tool(ctx, arguments)
    if tool is None:
        return service.clarification(SPEECH_NO_TOOL)
    device_action = ctx.live.get("device_action")
    default_project, default_scene = _default_project_scene(ctx)
    project = str(arguments.get("project")) if isinstance(arguments.get("project"), str) else None
    scene = str(arguments.get("scene")) if isinstance(arguments.get("scene"), str) else None
    label = str(arguments.get("label"))[:200] if isinstance(arguments.get("label"), str) else None
    plan = {
        "tool": tool,
        "project": project or default_project,
        "scene": scene or default_scene,
        "operations": [{"op": "create_scene"}],
        "label": label,
    }
    return service.create(db, device_action, plan=plan, session_id=str(ctx.session_id))


# ----------------------------------------------------------------------- scene.add


def scene_add(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bir küp ekle", "Blender'da küre oluştur", "Bir ışık ekle" (spec §5)."""
    db = _require_db(ctx, TOOL_SCENE_ADD)
    service = _service(ctx, TOOL_SCENE_ADD)
    tool = _resolve_tool(ctx, arguments)
    if tool is None:
        return service.clarification(SPEECH_NO_TOOL)
    device_action = ctx.live.get("device_action")
    turn = _turn_record(ctx)
    turn_kind = turn.get("scene_kind")
    kind = turn_kind if isinstance(turn_kind, str) and turn_kind else arguments.get("kind")
    if kind not in ("cube", "sphere", "cylinder", "plane", "light_sun", "light_point", "camera"):
        return service.clarification(
            "Ne eklemek istiyorsun: küp, küre, silindir, düzlem, ışık ya da kamera mı?"
        )
    name = (
        str(arguments.get("name"))
        if isinstance(arguments.get("name"), str) and arguments.get("name")
        else f"{kind}_1"
    )
    op: dict[str, Any] = {"op": "add_primitive", "kind": kind, "name": name}
    if isinstance(arguments.get("location"), list) and len(arguments["location"]) == 3:
        op["location"] = arguments["location"]
    row = service.resolve_scene(db, _target(ctx, arguments))
    if row is None:
        default_project, default_scene = _default_project_scene(ctx)
        plan = {
            "tool": tool,
            "project": default_project,
            "scene": default_scene,
            "operations": [{"op": "create_scene"}, op],
        }
        return service.create(db, device_action, plan=plan, session_id=str(ctx.session_id))
    return service.apply(
        db,
        device_action,
        target=str(row.id),
        operations=[op],
        capability=TOOL_SCENE_ADD,
        session_id=str(ctx.session_id),
    )


# ----------------------------------------------------------------- scene.transform


def scene_transform(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Küpü sağa taşı", "Küreyi iki kat büyüt" (spec §5)."""
    db = _require_db(ctx, TOOL_SCENE_TRANSFORM)
    service = _service(ctx, TOOL_SCENE_TRANSFORM)
    device_action = ctx.live.get("device_action")
    name = arguments.get("name")
    if not isinstance(name, str) or not name:
        return service.clarification("Hangi nesneyi taşıyayım efendim?")
    op: dict[str, Any] = {"op": "transform", "name": name}
    for field in ("location", "rotation", "scale"):
        value = arguments.get(field)
        if isinstance(value, list) and len(value) == 3:
            op[field] = value
    return service.apply(
        db,
        device_action,
        target=_target(ctx, arguments),
        operations=[op],
        capability=TOOL_SCENE_TRANSFORM,
        session_id=str(ctx.session_id),
    )


# ------------------------------------------------------------------ scene.material


def scene_material(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Küpü kırmızı yap", "Rengini maviye boya" (spec §5's ``scene.material``
    tool)."""
    db = _require_db(ctx, TOOL_SCENE_MATERIAL)
    service = _service(ctx, TOOL_SCENE_MATERIAL)
    device_action = ctx.live.get("device_action")
    name = arguments.get("name")
    color = arguments.get("color")
    if not isinstance(name, str) or not name or not isinstance(color, list) or len(color) != 4:
        return service.clarification("Hangi nesnenin rengini değiştireyim efendim?")
    op: dict[str, Any] = {"op": "set_material", "name": name, "color": color}
    if isinstance(arguments.get("metallic"), int | float):
        op["metallic"] = float(arguments["metallic"])
    if isinstance(arguments.get("roughness"), int | float):
        op["roughness"] = float(arguments["roughness"])
    return service.apply(
        db,
        device_action,
        target=_target(ctx, arguments),
        operations=[op],
        capability=TOOL_SCENE_MATERIAL,
        session_id=str(ctx.session_id),
    )


# --------------------------------------------------------------------- scene.light


def scene_light(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Işığı ayarla", "Işığı artır" (spec §5)."""
    db = _require_db(ctx, TOOL_SCENE_LIGHT)
    service = _service(ctx, TOOL_SCENE_LIGHT)
    device_action = ctx.live.get("device_action")
    name = arguments.get("name")
    energy = arguments.get("energy")
    if not isinstance(name, str) or not name or not isinstance(energy, int | float):
        return service.clarification("Hangi ışığı ayarlayayım efendim, ve ne kadar?")
    op: dict[str, Any] = {"op": "set_light", "name": name, "energy": float(energy)}
    color = arguments.get("color")
    if isinstance(color, list) and len(color) == 3:
        # B44 (req 524): the light's colour, linear RGB.
        op["color"] = [float(c) for c in color]
    return service.apply(
        db,
        device_action,
        target=_target(ctx, arguments),
        operations=[op],
        capability=TOOL_SCENE_LIGHT,
        session_id=str(ctx.session_id),
    )


# -------------------------------------------------------------------- scene.camera


def scene_camera(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Kamerayı nesneye çevir" (spec §5)."""
    db = _require_db(ctx, TOOL_SCENE_CAMERA)
    service = _service(ctx, TOOL_SCENE_CAMERA)
    device_action = ctx.live.get("device_action")
    name = (
        str(arguments.get("name"))
        if isinstance(arguments.get("name"), str) and arguments.get("name")
        else "Kamera"
    )
    look_at = arguments.get("look_at")
    lens = arguments.get("lens")
    has_lens = isinstance(lens, int | float)
    if (not isinstance(look_at, str) or not look_at) and not has_lens:
        return service.clarification("Kamerayı hangi nesneye çevireyim efendim?")
    op: dict[str, Any] = {"op": "set_camera", "name": name}
    if isinstance(look_at, str) and look_at:
        op["look_at"] = look_at
    if has_lens:
        # B44 (req 525): the focal length in millimetres.
        op["lens"] = float(lens)
    return service.apply(
        db,
        device_action,
        target=_target(ctx, arguments),
        operations=[op],
        capability=TOOL_SCENE_CAMERA,
        session_id=str(ctx.session_id),
    )


# -------------------------------------------------------------------- scene.render


def scene_render(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Render al" (spec §5)."""
    db = _require_db(ctx, TOOL_SCENE_RENDER)
    service = _service(ctx, TOOL_SCENE_RENDER)
    device_action = ctx.live.get("device_action")
    width = int(arguments["width"]) if isinstance(arguments.get("width"), int | float) else 320
    height = int(arguments["height"]) if isinstance(arguments.get("height"), int | float) else 240
    engine = (
        arguments.get("engine")
        if arguments.get("engine") in ("workbench", "eevee")
        else "workbench"
    )
    return service.render(
        db,
        device_action,
        target=_target(ctx, arguments),
        width=width,
        height=height,
        engine=engine,
        session_id=str(ctx.session_id),
    )


# ------------------------------------------------------------------- scene.inspect


def scene_inspect(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Sahnede ne var?" (spec §5) - a QUERY, mutates nothing."""
    db = _require_db(ctx, TOOL_SCENE_INSPECT)
    service = _service(ctx, TOOL_SCENE_INSPECT)
    device_action = ctx.live.get("device_action")
    return service.inspect(
        db, device_action, target=_target(ctx, arguments), session_id=str(ctx.session_id)
    )


# ------------------------------------------------------ B44: scene.animate / export

#: B44 (req 526): the rate a voice-made animation plays at, and its longest duration.
ANIMATION_FPS: Final = 24
MAX_ANIMATION_SECONDS: Final = 60.0


def scene_animate(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Küreye bir animasyon ekle." (B44 req 526): the object's channel moved from its
    CURRENT read-back value (or ``from``) to ``to`` over ``seconds`` - two keyframes, flat
    arguments (the model names numbers, never a list of objects)."""
    db = _require_db(ctx, TOOL_SCENE_ANIMATE)
    service = _service(ctx, TOOL_SCENE_ANIMATE)
    device_action = ctx.live.get("device_action")
    name = arguments.get("name")
    target_value = arguments.get("to")
    if (
        not isinstance(name, str)
        or not name
        or not (isinstance(target_value, list) and len(target_value) == 3)
    ):
        return service.clarification("Hangi nesneyi nereye hareket ettireyim efendim?")
    row = service.resolve_scene(db, _target(ctx, arguments))
    if row is None:
        return service.clarification("Önce bir sahne oluşturalım efendim.")
    if row.tool != "blender":
        return service.clarification(
            "Animasyonu şimdilik yalnız Blender sahnelerinde yapabiliyorum efendim."
        )
    channel = arguments.get("channel")
    if channel not in ("location", "rotation", "scale"):
        channel = "location"
    objects = (row.inspection_json or {}).get("objects") or []
    obj = next((o for o in objects if o.get("name") == name), None)
    if obj is None:
        return service.clarification(f"Sahnede {name} diye bir nesne yok efendim.")
    start_value = arguments.get("from")
    if not (isinstance(start_value, list) and len(start_value) == 3):
        start_value = obj.get(channel) or [0.0, 0.0, 0.0]
    seconds = arguments.get("seconds")
    duration = (
        min(float(seconds), MAX_ANIMATION_SECONDS)
        if isinstance(seconds, int | float) and seconds > 0
        else 2.0
    )
    end_frame = max(2, 1 + round(duration * ANIMATION_FPS))
    operations = [
        {"op": "set_frames", "start": 1, "end": end_frame, "fps": ANIMATION_FPS},
        {
            "op": "animate",
            "name": name,
            "channel": channel,
            "keyframes": [
                {"frame": 1, "value": [float(c) for c in start_value]},
                {"frame": end_frame, "value": [float(c) for c in target_value]},
            ],
        },
    ]
    return service.apply(
        db,
        device_action,
        target=str(row.id),
        operations=operations,
        capability=TOOL_SCENE_ANIMATE,
        session_id=str(ctx.session_id),
    )


def scene_export(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Sahneyi GLB olarak dışa aktar." (B44 req 527): the owner's format word wins, else
    the model's argument, else GLB. The file stays on the owner's disk beside the scene."""
    db = _require_db(ctx, TOOL_SCENE_EXPORT)
    service = _service(ctx, TOOL_SCENE_EXPORT)
    device_action = ctx.live.get("device_action")
    turn = _turn_record(ctx)
    fmt = turn.get("scene_format")
    if fmt not in ("glb", "fbx"):
        fmt = arguments.get("format") if arguments.get("format") in ("glb", "fbx") else "glb"
    row = service.resolve_scene(db, _target(ctx, arguments))
    if row is None:
        return service.clarification("Dışa aktarılacak bir sahne yok efendim.")
    if row.tool != "blender":
        return service.clarification(
            "Dışa aktarmayı şimdilik yalnız Blender sahnelerinde yapabiliyorum efendim."
        )
    return service.apply(
        db,
        device_action,
        target=str(row.id),
        operations=[{"op": "export", "format": fmt}],
        capability=TOOL_SCENE_EXPORT,
        session_id=str(ctx.session_id),
    )


def register_scene_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_SCENE_CREATE,
            description=(
                "Blender veya Unity'de YENİ, BOŞ bir 3B sahne oluşturur: 'Unity'de boş "
                "bir sahne oluştur', 'Blender'da yeni sahne aç' denince HER ZAMAN bu "
                "araç çağrılır. Araç (blender/unity) sahibin sözlerinden gelir. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": ["blender", "unity"]},
                    "project": {"type": "string", "maxLength": 64},
                    "scene": {"type": "string", "maxLength": 64},
                    "label": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=scene_create,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SCENE_ADD,
            description=(
                "ODAKTAKİ sahneye bir nesne (küp, küre, silindir, düzlem, ışık, kamera) "
                "EKLER: 'bir küp ekle', 'küre oluştur', 'bir ışık ekle'. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "cube",
                            "sphere",
                            "cylinder",
                            "plane",
                            "light_sun",
                            "light_point",
                            "camera",
                        ],
                    },
                    "name": {"type": "string", "maxLength": 64},
                    "location": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=scene_add,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SCENE_TRANSFORM,
            description=(
                "ODAKTAKİ sahnedeki bir nesneyi TAŞIR/DÖNDÜRÜR/BOYUTLANDIRIR: 'küpü "
                "sağa taşı', 'küreyi iki kat büyüt'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 64},
                    "location": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "rotation": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "scale": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=scene_transform,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SCENE_MATERIAL,
            description=(
                "ODAKTAKİ sahnedeki bir nesnenin RENGİNİ/malzemesini değiştirir: "
                "'küpü kırmızı yap'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 64},
                    "color": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "metallic": {"type": "number"},
                    "roughness": {"type": "number"},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=scene_material,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SCENE_LIGHT,
            description=(
                "ODAKTAKİ sahnedeki bir IŞIĞI ayarlar (gücünü artırır/azaltır): 'ışığı "
                "ayarla', 'ışığı artır'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 64},
                    "energy": {"type": "number"},
                    "target": {"type": "string", "maxLength": 200},
                    "color": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                },
                "additionalProperties": False,
            },
            handler=scene_light,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SCENE_CAMERA,
            description=(
                "ODAKTAKİ sahnede KAMERAYI bir nesneye ÇEVİRİR: 'kamerayı nesneye "
                "çevir'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 64},
                    "look_at": {"type": "string", "maxLength": 64},
                    "lens": {"type": "number", "minimum": 1, "maximum": 500},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=scene_camera,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SCENE_RENDER,
            description=(
                "ODAKTAKİ sahnenin bir GÖRÜNTÜSÜNÜ render eder: 'render al'. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "minimum": 1, "maximum": 1920},
                    "height": {"type": "integer", "minimum": 1, "maximum": 1080},
                    "engine": {"type": "string", "enum": ["workbench", "eevee"]},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=scene_render,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SCENE_ANIMATE,
            description=(
                "ODAKTAKİ Blender sahnesinde bir nesneye ANİMASYON ekler: 'küreye bir animasyon "
                "ekle', 'küpü yukarı kaldırarak canlandır'. 'name' nesne adı, 'channel' location/"
                "rotation/scale, 'to' hedef değer [x, y, z], 'from' başlangıç (verilmezse şimdiki "
                "değer), 'seconds' süre. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 64},
                    "channel": {"type": "string", "enum": ["location", "rotation", "scale"]},
                    "to": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "from": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "seconds": {"type": "number", "minimum": 0.1, "maximum": 60},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=scene_animate,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SCENE_EXPORT,
            description=(
                "ODAKTAKİ Blender sahnesini GLB ya da FBX olarak DIŞA AKTARIR (dosya sahibin "
                "diskinde, sahnenin yanında): 'sahneyi dışa aktar', 'sahneyi FBX olarak dışa "
                "aktar'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["glb", "fbx"]},
                    "target": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            handler=scene_export,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_SCENE_INSPECT,
            description=(
                "ODAKTAKİ sahnede NE OLDUĞUNU okur (hiçbir şeyi DEĞİŞTİRMEZ): 'sahnede "
                "ne var?'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=scene_inspect,
        )
    )
    return reg


__all__ = [
    "SCENE_TOOL_NAMES",
    "TOOL_SCENE_ADD",
    "TOOL_SCENE_CAMERA",
    "TOOL_SCENE_CREATE",
    "TOOL_SCENE_INSPECT",
    "TOOL_SCENE_LIGHT",
    "TOOL_SCENE_MATERIAL",
    "TOOL_SCENE_RENDER",
    "TOOL_SCENE_TRANSFORM",
    "register_scene_tools",
    "scene_add",
    "scene_camera",
    "scene_create",
    "scene_inspect",
    "scene_light",
    "scene_material",
    "scene_render",
    "scene_transform",
]
