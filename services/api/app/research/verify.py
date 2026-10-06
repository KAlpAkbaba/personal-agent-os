"""Doğrula modu (card verify-mode): the pure verdict core.

"Bunu doğrula: ...", "X doğru mu", "şunu kontrol et: ..." -> a claim (the sentence as the
owner said it and the claim without the trigger), a verdict DOĞRU | YANLIŞ | KISMEN |
BELİRSİZ with a confidence, at most five decisive sources, the strongest counter-argument and
one spoken sentence. Kept records are recalled by text and by date ("geçen hafta neyi
doğrulamıştık", "X hakkında ne bulmuştuk").

Honesty (ADR-0063 truthful speech): no decisive source -> BELİRSİZ with confidence 0, never a
guess; a source dated before the year the claim names is said aloud. Each source's stance
(supports / refutes / neutral) comes from a judge outside this module; this module only maps
the judged evidence to a verdict, deterministically. Codes are stored ASCII, labels Turkish.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

VERDICT_TRUE = "dogru"
VERDICT_FALSE = "yanlis"
VERDICT_PARTLY = "kismen"
VERDICT_UNKNOWN = "belirsiz"

VERDICT_LABELS: dict[str, str] = {
    VERDICT_TRUE: "DOĞRU",
    VERDICT_FALSE: "YANLIŞ",
    VERDICT_PARTLY: "KISMEN",
    VERDICT_UNKNOWN: "BELİRSİZ",
}
_VERDICT_SPOKEN: dict[str, str] = {
    VERDICT_TRUE: "doğru",
    VERDICT_FALSE: "yanlış",
    VERDICT_PARTLY: "kısmen doğru",
    VERDICT_UNKNOWN: "belirsiz",
}

STANCE_SUPPORTS = "supports"
STANCE_REFUTES = "refutes"
STANCE_NEUTRAL = "neutral"
STANCES: tuple[str, ...] = (STANCE_SUPPORTS, STANCE_REFUTES, STANCE_NEUTRAL)

MAX_SOURCES = 5

_MONTHS_TR = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)  # fmt: skip


@dataclass(frozen=True)
class VerifySource:
    url: str
    title: str
    published_at: datetime | None
    quote: str
    stance: str

    def __post_init__(self) -> None:
        if self.stance not in STANCES:
            raise ValueError(f"unknown stance: {self.stance!r}")

    @property
    def decisive(self) -> bool:
        """A source decides only with a stance AND the quote that decides."""
        return self.stance != STANCE_NEUTRAL and bool(self.quote.strip())


@dataclass(frozen=True)
class Verdict:
    code: str
    confidence: float

    @property
    def label(self) -> str:
        return VERDICT_LABELS[self.code]


@dataclass(frozen=True)
class VerificationRecord:
    said: str
    claim: str
    verdict: Verdict
    sources: tuple[VerifySource, ...]
    created_at: datetime


# --- claim -------------------------------------------------------------------------------

_TRIGGER_PREFIX = re.compile(
    r"^\s*(?:(?:bunu|şunu|sunu)\s+)?(?:doğrula|dogrula|kontrol\s+et|teyit\s+et)\b\s*[:,\-–]?\s*",
    re.IGNORECASE,
)
_TRIGGER_SUFFIX = re.compile(r"\s*,?\s*(?:doğru|dogru)\s+mu\s*[?.!]*\s*$", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


def normalise_claim(said: str) -> str:
    """The claim without the trigger words and the closing punctuation."""
    claim = _TRIGGER_PREFIX.sub("", said, count=1)
    claim = _TRIGGER_SUFFIX.sub("", claim, count=1)
    claim = re.sub(r"\s+", " ", claim).strip()
    return claim.rstrip(".?!…").strip()


def claim_subject_year(claim: str) -> int | None:
    years = [int(y) for y in _YEAR.findall(claim)]
    return max(years) if years else None


# --- verdict -----------------------------------------------------------------------------


def select_sources(sources: Iterable[VerifySource]) -> list[VerifySource]:
    """The decisive sources in the research engine's rank order, at most five."""
    return [s for s in sources if s.decisive][:MAX_SOURCES]


def decide_verdict(sources: Iterable[VerifySource]) -> Verdict:
    kept = select_sources(sources)
    supports = sum(1 for s in kept if s.stance == STANCE_SUPPORTS)
    refutes = sum(1 for s in kept if s.stance == STANCE_REFUTES)
    decisive = supports + refutes
    if decisive == 0:
        return Verdict(VERDICT_UNKNOWN, 0.0)
    if refutes == 0:
        code = VERDICT_TRUE
    elif supports == 0:
        code = VERDICT_FALSE
    else:
        code = VERDICT_PARTLY
    agreement = max(supports, refutes) / decisive
    strength = 0.5 if decisive == 1 else min(0.9, 0.5 + 0.15 * (decisive - 1))
    return Verdict(code, round(agreement * strength, 2))


def counter_argument(verdict: Verdict, sources: Iterable[VerifySource]) -> VerifySource | None:
    """The best-ranked source against the verdict; for KISMEN, against the majority."""
    kept = select_sources(sources)
    if verdict.code == VERDICT_TRUE:
        against = STANCE_REFUTES
    elif verdict.code == VERDICT_FALSE:
        against = STANCE_SUPPORTS
    elif verdict.code == VERDICT_PARTLY:
        supports = sum(1 for s in kept if s.stance == STANCE_SUPPORTS)
        against = STANCE_REFUTES if supports * 2 >= len(kept) else STANCE_SUPPORTS
    else:
        return None
    return next((s for s in kept if s.stance == against), None)


def stale_sources(claim: str, sources: Iterable[VerifySource]) -> list[VerifySource]:
    """Kept sources published before the year the claim names."""
    year = claim_subject_year(claim)
    if year is None:
        return []
    return [
        s
        for s in select_sources(sources)
        if s.published_at is not None and s.published_at.year < year
    ]


def _spoken_date(value: datetime) -> str:
    return f"{value.day} {_MONTHS_TR[value.month - 1]} {value.year}"


def spoken_sentence(claim: str, verdict: Verdict, sources: Sequence[VerifySource]) -> str:
    """One or two short sentences: the verdict, the first source, a stale-source warning."""
    kept = select_sources(sources)
    if verdict.code == VERDICT_UNKNOWN or not kept:
        return "Bunu doğrulayacak bir kaynak bulamadım; hüküm belirsiz."
    first = kept[0]
    dated = f", {_spoken_date(first.published_at)}" if first.published_at is not None else ""
    text = (
        f"Hüküm: {_VERDICT_SPOKEN[verdict.code]} (güven yüzde {round(verdict.confidence * 100)}). "
        f"Kaynak: {first.title}{dated}."
    )
    if stale_sources(claim, kept):
        text += " Kaynaklardan biri iddianın konusundan eski."
    return text


# --- recall ------------------------------------------------------------------------------

_FOLD = str.maketrans({"İ": "i", "I": "ı"})
_WORD = re.compile(r"\w+")


def _fold(text: str) -> str:
    return text.translate(_FOLD).lower()


def _words(text: str) -> list[str]:
    return _WORD.findall(_fold(text))


def recall_window(phrase: str, now: datetime) -> tuple[datetime | None, datetime | None]:
    """[since, until) for the date words the owner used; (None, None) when there are none."""
    folded = " ".join(_words(phrase))
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())
    month = day.replace(day=1)

    def said(words: str) -> bool:
        # "dün" must not match inside "dünya": whole words, any suffix on the last one.
        return re.search(rf"(?<!\w){words}", folded) is not None

    if said("geçen hafta"):
        return week - timedelta(days=7), week
    if said("bu hafta"):
        return week, week + timedelta(days=7)
    if said("geçen ay"):
        return (month - timedelta(days=1)).replace(day=1), month
    if said(r"bu ay(?:ın|da)?\b"):
        return month, (month + timedelta(days=32)).replace(day=1)
    if said(r"dün(?:kü)?\b"):
        return day - timedelta(days=1), day
    if said("bugün"):
        return day, day + timedelta(days=1)
    return None, None


def _matches(record: VerificationRecord, text: str) -> bool:
    wanted = _words(text)
    have = _words(record.claim)
    return all(any(h.startswith(w) for h in have) for w in wanted)


def recall(
    records: Iterable[VerificationRecord],
    *,
    text: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[VerificationRecord]:
    """Records whose claim holds every word of ``text`` (Turkish-folded, word prefix) and
    whose time is in [since, until); newest first."""
    found = [
        r
        for r in records
        if (not text or _matches(r, text))
        and (since is None or r.created_at >= since)
        and (until is None or r.created_at < until)
    ]
    return sorted(found, key=lambda r: r.created_at, reverse=True)
