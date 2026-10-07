"""The memory's word leg, in Turkish (card memory-lexical-turkish-rrf).

``retrieval.keyword_candidates`` used to take every word of three letters or more, lower it
with ``str.lower`` (which turns "İ" into "i" + a combining dot) and give a memory found only
by its words a semantic score of 0.0 - so "Ahmet'e ne söz vermiştim?" buried the one memory
that named Ahmet under every look-alike. This module holds the Turkish pieces the fixed leg
needs; the leg itself stays in ``retrieval``.

- ``fold``: the matching key. Turkish lower case, then dotless "ı" -> "i", so İzmir / IZMIR /
  izmir / ızmır are one key. PostgreSQL's ``lower('I')`` is "i", not "ı"; the SQL side folds
  the same way with ``translate(lower(text), 'ı', 'i')`` (``FOLD_SQL``).
- ``query_terms``: the query's words, folded, stop words out, duplicates out, order kept.
- ``stem_root`` / ``tsquery_text``: the ``turkish`` Snowball stemmer keeps "vermiş" for
  "vermiştim" and "ver" for "verdim", so a stemmed query misses the memory; stripping the
  tense and person here and asking for the root as a prefix ("ver:*") finds it.
- ``rrf_score``: Reciprocal Rank Fusion (Cormack, Clarke, Büttcher 2009), k = 60,
  normalised so first-in-both is 1.0.
- ``lexical_mode``: ``PAGENTOS_MEMORY_LEXICAL`` = ``trgm`` (default since the measurement of
  card memory-lexical-turkish-measure) or ``like`` (the old leg, kept as the way back).
  Read from the environment here; it moves into ``app.config`` later (ADR).
"""

from __future__ import annotations

import os
import re
from typing import Final, Literal

from app.conversations.search import search_fold, search_fold_sql
from app.household.parse import turkish_lower
from app.logging import get_logger

logger = get_logger("app.memory.lexical")

MODE_ENV: Final = "PAGENTOS_MEMORY_LEXICAL"
LexicalMode = Literal["like", "trgm"]

#: Reciprocal Rank Fusion's constant; 60 is the paper's value and the common default.
RRF_K: Final = 60
#: The most one memory can earn: first in the semantic list and first in the word list.
RRF_MAX: Final = 2.0 / (RRF_K + 1)

MAX_TERMS: Final = 8

#: Same word shape as ``retrieval._QUERY_WORD``: letters and digits, three or more.
_QUERY_WORD: Final = re.compile(r"[a-zA-Z0-9çğıöşüÇĞİÖŞÜ]{3,}")

#: What the owner says around a question, never what it is about. Folded (no "ı").
STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "ve",
        "veya",
        "ya",
        "ile",
        "ama",
        "fakat",
        "ki",
        "da",
        "de",
        "mi",
        "mu",
        "mü",
        "bir",
        "bu",
        "şu",
        "su",
        "o",
        "ne",
        "neyi",
        "nedir",
        "için",
        "icin",
        "gibi",
        "kadar",
        "olan",
        "şey",
        "sey",
        "çok",
        "cok",
        "daha",
        "en",
        "her",
        "hiç",
        "hic",
        "sonra",
        "önce",
        "ben",
        "sen",
        "biz",
        "siz",
        "onlar",
        "bana",
        "sana",
        "beni",
        "seni",
        "benim",
        "senin",
        "var",
        "yok",
        "mı",
        "mıydı",
        "miydi",
        "acaba",
        "hangi",
        "nasıl",
        "nasil",
    }
)

#: Tense + person endings stripped by ``stem_root``, longest first. A candidate generator,
#: not a morphology: what it over-reaches, the ranking after it sorts out.
_VERB_ENDINGS: Final = tuple(
    sorted(
        {
            "miştim",
            "mıştım",
            "muştum",
            "müştüm",
            "miştin",
            "mıştın",
            "muştun",
            "müştün",
            "miştik",
            "mıştık",
            "muştuk",
            "müştük",
            "mişti",
            "mıştı",
            "muştu",
            "müştü",
            "mişim",
            "mışım",
            "muşum",
            "müşüm",
            "miş",
            "mış",
            "muş",
            "müş",
            "ecektim",
            "acaktım",
            "ecekti",
            "acaktı",
            "eceğim",
            "acağım",
            "ecek",
            "acak",
            "iyorum",
            "ıyorum",
            "uyorum",
            "üyorum",
            "iyordum",
            "ıyordum",
            "uyordum",
            "üyordum",
            "dim",
            "dım",
            "dum",
            "düm",
            "tim",
            "tım",
            "tum",
            "tüm",
            "din",
            "dın",
            "dun",
            "dün",
            "tin",
            "tın",
            "tun",
            "tün",
            "dik",
            "dık",
            "duk",
            "dük",
            "tik",
            "tık",
            "tuk",
            "tük",
            "di",
            "dı",
            "du",
            "dü",
            "ti",
            "tı",
            "tu",
            "tü",
        },
        key=len,
        reverse=True,
    )
)
_MIN_ROOT: Final = 3


def fold(text: str) -> str:
    """The matching key: Turkish lower case with "ı" folded onto "i"."""
    return turkish_lower(text).replace("ı", "i")


#: ``fold`` in SQL. ``lower`` under the database's UTF-8 locale already maps İ and I to i.
FOLD_SQL: Final = "translate(lower(text), 'ı', 'i')"


def query_words(text: str, limit: int = MAX_TERMS) -> list[str]:
    """The query's words in Turkish lower case, stop words and duplicates out, order kept."""
    words: list[str] = []
    seen: set[str] = set()
    for raw in _QUERY_WORD.findall(text or ""):
        word = turkish_lower(raw)
        key = fold(word)
        if key in STOPWORDS or key in seen:
            continue
        seen.add(key)
        words.append(word)
        if len(words) >= limit:
            break
    return words


def query_terms(text: str, limit: int = MAX_TERMS) -> list[str]:
    """``query_words`` folded: the keys the word leg looks for."""
    return [fold(word) for word in query_words(text, limit)]


def stem_root(word: str) -> str:
    """``word`` without one tense + person ending, if what is left has three letters."""
    for ending in _VERB_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= _MIN_ROOT:
            return word[: -len(ending)]
    return word


def tsquery_text(text: str) -> str:
    """An OR of root prefixes for ``to_tsquery('turkish', ...)``; "" when nothing is left.

    Safe to bind: every word is letters and digits only (``_QUERY_WORD``)."""
    roots: list[str] = []
    for word in query_words(text):
        root = stem_root(word)
        if root not in roots:
            roots.append(root)
    return " | ".join(f"{root}:*" for root in roots)


#: The conversation search's ASCII fold (ş->s, ü->u, ğ->g, ı/İ->i, ç->c, ö->o, case), one copy
#: for both searches (card memory-search-ascii-fold): 'sukru' finds 'Şükrü' and back. ``fold``
#: stays the PostgreSQL index's key; this one is only compared, never indexed.
search_key = search_fold
search_key_sql = search_fold_sql


def term_matches(term: str, folded_text: str) -> bool:
    """Whether a folded ``term`` (or its root, as a word prefix) is in ``folded_text``, both
    sides through ``search_key`` so ASCII typing matches the Turkish letters."""
    text = search_key(folded_text)
    if search_key(term) in text:
        return True
    root = stem_root(term)
    return root != term and re.search(rf"(?<![\w]){re.escape(search_key(root))}", text) is not None


def rrf_score(semantic_rank: int, keyword_rank: int | None) -> float:
    """Reciprocal Rank Fusion of two 1-based ranks, normalised to 0..1 by ``RRF_MAX``."""
    score = 1.0 / (RRF_K + semantic_rank)
    if keyword_rank is not None:
        score += 1.0 / (RRF_K + keyword_rank)
    return score / RRF_MAX


def lexical_mode() -> LexicalMode:
    """``PAGENTOS_MEMORY_LEXICAL``: ``trgm`` (default) or ``like``; anything else is ``trgm``.

    The default follows docs/evidence/memory-lexical-turkish-measure.json."""
    raw = (os.environ.get(MODE_ENV) or "trgm").strip().lower()
    if raw in ("like", "trgm"):
        return raw  # type: ignore[return-value]
    logger.warning("memory_lexical_mode_unknown", value=raw[:20], used="trgm")
    return "trgm"


__all__ = [
    "FOLD_SQL",
    "MODE_ENV",
    "RRF_K",
    "RRF_MAX",
    "STOPWORDS",
    "fold",
    "lexical_mode",
    "query_terms",
    "query_words",
    "rrf_score",
    "search_key",
    "search_key_sql",
    "stem_root",
    "term_matches",
    "tsquery_text",
    "turkish_lower",
]
