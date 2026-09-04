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
# M17 phase 9: the owner asks about what the system learned and what it is building.
QUERY_LEARNED = "learned"  # ne öğrendin / son hatalardan ne öğrendin
QUERY_EVOLUTION = "evolution"  # kendi üzerinde ne geliştiriyorsun
QUERY_SHADOW_READY = "shadow_ready"  # hazır modüllerin neler / canlıya alınmayı bekleyen ne var
QUERY_WHY_BUILT = "why_built"  # bu özelliği neden geliştirdin
QUERY_TESTS = "tests"  # test sonuçlarını anlat
QUERY_GOALS = "goals"  # neyi hedefliyorsun / hedeflerin ne durumda
QUERY_SINCE_YOU_LEFT = "since_you_left"  # siz yokken / yokluğumda ne oldu

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
    QUERY_LEARNED,
    QUERY_EVOLUTION,
    QUERY_SHADOW_READY,
    QUERY_WHY_BUILT,
    QUERY_TESTS,
    QUERY_GOALS,
    QUERY_SINCE_YOU_LEFT,
)

LEVEL_EXECUTIVE = "executive"
LEVEL_DETAILED = "detailed"
LEVEL_TECHNICAL = "technical"
LEVEL_FULL = "full"
LEVELS = (LEVEL_EXECUTIVE, LEVEL_DETAILED, LEVEL_TECHNICAL, LEVEL_FULL)

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
    # --- the returning owner: one briefing for a whole absence ------------------
    (("yokken",), QUERY_SINCE_YOU_LEFT),
    (("yokluğum",), QUERY_SINCE_YOU_LEFT),
    (("ben", "yokken"), QUERY_SINCE_YOU_LEFT),
    (("gece", "ne"), QUERY_SINCE_YOU_LEFT),
    # --- what did you learn / what are you building (M17 phase 9) ---------------
    (("neden", "geliştir"), QUERY_WHY_BUILT),
    (("neden", "yaptın"), QUERY_WHY_BUILT),
    (("hata", "öğren"), QUERY_LEARNED),
    (("ne", "öğren"), QUERY_LEARNED),
    (("öğren",), QUERY_LEARNED),
    (("ders",), QUERY_LEARNED),
    (("kendi", "geliştir"), QUERY_EVOLUTION),
    (("üzerinde", "çalış"), QUERY_EVOLUTION),
    (("geliştir", "misin"), QUERY_EVOLUTION),
    (("hazır", "modül"), QUERY_SHADOW_READY),
    (("canlı", "bekle"), QUERY_SHADOW_READY),
    (("shadow",), QUERY_SHADOW_READY),
    (("yayına", "bekle"), QUERY_SHADOW_READY),
    (("test", "sonuç"), QUERY_TESTS),
    (("test", "anlat"), QUERY_TESTS),
    (("hedef",), QUERY_GOALS),
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
    if (_has(tokens, "hepsini") or _has(tokens, "tamamını") or _has(tokens, "tümünü")) and (
        _has(tokens, "oku") or _has(tokens, "anlat")
    ):
        level = LEVEL_FULL
    elif (_has(tokens, "bütün") or _has(tokens, "tüm")) and _has(tokens, "detay"):
        level = LEVEL_FULL
    elif kind == QUERY_TECHNICAL or _has(tokens, "teknik"):
        level = LEVEL_TECHNICAL
    elif kind == QUERY_RESEARCH_DETAIL or _has(tokens, "detay") or _has(tokens, "ayrıntı"):
        level = LEVEL_DETAILED
    since: datetime | None = None
    if kind == QUERY_TODAY:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif kind in (QUERY_LAST_ACTIVITY, QUERY_FAILURES, QUERY_PROBLEMS_NOW):
        since = now - timedelta(days=7)
    elif kind == QUERY_SINCE_YOU_LEFT:
        # an absence is measured from the owner's last interaction; the caller may narrow
        # it, and a day is the honest default for "while you were away"
        since = now - timedelta(days=1)
    elif kind in (QUERY_LEARNED, QUERY_EVOLUTION, QUERY_SHADOW_READY, QUERY_TESTS, QUERY_GOALS):
        # what was learned and what is being built are not this week's news
        since = now - timedelta(days=90)
    subsystem = _subsystem(tokens)
    if kind == QUERY_RESEARCH_DETAIL:
        subsystem = "research"
    elif kind in (QUERY_EVOLUTION, QUERY_SHADOW_READY, QUERY_WHY_BUILT):
        subsystem = "evolution"
    elif kind == QUERY_LEARNED:
        subsystem = None  # lessons span every subsystem
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
    "LEVEL_FULL",
    "LEVEL_TECHNICAL",
    "QUERY_EVIDENCE",
    "QUERY_FAILURES",
    "QUERY_KINDS",
    "QUERY_LEARNED",
    "QUERY_EVOLUTION",
    "QUERY_SHADOW_READY",
    "QUERY_WHY_BUILT",
    "QUERY_TESTS",
    "QUERY_GOALS",
    "QUERY_SINCE_YOU_LEFT",
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
