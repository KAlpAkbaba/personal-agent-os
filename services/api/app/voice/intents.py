"""Turkish realtime intent resolver + narration bridge (M12 spec §5, ADR-0034 §4).

The provider gives us a transcript of what the owner just said; this module
decides what it MEANS for the assistant, against the live session state and
the narration state, and translates it into narration-engine cursor / speed
operations. It is semantic, not keyword-only:

- the text is passed through the existing tr-TR normaliser first ("2. maddeyi"
  -> "ikinci maddeyi"), then Turkish-casefolded (İ -> i, I -> ı) and tokenised;
- hesitation fillers ("şey", "yani", "hani", "ııı", "eee", "hmm", ...) are
  dropped, so "şey, yani biraz daha yavaş" resolves like "biraz daha yavaş";
- matching is on TOKENS, never substrings: "durum raporunu oku" does not stop,
  "durdur" does; the M4 ``STOP_WORDS`` keep their meaning and top priority;
- "devam" / "tekrar" resolve against state: with a narration paused they are
  narration operations, in a plain conversation they mean "continue / repeat
  what you were saying" (``scope``).

Nothing here touches audio, the network or the database. The bridge returns a
new ``NarrationState`` through the M4 command machine (``app.narration.commands``)
so "dur always wins" and the exact-cursor rules are inherited, not duplicated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Final

from app.narration import commands
from app.narration.commands import Command, NarrationState, ParsedCommand, State
from app.narration.engine import PARAGRAPH_HEADING, PARAGRAPH_LIST, Cursor, NarrationPlan
from app.narration.normalizer import normalize
from app.voice.realtime import STOP_WORDS, RealtimeState


class Intent(StrEnum):
    # M18 (docs/M18_HOLOGRAPHIC_CORE_SPEC.md §2): the privacy-critical Active Eye
    # stop phrases. Listed first because it is checked first — see resolve_intent.
    EYE_DISABLE = "eye_disable"  # gözünü kapat / kamerayı kapat / beni izleme
    # docs/M18_ACTION_CONTRACT.md §2: the enable path that did not exist on 2026-09-06.
    EYE_ENABLE = "eye_enable"  # gözünü aç / kamerayı aç / beni izle / beni tekrar izle
    # "Canlıya al." as an IMPERATIVE is an action (always refused by policy, contract §2);
    # "canlıya alabilir misin?" stays the can_deploy QUERY.
    DEPLOY = "deploy"  # canlıya al / yayına al
    STOP = "stop"  # dur / kes / sus / yeter / durdur / duraklat / bekle
    RESUME = "resume"  # devam / kaldığın yerden / sürdür
    REPEAT = "repeat"  # tekrar (oku) / yeniden oku / bir daha
    REPEAT_ITEM = "repeat_item"  # ikinci maddeyi tekrar oku / üçüncü maddeye geç
    NEXT_ITEM = "next_item"  # sonraki madde
    PREVIOUS_ITEM = "previous_item"  # önceki madde
    FIRST_ITEM = "first_item"  # ilk madde
    LAST_ITEM = "last_item"  # son madde
    NEXT_SECTION = "next_section"  # sonraki bölüm / başlık
    SLOWER = "slower"  # biraz daha yavaş / yavaşla
    FASTER = "faster"  # biraz daha hızlı / hızlan
    SUMMARIZE = "summarize"  # özet geç / özetle / kısaca
    DETAIL = "detail"  # detaya gir / detaylandır / ayrıntı
    SKIP = "skip"  # burayı atla / bunu geç
    # M16 (docs/M16_ACTIVITY_LEDGER_SPEC.md §3.3): self explanation over the ledger
    TECHNICAL = "technical"  # teknik anlat / teknik olarak ne değişti
    EXPLAIN_PREVIOUS = "explain_previous"  # önceki maddeyi açıkla
    EXPLAIN = "explain"  # son yaptıklarını anlat / ne başarısız oldu / kanıtı ne ...
    FULL = "full"  # hepsini oku / tamamını anlat / bütün detayları oku
    NONE = "none"


#: Scope tells the caller WHICH subsystem the intent targets, resolved from
#: state: a paused narration owns "devam"; a cancelled conversational answer
#: owns "devam" otherwise.
SCOPE_NARRATION = "narration"
SCOPE_CONVERSATION = "conversation"

#: The three classes of owner utterance (docs/M18_ACTION_CONTRACT.md §2). A QUERY is
#: answered from an authoritative source and mutates nothing; an ACTION targets a
#: canonical capability and ends in a receipt; a CONTROL steers the conversation or a
#: narration (stop, resume, item moves, speed). This resolver is the ONLY place the
#: class is decided.
KLASS_QUERY = "query"
KLASS_ACTION = "action"
KLASS_CONTROL = "control"

#: The canonical capability each ACTION intent targets (contract §2's third column).
CAPABILITY_BY_INTENT: dict[Intent, str] = {
    Intent.EYE_DISABLE: "eye.disable",
    Intent.EYE_ENABLE: "eye.enable",
    Intent.DEPLOY: "release.promote",
}


#: The four RESEARCH interaction classes (docs/DECISIONS.md ADR-0075). They are not
#: intents: an owner utterance about a research already carries an intent (TECHNICAL,
#: EXPLAIN, REPEAT, NONE ...), and what the server additionally has to know is whether
#: this turn may START A CRAWL. That question has exactly four answers, and they are
#: decided HERE, in the one router, so the durable audit row says which class was
#: decided and no second Turkish table can disagree with it.
RESEARCH_CLASS_NEW = "new_research"
RESEARCH_CLASS_TECHNICAL_EXPLANATION = "research_technical_explanation"
RESEARCH_CLASS_FOLLOWUP = "research_followup"
RESEARCH_CLASS_RETRY = "research_retry"

RESEARCH_CLASSES: Final[tuple[str, ...]] = (
    RESEARCH_CLASS_NEW,
    RESEARCH_CLASS_TECHNICAL_EXPLANATION,
    RESEARCH_CLASS_FOLLOWUP,
    RESEARCH_CLASS_RETRY,
)

#: The two classes that MAY start a crawl. Everything else about a completed research is
#: answered from that research's own report.
RESEARCH_CLASSES_MAY_CRAWL: Final[tuple[str, ...]] = (
    RESEARCH_CLASS_NEW,
    RESEARCH_CLASS_RETRY,
)

#: The two classes that are ABOUT a research that already finished, and therefore must
#: never become a second crawl (ADR-0075 decision 3).
RESEARCH_CLASSES_BOUND_TO_A_RUN: Final[tuple[str, ...]] = (
    RESEARCH_CLASS_TECHNICAL_EXPLANATION,
    RESEARCH_CLASS_FOLLOWUP,
)


def klass_for(intent: Intent) -> str:
    """query | action | control for an intent (contract §2).

    ``NONE`` is reported as a query: nothing this resolver owns was said, the model
    answers conversationally, and nothing mutates - which is the query class's
    guarantee. It is emphatically not an action."""
    if intent in CAPABILITY_BY_INTENT:
        return KLASS_ACTION
    if intent in (Intent.EXPLAIN, Intent.NONE):
        return KLASS_QUERY
    return KLASS_CONTROL

PRESENTATION_SUMMARY = "summary"
PRESENTATION_DETAIL = "detail"
PRESENTATION_TECHNICAL = "technical"
PRESENTATION_FULL = "full"

#: Section titles of an activity briefing (app.explain) that the presentation levels map
#: onto. A briefing is an artifact, so "özetle" / "detay ver" / "teknik anlat" are cursor
#: jumps into it, not a different document.
LEVEL_SECTION_TITLES: dict[str, tuple[str, ...]] = {
    PRESENTATION_SUMMARY: ("özet", "ozet"),
    PRESENTATION_DETAIL: ("ayrıntı", "ayrinti", "detay"),
    PRESENTATION_TECHNICAL: ("teknik",),
}

#: Questions about the system's own activity (spec §2). Each entry: the tokens that must
#: ALL be present (as stems), and the query kind they resolve to. Order matters: the
#: first match wins, so the more specific phrasings come first.
#: REMOVED 2026-09-05. This was a second Turkish pattern table, duplicating
#: app/explain/classify.py's. They drifted, and the drift cost an owner qualification run:
#: the classifier knew the M17 question kinds and this list did not. _explain_kind now
#: delegates, so there is one table and it cannot disagree with itself.

SPEED_STEP = 0.25

# Hesitation fillers (spec §5 hesitation guard vocabulary + common Turkish).
FILLERS = frozenset(
    {
        "şey",
        "yani",
        "hani",
        "işte",
        "böyle",
        "ya",
        "yaa",
        "ee",
        "eee",
        "ıı",
        "ııı",
        "ı",
        "hmm",
        "hm",
        "hımm",
        "hım",
        "ehm",
        "aa",
        "aaa",
        "of",
        "e",
        "mm",
        "mmm",
    }
)
_ELONGATED_FILLER = re.compile(r"^(?:ı{2,}|e{2,}|a{2,}|m{2,}|h[ıi]?m+|ee+h?|ya+)$")

# Stop tokens: the M4 STOP_WORDS (single-word members) plus imperative forms
# that M4's narration command parser already treats as "dur".
_SINGLE_STOP_WORDS = frozenset(w for w in STOP_WORDS if " " not in w)
_MULTI_STOP_PHRASES = tuple(w for w in STOP_WORDS if " " in w)
STOP_TOKENS = _SINGLE_STOP_WORDS | frozenset({"durdur", "duraklat", "bekle"})

_ORDINALS: dict[str, int] = {**commands._ORDINAL_WORDS}
# Stems, because Turkish suffixes soften the final consonant (başlık -> başlığa).
_ITEM_NOUNS = ("madde", "paragraf", "nokta", "başlı", "bölüm")
_SECTION_NOUNS = ("bölüm", "başlı")

_PUNCT_RE = re.compile(r"[^\w\s']", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ResolvedIntent:
    intent: Intent
    scope: str = SCOPE_CONVERSATION
    target_index: int | None = None  # 1-based item index for REPEAT_ITEM
    normalized_text: str = ""
    tokens: tuple[str, ...] = ()
    fillers_removed: int = 0
    confidence: float = 1.0
    matched: str = ""  # the token/phrase that decided it (for audit/debug)
    query_kind: str | None = None  # EXPLAIN only: which question about the system
    #: One of :data:`RESEARCH_CLASSES` when this utterance is about research at all
    #: (ADR-0075). ``None`` means "nothing to do with research" - never "safe to crawl".
    research_class: str | None = None
    #: query | action | control (contract §2); derived from the intent unless given.
    klass: str = ""
    #: The canonical capability an ACTION targets ("eye.disable"); None for the rest.
    capability: str | None = None

    def __post_init__(self) -> None:
        if not self.klass:
            object.__setattr__(self, "klass", klass_for(self.intent))
        if self.capability is None and self.intent in CAPABILITY_BY_INTENT:
            object.__setattr__(self, "capability", CAPABILITY_BY_INTENT[self.intent])

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "klass": self.klass,
            "capability": self.capability,
            "scope": self.scope,
            "target_index": self.target_index,
            "normalized_text": self.normalized_text,
            "fillers_removed": self.fillers_removed,
            "confidence": self.confidence,
            "matched": self.matched,
            "query_kind": self.query_kind,
            "research_class": self.research_class,
        }


# ------------------------------------------------------------- normalisation


def turkish_casefold(text: str) -> str:
    """Casefold that keeps Turkish dotted/dotless i distinct (str.lower maps
    'I' to 'i', which would turn 'ISI' into 'isi' instead of 'ısı')."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def is_filler(token: str) -> bool:
    return token in FILLERS or bool(_ELONGATED_FILLER.match(token))


def normalize_transcript(text: str) -> tuple[str, tuple[str, ...], int]:
    """(normalized text, content tokens, fillers removed).

    Runs the tr-TR normaliser so numerals become words ("2." -> "ikinci"),
    casefolds the Turkish way, strips punctuation and drops hesitation fillers.
    """
    if not text or not text.strip():
        return "", (), 0
    spoken = normalize(text.strip())
    lowered = turkish_casefold(spoken)
    cleaned = _PUNCT_RE.sub(" ", lowered)
    raw_tokens = [t.strip("'") for t in cleaned.split()]
    raw_tokens = [t for t in raw_tokens if t]
    tokens = [t for t in raw_tokens if not is_filler(t)]
    return " ".join(tokens), tuple(tokens), len(raw_tokens) - len(tokens)


# ---------------------------------------------------------------- resolution


def _has(tokens: tuple[str, ...], *stems: str) -> str | None:
    """First token that starts with one of ``stems`` (Turkish suffixes vary:
    maddeyi / maddeye / maddeden), or None."""
    for tok in tokens:
        for stem in stems:
            if tok == stem or tok.startswith(stem):
                return tok
    return None


def _has_exact(tokens: tuple[str, ...], *words: str) -> str | None:
    for tok in tokens:
        if tok in words:
            return tok
    return None


#: "Read it all": the only phrasings that lift the narration budget (spec: full/read-all).
_FULL_READ_PHRASES: tuple[tuple[str, ...], ...] = (
    ("hepsini", "oku"),
    ("hepsini", "anlat"),
    ("tamamını", "oku"),
    ("tamamını", "anlat"),
    ("bütün", "detay"),
    ("tüm", "detay"),
    ("tümünü", "oku"),
    ("tümünü", "anlat"),
)


def _full_read(tokens: tuple[str, ...]) -> bool:
    return any(all(_has(tokens, stem) for stem in stems) for stems in _FULL_READ_PHRASES)


#: Kinds the intent resolver refuses on a single common word, and the stems that
#: corroborate them.
#:
#: The classifier and this resolver answer different questions, and that difference is the
#: whole reason this table exists. The classifier is deliberately liberal: by the time it
#: runs, the utterance is already known to BE a question about the system, so matching
#: "teknik" or "bugün" alone is correct there. This resolver decides whether an utterance
#: was a question at all, from raw speech that may be about the weather - so "bugün hava
#: güzel" must not become a briefing request, and a bare "teknik anlat" is a move through
#: an open briefing rather than a new one. The duplicate table this replaced encoded these
#: distinctions accidentally, by being narrower; they are stated deliberately now
#: (2026-09-05).
_INTENT_CORROBORATION: dict[str, tuple[str, ...]] = {
    "today": ("yaptı", "yapti", "neler", "oldu"),
    "technical": ("değiş", "degis"),
    "research_detail": ("araştırma", "arastirma", "bulgu"),
    # "durumu anlat" and "durum raporunu oku" must not become a cognitive query on one
    # common noun; a subsystem has to be named.
    "subsystem_status": (
        "araştırma",
        "arastirma",
        "research",
        "tarayıcı",
        "tarayici",
        "chrome",
        "ses",
        "voice",
        "dağıtım",
        "dagitim",
        "release",
        "hafıza",
        "hafiza",
        "bellek",
        "sistem",
        "cihaz",
        "sunucu",
    ),
}


def _explain_kind(tokens: tuple[str, ...], text: str = "") -> str | None:
    """The kind of question about the system's own activity, or None.

    Delegates to ``app.explain.classify``, which is the ONE Turkish normalisation table.
    This module used to keep a second one, and the two drifted: the classifier learned the
    M17 kinds and this list never did, so the server watched the owner ask "Kendi
    sisteminde şu anda ne görüyorsun?" and recorded intent=none with no query kind at all
    (owner M17 run, 2026-09-05). Two tables that must agree will not.

    The import is local because ``classify`` imports THIS module for
    ``normalize_transcript``; a module-level import would be circular.

    ``matched`` matters: ``classify`` answers every input, defaulting to last-activity.
    That default is a reasonable answer to a question and a bad reason to decide something
    WAS a question, so only a real pattern match counts here.
    """
    from app.explain.classify import classify

    query = classify(text or " ".join(tokens))
    if not query.matched:
        return None
    required = _INTENT_CORROBORATION.get(query.kind)
    if required is not None and not any(_has(tokens, stem) for stem in required):
        return None
    return query.kind


#: Exact inflected forms, not stems — "göz" as a startswith-stem would also match
#: "gözlük" (glasses) and "gözlem" (observation), unrelated words that happen to
#: share the root. A privacy-critical trigger is worth the extra explicit forms
#: rather than a prefix match that fires on the wrong noun.
_EYE_WORD_FORMS: Final[tuple[str, ...]] = ("göz", "gözü", "gözünü", "gözler", "gözlerini")
_CAMERA_WORD_FORMS: Final[tuple[str, ...]] = (
    "kamera",
    "kamerayı",
    "kameramı",
    "kamerasını",
    "kameraları",
)


def _eye_disable_match(tokens: tuple[str, ...]) -> str | None:
    """``Gözünü kapat`` / ``Kamerayı kapat`` / ``Beni izleme`` (M18 spec §2).

    Uses the SAME token/stem primitives (``_has_exact``) as every other intent
    in this file — there is deliberately no second Turkish pattern table for
    this. The task brief is explicit about why: a second table already
    drifted from this one twice (see ``_explain_kind``'s docstring), and the
    Active Eye's disable phrases are exactly the kind of privacy-critical
    command that must never live somewhere it could silently fall out of
    sync. Exact forms, not ``_has`` stems, on purpose (see the word-form
    comments above).
    """
    if _has_exact(tokens, *_EYE_WORD_FORMS) and _has_exact(tokens, "kapat"):
        return "gözünü kapat"
    if _has_exact(tokens, *_CAMERA_WORD_FORMS) and _has_exact(tokens, "kapat"):
        return "kamerayı kapat"
    if _has_exact(tokens, "beni") and _has_exact(tokens, "izleme"):
        return "beni izleme"
    return None


#: The imperative "open" forms. Exact, like the eye/camera nouns: "açık" (open, adj.) is
#: the QUERY "kamera açık mı?" and must not become an action; "açar mısın" is a request
#: and is honoured as one.
_OPEN_VERB_FORMS: Final[tuple[str, ...]] = ("aç", "açsana", "açar")
#: "Active Eye'ı aç" — the product name, as the ASR renders it (the apostrophe survives
#: normalisation, so the stem match on "eye" is the honest way to catch "eye'ı"/"eye'i").
_ACTIVE_EYE_FORMS: Final[tuple[str, ...]] = ("active", "aktif")


def _eye_enable_match(tokens: tuple[str, ...]) -> str | None:
    """``Gözünü aç`` / ``Kamerayı aç`` / ``Beni izle`` / ``Beni tekrar izle`` /
    ``Gözünü tekrar aç`` / ``Active Eye'ı aç`` (docs/M18_ACTION_CONTRACT.md §2).

    Built on the same word forms as :func:`_eye_disable_match`, and evaluated AFTER
    it, so "beni izleme" (the negative imperative: do not watch me) stays a disable and
    "beni izle" (watch me) is an enable — the two differ by one suffix and the privacy
    direction must win a tie.
    """
    if _has_exact(tokens, *_EYE_WORD_FORMS) and _has_exact(tokens, *_OPEN_VERB_FORMS):
        return "gözünü aç"
    if _has_exact(tokens, *_CAMERA_WORD_FORMS) and _has_exact(tokens, *_OPEN_VERB_FORMS):
        return "kamerayı aç"
    if (
        _has_exact(tokens, *_ACTIVE_EYE_FORMS)
        and _has(tokens, "eye")
        and _has_exact(tokens, *_OPEN_VERB_FORMS)
    ):
        return "active eye'ı aç"
    if _has_exact(tokens, "beni") and _has_exact(tokens, "izle"):
        return "beni izle"
    return None


#: "Canlıya al." / "Yayına al." as imperatives (contract §2). Exact verb forms: "alabilir"
#: is the question, and the question stays a can_deploy QUERY answered from policy.
_PROMOTE_TARGETS: Final[tuple[str, ...]] = ("canlıya", "canliya", "yayına", "yayina")
_TAKE_VERB_FORMS: Final[tuple[str, ...]] = ("al", "alsana")


def _deploy_match(tokens: tuple[str, ...]) -> str | None:
    target = _has_exact(tokens, *_PROMOTE_TARGETS)
    if target and _has_exact(tokens, *_TAKE_VERB_FORMS):
        return "yayına al" if target.startswith("yay") else "canlıya al"
    return None


# ------------------------------------------------- research interaction classes

#: A research word in any Turkish inflection: "araştır", "araştırma", "araştırmayı",
#: "araştırmasını". A prefix stem is right here (unlike the eye nouns) because every
#: word that starts with "araştır" IS about researching - there is no unrelated Turkish
#: word sharing that prefix the way "gözlük" shares "göz".
_RESEARCH_STEMS: Final[tuple[str, ...]] = ("araştır", "arastir", "research")

#: "Do it again": the words that make a research request a RE-RUN rather than a question
#: about the run that finished.
_RERUN_WORDS: Final[tuple[str, ...]] = ("yeniden", "tekrar", "baştan", "bastan")

#: The imperative that actually asks for a crawl. Exact forms: "araştırmayı tekrar
#: ANLAT" is a question about the finished run, not an order to run it again, and the
#: difference is exactly this verb (ADR-0075: the whole defect is one utterance class
#: being read as another).
_RESEARCH_IMPERATIVES: Final[tuple[str, ...]] = ("araştır", "arastir", "araştırsana")
_RUN_VERB_FORMS: Final[tuple[str, ...]] = ("yap", "yapar", "başlat", "baslat", "çalıştır")

#: Words that make an utterance a question about the PIPELINE (technical/diagnostic)
#: rather than about the findings. Mirrors the two diagnostic query kinds
#: app.explain.classify already owns (research_problems / rejected_pages) - it does not
#: restate them: those kinds ride on ``query_kind`` and are consulted here directly.
_RESEARCH_PROBLEM_WORDS: Final[tuple[str, ...]] = ("sorun", "hata", "problem")

#: Words that make an utterance a follow-up ON the findings of the finished run.
_RESEARCH_FOLLOWUP_STEMS: Final[tuple[str, ...]] = (
    "kaynak",  # "Kaynakları söyle."
    "bulgu",  # "Birinci bulguyu detaylandır."
    "sonuç",  # "Sonuçları anlat."
    "sonuc",
    "detay",  # "detaylandır"
    "ayrıntı",
    "ayrinti",
    "özet",
    "ozet",
    "kısaca",
)
_RESEARCH_TELLING_VERBS: Final[tuple[str, ...]] = ("anlat", "söyle", "soyle", "oku", "aktar")


def classify_research_interaction(
    tokens: tuple[str, ...],
    *,
    has_completed_research: bool,
    query_kind: str | None = None,
) -> str | None:
    """Which of the four research interaction classes this utterance is, or None.

    Pure: the utterance's tokens, whether a COMPLETED research context exists, and the
    query kind the one question table (``app.explain.classify``) already decided. No
    database, no session, no second Turkish table.

    Order is the whole point, and it is the owner's own rule (ADR-0075):

    1. an explicit re-run ("araştırmayı yeniden yap", "tekrar araştır") is a RETRY, and
       a retry may crawl;
    2. a research imperative on a topic ("... gelişmelerini araştır") is NEW_RESEARCH,
       and it may crawl;
    3. with a completed research to answer from, a pipeline question ("teknik anlat",
       "hangi sayfalar elendi", "araştırma sırasında ne sorun oldu") is a
       TECHNICAL_EXPLANATION, and it may NOT crawl;
    4. with a completed research to answer from, a question about the findings
       ("kaynakları söyle", "birinci bulguyu detaylandır", "neden önemli") is a
       FOLLOWUP, and it may NOT crawl.

    Classes 3 and 4 exist only when there IS a completed research: without one,
    "teknik anlat" is an ordinary technical explanation of the last activity and has
    nothing to bind to.
    """
    research_word = _has(tokens, *_RESEARCH_STEMS)
    rerun_word = _has_exact(tokens, *_RERUN_WORDS)
    imperative = _has_exact(tokens, *_RESEARCH_IMPERATIVES)
    run_verb = _has_exact(tokens, *_RUN_VERB_FORMS)

    # 1. RETRY - "again" plus an order to RUN it, never merely "again" plus the word
    #    research ("araştırmayı tekrar anlat" is a follow-up, not a re-run).
    if rerun_word and (imperative or (research_word and run_verb)):
        return RESEARCH_CLASS_RETRY
    # 2. NEW - the research imperative, or "araştırma yap/başlat", with no "again".
    if imperative or (research_word and run_verb):
        return RESEARCH_CLASS_NEW
    if not has_completed_research:
        return None
    # 3. TECHNICAL EXPLANATION - the pipeline's own diagnostics.
    if query_kind in ("rejected_pages", "research_problems"):
        return RESEARCH_CLASS_TECHNICAL_EXPLANATION
    if _has(tokens, "teknik") or (_has(tokens, "kod") and _has(tokens, "seviye")):
        return RESEARCH_CLASS_TECHNICAL_EXPLANATION
    if _has(tokens, "elendi", "elen") or (_has(tokens, "hangi") and _has(tokens, "sayfa")):
        return RESEARCH_CLASS_TECHNICAL_EXPLANATION
    if research_word and _has(tokens, *_RESEARCH_PROBLEM_WORDS):
        return RESEARCH_CLASS_TECHNICAL_EXPLANATION
    # 4. FOLLOW-UP - the findings themselves.
    if query_kind == "research_detail":
        return RESEARCH_CLASS_FOLLOWUP
    if _has(tokens, *_RESEARCH_FOLLOWUP_STEMS):
        return RESEARCH_CLASS_FOLLOWUP
    if _has(tokens, "neden") and _has(tokens, "önemli", "onemli"):
        return RESEARCH_CLASS_FOLLOWUP
    if research_word and _has(tokens, *_RESEARCH_TELLING_VERBS):
        return RESEARCH_CLASS_FOLLOWUP
    return None


def research_class_for(text: str, *, has_completed_research: bool) -> str | None:
    """:func:`classify_research_interaction` from raw speech (normalises first).

    The sibling entry point for callers that hold an utterance rather than a resolved
    intent; ``resolve_intent`` attaches the same value to every ``ResolvedIntent``.
    """
    _normalized, tokens, _dropped = normalize_transcript(text)
    if not tokens:
        return None
    return classify_research_interaction(
        tokens,
        has_completed_research=has_completed_research,
        query_kind=_explain_kind(tokens, _normalized),
    )


def _stop_match(text: str, tokens: tuple[str, ...]) -> str | None:
    for phrase in _MULTI_STOP_PHRASES:
        if re.search(rf"(?<!\S){re.escape(phrase)}(?!\S)", text):
            return phrase
    return _has_exact(tokens, *STOP_TOKENS)


def _scope_for(intent: Intent, narration: NarrationState | None) -> str:
    """Narration owns the intent when there is a narration in a non-idle state."""
    if narration is not None and narration.state != State.IDLE:
        return SCOPE_NARRATION
    if narration is not None and intent in (
        Intent.REPEAT_ITEM,
        Intent.NEXT_ITEM,
        Intent.PREVIOUS_ITEM,
        Intent.FIRST_ITEM,
        Intent.LAST_ITEM,
        Intent.NEXT_SECTION,
        Intent.SKIP,
    ):
        return SCOPE_NARRATION
    return SCOPE_CONVERSATION


def resolve_intent(
    text: str,
    *,
    session_state: RealtimeState | None = None,
    narration: NarrationState | None = None,
    has_completed_research: bool = False,
) -> ResolvedIntent:
    """Resolve a transcript into an :class:`Intent` against the live state.

    ``session_state`` is the M4 control FSM state of the conversation;
    ``narration`` the narration machine state when a narration is attached.
    Stop words win from ANY state (spec §5); everything else is resolved in
    a fixed priority order documented inline.

    ``has_completed_research`` is the one piece of durable context this resolver takes:
    whether a COMPLETED research exists for the owner (ADR-0075). It decides nothing
    about the intent; it decides whether "teknik anlat" is additionally a
    ``research_technical_explanation`` (a question about a finished run) or just a
    technical explanation of the last activity. The caller establishes it from the
    research runs/reports - the resolver stays pure.
    """
    normalized, tokens, dropped = normalize_transcript(text)
    confidence = 1.0 if dropped == 0 else 0.9
    base: dict[str, Any] = {
        "normalized_text": normalized,
        "tokens": tokens,
        "fillers_removed": dropped,
        "confidence": confidence,
    }
    if not tokens:
        return ResolvedIntent(Intent.NONE, **{**base, "confidence": 0.0})

    # The question table is consulted ONCE, here, and its answer serves both the
    # research interaction class (below) and the EXPLAIN branch further down - the two
    # can therefore never disagree about what kind of question was asked.
    explain_kind = _explain_kind(tokens, normalized)
    base["research_class"] = classify_research_interaction(
        tokens, has_completed_research=has_completed_research, query_kind=explain_kind
    )

    # 0. Active Eye privacy stop (M18 spec §2) — checked before even STOP. A camera
    #    disable phrase must never be shadowed by anything this resolver learns
    #    later, in any state, including mid-narration or mid-tool-call.
    if eye_matched := _eye_disable_match(tokens):
        return ResolvedIntent(
            Intent.EYE_DISABLE, scope=SCOPE_CONVERSATION, matched=eye_matched, **base
        )
    # 0b. The enable path (contract §2). Same place, same primitives, evaluated second so
    #     the disable direction wins whenever both could read.
    if eye_matched := _eye_enable_match(tokens):
        return ResolvedIntent(
            Intent.EYE_ENABLE, scope=SCOPE_CONVERSATION, matched=eye_matched, **base
        )

    # 1. stop — top priority in any state, including TOOL_RUNNING progress.
    stop = _stop_match(normalized, tokens)
    if stop:
        return ResolvedIntent(
            Intent.STOP, scope=_scope_for(Intent.STOP, narration), matched=stop, **base
        )

    # 1a. "Canlıya al." — an action the policy always refuses, but an ACTION: it goes to
    #     release.promote and comes back as a refused receipt, so the refusal is evidence
    #     (contract §2). Checked before the question classifier so the imperative can never
    #     be softened into the can_deploy question.
    if deploy_matched := _deploy_match(tokens):
        return ResolvedIntent(
            Intent.DEPLOY, scope=SCOPE_CONVERSATION, matched=deploy_matched, **base
        )

    # 1b. questions about the system's own activity resolve BEFORE presentation words,
    #     because "araştırmayı detaylandır" with no briefing open is a request for one,
    #     while the same words with a briefing attached are a jump into its detail section.
    if (
        explain_kind is not None
        and not (narration is not None and explain_kind in ("research_detail", "technical"))
        and not _full_read(tokens)
    ):
        return ResolvedIntent(
            Intent.EXPLAIN,
            scope=SCOPE_CONVERSATION,
            matched=explain_kind,
            query_kind=explain_kind,
            **base,
        )

    # 2. speed
    if tok := _has(tokens, "yavaş"):
        return ResolvedIntent(Intent.SLOWER, scope=SCOPE_NARRATION, matched=tok, **base)
    if tok := _has(tokens, "hızlı", "hızlan"):
        return ResolvedIntent(Intent.FASTER, scope=SCOPE_NARRATION, matched=tok, **base)

    # 3. presentation level. Surface wording varies ("detaylandır", "ayrıntı ver", "daha
    #    detaylı anlat"; "teknik detaya gir", "kod seviyesinde anlat"); the durable record
    #    carries the normalised intent, never the wording. The full-read phrases outrank the
    #    level words, and "teknik" outranks "detay" so "teknik detaya gir" is technical.
    if _full_read(tokens):
        return ResolvedIntent(
            Intent.FULL, scope=_scope_for(Intent.FULL, narration), matched="full", **base
        )
    if tok := _has(tokens, "özet", "kısaca", "kısa"):
        return ResolvedIntent(
            Intent.SUMMARIZE, scope=_scope_for(Intent.SUMMARIZE, narration), matched=tok, **base
        )
    if (tok := _has(tokens, "teknik")) or (_has(tokens, "kod") and _has(tokens, "seviye")):
        return ResolvedIntent(
            Intent.TECHNICAL,
            scope=_scope_for(Intent.TECHNICAL, narration),
            matched=tok or "kod seviyesinde",
            **base,
        )
    if tok := _has(tokens, "detay", "ayrıntı", "derinle"):
        return ResolvedIntent(
            Intent.DETAIL, scope=_scope_for(Intent.DETAIL, narration), matched=tok, **base
        )

    # 4. skip — "burayı atla", "bunu atla", "bunu geç", "burayı geç"
    if tok := _has(tokens, "atla"):
        return ResolvedIntent(
            Intent.SKIP, scope=_scope_for(Intent.SKIP, narration), matched=tok, **base
        )
    if _has_exact(tokens, "geç") and _has_exact(tokens, "bunu", "burayı", "şunu", "burası"):
        return ResolvedIntent(
            Intent.SKIP, scope=_scope_for(Intent.SKIP, narration), matched="geç", **base
        )

    # 5. item / section navigation
    item_noun = _has(tokens, *_ITEM_NOUNS)
    if item_noun:
        is_section = any(item_noun.startswith(n) for n in _SECTION_NOUNS)
        if _has(tokens, "sonraki", "bir sonraki", "diğer"):
            intent = Intent.NEXT_SECTION if is_section else Intent.NEXT_ITEM
            return ResolvedIntent(intent, scope=SCOPE_NARRATION, matched=item_noun, **base)
        if _has(tokens, "önceki", "evvelki"):
            if _has(tokens, "açıkla", "anlat"):
                return ResolvedIntent(
                    Intent.EXPLAIN_PREVIOUS, scope=SCOPE_NARRATION, matched=item_noun, **base
                )
            return ResolvedIntent(
                Intent.PREVIOUS_ITEM, scope=SCOPE_NARRATION, matched=item_noun, **base
            )
        if _has_exact(tokens, "son", "sonuncu"):
            return ResolvedIntent(
                Intent.LAST_ITEM, scope=SCOPE_NARRATION, matched=item_noun, **base
            )
        for tok in tokens:
            if tok in _ORDINALS:
                n = _ORDINALS[tok]
                if n == 1 and tok == "ilk":
                    return ResolvedIntent(
                        Intent.FIRST_ITEM,
                        scope=SCOPE_NARRATION,
                        target_index=1,
                        matched=tok,
                        **base,
                    )
                return ResolvedIntent(
                    Intent.REPEAT_ITEM, scope=SCOPE_NARRATION, target_index=n, matched=tok, **base
                )
        if _has(tokens, "tekrar", "yeniden"):
            return ResolvedIntent(
                Intent.REPEAT, scope=_scope_for(Intent.REPEAT, narration), matched=item_noun, **base
            )

    # 6. repeat / resume
    if tok := _has(tokens, "tekrar", "yeniden"):
        return ResolvedIntent(
            Intent.REPEAT, scope=_scope_for(Intent.REPEAT, narration), matched=tok, **base
        )
    if (
        _has_exact(tokens, "daha")
        and _has_exact(tokens, "bir")
        and _has_exact(tokens, "oku", "söyle")
    ):
        return ResolvedIntent(
            Intent.REPEAT, scope=_scope_for(Intent.REPEAT, narration), matched="bir daha", **base
        )
    if tok := _has(tokens, "devam", "sürdür"):
        return ResolvedIntent(
            Intent.RESUME, scope=_scope_for(Intent.RESUME, narration), matched=tok, **base
        )
    if _has_exact(tokens, "kaldığın", "kaldığımız") and _has(tokens, "yer"):
        return ResolvedIntent(
            Intent.RESUME,
            scope=_scope_for(Intent.RESUME, narration),
            matched="kaldığın yerden",
            **base,
        )

    return ResolvedIntent(Intent.NONE, **{**base, "confidence": 0.0})


# ------------------------------------------------------------------ bridge


@dataclass(frozen=True, slots=True)
class NarrationBridgeResult:
    """The narration-side effect of an intent (pure)."""

    state: NarrationState
    action: str
    ok: bool = True
    message: str | None = None
    presentation: str | None = None  # "summary" | "detail" when changed
    speed: float | None = None  # new speed when changed
    cursor: Cursor | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "ok": self.ok,
            "message": self.message,
            "presentation": self.presentation,
            "speed": self.speed,
            "cursor": self.cursor.as_dict() if self.cursor else None,
            "narration_state": self.state.state.value,
            **self.extra,
        }


def level_section_cursor(plan: NarrationPlan, level: str) -> Cursor | None:
    """Cursor at the first content chunk of the section that carries ``level``
    (an activity briefing's "Özet" / "Ayrıntı" / "Teknik"), or None when the
    document has no such section."""
    titles = LEVEL_SECTION_TITLES.get(level, ())
    for section in plan.sections:
        folded = turkish_casefold(section.title).strip()
        if not any(folded.startswith(t) for t in titles):
            continue
        for ch in plan.chunks:
            if ch.cursor.section_id != section.id:
                continue
            para = plan.paragraphs.get(ch.cursor.paragraph_id)
            if para is not None and para.kind == PARAGRAPH_HEADING:
                continue
            return ch.cursor
    return None


#: Spoken budgets per presentation level (M16 owner UX result 2026-09-04): narration is for
#: listening, not document reading. Roughly 10-20 s for an executive answer, 30-60 s for the
#: detail, a concise technical briefing; only "hepsini oku" lifts the budget. Chunks end at
#: sentence boundaries and the cursor keeps the position, so "devam et" reads the next chunk.
SPEECH_BUDGET_CHARS: dict[str, int] = {
    PRESENTATION_SUMMARY: 420,
    PRESENTATION_DETAIL: 900,
    PRESENTATION_TECHNICAL: 700,
    PRESENTATION_FULL: 6000,
}


def speech_budget(level: str | None) -> int:
    default = SPEECH_BUDGET_CHARS[PRESENTATION_SUMMARY]
    return SPEECH_BUDGET_CHARS.get(level or PRESENTATION_SUMMARY, default)


def speech_from(
    plan: NarrationPlan, cursor: Cursor | None, *, whole_section: bool = True, max_chars: int = 6000
) -> str:
    """The text to speak from ``cursor``: every chunk up to the end of its section
    (or the document when ``whole_section`` is False), headings skipped. This is what
    lets the realtime provider resume at the exact sentence after "devam"."""
    if not plan.chunks:
        return ""
    start = plan.index_of(cursor)
    section_id = plan.chunks[start].cursor.section_id if cursor is None else cursor.section_id
    parts: list[str] = []
    total = 0
    for ch in plan.chunks[start:]:
        if whole_section and ch.cursor.section_id != section_id:
            break
        para = plan.paragraphs.get(ch.cursor.paragraph_id)
        if para is not None and para.kind == PARAGRAPH_HEADING:
            continue
        text = ch.text.strip()
        if not text:
            continue
        if parts and total + len(text) > max_chars:
            break  # the budget ends at a sentence boundary; the cursor reads on from here
        parts.append(text)
        total += len(text) + 1
    return " ".join(parts)


def ordered_paragraph_ids(plan: NarrationPlan, section_id: str | None = None) -> list[str]:
    """Content items in document order. A "madde" is something the owner
    hears as an item: headings are navigation structure, not items, so
    "ikinci madde" is the second content paragraph, not the second block.

    With ``section_id``, only that section's items: while a briefing's "Ayrıntı" is
    being read, "ikinci madde" is its second finding, not the second paragraph of the
    whole document (M16). A section with no items falls back to the document."""
    ordered: list[str] = []
    for ch in plan.chunks:
        if section_id is not None and ch.cursor.section_id != section_id:
            continue
        pid = ch.cursor.paragraph_id
        para = plan.paragraphs.get(pid)
        if para is not None and para.kind == PARAGRAPH_HEADING:
            continue
        if pid not in ordered:
            ordered.append(pid)
    if section_id is not None and not ordered:
        return ordered_paragraph_ids(plan)
    return ordered


def _item_cursors(plan: NarrationPlan, cursor: Cursor | None) -> list[Cursor]:
    """Where each item the owner might name begins.

    When the section being read carries a list, a "madde" is one of ITS entries - the
    plan keeps a list as one paragraph with one chunk per entry, so the items are that
    paragraph's chunks (a briefing's numbered findings). Otherwise the M4/ADR-0036 rule
    stands: items are the document's content paragraphs in order, each starting at its
    first chunk."""
    if cursor is not None:
        listed = [
            ch.cursor
            for ch in plan.chunks
            if ch.cursor.section_id == cursor.section_id
            and (para := plan.paragraphs.get(ch.cursor.paragraph_id)) is not None
            and para.kind == PARAGRAPH_LIST
        ]
        if listed:
            return listed
    starts: list[Cursor] = []
    for pid in ordered_paragraph_ids(plan):
        first = plan.paragraph_start_cursor(pid)
        if first is not None:
            starts.append(first)
    return starts


def _same_item(a: Cursor, b: Cursor, *, by_sentence: bool) -> bool:
    if a.section_id != b.section_id or a.paragraph_id != b.paragraph_id:
        return False
    return a.sentence_index == b.sentence_index if by_sentence else True


def current_item_index(plan: NarrationPlan, cursor: Cursor | None) -> int:
    """1-based index of the item the cursor is in (0 when no cursor or when the
    cursor sits on a heading)."""
    if cursor is None:
        return 0
    items = _item_cursors(plan, cursor)
    by_sentence = _is_list_mode(plan, items)
    for n, item in enumerate(items, start=1):
        if _same_item(item, cursor, by_sentence=by_sentence):
            return n
    return 0


def _is_list_mode(plan: NarrationPlan, items: list[Cursor]) -> bool:
    if not items:
        return False
    para = plan.paragraphs.get(items[0].paragraph_id)
    return para is not None and para.kind == PARAGRAPH_LIST


def _jump_to_item(
    state: NarrationState, plan: NarrationPlan, n: int, *, action: str
) -> NarrationBridgeResult:
    """Same state shape as the M4 ``MADDEYE_GEC`` transition, over content items."""
    items = _item_cursors(plan, state.cursor)
    if n < 1 or n > len(items):
        return NarrationBridgeResult(
            state=state,
            action="jump_failed",
            ok=False,
            message="Böyle bir madde yok.",
            cursor=state.cursor,
        )
    target = items[n - 1]
    new_state = replace(state, state=State.READING, cursor=target, paragraph_anchor=target)
    return NarrationBridgeResult(state=new_state, action=action, cursor=target)


def _via_commands(
    state: NarrationState, parsed: ParsedCommand, plan: NarrationPlan, *, action: str | None = None
) -> NarrationBridgeResult:
    res = commands.apply(state, parsed, plan)
    return NarrationBridgeResult(
        state=res.state,
        action=action or res.action,
        ok=res.ok,
        message=res.message,
        cursor=res.state.cursor,
    )


def apply_to_narration(
    resolved: ResolvedIntent, state: NarrationState, plan: NarrationPlan
) -> NarrationBridgeResult:
    """Translate a resolved intent into narration cursor / speed operations.

    Delegates every state change to the M4 command machine so its invariants
    hold ("dur" always wins and never errors; explain-then-return is intact).
    """
    intent = resolved.intent
    if intent == Intent.STOP:
        return _via_commands(state, ParsedCommand(Command.DUR), plan)
    if intent == Intent.RESUME:
        return _via_commands(state, ParsedCommand(Command.DEVAM), plan)
    if intent == Intent.REPEAT:
        return _via_commands(state, ParsedCommand(Command.TEKRAR), plan)
    if intent == Intent.REPEAT_ITEM:
        return _jump_to_item(state, plan, resolved.target_index or 1, action="jump_item")
    if intent == Intent.FIRST_ITEM:
        return _jump_to_item(state, plan, 1, action="jump_item")
    if intent == Intent.LAST_ITEM:
        return _jump_to_item(
            state, plan, len(_item_cursors(plan, state.cursor)), action="jump_item"
        )
    if intent in (Intent.NEXT_ITEM, Intent.SKIP):
        current = current_item_index(plan, state.cursor)
        res = _jump_to_item(
            state, plan, current + 1, action="skipped" if intent == Intent.SKIP else "jump_item"
        )
        if not res.ok:
            return replace(
                res, action="end_of_document", message="Atlanacak bir sonraki madde yok."
            )
        return res
    if intent == Intent.PREVIOUS_ITEM:
        current = current_item_index(plan, state.cursor)
        return _jump_to_item(state, plan, max(1, current - 1), action="jump_item")
    if intent == Intent.NEXT_SECTION:
        return _via_commands(state, ParsedCommand(Command.SONRAKI_BOLUM), plan)
    if intent in (Intent.SLOWER, Intent.FASTER):
        delta = -SPEED_STEP if intent == Intent.SLOWER else SPEED_STEP
        res = _via_commands(state, ParsedCommand(Command.HIZ, speed=state.speed + delta), plan)
        return replace(res, speed=res.state.speed)
    if intent == Intent.FULL:
        # Read everything from the top: the caller lifts the budget (presentation == full);
        # the cursor starts at the first content chunk.
        start = plan.chunks[0].cursor if plan.chunks else None
        new_state = replace(
            state, state=State.READING, cursor=start, paragraph_anchor=start, saved_cursor=None
        )
        return NarrationBridgeResult(
            state=new_state, action="read_all", presentation=PRESENTATION_FULL, cursor=start
        )
    if intent in (Intent.SUMMARIZE, Intent.DETAIL, Intent.TECHNICAL):
        level = {
            Intent.SUMMARIZE: PRESENTATION_SUMMARY,
            Intent.DETAIL: PRESENTATION_DETAIL,
            Intent.TECHNICAL: PRESENTATION_TECHNICAL,
        }[intent]
        # A briefing carries its levels as sections: move the cursor there, so what is
        # spoken next IS the requested level. A plain document has no such section and
        # only the presentation flag changes, exactly as before.
        target = level_section_cursor(plan, level)
        if target is not None:
            new_state = replace(
                state,
                state=State.READING,
                cursor=target,
                paragraph_anchor=target,
                saved_cursor=None,
            )
            return NarrationBridgeResult(
                state=new_state, action="jump_level", presentation=level, cursor=target
            )
        return NarrationBridgeResult(
            state=state, action="presentation_changed", presentation=level, cursor=state.cursor
        )
    if intent == Intent.EXPLAIN_PREVIOUS:
        # Explain-then-return (M4 invariant): the EXACT current cursor is saved, the
        # previous item is read, and "devam" comes back to where the owner was.
        current = current_item_index(plan, state.cursor)
        jumped = _jump_to_item(state, plan, max(1, current - 1), action="explain_previous")
        if not jumped.ok:
            return jumped
        explaining = replace(jumped.state, state=State.EXPLAINING, saved_cursor=state.cursor)
        return replace(jumped, state=explaining, cursor=explaining.cursor)
    return NarrationBridgeResult(
        state=state, action="noop", ok=False, message="Bilinmeyen komut.", cursor=state.cursor
    )


__all__ = [
    "CAPABILITY_BY_INTENT",
    "FILLERS",
    "KLASS_ACTION",
    "KLASS_CONTROL",
    "KLASS_QUERY",
    "LEVEL_SECTION_TITLES",
    "PRESENTATION_DETAIL",
    "PRESENTATION_FULL",
    "PRESENTATION_SUMMARY",
    "PRESENTATION_TECHNICAL",
    "RESEARCH_CLASSES",
    "RESEARCH_CLASSES_BOUND_TO_A_RUN",
    "RESEARCH_CLASSES_MAY_CRAWL",
    "RESEARCH_CLASS_FOLLOWUP",
    "RESEARCH_CLASS_NEW",
    "RESEARCH_CLASS_RETRY",
    "RESEARCH_CLASS_TECHNICAL_EXPLANATION",
    "SPEECH_BUDGET_CHARS",
    "SCOPE_CONVERSATION",
    "SCOPE_NARRATION",
    "SPEED_STEP",
    "STOP_TOKENS",
    "Intent",
    "NarrationBridgeResult",
    "ResolvedIntent",
    "apply_to_narration",
    "classify_research_interaction",
    "current_item_index",
    "is_filler",
    "klass_for",
    "level_section_cursor",
    "normalize_transcript",
    "ordered_paragraph_ids",
    "research_class_for",
    "resolve_intent",
    "speech_budget",
    "speech_from",
    "turkish_casefold",
]
