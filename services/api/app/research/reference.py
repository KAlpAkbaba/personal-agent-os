"""Which research the owner just referred to (docs/DECISIONS.md ADR-0076).

Deterministic and pure over durable state: the focus stack
(``app.research.focus``), the pending clarification, and the completed research rows.
No model, no timing heuristic, and — the owner's directive, verbatim in spirit —
identity is ID-based, never title-based. Two runs may share a title; the owner's
2026-09-06 record has exactly that pair, and the whole defect was a server that had no
way to tell them apart, so it either guessed or asked the same question six times.

The order below IS the decision table. Each step either answers or hands on:

===== ============================================ =======================================
Order Reference                                    Answer
===== ============================================ =======================================
1     an explicit job id from the caller           ``resolved`` / ``explicit``
2     an answer to a live clarification            ``resolved`` / ``owner_selected_by_voice``
3     "bir önceki", "bundan önceki", "öncekini"    the previous focus, else ``missing``
4     "ikinci araştırma", "üçüncü"                 the N-th distinct focus, else ``missing``
5     "bu", "bunu", "onu", "az önceki", or nothing the current focus
6     a topic phrase ("OpenAI araştırmasını ...")  unique match, else the focus, else ask
===== ============================================ =======================================

Title equality NEVER creates ambiguity when a contextual identity is available: two runs
called "OpenAI son gelişmeler" are not ambiguous to someone who just heard one of them
announced. Ambiguity is what is left when NOTHING points at a run — and then the answer is
one short question, remembered so the owner can answer it by voice, never a guess and
never a crawl.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.narration.numbers import cardinal
from app.research import focus as focus_module
from app.research.focus import FocusEntry
from app.voice.intents import (
    RESEARCH_REFERENCE_CURRENT,
    RESEARCH_REFERENCE_NONE,
    RESEARCH_REFERENCE_ORDINAL,
    RESEARCH_REFERENCE_PREVIOUS,
    RESEARCH_REFERENCE_SELECTION,
    ResearchReference,
    classify_research_reference,
    normalize_transcript,
    turkish_casefold,
)

#: The owner's wall clock. A research the owner remembers as "20:19'daki" is 20:19 where
#: the owner was standing, never UTC (the production record's own timestamps are Z, and
#: reading those back to the owner is how a clarification becomes unanswerable).
OWNER_TZ = ZoneInfo("Europe/Istanbul")

STATUS_RESOLVED = "resolved"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_MISSING = "missing"

REASON_EXPLICIT = "explicit"
REASON_OWNER_SELECTED_BY_VOICE = "owner_selected_by_voice"
REASON_PREVIOUS_FOCUS = "previous_focus"
REASON_ORDINAL_FOCUS = "ordinal_focus"
REASON_CURRENT_FOCUS = "current_focus"
REASON_TOPIC_UNIQUE = "topic_unique"
REASON_ONLY_COMPLETED = "only_completed"
REASON_LATEST_COMPLETED = "latest_completed"
REASON_NO_PREVIOUS = "no_previous_research"
REASON_NO_NTH = "no_nth_research"
REASON_NO_RESEARCH = "no_completed_research"
REASON_AMBIGUOUS_TOPIC = "ambiguous_topic"
REASON_AMBIGUOUS_NO_FOCUS = "ambiguous_no_focus"

#: The one question asked when nothing points at a run — including when there is no run at
#: all. "Hangi araştırmayı kastediyorsunuz efendim?" is the honest answer to "Teknik
#: anlat." on an empty history; a crawl is not (ADR-0076 decision 5).
MISSING_QUESTION_TR = "Hangi araştırmayı kastediyorsunuz efendim?"
NO_PREVIOUS_QUESTION_TR = (
    "Bundan öncesi için kayıtlı bir araştırmam yok efendim; hangisini kastediyorsunuz?"
)

#: How many completed runs a topic phrase may search. Wider than the focus stack: the
#: owner may name a topic they have not talked about in this conversation at all.
TOPIC_LOOKBACK = 20

_MONTHS_TR = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)

_BACK_VOWELS = "aıouâ"
_FRONT_VOWELS = "eiöüî"
_VOICELESS = "fstkçşhp"


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    """What the server decided, and the record of why."""

    status: str
    research_job_id: str | None = None
    artifact_id: str | None = None
    reason: str = ""
    candidates: tuple[FocusEntry, ...] = ()
    question: str | None = None
    #: current | previous | ordinal | topic | selection | none (ADR-0076).
    reference: str = RESEARCH_REFERENCE_NONE
    #: Why the entry it resolved to was in focus in the first place, when it came from
    #: the focus stack: research_just_completed, owner_selected_in_ui, ...
    focus_source: str = ""
    entry: FocusEntry | None = None

    @property
    def resolved(self) -> bool:
        return self.status == STATUS_RESOLVED

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "research_job_id": self.research_job_id,
            "research_artifact_id": self.artifact_id,
            "resolution_reason": self.reason,
            "research_reference": self.reference,
            "focus_source": self.focus_source,
            "candidates": [c.as_dict() for c in self.candidates],
            "question": self.question,
        }


# ------------------------------------------------------------ Turkish rendering


def local_hhmm(value: datetime | None) -> str:
    if value is None:
        return ""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(OWNER_TZ).strftime("%H:%M")


def _local_date(value: datetime | None) -> date | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(OWNER_TZ).date()


def _locative(minute_text: str) -> str:
    """The Turkish locative suffix a spoken clock time takes: -daki/-deki/-taki/-teki.

    Chosen from how the MINUTES are read aloud, which is how the owner hears it: 19 is
    "on dokuz" (back vowel, voiced) -> "20:19'daki"; 53 is "elli üç" (front vowel,
    voiceless) -> "19:53'teki". Getting this wrong makes a clarification sound like a
    machine, and this question is the one the owner has to be able to answer.
    """
    letters = [c for c in minute_text.lower() if c.isalpha()]
    vowel = "a"
    for ch in reversed(letters):
        if ch in _BACK_VOWELS:
            vowel = "a"
            break
        if ch in _FRONT_VOWELS:
            vowel = "e"
            break
    consonant = "t" if letters and letters[-1] in _VOICELESS else "d"
    return f"{consonant}{vowel}ki"


def _day_word(entry: FocusEntry, today: date) -> str:
    day = _local_date(entry.completed_at)
    if day is None:
        return ""
    if day == today:
        return "bugün"
    if (today - day).days == 1:
        return "dün"
    return f"{day.day} {_MONTHS_TR[day.month - 1]}"


def spoken_candidate(entry: FocusEntry, *, today: date, with_day: bool) -> str:
    """"bugün 20:19'daki" / "19:53'teki" — a time the owner can answer with."""
    hhmm = local_hhmm(entry.completed_at)
    if not hhmm:
        return (entry.topic or "başlıksız araştırma")[:60]
    minute = int(hhmm.split(":")[1])
    said = f"{hhmm}'{_locative(cardinal(minute))}"
    day = _day_word(entry, today) if with_day else ""
    return f"{day} {said}".strip()


def _particle(spoken: str) -> str:
    return "mı" if spoken.endswith("daki") or spoken.endswith("taki") else "mi"


def ambiguity_question(candidates: tuple[FocusEntry, ...], *, now: datetime) -> str:
    """ONE short Turkish question naming the candidates by their LOCAL times.

    Times, not titles: the two runs in the owner's record share a title, so a question
    built from titles is the same question twice and cannot be answered.
    """
    today = _local_date(now) or datetime.now(UTC).date()
    if len(candidates) < 2:
        return MISSING_QUESTION_TR
    first_day = _local_date(candidates[0].completed_at)
    spoken = [
        spoken_candidate(candidates[0], today=today, with_day=True),
        *(
            spoken_candidate(
                c, today=today, with_day=_local_date(c.completed_at) != first_day
            )
            for c in candidates[1:]
        ),
    ]
    if len(spoken) == 2:
        return (
            f"Aynı konuda iki araştırmanız var: {spoken[0]} {_particle(spoken[0])}, "
            f"yoksa {spoken[1]} {_particle(spoken[1])}?"
        )
    listed = ", ".join(spoken[:-1]) + f" ve {spoken[-1]}"
    return f"Aynı konuda {cardinal(len(spoken))} araştırmanız var: {listed}. Hangisini anlatayım?"


# --------------------------------------------------------------- completed runs


def completed_entries(
    db: Session, *, now: datetime, limit: int = TOPIC_LOOKBACK
) -> tuple[FocusEntry, ...]:
    """The owner's completed researches, most recent first, as focus entries."""
    from app.explain.research_context import list_completed_research

    out: list[FocusEntry] = []
    for completed in list_completed_research(db, now=now, limit=limit):
        entry = focus_module.describe(db, completed.research_job_id)
        if entry is not None:
            out.append(entry)
    return tuple(out)


def _topic_matches(
    entries: tuple[FocusEntry, ...], content_words: tuple[str, ...]
) -> tuple[FocusEntry, ...]:
    """Entries whose topic contains EVERY content word of the phrase.

    A filter over candidates, never an identity: the answer is a set of runs, and when
    the set has more than one member the server asks rather than picking.
    """
    if not content_words:
        return ()
    out = []
    for entry in entries:
        folded = turkish_casefold(entry.topic or "")
        if all(word in folded for word in content_words):
            out.append(entry)
    return tuple(out)


# ------------------------------------------------------------------- selection


def _selection_index(
    reference: ResearchReference, candidates: tuple[FocusEntry, ...], *, now: datetime
) -> int | None:
    """Which offered candidate the owner just picked, or None.

    Candidates are offered most recent first, so "ilki/birincisi" and "en son/sonuncusu"
    both name the newer run — the same one, said two ways, and each unambiguous on its own
    terms. "ikincisi" and "bir önceki" both name the older one.
    """
    if not candidates:
        return None
    if reference.clock:
        for n, entry in enumerate(candidates):
            if local_hhmm(entry.completed_at) == reference.clock:
                return n
        return None
    if reference.kind == RESEARCH_REFERENCE_ORDINAL and reference.ordinal:
        index = reference.ordinal - 1
        return index if 0 <= index < len(candidates) else None
    if reference.kind == RESEARCH_REFERENCE_PREVIOUS:
        return 1 if len(candidates) >= 2 else None
    if reference.latest:
        newest = max(
            range(len(candidates)),
            key=lambda n: candidates[n].completed_at or datetime.min.replace(tzinfo=UTC),
        )
        return newest
    if reference.day:
        today = _local_date(now) or datetime.now(UTC).date()
        wanted = today if reference.day == "today" else date.fromordinal(today.toordinal() - 1)
        hits = [n for n, c in enumerate(candidates) if _local_date(c.completed_at) == wanted]
        return hits[0] if len(hits) == 1 else None
    return None


# -------------------------------------------------------------------- resolver


def resolve_reference(
    db: Session,
    *,
    utterance: str | None = None,
    tokens: tuple[str, ...] = (),
    explicit_job_id: str | uuid.UUID | None = None,
    now: datetime | None = None,
    reference: ResearchReference | None = None,
    consume: bool = True,
) -> ReferenceResolution:
    """Resolve "which research?" from durable state alone. Never starts anything.

    ``reference`` is the router's own reading of the turn
    (``app.voice.intents.classify_research_reference``); pass it when the caller has the
    turn's stored reference rather than its words — a tool call carries no utterance of
    its own, and the session record keeps the bounded reference, not the transcript.

    ``consume`` is False for the crawl guard, which must be able to see that a
    clarification is open without spending the owner's answer on a refusal.
    """
    now = now or datetime.now(UTC)
    if reference is None:
        if not tokens and utterance:
            _normalized, tokens, _dropped = normalize_transcript(utterance)
        reference = classify_research_reference(tokens, utterance=utterance)

    # 1. An explicit job id from the caller (a UI selection, a route). Identity given,
    #    nothing to infer.
    if explicit_job_id:
        entry = focus_module.describe(db, explicit_job_id)
        if entry is not None:
            if consume:
                focus_module.clear_pending_clarification(db)
            return ReferenceResolution(
                STATUS_RESOLVED,
                research_job_id=entry.research_job_id,
                artifact_id=entry.artifact_id,
                reason=REASON_EXPLICIT,
                reference=reference.kind,
                entry=entry,
            )

    # 2. An answer to the question the server is waiting on. This is the half ADR-0075
    #    was missing: the clarification existed, and nothing could hear the answer.
    pending = focus_module.peek_pending_clarification(db, now=now)
    if pending:
        candidates = focus_module.pending_candidates(pending)
        index = _selection_index(reference, candidates, now=now)
        if index is not None:
            chosen = candidates[index]
            if consume:
                focus_module.take_pending_clarification(db, now=now)
            entry = focus_module.describe(db, chosen.research_job_id) or chosen
            return ReferenceResolution(
                STATUS_RESOLVED,
                research_job_id=entry.research_job_id,
                artifact_id=entry.artifact_id,
                reason=REASON_OWNER_SELECTED_BY_VOICE,
                reference=RESEARCH_REFERENCE_SELECTION,
                entry=entry,
            )

    # 3. "bir önceki araştırma" - the most recent DISTINCT job before the current one.
    if reference.kind == RESEARCH_REFERENCE_PREVIOUS:
        previous = focus_module.previous_focus(db)
        if previous is not None:
            return _resolved(previous, REASON_PREVIOUS_FOCUS, reference.kind, db, consume)
        return _ask(
            db,
            reason=REASON_NO_PREVIOUS,
            question=NO_PREVIOUS_QUESTION_TR,
            reference=reference.kind,
            now=now,
            consume=consume,
        )

    # 4. "ikinci araştırma" - the N-th distinct focus, 1 being the current one.
    if reference.kind == RESEARCH_REFERENCE_ORDINAL and reference.ordinal:
        nth = focus_module.nth_focus(db, reference.ordinal)
        if nth is not None:
            return _resolved(nth, REASON_ORDINAL_FOCUS, reference.kind, db, consume)
        return _ask(
            db,
            reason=REASON_NO_NTH,
            question=MISSING_QUESTION_TR,
            reference=reference.kind,
            now=now,
            consume=consume,
        )

    current = focus_module.current_focus(db)

    # 5. "bu", "bunu", "onu", "az önceki" - or no pointer at all ("Teknik anlat."). Both
    #    mean the research being talked about, and that is exactly what the focus is.
    if reference.kind in (RESEARCH_REFERENCE_CURRENT, RESEARCH_REFERENCE_NONE):
        if current is not None:
            return _resolved(current, REASON_CURRENT_FOCUS, reference.kind, db, consume)
        return _without_focus(db, reference.kind, now=now, consume=consume)

    # 6. A topic phrase: "OpenAI araştırmasını anlat".
    entries = completed_entries(db, now=now)
    matches = _topic_matches(entries, reference.content_words)
    if len(matches) == 1:
        return _resolved(matches[0], REASON_TOPIC_UNIQUE, reference.kind, db, consume)
    if len(matches) > 1:
        # Same title, several runs. If one of them is the research already in focus, the
        # owner is talking about THAT one - a shared title is not a reason to re-ask
        # someone who just heard one of them announced.
        if current is not None and any(
            m.research_job_id == current.research_job_id for m in matches
        ):
            return _resolved(current, REASON_CURRENT_FOCUS, reference.kind, db, consume)
        return _ambiguous(db, matches[:2], REASON_AMBIGUOUS_TOPIC, reference.kind, now=now)
    if current is not None:
        return _resolved(current, REASON_CURRENT_FOCUS, reference.kind, db, consume)
    return _without_focus(db, reference.kind, now=now, consume=consume)


def _resolved(
    entry: FocusEntry, reason: str, reference: str, db: Session, consume: bool
) -> ReferenceResolution:
    if consume:
        # A reference that resolved answers whatever question was open: leaving the
        # clarification live would let a later "ikincisi" select against a stale list.
        focus_module.clear_pending_clarification(db)
    return ReferenceResolution(
        STATUS_RESOLVED,
        research_job_id=entry.research_job_id,
        artifact_id=entry.artifact_id,
        reason=reason,
        reference=reference,
        focus_source=entry.source_of_focus,
        entry=entry,
    )


def _without_focus(
    db: Session, reference: str, *, now: datetime, consume: bool
) -> ReferenceResolution:
    """No focus at all: the pre-ADR-0076 rows, or a genuinely empty history.

    One completed research and nothing pointing at it is not ambiguous - it is the only
    answer there is. Two close together with nothing pointing at either is the ADR-0075
    case, and it is still a question rather than a guess.
    """
    from app.explain.research_context import AMBIGUITY_WINDOW

    entries = completed_entries(db, now=now, limit=2)
    if not entries:
        return _ask(
            db,
            reason=REASON_NO_RESEARCH,
            question=MISSING_QUESTION_TR,
            reference=reference,
            now=now,
            consume=consume,
        )
    if len(entries) == 1:
        return _resolved(entries[0], REASON_ONLY_COMPLETED, reference, db, consume)
    newest, runner_up = entries[0], entries[1]
    if (
        newest.completed_at is not None
        and runner_up.completed_at is not None
        and (newest.completed_at - runner_up.completed_at) <= AMBIGUITY_WINDOW
    ):
        return _ambiguous(db, entries[:2], REASON_AMBIGUOUS_NO_FOCUS, reference, now=now)
    return _resolved(newest, REASON_LATEST_COMPLETED, reference, db, consume)


def _ambiguous(
    db: Session,
    candidates: tuple[FocusEntry, ...],
    reason: str,
    reference: str,
    *,
    now: datetime,
) -> ReferenceResolution:
    question = ambiguity_question(tuple(candidates), now=now)
    focus_module.set_pending_clarification(
        db, question=question, candidates=tuple(candidates), now=now
    )
    return ReferenceResolution(
        STATUS_AMBIGUOUS,
        reason=reason,
        candidates=tuple(candidates),
        question=question,
        reference=reference,
    )


def _ask(
    db: Session,
    *,
    reason: str,
    question: str,
    reference: str,
    now: datetime,
    consume: bool,
) -> ReferenceResolution:
    """A question, and the durable memory that it was asked.

    Recorded whatever ``consume`` says: ``consume`` governs whether an EXISTING answer is
    spent, never whether a NEW question is remembered. The owner's record is what happens
    when it is not remembered — the same clarification, six times, answerable by nothing.
    """
    focus_module.set_pending_clarification(db, question=question, candidates=(), now=now)
    return ReferenceResolution(
        STATUS_MISSING, reason=reason, question=question, reference=reference
    )


__all__ = [
    "MISSING_QUESTION_TR",
    "NO_PREVIOUS_QUESTION_TR",
    "OWNER_TZ",
    "REASON_AMBIGUOUS_NO_FOCUS",
    "REASON_AMBIGUOUS_TOPIC",
    "REASON_CURRENT_FOCUS",
    "REASON_EXPLICIT",
    "REASON_LATEST_COMPLETED",
    "REASON_NO_NTH",
    "REASON_NO_PREVIOUS",
    "REASON_NO_RESEARCH",
    "REASON_ONLY_COMPLETED",
    "REASON_ORDINAL_FOCUS",
    "REASON_OWNER_SELECTED_BY_VOICE",
    "REASON_PREVIOUS_FOCUS",
    "REASON_TOPIC_UNIQUE",
    "STATUS_AMBIGUOUS",
    "STATUS_MISSING",
    "STATUS_RESOLVED",
    "ReferenceResolution",
    "ambiguity_question",
    "completed_entries",
    "local_hhmm",
    "resolve_reference",
    "spoken_candidate",
]
