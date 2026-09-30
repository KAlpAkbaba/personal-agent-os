"""Recognise "what happened?" - the owner asking the record to be told (ROADMAP order 2c).

Pure text in, :class:`NarrativeAsk` or ``None`` out. Nothing is guessed: a period the collector
cannot resolve ('geçen hafta', 'hafta sonu') or a bare "ne oldu" is not an ask, so the sentence
falls through to whatever the router does with it. Device words come from
``app.devices.aliases`` - the same closed locatives every other device phrase uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.devices.aliases import extract_alias
from app.narrative.collector import fold

# The system's doing, second person ("yaptın") or impersonal ("oldu"). "yaptım" is the owner's
# own doing and "yapalım" a plan: neither is a question about the record.
_QUESTION = re.compile(r"\bne(?:ler)?\s+(?:yapt[ıi]n(?:[ıi]z)?|oldu|yap[ıi]ld[ıi])\b")
_FAILURE = re.compile(r"\bne(?:ler)?\s+ba[şs]ar[ıi]s[ıi]z\s+(?:oldu|kald[ıi])\b")
_PERIODS = (
    ("bu hafta", re.compile(r"\bbu\s+hafta\b(?!\s*sonu)")),
    ("dün", re.compile(r"\bd[üu]n\b")),
    ("bugün", re.compile(r"\bbug[üu]n\b")),
)
# A period word this module does not resolve: the sentence is about another window, so it is
# no ask - answering it with today's rows would be a wrong answer said confidently.
_OTHER_WINDOW = re.compile(r"\b(?:ge[çc]en|gelecek|[öo]nceki|hafta\s*sonu|ay|y[ıi]l)\b")
# "işte" also means 'well then'; only where it starts the sentence is it never the machine.
_LEADING_ISTE = re.compile(r"^\s*i[şs]te\b[\s,]*")

DEFAULT_PERIOD = "bugün"
DEFAULT_FAILURE_PERIOD = "bu hafta"


@dataclass(frozen=True, slots=True)
class NarrativeAsk:
    #: a period word ``collector.resolve_period`` accepts.
    period: str
    #: a canonical device word (``ofis``, ``ev``...) or ``None`` for every device.
    device: str | None
    #: "ne başarısız oldu": the owner wants the failures; the narrative still lists all facts.
    failures_only: bool = False


def recognise(utterance: str) -> NarrativeAsk | None:
    text = fold(utterance or "")
    if not text:
        return None
    text = _LEADING_ISTE.sub("", text)
    failures = bool(_FAILURE.search(text))
    if not failures and not _QUESTION.search(text):
        return None
    period = next((label for label, rx in _PERIODS if rx.search(text)), None)
    if _OTHER_WINDOW.search(text):
        return None
    device = extract_alias(text)
    if period is None and device is None and not failures:
        return None
    if period is None:
        period = DEFAULT_FAILURE_PERIOD if failures else DEFAULT_PERIOD
    return NarrativeAsk(period, device, failures)
