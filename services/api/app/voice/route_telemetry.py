"""B26 req 749/750: what the router decided, and how a wrong decision announces itself.

The seven misroutes the audit found were found by a person sitting down with 103 sentences.
Nothing in the running system would ever have reported them — the router resolved, the tool
ran, the screen automation went off, and no record anywhere said "this was wrong". A class
of defect that can only be found by a human reading a table will be back the week after the
table is put down.

So two things, and they are different jobs.

**749 — telemetry.** Every resolution is recorded: which intent, which rule matched, and
whether it routed at all. The owner's WORDS are not recorded here. The transcript is the
most private thing this system handles and the detector below does not need it: a misroute
announces itself in the SEQUENCE, not in the sentence.

**750 — detection.** The signature of a misroute is not something a router can notice about
itself — if it could see the mistake it would not make it. What it can notice is what the
owner does next. A person whose screen automation just went off because they asked about
software updates says "dur", or "hayır", or "iptal", within a few seconds. An acting intent
followed immediately by a stop or a negation is the shape, and it is the shape whether or
not anybody has written that particular sentence into a corpus.

This is deliberately a CANDIDATE detector. It reports; it changes no route and blocks no
tool. A detector that silently stopped resolving an intent because the owner coughed would
be a worse defect than the one it was built for.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from app.logging import get_logger
from app.voice.intents import Intent

logger = get_logger("app.voice.route_telemetry")

#: How long after an acting intent a stop or a negation still looks like a reaction to it.
#: Long enough for a person to notice the screen change and speak; short enough that the
#: next ordinary "dur" in a conversation is not read as a complaint about the last one.
MISROUTE_WINDOW_S: Final = 12.0

#: How many resolutions are kept in process. The ledger is the durable record; this ring is
#: what the detector reads, and it only ever looks at the last few seconds.
RING_SIZE: Final = 256

#: The intents that ACT on the world outside the conversation — the only ones whose misroute
#: costs the owner anything. A narration that reads the wrong paragraph is corrected by
#: saying so; a mail that was sent is sent.
ACTING_INTENTS: Final[frozenset[Intent]] = frozenset(
    {
        Intent.MAIL_SEND,
        Intent.CALENDAR_COMMIT,
        Intent.CALENDAR_PROPOSE,
        Intent.TYPE_TEXT,
        Intent.AMBIENT_POLICY_SET,
        Intent.DISPLAY_OFF,
        Intent.EYE_ENABLE,
        Intent.DEPLOY,
        Intent.RELEASE_ROLLBACK,
        Intent.MEMORY_FORGET,
        Intent.ALARM_CANCEL,
        Intent.ROUTINE_CANCEL,
        Intent.WINDOW_CLOSE,
        Intent.EXEC_CANCEL,
        Intent.APP_FACTORY_STOP,
        # B27: a cancelled research is not resumed, a deletion is asked for by name, and a
        # screenshot reads whatever is on the screen. (MEDIA_VOLUME is reversible with the
        # next sentence and deliberately not here.)
        Intent.RESEARCH_CANCEL,
        Intent.CALENDAR_CANCEL,
        Intent.SCREENSHOT_CAPTURE,
        # B30: an application closed, a process stopped, a service restarted - each is a
        # change on the machine the owner did not make with their own hands.
        Intent.APP_CLOSE,
        Intent.PROCESS_STOP,
        Intent.SERVICE_RESTART,
        # B31: a research paused or resumed by mistake changes what the machine does.
        Intent.RESEARCH_PAUSE,
        Intent.RESEARCH_RESUME,
        # B32: copies sent to the Recycle Bin - reversible, but a change the owner did not
        # make with their own hands.
        Intent.DOCUMENT_DEDUP,
        # B33 (469): the one lifecycle verb that removes something the owner had.
        Intent.NATIVE_UNINSTALL,
        # B35 (622/623): a queued defect spends the model budget and produces a branch.
        Intent.SELFDEV_FIX,
        Intent.SELFDEV_FEATURE,
        # B39 (127-130): a mission acts on the desktop the moment it starts.
        Intent.MISSION_START,
        # B34 (153-160): what changes the owner's files - a direct write/append, the
        # confirmation of a proposal, an undo, a move, a delete to the Recycle Bin.
        Intent.DOCUMENT_WRITE,
        Intent.DOCUMENT_APPEND,
        Intent.DOCUMENT_APPLY,
        Intent.DOCUMENT_UNDO,
        Intent.DOCUMENT_MOVE,
        Intent.DOCUMENT_DELETE,
    }
)

#: What "no, not that" sounds like on the next turn. STOP is the loudest, and the two cancel
#: intents are the owner undoing something by name.
REACTION_INTENTS: Final[frozenset[Intent]] = frozenset(
    {Intent.STOP, Intent.DISCARD, Intent.OPERATOR_CANCEL, Intent.EVOLUTION_CANCEL}
)

#: Bare negations the resolver does not give an intent of their own. Matched on the token
#: list the resolver already produced, never on the raw transcript.
REACTION_TOKENS: Final[frozenset[str]] = frozenset(
    {"hayır", "hayir", "yanlış", "yanlis", "iptal", "geri", "olmaz", "istemiyorum"}
)


@dataclass(frozen=True, slots=True)
class RouteEvent:
    """One resolution, without the owner's words."""

    at: datetime
    intent: Intent
    #: The rule that matched, as the resolver names it ("otomatik", "gönder", …). This is
    #: router vocabulary, not the owner's: it is what a person debugging a misroute needs
    #: and it says nothing about what was said.
    matched: str | None
    #: True when the utterance reached a capability at all.
    routed: bool
    #: The reaction words present on this turn, if any. A closed set (`REACTION_TOKENS`),
    #: so this is a flag rather than a transcript.
    reaction_words: tuple[str, ...] = ()
    session_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "intent": str(self.intent),
            "matched": self.matched,
            "routed": self.routed,
            "reaction_words": list(self.reaction_words),
            "session_id": self.session_id,
        }


@dataclass(frozen=True, slots=True)
class MisrouteCandidate:
    """An acting intent the owner appears to have objected to."""

    acted: RouteEvent
    reaction: RouteEvent
    seconds: float

    @property
    def why(self) -> str:
        how = str(self.reaction.intent) if self.reaction.intent is not Intent.NONE else "hayır"
        return (
            f"{self.acted.intent} ({self.acted.matched or 'eşleşme yok'}) "
            f"→ {self.seconds:.1f} sn sonra {how}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "acted": self.acted.as_dict(),
            "reaction": self.reaction.as_dict(),
            "seconds": round(self.seconds, 3),
            "why": self.why,
        }


def reaction_words(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """The closed-set reaction words present, in order. Never the whole token list."""
    return tuple(token for token in tokens if token in REACTION_TOKENS)


def is_reaction(event: RouteEvent) -> bool:
    return event.intent in REACTION_INTENTS or bool(event.reaction_words)


def candidates(
    events: list[RouteEvent] | tuple[RouteEvent, ...],
    *,
    window_s: float = MISROUTE_WINDOW_S,
) -> list[MisrouteCandidate]:
    """Every acting resolution the owner objected to within ``window_s``.

    Pure, and ordered: only a reaction that comes AFTER the action counts. The pairing is
    with the NEAREST preceding acting event rather than with all of them, because an owner
    saying "dur" once is objecting to one thing — reporting three candidates for one "dur"
    would drown the real one in the other two.
    """
    out: list[MisrouteCandidate] = []
    pending: RouteEvent | None = None
    for event in sorted(events, key=lambda item: item.at):
        if event.intent in ACTING_INTENTS and event.routed:
            pending = event
            continue
        if pending is None or not is_reaction(event):
            continue
        seconds = (event.at - pending.at).total_seconds()
        if 0 <= seconds <= window_s:
            out.append(MisrouteCandidate(acted=pending, reaction=event, seconds=seconds))
        pending = None
    return out


class RouteTelemetry:
    """The in-process ring the detector reads, and the ledger note it writes.

    One object rather than a module-level list, so a test gets its own and the app gets one
    injected the way every other runtime here is injected (`app.state`), never a singleton
    two tests can see at once.
    """

    def __init__(self, *, size: int = RING_SIZE) -> None:
        self._events: deque[RouteEvent] = deque(maxlen=size)

    @property
    def events(self) -> tuple[RouteEvent, ...]:
        return tuple(self._events)

    def record(
        self,
        intent: Intent,
        *,
        matched: str | None = None,
        tokens: tuple[str, ...] = (),
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> RouteEvent:
        event = RouteEvent(
            at=now or datetime.now(UTC),
            intent=intent,
            matched=matched,
            routed=intent is not Intent.NONE,
            reaction_words=reaction_words(tokens),
            session_id=session_id,
        )
        self._events.append(event)
        return event

    def candidates(self, *, window_s: float = MISROUTE_WINDOW_S) -> list[MisrouteCandidate]:
        return candidates(self.events, window_s=window_s)

    def latest_candidate(self, *, window_s: float = MISROUTE_WINDOW_S) -> MisrouteCandidate | None:
        found = self.candidates(window_s=window_s)
        return found[-1] if found else None


#: The one telemetry the running Cloud Core writes to. Module-level for the same reason the
#: operator registry is: `record_client_events` is a function, not a service object, and
#: threading a ring buffer through every call site would be a worse shape than one reset.
#: `tests/conftest.py` resets it before and after every test, like the others.
_TELEMETRY = RouteTelemetry()


def telemetry() -> RouteTelemetry:
    return _TELEMETRY


def reset_telemetry() -> None:
    """Start again with an empty ring. Called by the test fixture, never by the app."""
    global _TELEMETRY  # noqa: PLW0603 - the documented registry pattern in this codebase
    _TELEMETRY = RouteTelemetry()


def observe(
    db: Any,
    intent: Intent,
    *,
    matched: str | None,
    tokens: tuple[str, ...],
    session_id: str | None,
    now: datetime,
) -> MisrouteCandidate | None:
    """Record one resolution (req 749) and note a candidate if this turn made one (750).

    The candidate is written only when THIS event is the reaction that completed it, so one
    suspicion produces one ledger row rather than one per following turn.
    """
    ring = telemetry()
    event = ring.record(intent, matched=matched, tokens=tokens, session_id=session_id, now=now)
    candidate = ring.latest_candidate()
    if candidate is None or candidate.reaction is not event:
        return None
    note_candidate(db, candidate, now=now)
    logger.warning(
        "voice_misroute_suspected",
        intent=str(candidate.acted.intent),
        matched=candidate.acted.matched,
        seconds=round(candidate.seconds, 2),
    )
    return candidate


def note_candidate(db: Any, candidate: MisrouteCandidate, *, now: datetime) -> bool:
    """Write one candidate to the Activity Ledger. Never raises; returns whether it landed.

    `warning`, not `error`: a candidate is a suspicion, and the evidence for it is one
    person's next sentence. The Evolution Supervisor's own scan is what turns a repeated
    suspicion into an opportunity — this function's whole job is to make sure the suspicion
    exists somewhere other than in memory.
    """
    if db is None:
        return False
    try:
        from app.ledger import service as ledger_service
        from app.ledger.service import ActivityEvent
    except ImportError:  # pragma: no cover - the ledger is evidence, not a dependency
        return False
    try:
        ledger_service.record(
            db,
            ActivityEvent(
                event_type="voice.misroute_suspected",
                subsystem="voice",
                status="completed",
                severity="warning",
                action=str(candidate.acted.intent),
                occurred_at=now,
                factual_summary=(f"Yanlış yönlendirme şüphesi: {candidate.why}."),
                detail_json=candidate.as_dict(),
                source="live",
                source_ref=f"voice_misroute:{candidate.acted.at.isoformat()}",
                evidence_refs=[{"kind": "intent", "ref": str(candidate.acted.intent)}],
            ),
        )
        return True
    except Exception:  # noqa: BLE001 - the ledger is evidence, not a dependency
        logger.warning("voice_misroute_note_failed", intent=str(candidate.acted.intent))
        return False


__all__ = [
    "ACTING_INTENTS",
    "MISROUTE_WINDOW_S",
    "MisrouteCandidate",
    "REACTION_INTENTS",
    "REACTION_TOKENS",
    "RING_SIZE",
    "RouteEvent",
    "RouteTelemetry",
    "candidates",
    "is_reaction",
    "note_candidate",
    "observe",
    "reaction_words",
    "reset_telemetry",
    "telemetry",
]
