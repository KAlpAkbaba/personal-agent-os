"""B20 req 233: what could not be said is still told — in text.

Two paths speak to the owner without a browser open: the wake greeting and the morning
briefing. Both already failed politely and both already had the words in hand:

* the greeting sentence was composed and normalised, then dropped on the floor with
  `greeting_failure="no_tts_key"` written to a database row the owner has no reason to
  read — and because the briefing only ran after a greeting that was actually spoken, the
  same failure silently took the briefing with it;
* the briefing stopped at the clip that would not play, recorded how far it got, and threw
  away the sentences the owner never heard.

An owner whose TTS credit ran out overnight therefore woke to silence and found nothing,
anywhere, about their morning. The rule these tests pin: **a failure to SPEAK is never a
failure to TELL.**
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.alarms import service as alarms_service
from app.alarms.greeting_audio import FALLBACK_PROVIDER_NAME
from app.alarms.sequence import WakeSequence
from app.alarms.tr_time import parse_when_struct
from app.notifications.models import NotificationRow
from app.voice.providers import FakeTTSProvider
from app.voice.text_fallback import (
    KIND_VOICE_TEXT_FALLBACK,
    MAX_BODY_CHARS,
    deliver_as_text,
    describe_reason,
)
from tests.alarms_support import (
    FakeDeviceAction,
    build_session_factory,
    failed,
    happy_device_results,
    ok,
)

NOW = datetime(2026, 9, 9, 4, 0, tzinfo=UTC)
FIRED_AT = datetime(2026, 9, 9, 4, 0, 30, tzinfo=UTC)


@pytest.fixture()
def session():
    with build_session_factory()() as s:
        yield s


@pytest.fixture()
def device():
    return FakeDeviceAction(results=happy_device_results())


class _Briefing:
    """The briefing service's one method, as the sequence uses it."""

    def __init__(self, speech: str) -> None:
        self.speech = speech
        self.builds = 0

    def build(self, _session, **_kwargs):
        self.builds += 1
        return {"speech": self.speech}


def _alarm(session):
    return alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 30}, now=NOW)
    )


def _fire(session, device, alarm, *, tts, briefing=None):
    sequence = WakeSequence(device_action=device, tts=tts, briefing=briefing)
    sequence.fire(
        session,
        alarm,
        firing_id=uuid.uuid4(),
        now=FIRED_AT,
        transition=alarms_service.transition,
    )
    device.reset()
    return sequence


def _fallbacks(session) -> list[NotificationRow]:
    return list(
        session.query(NotificationRow)
        .filter(NotificationRow.kind == KIND_VOICE_TEXT_FALLBACK)
        .order_by(NotificationRow.created_at)
        .all()
    )


def test_the_unspeakable_greeting_reaches_the_owner_as_text(session, device):
    alarm = _alarm(session)
    sequence = _fire(session, device, alarm, tts=FakeTTSProvider(name=FALLBACK_PROVIDER_NAME))

    sequence.speak_greeting(
        session, alarm, local_now=FIRED_AT, now=FIRED_AT, transition=alarms_service.transition
    )

    rows = _fallbacks(session)
    assert len(rows) == 1
    assert "Günaydın" in rows[0].body
    assert "selamlamayı" in rows[0].title
    assert "ses sağlayıcısı yapılandırılmamış" in rows[0].title
    assert rows[0].data_json["reason"] == "no_tts_key"
    # And the row still says what happened: the fallback must not cost the record.
    assert (alarm.detail_json or {}).get("greeting_failure") == "no_tts_key"
    # Nothing was played: this is instead of the buzz, not as well as it.
    assert device.count("desktop.play_audio") == 0


def test_the_briefing_that_could_not_be_spoken_arrives_too(session, device):
    briefing = _Briefing("Bugün üç toplantın var. Hava on sekiz derece. Gece iki iş bitti.")
    alarm = _alarm(session)
    sequence = _fire(
        session,
        device,
        alarm,
        tts=FakeTTSProvider(name=FALLBACK_PROVIDER_NAME),
        briefing=briefing,
    )

    sequence.speak_greeting(
        session, alarm, local_now=FIRED_AT, now=FIRED_AT, transition=alarms_service.transition
    )

    # The greeting could not be spoken, so the briefing was never attempted ALOUD - and
    # before this batch that meant it was never built at all and the owner heard and read
    # nothing about their morning.
    assert briefing.builds == 1
    bodies = [row.body for row in _fallbacks(session)]
    assert any("üç toplantın" in body for body in bodies)
    assert (alarm.detail_json or {}).get("briefing", {}).get("delivered_as_text") is True
    # Text INSTEAD of audio: no clip was sent to the device on this path.
    assert device.count("desktop.play_audio") == 0


def test_only_the_part_the_owner_missed_is_sent(session, device):
    """The briefing got one clip out and the device stopped playing.

    What arrives as text is the REST of it. The owner heard the first sentences; making
    them read those again would bury the part they actually missed.
    """
    briefing = _Briefing(" ".join(f"Cumle {i} burada ve biraz uzundur." for i in range(8)))
    alarm = _alarm(session)

    def _play(payload):
        audio_id = str(payload.get("audio_id") or "")
        # The greeting and the first briefing clip play; the device then stops answering.
        if audio_id.startswith("greeting-") or audio_id.startswith("briefing-1-"):
            return ok(played=True, duration_ms=2500)
        return failed("device_unavailable", "hoparlör yanıt vermiyor")

    device.results["desktop.play_audio"] = _play
    sequence = _fire(
        session, device, alarm, tts=FakeTTSProvider(synthetic_speech=False), briefing=briefing
    )

    sequence.speak_greeting(
        session, alarm, local_now=FIRED_AT, now=FIRED_AT, transition=alarms_service.transition
    )

    rows = _fallbacks(session)
    assert len(rows) == 1
    body = rows[0].body
    receipt = (alarm.detail_json or {}).get("briefing", {})
    assert receipt["spoken"] == 1
    assert receipt["unspoken"] == receipt["clips"] - 1
    # The last sentence is in the text; the first one, which they heard, is not.
    assert "Cumle 7 burada" in body
    assert "Cumle 0 burada" not in body
    assert "cihaz sesi çalmadı" in rows[0].title


def test_an_empty_text_is_not_a_notification(session):
    assert deliver_as_text(session, text="   ", reason="no_tts_key", what="selamlamayı") is None
    assert _fallbacks(session) == []


def test_a_long_briefing_is_trimmed_and_says_so(session):
    row = deliver_as_text(
        session,
        text="A" * (MAX_BODY_CHARS + 500),
        reason="clip_not_played:2",
        what="sabah brifingini",
    )
    assert row is not None
    assert len(row.body) == MAX_BODY_CHARS
    assert row.data_json["truncated"] is True
    assert row.data_json["chars"] == MAX_BODY_CHARS + 500


def test_an_unknown_reason_is_passed_through_rather_than_explained_wrongly():
    assert describe_reason("no_tts_key") == "ses sağlayıcısı yapılandırılmamış"
    assert describe_reason("clip_not_played:3") == "cihaz sesi çalmadı"
    assert describe_reason("wormhole_collapsed") == "wormhole_collapsed"
    assert describe_reason("") == "bilinmeyen sebep"


def test_a_failed_notification_never_breaks_the_wake_path(session, device):
    # The store is gone. The alarm still rings, the sequence still finishes, and the row
    # still records the failure - the workaround must not destroy the evidence.
    session.execute(__import__("sqlalchemy").text("DROP TABLE notifications"))
    alarm = _alarm(session)
    sequence = _fire(session, device, alarm, tts=FakeTTSProvider(name=FALLBACK_PROVIDER_NAME))

    sequence.speak_greeting(
        session, alarm, local_now=FIRED_AT, now=FIRED_AT, transition=alarms_service.transition
    )

    assert (alarm.detail_json or {}).get("greeting_failure") == "no_tts_key"


def test_no_production_path_declares_a_fake_to_be_real_speech():
    """`synthetic_speech=False` is a test affordance and must stay one.

    It exists so a keyless machine can still exercise the path where audio is produced and
    played. A production module that passed it would be handing the owner a 110 Hz sine
    wave with the policy's own blessing, which is worse than the defect the policy fixed.
    """
    root = Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "synthetic_speech" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "synthetic_speech":
                    continue
                value = keyword.value
                if not (isinstance(value, ast.Constant) and value.value is True):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == []
