"""TTS -> STT loopback proxy qualification (VOICE_SPEC §7a; owner directive 2026-09-07).

The owner's directive asks, for every milestone's synthetic owner-command corpus, a
RESPONSE test: the assistant's spoken answer synthesised, captured on an ISOLATED path,
transcribed, and compared semantically with the text that was meant to be spoken. This
module is that loop, provider-agnostic and pure:

    expected speech --TTS--> WAV bytes --STT--> transcript
                                  |
                  Turkish normalisation of both sides (the router's own rules)
                                  |
             word error rate + a content-word check -> matched / degraded / mismatched

The "controlled virtual/loopback capture" is the byte path itself: the synthesised bytes
are handed straight to the recogniser. No loudspeaker, no microphone, no room, no
feedback loop - and therefore no claim about hearing. The marks say exactly that:

    audio_generation    PROVEN_AUTOMATED  a real provider produced audio for every case
                        PROVEN_PROXY      the deterministic fakes did
    loopback_semantics  PROVEN_PROXY      always - an isolated byte path, not a room
    physical_hearing    NOT_CLAIMED       always - only the owner's ear can prove it

Nothing here reads a key, opens a socket or picks a provider; the caller hands in the
two providers (``app.voice.providers``) and the module's own tests use the fakes.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.voice.benchmark import levenshtein, word_error_rate
from app.voice.errors import VoiceError
from app.voice.intents import normalize_transcript
from app.voice.providers import (
    FakeSTTProvider,
    FakeTTSProvider,
    STTProvider,
    TTSProvider,
    is_wav,
    wav_duration_ms,
)

SCHEMA_VERSION = "1.0"
SUITE = "TtsLoopbackProxy"

# ------------------------------------------------------------------- verdicts

VERDICT_MATCHED = "matched"
VERDICT_DEGRADED = "degraded"
VERDICT_MISMATCHED = "mismatched"
VERDICT_ERROR = "error"
VERDICTS = (VERDICT_MATCHED, VERDICT_DEGRADED, VERDICT_MISMATCHED, VERDICT_ERROR)

#: WER at or under which a case is ``matched`` (with every content word present).
WER_MATCHED = 0.35
#: WER at or under which a case is ``degraded`` rather than ``mismatched``.
WER_DEGRADED = 0.60
#: A content word is an expected token of at least this many characters that is not a
#: stop word; each must appear in the transcript within ``CONTENT_MAX_EDITS`` edits.
CONTENT_MIN_CHARS = 4
CONTENT_MAX_EDITS = 1

DEFAULT_MAX_CASES = 60
DEFAULT_SEED = 20260907

# ---------------------------------------------------------------------- marks

MARK_PROVEN_AUTOMATED = "PROVEN_AUTOMATED"
MARK_PROVEN_PROXY = "PROVEN_PROXY"
MARK_NOT_PROVEN = "NOT_PROVEN"
MARK_NOT_CLAIMED = "NOT_CLAIMED"

LOOPBACK_SEMANTICS_REASON = (
    "an isolated in-process byte path: the synthesised bytes are handed straight to the "
    "recogniser - no loudspeaker, no microphone, no room, no feedback loop. The match "
    "proves the spoken text survives synthesis and recognition; it proves nothing about "
    "what a person hears."
)
PHYSICAL_HEARING_REASON = (
    "never claimed by this harness: only the owner's ear, on the owner's device, can prove "
    "it (docs/OWNER_ACTIONS.md audio items)."
)

# ------------------------------------------------------------- normalisation

_DIACRITICS = str.maketrans("çğıöşüâîû", "cgiosuaiu")

#: Turkish function words of CONTENT_MIN_CHARS+ characters that carry no content of their
#: own (diacritics stripped, as compared). A recogniser dropping one of these is a WER
#: matter, not a semantic miss.
STOP_WORDS: frozenset[str] = frozenset(
    {
        "icin", "gibi", "daha", "ancak", "simdi", "sonra", "once", "kadar", "degil",
        "olan", "olarak", "bunu", "bunun", "bunlar", "sunu", "sunun", "sunlar", "onun",
        "onlar", "sana", "bana", "size", "sizin", "senin", "benim", "bizim", "hangi",
        "nasil", "neden", "veya", "yani", "hemen", "artik", "biraz", "boyle", "soyle",
        "oyle", "burada", "orada", "surada", "butun", "hepsi", "baska", "diger", "ayni",
        "henuz", "zaten", "tabii", "elbette", "tamam", "evet", "hayir", "lutfen", "peki",
        "yoksa", "fakat", "cunku", "olsun", "oldu", "olur", "olacak", "misin", "musun",
        "musunuz", "misiniz", "seyi", "seyler", "birlikte", "sadece", "yalnizca", "hakkinda",
        "uzerine", "kendi", "burasi", "orasi", "boylece", "ayrica", "iste", "hani",
        "suan", "simdilik", "bile", "hala", "yine", "gene",
    }
)

_WS = re.compile(r"\s+")


def strip_diacritics(text: str) -> str:
    return text.translate(_DIACRITICS)


def normalize_for_comparison(text: str) -> tuple[str, tuple[str, ...]]:
    """The comparison form of a spoken text: the router's own transcript normalisation
    (tr-TR numerals to words, Turkish casefold, punctuation and fillers gone), then
    diacritics folded so "araştırma" and an ASR's "arastirma" are one token.

    Returns ``(joined, tokens)``; both sides of every comparison go through this.
    """
    joined, tokens, _fillers = normalize_transcript(text or "")
    folded = tuple(strip_diacritics(t) for t in tokens if t)
    return " ".join(folded), folded


def content_words(tokens: Sequence[str]) -> tuple[str, ...]:
    """The expected tokens that must survive: long enough to carry meaning, not a stop
    word, each once."""
    seen: list[str] = []
    for tok in tokens:
        if len(tok) >= CONTENT_MIN_CHARS and tok not in STOP_WORDS and tok not in seen:
            seen.append(tok)
    return tuple(seen)


def missing_content_words(
    expected_tokens: Sequence[str], transcript_tokens: Sequence[str]
) -> list[str]:
    """The content words of the expected text with no transcript token within
    ``CONTENT_MAX_EDITS`` edits - and not present once the transcript's spaces are
    removed either (a merged or split word is a spacing difference, not a meaning)."""
    glued = "".join(transcript_tokens)
    missing: list[str] = []
    for word in content_words(expected_tokens):
        if any(levenshtein(word, tok) <= CONTENT_MAX_EDITS for tok in transcript_tokens):
            continue
        if word in glued:
            continue
        missing.append(word)
    return missing


def verdict_for(wer: float, missing: Sequence[str]) -> str:
    if wer <= WER_MATCHED and not missing:
        return VERDICT_MATCHED
    if wer <= WER_DEGRADED:
        return VERDICT_DEGRADED
    return VERDICT_MISMATCHED


# --------------------------------------------------------------------- cases


@dataclass(frozen=True, slots=True)
class LoopbackCase:
    case_id: str
    expected_speech: str
    category: str = "misc"

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "expected_speech": self.expected_speech,
            "category": self.category,
        }


def case_from_dict(raw: dict[str, Any]) -> LoopbackCase | None:
    """A ``LoopbackCase`` from a plain row (a CLI input file); None when it has no text."""
    speech = str(raw.get("expected_speech") or raw.get("speech") or "").strip()
    case_id = str(raw.get("case_id") or "").strip()
    if not speech or not case_id:
        return None
    return LoopbackCase(
        case_id=case_id, expected_speech=speech, category=str(raw.get("category") or "misc")
    )


#: The corpus response classes that carry an answer worth speaking (tests/voice_corpus).
SPOKEN_RESPONSE_CLASSES = frozenset({"ok", "refused"})


def cases_from_corpus_results(rows: Iterable[dict[str, Any]]) -> list[LoopbackCase]:
    """``LoopbackCase``s from a corpus report's ``results`` rows (``CaseResult.as_dict``).

    Keeps the cases the suite judged ``correct`` whose response class is ok / refused and
    whose speech is non-empty; deduplicates identical speech texts (the deterministic
    variants of one utterance - no diacritics, no punctuation - answer with the same
    sentence, and a budgeted run should not synthesise it four times). Sorted by case id
    so the result is stable for the sampler.
    """
    out: list[LoopbackCase] = []
    seen_speech: set[str] = set()
    for row in sorted(rows, key=lambda r: str(r.get("case_id") or "")):
        if row.get("verdict") != "correct":
            continue
        if str(row.get("expected_response") or "") not in SPOKEN_RESPONSE_CLASSES:
            continue
        speech = str(row.get("speech") or "").strip()
        if not speech:
            continue
        key = _WS.sub(" ", speech)
        if key in seen_speech:
            continue
        seen_speech.add(key)
        out.append(
            LoopbackCase(
                case_id=str(row["case_id"]),
                expected_speech=speech,
                category=str(row.get("category") or "misc"),
            )
        )
    return out


def sample_balanced(
    cases: Sequence[LoopbackCase],
    *,
    max_cases: int = DEFAULT_MAX_CASES,
    per_category: int | None = None,
    seed: int = DEFAULT_SEED,
) -> list[LoopbackCase]:
    """A deterministic, category-balanced sample: at most ``max_cases`` in total and at
    most ``per_category`` from any one category, taken round-robin across the categories
    (sorted by name) from a per-category shuffle seeded with ``seed``.

    Round-robin means every category is represented as long as ``max_cases`` is at least
    the number of categories - a milestone's new category is never crowded out by the
    large alarm family.
    """
    if max_cases <= 0:
        return []
    by_category: dict[str, list[LoopbackCase]] = {}
    for case in sorted(cases, key=lambda c: c.case_id):
        by_category.setdefault(case.category, []).append(case)
    rng = random.Random(seed)
    for name in sorted(by_category):
        rng.shuffle(by_category[name])
    cursor = dict.fromkeys(by_category, 0)
    picked: list[LoopbackCase] = []
    while len(picked) < max_cases:
        progressed = False
        for name in sorted(by_category):
            if per_category is not None and cursor[name] >= per_category:
                continue
            bucket = by_category[name]
            if cursor[name] >= len(bucket):
                continue
            picked.append(bucket[cursor[name]])
            cursor[name] += 1
            progressed = True
            if len(picked) >= max_cases:
                break
        if not progressed:
            break
    return picked


# ------------------------------------------------------------------- results


@dataclass(slots=True)
class LoopbackCaseResult:
    case_id: str
    category: str
    expected_speech: str
    transcript: str = ""
    expected_normalized: str = ""
    transcript_normalized: str = ""
    wer: float | None = None
    missing_content: list[str] = field(default_factory=list)
    verdict: str = VERDICT_ERROR
    audio_ms: int = 0
    audio_bytes: int = 0
    audio_format: str = ""
    tts_latency_ms: float = 0.0
    stt_latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "expected_speech": self.expected_speech,
            "transcript": self.transcript,
            "expected_normalized": self.expected_normalized,
            "transcript_normalized": self.transcript_normalized,
            "wer": None if self.wer is None else round(self.wer, 4),
            "missing_content": list(self.missing_content),
            "verdict": self.verdict,
            "audio_ms": self.audio_ms,
            "audio_bytes": self.audio_bytes,
            "audio_format": self.audio_format,
            "tts_latency_ms": round(self.tts_latency_ms, 1),
            "stt_latency_ms": round(self.stt_latency_ms, 1),
            "error": self.error,
        }


@dataclass(slots=True)
class LoopbackReport:
    generated_at: str
    language: str
    tts_provider: str
    stt_provider: str
    tts_is_fake: bool
    stt_is_fake: bool
    cases: list[LoopbackCaseResult]
    seed: int | None = None
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ counts

    def count(self, verdict: str) -> int:
        return sum(1 for c in self.cases if c.verdict == verdict)

    @property
    def totals(self) -> dict[str, int]:
        return {"cases": len(self.cases), **{v: self.count(v) for v in VERDICTS}}

    @property
    def mean_wer(self) -> float | None:
        scored = [c.wer for c in self.cases if c.wer is not None]
        return round(sum(scored) / len(scored), 4) if scored else None

    @property
    def per_category(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for c in self.cases:
            bucket = out.setdefault(
                c.category, {"cases": 0, **dict.fromkeys(VERDICTS, 0), "wers": []}
            )
            bucket["cases"] += 1
            bucket[c.verdict] += 1
            if c.wer is not None:
                bucket["wers"].append(c.wer)
        for bucket in out.values():
            wers = bucket.pop("wers")
            bucket["mean_wer"] = round(sum(wers) / len(wers), 4) if wers else None
        return dict(sorted(out.items()))

    @property
    def audio_stats(self) -> dict[str, Any]:
        with_audio = [c for c in self.cases if c.audio_bytes > 0]
        durations = [c.audio_ms for c in with_audio]
        formats: dict[str, int] = {}
        for c in with_audio:
            formats[c.audio_format or "unknown"] = formats.get(c.audio_format or "unknown", 0) + 1
        tts_lat = [c.tts_latency_ms for c in with_audio]
        stt_lat = [c.stt_latency_ms for c in self.cases if c.transcript_normalized or c.transcript]
        return {
            "cases_with_audio": len(with_audio),
            "total_ms": sum(durations),
            "mean_ms": round(sum(durations) / len(durations)) if durations else 0,
            "min_ms": min(durations) if durations else 0,
            "max_ms": max(durations) if durations else 0,
            "total_bytes": sum(c.audio_bytes for c in with_audio),
            "formats": formats,
            "mean_tts_latency_ms": round(sum(tts_lat) / len(tts_lat), 1) if tts_lat else 0.0,
            "mean_stt_latency_ms": round(sum(stt_lat) / len(stt_lat), 1) if stt_lat else 0.0,
        }

    # ------------------------------------------------------------- marks

    @property
    def marks(self) -> dict[str, dict[str, str]]:
        with_audio = sum(1 for c in self.cases if c.audio_bytes > 0)
        total = len(self.cases)
        if self.tts_is_fake:
            audio_mark = MARK_PROVEN_PROXY
            audio_reason = (
                f"the deterministic fake TTS ({self.tts_provider}) produced the audio: "
                "the loop and its judgement are exercised, no real voice was synthesised."
            )
        elif total and with_audio == total:
            audio_mark = MARK_PROVEN_AUTOMATED
            audio_reason = (
                f"a real provider ({self.tts_provider}) synthesised audio for every one of "
                f"the {total} cases; each is recorded by format, size and duration."
            )
        elif total:
            audio_mark = MARK_NOT_PROVEN
            failed = total - with_audio
            audio_reason = (
                f"a real provider ({self.tts_provider}) produced audio for {with_audio} of "
                f"{total} cases; {failed} failed (named per case under 'error')."
            )
        else:
            audio_mark = MARK_NOT_PROVEN
            audio_reason = "no case was run."
        semantics_reason = LOOPBACK_SEMANTICS_REASON
        if self.stt_is_fake:
            semantics_reason = (
                f"the deterministic fake STT ({self.stt_provider}) recovered the text the "
                "fake TTS encoded; " + semantics_reason
            )
        return {
            "audio_generation": {"mark": audio_mark, "reason": audio_reason},
            "loopback_semantics": {"mark": MARK_PROVEN_PROXY, "reason": semantics_reason},
            "physical_hearing": {"mark": MARK_NOT_CLAIMED, "reason": PHYSICAL_HEARING_REASON},
        }

    @property
    def summary(self) -> str:
        t = self.totals
        if t["cases"] == 0:
            return "NO_CASES"
        if t["error"] or t["mismatched"]:
            return "REGRESSION_FOUND"
        if t["degraded"]:
            return "DEGRADED"
        return "HEALTHY"

    # ------------------------------------------------------------ ledger

    def ledger_detail(self) -> dict[str, Any]:
        """The bounded ``detail_json`` of the ``voice.tts_loopback`` ledger row: counts,
        the mean WER, the synthesis statistics, the provider names, the marks and the ids
        of the cases that failed. No expected text, no transcript, no audio - the ledger's
        forbidden-key scan refuses those shapes, and the evidence file keeps them."""
        totals = self.totals
        stats = self.audio_stats
        failed = [c.case_id for c in self.cases if c.verdict in (VERDICT_MISMATCHED, VERDICT_ERROR)]
        return {
            "suite": SUITE,
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "language": self.language,
            "seed": self.seed,
            "tts_provider": self.tts_provider,
            "stt_provider": self.stt_provider,
            "tts_is_fake": self.tts_is_fake,
            "stt_is_fake": self.stt_is_fake,
            "cases": totals["cases"],
            "matched": totals[VERDICT_MATCHED],
            "degraded": totals[VERDICT_DEGRADED],
            "mismatched": totals[VERDICT_MISMATCHED],
            "errors": totals[VERDICT_ERROR],
            "mean_wer": self.mean_wer,
            "per_category": self.per_category,
            "synthesis": {
                "files": stats["cases_with_audio"],
                "total_ms": stats["total_ms"],
                "mean_ms": stats["mean_ms"],
                "min_ms": stats["min_ms"],
                "max_ms": stats["max_ms"],
                "total_bytes": stats["total_bytes"],
                "formats": stats["formats"],
                "mean_tts_latency_ms": stats["mean_tts_latency_ms"],
                "mean_stt_latency_ms": stats["mean_stt_latency_ms"],
            },
            # the ledger's forbidden-key scan refuses "audio"-shaped keys, so the marks are
            # keyed by what they judge rather than by the medium
            "marks": {
                "speech_generation": self.marks["audio_generation"]["mark"],
                "loopback_semantics": self.marks["loopback_semantics"]["mark"],
                "physical_hearing": self.marks["physical_hearing"]["mark"],
            },
            "summary": self.summary,
            "failed_case_ids": failed[:20],
        }

    def factual_summary_tr(self) -> str:
        t = self.totals
        marks = self.marks
        verdict = {
            "HEALTHY": "sağlıklı",
            "DEGRADED": "bozulma var",
            "REGRESSION_FOUND": "regresyon bulundu",
            "NO_CASES": "cümle yok",
        }[self.summary]
        wer = "-" if self.mean_wer is None else f"{self.mean_wer:.3f}"
        return (
            f"TTS-STT geri döngü sınaması (vekil yol): {t['cases']} cümle, {t['matched']} "
            f"eşleşti, {t['degraded']} bozuk, {t['mismatched']} uyumsuz, {t['error']} hata; "
            f"ortalama WER {wer}; ses üretimi {self.tts_provider} "
            f"({marks['audio_generation']['mark']}), tanıma {self.stt_provider}; anlam geri "
            f"döngüsü {marks['loopback_semantics']['mark']}; fiziksel duyma "
            f"{marks['physical_hearing']['mark']} — {verdict}."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": SUITE,
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "language": self.language,
            "seed": self.seed,
            "providers": {
                "tts": self.tts_provider,
                "stt": self.stt_provider,
                "tts_is_fake": self.tts_is_fake,
                "stt_is_fake": self.stt_is_fake,
            },
            "thresholds": {
                "wer_matched": WER_MATCHED,
                "wer_degraded": WER_DEGRADED,
                "content_min_chars": CONTENT_MIN_CHARS,
                "content_max_edits": CONTENT_MAX_EDITS,
            },
            "totals": self.totals,
            "mean_wer": self.mean_wer,
            "per_category": self.per_category,
            "audio": self.audio_stats,
            "marks": self.marks,
            "summary": self.summary,
            "notes": list(self.notes),
            "ledger": {
                "event_type": "voice.tts_loopback",
                "factual_summary": self.factual_summary_tr(),
                "detail": self.ledger_detail(),
            },
            "cases": [c.to_dict() for c in self.cases],
        }


# ------------------------------------------------------------------- the loop


def is_fake_provider(provider: Any) -> bool:
    """The deterministic offline fakes, by class or by their own declaration."""
    if isinstance(provider, FakeTTSProvider | FakeSTTProvider):
        return True
    if getattr(provider, "is_fake", False):
        return True
    try:
        note = str(provider.capabilities().cost_metadata.get("note") or "")
    except Exception:  # noqa: BLE001 - a provider without capabilities is not a fake
        return False
    return "fake" in note.lower()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _error_text(stage: str, provider: str, exc: BaseException) -> str:
    if isinstance(exc, VoiceError):
        return f"{stage}:{provider}:{exc.error_class.value}: {exc}"[:400]
    return f"{stage}:{provider}:{type(exc).__name__}: {exc}"[:400]


def run_case(
    case: LoopbackCase, *, tts: TTSProvider, stt: STTProvider, language: str = "tr-TR",
    voice: str = "default",
) -> LoopbackCaseResult:
    """One case through the loop; a provider failure is an ``error`` verdict that names
    the stage and the provider, never an exception out of the run."""
    result = LoopbackCaseResult(
        case_id=case.case_id, category=case.category, expected_speech=case.expected_speech
    )
    tts_name = getattr(tts, "name", type(tts).__name__)
    stt_name = getattr(stt, "name", type(stt).__name__)

    started = time.perf_counter()
    try:
        synthesized = tts.synthesize(case.expected_speech, voice=voice, speed=1.0, fmt="wav")
    except Exception as exc:  # noqa: BLE001 - the verdict carries it
        result.error = _error_text("tts", tts_name, exc)
        return result
    audio = synthesized.audio or b""
    result.audio_bytes = len(audio)
    result.audio_format = synthesized.audio_format or ("wav" if is_wav(audio) else "")
    result.audio_ms = wav_duration_ms(audio) if is_wav(audio) else int(synthesized.duration_ms)
    result.tts_latency_ms = float(synthesized.latency_ms) or (time.perf_counter() - started) * 1000
    if not audio:
        result.error = f"tts:{tts_name}:EMPTY_AUDIO: the provider returned no bytes"
        return result

    started = time.perf_counter()
    try:
        recognised = stt.transcribe(audio, language=language)
    except Exception as exc:  # noqa: BLE001
        result.error = _error_text("stt", stt_name, exc)
        return result
    result.transcript = recognised.text or ""
    result.stt_latency_ms = float(recognised.latency_ms) or (time.perf_counter() - started) * 1000

    expected_norm, expected_tokens = normalize_for_comparison(case.expected_speech)
    transcript_norm, transcript_tokens = normalize_for_comparison(result.transcript)
    result.expected_normalized = expected_norm
    result.transcript_normalized = transcript_norm
    result.wer = word_error_rate(expected_norm, transcript_norm)
    result.missing_content = missing_content_words(expected_tokens, transcript_tokens)
    result.verdict = verdict_for(result.wer, result.missing_content)
    return result


def run_loopback(
    cases: Sequence[LoopbackCase],
    tts: TTSProvider,
    stt: STTProvider,
    *,
    language: str = "tr-TR",
    max_cases: int = DEFAULT_MAX_CASES,
    voice: str = "default",
    seed: int | None = None,
    notes: Sequence[str] = (),
) -> LoopbackReport:
    """The loop over ``cases[:max_cases]``: synthesise, transcribe, normalise, judge.

    ``max_cases`` is the budget guard for real providers (they cost money per character
    and per second); the caller samples before this, this only truncates.
    """
    tts_name = getattr(tts, "name", type(tts).__name__)
    stt_name = getattr(stt, "name", type(stt).__name__)
    results = [
        run_case(case, tts=tts, stt=stt, language=language, voice=voice)
        for case in list(cases)[: max(0, max_cases)]
    ]
    return LoopbackReport(
        generated_at=_now(),
        language=language,
        tts_provider=tts_name,
        stt_provider=stt_name,
        tts_is_fake=is_fake_provider(tts),
        stt_is_fake=is_fake_provider(stt),
        cases=results,
        seed=seed,
        notes=list(notes),
    )


__all__ = [
    "CONTENT_MAX_EDITS",
    "CONTENT_MIN_CHARS",
    "DEFAULT_MAX_CASES",
    "DEFAULT_SEED",
    "MARK_NOT_CLAIMED",
    "MARK_NOT_PROVEN",
    "MARK_PROVEN_AUTOMATED",
    "MARK_PROVEN_PROXY",
    "SCHEMA_VERSION",
    "STOP_WORDS",
    "SUITE",
    "VERDICTS",
    "VERDICT_DEGRADED",
    "VERDICT_ERROR",
    "VERDICT_MATCHED",
    "VERDICT_MISMATCHED",
    "WER_DEGRADED",
    "WER_MATCHED",
    "LoopbackCase",
    "LoopbackCaseResult",
    "LoopbackReport",
    "case_from_dict",
    "cases_from_corpus_results",
    "content_words",
    "is_fake_provider",
    "missing_content_words",
    "normalize_for_comparison",
    "run_case",
    "run_loopback",
    "sample_balanced",
    "strip_diacritics",
    "verdict_for",
]
