"""The watch's pure half: the hash, the tr-TR number, the conditions and the edge.

Nothing here touches a database, a device or a model. ``judge`` is the one decision a reading
makes once its page has changed (or is the first): its outcome, whether the owner hears of
it, and whether the baseline moves. It is edge-triggered - a condition notifies when it
turns true, never while it stays true - and the first reading is the baseline.

The number parser is our own (the integrator's verdict: no price-parser/Babel). Lessons from
changedetection.io that became rules: no character is stripped before parsing ('39,99' must
never become 3999), NBSP is a space, and an ambiguous '19.99' is None - never a guess.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from app.macros.naming import fold

OUTCOME_SAME: Final = "same"
OUTCOME_CHANGED: Final = "changed"
OUTCOME_CONDITION_MET: Final = "condition_met"
OUTCOME_UNREADABLE: Final = "unreadable"
OUTCOMES: Final = (OUTCOME_SAME, OUTCOME_CHANGED, OUTCOME_CONDITION_MET, OUTCOME_UNREADABLE)

KIND_CHANGED: Final = "changed"
KIND_CONTAINS: Final = "contains"
KIND_BELOW: Final = "number_below"
KIND_ABOVE: Final = "number_above"
NUMERIC_KINDS: Final = frozenset({KIND_BELOW, KIND_ABOVE})

REASON_NO_VALUE: Final = "değer bulunamadı"

#: The third failure in a row is told; the fourth and later are not.
FAILURES_BEFORE_NOTICE: Final = 3

# ------------------------------------------------------------------ the hash


def normalize_text(text: str) -> str:
    """Whitespace (NBSP and every other Unicode space included) collapsed to one space."""
    return " ".join((text or "").split())


def text_sha256(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ the tr-TR number

_TOKEN = re.compile(r"\d[\d.,]*\d|\d")
_PLAIN = re.compile(r"\d+")
_THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+")
_DECIMAL = re.compile(r"\d+,\d+")
_BOTH = re.compile(r"\d{1,3}(?:\.\d{3})+,\d+")


def _token_value(token: str) -> float | None:
    if _PLAIN.fullmatch(token):
        return float(token)
    if _BOTH.fullmatch(token):
        whole, fraction = token.split(",")
        return float(f"{whole.replace('.', '')}.{fraction}")
    if _DECIMAL.fullmatch(token):
        return float(token.replace(",", "."))
    if _THOUSANDS.fullmatch(token):
        return float(token.replace(".", ""))
    return None  # '19.99', '1,299.00', '1,2,3': ambiguous or not Turkish - never guessed


def parse_tr_number(text: str) -> float | None:
    """The one number a Turkish text states, or None (none, several, or ambiguous)."""
    values = set()
    for token in _TOKEN.findall(normalize_text(text)):
        value = _token_value(token)
        if value is None:
            return None
        values.add(value)
    return values.pop() if len(values) == 1 else None


# ------------------------------------------------------------------ conditions


@dataclass(frozen=True, slots=True)
class Condition:
    kind: str
    number: float | None = None
    text: str | None = None

    @property
    def numeric(self) -> bool:
        return self.kind in NUMERIC_KINDS


def _condition_number(raw: str) -> float | None:
    value = parse_tr_number(raw)
    if value is None and re.fullmatch(r"\d+\.\d+", raw):
        value = float(raw)  # the API's own decimal ('19.99') - the owner typed it, not a page
    if value is None or not _TOKEN.fullmatch(raw):
        return None
    return value


def parse_condition(raw: str) -> Condition:
    """``changed``, ``contains:<text>``, ``number_below:<n>`` or ``number_above:<n>``."""
    value = (raw or "").strip()
    if value == KIND_CHANGED:
        return Condition(KIND_CHANGED)
    kind, sep, rest = value.partition(":")
    rest = rest.strip()
    if sep and kind == KIND_CONTAINS and rest:
        return Condition(KIND_CONTAINS, text=rest)
    if sep and kind in NUMERIC_KINDS and rest:
        number = _condition_number(rest)
        if number is not None:
            return Condition(kind, number=number)
    raise ValueError(f"unknown watch condition {value[:40]!r}")


def condition_holds(condition: Condition, *, text: str, value: float | None) -> bool | None:
    """True / False, or None when it cannot be said (a numeric condition without a value)."""
    if condition.kind == KIND_CONTAINS:
        return fold(condition.text or "") in fold(normalize_text(text))
    if condition.numeric:
        if value is None or condition.number is None:
            return None
        if condition.kind == KIND_BELOW:
            return value < condition.number
        return value > condition.number
    return None


# ------------------------------------------------------------------ the judge


@dataclass(frozen=True, slots=True)
class Prior:
    """What the watch knew before this reading."""

    baseline_sha256: str | None
    condition_met: bool | None
    consecutive_failures: int


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: str
    notify: bool
    baseline: bool
    move_baseline: bool
    condition_met: bool | None = None
    reason_tr: str | None = None


def judge(
    condition: Condition, prior: Prior, *, sha: str, text: str, value: float | None
) -> Verdict:
    baseline = prior.baseline_sha256 is None
    unchanged = not baseline and sha == prior.baseline_sha256
    if condition.kind == KIND_CHANGED:
        if baseline or unchanged:
            return Verdict(OUTCOME_SAME, False, baseline, baseline)
        return Verdict(OUTCOME_CHANGED, True, False, True)
    holds = condition_holds(condition, text=text, value=value)
    if holds is None:
        return Verdict(
            OUTCOME_UNREADABLE, False, baseline, False, prior.condition_met, REASON_NO_VALUE
        )
    if holds and not prior.condition_met:
        return Verdict(OUTCOME_CONDITION_MET, True, baseline, True, True)
    outcome = OUTCOME_SAME if baseline or unchanged else OUTCOME_CHANGED
    return Verdict(outcome, False, baseline, True, holds)


def failure_notifies(prior: Prior) -> bool:
    """A failing FIRST reading is told at once; after a baseline, the third in a row."""
    if prior.baseline_sha256 is None:
        return prior.consecutive_failures == 0
    return prior.consecutive_failures == FAILURES_BEFORE_NOTICE - 1


# ------------------------------------------------------------------ spreading the checks


def due_offset_seconds(watch_id: uuid.UUID, every_hours: int) -> int:
    """A fixed offset within a tenth of the period, from the id: twenty watches made at
    once do not all read at the same second (changedetection #3178)."""
    span = max(1, int(every_hours)) * 360
    return int.from_bytes(hashlib.sha256(watch_id.bytes).digest()[:8], "big") % span


def next_due(now: datetime, watch_id: uuid.UUID, every_hours: int) -> datetime:
    """Counted from now - never from a missed slot, so a Core that was down ten hours reads
    once on return, not ten times."""
    return now + timedelta(hours=every_hours, seconds=due_offset_seconds(watch_id, every_hours))
