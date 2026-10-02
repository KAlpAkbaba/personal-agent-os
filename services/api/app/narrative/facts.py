"""The facts a narrative may be built from (ROADMAP order 2c: "records everything and tells him").

Plain frozen data, tuples only, so the same ledger rows give an EQUAL value and a test (or the
auditor) can compare two collections with ``==``. No database and no model in here.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from app.ledger.vocabulary import (
    SUBSYSTEM_BROWSER,
    SUBSYSTEM_CALENDAR,
    SUBSYSTEM_CLOUD_CORE,
    SUBSYSTEM_DEPLOYMENT,
    SUBSYSTEM_DOCUMENTS,
    SUBSYSTEM_MAIL,
    SUBSYSTEM_MEMORY,
    SUBSYSTEM_RESEARCH,
    SUBSYSTEM_ROUTINE,
    SUBSYSTEM_VOICE,
)

#: the broker's own refusal class when no enrolled device can run a command (app/broker).
NO_CAPABLE_DEVICE = "no_capable_device"

#: a ledger row that names no device ran in the cloud core.
DEVICE_CLOUD = "bulut"

_SUBSYSTEM_TR = {
    SUBSYSTEM_RESEARCH: "araştırma",
    SUBSYSTEM_BROWSER: "tarayıcı",
    SUBSYSTEM_MAIL: "posta",
    SUBSYSTEM_CALENDAR: "takvim",
    SUBSYSTEM_VOICE: "ses",
    SUBSYSTEM_MEMORY: "hafıza",
    SUBSYSTEM_ROUTINE: "rutin",
    SUBSYSTEM_DOCUMENTS: "belge",
    SUBSYSTEM_DEPLOYMENT: "yayın",
    SUBSYSTEM_CLOUD_CORE: "bulut çekirdeği",
}


def subsystem_label(subsystem: str) -> str:
    """The Turkish word the narrator says and the auditor looks for; an unlisted subsystem is
    spoken as its own code (underscores as spaces) rather than dropped."""
    return _SUBSYSTEM_TR.get(subsystem, subsystem.replace("_", " "))


@dataclasses.dataclass(frozen=True, slots=True)
class Period:
    """``start`` inclusive, ``end`` exclusive, both UTC."""

    start: datetime
    end: datetime
    label: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class FactEvent:
    event_id: str
    occurred_at: datetime
    subsystem: str
    event_type: str
    summary: str
    #: the reason the ledger holds (its ``result``); ``None`` when it holds none.
    reason: str | None
    device: str


@dataclasses.dataclass(frozen=True, slots=True)
class NarrativeFacts:
    #: the period actually covered (the requested end clipped to "now").
    covered: Period
    #: the canonical device word the rows were narrowed to, or ``None`` for all.
    device: str | None
    failed: tuple[FactEvent, ...]
    completed: tuple[tuple[str, tuple[FactEvent, ...]], ...]
    counts_by_subsystem: tuple[tuple[str, int], ...]
    counts_by_device: tuple[tuple[str, int], ...]
    no_capable_device: tuple[FactEvent, ...]
    total: int

    @property
    def is_empty(self) -> bool:
        return self.total == 0


def _counts(keys: list[str]) -> tuple[tuple[str, int], ...]:
    return tuple((key, keys.count(key)) for key in sorted(set(keys)))


def only_failures(facts: NarrativeFacts) -> NarrativeFacts:
    """The same period and device, told as if the failed rows were all there was ("ne
    başarısız oldu"). Nothing completed is left in the facts, so a narrator cannot list it
    and the auditor holds a text to the failures' own numbers: a count of the completed work
    is a foreign number there."""
    return NarrativeFacts(
        covered=facts.covered,
        device=facts.device,
        failed=facts.failed,
        completed=(),
        counts_by_subsystem=_counts([e.subsystem for e in facts.failed]),
        counts_by_device=_counts([e.device for e in facts.failed]),
        no_capable_device=facts.no_capable_device,
        total=len(facts.failed),
    )
