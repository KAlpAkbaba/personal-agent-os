"""A spend in a conversation's lines: BOOK it, ASK about it later, or nothing (money-ledger).

The decision table, pure (no database, no clock, no network):

* a price asked ("ne kadar", "kaç para", "kaça", "fiyatı ne"), ONE amount said ("yedi yüz
  elli lira"; a bare number counts when someone else answers the price question) and the
  OWNER's clear acceptance ("tamam alayım", "olur veriyorum", "alıyorum", "anlaştık") ->
  ``BOOK`` (tentative), anchored on the acceptance line;
* the same with two different amounts (a haggle) -> ``ASK`` (``two_prices``), with the last;
* an amount after a price question and no acceptance of HIS by the time the conversation
  ends -> ``ASK`` (``no_acceptance``) - including "tamam alayım" said by someone else;
* an acceptance when nobody in the conversation is known to be the owner -> ``ASK``
  (``unknown_speaker``): it may not have been his;
* a price asked and accepted with no amount anyone said -> ``ASK`` without an amount;
* anything else - a salary mentioned, "ne kadar sürer?", "tamam alayım" with no price - is
  nothing. An open episode is never asked about while the conversation goes on.

"Nakit" said by him within the episode makes it cash (booked apart, never matched to a bank
mail). An acceptance is not one when it is a question ("alayım mı?") or hedged ("sonra
alırım", "bir düşüneyim").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from app.money import categories
from app.money.amounts import money_in

BOOK: Final = "book"
ASK: Final = "ask"

REASON_NO_ACCEPTANCE: Final = "no_acceptance"
REASON_TWO_PRICES: Final = "two_prices"
REASON_UNKNOWN_SPEAKER: Final = "unknown_speaker"
REASON_UNCLEAR_AMOUNT: Final = "unclear_amount"

METHOD_CARD: Final = "card"
METHOD_CASH: Final = "cash"


@dataclass(frozen=True)
class Line:
    seq: int
    is_owner: bool
    said: str
    at: datetime


@dataclass(frozen=True)
class Decision:
    kind: str
    anchor_seq: int
    amount_kurus: int | None
    method: str
    occurred_at: datetime
    reason: str | None = None
    category: str | None = None


_ACCEPT_VERBS: Final = frozenset(
    {"alayım", "alıyorum", "alırım", "aldım", "veriyorum", "vereyim", "anlaştık"}
)
_HEDGES: Final = frozenset(
    {"belki", "sonra", "düşüneyim", "düşünürüm", "düşüneceğim", "bakarım", "bakayım", "yarın"}
)
_QUESTION: Final = frozenset({"mı", "mi", "mu", "mü"})
#: "ne kadar" asks a price unless it asks a time or a distance.
_NOT_PRICE_AFTER_NE_KADAR: Final = frozenset(
    {"sürer", "sürüyor", "sürecek", "zaman", "vakit", "uzak", "uzakta", "yakın", "büyük", "kaldı"}
)


def _words(said: str) -> list[str]:
    lowered = said.replace("İ", "i").replace("I", "ı").lower()
    return re.findall(r"[0-9a-zçğıöşüâîû.,₺]+", lowered)


def _plain(words: list[str]) -> list[str]:
    return [w.strip(".,") for w in words if w.strip(".,")]


def is_price_question(said: str) -> bool:
    words = _plain(_words(said))
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if word == "ne" and nxt == "kadar":
            after = words[i + 2] if i + 2 < len(words) else ""
            if after not in _NOT_PRICE_AFTER_NE_KADAR:
                return True
        if word == "kaç" and nxt in ("para", "lira", "tl", "paraya", "liraya"):
            return True
        if word in ("kaça", "kaçtan"):
            return True
        if word in ("fiyatı", "fiyat", "fiyatını") and nxt in ("ne", "nedir", "kaç", "kaçtı"):
            return True
    return False


def is_acceptance(said: str) -> bool:
    words = _plain(_words(said))
    if "?" in said or any(w in _QUESTION for w in words) or any(w in _HEDGES for w in words):
        return False
    return any(w in _ACCEPT_VERBS for w in words)


def says_cash(said: str) -> bool:
    return any(w.startswith("nakit") for w in _plain(_words(said)))


@dataclass
class _Episode:
    price_asked: bool = False
    amounts: list[int] = field(default_factory=list)
    last_seq: int = 0
    last_at: datetime | None = None
    cash: bool = False
    said: list[str] = field(default_factory=list)

    def distinct(self) -> list[int]:
        out: list[int] = []
        for value in self.amounts:
            if value not in out:
                out.append(value)
        return out


def _decision(
    episode: _Episode, kind: str, *, seq: int, at: datetime, reason: str | None
) -> Decision:
    distinct = episode.distinct()
    return Decision(
        kind=kind,
        anchor_seq=seq,
        amount_kurus=distinct[-1] if distinct else None,
        method=METHOD_CASH if episode.cash else METHOD_CARD,
        occurred_at=at,
        reason=reason,
        category=categories.categorize(" ".join(episode.said)),
    )


def decide(lines: list[Line], *, ended: bool, owner_known: bool = True) -> list[Decision]:
    """Every purchase the lines show, in order. ``owner_known``: at least one line in the
    conversation is known to be the owner's (otherwise no acceptance can be his for sure)."""
    out: list[Decision] = []
    episode: _Episode | None = None
    for line in sorted(lines, key=lambda item: item.seq):
        price = is_price_question(line.said)
        answering = episode is not None and episode.price_asked and not line.is_owner
        found = money_in(line.said, bare=answering and not price)
        if price and episode is not None and episode.amounts:
            # A new price question while the last one waits: the last one is left to ASK
            # (dropped below while the conversation goes on, kept once it ended).
            out.append(
                _decision(
                    episode,
                    ASK,
                    seq=episode.last_seq,
                    at=episode.last_at or line.at,
                    reason=REASON_NO_ACCEPTANCE,
                )
            )
            episode = None
        if price or (found and episode is not None):
            episode = episode or _Episode()
            episode.price_asked = episode.price_asked or price
        if episode is None:
            continue
        episode.said.append(line.said)
        if found:
            episode.amounts.extend(found)
            episode.last_seq, episode.last_at = line.seq, line.at
        if says_cash(line.said) and (line.is_owner or not owner_known):
            episode.cash = True
        if not is_acceptance(line.said) or not episode.price_asked:
            continue
        if owner_known and not line.is_owner:
            continue  # someone else's "tamam alayım" is not his
        if not owner_known:
            reason: str | None = REASON_UNKNOWN_SPEAKER
        elif not episode.amounts:
            reason = REASON_UNCLEAR_AMOUNT
        elif len(episode.distinct()) > 1:
            reason = REASON_TWO_PRICES
        else:
            reason = None
        kind = BOOK if reason is None else ASK
        out.append(_decision(episode, kind, seq=line.seq, at=line.at, reason=reason))
        episode = None
    if ended and episode is not None and episode.price_asked and episode.amounts:
        out.append(
            _decision(
                episode,
                ASK,
                seq=episode.last_seq,
                at=episode.last_at or lines[-1].at,
                reason=REASON_NO_ACCEPTANCE,
            )
        )
    # An ASK for a reason other than "no acceptance" is final already; one for "no
    # acceptance" waits for the end (a later "tamam alayım" would have closed it).
    if not ended:
        out = [d for d in out if d.kind == BOOK or d.reason != REASON_NO_ACCEPTANCE]
    return out


__all__ = [
    "ASK",
    "BOOK",
    "REASON_NO_ACCEPTANCE",
    "REASON_TWO_PRICES",
    "REASON_UNCLEAR_AMOUNT",
    "REASON_UNKNOWN_SPEAKER",
    "Decision",
    "Line",
    "decide",
    "is_acceptance",
    "is_price_question",
    "says_cash",
]
