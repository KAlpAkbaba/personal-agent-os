"""What the owner said about his money: one command, or nothing (money-ledger).

Six shapes, anchored on EXACT words (the memory rule "Turkish suffixes break prefix matching"):

* balance - "hesabımda ne kadar var", "bakiyem ne kadar", "ne kadar param var";
* spent - "bu ay markete ne harcadım", "bu ay ne kadar harcadım", "harcamalarım ne kadar";
* yes - "evet harcadım", and - only while his question is open - a bare "evet",
  "evet ama 750";
* no - "hayır harcamadım", "harcama yapmadım", and - only while open - a bare "hayır";
* undo - "harcamayı geri al", "harcamayı sil", and - only right after a booking was announced
  and nothing else is in focus - a bare "geri al";
* cash - "<amount> nakit verdim", "nakit <amount> ödedim" (an amount is required).

Nothing here moves money: "Ahmet'e iki yüz lira gönder" is not a shape of this module.
The router passes its normalised tokens: numerals are WORDS by then ("yedi yüz elli").

Pure apart from :mod:`app.money.pending` (a process-local "his question is open" note).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.money import categories, pending
from app.money.amounts import money_in, words_amount

ACTION_BALANCE: Final = "balance"
ACTION_SPENT: Final = "spent"
ACTION_YES: Final = "yes"
ACTION_NO: Final = "no"
ACTION_UNDO: Final = "undo"
ACTION_CASH: Final = "cash"


@dataclass(frozen=True)
class MoneyCommand:
    action: str
    matched: str
    amount_kurus: int | None = None
    category: str | None = None


_BALANCE_NOUNS: Final = frozenset(
    {"hesabımda", "hesabımızda", "hesapta", "hesabımdaki", "bankada", "bankamda", "kartımda"}
)
_BALANCE_WORDS: Final = frozenset({"bakiyem", "bakiyemiz", "bakiye", "bakiyeyi"})
_SPENT_VERBS: Final = frozenset({"harcadım", "harcamışım", "harcadık"})
_SPENT_NOUNS: Final = frozenset({"harcamalarım", "harcamam", "harcamalarımız"})
_PERIOD: Final = ("bu", "ay")
_YES_VERBS: Final = frozenset({"harcadım", "aldım", "yaptım", "ödedim"})
_NO_VERBS: Final = frozenset({"harcamadım", "almadım", "yapmadım", "ödemedim"})
_UNDO_OBJECTS: Final = frozenset({"harcamayı", "harcamamı", "kaydı", "ödemeyi"})
_UNDO_VERBS: Final = (("geri", "al"), ("sil",), ("iptal", "et"))
_CASH_VERBS: Final = frozenset({"verdim", "ödedim", "harcadım"})


def _has_seq(words: list[str], seq: tuple[str, ...]) -> bool:
    n = len(seq)
    return any(tuple(words[i : i + n]) == seq for i in range(len(words) - n + 1))


def _balance(words: list[str]) -> bool:
    if _has_seq(words, ("ne", "kadar", "param", "var")):
        return True
    if any(w in _BALANCE_NOUNS for w in words) and (
        _has_seq(words, ("ne", "kadar")) or "kaç" in words
    ):
        # "Hesabımda kaç mail var?" is the inbox, not money.
        return not any(w in ("mail", "posta", "ileti", "mesaj") for w in words) and (
            "var" in words or "para" in words or "lira" in words
        )
    return any(w in _BALANCE_WORDS for w in words) and (
        _has_seq(words, ("ne", "kadar")) or "ne" in words or "nedir" in words
    )


def _spent(words: list[str]) -> str | None:
    if any(w in _SPENT_NOUNS for w in words) and _has_seq(words, ("ne", "kadar")):
        return "spent"
    if any(w in _SPENT_VERBS for w in words) and (
        "ne" in words or _has_seq(words, ("ne", "kadar"))
    ):
        return "spent"
    return None


def _amount(words: list[str]) -> int | None:
    found = money_in(" ".join(words), bare=True)
    if found:
        return found[0]
    return words_amount(words) if words else None


def parse_tokens(
    tokens: tuple[str, ...],
    *,
    busy: bool = False,
    focused: bool = False,
) -> MoneyCommand | None:
    """The router's entry. ``busy``: something else waits for a yes/no (a mail draft, a
    pending mutation...) - then a bare "evet" is not ours. ``focused``: a document or a
    creative run is in focus - then a bare "geri al" is theirs."""
    words = [t.strip("'") for t in tokens if t.strip("'")]
    if not words:
        return None
    if _balance(words):
        return MoneyCommand(ACTION_BALANCE, "hesabımda ne kadar var")
    if _spent(words):
        category = None
        for word in words:
            if category := categories.categorize(word):
                break
        return MoneyCommand(ACTION_SPENT, "ne harcadım", category=category)
    head = words[0]
    if any(w in _UNDO_OBJECTS for w in words) and any(_has_seq(words, v) for v in _UNDO_VERBS):
        return MoneyCommand(ACTION_UNDO, "harcamayı geri al")
    if "nakit" in words and any(w in _CASH_VERBS for w in words):
        rest = [w for w in words if w != "nakit"]
        found = money_in(" ".join(rest))
        if found:
            return MoneyCommand(
                ACTION_CASH,
                "nakit verdim",
                amount_kurus=found[0],
                category=categories.categorize(" ".join(rest)),
            )
    if head == "evet" and any(w in _YES_VERBS for w in words[1:]) and len(words) <= 4:
        return MoneyCommand(ACTION_YES, "evet harcadım")
    if head == "hayır" and any(w in _NO_VERBS for w in words[1:]) and len(words) <= 4:
        return MoneyCommand(ACTION_NO, "hayır harcamadım")
    if words in (["harcama", "yapmadım"], ["harcamadım"]):
        return MoneyCommand(ACTION_NO, "harcamadım")
    if pending.question_open() and not busy:
        if words == ["evet"]:
            return MoneyCommand(ACTION_YES, "evet")
        if words == ["hayır"]:
            return MoneyCommand(ACTION_NO, "hayır")
        if head == "evet" and len(words) >= 3 and words[1] == "ama":
            amount = _amount(words[2:])
            if amount:
                return MoneyCommand(ACTION_YES, "evet ama", amount_kurus=amount)
    if pending.booking_open() and not focused and words == ["geri", "al"]:
        return MoneyCommand(ACTION_UNDO, "geri al")
    return None


__all__ = [
    "ACTION_BALANCE",
    "ACTION_CASH",
    "ACTION_NO",
    "ACTION_SPENT",
    "ACTION_UNDO",
    "ACTION_YES",
    "MoneyCommand",
    "parse_tokens",
]
