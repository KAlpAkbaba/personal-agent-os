"""B25 req 701: the assistant can say what it can do — registered from
``tools.default_registry()`` by ONE added line, like every other family here.

The owner has had no way to find out what they may say to this system. There is no manual,
the tools are named for the people who wrote them, and the one surface that knows the whole
answer — the tool registry — was only ever read by the model. Asking out loud is the most
natural way to ask, so the answer has to exist as a tool before any router can reach it.

What this deliberately does NOT do is read the list aloud. A hundred and thirty tool names
is not an answer; it is a refusal wearing an answer's clothes. The spoken form says how many
things and which areas, and names the surface that can show them all — the constitution's
own rule for a finished task, applied to a question: notify briefly and wait. The full list
travels in the tool result for the surface that asked, never through the speaker.

The routing half — making "Neler yapabilirsin?" land on this tool without the model
guessing — is requirement 734 and belongs to B27. This is the thing 734 will route to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.voice import capabilities as caps

if TYPE_CHECKING:  # pragma: no cover - types only
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_NAME = "assistant.capabilities"

#: How many families the tool result names before it stops listing and starts counting.
#: The surface that asked can render all of them; a voice answer cannot.
MAX_FAMILIES_IN_RESULT = 40


def assistant_capabilities(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 701. "Neler yapabilirsin?" answered from the registry, not from a written list.

    `family` narrows the answer when the owner asked about one area ("alarmlarla ne
    yapabilirsin?"). An unknown family is not an error: the assistant says it has no such
    area and still reports what it does have, because refusing the whole question over one
    mistyped word is the failure this requirement exists to remove.
    """
    items = caps.capabilities()
    # B27 req 734: the area the OWNER named, read off the router's record of the turn
    # ("mail konusunda neler yapabilirsin?" -> "mail"); the model's own `family` argument
    # only when the router named none - the owner's words win, as everywhere else.
    turn = dict((ctx.context if ctx is not None else {}).get("last_utterance") or {})
    spoken = turn.get("capability_family")
    wanted = spoken if isinstance(spoken, str) and spoken.strip() else arguments.get("family")
    family = str(wanted).strip().lower() if isinstance(wanted, str) and wanted.strip() else None

    if family:
        chosen = [item for item in items if item.family == family]
        if not chosen:
            return {
                "family": family,
                "known": False,
                "count": 0,
                "families": caps.families(items)[:MAX_FAMILIES_IN_RESULT],
                "capabilities": [],
                "speech": (f"Efendim, '{family}' diye bir alanım yok. " + caps.speech(items)),
            }
        name = chosen[0].family_tr
        phrases = [phrase for item in chosen for phrase in item.phrases][:6]
        spoken = f"Efendim, {name} için {len(chosen)} şey yapabiliyorum."
        if phrases:
            spoken += " Örneğin: " + "; ".join(phrases[:3]) + "."
        return {
            "family": family,
            "known": True,
            "count": len(chosen),
            "families": [],
            "capabilities": [item.as_dict() for item in chosen],
            "speech": spoken,
        }

    return {
        "family": None,
        "known": True,
        "count": len(items),
        "families": caps.families(items)[:MAX_FAMILIES_IN_RESULT],
        "capabilities": [item.as_dict() for item in items],
        "speech": caps.speech(items),
    }


#: The tool's manifest entry, built inside `register` the way every sibling family does:
#: `tools` imports this module, so `ToolSpec` is not importable here at module scope.
CAPABILITIES_DESCRIPTION = (
    "Neler yapabildiğini SÖYLER: 'neler yapabilirsin', 'nelerden anlıyorsun', "
    "'sana ne diyebilirim', 'hangi konularda yardım edebilirsin' denince bu araç "
    "çağrılır. Bir alan sorulduysa ('alarmlarla ne yapabilirsin', 'hafızayla ilgili "
    "ne yapabilirsin') family argümanıyla daralt. Dönen 'speech' metnini aynen oku; "
    "listeyi TEK TEK OKUMA."
)

CAPABILITIES_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "family": {
            "type": "string",
            "description": (
                "Tek bir alan sorulduysa onun anahtarı (alarm, memory, mail, "
                "operator, research, …). Genel soruda boş bırak."
            ),
            "maxLength": 32,
        }
    },
    "required": [],
    "additionalProperties": False,
}


CHAT_TOOL_NAME = "assistant.chat"
CHAT_DESCRIPTION = (
    "YEREL MOD içindir: yönlendiricinin anlamadığı serbest bir soruyu ya da sohbeti küçük bir "
    "metin modeline sorar ve yanıtı döner. Hiçbir eylem yapmaz. Sahibin cümlesi turun "
    "kaydından okunur; argüman gerekmez. Dönen 'speech' metnini aynen oku."
)
CHAT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def assistant_chat(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ADR-0173 addendum: free conversation in the local mode. The question is the owner's own
    sentence from THIS turn's record (set only in a local session, only when the router
    understood nothing) - never an argument a caller wrote. No question on the turn is a
    no-op, which is what keeps a paid session's model from spending a second model on a
    question it can answer itself."""
    del arguments
    from app import assistant_chat as chat

    turn = dict(ctx.context.get("last_utterance") or {})
    question = str(turn.get("chat_question") or "").strip()
    if not question:
        # A paid session's model reached for it: nothing is said and nothing is spent - the
        # realtime model answers its own conversation. The note is for that model.
        return {
            "speech": "",
            "answered": False,
            "error_class": None,
            "note": "assistant.chat yalnız yerel modda kullanılır; soruyu kendin yanıtla.",
        }
    provider = ctx.live.get("chat_provider") or chat.build_chat_provider(ctx.live.get("settings"))
    session = str(ctx.session_id)
    now_tr = ctx.now.astimezone().strftime("%d.%m.%Y %H:%M")
    answer = provider.answer(question, history=chat.MEMORY.history(session), now_tr=now_tr)
    if answer.ok:
        chat.MEMORY.remember(session, question, answer.speech)
    return {
        "speech": answer.speech,
        "answered": answer.ok,
        "error_class": answer.error_class,
        "model": answer.model,
        "usage": {"input_tokens": answer.input_tokens, "output_tokens": answer.output_tokens},
    }


def register(registry: ToolRegistry) -> None:
    """Register the tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    registry.register(
        ToolSpec(
            name=TOOL_NAME,
            description=CAPABILITIES_DESCRIPTION,
            parameters=CAPABILITIES_PARAMETERS,
            handler=assistant_capabilities,
        )
    )
    registry.register(
        ToolSpec(
            name=CHAT_TOOL_NAME,
            description=CHAT_DESCRIPTION,
            parameters=CHAT_PARAMETERS,
            handler=assistant_chat,
        )
    )


__all__ = [
    "CAPABILITIES_DESCRIPTION",
    "CHAT_TOOL_NAME",
    "assistant_chat",
    "CAPABILITIES_PARAMETERS",
    "MAX_FAMILIES_IN_RESULT",
    "TOOL_NAME",
    "assistant_capabilities",
    "register",
]
