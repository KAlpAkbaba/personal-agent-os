"""Which question about the system the owner asked (spec §2 step 1).

Deterministic and Turkish. The realtime intent resolver (``app.voice.intents``) already
recognises these phrasings as ``Intent.EXPLAIN`` with a ``query_kind``; this module is the
single home of that vocabulary so the voice tool, the REST route and the tests agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.voice.intents import normalize_transcript

QUERY_LAST_ACTIVITY = "last_activity"
QUERY_TODAY = "today"
QUERY_FAILURES = "failures"
QUERY_PROBLEMS_NOW = "problems_now"
QUERY_SUBSYSTEM_STATUS = "subsystem_status"
QUERY_WHY_FAILED = "why_failed"
QUERY_EVIDENCE = "evidence"
QUERY_RESEARCH_DETAIL = "research_detail"
QUERY_TECHNICAL = "technical"
QUERY_MODULE_PROBLEM = "module_problem"

QUERY_KINDS = (
    QUERY_LAST_ACTIVITY,
    QUERY_TODAY,
    QUERY_FAILURES,
    QUERY_PROBLEMS_NOW,
    QUERY_SUBSYSTEM_STATUS,
    QUERY_WHY_FAILED,
    QUERY_EVIDENCE,
    QUERY_RESEARCH_DETAIL,
    QUERY_TECHNICAL,
    QUERY_MODULE_PROBLEM,
)

LEVEL_EXECUTIVE = "executive"
LEVEL_DETAILED = "detailed"
LEVEL_TECHNICAL = "technical"
LEVELS = (LEVEL_EXECUTIVE, LEVEL_DETAILED, LEVEL_TECHNICAL)

#: Subsystem words the owner uses, mapped onto ledger subsystem names.
_SUBSYSTEM_WORDS: tuple[tuple[str, str], ...] = (
    ("araştırma", "research"),
    ("arastirma", "research"),
    ("research", "research"),
    ("tarayıcı", "browser"),
    ("tarayici", "browser"),
    ("chrome", "browser"),
    ("ses", "voice"),
    ("voice", "voice"),
    ("dağıtım", "deployment"),
    ("dagitim", "deployment"),
    ("release", "deployment"),
    ("hafıza", "memory"),
    ("hafiza", "memory"),
    ("bellek", "memory"),
)

# (all of these stems present) -> kind; first match wins, so specific phrasings come first.
_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("araştırma", "detay"), QUERY_RESEARCH_DETAIL),
    (("araştırma", "ayrıntı"), QUERY_RESEARCH_DETAIL),
    (("bulgu",), QUERY_RESEARCH_DETAIL),
    (("neden", "başarısız"), QUERY_WHY_FAILED),
    (("ne", "başarısız"), QUERY_FAILURES),
    (("başarısız", "oldu"), QUERY_FAILURES),
    (("hata",), QUERY_FAILURES),
    (("kanıt",), QUERY_EVIDENCE),
    (("sorun", "ne"), QUERY_MODULE_PROBLEM),
    (("sorun", "var"), QUERY_PROBLEMS_NOW),
    (("sorun",), QUERY_PROBLEMS_NOW),
    (("durum",), QUERY_SUBSYSTEM_STATUS),
    (("teknik",), QUERY_TECHNICAL),
    (("bugün",), QUERY_TODAY),
    (("son", "yaptık"), QUERY_LAST_ACTIVITY),
    (("son", "ne", "yaptı"), QUERY_LAST_ACTIVITY),
    (("neler", "yaptı"), QUERY_LAST_ACTIVITY),
    (("ne", "yaptı"), QUERY_LAST_ACTIVITY),
    (("yaptıkları", "anlat"), QUERY_LAST_ACTIVITY),
)


@dataclass(frozen=True, slots=True)
class ExplainQuery:
    kind: str
    level: str
    since: datetime | None
    subsystem: str | None
    module: str | None
    normalized: str

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "level": self.level,
            "since": self.since.isoformat() if self.since else None,
            "subsystem": self.subsystem,
            "module": self.module,
        }


def _has(tokens: tuple[str, ...], stem: str) -> bool:
    return any(tok == stem or tok.startswith(stem) for tok in tokens)


def _subsystem(tokens: tuple[str, ...]) -> str | None:
    for word, subsystem in _SUBSYSTEM_WORDS:
        if _has(tokens, word):
            return subsystem
    return None


def _module(tokens: tuple[str, ...], normalized: str) -> str | None:
    """ "Diagnostic Observer'da sorun ne?" -> "diagnostic observer". Anything before
    the locative suffix that is not a question word is the module name."""
    if "sorun" not in normalized:
        return None
    words = []
    for tok in tokens:
        if tok in ("sorun", "ne", "var", "mı", "mi", "mu", "mü"):
            break
        words.append(tok)
    if not words:
        return None
    # strip a Turkish locative suffix from the last word ("observer'da" -> "observer")
    last = words[-1]
    for suffix in ("'da", "'de", "'ta", "'te", "da", "de", "ta", "te"):
        if last.endswith(suffix) and len(last) > len(suffix) + 2:
            words[-1] = last[: -len(suffix)]
            break
    return " ".join(words) or None


def classify(
    question: str, *, now: datetime | None = None, default_level: str = LEVEL_EXECUTIVE
) -> ExplainQuery:
    """Map a Turkish question onto a query kind, a level and a time window."""
    normalized, tokens, _dropped = normalize_transcript(question)
    now = now or datetime.now(UTC)
    kind = QUERY_LAST_ACTIVITY
    for stems, candidate in _PATTERNS:
        if all(_has(tokens, stem) for stem in stems):
            kind = candidate
            break
    level = default_level
    if kind == QUERY_TECHNICAL or _has(tokens, "teknik"):
        level = LEVEL_TECHNICAL
    elif kind == QUERY_RESEARCH_DETAIL or _has(tokens, "detay") or _has(tokens, "ayrıntı"):
        level = LEVEL_DETAILED
    since: datetime | None = None
    if kind == QUERY_TODAY:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif kind in (QUERY_LAST_ACTIVITY, QUERY_FAILURES, QUERY_PROBLEMS_NOW):
        since = now - timedelta(days=7)
    subsystem = _subsystem(tokens)
    if kind == QUERY_RESEARCH_DETAIL:
        subsystem = "research"
    module = _module(tokens, normalized) if kind == QUERY_MODULE_PROBLEM else None
    if kind == QUERY_MODULE_PROBLEM and module is None:
        kind = QUERY_PROBLEMS_NOW
    return ExplainQuery(
        kind=kind,
        level=level,
        since=since,
        subsystem=subsystem,
        module=module,
        normalized=normalized,
    )


__all__ = [
    "LEVELS",
    "LEVEL_DETAILED",
    "LEVEL_EXECUTIVE",
    "LEVEL_TECHNICAL",
    "QUERY_EVIDENCE",
    "QUERY_FAILURES",
    "QUERY_KINDS",
    "QUERY_LAST_ACTIVITY",
    "QUERY_MODULE_PROBLEM",
    "QUERY_PROBLEMS_NOW",
    "QUERY_RESEARCH_DETAIL",
    "QUERY_SUBSYSTEM_STATUS",
    "QUERY_TECHNICAL",
    "QUERY_TODAY",
    "QUERY_WHY_FAILED",
    "ExplainQuery",
    "classify",
]
