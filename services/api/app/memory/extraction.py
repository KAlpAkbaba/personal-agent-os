"""B16 req 33/34: preferences and project context, caught from what was actually said.

`app.memory.policy` has carried trigger patterns since M5 — "hatırla", "bundan sonra",
"tercih ederim", "her zaman", "karar" — and the matrix's note on requirement 33 was exact:
*kod var, girdi yok*. Nothing ever fed them a sentence a person had spoken. The write
policy, the ladder, the evidence chain and the promotion rule were all complete and the
only text that ever reached them arrived through `POST /v1/memory/observe`.

**Why the session SUMMARY and not the transcript.** A raw transcript must never travel as
a tool argument — `app.voice.realtime_sessions.service.FORBIDDEN_KEY_PARTS` refuses any
key spelled like one, which is a privacy rule and not an obstacle to route around. It also
does not have to: the realtime session already persists `transcript_summary`, written from
the client's own `summary` sideband event and fed back into the persona on every attach.
It is server-side, it is already durable, and reading it crosses no boundary. So extraction
runs where the text already lives instead of moving text to where the extractor is.

**Nothing here decides anything.** Every sentence goes through `policy.decide()`, the same
frozen decision table `/v1/memory/observe` uses, with `explicit=False` — so a summary that
happens to contain "always use ..." produces a CANDIDATE capped at
`SINGLE_OBSERVATION_MAX_CONFIDENCE`, never an owner memory. That is M5 review #4's rule and
it is precisely why this module is allowed to exist: the untrusted-ingestion threat the
policy was hardened against is exactly this shape, and the hardening already holds.

**A summary re-sent is not a second observation.** `transcript_summary` is REPLACED as the
conversation grows, and each new one tends to contain what the last one said. Extracting
blindly would let one thing the owner mentioned once corroborate itself up the ladder to
durable, which is the promotion rule being fed its own output. The caller passes what this
session has already extracted and gets back what to add.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.memory import service as memory_service
from app.memory.embedding import Embedder
from app.memory.policy import ACTION_IGNORE, ACTION_REFUSE, Observation, decide
from app.memory.types import MemoryClass, WriteStage

logger = get_logger("app.memory.extraction")

#: Sentence boundary. A local two-line regex rather than an import from `app.briefing`:
#: these two packages share nothing else and a dependency between them would be a worse
#: cost than the duplication.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

#: A sentence longer than this is a paragraph the summariser did not punctuate; a memory is
#: a fact, and one this long is not a fact.
MAX_SENTENCE_CHARS = 300

#: How many observations one summary may contribute. A summary that produces twelve
#: preferences is a summariser writing an essay, not an owner stating twelve preferences,
#: and the cheapest place to notice that is a ceiling.
MAX_PER_SUMMARY = 5

#: What makes a sentence PROJECT rather than PREFERENCE (req 34). Kept separate from
#: `policy.STRONG_SIGNAL_PATTERNS`, which decides WHETHER something is worth writing; this
#: decides WHICH SHELF it goes on, and the policy is frozen.
#:
#: Every stem takes a trailing ``\w*`` and none of them takes a closing ``\b``. Turkish is
#: agglutinative and the case endings are where the sentence's meaning lives: "bu repoDA",
#: "projeNIN", "sürümLERI". A pattern anchored at both ends matches the word only in the
#: one form nobody says out loud - ``\brepo\b`` misses "repoda", which is how the first
#: draft of this filed "Bu repoda testleri önce yazıyoruz" as a PREFERENCE.
_PROJECT_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bproje\w*"),
    re.compile(r"(?i)\bdepo\w*"),
    re.compile(r"(?i)\brepo\w*"),
    re.compile(r"(?i)\bkod taban\w*"),
    re.compile(r"(?i)\bs[üu]r[üu]m\w*"),
    re.compile(r"(?i)\bmimari\w*"),
    re.compile(r"(?i)\bcodebase\w*"),
    re.compile(r"(?i)\bmilestone\w*"),
)

#: Stages worth a durable row. SESSION is deliberately excluded: a session-retention row
#: per summary sentence would fill the table with things nobody will ever ask about, and
#: the sweeper would then spend its time deleting them.
_KEPT_STAGES = (WriteStage.CANDIDATE, WriteStage.DURABLE)


def sentence_key(sentence: str) -> str:
    """A stable id for "this session already extracted this sentence".

    A hash and not the sentence: this travels into the realtime session's `context_json`,
    which is continuity state a reattaching client receives, and the summary's own words
    have no business being copied there.
    """
    return hashlib.sha256(" ".join(sentence.split()).casefold().encode("utf-8")).hexdigest()[:16]


def classify(sentence: str) -> MemoryClass:
    """PROJECT when the sentence is about the work, PREFERENCE otherwise (req 33/34)."""
    if any(p.search(sentence) for p in _PROJECT_SIGNAL_PATTERNS):
        return MemoryClass.PROJECT
    return MemoryClass.PREFERENCE


@dataclass(slots=True)
class ExtractionResult:
    """What one summary contributed, and what the caller must remember it contributed."""

    #: Memory ids written or corroborated, in order.
    written: list[str] = field(default_factory=list)
    #: Sentence keys the caller should add to what this session has already extracted -
    #: including REFUSED ones, so a secret in a summary is not re-offered on every event.
    seen: list[str] = field(default_factory=list)
    #: How many sentences the policy refused outright (a credential in the summary).
    refused: int = 0
    #: How many were skipped as chatty/weak - the normal case, and worth counting so
    #: "extraction is running and finding nothing" is distinguishable from "not running".
    skipped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "written": len(self.written),
            "refused": self.refused,
            "skipped": self.skipped,
        }


def extract_from_summary(
    session: Session,
    embedder: Embedder,
    summary: str,
    *,
    already: set[str] | None = None,
    source: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ExtractionResult:
    """Turn one conversation summary into observations, through the write policy.

    Never raises: this runs while recording a client event, and an owner whose voice
    session started failing because a preference could not be filed would rightly regard
    that as the memory feature making things worse.
    """
    del now  # the memory service stamps its own timestamps
    result = ExtractionResult()
    seen = set(already or ())

    for raw in _SENTENCE_END.split((summary or "").strip()):
        sentence = raw.strip()
        if not sentence or len(sentence) > MAX_SENTENCE_CHARS:
            continue
        key = sentence_key(sentence)
        if key in seen:
            continue
        seen.add(key)

        observation = Observation(
            text=sentence,
            memory_class=classify(sentence),
            explicit=False,  # never: see the module docstring, and M5 review #4
            source=dict(source or {}),
        )
        decision = decide(observation)
        if decision.action == ACTION_REFUSE:
            # The secrets guard fired on the SUMMARY. Counted and remembered so the same
            # sentence is not re-offered on every later summary; never logged with content.
            result.refused += 1
            result.seen.append(key)
            continue
        if decision.action == ACTION_IGNORE or decision.action not in _KEPT_STAGES:
            result.skipped += 1
            result.seen.append(key)
            continue
        if len(result.written) >= MAX_PER_SUMMARY:
            # Not marked seen: the ceiling is about this event, not about the sentence, and
            # a sentence dropped for room should still be extractable from the next summary.
            logger.info("memory_extraction_capped", cap=MAX_PER_SUMMARY)
            break

        try:
            outcome = memory_service.record_observation(session, embedder, observation)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning(
                "memory_extraction_failed", error=f"{type(exc).__name__}: {exc}"
            )
            try:
                session.rollback()
            except Exception:  # noqa: BLE001 - nothing further to do about it
                pass
            continue
        result.seen.append(key)
        # `ObserveResult` carries an id and an ACTION - "created", "corroborated",
        # "ignored" - not a row. `None` means the policy declined after all (the dedup
        # path can), and a sentence that produced nothing is not something written.
        if outcome.memory_id is not None:
            result.written.append(str(outcome.memory_id))

    return result


__all__ = [
    "MAX_PER_SUMMARY",
    "MAX_SENTENCE_CHARS",
    "ExtractionResult",
    "classify",
    "extract_from_summary",
    "sentence_key",
]
