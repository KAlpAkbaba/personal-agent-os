"""Narration command state machine (VOICE_SPEC §3).

States: IDLE / READING / PAUSED / EXPLAINING. Commands map from the Turkish voice
utterances "oku / dur / devam / tekrar / sonraki bölüm / ikinci maddeye geç /
bu ne demek? / hız" plus an explicit "explain-done".

Two hard invariants, enforced by the transition table and tested directly:

- **"dur" always wins.** From any state it moves to PAUSED and preserves the
  current cursor. It never errors.
- **explain-then-return.** "bu ne demek?" saves the EXACT current cursor and
  enters EXPLAINING; "explain-done" restores that exact cursor and resumes
  READING. The explanation never advances the narration position.

The machine is a pure function over ``NarrationState`` + ``NarrationPlan``; it
holds no audio and no I/O. Persistence maps ``NarrationState`` <-> a DB row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from app.narration.engine import Cursor, NarrationPlan


class State(StrEnum):
    IDLE = "IDLE"
    READING = "READING"
    PAUSED = "PAUSED"
    EXPLAINING = "EXPLAINING"


class Command(StrEnum):
    OKU = "oku"  # start / read
    DUR = "dur"  # stop (highest priority)
    DEVAM = "devam"  # resume from saved cursor
    TEKRAR = "tekrar"  # repeat last paragraph
    SONRAKI_BOLUM = "sonraki_bolum"  # jump to next section
    MADDEYE_GEC = "maddeye_gec"  # jump to a given item/paragraph
    ACIKLA = "acikla"  # "bu ne demek?" -> explanation mode
    ACIKLA_BITTI = "acikla_bitti"  # explanation finished -> return to saved cursor
    HIZ = "hiz"  # change playback speed


@dataclass(frozen=True)
class NarrationState:
    state: State = State.IDLE
    cursor: Cursor | None = None
    # cursor saved when entering EXPLAINING, to return to the EXACT position
    saved_cursor: Cursor | None = None
    # cursor at the start of the paragraph currently/last being read (for "tekrar")
    paragraph_anchor: Cursor | None = None
    speed: float = 1.0


@dataclass(frozen=True)
class CommandResult:
    state: NarrationState
    # a short action tag the caller/UI can act on (deterministic)
    action: str
    ok: bool = True
    message: str | None = None


# ------------------------------------------------------- Turkish intent parsing

_ORDINAL_WORDS = {
    "birinci": 1, "ilk": 1, "ikinci": 2, "üçüncü": 3, "dördüncü": 4, "beşinci": 5,
    "altıncı": 6, "yedinci": 7, "sekizinci": 8, "dokuzuncu": 9, "onuncu": 10,
}


@dataclass(frozen=True)
class ParsedCommand:
    command: Command
    speed: float | None = None
    target_index: int | None = None  # 1-based item/paragraph index for MADDEYE_GEC


def parse_utterance(text: str) -> ParsedCommand | None:
    """Map a Turkish utterance to a :class:`ParsedCommand`. Deterministic; returns
    None if nothing matches. "dur" is checked first so it always wins."""
    low = text.strip().casefold()
    if not low:
        return None
    # dur / stop / duraklat -- highest priority, matched first.
    if re.search(r"\b(dur|durdur|duraklat|stop|bekle)\b", low):
        return ParsedCommand(Command.DUR)
    if re.search(r"\b(bu ne demek|ne demek|açıkla|anlamı ne|açıklar mısın)\b", low):
        return ParsedCommand(Command.ACIKLA)
    if re.search(r"\b(açıklama bitti|devam et açıklamadan|geri dön)\b", low):
        return ParsedCommand(Command.ACIKLA_BITTI)
    if re.search(r"\b(devam|devam et|kaldığın yerden)\b", low):
        return ParsedCommand(Command.DEVAM)
    if re.search(r"\b(tekrar|tekrarla|yeniden oku|burayı tekrar)\b", low):
        return ParsedCommand(Command.TEKRAR)
    if re.search(r"\b(sonraki bölüm|sonraki başlık|bir sonraki bölüm|ikinci başlığa geç)\b", low):
        return ParsedCommand(Command.SONRAKI_BOLUM)
    # "ikinci maddeye geç" / "3. maddeye geç"
    m = re.search(r"(\d+)\s*\.?\s*madde", low)
    if m:
        return ParsedCommand(Command.MADDEYE_GEC, target_index=int(m.group(1)))
    for word, idx in _ORDINAL_WORDS.items():
        if word in low and "madde" in low:
            return ParsedCommand(Command.MADDEYE_GEC, target_index=idx)
    m = re.search(r"\bhız\w*\b.*?(\d+(?:[.,]\d+)?)", low)
    if m:
        return ParsedCommand(Command.HIZ, speed=float(m.group(1).replace(",", ".")))
    if re.search(r"\b(hızlan|daha hızlı)\b", low):
        return ParsedCommand(Command.HIZ, speed=1.25)
    if re.search(r"\b(yavaşla|daha yavaş)\b", low):
        return ParsedCommand(Command.HIZ, speed=0.75)
    if re.search(r"\b(oku|başla|okumaya başla|seslendir)\b", low):
        return ParsedCommand(Command.OKU)
    return None


# ----------------------------------------------------------- transition engine


def _paragraph_anchor_for(plan: NarrationPlan, cursor: Cursor | None) -> Cursor | None:
    if cursor is None:
        return None
    return plan.paragraph_start_cursor(cursor.paragraph_id)


def apply(
    state: NarrationState,
    parsed: ParsedCommand,
    plan: NarrationPlan,
) -> CommandResult:
    """Apply a parsed command to ``state`` given ``plan``. Pure function."""
    cmd = parsed.command

    # "dur" ALWAYS wins, from any state, and never errors.
    if cmd == Command.DUR:
        return CommandResult(
            state=replace(state, state=State.PAUSED),
            action="paused",
        )

    if cmd == Command.OKU:
        start = state.cursor or (plan.chunks[0].cursor if plan.chunks else None)
        return CommandResult(
            state=replace(
                state,
                state=State.READING,
                cursor=start,
                paragraph_anchor=_paragraph_anchor_for(plan, start),
            ),
            action="reading",
        )

    if cmd == Command.DEVAM:
        # Resume from the saved/last cursor. If we were EXPLAINING, prefer the
        # saved cursor so "devam" after an explanation also returns correctly.
        target = state.saved_cursor if state.state == State.EXPLAINING else state.cursor
        target = target or (plan.chunks[0].cursor if plan.chunks else None)
        return CommandResult(
            state=replace(
                state,
                state=State.READING,
                cursor=target,
                saved_cursor=None,
                paragraph_anchor=_paragraph_anchor_for(plan, target),
            ),
            action="reading",
        )

    if cmd == Command.TEKRAR:
        # Repeat the current paragraph from its first sentence.
        anchor = state.paragraph_anchor or _paragraph_anchor_for(plan, state.cursor)
        target = anchor or state.cursor
        return CommandResult(
            state=replace(
                state, state=State.READING, cursor=target, paragraph_anchor=anchor
            ),
            action="repeat_paragraph",
        )

    if cmd == Command.SONRAKI_BOLUM:
        nxt = plan.next_section_cursor(state.cursor)
        if nxt is None:
            return CommandResult(
                state=replace(state, state=State.PAUSED),
                action="end_of_document",
                ok=False,
                message="Sonraki bölüm yok.",
            )
        return CommandResult(
            state=replace(
                state, state=State.READING, cursor=nxt, paragraph_anchor=nxt
            ),
            action="jump_section",
        )

    if cmd == Command.MADDEYE_GEC:
        target = _nth_paragraph_cursor(plan, parsed.target_index or 1)
        if target is None:
            return CommandResult(
                state=state, action="jump_failed", ok=False, message="Böyle bir madde yok."
            )
        return CommandResult(
            state=replace(
                state, state=State.READING, cursor=target, paragraph_anchor=target
            ),
            action="jump_item",
        )

    if cmd == Command.ACIKLA:
        # Save the EXACT cursor and enter explanation mode.
        return CommandResult(
            state=replace(state, state=State.EXPLAINING, saved_cursor=state.cursor),
            action="explaining",
        )

    if cmd == Command.ACIKLA_BITTI:
        # Return to the EXACT saved cursor and resume reading.
        target = state.saved_cursor or state.cursor
        return CommandResult(
            state=replace(
                state,
                state=State.READING,
                cursor=target,
                saved_cursor=None,
                paragraph_anchor=_paragraph_anchor_for(plan, target),
            ),
            action="resumed_after_explain",
        )

    if cmd == Command.HIZ:
        new_speed = parsed.speed if parsed.speed is not None else state.speed
        new_speed = max(0.5, min(3.0, new_speed))
        return CommandResult(
            state=replace(state, speed=new_speed),
            action="speed_changed",
        )

    return CommandResult(state=state, action="noop", ok=False, message="Bilinmeyen komut.")


def _nth_paragraph_cursor(plan: NarrationPlan, n: int) -> Cursor | None:
    """1-based nth *content* paragraph (list items count as their own chunks)."""
    if n < 1:
        return None
    ordered_pids: list[str] = []
    for ch in plan.chunks:
        if ch.cursor.paragraph_id not in ordered_pids:
            ordered_pids.append(ch.cursor.paragraph_id)
    if n > len(ordered_pids):
        return None
    return plan.paragraph_start_cursor(ordered_pids[n - 1])


__all__ = [
    "State",
    "Command",
    "NarrationState",
    "CommandResult",
    "ParsedCommand",
    "parse_utterance",
    "apply",
]
