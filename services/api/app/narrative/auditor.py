"""The auditor: a narrative is REJECTED when it leaves a failure out, leaves a completed
subsystem out, or states a number that is not one of the facts. It never lets a pleasant
summary hide a failure; ``repair`` puts the missing failures back, deterministically."""

from __future__ import annotations

import dataclasses
import re

from app.narrative.collector import fold
from app.narrative.facts import FactEvent, NarrativeFacts, subsystem_label
from app.narrative.narrator import failure_sentence

_NUMBER = re.compile(r"\d+")
_WORD = re.compile(r"\w+")
_MIN_WORD = 3  # shorter words ("ve", "de") say nothing about WHICH failure a sentence names


@dataclasses.dataclass(frozen=True, slots=True)
class Verdict:
    missing_failures: tuple[str, ...] = ()
    missing_subsystems: tuple[str, ...] = ()
    foreign_numbers: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.missing_failures or self.missing_subsystems or self.foreign_numbers)


def _mentions(event: FactEvent, folded_text: str) -> bool:
    words = [w for w in _WORD.findall(fold(event.summary)) if len(w) >= _MIN_WORD]
    words = words or _WORD.findall(fold(event.summary))
    return bool(words) and all(w in folded_text for w in words)


def _allowed_numbers(facts: NarrativeFacts) -> set[str]:
    numbers = {str(facts.total), str(len(facts.failed)), str(len(facts.no_capable_device))}
    numbers.update(str(n) for _, n in facts.counts_by_subsystem)
    numbers.update(str(n) for _, n in facts.counts_by_device)
    numbers.update(str(len(evs)) for _, evs in facts.completed)
    for event in (*facts.failed, *facts.no_capable_device):
        numbers.update(_NUMBER.findall(event.summary))
        numbers.update(_NUMBER.findall(event.reason or ""))
    return numbers


def audit(facts: NarrativeFacts, text: str) -> Verdict:
    folded = fold(text)
    missing_failures = tuple(e.summary for e in facts.failed if not _mentions(e, folded))
    missing_subsystems = tuple(
        sub for sub, _ in facts.completed if fold(subsystem_label(sub)) not in folded
    )
    allowed = _allowed_numbers(facts)
    foreign = tuple(dict.fromkeys(n for n in _NUMBER.findall(text) if n not in allowed))
    return Verdict(missing_failures, missing_subsystems, foreign)


def repair(facts: NarrativeFacts, text: str) -> str:
    """Append a sentence for every failure the text does not name; nothing else is touched."""
    missing = set(audit(facts, text).missing_failures)
    extra = [
        "Ayrıca başarısız: " + failure_sentence(e) for e in facts.failed if e.summary in missing
    ]
    return text if not extra else " ".join([text.rstrip(), *extra])
