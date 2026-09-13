"""B17 req 39-45: what the system knows about its owner, in front of the model.

B16 gave the owner a way to teach this system something. This is the other half, and
without it the first half is a write-only diary: `hybrid_search` has been complete since
M5, B16 gave it its first product caller (`memory.search`, when the owner ASKS), and
nothing has ever put a memory in front of the model on the path where it answers.

**Explicit outranks inferred, and here it is a precedence rather than a weight.**
`hybrid_search` scores `W_EXPLICIT` alongside recency and confidence, so a fresh guess can
outscore something the owner stated months ago. Requirement 45 is not a nudge: what the
owner SAID wins over what this system worked out, every time, and the selection below sorts
that way before the budget is spent. `apply_filters` already refuses superseded rows
unconditionally, so a corrected memory cannot come back this way - that half was already
right and is not re-implemented here.

**An inferred row is never presented as a fact.** The block says which sentences the owner
stated and which this system worked out, with the confidence, and tells the model to ask
rather than assert when it is working from the second kind. A system that quietly promotes
its own guesses to "things you told me" is how it starts confidently telling its owner
things they never said - and the owner has no way to catch it, because it sounds exactly
like the memory working.

**The budget is measured, not guessed.** The persona is 10,110 characters before any of
this (measured 2026-09-13), and it is sent on every session create and every attach. A
memory block that grows without a ceiling is a token bill that grows with the owner's
history, so the ceiling is here, it is enforced by construction rather than by hope, and
`test_the_injected_block_stays_under_its_ceiling` holds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.memory.embedding import Embedder
from app.memory.models import Memory
from app.memory.policy import find_secret
from app.memory.retrieval import RetrievalFilters, hybrid_search
from app.memory.types import MemoryClass

logger = get_logger("app.memory.injection")

#: How many memories may reach one instruction. Twelve is about as much as is worth saying
#: before a persona block stops being context and starts being a list nobody reads.
MAX_INJECTED = 12

#: How many candidates retrieval is asked for per class, before ranking and the budget
#: narrow them. Deliberately larger than `MAX_INJECTED`: you cannot drop for budget what
#: you never retrieved, so a pool equal to the ceiling would make `dropped_for_budget`
#: permanently zero — and that count is the signal that the owner has taught this system
#: more than one instruction can carry. It is also what lets the precedence in `rank` mean
#: anything: with a pool the size of the answer, `hybrid_search`'s own ordering has already
#: decided, and re-sorting a list nothing was excluded from changes nothing.
CANDIDATE_POOL = MAX_INJECTED * 3

#: The character ceiling for the whole block. The persona around it measures 10,110
#: characters (2026-09-13), sent on every session create AND every attach, so this is a
#: deliberate ~14% ceiling on a cost the owner pays per session rather than per word they
#: ever taught. Enforced by construction below.
MAX_INSTRUCTION_CHARS = 1400

#: An inferred memory below this is a guess the system has not stood up yet - one
#: observation, never corroborated. `policy.decide` caps a single observation at 0.4, so
#: this is deliberately just above it: an inferred row reaches the owner's persona only
#: once evidence has accumulated, not the first time a summary mentioned something.
MIN_INFERRED_CONFIDENCE = 0.45

#: The classes worth putting in a persona. Episodic memories are what HAPPENED and belong
#: in the briefing and the ledger; a persona carries who the owner is and what they have
#: decided.
INJECTED_CLASSES: tuple[str, ...] = (
    MemoryClass.PREFERENCE.value,
    MemoryClass.VOICE_PREFERENCE.value,
    MemoryClass.PROJECT.value,
    MemoryClass.SEMANTIC.value,
    MemoryClass.PROCEDURAL.value,
)

_HEADING_TR = "Sahibi hakkında bildiklerin — bunları bir daha sorma:"
_INFERRED_NOTE_TR = (
    "Yukarıdakilerden «çıkarım» diye işaretlenenleri sahip SÖYLEMEDİ; sen konuşmalardan "
    "çıkardın. Onlara olgu gibi davranma: gerekiyorsa kullan, ama emin değilsen sor."
)


def _recency_key(memory: Memory) -> float:
    stamp = memory.last_confirmed_at or memory.updated_at or memory.created_at
    return stamp.timestamp() if stamp is not None else 0.0


def _line(memory: Memory) -> str:
    """One memory, and WHERE it came from - never one without the other."""
    text = " ".join(str(memory.text or "").split())
    if memory.explicit:
        return f"- {text} (sahibin söylediği)"
    percent = int(round(float(memory.confidence or 0.0) * 100))
    return f"- {text} (çıkarım, güven %{percent})"


@dataclass(slots=True)
class InjectionSelection:
    """What reached the instruction, and what did not."""

    memories: list[Memory] = field(default_factory=list)
    #: How many candidates retrieval offered before any of this narrowed them.
    considered: int = 0
    #: Inferred rows the system has not stood up yet.
    dropped_low_confidence: int = 0
    #: Rows whose TEXT looks like a credential. Should always be zero - the write policy
    #: refuses these at the door - and it is counted rather than assumed, because the
    #: consequence of being wrong is a credential inside an instruction sent to a
    #: third-party model provider.
    dropped_secret_shaped: int = 0
    #: Rows that lost to the ceiling. Non-zero here is the signal that the owner has taught
    #: this system more than one instruction can carry.
    dropped_for_budget: int = 0

    @property
    def explicit_count(self) -> int:
        return sum(1 for m in self.memories if m.explicit)

    def as_block(self) -> str:
        """The persona block, or "" when there is nothing to say.

        Empty rather than a heading with no rows under it: a section that announces
        knowledge and then lists none spends tokens saying nothing and reads, to a model,
        as an instruction it has failed to satisfy.
        """
        if not self.memories:
            return ""
        lines = [_HEADING_TR, *(_line(m) for m in self.memories)]
        if any(not m.explicit for m in self.memories):
            lines.append(_INFERRED_NOTE_TR)
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "injected": len(self.memories),
            "explicit": self.explicit_count,
            "considered": self.considered,
            "dropped_low_confidence": self.dropped_low_confidence,
            "dropped_secret_shaped": self.dropped_secret_shaped,
            "dropped_for_budget": self.dropped_for_budget,
            "chars": len(self.as_block()),
        }


def rank(memories: list[Memory]) -> list[Memory]:
    """req 45: what the owner SAID, then what is best evidenced, then what is most recent.

    A precedence and not a weighted score. `hybrid_search`'s ranking mixes explicit into a
    sum with recency, so a guess made this morning can outrank something the owner stated
    in March; that is the right shape for "find me what is relevant" and the wrong one for
    "what does this system believe about its owner".
    """
    return sorted(
        memories,
        key=lambda m: (
            0 if m.explicit else 1,
            -float(m.confidence or 0.0),
            -_recency_key(m),
            str(m.id),
        ),
    )


def select_for_instruction(
    session: Session,
    embedder: Embedder,
    *,
    query: str | None = None,
    now: datetime | None = None,
    limit: int = MAX_INJECTED,
    max_chars: int = MAX_INSTRUCTION_CHARS,
) -> InjectionSelection:
    """req 39/41: the top-k this session should carry, inside the budget.

    ``query`` narrows the retrieval when there is something to narrow by (a session that
    already has a topic); with none, `hybrid_search` degrades to structured retrieval
    ranked by recency, confidence and explicitness, which is exactly right for "what should
    you know about your owner before they say anything".
    """
    selection = InjectionSelection()
    candidates: dict[Any, Memory] = {}
    for memory_class in INJECTED_CLASSES:
        for hit in hybrid_search(
            session,
            embedder,
            query,
            RetrievalFilters(memory_class=memory_class),
            k=CANDIDATE_POOL,
            now=now,
        ):
            candidates[hit.memory.id] = hit.memory
    selection.considered = len(candidates)

    standing: list[Memory] = []
    for memory in candidates.values():
        # req 56, and this batch is what makes it matter. The write policy's secrets guard
        # refuses a credential at the door, so nothing here should ever fire - but "should
        # never" is not a place to stop when the consequence is a credential travelling to
        # a third-party model provider inside a session instruction. A row can predate a
        # pattern the guard learned later, or arrive through a path the guard does not
        # cover; this is the LAST gate before the text leaves this machine, and a last gate
        # that trusts the first one is not a gate. Counted, logged without content.
        if find_secret(str(memory.text or "")) is not None:
            selection.dropped_secret_shaped += 1
            logger.warning("memory_injection_refused_secret", memory_id=str(memory.id))
            continue
        if not memory.explicit and float(memory.confidence or 0.0) < MIN_INFERRED_CONFIDENCE:
            selection.dropped_low_confidence += 1
            continue
        standing.append(memory)

    ordered = rank(standing)[:limit]
    selection.dropped_for_budget = len(standing) - len(ordered)

    # Spend the budget in rank order, and STOP at the first one that does not fit rather
    # than skipping ahead to something shorter: a block that silently preferred terse
    # memories over important ones would be a ranking nobody wrote down, and it would put
    # "sabah toplantı sevmem" in front of the model while dropping the long sentence that
    # explains why.
    used = len(_HEADING_TR) + len(_INFERRED_NOTE_TR)
    for index, memory in enumerate(ordered):
        line = len(_line(memory)) + 1
        if used + line > max_chars:
            selection.dropped_for_budget += len(ordered) - index
            break
        used += line
        selection.memories.append(memory)

    return selection


__all__ = [
    "INJECTED_CLASSES",
    "MAX_INJECTED",
    "MAX_INSTRUCTION_CHARS",
    "MIN_INFERRED_CONFIDENCE",
    "InjectionSelection",
    "rank",
    "select_for_instruction",
]
