"""Doğrula modu (card verify-mode): the trigger, the verdict core and the store.

"Bunu doğrula: ...", "X doğru mu", "şunu kontrol et: ..." -> a claim (the sentence as the
owner said it and the claim without the trigger), a verdict DOĞRU | YANLIŞ | KISMEN |
BELİRSİZ with a confidence, at most five decisive sources, the strongest counter-argument and
one spoken sentence. Kept records are recalled by text and by date ("geçen hafta neyi
doğrulamıştık", "X hakkında ne bulmuştuk").

Honesty (ADR-0063 truthful speech): no decisive source -> BELİRSİZ with confidence 0, never a
guess; a source dated before the year the claim names is said aloud; one source is said as
one source. Each source's stance (supports / refutes / neutral) comes from the deterministic
judge in the "stance judge" section (the seam a model-backed judge replaces); the verdict is
mapped from the judged evidence, deterministically. Codes are stored ASCII, labels Turkish.

Sections: claim and trigger (pure), verdict (pure), recall (pure), stance judge (pure), store
(``claim_verifications``; the voice tools, the announcer and the REST list all go through it).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.research.models import (
    STAGE_FAILED,
    TERMINAL_STAGES,
    VERIFICATION_PENDING,
    VERIFICATION_SETTLED,
    ClaimVerificationRow,
)

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
#: A four-digit number is a year unless a unit follows it ("8849 metre", "2000 kişi").
_YEAR = re.compile(
    r"(?<!\d)(19\d{2}|20\d{2})(?!\d)(?!\s*(?:metre|m\b|km|kilometre|kg|kilo|ton|gram|litre"
    r"|kişi|adet|lira|tl\b|dolar|euro|avro|sterlin|yen\b|derece|mil\b))",
    re.IGNORECASE,
)


#: "..., doğrula" / "...: teyit et" at the end of the sentence: the claim came first.
_TRIGGER_TAIL = re.compile(
    r"\s*[,:;\-–]\s*(?:bunu\s+|şunu\s+|sunu\s+)?(?:doğrula|dogrula|kontrol\s+et|teyit\s+et)"
    r"(?:\s*(?:misin|mısın|bakalım))?\s*[?.!]*\s*$",
    re.IGNORECASE,
)
#: The question particle a claim put to the test may end on ("Ankara mı, doğrula").
_CLAIM_PARTICLE = re.compile(r"\s+(?:mı|mi|mu|mü)\s*$", re.IGNORECASE)
#: A nominalised verb right before "doğru mu" ("... olduğu doğru mu"): a claim is put to test.
_NOMINALISED = re.compile(r"(?:d|t)(?:ığı|iği|uğu|üğü|ıkları|ikleri|ukları|ükleri)n?$")
_RECALL_VERB = re.compile(r"^(?:doğrula|dogrula)(?:mış|mis|dı|di|dığ|dig)", re.IGNORECASE)
_RECALL_FOUND = frozenset({"bulmuştuk", "bulduk", "bulmuştun", "buldun", "bulmustuk"})
_RECALL_ABOUT = frozenset({"hakkında", "hakkinda", "konusunda"})
#: A claim needs this many words to be one when only "doğru mu" says it is put to the test.
_MIN_PLAIN_CLAIM_WORDS = 5
#: ...and this many when a leading trigger has no separator ("doğrula Türkiye'nin başkenti
#: Ankara").
_MIN_UNMARKED_CLAIM_WORDS = 3

VERIFY_CLAIM = "claim"
VERIFY_RECALL = "recall"


def verify_request_kind(text: str) -> str | None:
    """ "claim" for a sentence that puts a claim to the test, "recall" for one that asks what
    was verified before, None otherwise. Only the owner's own sentence triggers a verify: there
    is no reading of anyone else's speech here (card verify-mode).

    Near-misses stay out: "Doğru söylüyorsun." (agreement), "Bu dosya doğru mu?" (two words,
    artifact.validate's), "Uygulamayı doğrula." (no claim; native.verify's), "Gelen kutusunu
    kontrol et." (no trigger at the start, no separator)."""
    words = _words(text)
    if not words:
        return None
    stripped = text.strip()
    lead = _TRIGGER_PREFIX.match(stripped)
    if lead is not None:
        rest = _words(stripped[lead.end() :])
        if re.search(r"[:,\-–]", lead.group(0)):
            return VERIFY_CLAIM if rest else None
        # No separator: "kontrol et" alone opens too many requests ("kontrol et bakalım hava
        # nasıl"); only "doğrula" / "teyit et" lead an unmarked claim.
        if "kontrol" in _words(lead.group(0)) or len(rest) < _MIN_UNMARKED_CLAIM_WORDS:
            return None
        return VERIFY_CLAIM
    if any(_RECALL_VERB.match(w) for w in words) or (
        _RECALL_ABOUT & set(words) and _RECALL_FOUND & set(words)
    ):
        return VERIFY_RECALL
    tail = _TRIGGER_TAIL.search(stripped)
    if tail is not None and len(_words(stripped[: tail.start()])) >= _MIN_UNMARKED_CLAIM_WORDS:
        return VERIFY_CLAIM
    suffix = _TRIGGER_SUFFIX.search(stripped)
    if suffix is not None:
        before = _words(stripped[: suffix.start()])
        if before and (_NOMINALISED.search(before[-1]) or len(before) >= _MIN_PLAIN_CLAIM_WORDS):
            return VERIFY_CLAIM
    return None


def normalise_claim(said: str) -> str:
    """The claim without the trigger words and the closing punctuation."""
    claim = _TRIGGER_PREFIX.sub("", said, count=1)
    claim = _TRIGGER_TAIL.sub("", claim, count=1)
    claim = _TRIGGER_SUFFIX.sub("", claim, count=1)
    claim = re.sub(r"\s+", " ", claim).strip().rstrip(".?!…").strip()
    return _CLAIM_PARTICLE.sub("", claim).strip()


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
    # Lead decision (run 3): one source keeps the 0.5 ceiling AND is said as one source.
    lead_in = "Tek kaynak" if len(kept) == 1 else "Kaynak"
    text = (
        f"Hüküm: {_VERDICT_SPOKEN[verdict.code]} (güven yüzde {round(verdict.confidence * 100)}). "
        f"{lead_in}: {first.title}{dated}."
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
        # Whole words: the lookbehind keeps "dün" out of "ödün", the patterns' own "\b" keeps
        # it out of "dünya" ("geçen hafta" may carry any suffix: "geçen haftaki").
        return re.search(rf"(?<!\w){words}", folded) is not None

    if said("geçen hafta"):
        return week - timedelta(days=7), week
    if said("bu hafta"):
        return week, week + timedelta(days=7)
    if said(r"geçen ay(?:ın|da|ki)?\b"):
        return (month - timedelta(days=1)).replace(day=1), month
    if said(r"bu ay(?:ın|da)?\b"):
        return month, (month + timedelta(days=32)).replace(day=1)
    if said(r"dün(?:kü)?\b"):
        return day - timedelta(days=1), day
    if said("bugün"):
        return day, day + timedelta(days=1)
    return None, None


class _Recallable(Protocol):
    @property
    def claim(self) -> str: ...

    @property
    def created_at(self) -> datetime: ...


def _matches(record: _Recallable, text: str) -> bool:
    # A one-letter word is a suffix the apostrophe cut off ("Everest'i" -> "everest", "i").
    wanted = [w for w in _words(text) if len(w) > 1]
    have = _words(record.claim)
    return all(any(h.startswith(w) for h in have) for w in wanted)


#: What a recall sentence says besides its subject: the date words, the verbs of asking.
_RECALL_NOISE = frozenset(
    {
        "geçen", "bu", "hafta", "haftaki", "ay", "ayın", "ayda", "ayki", "dün", "dünkü",
        "bugün", "ne", "neyi", "neleri", "nelerin", "neler", "hangi", "şey", "şeyi", "hani",
        "biz", "sen", "daha", "önce", "mi", "mı", "mu", "mü", "doğrulamıştık", "doğruladık",
        "doğrulamıştın", "doğruladın", "doğrulattım", "doğrulatmıştım", "kontrol", "etmiştik",
        "ettik", "ettirmiştim", "bulmuştuk", "bulduk", "bulmuştun", "buldun", "hakkında",
        "konusunda", "ile", "ilgili", "sonuç", "sonucu", "neydi", "çıkmıştı", "çıktı",
    }
)  # fmt: skip


def recall_subject(phrase: str) -> str | None:
    """The subject of a recall sentence ("Everest hakkında ne bulmuştuk" -> "everest"), or
    None when the owner named only a time ("geçen hafta neyi doğrulamıştık")."""
    kept = [w for w in _words(phrase) if len(w) > 1 and w not in _RECALL_NOISE]
    return " ".join(kept) or None


def recall[R: _Recallable](
    records: Iterable[R],
    *,
    text: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[R]:
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


# --- stance judge (deterministic seam) ---------------------------------------------------

#: Words that turn a sentence against what it states (ADR decision 8: a deterministic judge;
#: a model-backed one is a later card and replaces only this section).
_NEGATIONS = frozenset(
    {
        "değil", "değildir", "yok", "yoktur", "yanlış", "yanlıştır", "yalan", "asılsız",
        "olmadı", "olmamıştır", "çürütüldü", "yalanlandı", "yalanladı", "reddetti",
    }
)  # fmt: skip
#: Words that carry no content of the claim.
_STOPWORDS = frozenset(
    {
        "ve", "ile", "bir", "bu", "şu", "da", "de", "ki", "mi", "mı", "için", "olarak",
        "olan", "olduğu", "olduğunu", "çok", "daha", "en", "gibi", "kadar", "ise",
    }
)  # fmt: skip
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
#: Share of the claim's content stems one sentence must carry to speak about the claim.
MIN_OVERLAP = 0.6
_STEM = 5
MAX_QUOTE = 300


def _stems(text: str) -> set[str]:
    return {
        w[:_STEM] for w in _words(text) if len(w) > 2 and w not in _STOPWORDS and not w.isdigit()
    }


def _negated(text: str) -> bool:
    return any(w in _NEGATIONS for w in _words(text))


def judge_stance(claim: str, excerpt: str) -> tuple[str, str]:
    """(stance, quote): the excerpt's sentence that speaks most about the claim, and whether
    it supports or refutes it. Neutral (with no quote) when no sentence carries enough of the
    claim. Refutes when its negation differs from the claim's, or when both name numbers and
    none of the claim's appears in it ("8849 metre" against "8848 metre")."""
    wanted = _stems(claim)
    if not wanted:
        return STANCE_NEUTRAL, ""
    best, best_share = "", 0.0
    for sentence in _SENTENCE_END.split(excerpt or ""):
        share = len(wanted & _stems(sentence)) / len(wanted)
        if share > best_share:
            best, best_share = sentence.strip(), share
    if best_share < MIN_OVERLAP:
        return STANCE_NEUTRAL, ""
    claim_numbers = set(_NUMBER.findall(claim))
    quote_numbers = set(_NUMBER.findall(best))
    numbers_disagree = bool(claim_numbers and quote_numbers and not claim_numbers & quote_numbers)
    against = _negated(best) != _negated(claim) or numbers_disagree
    return (STANCE_REFUTES if against else STANCE_SUPPORTS), best[:MAX_QUOTE]


def _parsed_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def sources_from_report(claim: str, report_json: dict[str, Any] | None) -> list[VerifySource]:
    """The research report's sources, judged against the claim, in the report's own order. A
    source the pipeline flagged as a suspected prompt injection never decides anything."""
    out: list[VerifySource] = []
    for raw in (report_json or {}).get("sources") or ():
        if not isinstance(raw, dict) or raw.get("injection_suspected"):
            continue
        url = str(raw.get("final_url") or raw.get("url") or "").strip()
        if not url:
            continue
        stance, quote = judge_stance(claim, str(raw.get("excerpt") or ""))
        out.append(
            VerifySource(
                url=url[:2000],
                title=str(raw.get("title") or raw.get("publisher") or url).strip()[:300],
                published_at=_parsed_date(raw.get("published_at")),
                quote=quote,
                stance=stance,
            )
        )
    return out


# --- store (claim_verifications) ---------------------------------------------------------

#: When the run behind a verify failed before it found anything: not "no source", the truth.
RUN_FAILED_TR = "Doğrulama araştırması tamamlanamadı; hüküm belirsiz."
#: The voice tool's immediate answer; the verdict follows when the run settles.
STARTED_TR = "Doğruluyorum; kaynakları tarayıp hükmü söyleyeceğim."
NOTHING_RECALLED_TR = "Bu konuda daha önce doğruladığımız bir şey bulamadım."
RECALL_LIMIT = 500


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _source_as_dict(source: VerifySource) -> dict[str, Any]:
    return {
        "url": source.url,
        "title": source.title,
        "published_at": _iso(source.published_at),
        "quote": source.quote,
        "stance": source.stance,
    }


def start_pending(
    db: Session, *, said: str, claim: str, task_id: uuid.UUID | None, now: datetime
) -> ClaimVerificationRow:
    row = ClaimVerificationRow(
        id=uuid.uuid4(),
        created_at=now,
        said=said[:2000],
        claim=claim[:1000],
        status=VERIFICATION_PENDING,
        task_id=task_id,
        sources_json=[],
    )
    db.add(row)
    db.flush()
    return row


def settle(
    db: Session,
    row: ClaimVerificationRow,
    *,
    report_json: dict[str, Any] | None,
    run_failed: bool,
    now: datetime,
) -> dict[str, Any]:
    """Decide the verdict from the run's report, write it on the row, and return the tool
    result the owner hears (``speech``). Idempotent: a settled row answers what it holds."""
    if row.status != VERIFICATION_SETTLED:
        judged = sources_from_report(row.claim, report_json)
        verdict = decide_verdict(judged)
        kept = select_sources(judged)
        counter = counter_argument(verdict, judged)
        if run_failed and not kept:
            spoken = RUN_FAILED_TR
        else:
            spoken = spoken_sentence(row.claim, verdict, kept)
        row.status = VERIFICATION_SETTLED
        row.verdict = verdict.code
        row.confidence = verdict.confidence
        row.sources_json = [_source_as_dict(s) for s in kept]
        row.counter_json = _source_as_dict(counter) if counter is not None else None
        row.spoken = spoken[:1000]
        row.settled_at = now
        db.flush()
    return {"speech": row.spoken, "spoken_result": row.spoken, **row_as_dict(row)}


def settle_task(db: Session, task_id: uuid.UUID, *, now: datetime) -> dict[str, Any] | None:
    """Settle the verification behind ``task_id`` once its run is terminal and return what the
    owner hears; None while the run is still going, or when no verification is behind it."""
    from app.research import runs_service

    row = db.execute(
        select(ClaimVerificationRow).where(ClaimVerificationRow.task_id == task_id)
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.status == VERIFICATION_SETTLED:
        return settle(db, row, report_json=None, run_failed=False, now=now)
    run = runs_service.get_run(db, task_id)
    if run is None or run.stage not in TERMINAL_STAGES:
        return None
    report = runs_service.get_report(db, task_id)
    report_json = dict(report.report_json) if report is not None and report.report_json else None
    return settle(db, row, report_json=report_json, run_failed=run.stage == STAGE_FAILED, now=now)


def settle_pending(db: Session, *, now: datetime, limit: int = 50) -> int:
    """Settle every pending verification whose run has finished (a recall reads settled rows
    even when the announcer has not passed yet). Returns how many were settled."""
    task_ids = (
        db.execute(
            select(ClaimVerificationRow.task_id)
            .where(
                ClaimVerificationRow.status == VERIFICATION_PENDING,
                ClaimVerificationRow.task_id.is_not(None),
            )
            .order_by(ClaimVerificationRow.created_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return sum(
        1 for tid in task_ids if tid is not None and settle_task(db, tid, now=now) is not None
    )


def search(
    db: Session,
    *,
    text: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = RECALL_LIMIT,
) -> list[ClaimVerificationRow]:
    """Recall from the table: the date window in SQL, the words Turkish-folded in Python
    (:func:`recall`), newest first."""
    stmt = select(ClaimVerificationRow)
    if since is not None:
        stmt = stmt.where(ClaimVerificationRow.created_at >= since)
    if until is not None:
        stmt = stmt.where(ClaimVerificationRow.created_at < until)
    stmt = stmt.order_by(ClaimVerificationRow.created_at.desc()).limit(limit)
    return recall(list(db.execute(stmt).scalars().all()), text=text)


def recall_speech(rows: Sequence[ClaimVerificationRow]) -> str:
    """One or two sentences for a recall: the newest claim, what was found, how many more."""
    if not rows:
        return NOTHING_RECALLED_TR
    newest = rows[0]
    if newest.status != VERIFICATION_SETTLED or newest.verdict is None:
        head = f"'{newest.claim}' iddiasını doğruluyorduk; hüküm henüz çıkmadı."
    else:
        sources = newest.sources_json or []
        title = str(sources[0].get("title") or "") if sources else ""
        head = f"'{newest.claim}' iddiası için hüküm {_VERDICT_SPOKEN[newest.verdict]} çıkmıştı"
        head += f"; kaynak: {title}." if title else "."
    more = len(rows) - 1
    return head + (f" Bu aralıkta {more} doğrulama daha var." if more else "")


def row_as_dict(row: ClaimVerificationRow) -> dict[str, Any]:
    return {
        "verification_id": str(row.id),
        "created_at": _iso(row.created_at),
        "said": row.said,
        "claim": row.claim,
        "status": row.status,
        "task_id": str(row.task_id) if row.task_id else None,
        "verdict": row.verdict,
        "verdict_label": VERDICT_LABELS.get(row.verdict or ""),
        "confidence": row.confidence,
        "sources": list(row.sources_json or []),
        "counter_argument": row.counter_json,
        "spoken": row.spoken,
        "settled_at": _iso(row.settled_at),
    }
