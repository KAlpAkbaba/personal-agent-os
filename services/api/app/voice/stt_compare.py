"""STT engine comparison on the owner's own recordings (stt-engines-measure).

MEASUREMENT ONLY (owner decision 2026-10-01). Nothing here changes a default, registers a
provider or opens an account: a folder of recordings, each with the sentence that was said,
is given to every engine that is configured on this machine, and one table comes out.

Per engine:

* **WER / CER**, pooled over the set (Σ edits / Σ reference words - the number to rank by;
  the mean of the per-sentence rates is reported beside it, because the older
  ``benchmark.run_stt_benchmark`` reports that one). The edit distance and the two rate
  functions are ``benchmark``'s own; nothing is forked.
* **intent changes** - the number the owner actually feels: how many sentences would have
  been ACTED ON differently. Both the reference and the transcript go through
  ``resolve_intent`` as heard (not through the ADR-0224 repair layer: the number is what the
  engine did, not what the repair hides) and their :func:`intent_signature` is compared.
* **latency p50/p95** - wall time per file. The file is sent in one go, so for a streaming
  engine this is processing time, not first-token latency.
* **real-time factor** - Σ processing time / Σ audio time of the files that came back (0,05 =
  twenty times faster than speech); ``None`` where it cannot be measured, never 0. A local
  engine's row also carries the process's peak memory (``None`` where it cannot be read).
* **intent changes on the command sentences** - the same count over the ten offline command
  sentences (numbers 21-30) alone: the cheap always-on layer would act on exactly these.
* **the ten worst sentences**, every engine's transcript side by side.

An engine that cannot run is a ROW (``NOT_RUN`` and why), never an absence. A file an engine
fails on - whatever it raises - is that file's error in the engine's row, never the end of
the run: the report is what says where the audio went.

Normalisation before counting (:func:`normalize_for_compare`): the repo's Turkish casefold
(``I``→``ı``, ``İ``→``i``), apostrophes dropped, other punctuation to a space, whitespace
collapsed. Numbers are left as spoken ("7" against "yedi" is an error) and Turkish letters
are kept (``ı/i``, ``ü/u`` are exactly the errors being measured).

Privacy: the audio goes to the engines that ran and nowhere else, and the report names them.
The transcripts are written to the ONE output file the caller names; nothing else is written
and no transcript is printed. That file must not exist yet (a report is never overwritten)
and its name is taken before the first engine is called.

``python -m app.voice.stt_compare --folder <recordings> --out <report.json>``
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import sys
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.voice.benchmark import char_error_rate, levenshtein, word_error_rate
from app.voice.errors import VoiceError
from app.voice.intents import ResolvedIntent, resolve_intent, turkish_casefold
from app.voice.providers import (
    AzureSTTProvider,
    FasterWhisperSTTProvider,
    OpenAISTTProvider,
    STTProvider,
    is_wav,
    wav_duration_ms,
)
from app.voice.providers_sherpa import (
    REASON_MODEL_HASH,
    REASON_MODEL_MISSING,
    REASON_NOT_INSTALLED,
    SherpaOnnxSTTProvider,
)
from app.voice.providers_soniox import SONIOX_DEFAULT_MODEL, SONIOX_URL_US, SonioxSTTProvider
from app.voice.spoken_device import resolve_without_device_phrase

#: 1.1: the ``recorded_live`` rows - ``source`` on every row, ``heard_live_by``,
#: ``from_browser`` and the items' ``browser_engine``.
#: 1.2 (local-tr-stt-measure): every row's ``real_time_factor``, ``peak_memory_bytes`` (local
#: rows only), ``commands_ran`` and ``intent_changes_commands``; the report's two notes.
REPORT_SCHEMA_VERSION = "1.2"
MANIFEST_NAME = "manifest.json"
TEMPLATE_NAME = "manifest.template.json"

OPENAI_KEY_ENV = "PAGENTOS_VOICE_OPENAI_API_KEY"
SONIOX_KEY_ENV = "PAGENTOS_VOICE_SONIOX_API_KEY"
AZURE_KEY_ENV = "PAGENTOS_VOICE_AZURE_SPEECH_KEY"
AZURE_REGION_ENV = "PAGENTOS_VOICE_AZURE_SPEECH_REGION"

STATUS_RAN = "RAN"
STATUS_NOT_RUN = "NOT_RUN"
#: Configured and tried, and not one file came back: no rate exists, which is not a rate of 0.
STATUS_FAILED = "FAILED"

REASON_NOT_CONFIGURED = "not configured"
REASON_NO_FILE_INPUT = "no file input"
REASON_NOT_SELECTED = "not selected"
REASON_NO_RECORDING = "no usable recording"

SKIP_NOT_FOUND = "file not found"
SKIP_NOT_WAV = "unsupported container (WAV only)"

#: A failure on one file that is not a ``VoiceError`` (a model that will not load, a body
#: that is not JSON): ``"unexpected: <ExceptionType>"`` in that file's error.
ERROR_UNEXPECTED = "unexpected"
#: A recording that carries no sentence for a ``recorded_live`` engine: that file's error,
#: never a perfect and never an empty hearing.
ERROR_NO_READY_TRANSCRIPT = "no ready transcript"

#: Where a row's sentences came from: this run sent the file to the engine, or the engine
#: wrote the sentence in the browser while the owner was reading (``ready_transcripts``).
SOURCE_FILE = "file"
SOURCE_RECORDED_LIVE = "recorded_live"

#: Who heard the sound of a ``recorded_live`` row, at recording time.
LIVE_DESTINATIONS = {
    "chrome-web-speech": "Google (Chrome'un konuşma tanıyıcısı, kayıt anında tarayıcıda)",
}
LIVE_DESTINATION_OTHER = "tarayıcının tanıyıcısı, kayıt anında"

EXIT_OK = 0
EXIT_BAD_INPUT = 2

WORST_COUNT = 10

#: The twenty sentences proposed to the owner (plan §4): the one recorded confusion of
#: 2026-09-30 ("Ofis" heard as "Ofisü") first, the rest chosen to provoke the same class of
#: error - suffixes, the dotted/dotless i, a negation that flips the intent, numbers in words.
OWNER_SENTENCES: tuple[str, ...] = (
    "Ofis bilgisayarımdan hesap makinesini aç.",
    "Ofis bilgisayarında not defterini aç.",
    "Ev bilgisayarında müziği durdur.",
    "Bunu unutma, yarın sabah hatırlat.",
    "Az önce söylediğimi unut.",
    "Yarın sabah yediye alarm kur.",
    "Alarmı on dakika ertele.",
    "İkinci maddeye geç.",
    "Isıtıcıyı kapat, ışığı aç.",
    "Iğdır'ın hava durumu nasıl?",
    "İstanbul'da yarın yağmur var mı?",
    "Yapay zeka son gelişmelerini araştır.",
    "Raporu bana PDF olarak gönder.",
    "Chrome'da YouTube'u aç ve sesi kıs.",
    "Son e-postayı oku.",
    "Toplantıyı perşembe saat üçe al.",
    "Şu an ne üzerinde çalışıyorsun?",
    "Ekibin durumunu özetle.",
    "Bundan sonra cevapları kısa tut.",
    "Görüşürüz, dinlemeyi bırak.",
)

#: Ten sentences for the companion's offline commands (packages/protocol/device-voice.json
#: ``offline_commands``: alarm.stop, alarm.snooze, listening.off, time.tell), numbers 21-30
#: after the owner's twenty: five of its own templates and five natural ways of saying them
#: that no template matches. The three listening sentences resolve to no server intent, so an
#: intent change is counted on them only when a mishearing turns them INTO an action.
OFFLINE_COMMAND_SENTENCES: tuple[str, ...] = (
    "Alarmı kapat.",
    "Alarmı ertele.",
    "Saat kaç?",
    "Dinlemeyi kapat.",
    "Mikrofonu kapat.",
    "Alarmı kapatır mısın?",
    "Beş dakika daha ertele.",
    "Şu an saat kaç acaba?",
    "Artık dinleme.",
    "Alarmı sustur lütfen.",
)

#: The measurement set, in recording order: 1-20 the owner's, 21-30 the commands.
MEASUREMENT_SENTENCES: tuple[str, ...] = OWNER_SENTENCES + OFFLINE_COMMAND_SENTENCES


class ManifestError(ValueError):
    """The recordings folder cannot be measured as it stands (bad input, exit 2)."""


# ------------------------------------------------------------- normalisation

_APOSTROPHES = frozenset("'’ʼ‘`´")


def normalize_for_compare(text: str) -> str:
    """The form two transcripts are compared in: Turkish casefold, no punctuation."""
    out: list[str] = []
    for char in turkish_casefold(text):
        if char in _APOSTROPHES:
            continue  # "Iğdır'ın" and "Iğdırın" are the same word
        category = unicodedata.category(char)
        out.append(" " if category[0] in "PSZC" else char)
    return " ".join("".join(out).split())


# ------------------------------------------------------------------- scoring


@dataclass(frozen=True, slots=True)
class PairScore:
    ref_words: int
    word_edits: int
    ref_chars: int
    char_edits: int
    wer: float
    cer: float


def score_pair(reference: str, hypothesis: str) -> PairScore:
    ref, hyp = normalize_for_compare(reference), normalize_for_compare(hypothesis)
    return PairScore(
        ref_words=len(ref.split()),
        word_edits=levenshtein(ref.split(), hyp.split()),
        ref_chars=len(ref.replace(" ", "")),
        char_edits=levenshtein(ref.replace(" ", ""), hyp.replace(" ", "")),
        wer=word_error_rate(ref, hyp),
        cer=char_error_rate(ref, hyp),
    )


#: What a resolution says ABOUT its own reading, not what it asks the system to do. Two
#: readings of one request differ in these whenever a comma differs.
_DESCRIPTIVE_FIELDS = frozenset(
    {
        "normalized_text",
        "tokens",
        "fillers_removed",
        "confidence",
        "matched",
        "route_repair",
        "band",
        "candidates",
    }
)


def _comparable(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_for_compare(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _comparable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return tuple(sorted((str(key), _comparable(item)) for key, item in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_comparable(item) for item in value)
    return value


def intent_signature(text: str) -> tuple[Any, ...]:
    """What the system would DO with ``text``: the intent, every field a tool acts on
    (the application, the minutes, the words to type, ...) and the device the sentence
    names. The device is part of it because that is where the 2026-09-30 confusion lived:
    "Ofis bilgisayarımdan ... aç" and "Ofisü bilgisayarında ... açın" are both ``app_open``
    of the calculator, on different machines. Words the owner's sentence carries into a
    tool are compared in :func:`normalize_for_compare` form, so punctuation is no change.

    ``reference`` (which research the words point at) is read for EVERY utterance and holds
    the sentence's content words, so it would turn every misheard word into an "intent
    change"; it counts only when the utterance is about research at all."""
    resolved, _used, named = resolve_without_device_phrase(text, resolve_intent)
    fields = tuple(
        (field.name, _comparable(getattr(resolved, field.name)))
        for field in dataclasses.fields(ResolvedIntent)
        if field.name not in _DESCRIPTIVE_FIELDS
        and not (field.name == "reference" and resolved.research_class is None)
    )
    return (*fields, ("named_devices", tuple(named)))


def intent_changed(reference: str, hypothesis: str) -> bool:
    return intent_signature(reference) != intent_signature(hypothesis)


def is_command_sentence(reference: str) -> bool:
    """Whether ``reference`` is one of the ten offline command sentences (punctuation and
    case aside): the item counts in ``intent_changes_commands``."""
    forms = {normalize_for_compare(sentence) for sentence in OFFLINE_COMMAND_SENTENCES}
    return normalize_for_compare(reference) in forms


def process_peak_memory_bytes() -> int | None:
    """This process's peak memory so far (Windows: peak working set; Linux: max RSS), or
    None when it cannot be read. One number for the PROCESS, not for an engine."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # without restype/argtypes the call answers 0 (the integrator measured it)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.K32GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_Counters),
                wintypes.DWORD,
            ]
            kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = _Counters()
            counters.cb = ctypes.sizeof(_Counters)
            if not kernel32.K32GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            ):
                return None
            return int(counters.PeakWorkingSetSize) or None
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if not peak:
            return None
        return peak if sys.platform == "darwin" else peak * 1024  # Linux reports KiB
    except (OSError, AttributeError, ImportError, ValueError):
        return None


def percentile(values: Sequence[float], percent: float) -> float | None:
    """Nearest-rank percentile; None for an empty set."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[rank - 1]


# ------------------------------------------------------------------ manifest


@dataclass(frozen=True, slots=True)
class Recording:
    item_id: str
    path: Path
    reference: str
    recorded_where: str
    #: engine label -> the sentence that engine wrote WHEN THE RECORDING WAS MADE (Chrome's
    #: recogniser on the recording page). Absent = it did not run; ``""`` = it wrote nothing.
    ready_transcripts: Mapping[str, str] = dataclasses.field(default_factory=dict)
    #: The browser's own name for that recogniser, as the recording page saw it.
    browser_engine: str | None = None
    #: The item came from the recording page (it carries either field): the audio is what the
    #: system hears, after the browser's noise suppression - not the raw microphone.
    from_browser: bool = False


def _ready_transcripts(index: int, item: dict[str, Any]) -> dict[str, str]:
    if "ready_transcripts" not in item:
        return {}
    ready = item["ready_transcripts"]
    if not isinstance(ready, dict) or not all(
        isinstance(label, str) and label and isinstance(text, str) for label, text in ready.items()
    ):
        raise ManifestError(
            f'item {index} ({item["file"]}): "ready_transcripts" must map an engine label to '
            "the sentence it wrote"
        )
    return dict(ready)


def load_manifest(folder: Path) -> tuple[str, list[Recording]]:
    """``(language, recordings)`` from ``<folder>/manifest.json``. Two item fields are
    optional: ``ready_transcripts`` and ``browser_engine`` (the Cloud Core's manifest)."""
    path = folder / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(f"no {MANIFEST_NAME} in {folder}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"{MANIFEST_NAME} is not readable JSON: {exc}") from exc
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ManifestError(f'{MANIFEST_NAME} must be an object with an "items" list')
    root = folder.resolve()
    recordings: list[Recording] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("file"), str) or not item["file"]:
            raise ManifestError(f'item {index}: "file" is missing')
        reference = item.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            raise ManifestError(f'item {index} ({item["file"]}): "reference" is missing')
        target = (root / item["file"]).resolve()
        if not target.is_relative_to(root):
            raise ManifestError(f'item {index}: "{item["file"]}" is outside the recordings folder')
        item_id = target.relative_to(root).as_posix()
        if any(existing.item_id == item_id for existing in recordings):
            raise ManifestError(f'item {index}: "{item_id}" is listed twice')
        browser_engine = item.get("browser_engine")
        if browser_engine is not None and not isinstance(browser_engine, str):
            raise ManifestError(f'item {index} ({item["file"]}): "browser_engine" must be text')
        recordings.append(
            Recording(
                item_id,
                target,
                reference.strip(),
                str(item.get("recorded_where") or ""),
                ready_transcripts=_ready_transcripts(index, item),
                browser_engine=browser_engine,
                from_browser="ready_transcripts" in item or "browser_engine" in item,
            )
        )
    language = raw.get("language")
    return (language if isinstance(language, str) and language else "tr-TR"), recordings


def write_manifest_template(folder: Path) -> Path:
    """Write ``manifest.template.json`` (the thirty sentences to record). It is a template
    and never the manifest itself: an existing ``manifest.json`` is not touched."""
    path = folder / TEMPLATE_NAME
    template = {
        "language": "tr-TR",
        "how": (
            "Her cümleyi bir kez kaydet (WAV, PCM 16-bit, mono, 16 kHz), dosyayı bu klasöre "
            "koy, sonra bu dosyanın adını manifest.json yap. recorded_where: masa / oda."
        ),
        "items": [
            {"file": f"{index:02d}.wav", "reference": sentence, "recorded_where": "masa"}
            for index, sentence in enumerate(MEASUREMENT_SENTENCES, start=1)
        ],
    }
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------- engines


@dataclass(frozen=True, slots=True)
class Engine:
    """One row of the table. ``label`` is the key - never ``provider.name``: two models of
    one vendor share a provider name and would be summed into one row."""

    label: str
    provider: STTProvider | None = None
    #: Why it cannot run, when ``provider`` is None.
    reason: str = ""
    #: Who receives the audio when it does run - named in the summary.
    destination: str = ""
    #: Runs in this process: its row carries the process's peak memory.
    local: bool = False


def configured_engines(
    env: Mapping[str, str],
    *,
    soniox_url: str = SONIOX_URL_US,
    soniox_model: str = SONIOX_DEFAULT_MODEL,
) -> list[Engine]:
    """Every engine this harness knows, runnable or not, in a fixed order."""
    openai_key = env.get(OPENAI_KEY_ENV, "").strip()
    soniox_key = env.get(SONIOX_KEY_ENV, "").strip()
    azure_key = env.get(AZURE_KEY_ENV, "").strip()
    engines: list[Engine] = []
    # gpt-4o-transcribe first: it is the model the live session transcribes with.
    for model in ("gpt-4o-transcribe", "whisper-1"):
        engines.append(
            Engine(
                f"openai:{model}",
                OpenAISTTProvider(openai_key, model=model, timeout_s=120.0) if openai_key else None,
                reason="" if openai_key else REASON_NOT_CONFIGURED,
                destination="OpenAI (api.openai.com)",
            )
        )
    engines.append(
        Engine(
            f"soniox:{soniox_model}",
            SonioxSTTProvider(soniox_key, model=soniox_model, url=soniox_url)
            if soniox_key
            else None,
            reason="" if soniox_key else REASON_NOT_CONFIGURED,
            destination=f"Soniox ({soniox_url.split('//', 1)[-1].split('/', 1)[0]})",
        )
    )
    azure_region = env.get(AZURE_REGION_ENV, "").strip() or "westeurope"
    engines.append(
        Engine(
            "azure:tr-TR",
            AzureSTTProvider(azure_key, region=azure_region) if azure_key else None,
            reason="" if azure_key else REASON_NOT_CONFIGURED,
            destination=f"Microsoft Azure Speech ({azure_region})",
        )
    )
    local = FasterWhisperSTTProvider(model_size="large-v3-turbo")
    installed = local.available()
    engines.append(
        Engine(
            "faster-whisper:large-v3-turbo",
            local if installed else None,
            reason="" if installed else REASON_NOT_INSTALLED,
            destination="bu bilgisayar (yerel, ses dışarı çıkmaz)",
            local=True,
        )
    )
    # local-tr-stt-measure: the on-device streaming Turkish recogniser, measured only.
    sherpa = SherpaOnnxSTTProvider()
    sherpa_status = sherpa.status()
    engines.append(
        Engine(
            "sherpa-onnx:tr-zipformer-int8",
            sherpa if sherpa_status.ok else None,
            reason=sherpa_status.reason,
            destination="bu bilgisayar (yerel, ses dışarı çıkmaz)",
            local=True,
        )
    )
    # A browser API on a live microphone: there is no way to hand it a file.
    engines.append(Engine("chrome-web-speech", None, reason=REASON_NO_FILE_INPUT))
    return engines


# ---------------------------------------------------------------- comparison


def _rate(edits: int, total: int) -> float | None:
    return round(edits / total, 4) if total else None


class _Tally:
    """One engine row being filled, file by file - the transcribed rows and the
    ``recorded_live`` rows score and close through the SAME code."""

    def __init__(self) -> None:
        self.ran = 0
        self.latencies: list[float] = []
        self.sentence_wers: list[float] = []
        self.intent_changes = 0
        #: Σ audio of the files that have a latency: the real-time factor's denominator.
        self.timed_audio_ms = 0
        self.commands = 0
        self.command_intent_changes = 0

    def score(
        self,
        row: dict[str, Any],
        item: dict[str, Any],
        label: str,
        recording: Recording,
        text: str,
        latency_ms: float | None,
    ) -> None:
        score = score_pair(recording.reference, text)
        changed = intent_changed(recording.reference, text)
        item["results"][label] = {
            "hypothesis": text,
            "word_edits": score.word_edits,
            "ref_words": score.ref_words,
            "char_edits": score.char_edits,
            "ref_chars": score.ref_chars,
            "wer": round(score.wer, 4),
            "cer": round(score.cer, 4),
            "intent_changed": changed,
            "latency_ms": latency_ms,
        }
        for name in ("word_edits", "ref_words", "char_edits", "ref_chars"):
            row[name] += getattr(score, name)
        self.ran += 1
        if latency_ms is not None:
            self.latencies.append(latency_ms)
            self.timed_audio_ms += int(item.get("audio_ms") or 0)
        self.sentence_wers.append(score.wer)
        self.intent_changes += int(changed)
        if is_command_sentence(recording.reference):
            self.commands += 1
            self.command_intent_changes += int(changed)

    @staticmethod
    def fail(
        row: dict[str, Any],
        item: dict[str, Any],
        label: str,
        recording: Recording,
        error_class: str,
    ) -> None:
        row["errors"].append({"id": recording.item_id, "error_class": error_class})
        item["results"][label] = {"error": error_class}

    def close(self, row: dict[str, Any]) -> None:
        row["files_ran"] = self.ran
        row["files_failed"] = len(row["errors"])
        row["reason"] = ""
        if not self.ran:
            row["status"] = STATUS_FAILED
            return
        row["status"] = STATUS_RAN
        row["wer"] = _rate(row["word_edits"], row["ref_words"])
        row["cer"] = _rate(row["char_edits"], row["ref_chars"])
        row["mean_sentence_wer"] = round(sum(self.sentence_wers) / len(self.sentence_wers), 4)
        row["intent_changes"] = self.intent_changes
        row["latency_p50_ms"] = percentile(self.latencies, 50)
        row["latency_p95_ms"] = percentile(self.latencies, 95)
        row["real_time_factor"] = (
            round(sum(self.latencies) / self.timed_audio_ms, 4) if self.timed_audio_ms else None
        )
        row["commands_ran"] = self.commands
        row["intent_changes_commands"] = self.command_intent_changes if self.commands else None


def run_comparison(
    folder: Path,
    engines: Sequence[Engine],
    *,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Run every runnable engine over the folder's recordings and build the report.

    Reads the folder, calls the engines, writes NOTHING."""
    labels = [engine.label for engine in engines]
    if len(set(labels)) != len(labels):
        raise ValueError(f"every engine needs its own label, got {labels}")
    language, recordings = load_manifest(folder)

    skipped: list[dict[str, str]] = []
    usable: list[tuple[Recording, bytes]] = []
    for recording in recordings:
        if not recording.path.is_file():
            skipped.append({"id": recording.item_id, "reason": SKIP_NOT_FOUND})
            continue
        audio = recording.path.read_bytes()
        if not is_wav(audio):
            # OpenAI refuses an unlabelled container and Soniox does not detect m4a (plan R3)
            skipped.append({"id": recording.item_id, "reason": SKIP_NOT_WAV})
            continue
        usable.append((recording, audio))

    item_rows: list[dict[str, Any]] = [
        {
            "id": recording.item_id,
            "reference": recording.reference,
            "recorded_where": recording.recorded_where,
            "browser_engine": recording.browser_engine,
            "audio_ms": wav_duration_ms(audio),
            "results": {},
        }
        for recording, audio in usable
    ]
    # Every label something wrote at recording time; one the configured engines do not have
    # gets its own row after theirs, so nothing recorded is dropped silently.
    ready_labels = list(
        dict.fromkeys(label for recording in recordings for label in recording.ready_transcripts)
    )
    heard_live = {
        label for label in ready_labels if any(label in r.ready_transcripts for r, _ in usable)
    }
    engines = [
        *engines,
        *(
            Engine(label, None, reason=REASON_NO_RECORDING)
            for label in ready_labels
            if label not in labels
        ),
    ]
    runnable = [engine for engine in engines if engine.provider is not None] if usable else []
    engine_rows: list[dict[str, Any]] = []
    for engine in engines:
        row: dict[str, Any] = {
            "label": engine.label,
            "status": STATUS_NOT_RUN,
            "reason": engine.reason,
            "destination": engine.destination,
            "files_ran": 0,
            "files_failed": 0,
            "wer": None,
            "cer": None,
            "mean_sentence_wer": None,
            "word_edits": 0,
            "ref_words": 0,
            "char_edits": 0,
            "ref_chars": 0,
            "intent_changes": None,
            "latency_p50_ms": None,
            "latency_p95_ms": None,
            "real_time_factor": None,
            "peak_memory_bytes": None,
            "commands_ran": 0,
            "intent_changes_commands": None,
            "errors": [],
            "source": SOURCE_FILE,
        }
        engine_rows.append(row)
        if engine.provider is None:
            # An engine that cannot be handed a file is scored from what it wrote while the
            # owner read - unless this run left it out on purpose.
            if engine.label in heard_live and engine.reason != REASON_NOT_SELECTED:
                row["source"] = SOURCE_RECORDED_LIVE
                row["destination"] = engine.destination or LIVE_DESTINATIONS.get(
                    engine.label, LIVE_DESTINATION_OTHER
                )
                tally = _Tally()
                for (recording, _audio), item in zip(usable, item_rows, strict=True):
                    text = recording.ready_transcripts.get(engine.label)
                    if text is None:
                        # not written is not heard: never a perfect or an empty hearing
                        tally.fail(row, item, engine.label, recording, ERROR_NO_READY_TRANSCRIPT)
                        continue
                    tally.score(row, item, engine.label, recording, text, None)
                tally.close(row)
            continue
        if engine not in runnable:
            row["reason"] = REASON_NO_RECORDING
            continue
        tally = _Tally()
        for (recording, audio), item in zip(usable, item_rows, strict=True):
            started = clock()
            try:
                result = engine.provider.transcribe(audio, language=language)
                latency_ms = round((clock() - started) * 1000, 2)
                tally.score(row, item, engine.label, recording, result.text, latency_ms)
            except Exception as exc:
                # ANY failure is this file's error, not the run's: the engines before this
                # one already received the audio, and the report is what names them. Only
                # the type is kept - a message can carry a path, a key or a transcript.
                error_class = (
                    str(exc.error_class)
                    if isinstance(exc, VoiceError)
                    else f"{ERROR_UNEXPECTED}: {type(exc).__name__}"
                )
                tally.fail(row, item, engine.label, recording, error_class)
        tally.close(row)
        if engine.local:
            row["peak_memory_bytes"] = process_peak_memory_bytes()

    tried = [row for row in engine_rows if row["status"] != STATUS_NOT_RUN]
    live = [row for row in tried if row["source"] == SOURCE_RECORDED_LIVE]
    report: dict[str, Any] = {
        "kind": "stt_compare",
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": now().isoformat().replace("+00:00", "Z"),
        "language": language,
        "measurement_only": True,
        "rates": "pooled: sum of edits / sum of reference words (letters for CER)",
        "normalisation": (
            "Turkish casefold (I->ı, İ->i), apostrophes dropped, punctuation to space; "
            "numbers left as spoken; Turkish letters kept"
        ),
        "latency": "wall time per file sent in one go: processing time, not first-token latency",
        "real_time_factor": (
            "sum of processing time / sum of audio time of the files that came back"
        ),
        "memory": (
            "peak_memory_bytes: the measuring PROCESS's peak so far when a local row closed "
            "(every engine runs in one process), not one engine's own"
        ),
        "recordings": {
            "listed": len(recordings),
            "usable": len(usable),
            "audio_ms": sum(item["audio_ms"] for item in item_rows),
            "skipped": skipped,
        },
        "engines": engine_rows,
        # this run sent audio only to the engines it transcribed with; a recorded_live row
        # was sent nothing now - what it heard, it heard in the browser while the owner read
        "audio_sent_to": [
            {"engine": row["label"], "destination": row["destination"]}
            for row in tried
            if row["source"] != SOURCE_RECORDED_LIVE
        ],
        "heard_live_by": [
            {"engine": row["label"], "destination": row["destination"]} for row in live
        ],
        "from_browser": any(recording.from_browser for recording, _ in usable),
        "items": item_rows,
        "worst": {row["label"]: _worst(row["label"], item_rows) for row in tried},
    }
    report["summary_tr"] = summary_tr(report)
    return report


def _worst(label: str, item_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The sentences this engine got most wrong, with what every engine heard beside it."""
    wrong = [
        item
        for item in item_rows
        if item["results"].get(label, {}).get("char_edits", 0) > 0
        or item["results"].get(label, {}).get("intent_changed")
    ]
    wrong.sort(
        key=lambda item: (
            -item["results"][label]["word_edits"],
            -item["results"][label]["char_edits"],
            item["id"],
        )
    )
    return [
        {
            "id": item["id"],
            "reference": item["reference"],
            "word_edits": item["results"][label]["word_edits"],
            "char_edits": item["results"][label]["char_edits"],
            "intent_changed": item["results"][label]["intent_changed"],
            "hypotheses": {
                other: result["hypothesis"]
                for other, result in item["results"].items()
                if "hypothesis" in result
            },
        }
        for item in wrong[:WORST_COUNT]
    ]


# ------------------------------------------------------------------- summary

_REASON_TR = {
    REASON_NOT_CONFIGURED: "yapılandırılmamış (anahtar yok)",
    REASON_NOT_INSTALLED: "kurulu değil",
    REASON_NO_FILE_INPUT: "dosyadan ölçülemez (canlı mikrofon ister)",
    REASON_NOT_SELECTED: "bu çalıştırmada seçilmedi",
    REASON_NO_RECORDING: "ölçülecek kayıt yok",
    REASON_MODEL_MISSING: "Türkçe model eksik (dosya yok)",
    REASON_MODEL_HASH: "Türkçe model dosyası sabitlenen hash ile tutmuyor; yüklenmedi",
    SKIP_NOT_FOUND: "dosya yok",
    SKIP_NOT_WAV: "WAV değil (yalnız WAV ölçülür)",
}


def _tr_number(value: float | None, digits: int = 4) -> str:
    return "-" if value is None else f"{value:.{digits}f}".replace(".", ",")


def summary_tr(report: dict[str, Any]) -> list[str]:
    """The owner's summary, in Turkish. Numbers and engine names only - no transcript."""
    recordings = report["recordings"]
    lines = [
        "STT karşılaştırması (yalnız ölçüm; hiçbir varsayılan değişmedi, hiçbir şey benimsenmedi)",
        f"Kayıt: {recordings['usable']} ölçülebilir / {recordings['listed']} listelenen, "
        f"toplam {_tr_number(recordings['audio_ms'] / 1000, 1)} sn ses, dil {report['language']}",
    ]
    if not recordings["usable"]:
        lines.append(
            "Ölçülecek kayıt yok: manifest'teki cümleleri WAV (PCM 16-bit, mono, 16 kHz) "
            "olarak kaydedip klasöre koyun."
        )
    for reason in dict.fromkeys(skip["reason"] for skip in recordings["skipped"]):
        ids = [skip["id"] for skip in recordings["skipped"] if skip["reason"] == reason]
        shown = ", ".join(ids[:5]) + (" ..." if len(ids) > 5 else "")
        lines.append(f"Atlanan {len(ids)} kayıt - {_REASON_TR.get(reason, reason)}: {shown}")
    lines.append("Motor | durum | WER | CER | niyeti değişen cümle | gecikme p50/p95 ms | dosya")
    for row in report["engines"]:
        if row["status"] == STATUS_NOT_RUN:
            reason = _REASON_TR.get(row["reason"], row["reason"])
            lines.append(f"{row['label']} | NOT_RUN - {reason}")
            continue
        if row["status"] == STATUS_FAILED:
            lines.append(
                f"{row['label']} | BAŞARISIZ - {row['files_failed']} dosyanın hiçbiri dönmedi"
            )
            continue
        live = row.get("source") == SOURCE_RECORDED_LIVE
        lines.append(
            f"{row['label']} | {'KAYIT ANINDA YAZILDI' if live else 'ÖLÇÜLDÜ'} | "
            f"{_tr_number(row['wer'])} | {_tr_number(row['cer'])} | "
            f"{row['intent_changes']} | {_tr_number(row['latency_p50_ms'], 0)}/"
            f"{_tr_number(row['latency_p95_ms'], 0)} | "
            f"{row['files_ran']} ölçüldü, {row['files_failed']} hata"
        )
    for row in report["engines"]:
        if row["status"] != STATUS_RAN:
            continue
        extra = []
        if row.get("real_time_factor") is not None:
            extra.append(f"gerçek zaman çarpanı {_tr_number(row['real_time_factor'], 3)}")
        if row.get("peak_memory_bytes") is not None:
            megabytes = row["peak_memory_bytes"] / 1_048_576
            extra.append(f"süreç tepe belleği {_tr_number(megabytes, 0)} MB")
        if row.get("commands_ran"):
            extra.append(
                f"Komut cümleleri: niyeti değişen {row['intent_changes_commands']} / "
                f"{row['commands_ran']}"
            )
        if extra:
            lines.append(f"{row['label']}: " + "; ".join(extra))
    sent = report["audio_sent_to"]
    if sent:
        names = "; ".join(f"{entry['engine']} -> {entry['destination']}" for entry in sent)
        lines.append(f"Ses şu motorlara gönderildi, başka hiçbir yere gönderilmedi: {names}")
    else:
        lines.append("Ses hiçbir motora gönderilmedi; bilgisayardan çıkmadı.")
    for entry in report.get("heard_live_by", []):
        lines.append(
            f"{entry['engine']} satırı, kayıt anında tarayıcıda yazılan cümledir; bu çalıştırma "
            f"o motora ses göndermedi. Sesi kayıt anında duyan: {entry['destination']} "
            "(Chrome sesi tanımak için Google'a göndermiş olabilir); gecikme ölçülmedi."
        )
    if report.get("from_browser"):
        lines.append(
            "Kayıtlar sistemin duyduğu sestir (tarayıcının gürültü bastırma ayarı açık), "
            "ham mikrofon sesi değildir."
        )
    lines += [
        "WER/CER havuzlanmış orandır (toplam hata / toplam referans kelime ya da harf); "
        "sayılar söylendiği gibi bırakıldı, Türkçe harfler korunuyor.",
        "Gecikme: dosya tek seferde gönderildi; bu işlem süresidir, canlı akıştaki "
        "ilk-kelime gecikmesi değildir.",
        "Uyarı: rapor dosyası söylenen cümlelerin dökümünü içerir ve depoya girer; "
        "bu betiğe serbest konuşma kaydı vermeyin.",
    ]
    return lines


# ----------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.voice.stt_compare",
        description="Compare the configured STT engines on a folder of recordings.",
    )
    parser.add_argument("--folder", required=True, type=Path, help="the recordings folder")
    parser.add_argument("--out", type=Path, help="the ONE file the report is written to")
    parser.add_argument("--engines", default="", help="comma-separated engine labels to run")
    parser.add_argument("--soniox-url", default=SONIOX_URL_US)
    parser.add_argument("--soniox-model", default=SONIOX_DEFAULT_MODEL)
    parser.add_argument(
        "--write-template",
        action="store_true",
        help=f"write {TEMPLATE_NAME} (the thirty sentences) into the folder and stop",
    )
    return parser.parse_args(argv)


def _say(line: str) -> None:
    sys.stdout.write(line + "\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):  # a Windows console is cp1252 and "ı" is not in it
        reconfigure(encoding="utf-8", errors="replace")
    folder: Path = args.folder
    if not folder.is_dir():
        _say(f"bad input: {folder} is not a folder")
        return EXIT_BAD_INPUT
    if args.write_template:
        _say(f"Şablon yazıldı: {write_manifest_template(folder)}")
        _say(
            "Otuz cümleyi birer kez kaydedin (WAV, PCM 16-bit, mono, 16 kHz), dosyaları bu "
            f"klasöre koyun, şablonun adını {MANIFEST_NAME} yapın ve komutu yeniden çalıştırın."
        )
        return EXIT_OK
    if args.out is None:
        _say("bad input: --out is required")
        return EXIT_BAD_INPUT
    out: Path = args.out
    # The report is the only record of where the audio went, so its place is settled BEFORE
    # an engine is called: the name is taken exclusively (an earlier report is evidence and
    # is never replaced; two runs that chose one name cannot both hold it) and a folder that
    # is missing or not writable is found out while nothing has been sent.
    try:
        out.open("x", encoding="utf-8").close()
    except FileExistsError:
        _say(f"bad input: {out} already exists; a report is never overwritten")
        return EXIT_BAD_INPUT
    except OSError as exc:
        _say(f"bad input: the report cannot be written to {out} ({type(exc).__name__})")
        return EXIT_BAD_INPUT
    written = False
    try:
        written = _measure_into(out, folder, args)
    finally:
        if not written:
            out.unlink(missing_ok=True)  # nothing was measured: leave no empty "report"
    return EXIT_OK if written else EXIT_BAD_INPUT


def _measure_into(out: Path, folder: Path, args: argparse.Namespace) -> bool:
    """Run the comparison into the reserved ``out``; False for bad input (said, not raised)."""
    engines = configured_engines(
        os.environ, soniox_url=args.soniox_url, soniox_model=args.soniox_model
    )
    selected = [label.strip() for label in args.engines.split(",") if label.strip()]
    unknown = sorted(set(selected) - {engine.label for engine in engines})
    if unknown:
        _say(f"bad input: unknown engine {unknown}; known: {[e.label for e in engines]}")
        return False
    if selected:
        # an engine that was left out stays in the table, saying so
        engines = [
            engine
            if engine.label in selected
            else Engine(engine.label, None, reason=REASON_NOT_SELECTED)
            for engine in engines
        ]
    try:
        report = run_comparison(folder, engines)
    except ManifestError as exc:
        _say(f"bad input: {exc}")
        return False
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    for line in report["summary_tr"]:
        _say(line)
    return True


if __name__ == "__main__":  # pragma: no cover - the module entry point
    raise SystemExit(main())


__all__ = [
    "MANIFEST_NAME",
    "MEASUREMENT_SENTENCES",
    "OFFLINE_COMMAND_SENTENCES",
    "OWNER_SENTENCES",
    "REPORT_SCHEMA_VERSION",
    "TEMPLATE_NAME",
    "Engine",
    "ManifestError",
    "PairScore",
    "Recording",
    "configured_engines",
    "intent_changed",
    "intent_signature",
    "load_manifest",
    "main",
    "normalize_for_compare",
    "is_command_sentence",
    "percentile",
    "process_peak_memory_bytes",
    "run_comparison",
    "score_pair",
    "summary_tr",
    "write_manifest_template",
]
