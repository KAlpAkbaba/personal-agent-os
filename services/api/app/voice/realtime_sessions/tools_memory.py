"""B16 req 31-38, 61-62: the owner's voice over their own memory.

`app.memory` is 3400 lines and complete — a frozen write-policy decision table with a
secrets guard, evidence and version chains, an audit log, hybrid retrieval, a promotion
ladder, a full REST surface. Nothing under `app/voice/` imported one line of it. The only
non-REST caller in the whole product is `app.experience`, which reads the Activity Ledger
and deliberately never touches conversation. So the owner could be taught nothing by
speaking, and everything the subsystem knew had arrived through a browser.

**Six tools, and the sixth is the one that makes the other five safe to say.**
`memory.forget` is a HARD delete — `app.memory.types.MemoryStatus` says so in as many
words: "Forgetting is a HARD delete (row + versions + evidence + embeddings), not a
status." That is the right product decision and it is why forgetting by voice takes an
**id**, never a description. The owner says "bunu unut", the model runs `memory.search`,
reads the matches back, and forgets the one the owner then names. A tool that deleted the
best fuzzy match for a sentence it half-heard would be the first irreversible mistake in
this product a person could make by mumbling. Same reasoning as B14's refusal to add
`routine.edit`, one step harder because there is no undo.

**Why `memory.remember` asserts `explicit=True`.** `policy.decide()` grants
`Actor.OWNER` only on a caller-asserted flag, and its comment names the threat: an
ingestion pipeline (browser, research, document) feeding text that says "always use ..."
must not mint an owner memory. A realtime session is the other thing entirely — it is the
owner speaking, through a step-up-gated SENSITIVE tool, on a verified device. That is the
same trust as the `/v1/memory/remember` REST route and the owner UI, which are exactly the
surfaces the policy names as allowed to set the flag. Recorded in docs/DECISIONS.md; the
alternative (a CANDIDATE row) would mean "sesle kalıcı bellek yazılır" is not true, since
a candidate is not durable.

**What travels as an argument.** `statement` is a sentence the model composed, the way
`routine.create` takes a structure the model composed — not a relayed transcript, which
`FORBIDDEN_KEY_PARTS` refuses by key on the way in. Automatic extraction from what was
actually said never crosses this boundary at all: it runs server-side over the session's
own stored summary (`app.memory.extraction`).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import TYPE_CHECKING, Any, Final

from app.logging import get_logger
from app.memory import receipts
from app.memory import service as memory_service
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.retrieval import RetrievalFilters, hybrid_search
from app.memory.types import MEMORY_CLASSES, Actor, MemoryClass
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_memory")

TOOL_MEMORY_REMEMBER: Final = "memory.remember"
TOOL_MEMORY_SEARCH: Final = "memory.search"
TOOL_MEMORY_FORGET: Final = "memory.forget"
TOOL_MEMORY_CORRECT: Final = "memory.correct"
TOOL_MEMORY_PIN: Final = "memory.pin"
TOOL_MEMORY_WHY: Final = "memory.why"

MEMORY_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_MEMORY_REMEMBER,
    TOOL_MEMORY_SEARCH,
    TOOL_MEMORY_FORGET,
    TOOL_MEMORY_CORRECT,
    TOOL_MEMORY_PIN,
    TOOL_MEMORY_WHY,
)

#: How many matches the spoken search reads out. Five is about as many as anyone can hold
#: while deciding which one to forget; `/v1/memory/search` has the rest.
SPOKEN_SEARCH_MAX = 5

#: The longest statement this will teach in one go. A memory is a fact, not a paragraph:
#: past this the owner is dictating a document, and `app.documents` is where that goes.
MAX_STATEMENT_CHARS = 500


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _embedder(ctx: ToolContext, tool: str) -> Any:
    """The process-wide memory runtime's embedder, through `ToolContext.live`.

    Never `DeterministicEmbedder()` constructed here. Production wires a real embedding
    provider behind the same protocol and `lifecycle.reindex` rebuilds the index for it;
    a tool that made its own would write vectors from a different model into the same
    table and every semantic search would quietly get worse (ADR-0078's own lesson: the
    unit tests injected runtimes into ToolContext.live and never noticed the route did
    not).
    """
    runtime = ctx.live.get("memory_runtime")
    if runtime is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the memory runtime; this session has none",
        )
    return runtime.embedder


def _memory_id(ctx: ToolContext, arguments: dict[str, Any], tool: str) -> uuid.UUID:
    """The id the owner means: the one named, or the one they were just read.

    B18 req 49. `memory.search` sets the durable object focus on every match it speaks, so
    a bare "bunu unut" resolves to the memory the owner has actually just heard - the same
    current/previous mechanism the window, document, message, draft, event, artifact,
    project, scene and creative families have used since M19.

    This is NOT the fuzzy matcher B16 refused. It resolves nothing from the sentence: it
    reads a record of what was said out loud, and when there is no such record it still
    refuses. The difference matters because `memory.forget` is a hard delete.
    """
    raw = arguments.get("memory_id")
    if raw:
        try:
            return uuid.UUID(str(raw))
        except (ValueError, AttributeError, TypeError) as exc:
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                f"{tool} needs the memory's id; '{raw}' is not one. "
                "Use memory.search first and act on the one the owner names.",
            ) from exc

    focused = _focused_memory(ctx)
    if focused is not None:
        return focused
    raise VoiceError(
        VoiceErrorClass.VALIDATION_ERROR,
        f"{tool} needs the memory's id, and nothing has been read back to the owner in "
        "this session to point at. Call memory.search first.",
    )


def _focused_memory(ctx: ToolContext) -> uuid.UUID | None:
    """The memory `memory.search` last spoke, or None. Never raises: a focus lookup that
    failed must read as "nothing to point at", which is the refusing direction."""
    if ctx.db is None:
        return None
    try:
        from app.operator import focus as focus_module
        from app.operator.models import FOCUS_KIND_MEMORY

        entry = focus_module.current(ctx.db, FOCUS_KIND_MEMORY)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("memory_focus_read_failed", error=f"{type(exc).__name__}: {exc}")
        return None
    if entry is None:
        return None
    try:
        return uuid.UUID(str(entry.object_id))
    except (ValueError, AttributeError, TypeError):
        return None


def _turn(ctx: ToolContext) -> dict[str, Any]:
    context = getattr(ctx, "context", None)
    return dict(context.get("last_utterance") or {}) if isinstance(context, dict) else {}


def _statement(arguments: dict[str, Any], tool: str, ctx: ToolContext | None = None) -> str:
    text = str(arguments.get("statement") or "").strip()
    if not text and ctx is not None:
        # ADR-0192: the local mode has no model to write the statement; the owner's own
        # sentence, with the command taken off, is on the turn record - but only when the
        # turn really was a "remember". An empty call with no such sentence behind it has
        # nothing of the owner's to write, and writing nothing is the honest answer.
        turn = _turn(ctx)
        if str(turn.get("intent") or "") == "memory_remember":
            text = str(turn.get("memory_statement") or "").strip()
    if not text:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, f"{tool} needs a statement")
    if len(text) > MAX_STATEMENT_CHARS:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"{tool} takes a fact, not a document ({len(text)} > {MAX_STATEMENT_CHARS} chars)",
        )
    return text


def _memory_class(
    arguments: dict[str, Any], default: MemoryClass = MemoryClass.PREFERENCE
) -> MemoryClass:
    """The ENUM member, not the string. `record_observation` reads `.value` off it, so a
    plain string reaches production as an AttributeError inside a tool call."""
    raw = str(arguments.get("memory_class") or default)
    if raw not in MEMORY_CLASSES:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"unknown memory_class {raw!r}; must be one of {MEMORY_CLASSES}",
        )
    return MemoryClass(raw)


def _voice_error(exc: MemorySubsystemError) -> VoiceError:
    """The memory subsystem's own refusal, in its own words.

    Particularly the secrets guard: when the owner says something that looks like a
    credential the answer they hear must be that it was refused and why, not a generic
    failure. A tool that re-worded it would be a second opinion about a rule with one
    owner.

    `validation_error` for all four reachable classes rather than new taxonomy members:
    `app.voice.errors` is deliberately small, and `tools_routines` already settled this for
    "there is no such routine" — from the owner's side a refused secret, a protected row
    and an unknown id are all problems with what they just said. The MESSAGE carries which,
    and it is the memory subsystem's own sentence.
    """
    by_class = {
        MemoryErrorClass.SECRET_REJECTED: VoiceErrorClass.VALIDATION_ERROR,
        MemoryErrorClass.EXPLICIT_PROTECTED: VoiceErrorClass.VALIDATION_ERROR,
        MemoryErrorClass.NOT_FOUND: VoiceErrorClass.VALIDATION_ERROR,
        MemoryErrorClass.VALIDATION_ERROR: VoiceErrorClass.VALIDATION_ERROR,
    }
    return VoiceError(
        by_class.get(exc.error_class, VoiceErrorClass.INTERNAL_BUG),
        exc.message,
        details={**dict(exc.details), "memory_error_class": str(exc.error_class)},
    )


def _links(ctx: ToolContext) -> Any:
    """B18 req 46/50: WHERE and WHEN this was taught, on the columns that already exist.

    `Memory.project_id` and `Memory.conversation_id` have been in the schema since M5,
    `RetrievalFilters` can scope by both and `hybrid_search` already scores a project
    match - and nothing in this product ever set either one. A link column nothing fills
    is a join nobody can make.

    The conversation is this realtime session, which is what makes "you told me this while
    we were talking about X" answerable at all.

    The project is the one the owner opened **during this conversation**, and the bound is
    the point. `app.operator.focus.current` has no staleness limit by design - a deictic
    word in a sentence means the most recent thing, however long ago it was - but a durable
    link stamped on every memory taught afterwards is a different question entirely. A
    project opened once in March would otherwise be attached to a preference taught in
    June, and a join that is always true is not a join. Comparing against the session's own
    `created_at` needs no magic window: it is exactly "the project context of this
    conversation".

    Never raises and never blocks the write: a memory with no links is still the memory the
    owner asked for.
    """
    from app.memory.service import MemoryLinks

    project_id = None
    if ctx.db is not None:
        try:
            project_id = _project_opened_in_this_conversation(ctx)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning("memory_project_link_failed", error=f"{type(exc).__name__}: {exc}")
            project_id = None
    return MemoryLinks(project_id=project_id, conversation_id=ctx.session_id)


def _project_opened_in_this_conversation(ctx: ToolContext) -> uuid.UUID | None:
    from app.operator import focus as focus_module
    from app.operator.models import FOCUS_KIND_PROJECT
    from app.voice.realtime_sessions.models import RealtimeSessionRow

    entry = focus_module.current(ctx.db, FOCUS_KIND_PROJECT)
    if entry is None:
        return None
    session_row = ctx.db.get(RealtimeSessionRow, ctx.session_id)
    if session_row is None or session_row.created_at is None:
        return None
    started = session_row.created_at
    selected = entry.selected_at
    if started.tzinfo is None:  # SQLite hands naive datetimes back
        started = started.replace(tzinfo=UTC)
    if selected.tzinfo is None:
        selected = selected.replace(tzinfo=UTC)
    if selected < started:
        return None
    return uuid.UUID(str(entry.object_id))


def _remember_focus(ctx: ToolContext, memory: Any) -> None:
    """Point the durable object focus at a memory the owner has just been read.

    Never raises. The focus is a convenience on top of an answer that has already been
    produced; a session whose focus could not be written still heard the search.
    """
    if ctx.db is None:
        return
    try:
        from app.operator import focus as focus_module
        from app.operator.models import FOCUS_KIND_MEMORY

        focus_module.set_focus(
            ctx.db,
            FOCUS_KIND_MEMORY,
            str(memory.id),
            label=str(memory.text or "")[:200],
            source="memory_search",
            owner_session_id=ctx.owner_session_id,
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("memory_focus_write_failed", error=f"{type(exc).__name__}: {exc}")
        # The rollback is the half that matters. A swallowed flush error leaves the session
        # in PendingRollbackError and the NEXT statement fails somewhere the owner cannot
        # connect to this - which is exactly what happened when this catch was first
        # written without it.
        try:
            ctx.db.rollback()
        except Exception:  # noqa: BLE001 - nothing further to do about it
            pass


def _source_ref(ctx: ToolContext, tool: str) -> str:
    """A receipt key unique to this tool call, so the ledger's idempotency helps rather
    than hides: a retried call writes one row, two different calls write two."""
    return f"voice:{ctx.session_id}:{ctx.call_id or tool}"


# ------------------------------------------------------------------------ the tools


def memory_remember(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 31/35. "Kahveyi sade severim, bunu hatırla."

    Durable immediately and superseding a keyed incumbent, because that is what the owner
    asked for. Every refusal comes from `app.memory` — above all the secrets guard, which
    is the one refusal the owner must actually HEAR: a password said out loud and quietly
    dropped is worse than one said out loud and refused out loud.
    """
    db = _require_db(ctx, TOOL_MEMORY_REMEMBER)
    embedder = _embedder(ctx, TOOL_MEMORY_REMEMBER)
    statement = _statement(arguments, TOOL_MEMORY_REMEMBER, ctx)
    memory_class = _memory_class(arguments)
    key = str(arguments.get("key") or "").strip() or None

    try:
        result = memory_service.remember_explicit(
            db,
            embedder,
            text=statement,
            memory_class=memory_class,
            key=key,
            links=_links(ctx),
            source={"kind": "owner", "channel": "voice", "session_id": str(ctx.session_id)},
        )
    except MemorySubsystemError as exc:
        raise _voice_error(exc) from exc

    # `ObserveResult` carries an id and an ACTION, not a row: "created", "corroborated",
    # "superseded_previous" - and "ignored", which is the one that matters here. An
    # explicit owner instruction cannot be ignored by the policy, so an id that comes back
    # None means something changed under this tool and the owner must not hear "aklımda".
    if result.memory_id is None:
        raise VoiceError(
            VoiceErrorClass.INTERNAL_BUG,
            f"the memory service wrote nothing ({result.action}: {result.reason})",
        )
    memory = memory_service.get_memory(db, result.memory_id)

    receipts.record_remembered(
        db,
        memory_id=memory.id,
        memory_class=memory.memory_class,
        text=memory.text,
        source="voice",
        source_ref=_source_ref(ctx, TOOL_MEMORY_REMEMBER),
        now=ctx.now,
        trace_id=str(ctx.session_id),
    )

    return {
        "memory_id": str(memory.id),
        "memory_class": memory.memory_class,
        "stage": memory.stage,
        "explicit": memory.explicit,
        "speech": "Aklımda efendim.",
    }


def memory_search(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 32. "Kahve hakkında ne biliyorsun?" — and the step every destructive tool
    below depends on, because it is what turns "bunu" into an id.

    Each match read back to the owner gets a `memory.used` receipt (req 62): this is the
    moment a memory leaves for somebody who will act on it.
    """
    db = _require_db(ctx, TOOL_MEMORY_SEARCH)
    embedder = _embedder(ctx, TOOL_MEMORY_SEARCH)
    query = str(arguments.get("query") or "").strip()
    if not query and "query" not in arguments:
        # ADR-0192: the subject the owner named, off the turn record, when no model wrote
        # one. "" is a real answer ("benim hakkımda ne biliyorsun" asks about everything).
        turn = _turn(ctx)
        if str(turn.get("intent") or "") == "memory_search":
            query = str(turn.get("memory_query") or "").strip()
    limit = max(1, min(int(arguments.get("limit") or SPOKEN_SEARCH_MAX), 20))
    requested = arguments.get("memory_class")
    filters = RetrievalFilters(memory_class=_memory_class(arguments).value if requested else None)

    try:
        hits = hybrid_search(db, embedder, query or None, filters, k=limit, now=ctx.now)
    except MemorySubsystemError as exc:
        raise _voice_error(exc) from exc

    if not hits:
        return {
            "memories": [],
            "count": 0,
            "speech": "Bu konuda kayıtlı bir şey bulamadım efendim.",
        }

    spoken = hits[:SPOKEN_SEARCH_MAX]
    # B18 req 49: what was READ ALOUD is what "bunu" points at. Set on the first match,
    # which is what a bare deictic means; an owner naming a later one ("ikincisini unut")
    # gives the model its id from the list below.
    #
    # BEFORE the receipts, not after: `set_focus` commits, and a failure inside it rolls
    # the session back - which would discard use receipts written just before it. Order
    # chosen so the cheaper thing is the one that can be lost.
    _remember_focus(ctx, spoken[0].memory)

    receipts.record_use(
        db,
        [(h.memory.id, h.memory.memory_class, h.memory.text) for h in spoken],
        reason="search",
        source="voice",
        source_ref=_source_ref(ctx, TOOL_MEMORY_SEARCH),
        now=ctx.now,
        trace_id=str(ctx.session_id),
    )

    listed = "; ".join(f"{i}. {h.memory.text}" for i, h in enumerate(spoken, start=1))
    more = len(hits) - len(spoken)
    tail = f" ve {more} tane daha" if more > 0 else ""
    return {
        "memories": [
            {
                "memory_id": str(h.memory.id),
                "text": h.memory.text,
                "memory_class": h.memory.memory_class,
                "explicit": h.memory.explicit,
                "pinned": h.memory.pinned,
                "confidence": h.memory.confidence,
            }
            for h in hits
        ],
        "count": len(hits),
        "speech": f"{len(hits)} kayıt buldum efendim: {listed}{tail}.",
    }


def memory_forget(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 36. "Onu unut." — and the reason this takes an id.

    `forget_memory` is a HARD delete: the row, its versions, its evidence and its
    embeddings. There is no undo, and `app.memory.types.MemoryStatus` says the product
    means it. So the id comes from a `memory.search` the owner heard read back, and this
    tool resolves nothing itself: the one irreversible operation in this subsystem is not
    given a fuzzy matcher.
    """
    db = _require_db(ctx, TOOL_MEMORY_FORGET)
    memory_id = _memory_id(ctx, arguments, TOOL_MEMORY_FORGET)

    try:
        memory = memory_service.get_memory(db, memory_id)
        text = memory.text
        counts = memory_service.forget_memory(
            db, memory_id, actor=Actor.OWNER, reason="voice_owner_request"
        )
    except MemorySubsystemError as exc:
        raise _voice_error(exc) from exc

    return {
        "memory_id": str(memory_id),
        "deleted": counts,
        "speech": f"Unuttum efendim: «{text}».",
    }


def memory_correct(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 37. "Hayır, sade değil, az şekerli."

    An EDIT and not a supersede: the owner is correcting what the system got wrong, not
    replacing a fact that used to be true. The distinction is in the history — an edit
    versions in place and keeps the previous text as a version, and `memory.why` can
    still say the row has been corrected.
    """
    db = _require_db(ctx, TOOL_MEMORY_CORRECT)
    embedder = _embedder(ctx, TOOL_MEMORY_CORRECT)
    memory_id = _memory_id(ctx, arguments, TOOL_MEMORY_CORRECT)
    statement = _statement(arguments, TOOL_MEMORY_CORRECT)

    try:
        memory = memory_service.edit_memory(
            db,
            embedder,
            memory_id,
            actor=Actor.OWNER,
            text=statement,
            change_reason="owner correction by voice",
        )
    except MemorySubsystemError as exc:
        raise _voice_error(exc) from exc

    return {
        "memory_id": str(memory.id),
        "version": memory.version,
        "text": memory.text,
        "speech": f"Düzelttim efendim: «{memory.text}».",
    }


def memory_pin(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 38. "Bunu sabitle." — never auto-expired, never auto-rewritten."""
    db = _require_db(ctx, TOOL_MEMORY_PIN)
    memory_id = _memory_id(ctx, arguments, TOOL_MEMORY_PIN)

    try:
        memory = memory_service.pin_memory(db, memory_id, actor=Actor.OWNER)
    except MemorySubsystemError as exc:
        raise _voice_error(exc) from exc

    return {
        "memory_id": str(memory.id),
        "pinned": memory.pinned,
        "speech": f"Sabitledim efendim: «{memory.text}». Bunu kendiliğimden unutmam.",
    }


def memory_why(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """req 61/62. "Bunu neden hatırlıyorsun?"

    `inspect_memory` has returned everything needed to answer this since M5 - provenance,
    evidence, versions, audit, conflicts - and nothing ever said it out loud. The answer
    is also a USE: the memory was read back to the owner, so it gets its receipt.
    """
    db = _require_db(ctx, TOOL_MEMORY_WHY)
    memory_id = _memory_id(ctx, arguments, TOOL_MEMORY_WHY)

    try:
        payload = memory_service.inspect_memory(db, memory_id)
    except MemorySubsystemError as exc:
        raise _voice_error(exc) from exc

    receipts.record_use(
        db,
        [(memory_id, str(payload.get("memory_class") or ""), str(payload.get("text") or ""))],
        reason="explain",
        source="voice",
        source_ref=_source_ref(ctx, TOOL_MEMORY_WHY),
        now=ctx.now,
        trace_id=str(ctx.session_id),
    )

    return {
        "memory_id": str(memory_id),
        "explicit": payload.get("explicit"),
        "confidence": payload.get("confidence"),
        "evidence_count": len(payload.get("evidence") or []),
        "conflict_count": len(payload.get("conflicts") or []),
        "speech": receipts.why_sentence(payload),
    }


# ------------------------------------------------------------------------ registration


_MEMORY_ID_SCHEMA: Final[dict[str, Any]] = {
    "type": "string",
    "description": "memory.search'ten gelen kaydın id'si. Tahmin etme; önce ara.",
}


def register_memory_tools(reg: ToolRegistry) -> ToolRegistry:
    """Registered one by one and never in a loop.

    `app.selfmodel.indexer` reads this file with `ast` and can only see a `ToolSpec` whose
    `name` is a LITERAL. B14 registered three routine tools in a loop over a tuple: they
    ran, they were reachable, and they were absent from the system's own index of what it
    can do until `test_the_index_knows_every_tool_the_running_registry_knows` said so.
    """
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_MEMORY_REMEMBER,
            description=(
                "Sahibin kalıcı olarak hatırlanmasını istediği bir şeyi yazar. "
                "'Kahveyi sade severim, bunu hatırla', 'bundan sonra raporları kısa tut' "
                "gibi cümleler için. 'statement' sahibin söylediğini tek cümlede özetler. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "statement": {
                        "type": "string",
                        "description": "Hatırlanacak tek cümlelik olgu.",
                    },
                    "memory_class": {"type": "string", "enum": list(MEMORY_CLASSES)},
                    "key": {
                        "type": "string",
                        "description": (
                            "Aynı konudaki eski kaydın yerine geçmesi için kararlı anahtar "
                            "(örn. 'coffee.style')."
                        ),
                    },
                },
                "required": ["statement"],
                "additionalProperties": False,
            },
            handler=memory_remember,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MEMORY_SEARCH,
            description=(
                "Kayıtlı bellekte arar ve bulduklarını numaralayarak okur. "
                "Sahip 'bunu unut', 'şunu düzelt' dediğinde ÖNCE bunu çağır: silme ve "
                "düzeltme araçları id ister, tahmin kabul etmez. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Aranacak konu."},
                    "memory_class": {"type": "string", "enum": list(MEMORY_CLASSES)},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=memory_search,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MEMORY_FORGET,
            description=(
                "Bir kaydı KALICI olarak siler; geri alınamaz. Yalnızca memory.search'ten "
                "gelen ve sahibe okunmuş bir id ile çağır — sahibin hangi kaydı kastettiği "
                "belli değilse önce ara ve sor. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"memory_id": _MEMORY_ID_SCHEMA},
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            handler=memory_forget,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MEMORY_CORRECT,
            description=(
                "Var olan bir kaydı sahibin düzeltmesiyle günceller; eski hâli sürüm "
                "olarak kalır. id memory.search'ten gelir. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": _MEMORY_ID_SCHEMA,
                    "statement": {"type": "string", "description": "Kaydın doğru hâli."},
                },
                "required": ["memory_id", "statement"],
                "additionalProperties": False,
            },
            handler=memory_correct,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MEMORY_PIN,
            description=(
                "Bir kaydı sabitler: kendiliğinden ne silinir ne değiştirilir. "
                "id memory.search'ten gelir. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"memory_id": _MEMORY_ID_SCHEMA},
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            handler=memory_pin,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MEMORY_WHY,
            description=(
                "Bir kaydı NEDEN hatırladığını anlatır: kim söyledi, kaç gözleme dayanıyor, "
                "çelişen bir şey duyuldu mu. 'Bunu neden hatırlıyorsun?' için. "
                "id memory.search'ten gelir. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"memory_id": _MEMORY_ID_SCHEMA},
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            handler=memory_why,
        )
    )
    return reg


__all__ = [
    "MAX_STATEMENT_CHARS",
    "MEMORY_TOOL_NAMES",
    "SPOKEN_SEARCH_MAX",
    "TOOL_MEMORY_CORRECT",
    "TOOL_MEMORY_FORGET",
    "TOOL_MEMORY_PIN",
    "TOOL_MEMORY_REMEMBER",
    "TOOL_MEMORY_SEARCH",
    "TOOL_MEMORY_WHY",
    "memory_correct",
    "memory_forget",
    "memory_pin",
    "memory_remember",
    "memory_search",
    "memory_why",
    "register_memory_tools",
]
