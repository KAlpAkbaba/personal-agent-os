"""watch-voice: the owner's watch over a public page, by voice.

Four tools - create, list, remove, forget all - over the ONE watch store
(``app.watch.service``), which decides what a watch may be and refuses in its own Turkish
words; these tools carry the owner's sentence to it and read the answer back.

**The owner's words win.** The router reads the condition, the interval, the page and the
name off the sentence (``ResolvedIntent.watch_*``, kept on the turn record) and they are
preferred over the model's arguments: "20 bin liranın altına inerse" is ``number_below:20000``
whatever number the model wrote. The words win the condition only when they said a whole one
(a number and a direction); otherwise the router carries None and the model's ``contains:``
stands - ``changed`` is the last default, never a spoken condition. The page is the one
thing a sentence rarely says ("Şu ürünün fiyatı..."): the realtime model fills ``url``; with
none anywhere - the free local mode - ONE missing-slot question is asked, "Hangi sayfayı
izleyeyim?", never a confirmation.

**No second confirmation** (owner rule 2026-09-18/19). A create reads back the domain, the
condition and the interval in one sentence and the watch exists; "Nöbeti kaldır" is the undo
(the watch created in this session is the one it removes).

Argument keys are only ``url``, ``condition``, ``label``, ``every_hours``, ``selector`` and
``watch_id``: the relay refuses a key carrying text/audio/token, and none of these do.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from app.narration.numbers import cardinal
from app.voice.errors import VoiceError, VoiceErrorClass
from app.watch import compare
from app.watch import service as watch_service
from app.watch.service import WatchRefused, WatchView

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_WATCH_CREATE: Final = "watch.create"
TOOL_WATCH_LIST: Final = "watch.list"
TOOL_WATCH_REMOVE: Final = "watch.remove"
TOOL_WATCH_FORGET_ALL: Final = "watch.forget_all"

WATCH_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_WATCH_CREATE,
    TOOL_WATCH_LIST,
    TOOL_WATCH_REMOVE,
    TOOL_WATCH_FORGET_ALL,
)
#: The two that may answer with one question instead of acting.
WATCH_CLARIFYING_TOOL_NAMES: Final[tuple[str, ...]] = (TOOL_WATCH_CREATE, TOOL_WATCH_REMOVE)

QUESTION_WHICH_PAGE: Final = "Hangi sayfayı izleyeyim?"
DEFAULT_EVERY_HOURS: Final = 6
#: How many names the spoken list reads out; the page has the rest.
SPOKEN_LIST_MAX: Final = 5
#: The session context key holding the watch this session created last ("Nöbeti kaldır").
CONTEXT_LAST_CREATED: Final = "watch_last_created"

_CLARIFY: Final = "needs_clarification"


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _turn(ctx: ToolContext, intent: str) -> dict[str, Any]:
    """The owner's last sentence, only when it was this tool's own intent."""
    record = ctx.context.get("last_utterance")
    if isinstance(record, dict) and record.get("intent") == intent:
        return record
    return {}


def _text(raw: object) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or url).lower()
    return host[4:] if host.startswith("www.") else host


def interval_phrase(hours: int) -> str:
    if hours == 1:
        return "saatte bir"
    if hours == 24:
        return "günde bir"
    if hours == 168:
        return "haftada bir"
    return f"{hours} saatte bir"


def condition_phrase(condition: str) -> str:
    """When the owner will hear, in Turkish ("değişince", "20.000 altına inince")."""
    parsed = compare.parse_condition(condition)
    if parsed.kind == compare.KIND_CONTAINS:
        return f"sayfada '{parsed.text}' görününce"
    if parsed.number is not None and parsed.kind == compare.KIND_BELOW:
        return f"değer {watch_service.format_number(parsed.number)} altına inince"
    if parsed.number is not None:
        return f"değer {watch_service.format_number(parsed.number)} üstüne çıkınca"
    return "sayfa değişince"


def _view_dict(view: WatchView) -> dict[str, Any]:
    return {
        "watch_id": view.id,
        "label": view.label,
        "url": view.url,
        "condition": view.condition,
        "every_hours": view.every_hours,
    }


def _hours(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


# ------------------------------------------------------------------------ the tools


def watch_create(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """'Şu sayfa değişince bana söyle.' - one watch, read back in one sentence."""
    db = _require_db(ctx, TOOL_WATCH_CREATE)
    said = _turn(ctx, "watch_create")
    url = _text(said.get("watch_url")) or _text(arguments.get("url"))
    if not url:
        return {"status": _CLARIFY, "speech": QUESTION_WHICH_PAGE, "missing": "url"}
    condition = (
        _text(said.get("watch_condition"))
        or _text(arguments.get("condition"))
        or compare.KIND_CHANGED
    )
    every_hours = (
        _hours(said.get("watch_every_hours"))
        or _hours(arguments.get("every_hours"))
        or DEFAULT_EVERY_HOURS
    )
    domain = domain_of(url)
    label = _text(arguments.get("label")) or _text(said.get("watch_label")) or domain
    selector = _text(arguments.get("selector")) or None
    try:
        view = watch_service.create_watch(
            db,
            url=url,
            condition=condition,
            label=label,
            every_hours=every_hours,
            selector=selector,
        )
    except WatchRefused as exc:
        return {"status": "refused", "reason": exc.code, "speech": exc.reason_tr}
    ctx.context[CONTEXT_LAST_CREATED] = view.id
    speech = (
        f"Tamam, {domain} sayfasına {interval_phrase(view.every_hours)} bakıp "
        f"{condition_phrase(view.condition)} haber vereceğim."
    )
    return {"status": "ok", **_view_dict(view), "speech": speech}


def watch_list(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """'Nöbetlerimi say.' - how many, and their names."""
    db = _require_db(ctx, TOOL_WATCH_LIST)
    views = watch_service.list_watches(db)
    if not views:
        return {"status": "ok", "watches": [], "count": 0, "speech": "Kurulu nöbetiniz yok."}
    names = ", ".join(view.label for view in views[:SPOKEN_LIST_MAX])
    more = len(views) - SPOKEN_LIST_MAX
    tail = f" ve {cardinal(more)} tane daha" if more > 0 else ""
    count = cardinal(len(views)).capitalize()
    return {
        "status": "ok",
        "watches": [_view_dict(view) for view in views],
        "count": len(views),
        "speech": f"{count} nöbetiniz var: {names}{tail}.",
    }


def _named(views: list[WatchView], label: str) -> list[WatchView]:
    from app.voice.intents import turkish_casefold

    words = turkish_casefold(label).split()
    return [view for view in views if all(w in turkish_casefold(view.label) for w in words)]


def _which(views: list[WatchView]) -> dict[str, Any]:
    names = " ya da ".join(view.label for view in views[:3])
    return {
        "status": _CLARIFY,
        "speech": f"Hangi nöbeti kaldırayım: {names}?",
        "candidates": [_view_dict(view) for view in views[:3]],
    }


def watch_remove(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """'Fiyat nöbetini kaldır.' by the name said; 'Nöbeti kaldır.' is the undo of the
    watch this session created, or the only one there is - otherwise one question."""
    db = _require_db(ctx, TOOL_WATCH_REMOVE)
    views = watch_service.list_watches(db)
    said = _turn(ctx, "watch_remove")
    label = _text(said.get("watch_label")) or _text(arguments.get("label"))
    target: WatchView | None = None
    raw_id = _text(arguments.get("watch_id"))
    if raw_id:
        target = next((view for view in views if view.id == raw_id), None)
    elif label:
        named = _named(views, label)
        if len(named) > 1:
            return _which(named)
        if not named:
            return {
                "status": "not_found",
                "speech": f"'{label}' adında bir nöbet bulamadım.",
            }
        target = named[0]
    else:
        last = ctx.context.get(CONTEXT_LAST_CREATED)
        target = next((view for view in views if view.id == last), None)
        if target is None and len(views) == 1:
            target = views[0]
        if target is None and len(views) > 1:
            return _which(views)
    if target is None:
        return {"status": "not_found", "speech": "Kaldırılacak bir nöbet bulamadım."}
    watch_service.remove_watch(db, uuid.UUID(target.id))
    if ctx.context.get(CONTEXT_LAST_CREATED) == target.id:
        ctx.context.pop(CONTEXT_LAST_CREATED, None)
    return {
        "status": "ok",
        **_view_dict(target),
        "speech": f"{target.label} nöbetini kaldırdım.",
    }


def watch_forget_all(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """'Nöbetleri unut.' - every watch and every reading, at once; says how many."""
    db = _require_db(ctx, TOOL_WATCH_FORGET_ALL)
    removed = watch_service.forget_all(db)
    ctx.context.pop(CONTEXT_LAST_CREATED, None)
    if not removed:
        return {"status": "ok", "removed": 0, "speech": "Unutulacak bir nöbet yok."}
    return {
        "status": "ok",
        "removed": removed,
        "speech": f"{cardinal(removed).capitalize()} nöbetin hepsini unuttum.",
    }


def register_watch_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    no_args = {"type": "object", "properties": {}, "additionalProperties": False}
    reg.register(
        ToolSpec(
            name=TOOL_WATCH_CREATE,
            description=(
                "Herkese açık bir SAYFAYA NÖBET kurar: 'Home Assistant'ın yeni sürümü "
                "çıkınca bana söyle', 'şu ürünün fiyatı 20 bin liranın altına inerse haber "
                "ver', 'bu sayfa değişince bana söyle'. 'url' sayfanın adresidir (sahip "
                "söylemediyse sen bul); koşulu ve aralığı sahibin cümlesinden SUNUCU okur. "
                "İkinci onay sorma. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "maxLength": 2048},
                    "condition": {
                        "type": "string",
                        "maxLength": 240,
                        "description": (
                            "changed, contains:<metin>, number_below:<sayı> ya da "
                            "number_above:<sayı>."
                        ),
                    },
                    "label": {"type": "string", "maxLength": 80},
                    "every_hours": {"type": "integer", "minimum": 1, "maximum": 168},
                    "selector": {"type": "string", "maxLength": 256},
                },
                "additionalProperties": False,
            },
            handler=watch_create,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_WATCH_LIST,
            description=(
                "Kurulu nöbetleri sayar: 'nöbetlerimi say', 'hangi nöbetlerim var'. Hiçbir "
                "şeyi değiştirmez. Dönen 'speech' metnini aynen oku."
            ),
            parameters=no_args,
            handler=watch_list,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_WATCH_REMOVE,
            description=(
                "Bir nöbeti KALDIRIR: 'fiyat nöbetini kaldır', 'nöbeti kaldır' (az önce "
                "kurulanı geri alır). Adı sahibin cümlesinden SUNUCU okur. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "label": {"type": "string", "maxLength": 80},
                    "watch_id": {"type": "string", "maxLength": 64},
                },
                "additionalProperties": False,
            },
            handler=watch_remove,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_WATCH_FORGET_ALL,
            description=(
                "BÜTÜN nöbetleri ve okumalarını hemen siler: 'nöbetleri unut', 'bütün "
                "nöbetleri sil'. 'Unutma' bu araç DEĞİLDİR. Dönen 'speech' metnini aynen oku."
            ),
            parameters=no_args,
            handler=watch_forget_all,
        )
    )
    return reg


__all__ = [
    "QUESTION_WHICH_PAGE",
    "WATCH_CLARIFYING_TOOL_NAMES",
    "WATCH_TOOL_NAMES",
    "condition_phrase",
    "domain_of",
    "interval_phrase",
    "register_watch_tools",
]
