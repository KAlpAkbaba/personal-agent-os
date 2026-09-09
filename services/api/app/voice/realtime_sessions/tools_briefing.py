"""The morning briefing's voice tools (docs/DECISIONS.md ADR-0091). Three tools,
registered from ``tools.default_registry()`` by ONE added line
(:func:`register_briefing_tools`) — the combined briefing and the two narrower
single-topic reads (task brief §5: "weather vs system status vs combined briefing...
deterministic and distinct")."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.briefing.service import BriefingService
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_BRIEFING_MORNING: Final = "briefing.morning"
TOOL_BRIEFING_SYSTEM_STATUS: Final = "briefing.system_status"
TOOL_BRIEFING_OVERNIGHT_WORK: Final = "briefing.overnight_work"

BRIEFING_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_BRIEFING_MORNING,
    TOOL_BRIEFING_SYSTEM_STATUS,
    TOOL_BRIEFING_OVERNIGHT_WORK,
)


def _service(ctx: ToolContext, tool: str) -> BriefingService:
    service = ctx.live.get("briefing_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the briefing service"
        )
    return service


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _settings(ctx: ToolContext, tool: str) -> Any:
    settings = ctx.live.get("settings")
    if settings is None:
        raise VoiceError(VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs settings")
    return settings


def briefing_morning(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Günaydın." / "Sabah özetimi ver." / "Bugün beni neler bekliyor?" / "Sabah
    durumunu anlat." (task brief §4) — greeting, date/time, weather, system status,
    overnight work, CI/deploy state and calendar, from REAL sources only."""
    del arguments
    db = _require_db(ctx, TOOL_BRIEFING_MORNING)
    service = _service(ctx, TOOL_BRIEFING_MORNING)
    return service.build(
        db,
        settings=_settings(ctx, TOOL_BRIEFING_MORNING),
        live=ctx.live,
        device_id=ctx.device_id,
        session_id=str(ctx.session_id),
    )


def briefing_system_status(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Sistem durumu nasıl?" (task brief §4) — the system-status sentence ALONE, never
    the whole briefing."""
    del arguments
    db = _require_db(ctx, TOOL_BRIEFING_SYSTEM_STATUS)
    service = _service(ctx, TOOL_BRIEFING_SYSTEM_STATUS)
    return service.system_status(
        db,
        settings=_settings(ctx, TOOL_BRIEFING_SYSTEM_STATUS),
        live=ctx.live,
        session_id=str(ctx.session_id),
    )


def briefing_overnight_work(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Gece neler yaptın?" (task brief §4) — the overnight autonomous-work summary
    ALONE, from durable ledger evidence, never the whole briefing."""
    del arguments
    db = _require_db(ctx, TOOL_BRIEFING_OVERNIGHT_WORK)
    service = _service(ctx, TOOL_BRIEFING_OVERNIGHT_WORK)
    return service.overnight_work(db, session_id=str(ctx.session_id))


def register_briefing_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all three tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_BRIEFING_MORNING,
            description=(
                "Sabah özetini verir: 'Günaydın.', 'Sabah özetimi ver.', 'Bugün beni "
                "neler bekliyor?', 'Sabah durumunu anlat.'. Gerçek kaynaklardan derlenir "
                "(hava durumu, sistem durumu, gece yapılanlar, takvim); cevap "
                "veremeyen bir kaynak kısaca söylenir, ASLA uydurulmaz. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=briefing_morning,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_BRIEFING_SYSTEM_STATUS,
            description=(
                "SADECE sistem durumunu bildirir (sabah özetinin tamamını DEĞİL): "
                "'Sistem durumu nasıl?'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=briefing_system_status,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_BRIEFING_OVERNIGHT_WORK,
            description=(
                "SADECE gece yapılan otonom geliştirme etkinliğini bildirir (sabah "
                "özetinin tamamını DEĞİL): 'Gece neler yaptın?'. Dönen 'speech' metnini "
                "aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=briefing_overnight_work,
        )
    )
    return reg


__all__ = [
    "BRIEFING_TOOL_NAMES",
    "TOOL_BRIEFING_MORNING",
    "TOOL_BRIEFING_OVERNIGHT_WORK",
    "TOOL_BRIEFING_SYSTEM_STATUS",
    "briefing_morning",
    "briefing_overnight_work",
    "briefing_system_status",
    "register_briefing_tools",
]
