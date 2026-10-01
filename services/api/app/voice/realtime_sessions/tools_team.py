"""The team's voice tool: ``team.status`` ("ekip ne yapıyor?"), one paragraph from the Ofis data.

Registered from ``tools.default_registry()`` by ONE added line (:func:`register_team_tools`).
It reads the same stores the page does (``app.team.office.office_view``) and says the answer
through :func:`app.team.speech.office_paragraph`; it changes nothing. The arguments object is
empty (the relay filters argument keys, and there is nothing to ask).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.team import approvals, office, speech
from app.team import store as team_store
from app.team.routes import DEFAULT_TEAM_ROOT

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_TEAM_STATUS: Final = "team.status"
TEAM_TOOL_NAMES: Final[tuple[str, ...]] = (TOOL_TEAM_STATUS,)


def team_status(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    del arguments
    root = ctx.live.get("team_root") or DEFAULT_TEAM_ROOT
    store = ctx.live.get("team_store") or team_store.FileStore(root)
    try:
        queue = store.read_queue()
    except (OSError, ValueError):
        queue = {"version": 1, "tasks": []}  # no store configured: the empty office
    view = office.office_view(
        queue,
        store.read_lock(),
        store.read_status(),
        approvals.list_pending(queue, root),
        team_store.utcnow(),
    )
    return {"status": "ok", "speech": speech.office_paragraph(view)}


def register_team_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_TEAM_STATUS,
            description=(
                "Ekibin ne yaptığını tek paragrafta söyler: 'Ekip ne yapıyor?', 'Ekip ne "
                "durumda?', 'Ofiste kim çalışıyor?', 'Ajanlar ne yapıyor?'. Ofis sayfasıyla "
                "aynı veriden gelir. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=team_status,
        )
    )
    return reg


__all__ = ["TEAM_TOOL_NAMES", "TOOL_TEAM_STATUS", "register_team_tools", "team_status"]
