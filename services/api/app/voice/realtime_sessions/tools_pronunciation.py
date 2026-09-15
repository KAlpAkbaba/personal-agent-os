"""B21 req 228/229: the owner teaches a pronunciation by saying it.

The dictionary has existed since M4 with a REST surface, a unique key per (token, context),
a normaliser that applies it before every other rule, and — in production — zero rows. The
matrix's note says why in four words: "tek yazıcı manuel PUT". The only way to add a rule
was to send an HTTP request by hand, which is not a thing the owner of a voice-first
assistant does, and it is the reason a complete subsystem has never held a single entry.

The moment a pronunciation rule is worth writing is the moment it is wrong out loud. The
owner hears their surname mangled and says "adımı 'ak-ba-ba' diye oku" — and that sentence,
now, writes the rule and the assistant says it correctly for the rest of the conversation
(req 229 makes the same table part of the persona instruction).

**A pronunciation is not a memory.** It says nothing about the owner and holds no fact; it
is a rendering rule for one token. `memory.remember` would have been the lazy home for it
and would have made the memory subsystem's provenance meaningless. It also means this tool
is not sensitive in the way `memory.remember` is: the worst a wrong rule does is mispronounce
a word until the owner corrects it, which is what they were already suffering.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

from app.logging import get_logger
from app.narration import service as narration_service
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_pronunciation")

TOOL_PRONUNCIATION_TEACH: Final = "pronunciation.teach"
TOOL_PRONUNCIATION_LIST: Final = "pronunciation.list"
TOOL_PRONUNCIATION_FORGET: Final = "pronunciation.forget"

PRONUNCIATION_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_PRONUNCIATION_TEACH,
    TOOL_PRONUNCIATION_LIST,
    TOOL_PRONUNCIATION_FORGET,
)

#: A token is a word or a short phrase (a surname, a product name, a model number). Longer
#: than this and the owner is teaching a sentence, which is not what a pronunciation table
#: is for. No example here names the owner's own hardware: `test_multi_device_invariant`
#: forbids a single-machine literal anywhere in application code, comments included, and
#: it caught this line when the example was a device model.
MAX_TOKEN_CHARS = 64
#: The spoken form is Turkish letters and hyphens by nature; the bound is generous because
#: a long name can need several syllables of guidance.
MAX_SPOKEN_CHARS = 160
#: How many rules the spoken list reads out. The REST surface has the rest.
SPOKEN_LIST_MAX = 10


def _require_db(ctx: ToolContext, tool: str):  # noqa: ANN201 - Session, imported lazily
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs a database session",
        )
    return ctx.db


def _text(arguments: dict[str, Any], key: str, limit: int, tool: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, f"{tool}: '{key}' boş olamaz")
    if len(value) > limit:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"{tool}: '{key}' en fazla {limit} karakter olabilir",
        )
    return value


def pronunciation_teach(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 228. "PagentOS'u 'peycent os' diye oku."

    Upserts, because teaching the same word twice is a correction and not a second rule —
    the table's own unique key over (token, context) already says so, and a tool that
    raised on the second attempt would make the owner's natural correction an error.
    """
    db = _require_db(ctx, TOOL_PRONUNCIATION_TEACH)
    token = _text(arguments, "written_form", MAX_TOKEN_CHARS, TOOL_PRONUNCIATION_TEACH)
    spoken = _text(arguments, "spoken_form", MAX_SPOKEN_CHARS, TOOL_PRONUNCIATION_TEACH)
    scope = str(arguments.get("scope") or "").strip() or None

    entry = narration_service.upsert_pronunciation(
        db,
        token=token,
        spoken_form=spoken,
        context=scope,
        explicit=True,
        confidence=None,
    )
    logger.info("pronunciation_taught_by_voice", token=token, entry_id=str(entry.id))
    return {
        "status": "succeeded",
        "entry_id": str(entry.id),
        "token": entry.token,
        "spoken_form": entry.spoken_form,
        # The confirmation SAYS the rule back, in the spoken form, because the one thing
        # the owner cannot verify from "tamam efendim" is whether it was heard correctly.
        "speech": f"Tamam efendim, bundan sonra {token} kelimesini {spoken} diye okuyacağım.",
    }


def pronunciation_list(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 228. "Hangi telaffuz kurallarım var?" """
    db = _require_db(ctx, TOOL_PRONUNCIATION_LIST)
    entries = narration_service.list_pronunciations(db, limit=SPOKEN_LIST_MAX * 5)
    shown = entries[:SPOKEN_LIST_MAX]
    if not entries:
        return {
            "status": "succeeded",
            "count": 0,
            "entries": [],
            "speech": "Kayıtlı telaffuz kuralın yok efendim.",
        }
    said = "; ".join(f"{e.token}: {e.spoken_form}" for e in shown)
    more = "" if len(entries) <= len(shown) else f" Toplam {len(entries)} kural var."
    return {
        "status": "succeeded",
        "count": len(entries),
        "entries": [
            {"id": str(e.id), "token": e.token, "spoken_form": e.spoken_form} for e in shown
        ],
        "speech": f"Telaffuz kuralların: {said}.{more}",
    }


def pronunciation_forget(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 228. Removal takes an ID, from `pronunciation.list`.

    The same rule `memory.forget` follows and for a milder version of the same reason: a
    deletion resolved from a half-heard word is a deletion of the wrong row. Here the cost
    is one rule the owner has to teach again, so this is a smaller stake than a memory —
    but there is no reason to be careless where being careful costs one extra sentence.
    """
    db = _require_db(ctx, TOOL_PRONUNCIATION_FORGET)
    raw = str(arguments.get("entry_id") or "").strip()
    try:
        entry_id = uuid.UUID(raw)
    except ValueError as exc:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"{TOOL_PRONUNCIATION_FORGET}: 'entry_id' bir kimlik olmalı "
            "(önce pronunciation.list çağır)",
        ) from exc
    entry = narration_service.get_pronunciation(db, entry_id)
    if entry is None:
        return {
            "status": "failed",
            "speech": "Böyle bir telaffuz kuralı bulamadım efendim.",
        }
    token = entry.token
    narration_service.delete_pronunciation(db, entry_id)
    logger.info("pronunciation_forgotten_by_voice", token=token)
    return {
        "status": "succeeded",
        "token": token,
        "speech": f"{token} için telaffuz kuralını sildim efendim.",
    }


def register_pronunciation_tools(reg: ToolRegistry) -> ToolRegistry:
    """Registered one by one and never in a loop (`app.selfmodel.indexer` reads this with
    `ast` and can only see a `ToolSpec` whose `name` is a LITERAL — B14's lesson)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_PRONUNCIATION_TEACH,
            description=(
                "Bir kelimenin nasıl okunacağını kaydeder. Sahip 'şunu şöyle oku', "
                "'adımı yanlış söyledin, doğrusu ...' dediğinde çağır. 'written_form' yazılışı, "
                "'spoken_form' okunuşudur. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "written_form": {
                        "type": "string",
                        "description": "Yazıldığı hâli (örn. 'PagentOS').",
                    },
                    "spoken_form": {
                        "type": "string",
                        "description": "Okunuşu (örn. 'peycent os').",
                    },
                    "scope": {
                        "type": "string",
                        "description": "Yalnız belirli bir bağlamda geçerliyse.",
                    },
                },
                "required": ["written_form", "spoken_form"],
                "additionalProperties": False,
            },
            handler=pronunciation_teach,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_PRONUNCIATION_LIST,
            description=(
                "Kayıtlı telaffuz kurallarını okur. Sahip bir kuralı sildirmek istediğinde "
                "ÖNCE bunu çağır: silme aracı kimlik ister. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=pronunciation_list,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_PRONUNCIATION_FORGET,
            description=(
                "Bir telaffuz kuralını siler. 'entry_id' pronunciation.list çıktısından "
                "gelir; tahminle çağırma. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "entry_id": {
                        "type": "string",
                        "description": "pronunciation.list'ten gelen kimlik.",
                    }
                },
                "required": ["entry_id"],
                "additionalProperties": False,
            },
            handler=pronunciation_forget,
        )
    )
    return reg


__all__ = [
    "MAX_SPOKEN_CHARS",
    "MAX_TOKEN_CHARS",
    "PRONUNCIATION_TOOL_NAMES",
    "SPOKEN_LIST_MAX",
    "TOOL_PRONUNCIATION_FORGET",
    "TOOL_PRONUNCIATION_LIST",
    "TOOL_PRONUNCIATION_TEACH",
    "pronunciation_forget",
    "pronunciation_list",
    "pronunciation_teach",
    "register_pronunciation_tools",
]
