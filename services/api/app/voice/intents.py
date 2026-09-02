"""Turkish realtime intent resolver + narration bridge (M12 spec §5, ADR-0034 §4).

The provider gives us a transcript of what the owner just said; this module
decides what it MEANS for the assistant, against the live session state and
the narration state, and translates it into narration-engine cursor / speed
operations. It is semantic, not keyword-only:

- the text is passed through the existing tr-TR normaliser first ("2. maddeyi"
  -> "ikinci maddeyi"), then Turkish-casefolded (İ -> i, I -> ı) and tokenised;
- hesitation fillers ("şey", "yani", "hani", "ııı", "eee", "hmm", ...) are
  dropped, so "şey, yani biraz daha yavaş" resolves like "biraz daha yavaş";
- matching is on TOKENS, never substrings: "durum raporunu oku" does not stop,
  "durdur" does; the M4 ``STOP_WORDS`` keep their meaning and top priority;
- "devam" / "tekrar" resolve against state: with a narration paused they are
  narration operations, in a plain conversation they mean "continue / repeat
  what you were saying" (``scope``).

Nothing here touches audio, the network or the database. The bridge returns a
new ``NarrationState`` through the M4 command machine (``app.narration.commands``)
so "dur always wins" and the exact-cursor rules are inherited, not duplicated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from app.narration import commands
from app.narration.commands import Command, NarrationState, ParsedCommand, State
from app.narration.engine import PARAGRAPH_HEADING, Cursor, NarrationPlan
from app.narration.normalizer import normalize
from app.voice.realtime import STOP_WORDS, RealtimeState


class Intent(StrEnum):
    STOP = "stop"  # dur / kes / sus / yeter / durdur / duraklat / bekle
    RESUME = "resume"  # devam / kaldığın yerden / sürdür
    REPEAT = "repeat"  # tekrar (oku) / yeniden oku / bir daha
    REPEAT_ITEM = "repeat_item"  # ikinci maddeyi tekrar oku / üçüncü maddeye geç
    NEXT_ITEM = "next_item"  # sonraki madde
    PREVIOUS_ITEM = "previous_item"  # önceki madde
    FIRST_ITEM = "first_item"  # ilk madde
    LAST_ITEM = "last_item"  # son madde
    NEXT_SECTION = "next_section"  # sonraki bölüm / başlık
    SLOWER = "slower"  # biraz daha yavaş / yavaşla
    FASTER = "faster"  # biraz daha hızlı / hızlan
    SUMMARIZE = "summarize"  # özet geç / özetle / kısaca
    DETAIL = "detail"  # detaya gir / detaylandır / ayrıntı
    SKIP = "skip"  # burayı atla / bunu geç
    NONE = "none"


#: Scope tells the caller WHICH subsystem the intent targets, resolved from
#: state: a paused narration owns "devam"; a cancelled conversational answer
#: owns "devam" otherwise.
SCOPE_NARRATION = "narration"
SCOPE_CONVERSATION = "conversation"

PRESENTATION_SUMMARY = "summary"
PRESENTATION_DETAIL = "detail"

SPEED_STEP = 0.25

# Hesitation fillers (spec §5 hesitation guard vocabulary + common Turkish).
FILLERS = frozenset({
    "şey", "yani", "hani", "işte", "böyle", "ya", "yaa", "ee", "eee", "ıı", "ııı", "ı",
    "hmm", "hm", "hımm", "hım", "ehm", "aa", "aaa", "of", "e", "mm", "mmm",
})
_ELONGATED_FILLER = re.compile(r"^(?:ı{2,}|e{2,}|a{2,}|m{2,}|h[ıi]?m+|ee+h?|ya+)$")

# Stop tokens: the M4 STOP_WORDS (single-word members) plus imperative forms
# that M4's narration command parser already treats as "dur".
_SINGLE_STOP_WORDS = frozenset(w for w in STOP_WORDS if " " not in w)
_MULTI_STOP_PHRASES = tuple(w for w in STOP_WORDS if " " in w)
STOP_TOKENS = _SINGLE_STOP_WORDS | frozenset({"durdur", "duraklat", "bekle"})

_ORDINALS: dict[str, int] = {**commands._ORDINAL_WORDS}
# Stems, because Turkish suffixes soften the final consonant (başlık -> başlığa).
_ITEM_NOUNS = ("madde", "paragraf", "nokta", "başlı", "bölüm")
_SECTION_NOUNS = ("bölüm", "başlı")

_PUNCT_RE = re.compile(r"[^\w\s']", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ResolvedIntent:
    intent: Intent
    scope: str = SCOPE_CONVERSATION
    target_index: int | None = None  # 1-based item index for REPEAT_ITEM
    normalized_text: str = ""
    tokens: tuple[str, ...] = ()
    fillers_removed: int = 0
    confidence: float = 1.0
    matched: str = ""  # the token/phrase that decided it (for audit/debug)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "scope": self.scope,
            "target_index": self.target_index,
            "normalized_text": self.normalized_text,
            "fillers_removed": self.fillers_removed,
            "confidence": self.confidence,
            "matched": self.matched,
        }


# ------------------------------------------------------------- normalisation


def turkish_casefold(text: str) -> str:
    """Casefold that keeps Turkish dotted/dotless i distinct (str.lower maps
    'I' to 'i', which would turn 'ISI' into 'isi' instead of 'ısı')."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def is_filler(token: str) -> bool:
    return token in FILLERS or bool(_ELONGATED_FILLER.match(token))


def normalize_transcript(text: str) -> tuple[str, tuple[str, ...], int]:
    """(normalized text, content tokens, fillers removed).

    Runs the tr-TR normaliser so numerals become words ("2." -> "ikinci"),
    casefolds the Turkish way, strips punctuation and drops hesitation fillers.
    """
    if not text or not text.strip():
        return "", (), 0
    spoken = normalize(text.strip())
    lowered = turkish_casefold(spoken)
    cleaned = _PUNCT_RE.sub(" ", lowered)
    raw_tokens = [t.strip("'") for t in cleaned.split()]
    raw_tokens = [t for t in raw_tokens if t]
    tokens = [t for t in raw_tokens if not is_filler(t)]
    return " ".join(tokens), tuple(tokens), len(raw_tokens) - len(tokens)


# ---------------------------------------------------------------- resolution


def _has(tokens: tuple[str, ...], *stems: str) -> str | None:
    """First token that starts with one of ``stems`` (Turkish suffixes vary:
    maddeyi / maddeye / maddeden), or None."""
    for tok in tokens:
        for stem in stems:
            if tok == stem or tok.startswith(stem):
                return tok
    return None


def _has_exact(tokens: tuple[str, ...], *words: str) -> str | None:
    for tok in tokens:
        if tok in words:
            return tok
    return None


def _stop_match(text: str, tokens: tuple[str, ...]) -> str | None:
    for phrase in _MULTI_STOP_PHRASES:
        if re.search(rf"(?<!\S){re.escape(phrase)}(?!\S)", text):
            return phrase
    return _has_exact(tokens, *STOP_TOKENS)


def _scope_for(intent: Intent, narration: NarrationState | None) -> str:
    """Narration owns the intent when there is a narration in a non-idle state."""
    if narration is not None and narration.state != State.IDLE:
        return SCOPE_NARRATION
    if narration is not None and intent in (
        Intent.REPEAT_ITEM, Intent.NEXT_ITEM, Intent.PREVIOUS_ITEM,
        Intent.FIRST_ITEM, Intent.LAST_ITEM, Intent.NEXT_SECTION, Intent.SKIP,
    ):
        return SCOPE_NARRATION
    return SCOPE_CONVERSATION


def resolve_intent(
    text: str,
    *,
    session_state: RealtimeState | None = None,
    narration: NarrationState | None = None,
) -> ResolvedIntent:
    """Resolve a transcript into an :class:`Intent` against the live state.

    ``session_state`` is the M4 control FSM state of the conversation;
    ``narration`` the narration machine state when a narration is attached.
    Stop words win from ANY state (spec §5); everything else is resolved in
    a fixed priority order documented inline.
    """
    normalized, tokens, dropped = normalize_transcript(text)
    confidence = 1.0 if dropped == 0 else 0.9
    base: dict[str, Any] = {
        "normalized_text": normalized, "tokens": tokens,
        "fillers_removed": dropped, "confidence": confidence,
    }
    if not tokens:
        return ResolvedIntent(Intent.NONE, **{**base, "confidence": 0.0})

    # 1. stop — top priority in any state, including TOOL_RUNNING progress.
    stop = _stop_match(normalized, tokens)
    if stop:
        return ResolvedIntent(Intent.STOP, scope=_scope_for(Intent.STOP, narration),
                              matched=stop, **base)

    # 2. speed
    if tok := _has(tokens, "yavaş"):
        return ResolvedIntent(Intent.SLOWER, scope=SCOPE_NARRATION, matched=tok, **base)
    if tok := _has(tokens, "hızlı", "hızlan"):
        return ResolvedIntent(Intent.FASTER, scope=SCOPE_NARRATION, matched=tok, **base)

    # 3. presentation level
    if tok := _has(tokens, "özet", "kısaca", "kısa"):
        return ResolvedIntent(Intent.SUMMARIZE, scope=_scope_for(Intent.SUMMARIZE, narration),
                              matched=tok, **base)
    if tok := _has(tokens, "detay", "ayrıntı"):
        return ResolvedIntent(Intent.DETAIL, scope=_scope_for(Intent.DETAIL, narration),
                              matched=tok, **base)

    # 4. skip — "burayı atla", "bunu atla", "bunu geç", "burayı geç"
    if tok := _has(tokens, "atla"):
        return ResolvedIntent(Intent.SKIP, scope=_scope_for(Intent.SKIP, narration),
                              matched=tok, **base)
    if _has_exact(tokens, "geç") and _has_exact(tokens, "bunu", "burayı", "şunu", "burası"):
        return ResolvedIntent(Intent.SKIP, scope=_scope_for(Intent.SKIP, narration),
                              matched="geç", **base)

    # 5. item / section navigation
    item_noun = _has(tokens, *_ITEM_NOUNS)
    if item_noun:
        is_section = any(item_noun.startswith(n) for n in _SECTION_NOUNS)
        if _has(tokens, "sonraki", "bir sonraki", "diğer"):
            intent = Intent.NEXT_SECTION if is_section else Intent.NEXT_ITEM
            return ResolvedIntent(intent, scope=SCOPE_NARRATION, matched=item_noun, **base)
        if _has(tokens, "önceki", "evvelki"):
            return ResolvedIntent(Intent.PREVIOUS_ITEM, scope=SCOPE_NARRATION,
                                  matched=item_noun, **base)
        if _has_exact(tokens, "son", "sonuncu"):
            return ResolvedIntent(Intent.LAST_ITEM, scope=SCOPE_NARRATION,
                                  matched=item_noun, **base)
        for tok in tokens:
            if tok in _ORDINALS:
                n = _ORDINALS[tok]
                if n == 1 and tok == "ilk":
                    return ResolvedIntent(Intent.FIRST_ITEM, scope=SCOPE_NARRATION,
                                          target_index=1, matched=tok, **base)
                return ResolvedIntent(Intent.REPEAT_ITEM, scope=SCOPE_NARRATION,
                                      target_index=n, matched=tok, **base)
        if _has(tokens, "tekrar", "yeniden"):
            return ResolvedIntent(Intent.REPEAT, scope=_scope_for(Intent.REPEAT, narration),
                                  matched=item_noun, **base)

    # 6. repeat / resume
    if tok := _has(tokens, "tekrar", "yeniden"):
        return ResolvedIntent(Intent.REPEAT, scope=_scope_for(Intent.REPEAT, narration),
                              matched=tok, **base)
    if _has_exact(tokens, "daha") and _has_exact(tokens, "bir") and _has_exact(tokens, "oku",
                                                                               "söyle"):
        return ResolvedIntent(Intent.REPEAT, scope=_scope_for(Intent.REPEAT, narration),
                              matched="bir daha", **base)
    if tok := _has(tokens, "devam", "sürdür"):
        return ResolvedIntent(Intent.RESUME, scope=_scope_for(Intent.RESUME, narration),
                              matched=tok, **base)
    if _has_exact(tokens, "kaldığın", "kaldığımız") and _has(tokens, "yer"):
        return ResolvedIntent(Intent.RESUME, scope=_scope_for(Intent.RESUME, narration),
                              matched="kaldığın yerden", **base)

    return ResolvedIntent(Intent.NONE, **{**base, "confidence": 0.0})


# ------------------------------------------------------------------ bridge


@dataclass(frozen=True, slots=True)
class NarrationBridgeResult:
    """The narration-side effect of an intent (pure)."""

    state: NarrationState
    action: str
    ok: bool = True
    message: str | None = None
    presentation: str | None = None  # "summary" | "detail" when changed
    speed: float | None = None  # new speed when changed
    cursor: Cursor | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "ok": self.ok,
            "message": self.message,
            "presentation": self.presentation,
            "speed": self.speed,
            "cursor": self.cursor.as_dict() if self.cursor else None,
            "narration_state": self.state.state.value,
            **self.extra,
        }


def ordered_paragraph_ids(plan: NarrationPlan) -> list[str]:
    """Content items in document order. A "madde" is something the owner
    hears as an item: headings are navigation structure, not items, so
    "ikinci madde" is the second content paragraph, not the second block."""
    ordered: list[str] = []
    for ch in plan.chunks:
        pid = ch.cursor.paragraph_id
        para = plan.paragraphs.get(pid)
        if para is not None and para.kind == PARAGRAPH_HEADING:
            continue
        if pid not in ordered:
            ordered.append(pid)
    return ordered


def current_item_index(plan: NarrationPlan, cursor: Cursor | None) -> int:
    """1-based index of the content item the cursor is in (0 when no cursor
    or when the cursor sits on a heading)."""
    if cursor is None:
        return 0
    ordered = ordered_paragraph_ids(plan)
    try:
        return ordered.index(cursor.paragraph_id) + 1
    except ValueError:
        return 0


def _jump_to_item(state: NarrationState, plan: NarrationPlan, n: int, *,
                  action: str) -> NarrationBridgeResult:
    """Same state shape as the M4 ``MADDEYE_GEC`` transition, over content items."""
    ordered = ordered_paragraph_ids(plan)
    if n < 1 or n > len(ordered):
        return NarrationBridgeResult(state=state, action="jump_failed", ok=False,
                                     message="Böyle bir madde yok.", cursor=state.cursor)
    target = plan.paragraph_start_cursor(ordered[n - 1])
    new_state = replace(state, state=State.READING, cursor=target, paragraph_anchor=target)
    return NarrationBridgeResult(state=new_state, action=action, cursor=target)


def _via_commands(state: NarrationState, parsed: ParsedCommand, plan: NarrationPlan,
                  *, action: str | None = None) -> NarrationBridgeResult:
    res = commands.apply(state, parsed, plan)
    return NarrationBridgeResult(
        state=res.state, action=action or res.action, ok=res.ok, message=res.message,
        cursor=res.state.cursor,
    )


def apply_to_narration(
    resolved: ResolvedIntent, state: NarrationState, plan: NarrationPlan
) -> NarrationBridgeResult:
    """Translate a resolved intent into narration cursor / speed operations.

    Delegates every state change to the M4 command machine so its invariants
    hold ("dur" always wins and never errors; explain-then-return is intact).
    """
    intent = resolved.intent
    if intent == Intent.STOP:
        return _via_commands(state, ParsedCommand(Command.DUR), plan)
    if intent == Intent.RESUME:
        return _via_commands(state, ParsedCommand(Command.DEVAM), plan)
    if intent == Intent.REPEAT:
        return _via_commands(state, ParsedCommand(Command.TEKRAR), plan)
    if intent == Intent.REPEAT_ITEM:
        return _jump_to_item(state, plan, resolved.target_index or 1, action="jump_item")
    if intent == Intent.FIRST_ITEM:
        return _jump_to_item(state, plan, 1, action="jump_item")
    if intent == Intent.LAST_ITEM:
        return _jump_to_item(state, plan, len(ordered_paragraph_ids(plan)), action="jump_item")
    if intent in (Intent.NEXT_ITEM, Intent.SKIP):
        current = current_item_index(plan, state.cursor)
        res = _jump_to_item(state, plan, current + 1,
                            action="skipped" if intent == Intent.SKIP else "jump_item")
        if not res.ok:
            return replace(res, action="end_of_document",
                           message="Atlanacak bir sonraki madde yok.")
        return res
    if intent == Intent.PREVIOUS_ITEM:
        current = current_item_index(plan, state.cursor)
        return _jump_to_item(state, plan, max(1, current - 1), action="jump_item")
    if intent == Intent.NEXT_SECTION:
        return _via_commands(state, ParsedCommand(Command.SONRAKI_BOLUM), plan)
    if intent in (Intent.SLOWER, Intent.FASTER):
        delta = -SPEED_STEP if intent == Intent.SLOWER else SPEED_STEP
        res = _via_commands(state, ParsedCommand(Command.HIZ, speed=state.speed + delta), plan)
        return replace(res, speed=res.state.speed)
    if intent in (Intent.SUMMARIZE, Intent.DETAIL):
        level = PRESENTATION_SUMMARY if intent == Intent.SUMMARIZE else PRESENTATION_DETAIL
        return NarrationBridgeResult(state=state, action="presentation_changed",
                                     presentation=level, cursor=state.cursor)
    return NarrationBridgeResult(state=state, action="noop", ok=False,
                                 message="Bilinmeyen komut.", cursor=state.cursor)


__all__ = [
    "FILLERS",
    "PRESENTATION_DETAIL",
    "PRESENTATION_SUMMARY",
    "SCOPE_CONVERSATION",
    "SCOPE_NARRATION",
    "SPEED_STEP",
    "STOP_TOKENS",
    "Intent",
    "NarrationBridgeResult",
    "ResolvedIntent",
    "apply_to_narration",
    "current_item_index",
    "is_filler",
    "normalize_transcript",
    "ordered_paragraph_ids",
    "resolve_intent",
    "turkish_casefold",
]
