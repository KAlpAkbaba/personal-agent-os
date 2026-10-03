"""The measurement recordings store, over the existing ``ObjectStore`` (no table).

The owner reads ``app.voice.stt_compare.OWNER_SENTENCES`` once per place; each reading is one
WAV (PCM 16-bit, mono, 16 kHz, at most 30 s) and one metadata sidecar:

    voice-measurement/<place>/<NN>.wav
    voice-measurement/<place>/<NN>.json

The ``ObjectStore`` has no list call, so the layout is ENUMERABLE: two places times twenty
sentences is every key that can exist. Nothing is read-modify-written (there is no shared
index object), and "delete everything" walks all forty pairs, so an orphan cannot survive.

A recording lives ``RETENTION_DAYS`` from ``recorded_at``. Three things enforce it: every
read filters on the expiry (an expired recording is never listed or served, purge or no
purge), ``GET /v1/voice/measurement`` purges before it lists, and ``DailyPurge`` purges on
the server's own retention sweeper once a day. The clock is always passed in.

What is logged is place, index, bytes and sha256 - never the transcript, the base64, the
capture settings or the audio. A store fault is ``StoreUnavailable`` on every path.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.logging import get_logger
from app.object_store import ObjectStore
from app.voice.providers import wav_duration_ms, wav_info
from app.voice.stt_compare import OWNER_SENTENCES

logger = get_logger("app.voice.measurement")

ROOT = "voice-measurement"
PLACES: tuple[str, ...] = ("ev", "ofis")
LANGUAGE = "tr-TR"
RETENTION_DAYS = 30
MAX_SECONDS = 30
MAX_AUDIO_BYTES = 1_048_576
#: The base64 of ``MAX_AUDIO_BYTES``: anything longer is refused before it is decoded.
MAX_BASE64_CHARS = 4 * math.ceil(MAX_AUDIO_BYTES / 3)
SAMPLE_RATE = 16_000
MAX_TRANSCRIPT_CHARS = 500
MAX_ENGINE_CHARS = 64
MAX_CAPTURE_KEYS = 20
MAX_CAPTURE_KEY_CHARS = 64
MAX_CAPTURE_STRING_CHARS = 120
#: The measuring tool's name for the transcript Chrome wrote while the owner was reading.
READY_ENGINE = "chrome-web-speech"
PURGE_INTERVAL_S = 86_400.0

_ITEM_FIELDS: tuple[str, ...] = (
    "place",
    "index",
    "file",
    "reference",
    "recorded_at",
    "expires_at",
    "audio_ms",
    "bytes",
    "sha256",
    "browser_transcript",
    "browser_engine",
    "capture",
)

#: One per process: save, delete and purge never interleave their two-object writes.
_LOCK = threading.Lock()


class Refusal(Exception):
    """The request is refused; ``message`` is the Turkish sentence the owner reads."""

    def __init__(self, code: str, message: str, *, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = status


class StoreUnavailable(Exception):
    """The object store did not answer; nothing is known about the recording."""


def slots() -> list[tuple[str, int]]:
    """Every (place, index) that can exist, in listing order."""
    return [(place, index) for place in PLACES for index in range(1, len(OWNER_SENTENCES) + 1)]


def file_name(place: str, index: int) -> str:
    return f"{place}-{index:02d}.wav"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _place(place: object) -> str:
    if not isinstance(place, str) or place not in PLACES:
        raise Refusal("invalid_place", "Kayıt yeri yalnızca 'ev' ya da 'ofis' olabilir.")
    return place


def _index(index: object) -> int:
    number: int | None = None
    if isinstance(index, int) and not isinstance(index, bool):
        number = index
    elif isinstance(index, str) and index.isascii() and index.isdigit() and len(index) <= 2:
        number = int(index)
    if number is None or not 1 <= number <= len(OWNER_SENTENCES):
        raise Refusal(
            "invalid_index",
            f"Cümle numarası 1 ile {len(OWNER_SENTENCES)} arasında bir sayı olmalı.",
        )
    return number


def _transcript(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise Refusal("transcript_invalid", "Tarayıcının yazdığı cümle bir metin olmalı.")
    if len(value) > MAX_TRANSCRIPT_CHARS:
        raise Refusal(
            "transcript_too_long",
            f"Tarayıcının yazdığı cümle en çok {MAX_TRANSCRIPT_CHARS} karakter olabilir.",
        )
    return value


def _engine(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise Refusal("engine_invalid", "Tarayıcı tanıyıcısının adı bir metin olmalı.")
    if len(value) > MAX_ENGINE_CHARS:
        raise Refusal(
            "engine_too_long",
            f"Tarayıcı tanıyıcısının adı en çok {MAX_ENGINE_CHARS} karakter olabilir.",
        )
    return value


def _capture(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    invalid = Refusal(
        "capture_invalid",
        "Mikrofon ayarları yalnızca doğru/yanlış, sayı ve en çok "
        f"{MAX_CAPTURE_STRING_CHARS} karakterlik metin içerebilir.",
    )
    if not isinstance(value, dict):
        raise invalid
    if len(value) > MAX_CAPTURE_KEYS:
        raise Refusal(
            "capture_too_many_keys",
            f"Mikrofon ayarları en çok {MAX_CAPTURE_KEYS} alan içerebilir.",
        )
    for key, setting in value.items():
        if not isinstance(key, str) or not key or len(key) > MAX_CAPTURE_KEY_CHARS:
            raise invalid
        if isinstance(setting, bool):
            continue
        if isinstance(setting, int | float):
            if not math.isfinite(setting):
                raise invalid
            continue
        if isinstance(setting, str) and len(setting) <= MAX_CAPTURE_STRING_CHARS:
            continue
        raise invalid
    return dict(value)


def _audio(audio_wav_base64: object) -> tuple[bytes, int]:
    """The WAV bytes and their duration, or the refusal that names what is wrong."""
    too_large = Refusal(
        "audio_too_large",
        f"Kayıt çok büyük; en çok {MAX_AUDIO_BYTES} bayt olabilir.",
        status=413,
    )
    if not isinstance(audio_wav_base64, str):
        raise Refusal("audio_missing", "Kayıt gönderilmedi; cümleyi yeniden oku.")
    if len(audio_wav_base64) > MAX_BASE64_CHARS:
        raise too_large
    try:
        audio = base64.b64decode(audio_wav_base64, validate=True)
    except (binascii.Error, ValueError):
        raise Refusal("not_base64", "Kayıt okunamadı: base64 biçiminde değil.") from None
    if len(audio) > MAX_AUDIO_BYTES:
        raise too_large
    info = wav_info(audio)
    if info is None:
        raise Refusal("not_wav", "Kayıt bir WAV dosyası değil.")
    rate, channels, bits, data_bytes = info
    if bits != 16:
        raise Refusal("wav_not_16bit", "Kayıt 16 bit PCM olmalı.")
    if channels != 1:
        raise Refusal("wav_not_mono", "Kayıt tek kanallı (mono) olmalı.")
    if rate != SAMPLE_RATE:
        raise Refusal("wav_wrong_sample_rate", f"Kayıt {SAMPLE_RATE} Hz olmalı.")
    frames = data_bytes // 2
    if frames <= 0:
        raise Refusal("wav_empty", "Kayıt boş; cümleyi yeniden oku.")
    if frames > MAX_SECONDS * SAMPLE_RATE:
        raise Refusal("wav_too_long", f"Kayıt en çok {MAX_SECONDS} saniye sürebilir.")
    return audio, wav_duration_ms(audio)


class Recordings:
    """The forty slots under ``root`` of one object store."""

    def __init__(self, store: ObjectStore, *, root: str = ROOT) -> None:
        self._store = store
        self._root = root

    # ---------------------------------------------------------------- the store, guarded

    def _audio_key(self, place: str, index: int) -> str:
        return f"{self._root}/{place}/{index:02d}.wav"

    def _sidecar_key(self, place: str, index: int) -> str:
        return f"{self._root}/{place}/{index:02d}.json"

    def _fault(self, operation: str, exc: Exception) -> StoreUnavailable:
        logger.warning(
            "measurement_store_unavailable", operation=operation, error=type(exc).__name__
        )
        return StoreUnavailable(operation)

    def _get(self, key: str) -> bytes | None:
        try:
            return self._store.get(key)
        except KeyError:
            return None
        except Exception as exc:  # noqa: BLE001 - any provider fault is one answer
            raise self._fault("get", exc) from exc

    def _exists(self, key: str) -> bool:
        try:
            return bool(self._store.exists(key))
        except Exception as exc:  # noqa: BLE001
            raise self._fault("exists", exc) from exc

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self._store.put(key, data, content_type)
        except Exception as exc:  # noqa: BLE001
            raise self._fault("put", exc) from exc

    def _delete(self, key: str) -> None:
        try:
            self._store.delete(key)
        except Exception as exc:  # noqa: BLE001
            raise self._fault("delete", exc) from exc

    def _sidecar(self, place: str, index: int) -> tuple[bool, dict[str, Any] | None]:
        """``(present, item)``: the item is None when the sidecar is absent or unreadable."""
        raw = self._get(self._sidecar_key(place, index))
        if raw is None:
            return False, None
        try:
            document = json.loads(raw.decode("utf-8"))
            if not isinstance(document, dict):
                raise ValueError("not an object")
            item = {field: document[field] for field in _ITEM_FIELDS}
            expires_at = datetime.fromisoformat(str(item["expires_at"]).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                raise ValueError("expires_at has no time zone")
        except (ValueError, KeyError):
            return True, None
        return True, item

    @staticmethod
    def _expired(item: dict[str, Any], now: datetime) -> bool:
        expires_at = datetime.fromisoformat(str(item["expires_at"]).replace("Z", "+00:00"))
        return now > expires_at

    def _live(self, place: str, index: int, now: datetime) -> dict[str, Any] | None:
        _, item = self._sidecar(place, index)
        if item is None or self._expired(item, now):
            return None
        if not self._exists(self._audio_key(place, index)):
            return None
        return item

    def _drop(self, place: str, index: int) -> None:
        self._delete(self._audio_key(place, index))
        self._delete(self._sidecar_key(place, index))

    # ---------------------------------------------------------------- write

    def save(
        self,
        place: object,
        index: object,
        *,
        audio_wav_base64: object,
        browser_transcript: object = None,
        browser_engine: object = None,
        capture: object = None,
        now: datetime,
    ) -> dict[str, Any]:
        """Validate, then write the audio and its sidecar; a re-save replaces both and
        restarts the thirty days. A refusal writes nothing."""
        place_name, number = _place(place), _index(index)
        transcript = _transcript(browser_transcript)
        engine = _engine(browser_engine)
        settings = _capture(capture)
        audio, audio_ms = _audio(audio_wav_base64)
        item: dict[str, Any] = {
            "place": place_name,
            "index": number,
            "file": file_name(place_name, number),
            "reference": OWNER_SENTENCES[number - 1],
            "recorded_at": _iso(now),
            "expires_at": _iso(now + timedelta(days=RETENTION_DAYS)),
            "audio_ms": audio_ms,
            "bytes": len(audio),
            "sha256": hashlib.sha256(audio).hexdigest(),
            "browser_transcript": transcript,
            "browser_engine": engine,
            "capture": settings,
        }
        sidecar = json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")
        with _LOCK:
            self._put(self._audio_key(place_name, number), audio, "audio/wav")
            try:
                self._put(self._sidecar_key(place_name, number), sidecar, "application/json")
            except StoreUnavailable:
                # The pair is half written: an audio whose sidecar says something else (or
                # nothing) is worse than an empty slot the owner reads again.
                try:
                    self._drop(place_name, number)
                except StoreUnavailable:
                    pass
                raise
        logger.info(
            "measurement_recording_saved",
            place=place_name,
            index=number,
            bytes=item["bytes"],
            sha256=item["sha256"],
        )
        return item

    def delete_one(self, place: object, index: object) -> int:
        place_name, number = _place(place), _index(index)
        with _LOCK:
            deleted = self._delete_slot(place_name, number)
        if deleted:
            logger.info("measurement_recording_deleted", place=place_name, index=number)
        return deleted

    def _delete_slot(self, place: str, index: int) -> int:
        existed = self._exists(self._sidecar_key(place, index)) or self._exists(
            self._audio_key(place, index)
        )
        self._drop(place, index)
        return int(existed)

    def delete_all(self) -> int:
        """Every recording of every place gone; the count of slots that held something."""
        with _LOCK:
            deleted = sum(self._delete_slot(place, index) for place, index in slots())
        logger.info("measurement_recordings_deleted_all", deleted=deleted)
        return deleted

    def purge(self, now: datetime) -> int:
        """Delete audio and sidecar of every expired recording, and of every half-written
        pair (no sidecar, an unreadable one, or one without its audio)."""
        purged = 0
        with _LOCK:
            for place, index in slots():
                present, item = self._sidecar(place, index)
                audio_present = self._exists(self._audio_key(place, index))
                if not present and not audio_present:
                    continue
                if item is not None and audio_present and not self._expired(item, now):
                    continue
                self._drop(place, index)
                purged += 1
                logger.info(
                    "measurement_recording_purged",
                    place=place,
                    index=index,
                    reason="expired" if item is not None and audio_present else "incomplete",
                )
        return purged

    # ---------------------------------------------------------------- read

    def list_items(self, now: datetime) -> list[dict[str, Any]]:
        """The live recordings, ``ev`` 1..20 then ``ofis`` 1..20. Never an expired one,
        whether a purge has run or not; never a sidecar whose audio is missing."""
        items = []
        for place, index in slots():
            item = self._live(place, index, now)
            if item is not None:
                items.append(item)
        return items

    def read_audio(self, place: object, index: object, now: datetime) -> bytes:
        place_name, number = _place(place), _index(index)
        missing = Refusal("not_found", "Bu cümlenin kaydı yok.", status=404)
        if self._live(place_name, number, now) is None:
            raise missing
        audio = self._get(self._audio_key(place_name, number))
        if audio is None:
            raise missing
        return audio

    def manifest(self, now: datetime, place: object = None) -> dict[str, Any]:
        """Exactly the ``manifest.json`` ``app.voice.stt_compare.load_manifest`` reads, plus
        ``ready_transcripts`` (only when Chrome's recogniser ran) and ``browser_engine``."""
        only = None if place is None else _place(place)
        entries = []
        for item in self.list_items(now):
            if only is not None and item["place"] != only:
                continue
            entry: dict[str, Any] = {
                "file": item["file"],
                "reference": item["reference"],
                "recorded_where": item["place"],
            }
            if item["browser_transcript"] is not None:
                entry["ready_transcripts"] = {READY_ENGINE: item["browser_transcript"]}
            entry["browser_engine"] = item["browser_engine"]
            entries.append(entry)
        return {"language": LANGUAGE, "items": entries}


class DailyPurge:
    """The thirty days, held by the server process. Called on the retention sweeper's clock
    (``app.maintenance``), it purges on the first call and then once ``interval_s`` (a day)
    after the last purge that succeeded; a purge that failed is tried again on the next call.
    One clock reading decides both whether it is due and what has expired."""

    def __init__(
        self,
        recordings: Callable[[], Recordings],
        *,
        interval_s: float = PURGE_INTERVAL_S,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._recordings = recordings
        self._interval_s = interval_s
        self._clock = clock
        self.last_purge_at: datetime | None = None

    def __call__(self) -> int:
        now = self._clock()
        if (
            self.last_purge_at is not None
            and (now - self.last_purge_at).total_seconds() < self._interval_s
        ):
            return 0
        purged = self._recordings().purge(now)
        self.last_purge_at = now
        return purged
