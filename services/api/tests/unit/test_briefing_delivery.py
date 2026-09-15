"""B15 req 281: the morning briefing, read aloud with no browser open.

`BriefingService.build` had exactly one caller before this batch — the voice tool — so the
whole morning experience required the owner to be awake, at a machine, with a browser open
and a live voice session running. That is the opposite of what a morning briefing is for.

The measurement that shapes this file: `desktop.play_audio` **truncates** at
`GreetingPlayer.MaxSeconds` (20 s) rather than refusing, and a representative full briefing
is about 33-39 seconds of Turkish speech. One WAV — which is what the roadmap's test plan
asked for — would mean the owner hears the greeting, the date, the weather and half the
system status, and never learns the rest existed, with nothing anywhere reporting a cut.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.briefing import delivery as briefing_delivery
from app.briefing.delivery import (
    DEVICE_MAX_CLIP_SECONDS,
    MAX_CLIP_CHARS,
    MAX_CLIPS,
    BriefingDelivery,
    estimated_seconds,
    speak_briefing,
    split_into_clips,
)

NOW = datetime(2026, 9, 13, 4, 15, tzinfo=UTC)

FULL_BRIEFING = " ".join(
    [
        "Günaydın efendim.",
        "Bugün 13 Eylül Pazar, saat 07 15.",
        "Şu an İstanbul'da hava 21 derece, parçalı bulutlu; en yüksek 27 derece bekleniyor.",
        "Sistem sağlıklı efendim; üretimde 0.6.0 sürümü çalışıyor, bir cihaz çevrimiçi.",
        "Gece boyunca üç otonom geliştirme etkinliği oldu: 3 tamamlandı, 0 başarısız oldu.",
        "Bir araştırma tamamlandı: kahve makinesi karşılaştırması.",
        "Bugün takviminizde iki etkinlik var: saat 10 00 toplantı ve saat 15 30 diş hekimi.",
        "Son haber: Merkez bankası faiz kararını açıkladı.",
    ]
)


# ---------------------------------------------------------------------- the split


def test_a_full_briefing_is_longer_than_the_device_will_play():
    """The measurement the whole design rests on. If this ever stops being true, the
    clip-splitting below is complexity nobody needs and should go."""
    assert estimated_seconds(FULL_BRIEFING) > DEVICE_MAX_CLIP_SECONDS


def test_every_clip_fits_inside_what_the_device_will_play():
    """Not "roughly fits". The device TRUNCATES rather than refusing, so a clip over the
    bound is a sentence the owner silently never hears."""
    for clip in split_into_clips(FULL_BRIEFING):
        assert estimated_seconds(clip) < DEVICE_MAX_CLIP_SECONDS, clip


def test_the_split_never_cuts_a_sentence_in_half():
    """A clip ending halfway through "bugün hava" is worse than one more clip."""
    for clip in split_into_clips(FULL_BRIEFING):
        assert clip.rstrip()[-1] in ".!?", clip


def test_every_sentence_survives_the_split():
    """The whole briefing is still there afterwards - nothing dropped between clips."""
    rejoined = " ".join(split_into_clips(FULL_BRIEFING))

    assert re.sub(r"\s+", " ", rejoined) == re.sub(r"\s+", " ", FULL_BRIEFING)


def test_a_single_sentence_over_the_bound_is_emitted_whole_and_alone():
    """The device will truncate it and the receipt will show a clip that did not fit, which
    is visible. A sentence this module chopped itself would be a lie it told quietly."""
    monster = "Bu " + "çok " * 200 + "uzun bir cümledir."

    clips = split_into_clips(monster)

    assert clips == [monster]


def test_an_empty_briefing_is_no_clips_rather_than_one_empty_one():
    assert split_into_clips("") == []
    assert split_into_clips("   \n  ") == []


# ------------------------------------------------- the bound is the device's own


def test_the_briefing_clip_bound_is_the_device_s_own():
    """Read from `GreetingPlayer.cs` itself, not restated from memory.

    The Cloud Core has to decide how to split BEFORE it sends anything, so it cannot ask
    the device. The same "read the other side's own file" discipline
    `test_desktop_capability_mirror` and `test_device_fakes_match_the_device` already use:
    two halves of one number, and this fails when they drift.
    """
    source = (
        Path(__file__).resolve().parents[4]
        / "devices/windows-agent/src/PagentOS.SessionCompanion/GreetingPlayer.cs"
    )
    if not source.exists():  # pragma: no cover - only in a partial checkout
        pytest.skip("the companion source is not present in this checkout")

    match = re.search(r"public const int MaxSeconds = (\d+);", source.read_text(encoding="utf-8"))

    assert match, "GreetingPlayer no longer declares MaxSeconds the way this test reads it"
    assert int(match.group(1)) == DEVICE_MAX_CLIP_SECONDS


def test_the_clip_budget_leaves_room_under_the_device_bound():
    """The split is an ESTIMATE from character count. Being wrong in the direction of a
    truncated clip is the one failure this module exists to prevent, so the budget sits
    under the device's bound rather than on it."""
    assert MAX_CLIP_CHARS / briefing_delivery.CHARS_PER_SECOND < DEVICE_MAX_CLIP_SECONDS


# ------------------------------------------------------------------ the delivery


class _Store:
    class _Handle:
        sha256 = "a" * 64
        size_bytes = 1024

        def path(self) -> str:
            return "/v1/alarms/audio/tok"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.puts = 0

    def put(self, audio: bytes, *, now=None):  # noqa: ANN001, ANN202, ARG002
        self.puts += 1
        if self.fail:
            raise RuntimeError("audio too large")
        return self._Handle()


class _Briefing:
    def __init__(self, speech: str = FULL_BRIEFING, boom: bool = False) -> None:
        self.speech = speech
        self.boom = boom
        self.calls = 0

    def build(self, session, **_kwargs):  # noqa: ANN001, ANN202, ARG002
        self.calls += 1
        if self.boom:
            raise RuntimeError("the weather service hung")
        return {"speech": self.speech}


class _Tts:
    name = "tts-that-speaks"


def _player(fail_at: int | None = None):
    played: list[str] = []

    def play(text, *, audio_id, url, sha256, size_bytes, max_seconds):  # noqa: ANN001, ARG001
        played.append(audio_id)
        return not (fail_at is not None and len(played) == fail_at)

    return play, played


@pytest.fixture(autouse=True)
def _synthesises(monkeypatch):
    """A real WAV is not what this file is about; that path is `test_alarms_sequence`'s."""
    monkeypatch.setattr(
        briefing_delivery, "tts_synthesise", lambda tts, text: b"RIFF" + text.encode()
    )


def _speak(**overrides: Any) -> BriefingDelivery:
    play, _ = overrides.pop("player", (None, None)) or (None, None)
    kwargs: dict[str, Any] = {
        "briefing_service": _Briefing(),
        "settings": object(),
        "live": {},
        "tts": _Tts(),
        "audio_store": _Store(),
        "audio_origin": "https://broker.example",
        "play": play or (lambda *a, **k: True),
        "now": NOW,
    }
    kwargs.update(overrides)
    return speak_briefing(None, **kwargs)


def test_the_whole_briefing_is_spoken_as_several_clips_in_order():
    play, played = _player()

    delivery = _speak(player=(play, played))

    assert delivery.ok
    assert delivery.clips > 1, "a full briefing does not fit in one clip"
    assert len(played) == delivery.clips
    assert played == sorted(played, key=lambda a: int(a.split("-")[1]))


def test_a_clip_that_did_not_play_stops_the_briefing_and_says_how_far_it_got():
    """A briefing with a hole in it is worse than a short one: the owner cannot tell which
    part they missed."""
    play, played = _player(fail_at=2)

    delivery = _speak(player=(play, played))

    assert not delivery.ok
    assert delivery.failure == "clip_not_played:2"
    assert len(delivery.spoken) == 1
    assert len(played) == 2, "it stopped rather than carrying on past the failure"


def test_a_briefing_that_raises_is_a_reason_not_an_exception():
    """This runs on the wake path. A weather service that hangs must not be why an alarm
    stopped ringing."""
    delivery = _speak(briefing_service=_Briefing(boom=True))

    assert delivery.failure.startswith("build_failed:")
    assert delivery.spoken == []


def test_an_empty_briefing_says_so_rather_than_playing_silence():
    delivery = _speak(briefing_service=_Briefing(speech="   "))

    assert delivery.failure == "empty_briefing"


def test_a_keyless_deployment_does_not_buzz_at_the_owner(monkeypatch):
    """B13 req 267, inherited: the offline fake makes a 110 Hz sine, and a briefing is even
    less the place to offer one as speech than a greeting was."""
    monkeypatch.setattr(briefing_delivery, "tts_synthesise", lambda tts, text: None)

    delivery = _speak()

    assert delivery.failure == "no_tts_key"
    assert delivery.spoken == []


def test_a_store_that_refuses_is_a_reason_too():
    delivery = _speak(audio_store=_Store(fail=True))

    assert delivery.failure.startswith("audio_store_failed:")


def test_the_briefing_is_normalised_before_it_is_spoken():
    """The briefing's sentences carry clock times, temperatures and percentages that no
    normaliser had touched, because nothing spoke them aloud. Now something does."""
    seen: list[str] = []

    _speak(normalise=lambda text: (seen.append(text), text.upper())[1])

    assert seen and "Günaydın" in seen[0]


def test_a_broken_normaliser_never_silences_the_briefing():
    """An unreadable pronunciation table is not a reason to say nothing - the same rule
    `normalize_greeting` has followed since M18.3."""

    def _boom(_text: str) -> str:
        raise RuntimeError("the pronunciation table is corrupt")

    delivery = _speak(normalise=_boom)

    assert delivery.ok


def test_the_number_of_clips_is_bounded():
    """An unbounded loop of device commands at 07:15 is not something to discover in
    production."""
    long_briefing = " ".join(f"Cümle {i} burada ve biraz uzundur." for i in range(400))
    play, played = _player()

    delivery = _speak(briefing_service=_Briefing(speech=long_briefing), player=(play, played))

    assert delivery.clips == MAX_CLIPS
    assert len(played) == MAX_CLIPS


def test_the_receipt_says_what_the_owner_actually_heard():
    play, played = _player(fail_at=3)

    delivery = _speak(player=(play, played))

    assert delivery.as_dict() == {
        "clips": delivery.clips,
        "spoken": 2,
        # B20 req 233: and what they did not hear, which is what gets delivered as text.
        "unspoken": delivery.clips - 2,
        "failure": "clip_not_played:3",
        "complete": False,
    }
    assert delivery.unspoken and delivery.unspoken[0] not in delivery.spoken


# ------------------------------------------------------------------- the wiring


def test_the_application_gives_the_wake_sequence_a_briefing():
    """The guard. A delivery module the alarm path cannot reach is the defect this
    repository keeps paying for - and it would look exactly like a quiet morning."""
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))

    assert app.state.wake_sequence._briefing is not None
    assert app.state.wake_sequence._briefing_live.get("weather_service") is not None


def test_one_setting_turns_the_briefing_off_again():
    """The roadmap's own rollback plan for B15: turn the extension off and keep the
    greeting. That has to be a setting, not a revert."""
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None, alarm_briefing_enabled=False))

    assert app.state.wake_sequence._briefing is None


def test_the_greeting_still_happens_without_a_briefing():
    """A sequence built with no briefing greets the owner exactly as it did before B15."""
    from app.alarms.sequence import WakeSequence

    sequence = WakeSequence(device_action=object(), tts=None)

    assert sequence._speak_briefing(None, _FakeAlarm(), now=NOW) == []


class _FakeAlarm:
    id = uuid.uuid4()
    device_id = None
    snooze_count = 0
    detail_json: dict[str, Any] = {}
