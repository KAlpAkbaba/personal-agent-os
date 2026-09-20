"""Candidate-evidence eligibility gate (M13).

Written after a real owner research run failed acceptance on 2026-09-04: the
owner asked, in Turkish, for "the most important developments about AI
agents in the last three days" and the pipeline fetched and ranked 12
"evidence" items but produced ZERO findings. The evidence that survived
included a Hugging Face model-card page published five days outside the
requested window, an arXiv paper on "Catalan's constant is irrational", a
cosmology halo-profile paper, and OpenAI pages whose extracted title was the
Turkish Cloudflare-style interstitial "Bir dakika lütfen..." ("Just a
moment..."). None of that should ever have reached ranking as candidate
evidence, and the fact that all of it did (yet nothing usable came out the
other end) means there was no gate rejecting the wrong things for the right,
inspectable reason.

This module is that gate. Every function here is pure, deterministic, and
free of I/O: no network calls, no DB, no settings, no clock reads other than
timestamps explicitly passed in by the caller. The eligibility outcome for a
candidate must be reproducible from its fields alone, so it can be unit
tested against the exact shapes that failed in production and so a caller
can explain, after the fact, exactly why a given URL was rejected.

Precedence (see :func:`evaluate_candidate`): page validity is checked first
(an interstitial has no real content to judge on), then content sufficiency
(an "empty" page has nothing to score for topic relevance), then topic
relevance, then recency/date confidence. Rejecting in this order means the
reason reported to the owner is always the most fundamental one — a
Cloudflare challenge page is reported as "interstitial", never as
"off_topic" just because its boilerplate text happens to share no words with
the topic.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from app.research.evidence import EXTRACTION_METHOD_OWNER_BROWSER_OCR, content_text
from app.research.plan import english_core_query

# ---------------------------------------------------------------------------
# Diacritic folding (Turkish-aware)
# ---------------------------------------------------------------------------

# Python's NFKD decomposition already strips most Latin diacritics via
# combining-mark removal, but Turkish dotless-ı and the soft-g do not
# decompose that way (ı has no accepted "base + combining mark" form in
# Unicode, and ğ's decomposition keeps the breve as an attached component in
# some normalization paths). Handle those explicitly before/after NFKD so
# "AJANLARIYLA", "değerlendirme", "çerez" and similar all fold to plain ASCII
# regardless of case.
_TURKISH_FOLD = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "I": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ö": "o",
        "Ö": "o",
        "ü": "u",
        "Ü": "u",
        "ç": "c",
        "Ç": "c",
        "â": "a",
        "Â": "a",
        "î": "i",
        "Î": "i",
        "û": "u",
        "Û": "u",
    }
)


def _fold(text: str) -> str:
    """Lowercase and strip diacritics (Turkish + general Latin) for matching.

    Used everywhere in this module that compares owner-facing text against a
    lexicon, so that "yapay zekâ", "YAPAY ZEKA" and "yapay zeka" are all the
    same token, and so a Turkish keyboard vs. an ASCII-only keyboard never
    changes classification.
    """
    folded = text.translate(_TURKISH_FOLD)
    # NFKD + drop combining marks handles any remaining accented Latin
    # (e.g. accented characters from non-Turkish sources) that the explicit
    # table above doesn't cover.
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return folded.lower()


def _tokens(text: str) -> list[str]:
    """Lowercase, diacritic-folded word tokens (letters/digits only)."""
    return re.findall(r"[a-z0-9]+", _fold(text))


# ---------------------------------------------------------------------------
# 1. Page validity classification
# ---------------------------------------------------------------------------

#: Default floor for :func:`topic_relevance` before a candidate may become evidence.
#: Named so the Cloud Core policy endpoint can publish the same number the gate uses.
MIN_TOPIC_RELEVANCE = 0.35

PAGE_VALIDITY_KINDS: tuple[str, ...] = (
    "normal_content",
    "consent",
    "captcha",
    "interstitial",
    "login_required",
    "access_denied",
    "empty",
    "malformed",
)

# Short, high-signal marker phrases. Matched against the TITLE (any position)
# and against only the first ~400 chars of the excerpt (the "above the fold"
# text a real challenge/consent page front-loads) -- an ordinary article that
# merely *mentions* "CAPTCHA" or "sign in" deep in its body must not trip
# these, hence the excerpt window plus the short-page corroboration required
# below for interstitial/captcha/consent.
_INTERSTITIAL_MARKERS = (
    "bir dakika lutfen",
    "bir dakika",
    "just a moment",
    "checking your browser",
    "attention required",
    "please wait",
    "dogrulaniyor",
    "verifying you are human",
    "enable javascript and cookies",
)
_CONSENT_MARKERS = (
    "before you continue",
    "cerez",
    "consent.google",
    "we value your privacy",
    "cookie policy",
    "kvkk",
    "gizlilik ve cerez",
    "accept all cookies",
)
_CAPTCHA_MARKERS = (
    "unusual traffic",
    "olagan disi trafik",
    "i'm not a robot",
    "im not a robot",
    "/sorry/",
    "recaptcha",
    "hcaptcha",
    "prove you are human",
)
_LOGIN_MARKERS = (
    "sign in to continue",
    "giris yapin",
    "log in to read",
    "please log in",
    "oturum acin",
    "create a free account to continue",
)
_ACCESS_DENIED_MARKERS = (
    "access denied",
    "erisim engellendi",
    "subscribers only",
    "paywall",
    "403 forbidden",
    "you do not have permission to access",
)

_ACCESS_DENIED_STATUSES = frozenset({403, 451})

# A real article has substantial prose. Pages under this length, when they
# also carry one of the marker phrases, are treated as the challenge/consent
# page itself rather than an article that happens to discuss one -- a genuine
# news piece about, say, a CAPTCHA vendor will run to many hundreds of
# characters of surrounding prose even if a sentence near the top uses the
# phrase "unusual traffic".
_SHORT_PAGE_CHARS = 400

# Below this many characters of excerpt, there isn't enough real prose to
# call the page "normal_content" -- it's "empty" for evidence purposes even
# if technically non-blank (e.g. a nav-only shell, a single heading). Chosen
# to comfortably exceed boilerplate ("404 Not Found", a bare cookie banner)
# while staying below the length of even a terse real paragraph.
_EMPTY_CONTENT_CHARS = 200


def _contains_marker(haystack_folded: str, markers: tuple[str, ...]) -> bool:
    return any(marker in haystack_folded for marker in markers)


# Strips HTML/XML-ish tags so a markup-only excerpt (e.g. an empty
# "<html><body></body></html>" shell scraped before rendering) can be told
# apart from real prose that merely happens to be wrapped in tags.
_TAG_RE = re.compile(r"<[^>]+>")


def classify_page_validity(
    *,
    title: str,
    excerpt: str,
    http_status: int | None = None,
    url: str = "",
) -> str:
    """Classify what kind of page a fetched candidate actually is.

    Judgement: title and the lead of the excerpt are weighted far more than
    the rest of the body, because interstitial/consent/captcha/login pages
    put their tell-tale phrase at the very top (often as the whole title,
    e.g. "Bir dakika lütfen..." / "Just a moment..."), while an ordinary
    article merely mentioning one of those phrases does so deep in its prose.
    For the four "blocked page" kinds (interstitial, consent, captcha,
    login_required) we additionally require the page to be short
    (< :data:`_SHORT_PAGE_CHARS` chars of excerpt) -- a long article that
    happens to open with a sentence like "Just a moment before we dive into
    the new agent framework..." should not be misclassified, and challenge
    pages are, in reality, always short.

    Order of checks (first match wins, matching how a real fetch pipeline
    would actually encounter these): malformed types -> http_status-driven
    access_denied -> title/excerpt marker classification -> empty.
    """
    if not isinstance(title, str) or not isinstance(excerpt, str):
        return "malformed"

    title_folded = _fold(title)
    lead_folded = _fold(excerpt[: _SHORT_PAGE_CHARS * 2])
    full_folded = _fold(excerpt)
    is_short = len(excerpt.strip()) < _SHORT_PAGE_CHARS

    if http_status is not None and http_status in _ACCESS_DENIED_STATUSES:
        return "access_denied"

    # Markup/boilerplate-only excerpt (no real prose once tags are
    # stripped -- e.g. a raw "<html><body></body></html>" shell scraped
    # before the page rendered) is malformed -- there is no page to judge.
    if excerpt.strip():
        text_only = _TAG_RE.sub(" ", excerpt)
        if not re.search(r"[a-zA-ZÀ-ɏ]", text_only):
            return "malformed"

    combined_title_and_lead = f"{title_folded} {lead_folded}"

    if _contains_marker(title_folded, _INTERSTITIAL_MARKERS) or (
        is_short and _contains_marker(combined_title_and_lead, _INTERSTITIAL_MARKERS)
    ):
        return "interstitial"

    if _contains_marker(title_folded, _CAPTCHA_MARKERS) or (
        is_short and _contains_marker(combined_title_and_lead, _CAPTCHA_MARKERS)
    ):
        return "captcha"

    if _contains_marker(title_folded, _CONSENT_MARKERS) or (
        is_short and _contains_marker(combined_title_and_lead, _CONSENT_MARKERS)
    ):
        return "consent"

    if _contains_marker(title_folded, _LOGIN_MARKERS) or (
        is_short and _contains_marker(combined_title_and_lead, _LOGIN_MARKERS)
    ):
        return "login_required"

    # access_denied text markers are meaningful anywhere in the excerpt (a
    # paywall notice is often the ONLY content on an otherwise normal-length
    # page shell), so no short-page gate here.
    if _contains_marker(title_folded, _ACCESS_DENIED_MARKERS) or _contains_marker(
        full_folded, _ACCESS_DENIED_MARKERS
    ):
        return "access_denied"

    if len(excerpt.strip()) < _EMPTY_CONTENT_CHARS:
        return "empty"

    return "normal_content"


# ---------------------------------------------------------------------------
# 2. Topic relevance
# ---------------------------------------------------------------------------

# Built-in AI-agent lexicon (Turkish + English), diacritic-folded once at
# import time. This exists because the topic string the owner speaks
# ("yapay zeka ajanlarıyla ilgili gelişmeler") is short and the candidate
# titles/excerpts use varied phrasing ("agentic", "LLM agent", "copilot")
# that pure token-overlap with the topic string alone would miss entirely --
# that gap is exactly how an unrelated arXiv math paper and a genuine AI-agent
# story could otherwise land at similar low scores.
#: The domain signal, as CONCEPTS rather than a flat phrase list.
#:
#: A flat list double-counts: "ajanlariyla" contains "ajan", "ajanlar" and "ajanlari", and
#: "yapay zeka" and "yapay zekâ" fold to the same string, so one Turkish word could score as
#: four independent hits while an English announcement scored none. Grouping the spellings of
#: one idea and counting each idea once makes the score mean what its name says.
#:
#: Weight separates "this is about agents" from "this mentions AI". A phone launch that says
#: "yapay zeka asistani" once is not coverage of AI agents; a model card that says "agentic",
#: "tool calling" and "task planning" is, even when its headline is only a product name.
_AGENT_CONCEPTS: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("agent", 1.0, ("ajan", "agent")),
    ("agentic", 1.0, ("agentic", "ajansal")),
    ("autonomous", 1.0, ("autonomous agent", "otonom ajan", "otonom")),
    ("tool_use", 1.0, ("tool use", "tool calling", "arac kullanimi", "arac cagirma")),
    ("mcp", 1.0, ("mcp", "model context protocol")),
    ("multi_agent", 1.0, ("multi-agent", "multi agent", "cok ajanli")),
    (
        "orchestration",
        1.0,
        ("orchestrator", "orkestrasyon", "task planning", "gorev planlamasi", "planlayici"),
    ),
    ("assistant", 0.5, ("copilot", "assistant", "asistan", "chatbot", "sohbet botu")),
    ("generic_ai", 0.5, ("yapay zeka", "artificial intelligence", "large language model")),
)


def _concept_weight(text_folded: str) -> float:
    """Total weight of the DISTINCT domain concepts present in this text."""
    total = 0.0
    for _name, weight, patterns in _AGENT_CONCEPTS:
        if any(_fold(pattern) in text_folded for pattern in patterns):
            total += weight
    return total


# Generic English/Turkish stop-words excluded from topic-token overlap so
# that shared function words (e.g. "the", "ile", "ve") don't inflate the
# score of an unrelated candidate that happens to share only glue words with
# the topic phrase.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "in",
        "on",
        "for",
        "to",
        "about",
        "with",
        "is",
        "are",
        "last",
        "days",
        "day",
        "top",
        "most",
        "important",
        "developments",
        "news",
        "ve",
        "ile",
        "icin",
        "hakkinda",
        "son",
        "gun",
        "gunde",
        "gundeki",
        "en",
        "onemli",
        "gelismeler",
        "gelisme",
        "ilgili",
        # "news" (above) had no Turkish counterpart here (2026-09-19 incident):
        # "haberler"/"haber" was still scored as a topic-specific token, quietly
        # diluting the token-overlap signal for the owner's routine "... ile ilgili
        # son haberler" phrasing (a filler word counted as if it were meaningful).
        "haberler",
        "haber",
    }
)


def _cross_language_topic_tokens(topic: str) -> frozenset[str]:
    """Cross-language augmentation for the token-overlap signal below (2026-09-19
    incident, docs/DECISIONS.md ADR addendum after ADR-0173).

    The owner phrases the topic in Turkish; a real, on-topic candidate is very often an
    English-language page that never uses the Turkish wording at all
    (topic "Yapay zeka ile ilgili son haberler" / an English article about AI coding
    tools). ``app.research.plan.english_core_query`` already carries a deterministic
    Turkish -> English term map built for exactly this gap (discovery's own English
    query expansion) — reusing it here, instead of maintaining a second translation
    table, is the least invasive fix: it means "yapay zeka" contributes the token "ai"
    to the overlap check without duplicating the map that already turns "yapay zeka"
    into "AI" for discovery.

    The general token filter below drops anything <= 2 characters (noise-word
    suppression); a short acronym ``english_core_query`` rendered in upper case (e.g.
    "AI") is trusted to be exactly that — a high-signal term, not noise — and is kept
    regardless of length. ``news``-type filler words are still dropped via the same
    ``_STOPWORDS`` set the Turkish tokens are filtered through.

    Deliberately returns ONLY what the term map itself produces (never widened with a
    hand-picked vocabulary): every token returned here also grows the token-overlap
    normalization denominator below, so padding this set with many extra words would
    make an article that mentions only ONE of them score WORSE, not better (measured
    while building :data:`_AI_SUBJECT_SYNONYMS` — see :func:`_ai_subject_bonus` for
    where that broader vocabulary is actually used instead).
    """
    translated = english_core_query(topic)
    if not translated:
        return frozenset()
    tokens: set[str] = set()
    for word in translated.split():
        is_acronym = word.isupper() and word.isalpha()
        cleaned = re.sub(r"[^a-z0-9]", "", _fold(word))
        if not cleaned or cleaned in _STOPWORDS:
            continue
        if len(cleaned) > 2 or is_acronym:
            tokens.add(cleaned)
    return frozenset(tokens)


#: ADR-0178 (owner incident 2026-09-19, item C): company/product names the pipeline's
#: OWN query planner already treats as part of the AI domain (mirrors
#: ``app.research.plan._AGENT_ENTITY_QUERIES``'s entity list) plus a small generic-AI
#: vocabulary. Used by :func:`_ai_subject_bonus`, ONLY when the topic's own subject
#: already reduced to bare "AI" (:func:`english_core_query` mapped it there) — that is
#: what lets a genuinely on-topic English page naming "OpenAI", "Anthropic" or "LLM
#: benchmarks", but never spelling out "yapay zeka"/"artificial intelligence" verbatim,
#: still pick up SOME credit. A small, capped, ADDITIVE bonus rather than more
#: token-overlap tokens on purpose: those tokens also grow the overlap score's own
#: normalization denominator (:func:`_cross_language_topic_tokens`'s own docstring),
#: so a large vocabulary added there would make a typical single-topic article score
#: WORSE by diluting the overlap fraction, not better — confirmed while building this
#: fix: the existing (passing) test
#: ``test_english_ai_coding_article_clears_the_relevance_floor_for_a_turkish_ai_topic``
#: regressed the moment this vocabulary was folded into the token set instead of kept
#: as its own bonus term.
_AI_SUBJECT_SYNONYMS: frozenset[str] = frozenset(
    {
        "ai",
        "artificial",
        "intelligence",
        "llm",
        "llms",
        "genai",
        "openai",
        "anthropic",
        "google",
        "gemini",
        "microsoft",
        "copilot",
        "meta",
        "llama",
        "chatgpt",
        "gpt",
    }
)

#: Per-hit and total cap for :func:`_ai_subject_bonus` — small enough that it can never
#: by itself carry an unrelated page across :data:`MIN_TOPIC_RELEVANCE` (it only adds
#: to a candidate that has ALREADY cleared page-validity/content checks and picked up
#: some base signal), large enough to matter for a genuinely AI-focused page that named
#: only one or two of these terms.
_AI_SUBJECT_BONUS_PER_HIT = 0.05
_AI_SUBJECT_BONUS_MAX = 0.15


def _ai_subject_bonus(
    *, cross_language_tokens: frozenset[str], title_tokens: set[str], body_tokens: set[str]
) -> float:
    """Additive credit for naming a known AI entity/vocabulary word, gated on the
    topic's own subject already being generic "AI" (see :data:`_AI_SUBJECT_SYNONYMS`).
    """
    if "ai" not in cross_language_tokens:
        return 0.0
    hits = (title_tokens | body_tokens) & _AI_SUBJECT_SYNONYMS
    if not hits:
        return 0.0
    return min(_AI_SUBJECT_BONUS_MAX, _AI_SUBJECT_BONUS_PER_HIT * len(hits))


#: ADR-0178 (owner incident 2026-09-19, item B): a single, narrow OCR misreading
#: table. "Al" (capital A + lowercase L) and "AI" (capital A + capital I) are visually
#: identical in many display fonts a screen-OCR pass has to read from a live browser
#: window — production example: "THE Al DAILY BRIEF" for "THE AI DAILY BRIEF". Applied
#: ONLY to ``owner_browser_ocr`` text (never to a real browser's ``innerText``, where
#: "Al" is far more likely to be a genuine name, e.g. "Al Gore") and deliberately
#: narrow: it does not attempt to recover garbage with no single likely reading (e.g.
#: "NEVİS" for "NEWS" — not corrected, per the incident report: "not recoverable, do
#: not over-reach").
_OCR_AL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])Al(?![A-Za-z0-9])")


def _fold_ocr_confusables(text: str, *, extraction_method: str = "") -> str:
    if extraction_method != EXTRACTION_METHOD_OWNER_BROWSER_OCR:
        return text
    return _OCR_AL_TOKEN_RE.sub("AI", text)


def topic_relevance(
    *,
    topic: str,
    title: str,
    excerpt: str,
    url: str = "",
    entities: tuple[str, ...] = (),
    extraction_method: str = "",
) -> float:
    """Score how relevant a candidate is to the topic, 0.0 (unrelated) .. 1.0.

    Combines two signals:

    1. Domain concepts -- how many distinct built-in AI-agent concepts
       (Turkish + English, see :data:`_AGENT_CONCEPTS`) appear in the
       title+excerpt. This is what correctly separates "OpenAI, Agent
       Builder'ı duyurdu" (multiple hits: "ajan"/"agent") from "Catalan's
       constant is irrational" or a cosmology halo-profile abstract (zero
       hits) -- topic-token overlap alone can't do this because the owner's
       spoken topic phrase doesn't literally contain every way an article
       might phrase "AI agent".
    2. Topic-token overlap -- non-stopword tokens shared between the `topic`
       string itself (PLUS its cross-language augmentation, see
       :func:`_cross_language_topic_tokens`) and the title+excerpt, so a query
       about a narrower sub-topic still gets credit for matching its own
       specific wording, in either language.

    Title matches count double: a candidate whose TITLE is on-topic is much
    more likely to actually be about the topic than one where a keyword
    shows up once in a long excerpt. ``excerpt`` is reduced to its own
    :func:`app.research.evidence.content_text` before scoring -- a page's nav
    bar/byline chrome must not itself decide relevance (2026-09-19 incident: a
    stray "AI" in a site's own navigation menu must not make an unrelated page
    score as on-topic, and real article prose must not be diluted by chrome
    sharing none of the topic's words).

    ``extraction_method`` (ADR-0178, ``EvidenceRecord.extraction_method``) is passed
    straight through to :func:`app.research.evidence.content_text` (an
    ``owner_browser_ocr`` excerpt gets its OCR-aware paragraph reflow, see that
    function) and gates a small, narrow OCR-misreading fold
    (:func:`_fold_ocr_confusables`) applied to the title and content before matching.

    The two signals are combined as ``0.65 * lexicon_score + 0.35 *
    token_overlap_score``, each independently capped at 1.0 before blending,
    and the AI-agent lexicon is always included in scoring (not just when
    the topic literally says "AI agents") because in this project the
    domain IS the owner's standing research subject, not one topic among
    many -- callers researching a different domain would need a different
    scorer, not a parameter to this one.
    """
    if not isinstance(title, str) or not isinstance(excerpt, str) or not isinstance(topic, str):
        return 0.0

    content_excerpt = content_text(excerpt, extraction_method=extraction_method)
    title_corrected = _fold_ocr_confusables(title, extraction_method=extraction_method)
    content_corrected = _fold_ocr_confusables(content_excerpt, extraction_method=extraction_method)
    title_folded = _fold(title_corrected)
    excerpt_folded = _fold(content_corrected)
    combined = f"{title_folded} {excerpt_folded}"

    lexicon_hits_title = _concept_weight(title_folded)
    lexicon_hits_body = _concept_weight(excerpt_folded)
    # Distinct-term credit, weighted so a title hit is worth more, capped so
    # a handful of hits already saturates the signal (a story doesn't need
    # to repeat "agent" ten times to be confidently about agents).
    lexicon_raw = (2 * lexicon_hits_title) + lexicon_hits_body
    # Saturates at three weighted points: two DISTINCT domain terms in the body is already a
    # confident signal, while a single passing mention is not. Calibrated against the live run
    # of 2026-09-04, where an official post announcing an agentic model family scored 0.325 -
    # below the floor - purely because its headline was a product name ("IBM Granite 4.2")
    # rather than the category. Announcements normally read that way, so a body-only signal
    # has to be able to carry a page on its own.
    lexicon_score = min(1.0, lexicon_raw / 3.0)

    cross_language_tokens = _cross_language_topic_tokens(topic)
    topic_tokens = {tok for tok in _tokens(topic) if tok not in _STOPWORDS and len(tok) > 2}
    topic_tokens |= cross_language_tokens
    title_tokens = set(_tokens(title_corrected))
    body_tokens = set(_tokens(content_corrected))
    if topic_tokens:
        title_overlap = len(topic_tokens & title_tokens)
        body_overlap = len(topic_tokens & body_tokens)
        overlap_raw = (2 * title_overlap) + body_overlap
        token_score = min(1.0, overlap_raw / max(2.0, 2 * len(topic_tokens)))
    else:
        token_score = 0.0

    entity_bonus = 0.0
    if entities:
        entity_folded = {_fold(e) for e in entities}
        if entity_folded & set(_tokens(combined)):
            entity_bonus = 0.05

    ai_subject_bonus = _ai_subject_bonus(
        cross_language_tokens=cross_language_tokens,
        title_tokens=title_tokens,
        body_tokens=body_tokens,
    )

    score = (0.65 * lexicon_score) + (0.35 * token_score) + entity_bonus + ai_subject_bonus
    return round(min(1.0, max(0.0, score)), 4)


# ---------------------------------------------------------------------------
# 3. Publication date confidence
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_YEAR_ONLY_RE = re.compile(r"^\d{4}$")

# Turkish + English relative-date hints ("N gün önce" / "N days ago" /
# "yesterday" / "dün" / "today" / "bugün"). Matched against
# `published_hint`, which callers pass verbatim from page text (e.g. a
# byline reading "3 gün önce") -- this is a "medium" confidence signal
# because it's owner/human-readable but not machine-precise (no exact time
# of day, and "gün önce" is relative to an unstated retrieval moment).
#: Every unit the SEARCH side writes into a hint (``search_engines._PUBLISHED_HINT_RE``),
#: in days. The two sides used to disagree: the extractor produced "17 saat önce" and this
#: pattern understood only DAYS, so a page published today was judged undated and rejected
#: (production 2026-09-20: three on-topic pages, relevance 0.45-0.70, all `date_uncertain`).
_HINT_UNITS_IN_DAYS: Final[dict[str, float]] = {
    "saniye": 0.0,
    "second": 0.0,
    "seconds": 0.0,
    "dakika": 0.0,
    "minute": 0.0,
    "minutes": 0.0,
    "saat": 0.0,
    "hour": 0.0,
    "hours": 0.0,
    "gun": 1.0,
    "gün": 1.0,
    "day": 1.0,
    "days": 1.0,
    "hafta": 7.0,
    "week": 7.0,
    "weeks": 7.0,
    "ay": 30.0,
    "month": 30.0,
    "months": 30.0,
    "yil": 365.0,
    "yıl": 365.0,
    "year": 365.0,
    "years": 365.0,
}
_RELATIVE_HINT_RE = re.compile(
    r"\b(?P<count>\d+)\s*(?P<unit>"
    + "|".join(sorted(_HINT_UNITS_IN_DAYS, key=len, reverse=True))
    + r")\s*(once|önce|ago)\b"
    r"|\b(?P<yesterday>dun|dün|yesterday)\b"
    r"|\b(?P<today>bugun|bugün|today)\b",
    re.IGNORECASE,
)


def resolve_relative_hint(hint: str, retrieved_at: str | None) -> str | None:
    """ "17 saat önce" read at a known moment -> that moment's date, as ISO.

    A hint used to grade confidence and return NO date, which made it useless: the recency
    window needs a date, so a page whose only date signal was a relative phrase came back
    ``date_uncertain`` and was rejected however on-topic it was. The fetch time IS known, so
    the date is computed from it - still ``medium`` confidence, because the phrase is
    human-readable and not machine-precise. Sub-day units ("saat", "dakika") land on the
    retrieval date itself, which is what they mean.
    """
    match = _RELATIVE_HINT_RE.search(hint or "")
    if match is None:
        return None
    moment = _parse_iso(retrieved_at or "")
    if moment is None:
        return None
    if match.group("today"):
        days = 0.0
    elif match.group("yesterday"):
        days = 1.0
    else:
        unit = (match.group("unit") or "").lower()
        try:
            count = int(match.group("count") or 0)
        except ValueError:
            return None
        days = count * _HINT_UNITS_IN_DAYS.get(unit, 0.0)
    return (moment.date() - timedelta(days=round(days))).isoformat()


def _parse_iso(value: str) -> datetime | None:
    try:
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        return datetime.fromisoformat(candidate)
    except (ValueError, TypeError):
        return None


def publication_date_confidence(
    *,
    published_at: str | None,
    published_hint: str | None,
    retrieved_at: str | None,
    body_dates: tuple[str, ...] = (),
) -> tuple[str, str | None]:
    """Grade confidence in the candidate's publication date.

    Judgement, in order:

    - "high": ``published_at`` parses as an explicit ISO 8601 date/datetime.
      This is the only source ever returned as the date -- machine-supplied
      metadata beats every inferred signal.
    - "medium": no explicit ``published_at``, but ``published_hint`` matches
      a relative-date phrase ("3 gün önce" / "3 days ago" / "dün" /
      "bugün"), OR at least one entry in ``body_dates`` parses as ISO and
      (when there is more than one) they agree with each other -- self
      -consistent body evidence is trusted more than a single unverified
      scrape.
    - "low": a year-only date, or body_dates that parse but disagree with
      each other (ambiguous -- we know *a* date was found but not *the*
      date).
    - "none": nothing usable at all.

    ``retrieved_at`` (when the page was fetched) is NEVER returned as the
    publication date, and does not by itself raise confidence above "none" --
    this is the exact defect from the 2026-09-04 incident: pages were being
    treated as fresh because they were fetched recently, not because they
    were published recently.
    """
    if published_at:
        parsed = _parse_iso(published_at)
        if parsed is not None:
            return "high", parsed.date().isoformat()
        if _YEAR_ONLY_RE.match(published_at.strip()):
            return "low", published_at.strip()

    if published_hint and _RELATIVE_HINT_RE.search(published_hint):
        # The hint is resolved against the moment the page was FETCHED (which is the moment
        # the phrase was written for). Without a retrieval time there is nothing to resolve
        # it against, and the hint grades confidence only, as it always did.
        return "medium", resolve_relative_hint(published_hint, retrieved_at)

    parsed_body_dates = [d.date() for raw in body_dates if (d := _parse_iso(raw)) is not None]
    if parsed_body_dates:
        distinct = set(parsed_body_dates)
        if len(distinct) == 1:
            return "medium", distinct.pop().isoformat()
        return "low", None

    return "none", None


# ---------------------------------------------------------------------------
# 4. Recency verdict
# ---------------------------------------------------------------------------


def recency_verdict(
    *,
    published_at: str | None,
    event_date: str | None,
    window_start: str,
    window_end: str,
    confidence: str,
) -> str:
    """Decide whether a candidate falls inside the requested recency window.

    A candidate is "in_window" when EITHER its publication date OR an
    explicit event date (e.g. a product-launch date mentioned in the body,
    which may predate or postdate when an article about it was published)
    falls within ``[window_start, window_end]`` inclusive.

    ``confidence == "none"`` (or both dates missing/unparseable) always
    yields "date_uncertain", never "in_window" -- this is the direct fix for
    the incident: a page with no real date must never be silently treated as
    fresh just because it was fetched today. "date_uncertain" is also
    returned for a "low" confidence date that doesn't fall in the window
    (a year-only date could plausibly be in-window or not; without a
    day-level date we can't assert "outside" either) unless the low
    -confidence date object itself parses to a year clearly outside the
    window, in which case "outside_recency_window" is safe to assert.
    """
    start = _parse_iso(window_start)
    end = _parse_iso(window_end)
    if start is None or end is None:
        return "date_uncertain"

    def _in_window(date_str: str | None) -> bool | None:
        if not date_str:
            return None
        parsed = _parse_iso(date_str)
        if parsed is None:
            if _YEAR_ONLY_RE.match(date_str.strip()):
                year = int(date_str.strip())
                if year < start.year or year > end.year:
                    return False
                return None  # ambiguous -- year overlaps window's year(s)
            return None
        # Compare at date granularity: sources rarely carry meaningful
        # sub-day precision and the window itself is day-granular ("son uc
        # gun"), so mixing naive/aware datetimes (a date-only ISO string has
        # no tzinfo; a full timestamp might) is sidestepped entirely by
        # never comparing time-of-day.
        return start.date() <= parsed.date() <= end.date()

    if confidence == "none":
        return "date_uncertain"

    pub_result = _in_window(published_at)
    event_result = _in_window(event_date)

    if pub_result is True or event_result is True:
        return "in_window"

    if pub_result is False and event_result in (False, None):
        return "outside_recency_window"
    if event_result is False and pub_result in (False, None):
        return "outside_recency_window"

    return "date_uncertain"


# ---------------------------------------------------------------------------
# 5. Duplicate-event key
# ---------------------------------------------------------------------------

_DEDUP_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "in",
        "on",
        "for",
        "to",
        "with",
        "is",
        "are",
        "at",
        "by",
        "its",
        "new",
        "announces",
        "announced",
        "launches",
        "launched",
        "duyurdu",
        "duyuruldu",
        "yayinladi",
        "ile",
        "icin",
        "ve",
        "bir",
    }
)

# Common publisher/site suffixes that titles are often decorated with, e.g.
# "OpenAI launches Agent Builder | TechCrunch" or "... - Reuters". Stripped
# so the same story from two outlets doesn't get two different keys purely
# because of the trailing masthead.
_PUBLISHER_SUFFIX_RE = re.compile(r"\s*[|\-–—]\s*[^|\-–—]{2,40}$")


def _normalize_title_for_dedup(title: str, publisher: str) -> list[str]:
    working = title
    if publisher:
        pub_folded = _fold(publisher)
        # Strip an exact trailing "... - Publisher" / "... | Publisher".
        stripped = _PUBLISHER_SUFFIX_RE.sub("", working)
        if _fold(stripped) != _fold(working) and pub_folded in _fold(working[len(stripped) :]):
            working = stripped
    else:
        working = _PUBLISHER_SUFFIX_RE.sub("", working)

    tokens = [tok for tok in _tokens(working) if tok not in _DEDUP_STOPWORDS and len(tok) > 1]
    return tokens


def duplicate_event_key(*, title: str, url: str, publisher: str = "") -> str:
    """Build a deterministic key identifying "the same event" across sources.

    Normalizes the title (diacritic-folded, lowercased, publisher-suffix and
    stop-words stripped, punctuation removed) preserving word order, then
    reduces it to a small set of 2-word shingles. Two titles collide
    (produce the same key) when they share enough shingles -- this is what
    lets "OpenAI launches Agent Builder" and "OpenAI, Agent Builder'ı
    duyurdu" be recognised as the same underlying event even though neither
    title is a substring of the other and the word order/language differ.

    The key itself is the sorted, joined set of shingles (falling back to
    the sorted token set for very short titles that don't yield 2 distinct
    tokens) -- deterministic and independent of ``url``, which is accepted
    for API symmetry with the rest of this module but intentionally not
    part of the key (the same event is often covered at different URLs).
    """
    tokens = _normalize_title_for_dedup(title, publisher)
    if len(tokens) < 2:
        return "|".join(sorted(tokens))
    shingles = {f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)}
    return "|".join(sorted(shingles))


def is_near_duplicate(a_key: str, b_key: str) -> bool:
    """True when two :func:`duplicate_event_key` outputs likely cover one event.

    Exact key equality always counts. Otherwise, compares shingle sets with
    Jaccard similarity and requires >= 0.34 overlap -- chosen so that two
    independently-worded headlines about the same launch (which typically
    share the entity name and product name shingles but diverge everywhere
    else) still match, while two different stories that merely share one
    generic shingle (e.g. both mention "the company") do not.
    """
    if a_key == b_key:
        return True
    a_shingles = set(a_key.split("|")) if a_key else set()
    b_shingles = set(b_key.split("|")) if b_key else set()
    if not a_shingles or not b_shingles:
        return False
    intersection = a_shingles & b_shingles
    if not intersection:
        return False
    union = a_shingles | b_shingles
    jaccard = len(intersection) / len(union)
    return jaccard >= 0.34


# ---------------------------------------------------------------------------
# 6. Rejection vocabulary + verdict
# ---------------------------------------------------------------------------

REJECTION_REASONS: frozenset[str] = frozenset(
    {
        "off_topic",
        "outside_recency_window",
        "date_uncertain",
        "interstitial",
        "duplicate_event",
        "insufficient_content",
    }
)


@dataclass(frozen=True, slots=True)
class EligibilityVerdict:
    """The full, inspectable outcome of evaluating one research candidate."""

    eligible: bool
    reason: str | None
    topic_relevance: float
    recency: str
    page_validity: str
    publication_date: str | None
    date_confidence: str

    def as_dict(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "topic_relevance": self.topic_relevance,
            "recency": self.recency,
            "page_validity": self.page_validity,
            "publication_date": self.publication_date,
            "date_confidence": self.date_confidence,
        }


# Page-validity kinds that map directly to the "interstitial" rejection
# reason -- from the owner's perspective a consent wall, CAPTCHA, login wall
# or generic bot-check interstitial are all the same failure mode: the fetch
# never reached real content. access_denied is folded in for the same
# reason; only "empty"/"malformed" are handled separately (as
# insufficient_content) since those are a content problem, not a blocking
# -page problem.
_BLOCKED_PAGE_KINDS = frozenset(
    {"interstitial", "consent", "captcha", "login_required", "access_denied"}
)


def evaluate_candidate(
    *,
    title: str,
    excerpt: str,
    url: str = "",
    topic: str = "",
    http_status: int | None = None,
    published_at: str | None = None,
    published_hint: str | None = None,
    retrieved_at: str | None = None,
    body_dates: tuple[str, ...] = (),
    event_date: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    publisher: str = "",
    entities: tuple[str, ...] = (),
    min_topic_relevance: float = MIN_TOPIC_RELEVANCE,
    existing_event_keys: tuple[str, ...] = (),
    extraction_method: str = "",
) -> EligibilityVerdict:
    """Run a candidate through the full eligibility gate and explain the result.

    Precedence (a candidate is rejected for the FIRST reason that applies,
    even if later checks would also fail -- so the reported reason is always
    the most fundamental problem):

    1. Page validity -- any blocked-page kind (interstitial/consent/
       captcha/login_required/access_denied) rejects as "interstitial"
       immediately: there is no real content underneath to evaluate topic or
       date on, so checking those would just produce a misleading second
       reason.
    2. Content sufficiency -- "empty" or "malformed" pages reject as
       "insufficient_content" for the same underlying reason (nothing to
       judge), kept as a distinct code from "interstitial" because the
       remediation differs (empty page: maybe re-fetch/render JS; blocked
       page: needs a different fetch strategy entirely).
    3. Topic relevance -- below ``min_topic_relevance`` (default 0.35,
       chosen because it sits clearly above the near-zero score real
       off-topic academic abstracts score in :func:`topic_relevance`
       (typically < 0.1) and clearly below the score a genuine but loosely
       -worded AI-agent story scores (typically >= 0.5), giving margin on
       both sides without being so strict that a story using only one
       lexicon term is discarded) rejects as "off_topic".
    4. Duplicate event -- if ``existing_event_keys`` contains a near
       -duplicate of this candidate's key, rejects as "duplicate_event".
       Checked before recency so a stale duplicate of an already-accepted
       fresh story is reported as a duplicate, not as an independent
       recency failure.
    5. Recency -- ``recency_verdict`` output is surfaced as-is:
       "outside_recency_window" or "date_uncertain" both reject (the latter
       is exactly the 2026-09-04 incident's missing check -- a candidate
       with no trustworthy date must never slip through as eligible).

    A candidate that survives all five checks is eligible.
    """
    page_validity = classify_page_validity(
        title=title, excerpt=excerpt, http_status=http_status, url=url
    )
    if page_validity in _BLOCKED_PAGE_KINDS:
        return EligibilityVerdict(
            eligible=False,
            reason="interstitial",
            topic_relevance=0.0,
            recency="date_uncertain",
            page_validity=page_validity,
            publication_date=None,
            date_confidence="none",
        )

    if page_validity in ("empty", "malformed"):
        return EligibilityVerdict(
            eligible=False,
            reason="insufficient_content",
            topic_relevance=0.0,
            recency="date_uncertain",
            page_validity=page_validity,
            publication_date=None,
            date_confidence="none",
        )

    relevance = topic_relevance(
        topic=topic,
        title=title,
        excerpt=excerpt,
        url=url,
        entities=entities,
        extraction_method=extraction_method,
    )

    confidence, publication_date = publication_date_confidence(
        published_at=published_at,
        published_hint=published_hint,
        retrieved_at=retrieved_at,
        body_dates=body_dates,
    )

    if relevance < min_topic_relevance:
        return EligibilityVerdict(
            eligible=False,
            reason="off_topic",
            topic_relevance=relevance,
            recency="date_uncertain",
            page_validity=page_validity,
            publication_date=publication_date,
            date_confidence=confidence,
        )

    if existing_event_keys:
        candidate_key = duplicate_event_key(title=title, url=url, publisher=publisher)
        if any(is_near_duplicate(candidate_key, other) for other in existing_event_keys):
            return EligibilityVerdict(
                eligible=False,
                reason="duplicate_event",
                topic_relevance=relevance,
                recency="date_uncertain",
                page_validity=page_validity,
                publication_date=publication_date,
                date_confidence=confidence,
            )

    if window_start is not None and window_end is not None:
        recency = recency_verdict(
            published_at=published_at or publication_date,
            event_date=event_date,
            window_start=window_start,
            window_end=window_end,
            confidence=confidence,
        )
    else:
        recency = "date_uncertain"

    if recency == "outside_recency_window":
        return EligibilityVerdict(
            eligible=False,
            reason="outside_recency_window",
            topic_relevance=relevance,
            recency=recency,
            page_validity=page_validity,
            publication_date=publication_date,
            date_confidence=confidence,
        )
    if recency == "date_uncertain":
        return EligibilityVerdict(
            eligible=False,
            reason="date_uncertain",
            topic_relevance=relevance,
            recency=recency,
            page_validity=page_validity,
            publication_date=publication_date,
            date_confidence=confidence,
        )

    return EligibilityVerdict(
        eligible=True,
        reason=None,
        topic_relevance=relevance,
        recency=recency,
        page_validity=page_validity,
        publication_date=publication_date,
        date_confidence=confidence,
    )
