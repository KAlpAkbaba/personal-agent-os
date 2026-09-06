"""Unit tests: app.alarms.speech (M18.3 spec §3.7, §6).

The exact sentences, and the one property that binds every one of them: no template may
claim a mutation the receipt did not verify.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.actions.receipt import (
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    contains_fake_completion,
)
from app.alarms import speech
from app.alarms.speech import (
    SPEECH_TEMPLATES,
    alarm_created_speech,
    alarm_query_speech,
    alarm_snoozed_speech,
    clock_words_tr,
    daypart_greeting,
    display_off_speech,
    display_status_speech,
    display_wake_speech,
    greeting_text,
    spoken_clock_tr,
    when_phrase,
)

IST = ZoneInfo("Europe/Istanbul")


@pytest.mark.parametrize(
    ("clock", "spoken"),
    [
        ("07:00", "yedi"),
        ("07:30", "yedi buçuk"),
        ("07:15", "yediyi çeyrek geçiyor"),
        ("07:45", "sekize çeyrek var"),
        ("07:42", "yedi kırk iki"),
        ("00:00", "on iki"),
        ("12:00", "on iki"),
        ("21:05", "dokuz beş"),
    ],
)
def test_spoken_clock_tr(clock: str, spoken: str) -> None:
    """Spec §3.7's table, exactly."""
    assert spoken_clock_tr(clock) == spoken


def test_spoken_clock_accepts_a_datetime_and_reads_its_local_fields() -> None:
    assert spoken_clock_tr(datetime(2026, 9, 10, 7, 30, tzinfo=IST)) == "yedi buçuk"


@pytest.mark.parametrize(
    ("hour", "greeting"),
    [(7, "Günaydın efendim"), (11, "Günaydın efendim"), (12, "İyi günler efendim"),
     (17, "İyi günler efendim"), (18, "İyi akşamlar efendim"), (23, "İyi akşamlar efendim")],
)
def test_daypart_greeting(hour: int, greeting: str) -> None:
    assert daypart_greeting(datetime(2026, 9, 10, hour, 0, tzinfo=IST)) == greeting


def test_greeting_text_is_the_spec_sentence() -> None:
    moment = datetime(2026, 9, 10, 7, 30, tzinfo=IST)
    assert greeting_text(moment) == "Günaydın efendim. Saat yedi buçuk. Alarmınız çalıyor."


def test_a_test_alarm_says_so_in_its_greeting() -> None:
    moment = datetime(2026, 9, 10, 7, 30, tzinfo=IST)
    assert greeting_text(moment, is_test=True).endswith("Test alarmınız çalıyor.")


# ------------------------------------------------------------------ the receipt table


def test_clock_words_read_back_the_exact_time_not_a_rounding() -> None:
    """The confirmation sentence reads the digits back (spec §6's "yedi otuza"), while the
    GREETING tells the time the way a person says it (§3.7's "yedi buçuk"). Two readings on
    purpose — this test is what stops someone collapsing them."""
    assert clock_words_tr("07:30") == "yedi otuz"
    assert spoken_clock_tr("07:30") == "yedi buçuk"


def test_alarm_created_speech_matches_the_spec_sentences() -> None:
    assert (
        alarm_created_speech(local_time="07:30", tomorrow=True)
        == "Alarmı yarın yedi otuza kurdum efendim."
    )
    assert (
        alarm_created_speech(local_time="07:31", relative_seconds=90, is_test=True)
        == "Test alarmını doksan saniye sonraya kurdum efendim."
    )


def test_alarm_created_speech_names_a_recurrence() -> None:
    said = alarm_created_speech(local_time="07:15", weekdays=(0, 1, 2, 3, 4))
    assert "her hafta içi" in said
    assert said.endswith("kurdum efendim.")


def test_alarm_query_and_snooze_sentences() -> None:
    assert alarm_query_speech("07:30") == "Sabah alarmınız yedi otuzda efendim."
    assert alarm_query_speech(None) == "Kurulu alarm yok efendim."
    assert (
        alarm_snoozed_speech(minutes=5, local_time="07:35")
        == "Beş dakika erteledim efendim; yedi otuz beşte tekrar çalacak."
    )


def test_when_phrase_uses_the_dative() -> None:
    assert when_phrase(local_time="08:00") == "sekize"
    assert when_phrase(local_time="07:30") == "yedi otuza"
    assert when_phrase(local_time="04:00") == "dörde"  # softening: dört -> dörde


def test_display_off_speech_covers_every_outcome_the_spec_names() -> None:
    assert display_off_speech(terminal_status=TERMINAL_VERIFIED) == "Ekranları kapattım efendim."
    assert display_off_speech(terminal_status=TERMINAL_ALREADY) == "Ekranlar zaten kapalı efendim."
    assert (
        display_off_speech(terminal_status=TERMINAL_FAILED, refused="recent_input")
        == "Ekranı kapatmadım efendim; az önce klavye kullanıldı."
    )
    assert (
        display_off_speech(terminal_status=TERMINAL_FAILED, refused="alarm_active")
        == "Ekranı kapatmadım efendim; alarm çalıyor."
    )
    assert (
        display_off_speech(terminal_status=TERMINAL_FAILED, error_class="no_capable_device")
        == "Ekranları kapatamadım efendim; uygun bir cihaz yok."
    )


def test_a_device_refusal_wins_over_the_terminal_status() -> None:
    """The device saying "no, the owner just used the keyboard" is a DIFFERENT fact from a
    failure, and the owner hears the reason (spec §1.3)."""
    said = display_off_speech(terminal_status=TERMINAL_FAILED, refused="recent_input")
    assert "klavye" in said
    assert "kapatamadım" not in said


def test_display_wake_and_status_speech() -> None:
    assert display_wake_speech(terminal_status=TERMINAL_VERIFIED) == "Ekranları açtım efendim."
    assert display_wake_speech(terminal_status=TERMINAL_ALREADY) == "Ekranlar zaten açık efendim."
    assert display_status_speech("on") == "Ekranlar açık efendim."
    assert display_status_speech("off") == "Ekranlar kapalı efendim."
    assert display_status_speech(None) == "Ekranların durumunu okuyamıyorum efendim."


# ------------------------------------------------------------------ the binding property


def test_no_template_contains_a_banned_fake_completion_phrase() -> None:
    """The same sweep ``test_actions_receipt.py`` runs over the eye's table, over this one.

    On 2026-09-06 the model answered a physical command with "öyle olmuş gibi düşün". These
    sentences are read verbatim, so a banned phrase reaching one of them would be that
    failure again with a wake alarm behind it.
    """
    for template in SPEECH_TEMPLATES:
        assert not contains_fake_completion(template), template


def test_every_module_level_sentence_is_in_the_swept_table() -> None:
    """A sentence added without being added to SPEECH_TEMPLATES would escape the sweep."""
    swept = set(SPEECH_TEMPLATES)
    for name in dir(speech):
        if not name.endswith("_TR") or name.startswith("_"):
            continue
        value = getattr(speech, name)
        if isinstance(value, str):
            assert value in swept, f"{name} is not in SPEECH_TEMPLATES"


def test_every_rendered_sentence_addresses_the_owner_as_efendim() -> None:
    """House style for this system's voice (VOICE_SPEC): the assistant addresses its one
    owner. Checked on the RENDERED sentences, not the templates, so a format() that dropped
    the ending would be caught."""
    rendered = [
        alarm_created_speech(local_time="07:30", tomorrow=True),
        alarm_query_speech("07:30"),
        alarm_snoozed_speech(minutes=5, local_time="07:35"),
        display_off_speech(terminal_status=TERMINAL_VERIFIED),
        display_wake_speech(terminal_status=TERMINAL_VERIFIED),
        speech.AMBIENT_TEST_STARTED_TR.format(seconds=10),
    ]
    for sentence in rendered:
        assert "efendim" in sentence, sentence
