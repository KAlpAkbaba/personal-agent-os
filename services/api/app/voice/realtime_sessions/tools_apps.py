"""The App Factory's voice tools (docs/M23_APP_FACTORY_SPEC.md §5). Seven tools,
registered from ``tools.default_registry()`` by ONE added line
(:func:`register_apps_tools`), the same discipline ``tools_documents``/``tools_artifacts``
already establish for their own families.

There is NO delete tool here (ADR-0086 decision 5): "Projeyi sil" reaches none of these -
the corpus proves it. Every result is a receipt-shaped dict whose ``speech`` is read
verbatim; a target that cannot be resolved (no project ever scaffolded) is an honest
``needs_clarification``, never a guess.

``AppFactoryService`` and the device/browser ports both come from ``ctx.live``
(``app_factory_service``, ``device_action``, ``browser_gateway``) - ``app.main.create_app``
registers the real service on the SAME object every request shares; a test injects fakes
the same way (docs/M18_ACTION_CONTRACT.md §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.appfactory.service import AppFactoryService
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_APP_CREATE: Final = "app.create"
TOOL_APP_RUN: Final = "app.run"
TOOL_APP_TEST: Final = "app.test"
TOOL_APP_STOP: Final = "app.stop"
TOOL_APP_STATUS: Final = "app.status"
TOOL_APP_OPEN: Final = "app.open"
TOOL_APP_LIST: Final = "app.list"
#: B40 (req 435-437): the bounded fix loop over a failed test run.
TOOL_APP_FIX: Final = "app.fix"

APP_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_APP_CREATE,
    TOOL_APP_RUN,
    TOOL_APP_TEST,
    TOOL_APP_STOP,
    TOOL_APP_STATUS,
    TOOL_APP_OPEN,
    TOOL_APP_LIST,
)


def _service(ctx: ToolContext, tool: str) -> AppFactoryService:
    service = ctx.live.get("app_factory_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the app factory service"
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
    """The ONE router's record of this turn (``app_ref``/``app_template``/``app_name`` -
    the owner's WORDS, preferred over the model's own argument, the same rule M20/M22's
    own tools already follow)."""
    return dict(ctx.context.get("last_utterance") or {})


def _target(ctx: ToolContext, arguments: dict[str, Any], *, default: str = "current") -> str:
    turn = _turn_record(ctx)
    ref = turn.get("app_ref")
    if isinstance(ref, str) and ref:
        return ref
    raw = arguments.get("target")
    return str(raw) if isinstance(raw, str) and raw else default


# --------------------------------------------------------------------- app.create


def app_create(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bana bir görev takip uygulaması yap", "Küçük bir web sayfası uygulaması
    oluştur: adı Notlarım", "Komut satırı aracı yap: selamla ve say komutları"
    (spec §5)."""
    db = _require_db(ctx, TOOL_APP_CREATE)
    service = _service(ctx, TOOL_APP_CREATE)
    turn = _turn_record(ctx)
    device_action = ctx.live.get("device_action")

    template = turn.get("app_template") if isinstance(turn.get("app_template"), str) else None
    template = template or (
        str(arguments.get("template")) if isinstance(arguments.get("template"), str) else None
    )
    turn_name = turn.get("app_name")
    name = turn_name if isinstance(turn_name, str) and turn_name else None
    name = name or (str(arguments.get("name")) if isinstance(arguments.get("name"), str) else None)

    raw_spec = arguments.get("spec")
    spec_dict: dict[str, Any] = dict(raw_spec) if isinstance(raw_spec, dict) else {}
    # B40 (req 422): a sentence no template serves is the composed path - the owner's
    # own words (app_request from the ONE router) win over the model's `content`.
    request_text = turn.get("app_request") if isinstance(turn.get("app_request"), str) else None
    request_text = request_text or (
        str(arguments.get("content")) if isinstance(arguments.get("content"), str) else None
    )
    if not template and "template" not in spec_dict and request_text:
        composed = {"request": request_text}
        if name:
            composed["name"] = name
        return service.create(db, device_action, spec=composed, session_id=str(ctx.session_id))
    if template:
        spec_dict.setdefault("template", template)
    if name:
        spec_dict.setdefault("name", name)
    spec_dict.setdefault("name", "Adsız Uygulama")
    kind_by_template = {
        "task-tracker": "web_static",
        "static-page": "web_static",
        "cli-tool": "cli",
    }
    if "template" in spec_dict and "kind" not in spec_dict:
        spec_dict["kind"] = kind_by_template.get(spec_dict["template"])

    if spec_dict.get("template") == "cli-tool" and "commands" not in spec_dict:
        turn_commands = turn.get("app_commands")
        names = (
            [c for c in turn_commands if isinstance(c, str) and c]
            if isinstance(turn_commands, list)
            else []
        )
        raw_commands = arguments.get("commands")
        if not names and isinstance(raw_commands, list):
            names = [str(c) for c in raw_commands if isinstance(c, str) and c]
        # AppSpec requires at least one command for a cli-tool; a plain default keeps
        # the create from being refused when neither the owner's words nor the model
        # named one explicitly (a bare "komut satırı aracı yap").
        spec_dict["commands"] = [{"name": n} for n in names] or [{"name": "calistir"}]

    return service.create(db, device_action, spec=spec_dict, session_id=str(ctx.session_id))


# ------------------------------------------------------------------------ app.run


def app_run(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Uygulamayı çalıştır." (spec §5)."""
    db = _require_db(ctx, TOOL_APP_RUN)
    service = _service(ctx, TOOL_APP_RUN)
    device_action = ctx.live.get("device_action")
    return service.run(
        db, device_action, target=_target(ctx, arguments), session_id=str(ctx.session_id)
    )


def app_test(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Testleri çalıştır." (spec §5)."""
    db = _require_db(ctx, TOOL_APP_TEST)
    service = _service(ctx, TOOL_APP_TEST)
    device_action = ctx.live.get("device_action")
    return service.test(
        db, device_action, target=_target(ctx, arguments), session_id=str(ctx.session_id)
    )


def app_fix(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Testleri düzelt." / "Uygulamadaki hatayı düzelt." (B40 req 435-437)."""
    db = _require_db(ctx, TOOL_APP_FIX)
    service = _service(ctx, TOOL_APP_FIX)
    device_action = ctx.live.get("device_action")
    return service.fix(
        db, device_action, target=_target(ctx, arguments), session_id=str(ctx.session_id)
    )


def app_stop(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Uygulamayı durdur." (spec §5)."""
    db = _require_db(ctx, TOOL_APP_STOP)
    service = _service(ctx, TOOL_APP_STOP)
    device_action = ctx.live.get("device_action")
    return service.stop(
        db, device_action, target=_target(ctx, arguments), session_id=str(ctx.session_id)
    )


def app_status(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Uygulama çalışıyor mu?" (spec §5)."""
    db = _require_db(ctx, TOOL_APP_STATUS)
    service = _service(ctx, TOOL_APP_STATUS)
    device_action = ctx.live.get("device_action")
    return service.status(
        db, device_action, target=_target(ctx, arguments), session_id=str(ctx.session_id)
    )


def app_open(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Uygulamayı aç." (spec §5) - the browser evidence for web kinds, the folder
    through the existing M19 ``file.reveal``."""
    db = _require_db(ctx, TOOL_APP_OPEN)
    service = _service(ctx, TOOL_APP_OPEN)
    device_action = ctx.live.get("device_action")
    browser_gateway = ctx.live.get("browser_gateway")
    return service.open(
        db,
        device_action,
        browser_gateway,
        target=_target(ctx, arguments),
        session_id=str(ctx.session_id),
    )


def app_list(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Hangi uygulamaları yaptın?" (spec §5)."""
    del arguments
    db = _require_db(ctx, TOOL_APP_LIST)
    service = _service(ctx, TOOL_APP_LIST)
    return service.list(db, session_id=str(ctx.session_id))


# ------------------------------------------------------------ B41: the lifecycle tools

TOOL_APP_VERIFY: Final = "app.verify"
TOOL_APP_LOG: Final = "app.log"
TOOL_APP_PACKAGE: Final = "app.package"
TOOL_APP_LAUNCH: Final = "app.launch"
TOOL_APP_HISTORY: Final = "app.history"
TOOL_APP_RESUME: Final = "app.resume"
TOOL_APP_MODIFY: Final = "app.modify"


def app_verify(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Uygulamayı doğrula." / "Arayüzünü test et." (B41 req 443/444)."""
    db = _require_db(ctx, TOOL_APP_VERIFY)
    service = _service(ctx, TOOL_APP_VERIFY)
    return service.verify(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        session_id=str(ctx.session_id),
    )


def app_log(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Uygulamanın günlüğünü oku." (B41 req 445)."""
    db = _require_db(ctx, TOOL_APP_LOG)
    service = _service(ctx, TOOL_APP_LOG)
    return service.log(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        session_id=str(ctx.session_id),
    )


def app_package(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Uygulamayı paketle." (B41 req 441/446)."""
    db = _require_db(ctx, TOOL_APP_PACKAGE)
    service = _service(ctx, TOOL_APP_PACKAGE)
    return service.package(db, target=_target(ctx, arguments), session_id=str(ctx.session_id))


def app_launch(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Paketlenmiş sürümü başlat." (B41 req 442)."""
    db = _require_db(ctx, TOOL_APP_LAUNCH)
    service = _service(ctx, TOOL_APP_LAUNCH)
    return service.launch(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        session_id=str(ctx.session_id),
    )


def app_history(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu uygulamada neler yaptık?" (B41 req 447)."""
    db = _require_db(ctx, TOOL_APP_HISTORY)
    service = _service(ctx, TOOL_APP_HISTORY)
    return service.history(db, target=_target(ctx, arguments), session_id=str(ctx.session_id))


def app_resume(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Kitaplık uygulamasına devam edelim." (B41 req 448)."""
    db = _require_db(ctx, TOOL_APP_RESUME)
    service = _service(ctx, TOOL_APP_RESUME)
    turn = _turn_record(ctx)
    name = turn.get("app_name") if isinstance(turn.get("app_name"), str) else None
    name = name or (str(arguments.get("name")) if isinstance(arguments.get("name"), str) else None)
    return service.resume(
        db, target=_target(ctx, arguments), name=name, session_id=str(ctx.session_id)
    )


def app_modify(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu uygulamaya siparişlere teslim tarihi ekle." (B41 req 449/450) - the owner's
    own sentence (app_request from the ONE router) wins over the model's content."""
    db = _require_db(ctx, TOOL_APP_MODIFY)
    service = _service(ctx, TOOL_APP_MODIFY)
    turn = _turn_record(ctx)
    text = turn.get("app_request") if isinstance(turn.get("app_request"), str) else None
    text = text or (
        str(arguments.get("content")) if isinstance(arguments.get("content"), str) else ""
    )
    return service.modify(
        db,
        ctx.live.get("device_action"),
        target=_target(ctx, arguments),
        request=text or "",
        session_id=str(ctx.session_id),
    )


def register_apps_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_APP_CREATE,
            description=(
                "Sahibin bilgisayarında YENİ bir uygulama OLUŞTURUR: 'bana bir görev "
                "takip uygulaması yap', 'küçük bir web sayfası uygulaması oluştur', "
                "'komut satırı aracı yap' denince HER ZAMAN bu araç çağrılır. Şablon "
                "(task-tracker/static-page/cli-tool) ve isim sahibin sözlerinden gelir. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "template": {
                        "type": "string",
                        "enum": ["task-tracker", "static-page", "cli-tool"],
                    },
                    "name": {"type": "string", "maxLength": 100},
                    "spec": {"type": "object"},
                    "content": {
                        "type": "string",
                        "maxLength": 600,
                        "description": (
                            "The owner's whole sentence when no template fits: the records the "
                            "application keeps, their fields, whether it needs a login."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            handler=app_create,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_VERIFY,
            description=(
                "ODAKTAKİ çalışan uygulamanın ARAYÜZÜNÜ cihazın tarayıcısında doğrular ve yeniden "
                "başlatınca kaydın kaldığını kontrol eder: 'uygulamayı doğrula', 'arayüzünü test "
                "et'."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 100}},
                "additionalProperties": False,
            },
            handler=app_verify,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_LOG,
            description=(
                "ODAKTAKİ uygulamanın çalışma GÜNLÜĞÜNÜ cihazdan okur: 'uygulamanın günlüğünü "
                "oku', "
                "'logları göster'."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 100}},
                "additionalProperties": False,
            },
            handler=app_log,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_PACKAGE,
            description=(
                "ODAKTAKİ test edilmiş uygulamayı SÜRÜM PAKETİ olarak çıkarır: 'uygulamayı "
                "paketle', "
                "'sürüm paketi çıkar'."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 100}},
                "additionalProperties": False,
            },
            handler=app_package,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_LAUNCH,
            description=(
                "ODAKTAKİ uygulamanın PAKETLENMİŞ sürümünü kendi klasöründe başlatır: 'paketlenmiş "
                "sürümü başlat', 'sürümü çalıştır'."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 100}},
                "additionalProperties": False,
            },
            handler=app_launch,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_HISTORY,
            description=(
                "ODAKTAKİ uygulamanın GEÇMİŞİNİ anlatır (sürümler, olaylar, paketler): 'bu "
                "uygulamada "
                "neler yaptık', 'uygulamanın geçmişi'."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 100}},
                "additionalProperties": False,
            },
            handler=app_history,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_RESUME,
            description=(
                "Daha önce yapılmış bir uygulamaya GERİ DÖNER ve odağa alır: 'Kitaplık "
                "uygulamasına "
                "devam edelim', 'uygulamaya devam et'. 'name' alanına sahibin söylediği adı ver."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 100},
                    **{"name": {"type": "string", "maxLength": 100}},
                },
                "additionalProperties": False,
            },
            handler=app_resume,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_MODIFY,
            description=(
                "ODAKTAKİ bileşik uygulamaya ÖZELLİK EKLER (yeni kayıt türü, alan, giriş) ve yeni "
                "sürümü test eder: 'bu uygulamaya siparişlere teslim tarihi ekle'. 'content' "
                "alanına "
                "sahibin cümlesini aynen yaz."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 100},
                    **{"content": {"type": "string", "maxLength": 600}},
                },
                "additionalProperties": False,
            },
            handler=app_modify,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_FIX,
            description=(
                "ODAKTAKİ uygulamanın BAŞARISIZ testlerini analiz eder ve sınırlı bir döngüde "
                "düzeltmeyi dener: 'testleri düzelt', 'uygulamadaki hatayı düzelt' denince bu "
                "araç çağrılır. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 100}},
                "additionalProperties": False,
            },
            handler=app_fix,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_RUN,
            description=(
                "ODAKTAKİ uygulamayı ÇALIŞTIRIR (sınırlı bir işlemde): 'uygulamayı "
                "çalıştır'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=app_run,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_TEST,
            description=(
                "ODAKTAKİ uygulamanın KENDİ testlerini çalıştırır: 'testleri çalıştır'. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=app_test,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_STOP,
            description=(
                "ODAKTAKİ çalışan uygulamayı DURDURUR: 'uygulamayı durdur'. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=app_stop,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_STATUS,
            description=(
                "ODAKTAKİ uygulamanın ÇALIŞIP ÇALIŞMADIĞINI söyler: 'uygulama çalışıyor "
                "mu?'. Hiçbir şeyi DEĞİŞTİRMEZ. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=app_status,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_OPEN,
            description=(
                "ODAKTAKİ uygulamayı AÇAR (web ise tarayıcıda, klasörünü de Gezgin'de): "
                "'uygulamayı aç'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=app_open,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_APP_LIST,
            description=(
                "Sahibin bilgisayarında yapılmış TÜM uygulamaları LİSTELER: 'hangi "
                "uygulamaları yaptın?', 'neler yaptın?'. Dönen 'speech' metnini aynen "
                "oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=app_list,
        )
    )
    return reg


__all__ = [
    "APP_TOOL_NAMES",
    "TOOL_APP_CREATE",
    "TOOL_APP_LIST",
    "TOOL_APP_OPEN",
    "TOOL_APP_RUN",
    "TOOL_APP_STATUS",
    "TOOL_APP_STOP",
    "TOOL_APP_TEST",
    "app_create",
    "app_list",
    "app_open",
    "app_run",
    "app_status",
    "app_stop",
    "app_test",
    "register_apps_tools",
]
