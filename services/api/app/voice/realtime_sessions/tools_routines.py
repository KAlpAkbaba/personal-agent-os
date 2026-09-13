"""B14 req 287-291: the owner's voice over their own routines.

The matrix called 287 "the biggest invisible feature", and that was exact. The Routine
Engine has been complete since M18 — triggers, conditions, actions, firings, a ledger row
for every transition, a REST surface, a clock driving it every ten seconds — and the owner
had no way to reach any of it by speaking. Seven routines existed in production and every
one of them was created by the ALARM subsystem on the owner's behalf. Nothing the owner said
could make an eighth.

Five tools, and deliberately no sixth: create, list, cancel, pause, resume. There is no
`routine.edit` — editing a trigger by voice means reading a whole routine back to confirm
what is being changed, and a mis-heard edit to something that runs unattended every morning
is worse than being asked to say it again.

**What this module does NOT do.** It never validates a trigger, never decides whether a
routine may fire, never dispatches an action. `app.routines.service` owns all of that and
refuses in its own words; these tools carry the owner's sentence to it and read the answer
back. A tool that re-checked would be a second opinion about a rule with one owner.

**One kind of routine cannot be created by voice, and that is correct.** The relay refuses
any tool argument whose key matches `text`
(`app.voice.realtime_sessions.service.FORBIDDEN_KEY_PARTS`) — a transcript must never
travel as a tool argument, which is a privacy rule and not a bug. `voice_briefing`'s detail
IS a free-text payload, so a routine carrying one is refused before this module is reached.
Five of the six action kinds (`display_action`, `media_playback`, `browser_action`,
`alarm`, `wake_alarm`) carry no free text and work by voice; a text-carrying routine is
REST-only until there is a briefing action that names a SELECTOR rather than a payload.
Found by the owner-utterance corpus, recorded here rather than worked around.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

from app.routines import service as routines_service
from app.routines.actions import InvalidActionDescriptor
from app.routines.conditions import InvalidCondition
from app.routines.models import (
    ROUTINE_STATUS_ARMED,
    ROUTINE_STATUS_PAUSED,
    TRIGGER_KINDS,
)
from app.routines.state import IllegalRoutineTransition
from app.routines.triggers import InvalidTrigger
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_ROUTINE_CREATE: Final = "routine.create"
TOOL_ROUTINE_LIST: Final = "routine.list"
TOOL_ROUTINE_CANCEL: Final = "routine.cancel"
TOOL_ROUTINE_PAUSE: Final = "routine.pause"
TOOL_ROUTINE_RESUME: Final = "routine.resume"

ROUTINE_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_ROUTINE_CREATE,
    TOOL_ROUTINE_LIST,
    TOOL_ROUTINE_CANCEL,
    TOOL_ROUTINE_PAUSE,
    TOOL_ROUTINE_RESUME,
)

#: How many routines the spoken list reads out. A voice answer that runs to twenty items is
#: an answer nobody hears the end of; the panel and `/v1/routines` have the rest.
SPOKEN_LIST_MAX = 5


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _routine_id(arguments: dict[str, Any], tool: str) -> uuid.UUID:
    raw = arguments.get("routine_id")
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"{tool} needs the routine's id; '{raw}' is not one",
        ) from exc


def _summary(routine: Any) -> str:
    """One routine in one clause, for a spoken list."""
    when = _trigger_phrase(routine.trigger_kind, routine.trigger_json or {})
    tail = " (duraklatılmış)" if routine.status == ROUTINE_STATUS_PAUSED else ""
    return f"{routine.name} — {when}{tail}"


_WEEKDAY_TR: Final[tuple[str, ...]] = (
    "pazartesi",
    "salı",
    "çarşamba",
    "perşembe",
    "cuma",
    "cumartesi",
    "pazar",
)


def _trigger_phrase(kind: str, trigger: dict[str, Any]) -> str:
    """When a routine runs, in Turkish, from the trigger itself.

    Never a guess: an unknown shape says the kind rather than inventing a sentence, because
    a spoken answer the owner cannot check is worse than a blunt one they can.
    """
    if kind == "schedule":
        days = trigger.get("weekdays") or []
        time_str = trigger.get("time") or "?"
        if sorted(days) == [0, 1, 2, 3, 4]:
            return f"hafta içi {time_str}"
        if sorted(days) == list(range(7)):
            return f"her gün {time_str}"
        if sorted(days) == [5, 6]:
            return f"hafta sonu {time_str}"
        named = ", ".join(_WEEKDAY_TR[d] for d in sorted(days) if 0 <= d <= 6)
        return f"{named} {time_str}" if named else time_str
    if kind == "at":
        return f"bir kez: {trigger.get('at', '?')}"
    if kind == "presence":
        return f"olay: {trigger.get('event', '?')}"
    if kind == "condition":
        seconds = int(trigger.get("min_seconds", 0))
        if trigger.get("kind") == "device_idle":
            return f"bilgisayar {seconds // 60} dakika boşta kalınca"
        if trigger.get("kind") == "device_active":
            return "bilgisayar tekrar kullanılınca"
        return "koşul"
    return kind


# ------------------------------------------------------------------------ the tools


def routine_create(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 287. "Her sabah 08:00'de bana haberleri oku." — the owner's own routine.

    Every refusal comes from `app.routines.service` and is passed through in its own words.
    A trigger this module approved and the engine then rejected would be a routine the owner
    was told they had and does not.
    """
    db = _require_db(ctx, TOOL_ROUTINE_CREATE)
    name = str(arguments.get("name") or "").strip()
    if not name:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "routine.create needs a name")
    trigger_kind = str(arguments.get("trigger_kind") or "")
    if trigger_kind not in TRIGGER_KINDS:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"unknown trigger_kind {trigger_kind!r}; must be one of {TRIGGER_KINDS}",
        )
    try:
        routine = routines_service.create_routine(
            db,
            name=name,
            trigger_kind=trigger_kind,
            trigger=dict(arguments.get("trigger") or {}),
            conditions=list(arguments.get("conditions") or []),
            actions=list(arguments.get("actions") or []),
            source="voice",
        )
    except (InvalidTrigger, InvalidCondition, InvalidActionDescriptor) as exc:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, str(exc)) from exc

    return {
        "routine_id": str(routine.routine_id),
        "name": routine.name,
        "status": routine.status,
        "trigger_kind": routine.trigger_kind,
        "speech": f"Rutin kuruldu efendim: {_summary(routine)}.",
    }


def routine_list(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 288. "Hangi rutinlerim var?"

    Armed AND paused, because a paused routine is one the owner still has - leaving it out
    would answer "you have three" to somebody who has four and turned one off.
    """
    db = _require_db(ctx, TOOL_ROUTINE_LIST)
    include_paused = bool(arguments.get("include_paused", True))
    rows = list(routines_service.list_routines(db, status=ROUTINE_STATUS_ARMED, limit=100))
    if include_paused:
        rows += list(routines_service.list_routines(db, status=ROUTINE_STATUS_PAUSED, limit=100))
    rows.sort(key=lambda r: r.created_at, reverse=True)

    if not rows:
        return {"routines": [], "count": 0, "speech": "Kurulu rutininiz yok efendim."}

    spoken = "; ".join(_summary(r) for r in rows[:SPOKEN_LIST_MAX])
    more = len(rows) - SPOKEN_LIST_MAX
    tail = f" ve {more} tane daha" if more > 0 else ""
    return {
        "routines": [
            {
                "routine_id": str(r.routine_id),
                "name": r.name,
                "status": r.status,
                "trigger_kind": r.trigger_kind,
                "when": _trigger_phrase(r.trigger_kind, r.trigger_json or {}),
            }
            for r in rows
        ],
        "count": len(rows),
        "speech": f"{len(rows)} rutininiz var efendim: {spoken}{tail}.",
    }


def _transition(ctx: ToolContext, arguments: dict[str, Any], tool: str) -> Any:
    db = _require_db(ctx, tool)
    routine_id = _routine_id(arguments, tool)
    try:
        if tool == TOOL_ROUTINE_CANCEL:
            return routines_service.cancel_routine(
                db, routine_id, reason=arguments.get("reason") or "owner_voice"
            )
        if tool == TOOL_ROUTINE_PAUSE:
            return routines_service.pause_routine(
                db, routine_id, reason=arguments.get("reason") or "owner_voice"
            )
        return routines_service.resume_routine(db, routine_id)
    except routines_service.RoutineNotFoundError as exc:
        # `validation_error` rather than a new class: the taxonomy is deliberately small
        # (app.voice.errors), and from the owner's side "there is no such routine" IS a
        # problem with what they named. The message carries the fact.
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, str(exc)) from exc
    except IllegalRoutineTransition as exc:
        # The engine's refusal, passed through in its own words rather than rewritten: "a
        # completed routine cannot be paused" is a true sentence, and the owner is better
        # served hearing it than "olmadı".
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, str(exc)) from exc


def routine_cancel(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 289. "Sabah rutinini iptal et." — gone, and the id with it."""
    routine = _transition(ctx, arguments, TOOL_ROUTINE_CANCEL)
    return {
        "routine_id": str(routine.routine_id),
        "status": routine.status,
        "speech": f"{routine.name} iptal edildi efendim.",
    }


def routine_pause(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 290. "Sabah rutinini bu hafta durdur." — off for a while, not gone.

    The distinction is the whole point of the tool: before `paused` existed, honouring this
    sentence meant cancelling and re-creating, which loses the routine's id, its firings and
    everything the ledger recorded about it.
    """
    routine = _transition(ctx, arguments, TOOL_ROUTINE_PAUSE)
    return {
        "routine_id": str(routine.routine_id),
        "status": routine.status,
        "speech": f"{routine.name} duraklatıldı efendim; siz söyleyene kadar çalışmayacak.",
    }


def routine_resume(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 291. "Sabah rutinini geri aç."

    It does not replay what it slept through, and the sentence says so: the owner asked for
    those mornings not to happen, and quietly running four of them at once on resume would
    be the opposite of what they meant.
    """
    routine = _transition(ctx, arguments, TOOL_ROUTINE_RESUME)
    return {
        "routine_id": str(routine.routine_id),
        "status": routine.status,
        "speech": f"{routine.name} tekrar çalışıyor efendim.",
    }


# ------------------------------------------------------------------- registration

#: The three control tools take the same one argument. Shared as a constant rather than
#: repeated, which the indexer is fine with - it reads the tool's NAME, not its schema.
_CONTROL_PARAMETERS: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "routine_id": {"type": "string", "description": "Rutinin kimliği."},
        "reason": {"type": "string"},
    },
    "required": ["routine_id"],
    "additionalProperties": False,
}

_TRIGGER_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "description": (
        "The trigger's own fields. at: {at}. schedule: {weekdays, time, timezone}. "
        "presence: {event}. condition: {kind, min_seconds}."
    ),
    "additionalProperties": True,
}


def register_routine_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_ROUTINE_CREATE,
            description=(
                "Sahibin kendi rutinini kurar: bir tetikleyici, isteğe bağlı koşullar ve bir "
                "veya daha fazla eylem. 'Her sabah 08:00'de haberleri oku', 'evden çıkınca "
                "ışıkları kapat', 'bilgisayar boşta kalınca ekranı kapat' gibi cümleler için. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Rutinin adı."},
                    "trigger_kind": {"type": "string", "enum": list(TRIGGER_KINDS)},
                    "trigger": _TRIGGER_SCHEMA,
                    "conditions": {"type": "array", "items": {"type": "object"}},
                    "actions": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["name", "trigger_kind", "trigger", "actions"],
                "additionalProperties": False,
            },
            handler=routine_create,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ROUTINE_LIST,
            description=(
                "Kurulu rutinleri sayar ve okur; duraklatılmış olanlar da dahildir. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"include_paused": {"type": "boolean"}},
                "additionalProperties": False,
            },
            handler=routine_list,
        )
    )
    # Spelled out one by one rather than looped, and the reason is not style.
    #
    # `app.selfmodel.indexer` reads THIS FILE with `ast` to learn what capabilities exist,
    # and it can only see a `ToolSpec(name=...)` whose name is a literal. A loop over a
    # tuple hides the tool from the system's own model of itself: registered and running,
    # invisible to the index. `test_the_index_knows_every_tool_the_running_registry_knows`
    # caught exactly that here - three tools registered, three tools the self-model did not
    # know about - which is the same "115 registered, 0 indexed" failure it was written for.
    reg.register(
        ToolSpec(
            name=TOOL_ROUTINE_CANCEL,
            description=(
                "Bir rutini kalıcı olarak iptal eder. Dönen 'speech' metnini aynen oku."
            ),
            parameters=_CONTROL_PARAMETERS,
            handler=routine_cancel,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ROUTINE_PAUSE,
            description=(
                "Bir rutini geçici olarak durdurur. İptal DEĞİL: kimliği, geçmişi ve "
                "ayarları durur. Dönen 'speech' metnini aynen oku."
            ),
            parameters=_CONTROL_PARAMETERS,
            handler=routine_pause,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ROUTINE_RESUME,
            description=(
                "Duraklatılmış bir rutini tekrar çalıştırır. Dönen 'speech' metnini aynen oku."
            ),
            parameters=_CONTROL_PARAMETERS,
            handler=routine_resume,
        )
    )
    return reg


__all__ = [
    "ROUTINE_TOOL_NAMES",
    "SPOKEN_LIST_MAX",
    "TOOL_ROUTINE_CANCEL",
    "TOOL_ROUTINE_CREATE",
    "TOOL_ROUTINE_LIST",
    "TOOL_ROUTINE_PAUSE",
    "TOOL_ROUTINE_RESUME",
    "register_routine_tools",
    "routine_cancel",
    "routine_create",
    "routine_list",
    "routine_pause",
    "routine_resume",
]
