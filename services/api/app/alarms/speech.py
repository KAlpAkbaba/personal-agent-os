"""What the assistant says about an alarm or a display (M18.3 spec §3.7, §6).

Three things live here and nothing else:

1. :func:`spoken_clock_tr` — the Turkish reading of a wall clock, the way a person says
   it: ``07:00`` "yedi", ``07:30`` "yedi buçuk", ``07:15`` "yediyi çeyrek geçiyor",
   ``07:45`` "sekize çeyrek var", anything else "yedi kırk iki". Digits go through
   ``app.narration.numbers.cardinal`` so there is ONE Turkish number reader in this
   codebase, not a second one that will drift from it.
2. :func:`greeting_text` — the wake greeting, daypart-aware.
3. The receipt speech table of spec §6 — exact strings, one function per capability, keyed
   by the terminal status and the error class the same way
   ``app.actions.receipt.eye_speech`` is. Every sentence here is swept by
   ``tests/unit/test_alarms_speech.py`` against
   ``app.actions.receipt.FAKE_COMPLETION_PHRASES``: no template may ever claim a mutation
   the receipt did not verify.

Nothing here reads the database, the clock or a device. A caller passes what it read back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from app.actions.receipt import TERMINAL_ALREADY, TERMINAL_VERIFIED
from app.narration.numbers import cardinal

# ------------------------------------------------------------------ the clock

_QUARTER_PAST_SUFFIX: Final[dict[int, str]] = {
    # "saati çeyrek geçiyor" needs the accusative of the hour: yedi -> yediyi.
    1: "biri",
    2: "ikiyi",
    3: "üçü",
    4: "dördü",
    5: "beşi",
    6: "altıyı",
    7: "yediyi",
    8: "sekizi",
    9: "dokuzu",
    10: "onu",
    11: "on biri",
    12: "on ikiyi",
}

_QUARTER_TO_SUFFIX: Final[dict[int, str]] = {
    # "saate çeyrek var" needs the dative: sekiz -> sekize.
    1: "bire",
    2: "ikiye",
    3: "üçe",
    4: "dörde",
    5: "beşe",
    6: "altıya",
    7: "yediye",
    8: "sekize",
    9: "dokuza",
    10: "ona",
    11: "on bire",
    12: "on ikiye",
}


def _twelve(hour: int) -> int:
    """1..12 for the hour a Turkish speaker names ("yirmi bir" is said "dokuz" only with a
    daypart word; the greeting always carries one, so the 12-hour reading is the natural
    one). 0 reads as 12."""
    value = hour % 12
    return 12 if value == 0 else value


def spoken_clock_tr(moment: datetime | str) -> str:
    """The Turkish reading of a wall-clock time (spec §3.7).

    Accepts a datetime (whose LOCAL fields are read as-is — the caller has already
    converted to the owner's zone; this module never guesses a timezone) or an "HH:MM"
    string.
    """
    if isinstance(moment, str):
        hour_s, _, minute_s = moment.partition(":")
        hour, minute = int(hour_s), int(minute_s)
    else:
        hour, minute = moment.hour, moment.minute
    twelve = _twelve(hour)
    if minute == 0:
        return cardinal(twelve)
    if minute == 30:
        return f"{cardinal(twelve)} buçuk"
    if minute == 15:
        return f"{_QUARTER_PAST_SUFFIX[twelve]} çeyrek geçiyor"
    if minute == 45:
        return f"{_QUARTER_TO_SUFFIX[_twelve(hour + 1)]} çeyrek var"
    return f"{cardinal(twelve)} {cardinal(minute)}"


# ------------------------------------------------------------------ the greeting

GREETING_MORNING: Final = "Günaydın efendim"
GREETING_DAY: Final = "İyi günler efendim"
GREETING_EVENING: Final = "İyi akşamlar efendim"

RINGING_SENTENCE: Final = "Alarmınız çalıyor."
TEST_RINGING_SENTENCE: Final = "Test alarmınız çalıyor."


def daypart_greeting(moment: datetime) -> str:
    """Before 12:00 "Günaydın efendim", 12:00-17:59 "İyi günler efendim", after "İyi
    akşamlar efendim" (spec §3.7). Read from the LOCAL fields the caller supplies."""
    if moment.hour < 12:
        return GREETING_MORNING
    if moment.hour < 18:
        return GREETING_DAY
    return GREETING_EVENING


def greeting_text(moment: datetime, *, is_test: bool = False) -> str:
    """"Günaydın efendim. Saat yedi buçuk. Alarmınız çalıyor." (spec §3.7).

    ``moment`` must already be in the owner's timezone — this module never converts, so a
    caller that hands it a UTC instant gets a UTC clock, which is a caller bug the alarm
    service's own tests pin rather than something to paper over here.
    """
    ringing = TEST_RINGING_SENTENCE if is_test else RINGING_SENTENCE
    return f"{daypart_greeting(moment)}. Saat {spoken_clock_tr(moment)}. {ringing}"


# ------------------------------------------------------------------ the receipt table

ALARM_CREATED_TR: Final = "Alarmı {when} kurdum efendim."
ALARM_TEST_CREATED_TR: Final = "Test alarmını {when} kurdum efendim."
ALARM_CANCELLED_TR: Final = "Alarmı iptal ettim efendim."
ALARM_STOPPED_TR: Final = "Alarmı kapattım efendim."
ALARM_SNOOZED_TR: Final = "{minutes} erteledim efendim; {when} tekrar çalacak."
ALARM_QUERY_TR: Final = "Sabah alarmınız {when} efendim."
ALARM_QUERY_NONE_TR: Final = "Kurulu alarm yok efendim."
ALARM_NOT_RINGING_TR: Final = "Şu anda çalan bir alarm yok efendim."
ALARM_CREATE_NEEDS_MEDIA_TR: Final = (
    "Alarmı kurdum efendim ama hangi müzik olduğunu bilmiyorum; bağlantısını verir misiniz?"
)
ALARM_CREATE_REFUSED_MEDIA_TR: Final = (
    "Hangi müzikle uyandırmamı istediğinizi bilmiyorum efendim; bağlantısını verirseniz "
    "tekrarlayan alarmı kurarım."
)
ALARM_CREATE_UNPARSED_TR: Final = (
    "Saati anlayamadım efendim; kaçta uyanmak istediğinizi söyler misiniz?"
)
ALARM_FAILED_TR: Final = "Alarmı çaldıramadım efendim; ne müzik ne de zil sesi çalıştı."

DISPLAY_OFF_VERIFIED_TR: Final = "Ekranları kapattım efendim."
DISPLAY_OFF_ALREADY_TR: Final = "Ekranlar zaten kapalı efendim."
DISPLAY_OFF_REFUSED_RECENT_INPUT_TR: Final = (
    "Ekranı kapatmadım efendim; az önce klavye kullanıldı."
)
DISPLAY_OFF_REFUSED_ALARM_TR: Final = "Ekranı kapatmadım efendim; alarm çalıyor."
DISPLAY_OFF_FAILED_TR: Final = "Ekranları kapatamadım efendim; işlem doğrulanmadı."
DISPLAY_OFF_NO_DEVICE_TR: Final = "Ekranları kapatamadım efendim; uygun bir cihaz yok."
DISPLAY_WAKE_VERIFIED_TR: Final = "Ekranları açtım efendim."
DISPLAY_WAKE_ALREADY_TR: Final = "Ekranlar zaten açık efendim."
DISPLAY_WAKE_FAILED_TR: Final = "Ekranları açamadım efendim; işlem doğrulanmadı."
DISPLAY_STATUS_ON_TR: Final = "Ekranlar açık efendim."
DISPLAY_STATUS_OFF_TR: Final = "Ekranlar kapalı efendim."
DISPLAY_STATUS_UNKNOWN_TR: Final = "Ekranların durumunu okuyamıyorum efendim."

AMBIENT_AUTO_OFF_ON_TR: Final = "Otomatik ekran kapatmayı açtım efendim."
AMBIENT_AUTO_OFF_OFF_TR: Final = "Otomatik ekran kapatmayı kapattım efendim."
AMBIENT_POLICY_UPDATED_TR: Final = "Ekran ayarını güncelledim efendim."
AMBIENT_TEST_STARTED_TR: Final = (
    "Ekran testini başlattım efendim; {seconds} saniye sonra ekranlar kapanacak, bir tuşa "
    "basınca açılacak."
)

#: Every template this module can emit, so one test can sweep them all against the banned
#: fake-completion phrases (the same discipline as
#: ``app.actions.receipt.SPEECH_TEMPLATES``).
SPEECH_TEMPLATES: Final[tuple[str, ...]] = (
    ALARM_CREATED_TR,
    ALARM_TEST_CREATED_TR,
    ALARM_CANCELLED_TR,
    ALARM_STOPPED_TR,
    ALARM_SNOOZED_TR,
    ALARM_QUERY_TR,
    ALARM_QUERY_NONE_TR,
    ALARM_NOT_RINGING_TR,
    ALARM_CREATE_NEEDS_MEDIA_TR,
    ALARM_CREATE_REFUSED_MEDIA_TR,
    ALARM_CREATE_UNPARSED_TR,
    ALARM_FAILED_TR,
    DISPLAY_OFF_VERIFIED_TR,
    DISPLAY_OFF_ALREADY_TR,
    DISPLAY_OFF_REFUSED_RECENT_INPUT_TR,
    DISPLAY_OFF_REFUSED_ALARM_TR,
    DISPLAY_OFF_FAILED_TR,
    DISPLAY_OFF_NO_DEVICE_TR,
    DISPLAY_WAKE_VERIFIED_TR,
    DISPLAY_WAKE_ALREADY_TR,
    DISPLAY_WAKE_FAILED_TR,
    DISPLAY_STATUS_ON_TR,
    DISPLAY_STATUS_OFF_TR,
    DISPLAY_STATUS_UNKNOWN_TR,
    AMBIENT_AUTO_OFF_ON_TR,
    AMBIENT_AUTO_OFF_OFF_TR,
    AMBIENT_POLICY_UPDATED_TR,
    AMBIENT_TEST_STARTED_TR,
    GREETING_MORNING,
    GREETING_DAY,
    GREETING_EVENING,
    RINGING_SENTENCE,
    TEST_RINGING_SENTENCE,
)

#: The device's own refusal reasons (Track D, spec §5.1) mapped to their sentences.
REFUSAL_RECENT_INPUT: Final = "recent_input"
REFUSAL_ALARM_ACTIVE: Final = "alarm_active"

_DISPLAY_OFF_REFUSAL_SPEECH: Final[dict[str, str]] = {
    REFUSAL_RECENT_INPUT: DISPLAY_OFF_REFUSED_RECENT_INPUT_TR,
    REFUSAL_ALARM_ACTIVE: DISPLAY_OFF_REFUSED_ALARM_TR,
}


# ------------------------------------------------------- case suffixes for a clock

#: Turkish vowel harmony, on the LAST vowel of a word.
_BACK_VOWELS: Final[frozenset[str]] = frozenset("aıou")
#: A locative suffix hardens to -te/-ta after a voiceless final consonant.
_VOICELESS_FINALS: Final[frozenset[str]] = frozenset("fstkçşhp")
#: Final-consonant softening a suffix triggers ("dört" -> "dörde", not "dörte").
_SOFTENED_STEMS: Final[dict[str, str]] = {"dört": "dörd"}


def _last_vowel(word: str) -> str:
    for ch in reversed(word):
        if ch in "aeıioöuü":
            return ch
    return "e"


def _dative(words: str) -> str:
    """"yedi" -> "yediye", "otuz" -> "otuza", "dört" -> "dörde" (spec §6's "yedi otuza")."""
    head, _, last = words.rpartition(" ")
    stem = _SOFTENED_STEMS.get(last, last)
    vowel = "a" if _last_vowel(stem) in _BACK_VOWELS else "e"
    buffer = "y" if stem[-1] in "aeıioöuü" else ""
    out = f"{stem}{buffer}{vowel}"
    return f"{head} {out}" if head else out


def _locative(words: str) -> str:
    """"yedi otuz" -> "yedi otuzda", "yedi otuz beş" -> "yedi otuz beşte" (spec §6)."""
    head, _, last = words.rpartition(" ")
    vowel = "a" if _last_vowel(last) in _BACK_VOWELS else "e"
    consonant = "t" if last[-1] in _VOICELESS_FINALS else "d"
    out = f"{last}{consonant}{vowel}"
    return f"{head} {out}" if head else out


def clock_words_tr(local_time: str) -> str:
    """The plain digit reading a receipt sentence uses: ``07:30`` -> "yedi otuz",
    ``07:00`` -> "yedi", ``07:35`` -> "yedi otuz beş".

    Deliberately NOT :func:`spoken_clock_tr`: that one is how a person tells the time out
    loud ("yedi buçuk") and belongs in the greeting, while a confirmation sentence reads
    back the exact time the owner set, so "yedi otuza kurdum" cannot be misheard as a
    rounding. Spec §3.7 fixes the first and §6 the second; they are two readings on
    purpose.
    """
    hour_s, _, minute_s = local_time.partition(":")
    hour, minute = int(hour_s), int(minute_s)
    if minute == 0:
        return cardinal(_twelve(hour))
    return f"{cardinal(_twelve(hour))} {cardinal(minute)}"


def when_phrase(*, local_time: str, tomorrow: bool = False, relative_seconds: int | None = None,
                weekdays: tuple[int, ...] | list[int] = ()) -> str:
    """The dative "when" fragment of a creation sentence: "yarın yedi otuza",
    "doksan saniye sonraya", "her hafta içi yedi on beşe"."""
    if relative_seconds is not None:
        return f"{cardinal(relative_seconds)} saniye sonraya"
    clock = _dative(clock_words_tr(local_time))
    if weekdays:
        prefix = "her hafta içi" if tuple(sorted(weekdays)) == (0, 1, 2, 3, 4) else "her gün"
        return f"{prefix} {clock}"
    return f"yarın {clock}" if tomorrow else clock


def at_phrase(local_time: str) -> str:
    """The locative "when" fragment of a query/snooze sentence: "yedi otuzda"."""
    return _locative(clock_words_tr(local_time))


def display_off_speech(
    *, terminal_status: str, error_class: str | None = None, refused: str | None = None
) -> str:
    """The exact sentence for a ``display.off`` receipt (spec §6).

    A device REFUSAL (``recent_input`` / ``alarm_active``) is its own sentence, because the
    owner asked for something and a truthful "I did not, and here is why" is a different
    fact from a failure.
    """
    if refused in _DISPLAY_OFF_REFUSAL_SPEECH:
        return _DISPLAY_OFF_REFUSAL_SPEECH[refused]
    if terminal_status == TERMINAL_VERIFIED:
        return DISPLAY_OFF_VERIFIED_TR
    if terminal_status == TERMINAL_ALREADY:
        return DISPLAY_OFF_ALREADY_TR
    if error_class == "no_capable_device":
        return DISPLAY_OFF_NO_DEVICE_TR
    return DISPLAY_OFF_FAILED_TR


def display_wake_speech(*, terminal_status: str, error_class: str | None = None) -> str:
    if terminal_status == TERMINAL_VERIFIED:
        return DISPLAY_WAKE_VERIFIED_TR
    if terminal_status == TERMINAL_ALREADY:
        return DISPLAY_WAKE_ALREADY_TR
    if error_class == "no_capable_device":
        return DISPLAY_OFF_NO_DEVICE_TR
    return DISPLAY_WAKE_FAILED_TR


def display_status_speech(state: str | None) -> str:
    if state == "on":
        return DISPLAY_STATUS_ON_TR
    if state == "off":
        return DISPLAY_STATUS_OFF_TR
    return DISPLAY_STATUS_UNKNOWN_TR


def alarm_snoozed_speech(*, minutes: int, local_time: str) -> str:
    """"Beş dakika erteledim efendim; yedi otuz beşte tekrar çalacak." (spec §6)."""
    return ALARM_SNOOZED_TR.format(
        minutes=f"{cardinal(minutes)} dakika", when=at_phrase(local_time)
    )


def alarm_created_speech(
    *,
    local_time: str,
    tomorrow: bool = False,
    relative_seconds: int | None = None,
    weekdays: tuple[int, ...] | list[int] = (),
    is_test: bool = False,
) -> str:
    """"Alarmı yarın yedi otuza kurdum efendim." / "Test alarmını doksan saniye sonraya
    kurdum efendim." (spec §6)."""
    when = when_phrase(
        local_time=local_time,
        tomorrow=tomorrow,
        relative_seconds=relative_seconds,
        weekdays=weekdays,
    )
    template = ALARM_TEST_CREATED_TR if is_test else ALARM_CREATED_TR
    return template.format(when=when)


def alarm_query_speech(local_time: str | None) -> str:
    """"Sabah alarmınız yedi otuzda efendim." / "Kurulu alarm yok efendim." (spec §6)."""
    if not local_time:
        return ALARM_QUERY_NONE_TR
    return ALARM_QUERY_TR.format(when=at_phrase(local_time))


__all__ = [
    "ALARM_CANCELLED_TR",
    "ALARM_CREATED_TR",
    "ALARM_CREATE_NEEDS_MEDIA_TR",
    "ALARM_CREATE_REFUSED_MEDIA_TR",
    "ALARM_CREATE_UNPARSED_TR",
    "ALARM_FAILED_TR",
    "ALARM_NOT_RINGING_TR",
    "ALARM_QUERY_NONE_TR",
    "ALARM_QUERY_TR",
    "ALARM_SNOOZED_TR",
    "ALARM_STOPPED_TR",
    "ALARM_TEST_CREATED_TR",
    "AMBIENT_AUTO_OFF_OFF_TR",
    "AMBIENT_AUTO_OFF_ON_TR",
    "AMBIENT_POLICY_UPDATED_TR",
    "AMBIENT_TEST_STARTED_TR",
    "DISPLAY_OFF_ALREADY_TR",
    "DISPLAY_OFF_FAILED_TR",
    "DISPLAY_OFF_NO_DEVICE_TR",
    "DISPLAY_OFF_REFUSED_ALARM_TR",
    "DISPLAY_OFF_REFUSED_RECENT_INPUT_TR",
    "DISPLAY_OFF_VERIFIED_TR",
    "DISPLAY_STATUS_OFF_TR",
    "DISPLAY_STATUS_ON_TR",
    "DISPLAY_STATUS_UNKNOWN_TR",
    "DISPLAY_WAKE_ALREADY_TR",
    "DISPLAY_WAKE_FAILED_TR",
    "DISPLAY_WAKE_VERIFIED_TR",
    "GREETING_DAY",
    "GREETING_EVENING",
    "GREETING_MORNING",
    "REFUSAL_ALARM_ACTIVE",
    "REFUSAL_RECENT_INPUT",
    "RINGING_SENTENCE",
    "SPEECH_TEMPLATES",
    "TEST_RINGING_SENTENCE",
    "alarm_created_speech",
    "alarm_query_speech",
    "alarm_snoozed_speech",
    "at_phrase",
    "clock_words_tr",
    "daypart_greeting",
    "display_off_speech",
    "display_status_speech",
    "display_wake_speech",
    "greeting_text",
    "spoken_clock_tr",
    "when_phrase",
]
