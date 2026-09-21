"""The recording on the session and the rows in ``voice_macros`` (ADR-0196).

Two states and one table:

* **Recording state** lives on the realtime session's ``context_json`` under
  :data:`RECORDING_KEY` - ``{"status": "recording" | "awaiting_name", "steps": [...],
  "started_at": iso}``. It is the session's because a recording is a conversation: the
  owner starts it, does things, ends it, names it. A session that closes mid-recording
  drops it, which is the honest outcome (nothing was named, nothing was kept).
* **Captured steps** are appended by the relay (``service.handle_tool_call``) for every
  tool call made while ``status == "recording"`` - the call as it was made, so a replay
  makes the same calls. Not the sentences: the relay's own rule keeps transcripts out of
  tool arguments, and a macro is a list of what was DONE.
* **Rows** are written once the name is spoken (:func:`save_macro`); one per name key.

This module executes nothing. Replaying is ``tools_macros.macro_run``'s, because only a
tool handler holds a ``ToolContext``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.macros.models import SOURCE_VOICE, VoiceMacroRow
from app.macros.naming import name_key

RECORDING_KEY: Final = "macro_recording"
STATUS_RECORDING: Final = "recording"
STATUS_AWAITING_NAME: Final = "awaiting_name"
#: Steps one recording keeps. A macro is a short habit ("open Gmail, click compose"),
#: not a workday; past this the recording keeps what it has and says so when ended.
MAX_MACRO_STEPS: Final = 30
#: Tool calls that are never a macro step: the macro words themselves (a macro that
#: records "hareketi bitir" would end its own replay), free conversation, and the
#: router's own intent probe.
NEVER_RECORDED_PREFIXES: Final[tuple[str, ...]] = ("macro.",)
#: ``research.start`` (security review, 2026-09-21): a crawl is a long job the relay gates
#: per turn (ADR-0075's follow-up refusal), not a desktop habit; a replay would start it
#: past that gate. The relay also skips every ``long_running`` tool for the same reason.
NEVER_RECORDED_TOOLS: Final[frozenset[str]] = frozenset(
    {"assistant.chat", "voice.intent", "research.start"}
)
#: Turn-record keys that describe the ROUTE, not the action; not kept in a step (they
#: would be stale on replay and the tools do not read them).
_TURN_KEYS_DROPPED: Final[frozenset[str]] = frozenset(
    {"at", "t_ms", "turn", "route_source", "route_confidence", "clarification_question"}
)


def _iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------ recording state


def recording_state(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """The session's recording, or None when nothing is being recorded or named."""
    state = ctx.get(RECORDING_KEY)
    if not isinstance(state, dict) or state.get("status") not in (
        STATUS_RECORDING,
        STATUS_AWAITING_NAME,
    ):
        return None
    return state


def is_recording(ctx: dict[str, Any]) -> bool:
    state = recording_state(ctx)
    return state is not None and state.get("status") == STATUS_RECORDING


def is_awaiting_name(ctx: dict[str, Any]) -> bool:
    state = recording_state(ctx)
    return state is not None and state.get("status") == STATUS_AWAITING_NAME


def begin_recording(ctx: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    state = {"status": STATUS_RECORDING, "steps": [], "started_at": _iso(now), "dropped": 0}
    ctx[RECORDING_KEY] = state
    return state


def is_recordable(tool: str) -> bool:
    return not tool.startswith(NEVER_RECORDED_PREFIXES) and tool not in NEVER_RECORDED_TOOLS


def _names_a_secret(arguments: dict[str, Any], turn: dict[str, Any] | None) -> bool:
    from app.voice.intents import contains_secret_reference

    texts = [v for v in arguments.values() if isinstance(v, str)]
    for key in ("text_to_type", "find_text", "replace_text", "question"):
        value = (turn or {}).get(key)
        if isinstance(value, str):
            texts.append(value)
    return any(contains_secret_reference(t) for t in texts)


def capture_step(
    ctx: dict[str, Any],
    *,
    tool: str,
    arguments: dict[str, Any],
    turn: dict[str, Any] | None,
    now: datetime,
) -> bool:
    """Keep one tool call on the recording; False when nothing was kept (not recording,
    a never-recorded tool, or the recording is full - counted in ``dropped``)."""
    state = recording_state(ctx)
    if state is None or state.get("status") != STATUS_RECORDING or not is_recordable(tool):
        return False
    if _names_a_secret(arguments, turn):
        # A step that would retype a password every replay is not kept, and the
        # recording says so at the end (security review, 2026-09-21). The keyword guard
        # is the same one operator.type applies; a secret VALUE with no such word around
        # it cannot be told from any other text and is a documented limitation (ADR-0196).
        ctx[RECORDING_KEY] = {**state, "dropped_secret": int(state.get("dropped_secret") or 0) + 1}
        return False
    steps = list(state.get("steps") or [])
    if len(steps) >= MAX_MACRO_STEPS:
        ctx[RECORDING_KEY] = {**state, "dropped": int(state.get("dropped") or 0) + 1}
        return False
    kept_turn = {k: v for k, v in dict(turn or {}).items() if k not in _TURN_KEYS_DROPPED}
    steps.append(
        {
            "tool": tool,
            "arguments": dict(arguments),
            "turn": kept_turn,
            "recorded_at": _iso(now),
        }
    )
    # A NEW dict every time, never the loaded one mutated in place: the session's
    # context_json is a plain JSON column, and SQLAlchemy writes it only when the value
    # it is given compares unequal to the one it loaded. A nested list appended in place
    # is the same object on both sides, equal to itself, and the step is silently lost -
    # which is exactly how the first version of this function lost every step.
    ctx[RECORDING_KEY] = {**state, "steps": steps}
    return True


def end_recording(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Recording -> awaiting a name; the state (with its steps), or None when nothing
    was being recorded. A recording with NO steps is closed instead of asking for a
    name: there is nothing to name."""
    state = recording_state(ctx)
    if state is None or state.get("status") != STATUS_RECORDING:
        return None
    if not state.get("steps"):
        ctx.pop(RECORDING_KEY, None)
        return state
    # Replaced, not mutated (see capture_step).
    ended = {**state, "status": STATUS_AWAITING_NAME}
    ctx[RECORDING_KEY] = ended
    return ended


def cancel_recording(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Drop the recording or the pending name; what was dropped, or None."""
    state = recording_state(ctx)
    if state is None:
        return None
    ctx.pop(RECORDING_KEY, None)
    return state


def take_pending(ctx: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The steps awaiting a name, removed from the session; None when none await."""
    state = recording_state(ctx)
    if state is None or state.get("status") != STATUS_AWAITING_NAME:
        return None
    ctx.pop(RECORDING_KEY, None)
    return [dict(s) for s in state.get("steps") or [] if isinstance(s, dict)]


# --------------------------------------------------------------------- rows


def save_macro(
    db: Session, *, name: str, steps: list[dict[str, Any]], now: datetime
) -> tuple[VoiceMacroRow, bool]:
    """Write (or replace, module docstring) the macro named ``name``; (row, replaced)."""
    key = name_key(name)
    if not key:
        raise ValueError("a macro needs a name with at least one word")
    if not steps:
        raise ValueError("a macro needs at least one step")
    row = find_macro(db, key)
    replaced = row is not None
    if row is None:
        row = VoiceMacroRow(name=name.strip()[:200], name_key=key, source=SOURCE_VOICE)
        db.add(row)
    row.name = name.strip()[:200]
    row.steps_json = [dict(s) for s in steps[:MAX_MACRO_STEPS]]
    row.step_count = len(row.steps_json)
    row.updated_at = now
    db.flush()
    return row, replaced


def find_macro(db: Session, key: str) -> VoiceMacroRow | None:
    if not key:
        return None
    return db.execute(
        select(VoiceMacroRow).where(VoiceMacroRow.name_key == key)
    ).scalar_one_or_none()


def find_macro_by_words(db: Session, words: str) -> VoiceMacroRow | None:
    """The macro the owner's words name, through ``name_key`` ("Yeni Mail Sekmesi
    hareketini" and "yeni mail sekmesi" are one key)."""
    return find_macro(db, name_key(words))


def list_macros(db: Session) -> list[VoiceMacroRow]:
    return list(
        db.execute(select(VoiceMacroRow).order_by(VoiceMacroRow.created_at.asc())).scalars()
    )


def name_keys(db: Session) -> tuple[str, ...]:
    """Every stored key, for the router's ``macro_names`` (ADR-0196)."""
    return tuple(db.execute(select(VoiceMacroRow.name_key)).scalars())


def delete_macro(db: Session, key: str) -> VoiceMacroRow | None:
    row = find_macro(db, key)
    if row is None:
        return None
    db.delete(row)
    db.flush()
    return row


def mark_run(row: VoiceMacroRow, *, now: datetime) -> None:
    row.run_count = int(row.run_count or 0) + 1
    row.last_run_at = now


__all__ = [
    "MAX_MACRO_STEPS",
    "NEVER_RECORDED_PREFIXES",
    "NEVER_RECORDED_TOOLS",
    "RECORDING_KEY",
    "STATUS_AWAITING_NAME",
    "STATUS_RECORDING",
    "begin_recording",
    "cancel_recording",
    "capture_step",
    "delete_macro",
    "end_recording",
    "find_macro",
    "find_macro_by_words",
    "is_awaiting_name",
    "is_recordable",
    "is_recording",
    "list_macros",
    "mark_run",
    "name_keys",
    "recording_state",
    "save_macro",
    "take_pending",
]
