"""M13 research plan generation: topic -> queries, source classes, recency.

Pure and deterministic: same ``(topic, now)`` always yields the same
:class:`ResearchPlan`. This is the first stage of the M13 pipeline
(app.research.browser_provider.BrowserResearchProvider); it never touches a
browser, the network or a database.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from app.research.dates import RecencyWindow, default_window, parse_recency_window

DEFAULT_RECENCY_DAYS = 3
DEFAULT_SOURCE_CLASSES: tuple[str, ...] = (
    "news",
    "official",
    "technical",
    "academic",
    "community",
)
DEFAULT_MAX_SOURCES_PER_QUERY = 3

# The owner phrases the topic in Turkish; the primary sources for most technology topics
# publish in English, and the first live run showed that Turkish-only queries fill the
# fetch budget with Turkish news listing pages. Two deterministic expansions follow:
# an English core query built from a small Turkish -> English term map (only when at
# least one mapped term appears; nothing is guessed), and entity sub-queries for the
# AI-agents domain (spec §1: OpenAI / Anthropic / Google-Gemini / Microsoft-Copilot /
# open-source frameworks / launches-papers-incidents). Both are data, extendable
# without touching the pipeline.
_TERM_MAP: tuple[tuple[str, str], ...] = (
    ("yapay zekâ ajanları", "AI agents"),
    ("yapay zeka ajanları", "AI agents"),
    ("yapay zekâ ajanlarıyla", "AI agents"),
    ("yapay zeka ajanlarıyla", "AI agents"),
    ("yapay zekâ ajanı", "AI agent"),
    ("yapay zeka ajanı", "AI agent"),
    ("ajan", "agent"),
    ("yapay zekâ", "AI"),
    ("yapay zeka", "AI"),
    ("büyük dil modeli", "large language model"),
    ("büyük dil modelleri", "large language models"),
    ("dil modeli", "language model"),
    ("siber güvenlik", "cybersecurity"),
    ("gelişmeleri", "developments"),
    ("gelişmeler", "developments"),
    ("gelişme", "development"),
    ("haberleri", "news"),
    ("haberler", "news"),
    ("önemli", "important"),
    ("son", "latest"),
)

#: A Turkish case suffix, optional, glued to a term before the word ends. The map used to
#: match a term as a bare SUBSTRING, which cut two ways: "haberleri" inside "haberlerini"
#: left the orphan "ni" in the query ("AI news ni", production 2026-09-20), and "son"
#: inside "sonuç" turned it into "latestuç". Matching the term as a WORD plus one of these
#: suffixes eats the suffix with the word and leaves anything else alone - "sonra" and
#: "ajanda" end in letters no case suffix has.
_CASE_SUFFIXES: Final[tuple[str, ...]] = (
    "nden", "ndan", "nin", "nın", "nun", "nün", "den", "dan", "ten", "tan",
    "nde", "nda", "yle", "yla", "in", "ın", "un", "ün", "yi", "yı", "yu", "yü",
    "ni", "nı", "nu", "nü", "ye", "ya", "de", "da", "te", "ta", "ne", "na",
    "i", "ı", "u", "ü", "e", "a",
)  # fmt: skip
_CASE_SUFFIX_RE: Final = "(?:" + "|".join(sorted(_CASE_SUFFIXES, key=len, reverse=True)) + ")?"

# Words that carry no search value once the date phrase and verb are removed.
_DROP_WORDS = frozenset({
    "ilgili", "ile", "ve", "araştır", "araştırın", "bul", "incele", "gündeki", "günde",
    "gün", "üç", "iki", "bir", "dört", "beş", "hafta", "haftadaki", "saat", "saatteki",
    "bugün", "dün", "the", "of", "in", "about", "latest",
})

_AGENT_DOMAIN_MARKERS = ("ajan", "agent")
_AGENT_ENTITY_QUERIES: tuple[str, ...] = (
    "OpenAI agents announcement",
    "Anthropic Claude agents announcement",
    "Google Gemini agents announcement",
    "Microsoft Copilot agents announcement",
    "open-source AI agent framework release",
    "AI agent security incident",
    "AI agents paper",
)

# --------------------------------------------------------------------------- #
# ADR-0178 (owner incident 2026-09-19): "araştırma başarısız oldu ... hep yabancı
# kaynaklara gidiyor" — a Turkish-worded query heuristic shared by the query
# builder below (which topics get the narrower discovery source-class set) and
# by app.research.browser_gateway (which search calls get a Turkish region
# hint). Both ask literally the same question — "is this text Turkish?" — so,
# unlike the deliberately-duplicated Turkish-fold tables elsewhere in this
# package (see app.research.evidence's own note on why the dedup/rank formula
# is hand-copied from a DIFFERENT process's codebase), this one lives in one
# place and is imported.
# --------------------------------------------------------------------------- #

_TURKISH_CHARS = frozenset("çğıöşüÇĞİÖŞÜ")
#: A handful of extremely common Turkish function/domain words — checked only when the
#: text carries no Turkish-specific LETTER at all (an ASCII-typed "yapay zeka ile
#: ilgili haberleri" carries none of the letters above).
_TURKISH_HINT_WORDS = frozenset(
    {
        "ve", "ile", "icin", "için", "ilgili", "haberleri", "haberler",
        "gelismeler", "gelişmeler", "son", "hakkinda", "hakkında",
        "yapay", "zeka", "zekâ", "konusunda", "nedir",
    }
)


def looks_turkish(text: str) -> bool:
    """Best-effort, dependency-free language guess for one piece of research text.

    Just enough to decide whether a search call should ask for the Turkish region, or
    whether a topic's default discovery source classes should skip HN/arXiv (both:
    ADR-0178) — never a real language-detection library, and a wrong guess costs
    nothing worse than the OLD, unbiased default. Turkish-specific letters are the
    strongest signal; failing that (an ASCII-typed Turkish sentence carries none), a
    handful of very common Turkish function words.
    """
    if not text:
        return False
    if any(ch in _TURKISH_CHARS for ch in text):
        return True
    words = set(re.findall(r"[a-zçğıöşü]+", text.lower()))
    return bool(words & _TURKISH_HINT_WORDS)


#: Fold table used ONLY by :func:`_bare_subject`'s trailing-filler match: a 1:1
#: character translation (never NFKD/combining-mark removal) so the folded string is
#: always exactly as long as the original, which is what lets the caller strip the
#: same number of characters from the UNFOLDED text it actually keeps.
_LENGTH_PRESERVING_FOLD = str.maketrans(
    {
        "ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ö": "o", "Ö": "o", "ü": "u", "Ü": "u", "ç": "c", "Ç": "c",
        "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u",
    }
)


def _fold_for_subject(text: str) -> str:
    return text.translate(_LENGTH_PRESERVING_FOLD).lower()


#: Trailing filler stripped, longest phrase first, so a compound ("ile ilgili
#: haberleri") strips as ONE unit rather than leaving a stray "ile" behind once only
#: its neighbour word is removed.
_SUBJECT_TRAILING_FILLERS: tuple[str, ...] = tuple(
    sorted(
        (
            "ile ilgili son haberleri", "ile ilgili son haberler",
            "ile ilgili haberleri", "ile ilgili haberler",
            "hakkındaki son haberler", "hakkında son haberler",
            "ile ilgili son gelişmeler", "ile ilgili son gelişmeleri",
            "ile ilgili gelişmeler", "ile ilgili gelişmeleri",
            "ile ilgili", "hakkındaki", "hakkında", "konusundaki", "konusunda",
            "son gelişmeler", "son gelişmeleri", "gelişmeler", "gelişmeleri", "gelişme",
            "son haberler", "son haberleri", "haberleri", "haberler",
            # The owner says "... haberlerini araştır": the accusative rides along and the
            # stripper did not know it, so the subject kept the whole news phrase and every
            # template doubled it (production 2026-09-20).
            "ile ilgili haberlerini", "son haberlerini", "haberlerini", "haberlerinden",
            "gelişmelerini", "son gelişmelerini", "ile ilgili gelişmelerini",
        ),
        key=len,
        reverse=True,
    )
)


def _bare_subject(topic: str) -> str:
    """The topic reduced to its SUBJECT — repeatedly strips one TRAILING filler
    phrase at a time (never touching the front: a leading relative-date phrase like
    "son üç günde" is the recency window, :mod:`app.research.dates`'s job, not this
    one) so "yapay zeka ile ilgili haberleri" reduces to "yapay zeka", the exact shape
    the owner's own words already take (ADR-0178, owner incident 2026-09-19: the
    UN-reduced topic fed straight back into the query templates below is what doubled
    every one of these words — "... haberleri haberleri", "AI news news").

    Never returns an empty string: a topic that is entirely filler (degenerate) is
    returned unchanged rather than reduced to nothing a search engine could use.
    """
    working = topic.strip()
    changed = True
    while changed:
        changed = False
        folded = _fold_for_subject(working)
        for filler in _SUBJECT_TRAILING_FILLERS:
            folded_filler = _fold_for_subject(filler)
            if len(folded) > len(folded_filler) and folded.endswith(folded_filler):
                working = working[: len(working) - len(filler)].rstrip()
                changed = True
                break
    return working or topic.strip()


def english_core_query(topic: str) -> str | None:
    """A compact English query for ``topic``, or ``None`` when no known term maps."""
    lowered = " ".join(topic.lower().replace("’", "'").split())
    mapped = False
    for turkish, english in sorted(_TERM_MAP, key=lambda pair: -len(pair[0])):
        pattern = re.compile(rf"\b{re.escape(turkish)}{_CASE_SUFFIX_RE}\b")
        replaced, count = pattern.subn(f" {english} ", lowered)
        if count:
            lowered = replaced
            mapped = True
    if not mapped:
        return None
    words = []
    for raw in lowered.replace(",", " ").replace(".", " ").split():
        word = raw.strip("'\"()[]{}:;!?")
        if not word or word.lower() in _DROP_WORDS:
            continue
        if any(ch in word for ch in "çğıöşü"):
            continue  # an unmapped Turkish word would only mislead an English engine
        words.append(word)
    seen: dict[str, str] = {}
    for w in words:
        seen.setdefault(w.lower(), w)
    query = " ".join(seen.values())
    return query or None


def expand_queries(topic: str) -> tuple[str, ...]:
    """Base Turkish templates + English core query + domain entity sub-queries.

    ADR-0178 (owner incident 2026-09-19): a topic that already ends in "haberleri" (the
    owner's own "... ile ilgili haberleri" phrasing) used to get "{topic} haberleri"
    appended anyway — "... haberleri haberleri" — and an English core query that
    already ends in "news" (``english_core_query`` maps "haberleri" -> "news" itself)
    got "{core} news" appended the same way — "AI news news", the exact doubled query
    a live QUICK run spent one of its two discovery-query slots on. Each of the three
    appended variants below is skipped when the text it would extend already ends with
    (or, for "gelişme", already contains) the word it would add. Two further queries —
    the topic reduced to its bare SUBJECT (:func:`_bare_subject`) plus "haberleri" /
    "son gelişmeler" exactly once — are appended whenever that reduction actually
    removed something, giving the owner's own desired shape ("yapay zeka haberleri",
    "yapay zeka son gelişmeler") a slot without deleting any existing query.
    """
    trimmed = topic.strip()
    base = [trimmed]
    folded_topic = _fold_for_subject(trimmed)
    # The guard used to read the phrase's ENDING, so "haberlerini" (the same word with a
    # case suffix) was not recognised and the plan spent a slot on "... haberlerini
    # haberleri" (production 2026-09-20). The last word's STEM is what makes it a news
    # phrase, whatever suffix rides on it.
    last_word = folded_topic.split()[-1] if folded_topic.split() else ""
    if not last_word.startswith("haber"):
        base.append(f"{trimmed} haberleri")
    if "gelisme" not in folded_topic:
        base.append(f"{trimmed} son gelişmeler")
    core = english_core_query(trimmed)
    if core:
        base.append(core)
        if not core.lower().endswith("news"):
            base.append(f"{core} news")
    if any(marker in trimmed.lower() for marker in _AGENT_DOMAIN_MARKERS):
        base.extend(_AGENT_ENTITY_QUERIES)
    subject = _bare_subject(trimmed)
    if subject and subject != trimmed:
        base.append(f"{subject} haberleri")
        base.append(f"{subject} son gelişmeler")
    return tuple(dict.fromkeys(q for q in base if q))


def _query_tokens(query: str) -> frozenset[str]:
    lowered = query.lower()
    for ch in ",.:;!?\"'()[]{}":
        lowered = lowered.replace(ch, " ")
    return frozenset(w for w in lowered.split() if w)


def _similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard overlap of two queries' word sets — 1.0 for the same words, 0.0 for
    none in common. Deterministic and language-agnostic: it needs no term list to notice
    that "X" and "X haberleri" ask a search engine almost the same thing."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def diversify_queries(queries: Sequence[str], limit: int) -> tuple[str, ...]:
    """The ``limit`` most DIFFERENT queries from an expansion, first one kept.

    :func:`expand_queries` returns its expansions in a fixed order whose first three
    entries are all Turkish variants of the same phrase ("X", "X haberleri", "X son
    gelişmeler"). Taking the first ``limit`` of that — which is exactly what a QUICK
    run's ``discovery_queries_max`` of 2 did — issues two near-identical searches and
    gets back one search engine's view of one language's coverage. On 2026-09-06 that
    is how a QUICK run reached ~100 candidates dominated by a handful of domains
    (ADR-0074 decision 4).

    Greedy and deterministic: query 0 (the owner's own phrasing) always leads; every
    later pick is the remaining query with the LOWEST word overlap against everything
    already picked, ties broken by the expansion's own order. For a Turkish topic
    naming an English entity this naturally yields a Turkish query and the English
    core query rather than two Turkish ones — no language list, no provider change.
    """
    ordered = [q for q in queries if q and q.strip()]
    if limit <= 0:
        return ()
    if len(ordered) <= limit:
        return tuple(ordered)
    # ADR-0185: a question asked in Turkish is searched in Turkish first. The greedy pick
    # below chose the ENGLISH core query for the second of a QUICK run's two slots by
    # design - it IS the most different query, and back when the search ran on the device's
    # own DuckDuckGo with a default region that was the only way to reach past one
    # language's coverage. Since ADR-0183 the search is typed into the owner's own Turkish
    # Google, signed in, and the owner watched it go out as "AI news". So the Turkish
    # queries take the slots first, and the English one takes what is left over - it is
    # still there, still expanded, just no longer ahead of the owner's own words.
    # Reordering alone would not do it: the greedy ranks by DIFFERENCE, and the English
    # query is the most different query there is. The Turkish ones are its whole candidate
    # pool until they run out.
    pools: list[list[str]] = [ordered]
    if looks_turkish(ordered[0]):
        turkish = [q for q in ordered if looks_turkish(q)]
        rest = [q for q in ordered if not looks_turkish(q)]
        if turkish and rest:
            pools = [turkish, rest]
    tokens = {q: _query_tokens(q) for q in ordered}
    picked = [pools[0][0]]
    for pool in pools:
        remaining = [q for q in pool if q not in picked]
        while len(picked) < limit and remaining:
            best_index = 0
            best_score = None
            for index, candidate in enumerate(remaining):
                score = max(_similarity(tokens[candidate], tokens[p]) for p in picked)
                if best_score is None or score < best_score:
                    best_score, best_index = score, index
            picked.append(remaining.pop(best_index))
        if len(picked) >= limit:
            break
    return tuple(picked)


#: ADR-0178 (owner incident 2026-09-19, item D4): markers that make HN Algolia
#: ("technical") and arXiv ("academic") discovery worth running at all. The AI-agent
#: domain markers already used for the entity sub-queries above count too — an
#: agent/framework topic is exactly HN/arXiv's home turf regardless of the language it
#: was asked in.
_ACADEMIC_TECHNICAL_MARKERS = (
    "arxiv", "paper", "research paper", "makale", "bilimsel", "akademik",
    "framework", "open-source", "open source", "github", "sdk", "kütüphane",
    "protokol", "protocol",
)


def _wants_academic_and_technical(topic: str) -> bool:
    """Whether HN/arXiv discovery is worth running for ``topic`` AT ALL, when the
    caller did not name explicit source classes.

    HN Algolia and arXiv are excellent for a technical/academic-English request
    ("open-source AI agent framework", an arXiv paper hunt) and close to useless for a
    general Turkish news request ("yapay zeka ile ilgili haberleri") — the production
    run behind ADR-0178 spent two of its five source-class x query discovery passes on
    APIs that read zero pages worth keeping. An English-worded topic keeps the old,
    broader default (nothing about this heuristic should narrow an English request);
    a Turkish-worded one needs an explicit technical/academic/agent marker to earn
    HN/arXiv discovery.
    """
    lowered = topic.lower()
    if any(marker in lowered for marker in _AGENT_DOMAIN_MARKERS):
        return True
    if any(marker in lowered for marker in _ACADEMIC_TECHNICAL_MARKERS):
        return True
    return not looks_turkish(topic)


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    """The M13 plan: what to search for, where, and in what time window."""

    topic: str
    recency: RecencyWindow
    queries: tuple[str, ...]
    source_classes: tuple[str, ...]
    max_sources_per_query: int = DEFAULT_MAX_SOURCES_PER_QUERY

    def as_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "recency": self.recency.as_dict(),
            "queries": list(self.queries),
            "source_classes": list(self.source_classes),
            "max_sources_per_query": self.max_sources_per_query,
        }


def build_plan(
    topic: str,
    *,
    now: datetime | None = None,
    source_classes: tuple[str, ...] = DEFAULT_SOURCE_CLASSES,
    max_sources_per_query: int = DEFAULT_MAX_SOURCES_PER_QUERY,
    recency_days_override: int | None = None,
) -> ResearchPlan:
    """Build a deterministic research plan for ``topic``.

    The recency window is parsed out of the topic text itself (the owner's
    own phrasing, e.g. "son üç günde ..."); when the topic carries no
    recognizable Turkish relative-date phrase, :func:`app.research.dates.default_window`
    (``DEFAULT_RECENCY_DAYS``) applies instead of guessing. ``recency_days_override``
    (REST ``recency_days``) is the owner's explicit, structured override and
    always wins over both the parsed phrase and the default.
    """
    if not topic or not topic.strip():
        raise ValueError("topic must be a non-empty string")
    if max_sources_per_query < 1:
        raise ValueError("max_sources_per_query must be >= 1")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    clean_topic = topic.strip()
    if recency_days_override is not None:
        if recency_days_override < 1:
            raise ValueError("recency_days_override must be >= 1")
        window = RecencyWindow(
            start=now - timedelta(days=recency_days_override), end=now,
            label=f"son {recency_days_override} gün", amount=recency_days_override, unit="day",
        )
    else:
        window = parse_recency_window(clean_topic, now=now) or default_window(
            now, days=DEFAULT_RECENCY_DAYS
        )
    queries = expand_queries(clean_topic)
    if not source_classes:
        raise ValueError("source_classes must be non-empty")

    resolved_source_classes = tuple(source_classes)
    # ADR-0178 item D4: only narrow the DEFAULT (a caller naming explicit classes,
    # e.g. REST's `discovery_source_classes`, always gets exactly what it asked for).
    if resolved_source_classes == DEFAULT_SOURCE_CLASSES and not _wants_academic_and_technical(
        clean_topic
    ):
        resolved_source_classes = tuple(
            c for c in resolved_source_classes if c not in ("technical", "academic")
        )

    return ResearchPlan(
        topic=clean_topic,
        recency=window,
        queries=queries,
        source_classes=resolved_source_classes,
        max_sources_per_query=max_sources_per_query,
    )


__all__ = [
    "diversify_queries",
    "english_core_query",
    "expand_queries",
    "looks_turkish",
    "DEFAULT_MAX_SOURCES_PER_QUERY",
    "DEFAULT_RECENCY_DAYS",
    "DEFAULT_SOURCE_CLASSES",
    "ResearchPlan",
    "build_plan",
]
