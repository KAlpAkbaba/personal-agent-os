"""M13 evidence + labelled-statement contracts, and dedup/rank/syndication.

``EvidenceRecord`` is the JSON-serializable shape evidence crosses the
API<->Windows-Browser-Agent boundary in (M13_RESEARCH_SPEC.md §1: "Evidence —
EvidenceRecord extended with id, final_url, publisher, published_at,
modified_at, retrieved_at, page_kind, http_status, injection_suspected,
device_id, command_id"). This module does NOT import the browser package: the
two services run as separate processes/machines (Hetzner Cloud Core vs. the
owner's Windows box) and only ever agree on a wire shape, exactly like the
device-command envelope in ``packages/protocol/DEVICE_PROTOCOL.md`` agrees on
JSON, not Python types. The ranking formula is likewise intentionally
duplicated (not imported) from ``browser_agent.evidence.dedup_and_rank_evidence``
for the same reason; keep the two in sync by hand if the formula changes (see
docs/DECISIONS.md ADR-0035/ADR-0050).

``LabelledStatement`` is the M13 spec's core content-integrity requirement:
"Every statement is labelled source_fact | model_inference | recommendation |
uncertainty and carries provenance." The label is validated at construction;
provenance (``evidence_urls``) is NOT hard-required by the dataclass itself
(an ``uncertainty`` statement about a total absence of sources has nothing to
cite) — instead each synthesis provider is responsible for actually attaching
provenance, and the synthesis unit tests assert it does.

Dedup/ranking (spec §2): canonical-URL dedup keeping the richer excerpt, then
near-duplicate TITLE detection (normalized token Jaccard >= 0.9) across
different URLs — the lower-priority copy (official > technical > academic >
news > community > unknown) is kept in the output (still stored, still
scored) but marked ``syndicated_of`` the primary's URL, so downstream report
building can exclude it from findings/sources while the record itself stays
auditable. Ranking: ``0.6*source_class_weight + 0.25*keyword_overlap +
0.15*recency_bonus``, ties broken by normalized URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

STATEMENT_LABEL_SOURCE_FACT = "source_fact"
STATEMENT_LABEL_MODEL_INFERENCE = "model_inference"
STATEMENT_LABEL_RECOMMENDATION = "recommendation"
STATEMENT_LABEL_UNCERTAINTY = "uncertainty"
STATEMENT_LABELS: tuple[str, ...] = (
    STATEMENT_LABEL_SOURCE_FACT,
    STATEMENT_LABEL_MODEL_INFERENCE,
    STATEMENT_LABEL_RECOMMENDATION,
    STATEMENT_LABEL_UNCERTAINTY,
)

SOURCE_CLASSES: tuple[str, ...] = (
    "official",
    "technical",
    "academic",
    "news",
    "community",
    "unknown",
)

# Primary-source priority for syndication tie-breaks (spec §2): lower is
# preferred (kept as primary when two evidence items share a near-duplicate
# title).
_PUBLISHER_PRIORITY: dict[str, int] = {cls: i for i, cls in enumerate(SOURCE_CLASSES)}

PAGE_KIND_OK = "ok"
PAGE_KIND_AUTH_WALL = "auth_wall"
PAGE_KIND_CAPTCHA = "captcha"
PAGE_KIND_ERROR_PAGE = "error_page"
PAGE_KIND_BLOCKED = "blocked"
PAGE_KIND_EMPTY = "empty"
PAGE_KINDS: tuple[str, ...] = (
    PAGE_KIND_OK,
    PAGE_KIND_AUTH_WALL,
    PAGE_KIND_CAPTCHA,
    PAGE_KIND_ERROR_PAGE,
    PAGE_KIND_BLOCKED,
    PAGE_KIND_EMPTY,
)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One provenance-complete evidence item (wire shape)."""

    url: str
    title: str
    excerpt: str
    fetched_at: datetime
    extraction_method: str
    source_class: str = "unknown"
    query: str = ""
    rank: int = 0
    score: float = 0.0
    # ---- M13 spec §1 provenance extension ----
    id: str = ""
    final_url: str = ""
    publisher: str = ""
    published_at: datetime | None = None
    modified_at: datetime | None = None
    retrieved_at: datetime | None = None
    page_kind: str = PAGE_KIND_OK
    http_status: int | None = None
    injection_suspected: bool = False
    device_id: str | None = None
    command_id: str | None = None
    syndicated_of: str | None = None

    def __post_init__(self) -> None:
        if self.retrieved_at is None:
            object.__setattr__(self, "retrieved_at", self.fetched_at)
        if not self.final_url:
            object.__setattr__(self, "final_url", self.url)

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "excerpt": self.excerpt,
            "fetched_at": self.fetched_at.isoformat(),
            "extraction_method": self.extraction_method,
            "source_class": self.source_class,
            "query": self.query,
            "rank": self.rank,
            "score": self.score,
            "id": self.id,
            "final_url": self.final_url,
            "publisher": self.publisher,
            "published_at": _iso(self.published_at),
            "modified_at": _iso(self.modified_at),
            "retrieved_at": _iso(self.retrieved_at),
            "page_kind": self.page_kind,
            "http_status": self.http_status,
            "injection_suspected": self.injection_suspected,
            "device_id": self.device_id,
            "command_id": self.command_id,
            "syndicated_of": self.syndicated_of,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, stage: str = "loading_evidence") -> EvidenceRecord:
        """Rebuild a stored evidence row, validating every numeric field first.

        ``rank`` and ``score`` go through the declared contract
        (:mod:`app.research.contracts`) rather than a bare ``int()``/``float()``: a row whose
        numeric field carries prose is a :class:`ContractViolation` naming the field and the
        observed value class, which the caller quarantines - it never becomes a ValueError
        that fails a whole research job (owner incident, 2026-09-04).
        """
        from app.research.contracts import (
            ENTITY_EVIDENCE_ITEM,
            require_number,
            require_text,
        )
        from app.research.contracts import (
            schema as entity_schema,
        )

        entity = ENTITY_EVIDENCE_ITEM
        item_schema = entity_schema(ENTITY_EVIDENCE_ITEM)

        def text(field_name: str, default: str = "") -> str:
            return require_text(
                data.get(field_name, default), entity, field_name, entity_id=url, stage=stage
            )

        def number(field_name: str, default: float) -> float:
            value = require_number(
                data.get(field_name, default), entity, field_name, entity_id=url, stage=stage
            )
            return default if value is None else value

        url = require_text(data.get("url"), entity, "url", stage=stage)
        return cls(
            url=url,
            title=text("title"),
            excerpt=text("excerpt"),
            fetched_at=datetime.fromisoformat(
                str(item_schema.read(data, "fetched_at", entity_id=url, stage=stage)).replace(
                    "Z", "+00:00"
                )
            ),
            extraction_method=str(
                item_schema.read(data, "extraction_method", entity_id=url, stage=stage)
            ),
            source_class=text("source_class", "unknown"),
            query=str(data.get("query", "")),
            rank=int(number("rank", 0)),
            score=float(number("score", 0.0)),
            id=str(data.get("id", "")),
            final_url=str(data.get("final_url", "")),
            publisher=require_text(
                data.get("publisher", ""),
                ENTITY_EVIDENCE_ITEM,
                "publisher",
                entity_id=url,
                stage=stage,
            ),
            published_at=_parse_dt(data.get("published_at")),
            modified_at=_parse_dt(data.get("modified_at")),
            retrieved_at=_parse_dt(data.get("retrieved_at")),
            page_kind=str(data.get("page_kind", PAGE_KIND_OK)),
            http_status=data.get("http_status"),
            injection_suspected=bool(data.get("injection_suspected", False)),
            device_id=data.get("device_id"),
            command_id=data.get("command_id"),
            syndicated_of=data.get("syndicated_of"),
        )


# --------------------------------------------------------------- content_text
#
# 2026-09-19 incident (docs/DECISIONS.md ADR addendum after ADR-0173): the owner asked
# for research and only ever heard source TITLES. Root cause chain: the device excerpt
# was capped at 1200 chars (raised elsewhere, see DEFAULT_EXCERPT_CHARS in
# app.research.browser_gateway) and that whole budget was routinely spent on page chrome
# a real browser renders before the article - a Hacker News page's nav bar
# ("Hacker Newsnew | past | comments | ask | show | jobs | submit"), a byline breadcrumb
# ("Gaming Industry / Features / By Wes Fenlon / Published ...") - because innerText
# extraction reads top-to-bottom and truncates from the start. Nothing downstream ever
# separated "the chrome the page front-loads" from "the article a person would call the
# content", so topic relevance, the synthesis prompt and even the deterministic
# provider's quoted excerpt all scored/echoed chrome text instead of the story.
#
# This is deliberately simple and language-neutral (tr/en) rather than a real
# boilerplate-removal model: a real device-side main-content extraction (e.g. reading
# `<article>`/`<main>` specifically) is the honest long-term fix and is out of scope
# here (a device release, not a Cloud-Core-only change) - see the ADR addendum.

_CHROME_SEPARATOR_RE = re.compile(r"\s*(?:\||/|»|›|>)\s*")
_SENTENCE_PUNCT_RE = re.compile(r"[.!?…]")
_BYLINE_RE = re.compile(
    r"^(by\s+\S|published\b|updated\b|güncellendi\b|yayın(lanma)?\s*tarih"
    r"|son güncelleme|yazar\s*:)",
    re.IGNORECASE,
)

#: A nav-bar/breadcrumb line splits into several short, punctuation-free segments
#: ("Hacker Newsnew", "past", "comments", "ask", ...) — real prose practically never
#: does, even when it happens to contain a "/" or "|" character.
_CHROME_MAX_SEGMENT_CHARS = 30
_CHROME_MIN_SEGMENTS = 3

#: Below this many characters, a line with no sentence-ending punctuation reads as a
#: fragment (a nav label, a byline, a "128 points | hide | past | favorite" score line)
#: rather than a sentence — a real sentence this short would be unusually terse, and the
#: cost of dropping one such genuine one-liner is far lower than the cost of keeping a
#: whole page's worth of chrome lines this rule is actually aimed at.
_SHORT_LINE_CHARS = 80

#: `content_text` falls back to the raw excerpt when filtering would leave less than
#: this many characters — either a genuinely all-chrome page (nothing to prefer over the
#: raw text) or a single-line excerpt with no newlines to filter on at all.
_CONTENT_TEXT_MIN_SURVIVING_CHARS = 40

#: ADR-0178 (owner incident 2026-09-19): the extraction method
#: ``app.research.owner_browser_gateway`` stamps on every record it produces — defined
#: HERE (not imported from that module) to avoid a cycle, since
#: ``owner_browser_gateway`` already imports ``EvidenceRecord`` from this module. That
#: module imports this constant back rather than keeping its own duplicate literal.
EXTRACTION_METHOD_OWNER_BROWSER_OCR = "owner_browser_ocr"

#: Extraction methods whose excerpt is a screen-by-screen OCR read rather than a real
#: browser's ``innerText``: one OCR "line" is one row of pixels the device recognised
#: text on, NOT one paragraph boundary — a wrapped sentence is many consecutive short
#: lines, none carrying sentence-ending punctuation until the very last one. See
#: :func:`_reflow_ocr_lines` for why that means these excerpts need a reflow pass
#: :func:`content_text`'s ordinary per-line chrome filter does not.
_OCR_EXTRACTION_METHODS = frozenset({EXTRACTION_METHOD_OWNER_BROWSER_OCR})

#: A single OCR screen-row consisting of a short, ALL-CAPS-ish run of 1-3 words with no
#: sentence punctuation reads as a nav item, social button or section label a real site
#: draws as its own row ("HOME", "PRİNT", "TWIHER", "FREDERIC LORDON") rather than
#: prose — even though, unlike :data:`_is_nav_bar_line`, it carries no "/"/"|" separator
#: of its own to detect it by (the whole MENU was one screen row there; here each MENU
#: ITEM is its own row). Never merged into a reflowed OCR paragraph.
_OCR_MENU_LABEL_MAX_CHARS = 24
_OCR_MENU_LABEL_MAX_WORDS = 3

#: A bare date byline row an OCR read often shows on its own line ("18 SEPTEMBER 2026",
#: "18 Eylül 2026") once the surrounding "Published"/"Yayın tarihi" wording (already
#: caught by :data:`_BYLINE_RE`) has scrolled out of the same screen row.
_OCR_DATE_LINE_RE = re.compile(
    r"^\d{1,2}\s+(ocak|şubat|subat|mart|nisan|mayıs|mayis|haziran|temmuz|ağustos|agustos"
    r"|eylül|eylul|ekim|kasım|kasim|aralık|aralik"
    r"|january|february|march|april|may|june|july|august|september|october|november"
    r"|december)\s+\d{4}$",
    re.IGNORECASE,
)


def _is_nav_bar_line(stripped: str) -> bool:
    segments = [s for s in _CHROME_SEPARATOR_RE.split(stripped) if s]
    if len(segments) < _CHROME_MIN_SEGMENTS:
        return False
    return all(
        len(s) <= _CHROME_MAX_SEGMENT_CHARS and not _SENTENCE_PUNCT_RE.search(s) for s in segments
    )


def _is_ocr_menu_label_line(stripped: str) -> bool:
    if not stripped or _SENTENCE_PUNCT_RE.search(stripped):
        return False
    words = stripped.split()
    if not words or len(words) > _OCR_MENU_LABEL_MAX_WORDS:
        return False
    if len(stripped) > _OCR_MENU_LABEL_MAX_CHARS:
        return False
    letters = "".join(ch for ch in stripped if ch.isalpha())
    return bool(letters) and letters == letters.upper()


def _is_chrome_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _is_nav_bar_line(stripped):
        return True
    if _BYLINE_RE.match(stripped):
        return True
    return len(stripped) < _SHORT_LINE_CHARS and not _SENTENCE_PUNCT_RE.search(stripped)


def _reflow_ocr_lines(lines: list[str]) -> list[str]:
    """Join consecutive OCR screen-rows into paragraphs BEFORE the generic per-line
    chrome filter (:func:`_is_chrome_line`) runs on them (ADR-0178, owner incident
    2026-09-19: "araştırma hep yabancı kaynaklara gidiyor" traced back to an
    ``owner_browser_ocr`` excerpt scoring almost no topic relevance because nearly the
    whole article had been silently dropped as "chrome").

    ``content_text``'s per-line filter was written for a real browser's ``innerText``,
    where one line break already IS a paragraph boundary a person drew — a genuine nav
    bar or byline line there really is short and really does lack sentence punctuation,
    while real prose lines are whole sentences. An OCR read has no such guarantee: one
    "line" is one row of pixels the device recognised text on, so a single sentence
    wraps across many consecutive short rows, none of which carries closing punctuation
    until the very last one. Running the ordinary filter directly on OCR rows (measured
    on the production run's own OCR shape) drops essentially the entire article and
    leaves relevance to be scored on the handful of rows that happened to end in a
    period within :data:`_SHORT_LINE_CHARS`.

    So for OCR text this runs FIRST: a genuine nav-bar row (:func:`_is_nav_bar_line`,
    still multi-segment within one row when a menu is drawn as one line), a byline row,
    a bare date row (:data:`_OCR_DATE_LINE_RE`) and a menu-label row
    (:func:`_is_ocr_menu_label_line` — "HOME", "PRİNT", a lone byline NAME in caps) are
    each dropped on their own, individually, and never merged into a paragraph; a
    genuinely blank OCR row ends the current paragraph (a real visual gap); everything
    else is joined with a single space into one running paragraph. A heading
    immediately followed by body text (no blank row between them, the common case)
    lands in the SAME paragraph as that body text, which is what keeps it — the
    generic short-line filter that runs on the OUTPUT of this function then judges
    each merged paragraph, not each individual screen row.
    """
    paragraphs: list[str] = []
    buffer: list[str] = []

    def _flush() -> None:
        if buffer:
            paragraphs.append(" ".join(buffer))
            buffer.clear()

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            _flush()
            continue
        if (
            _is_nav_bar_line(stripped)
            or _BYLINE_RE.match(stripped)
            or _OCR_DATE_LINE_RE.match(stripped)
            or _is_ocr_menu_label_line(stripped)
        ):
            _flush()
            continue
        buffer.append(stripped)
    _flush()
    return paragraphs


def content_text(excerpt: str, *, extraction_method: str = "") -> str:
    """Best-effort page-chrome removal: drop nav bars, breadcrumbs, bylines and other
    short, punctuation-free lines a page front-loads before its real prose, keeping only
    the paragraphs a person would call "the article".

    Deterministic and tr/en neutral: a line is chrome when it is blank, reads as a
    nav-bar/breadcrumb (several short segments split on "|"/"/"/"»"/"›"/">"), matches a
    byline pattern ("By ...", "Published ...", "Yayın tarihi ...", ...), or is simply
    short with no sentence-ending punctuation at all. Falls back to the raw excerpt when
    filtering would leave (almost) nothing — a page that is genuinely all chrome, or an
    excerpt with no line breaks to filter on, still has to be scored/summarized on
    SOMETHING rather than an empty string.

    ``extraction_method`` (ADR-0178): when it is one of :data:`_OCR_EXTRACTION_METHODS`
    (today, only ``owner_browser_ocr`` — a page read screen-by-screen through the PC's
    own OCR, see :mod:`app.research.owner_browser_gateway`), the lines are first
    reflowed into paragraphs (:func:`_reflow_ocr_lines`) before the ordinary chrome
    filter runs on the RESULT — see that function's docstring for why an OCR excerpt
    needs this and a real browser's ``innerText`` excerpt does not. Omitted (the
    default), behaviour is byte-for-byte the same as before this parameter existed.

    Used wherever page CONTENT (not provenance, not raw storage) is needed: topic
    relevance (:func:`app.research.eligibility.topic_relevance`), the synthesis prompt's
    per-source excerpt, and the deterministic provider's quoted ``source_fact`` text. The
    STORED evidence excerpt (``EvidenceRecord.excerpt``) is never replaced by this — it
    stays the raw device excerpt for provenance/audit.
    """
    if not excerpt or not excerpt.strip():
        return excerpt
    lines = excerpt.split("\n")
    if extraction_method in _OCR_EXTRACTION_METHODS:
        candidates = _reflow_ocr_lines(lines)
    else:
        candidates = [line.strip() for line in lines]
    kept = [line for line in candidates if not _is_chrome_line(line)]
    survivor = "\n".join(kept).strip()
    if len(survivor) < _CONTENT_TEXT_MIN_SURVIVING_CHARS:
        return excerpt
    return survivor


def validate_label(label: str) -> str:
    if label not in STATEMENT_LABELS:
        raise ValueError(f"unknown statement label {label!r}; must be one of {STATEMENT_LABELS}")
    return label


@dataclass(frozen=True, slots=True)
class LabelledStatement:
    """One synthesized claim, always labelled, ideally carrying provenance."""

    text: str
    label: str
    evidence_urls: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        validate_label(self.label)
        if not self.text or not self.text.strip():
            raise ValueError("statement text must be a non-empty string")

    @property
    def has_provenance(self) -> bool:
        return bool(self.evidence_urls)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "label": self.label,
            "evidence_urls": list(self.evidence_urls),
        }


# --------------------------------------------------------------- dedup/rank


def _normalize_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/")
    # Strip common tracking params (spec §2: "strip tracking params").
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"ref", "fbclid", "gclid"}
    ]
    query = urlencode(kept)
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def _normalize_title_tokens(title: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9çğıöşü]+", title.lower()))


def _title_jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


_SOURCE_CLASS_WEIGHT: dict[str, float] = {
    "official": 1.00,
    "academic": 0.90,
    "news": 0.80,
    "technical": 0.75,
    "community": 0.55,
    "unknown": 0.40,
}


def _keyword_overlap(topic: str, text: str) -> float:
    topic_tokens = {t for t in topic.lower().split() if len(t) >= 3}
    if not topic_tokens:
        return 0.0
    lowered = text.lower()
    hits = sum(1 for t in topic_tokens if t in lowered)
    return hits / len(topic_tokens)


def _recency_bonus(
    fetched_at: datetime, *, window_start: datetime | None, window_end: datetime | None
) -> float:
    if window_start is None or window_end is None:
        return 0.0
    if window_start <= fetched_at <= window_end:
        return 0.15
    return 0.0


TITLE_DEDUP_JACCARD_THRESHOLD = 0.9


def _mark_syndication(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Near-duplicate-title detection across distinct (already URL-deduped)
    records. The lower-priority source_class copy is marked syndicated_of the
    higher-priority one; ties broken by normalized URL for determinism."""
    tokens = {r.url: _normalize_title_tokens(r.title) for r in records}
    ordered = sorted(
        records,
        key=lambda r: (
            _PUBLISHER_PRIORITY.get(r.source_class, len(SOURCE_CLASSES)),
            _normalize_url(r.url),
        ),
    )
    kept: list[EvidenceRecord] = []
    syndicated_of: dict[str, str] = {}
    for record in ordered:
        primary = next(
            (
                k
                for k in kept
                if _title_jaccard(tokens[record.url], tokens[k.url])
                >= TITLE_DEDUP_JACCARD_THRESHOLD
            ),
            None,
        )
        if primary is not None:
            syndicated_of[record.url] = primary.url
        else:
            kept.append(record)
    return [
        replace(r, syndicated_of=syndicated_of[r.url]) if r.url in syndicated_of else r
        for r in records
    ]


def dedup_and_rank(
    records: list[EvidenceRecord],
    *,
    topic: str = "",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[EvidenceRecord]:
    """Dedup by normalized URL (keep the richer excerpt), mark near-duplicate
    titles as syndicated (spec §2), then rank.

    Deterministic given deterministic inputs. Returns NEW records with
    ``rank``/``score``/``syndicated_of`` populated; input records are never
    mutated (frozen dataclass).
    """
    best: dict[str, EvidenceRecord] = {}
    for r in records:
        key = _normalize_url(r.final_url or r.url)
        current = best.get(key)
        if current is None or len(r.excerpt) > len(current.excerpt):
            best[key] = r

    deduped = _mark_syndication(list(best.values()))

    scored: list[tuple[float, EvidenceRecord]] = []
    for r in deduped:
        weight = _SOURCE_CLASS_WEIGHT.get(r.source_class, _SOURCE_CLASS_WEIGHT["unknown"])
        overlap = _keyword_overlap(topic, f"{r.title} {r.excerpt}")
        recency = _recency_bonus(r.fetched_at, window_start=window_start, window_end=window_end)
        score = round(0.6 * weight + 0.25 * overlap + recency, 4)
        scored.append((score, r))

    ordered = sorted(scored, key=lambda t: (-t[0], _normalize_url(t[1].url)))
    return [replace(r, rank=i + 1, score=score) for i, (score, r) in enumerate(ordered)]


__all__ = [
    "EXTRACTION_METHOD_OWNER_BROWSER_OCR",
    "PAGE_KINDS",
    "PAGE_KIND_AUTH_WALL",
    "PAGE_KIND_BLOCKED",
    "PAGE_KIND_CAPTCHA",
    "PAGE_KIND_EMPTY",
    "PAGE_KIND_ERROR_PAGE",
    "PAGE_KIND_OK",
    "SOURCE_CLASSES",
    "STATEMENT_LABELS",
    "STATEMENT_LABEL_MODEL_INFERENCE",
    "STATEMENT_LABEL_RECOMMENDATION",
    "STATEMENT_LABEL_SOURCE_FACT",
    "STATEMENT_LABEL_UNCERTAINTY",
    "TITLE_DEDUP_JACCARD_THRESHOLD",
    "EvidenceRecord",
    "LabelledStatement",
    "content_text",
    "dedup_and_rank",
    "validate_label",
]
