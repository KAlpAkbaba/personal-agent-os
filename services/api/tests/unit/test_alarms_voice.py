"""Unit tests: the alarm/display/ambient intents and tools (M18.3 spec §3.8, §6).

Two halves:

* **Intents.** Every phrase from spec §6's list, pinned through ``resolve_intent`` — the ONE
  router. The class (query/action/control) and the capability are pinned too, because a
  wake alarm that resolved as a CONTROL would never reach a tool and never leave a receipt.
* **Tools.** Each handler's receipt and its ``speech``, including the two refusals that are
  the system declining to invent something: an unparseable time, and a recurring alarm whose
  music the owner only named.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.actions.receipt import (
    ACTION_CONTRACT_VERSION,
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    contains_fake_completion,
)
from app.alarms import service as alarms_service
from app.alarms import speech as alarm_speech
from app.alarms.models import STATE_CANCELLED, STATE_SNOOZED, STATE_STOPPED
from app.alarms.sequence import WakeSequence
from app.alarms.tr_time import parse_when_struct
from app.ambient import service as ambient_service
from app.ambient.holdoff import SOURCE_INPUT, HoldoffRegistry, set_holdoffs
from app.ledger.models import ActivityEventRow
from app.voice.intents import (
    CAPABILITY_BY_INTENT,
    KLASS_ACTION,
    KLASS_QUERY,
    Intent,
    klass_for,
    resolve_intent,
)
from app.voice.realtime_sessions import tools_ambient
from app.voice.realtime_sessions.tools import ToolContext, default_registry
from tests.alarms_support import (
    FakeDeviceAction,
    build_session_factory,
    happy_device_results,
    refused,
)

IST = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 9, 9, 6, 0, tzinfo=IST).astimezone(UTC)


# =========================================================================== intents


@pytest.mark.parametrize(
    ("utterance", "intent"),
    [
        # Spec §6's phrase list, verbatim.
        ("Yarın sabah 07:30'da beni uyandır.", Intent.ALARM_CREATE),
        ("Yarın 07:30'da YouTube'dan Hans Zimmer Time ile beni uyandır.", Intent.ALARM_CREATE),
        ("Her hafta içi 07:15'te beni bu şarkıyla uyandır.", Intent.ALARM_CREATE),
        ("Saat 08:00'e alarm kur.", Intent.ALARM_CREATE),
        ("Sabah alarmım kaçta?", Intent.ALARM_QUERY),
        ("Alarmı iptal et.", Intent.ALARM_CANCEL),
        ("Alarmı kapat.", Intent.ALARM_STOP),
        ("Alarmı durdur.", Intent.ALARM_STOP),
        ("Alarmı sustur.", Intent.ALARM_STOP),
        ("Beş dakika ertele.", Intent.ALARM_SNOOZE),
        ("On dakika ertele.", Intent.ALARM_SNOOZE),
        ("90 saniye sonra test alarmı kur.", Intent.ALARM_TEST_CREATE),
        ("90 saniye sonra YouTube'dan Time ile test alarmı kur.", Intent.ALARM_TEST_CREATE),
        ("Ekranları kapat.", Intent.DISPLAY_OFF),
        ("Ekranı kapat.", Intent.DISPLAY_OFF),
        ("Ekranları aç.", Intent.DISPLAY_WAKE),
        ("Ekranı aç.", Intent.DISPLAY_WAKE),
        ("Uyurken ekranları kapat.", Intent.AMBIENT_POLICY_SET),
        ("Ben yokken ekranları kapat.", Intent.AMBIENT_POLICY_SET),
        ("Otomatik ekran kapatmayı kapat.", Intent.AMBIENT_POLICY_SET),
        ("Otomatik ekran kapatmayı aç.", Intent.AMBIENT_POLICY_SET),
        ("Ben geri geldiğimde ekranı aç.", Intent.AMBIENT_POLICY_SET),
        ("Ekran uyku otomasyonunu test et.", Intent.AMBIENT_TEST_DISPLAY),
    ],
)
def test_every_owner_phrase_resolves_to_its_intent(utterance: str, intent: Intent) -> None:
    assert resolve_intent(utterance).intent is intent


def test_a_ringing_alarm_stop_beats_the_generic_stop_words() -> None:
    """"Alarmı durdur" / "sustur" / "kes" are built from words that are also STOP_TOKENS.

    A ringing alarm that answered "durdur" by stopping the NARRATION would leave the owner
    listening to the alarm — which is why the alarm check runs before the stop check.
    """
    for utterance in ("Alarmı durdur.", "Alarmı sustur.", "Alarmı kes."):
        assert resolve_intent(utterance).intent is Intent.ALARM_STOP


def test_a_bare_stop_word_is_still_a_stop() -> None:
    """...and the alarm check must not have swallowed the plain control command."""
    for utterance in ("Dur.", "Kes.", "Sus."):
        assert resolve_intent(utterance).intent is Intent.STOP


def test_the_eye_still_wins_over_everything(monkeypatch) -> None:
    """The privacy stop is checked before anything M18.3 added (``resolve_intent`` step 0)."""
    assert resolve_intent("Gözünü kapat.").intent is Intent.EYE_DISABLE
    assert resolve_intent("Kamerayı kapat.").intent is Intent.EYE_DISABLE


def test_a_policy_phrase_is_not_a_display_command() -> None:
    """One word apart, and the difference is whether the screens go dark in two seconds or
    in twenty minutes."""
    assert resolve_intent("Uyurken ekranları kapat.").intent is Intent.AMBIENT_POLICY_SET
    assert resolve_intent("Ekranları kapat.").intent is Intent.DISPLAY_OFF


def test_a_display_question_is_a_query_not_a_command() -> None:
    assert resolve_intent("Ekranlar açık mı?").intent is Intent.DISPLAY_QUERY


@pytest.mark.parametrize(
    "intent",
    [
        Intent.ALARM_CREATE,
        Intent.ALARM_TEST_CREATE,
        Intent.ALARM_CANCEL,
        Intent.ALARM_SNOOZE,
        Intent.ALARM_STOP,
        Intent.DISPLAY_OFF,
        Intent.DISPLAY_WAKE,
        Intent.AMBIENT_POLICY_SET,
        Intent.AMBIENT_TEST_DISPLAY,
    ],
)
def test_every_mutating_intent_is_an_action_with_a_capability(intent: Intent) -> None:
    """docs/M18_ACTION_CONTRACT.md §2: an ACTION targets a canonical capability and ends in
    a receipt. An intent classed as CONTROL would never reach a tool."""
    assert klass_for(intent) == KLASS_ACTION
    assert intent in CAPABILITY_BY_INTENT
    assert CAPABILITY_BY_INTENT[intent] in {spec for spec in default_registry().names()}


@pytest.mark.parametrize("intent", [Intent.ALARM_QUERY, Intent.DISPLAY_QUERY])
def test_the_two_questions_are_queries(intent: Intent) -> None:
    assert klass_for(intent) == KLASS_QUERY
    assert intent not in CAPABILITY_BY_INTENT


def test_a_test_alarm_targets_the_same_capability_as_a_real_one() -> None:
    """Spec §8.1: a test alarm is a real alarm with ``test=true``, not a second code path —
    so it must not be a second capability either."""
    assert (
        CAPABILITY_BY_INTENT[Intent.ALARM_TEST_CREATE]
        == CAPABILITY_BY_INTENT[Intent.ALARM_CREATE]
        == "alarm.create"
    )


def test_the_resolved_intent_carries_the_capability_for_the_client() -> None:
    resolved = resolve_intent("Ekranları kapat.")
    assert resolved.to_dict()["capability"] == "display.off"
    assert resolved.to_dict()["klass"] == KLASS_ACTION


# ============================================================================= tools


@pytest.fixture()
def session():
    with build_session_factory()() as s:
        yield s


@pytest.fixture()
def device():
    return FakeDeviceAction(results=happy_device_results())


@pytest.fixture()
def sequence(device):
    return WakeSequence(device_action=device, tts=None)


@pytest.fixture()
def holdoffs():
    registry = HoldoffRegistry()
    set_holdoffs(registry)
    yield registry
    set_holdoffs(HoldoffRegistry())


@pytest.fixture()
def ctx(session, sequence):
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={},
        db=session,
        now=NOW,
        call_id="call-1",
        live={"wake_sequence": sequence},
    )


def _receipts(session) -> list[ActivityEventRow]:
    return [r for r in session.query(ActivityEventRow).all() if r.event_type == "action.receipt"]


# ----------------------------------------------------------------------- the manifest


def test_the_registry_exposes_all_ten_tools() -> None:
    names = set(default_registry().names())
    assert set(tools_ambient.AMBIENT_TOOL_NAMES) <= names
    assert len(tools_ambient.AMBIENT_TOOL_NAMES) == 10


def test_the_action_contract_version_carries_the_alarm_family() -> None:
    """Spec §3.8: the M18.3 harness gates on this. v6 introduced the alarm / display /
    ambient family; later contracts (v7: ADR-0075's refused research.start) only add to it,
    so the floor is what matters here - the exact current value is pinned once, in
    test_health_endpoint.py."""
    assert ACTION_CONTRACT_VERSION >= 6


def test_every_new_tool_description_tells_the_model_to_read_speech_verbatim() -> None:
    registry = default_registry()
    for name in tools_ambient.AMBIENT_TOOL_NAMES:
        spec = registry.get(name)
        assert spec is not None
        assert "speech" in spec.description
        assert spec.long_running is False  # one round trip, then the answer
        assert spec.preamble is None


# --------------------------------------------------------------------- alarm.create


def test_alarm_create_from_the_owners_own_words(ctx, session) -> None:
    result = tools_ambient.alarm_create(ctx, {"when_text": "Yarın sabah 07:30'da beni uyandır."})
    assert result["execution_status"] == EXECUTION_EXECUTED
    assert result["terminal_status"] == TERMINAL_VERIFIED
    assert result["speech"] == "Alarmı yarın yedi otuza kurdum efendim."
    assert result["alarm"]["local_time"] == "07:30"
    assert [r.action for r in _receipts(session)] == ["alarm.create"]


def test_alarm_create_test_mode_says_so(ctx) -> None:
    result = tools_ambient.alarm_create(
        ctx, {"when_text": "90 saniye sonra test alarmı kur.", "test": True}
    )
    assert result["speech"] == "Test alarmını doksan saniye sonraya kurdum efendim."
    assert result["alarm"]["is_test"] is True
    assert result["alarm"]["max_play_seconds"] == 120


def test_alarm_create_refuses_an_unparseable_time_rather_than_guessing(ctx, session) -> None:
    result = tools_ambient.alarm_create(ctx, {"when_text": "Beni bir ara uyandır."})
    assert result["execution_status"] == EXECUTION_REFUSED
    assert result["terminal_status"] == TERMINAL_FAILED
    assert result["error_class"] == tools_ambient.ERROR_WHEN_UNPARSED
    assert result["speech"] == alarm_speech.ALARM_CREATE_UNPARSED_TR
    assert alarms_service.list_alarms(session) == []


def test_a_recurring_alarm_with_unnamed_media_is_refused_and_nothing_is_created(
    ctx, session
) -> None:
    """Spec §3.8: a repeating alarm that silently rings a tone every weekday instead of the
    song the owner named is a lie that repeats."""
    result = tools_ambient.alarm_create(
        ctx,
        {
            "when_text": "Her hafta içi 07:15'te beni bu şarkıyla uyandır.",
            "media": {"title": "bu şarkı"},
        },
    )
    assert result["execution_status"] == EXECUTION_REFUSED
    assert result["error_class"] == tools_ambient.ERROR_NEEDS_MEDIA_CONFIRMATION
    assert result["speech"] == alarm_speech.ALARM_CREATE_REFUSED_MEDIA_TR
    assert alarms_service.list_alarms(session) == []


def test_a_one_shot_alarm_with_unnamed_media_is_created_with_the_tone_and_asks(
    ctx, session
) -> None:
    """The owner still wakes up tomorrow; the speech asks for the link."""
    result = tools_ambient.alarm_create(
        ctx,
        {"when_text": "Yarın sabah 07:30'da beni uyandır.", "media": {"title": "Hans Zimmer"}},
    )
    assert result["execution_status"] == EXECUTION_EXECUTED
    assert result["speech"] == alarm_speech.ALARM_CREATE_NEEDS_MEDIA_TR
    assert alarms_service.list_alarms(session)[0].resolved_media_identity is None


def test_alarm_create_keeps_the_owners_url_byte_for_byte(ctx, session) -> None:
    url = "https://www.youtube.com/watch?v=ZZZ&t=30s"
    tools_ambient.alarm_create(
        ctx, {"when_text": "Yarın 07:30'da uyandır.", "media": {"url": url}}
    )
    assert alarms_service.list_alarms(session)[0].resolved_media_identity["url"] == url


# ------------------------------------------------- alarm.status / cancel / stop / snooze


def test_alarm_status_is_a_query_with_no_receipt(ctx, session) -> None:
    assert tools_ambient.alarm_status(ctx, {})["speech"] == "Kurulu alarm yok efendim."
    tools_ambient.alarm_create(ctx, {"when_text": "Yarın sabah 07:30'da beni uyandır."})
    result = tools_ambient.alarm_status(ctx, {})
    assert result["speech"] == "Sabah alarmınız yedi otuzda efendim."
    assert "execution_status" not in result
    assert [r.action for r in _receipts(session)] == ["alarm.create"]


def test_alarm_cancel_defaults_to_the_next_alarm(ctx, session) -> None:
    tools_ambient.alarm_create(ctx, {"when_text": "Yarın sabah 07:30'da beni uyandır."})
    result = tools_ambient.alarm_cancel(ctx, {})
    assert result["speech"] == alarm_speech.ALARM_CANCELLED_TR
    assert alarms_service.list_alarms(session, include_terminal=True)[0].state == STATE_CANCELLED


def test_alarm_cancel_with_nothing_scheduled_says_so_calmly(ctx) -> None:
    result = tools_ambient.alarm_cancel(ctx, {})
    assert result["speech"] == alarm_speech.ALARM_QUERY_NONE_TR
    assert result["error_class"] == tools_ambient.ERROR_NO_ALARM


def test_alarm_stop_targets_the_ringing_alarm(ctx, session, sequence) -> None:
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 5}, now=NOW)
    )
    alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NOW + timedelta(seconds=10),
    )
    result = tools_ambient.alarm_stop(ctx, {})
    assert result["speech"] == alarm_speech.ALARM_STOPPED_TR
    session.refresh(alarm)
    assert alarm.state == STATE_STOPPED


def test_alarm_stop_with_nothing_ringing_is_idempotent_and_truthful(ctx) -> None:
    result = tools_ambient.alarm_stop(ctx, {})
    assert result["speech"] == alarm_speech.ALARM_NOT_RINGING_TR
    assert result["error_class"] == tools_ambient.ERROR_NOT_RINGING


def test_alarm_snooze_defaults_to_five_minutes(ctx, session, sequence) -> None:
    alarm = alarms_service.create_alarm(
        session, when=parse_when_struct({"relative_seconds": 5}, now=NOW)
    )
    alarms_service.fire_alarm(
        session,
        alarm.id,
        sequence=sequence,
        firing_id=uuid.uuid4(),
        now=NOW + timedelta(seconds=10),
    )
    result = tools_ambient.alarm_snooze(ctx, {})
    session.refresh(alarm)
    assert alarm.snooze_count == 1
    assert alarm.snooze_minutes == 5
    assert "erteledim efendim" in result["speech"]
    assert alarm.state in (STATE_SNOOZED, "ARMED", "SCHEDULED")


def test_alarm_snooze_is_refused_when_nothing_is_ringing(ctx) -> None:
    result = tools_ambient.alarm_snooze(ctx, {"minutes": 10})
    assert result["execution_status"] == EXECUTION_REFUSED
    assert result["speech"] == alarm_speech.ALARM_NOT_RINGING_TR


# ------------------------------------------------------------------------ display


def test_display_off_returns_the_devices_receipt(ctx, session, device) -> None:
    result = tools_ambient.display_off(ctx, {})
    assert result["capability"] == "display.off"
    assert result["terminal_status"] == TERMINAL_VERIFIED
    assert result["speech"] == alarm_speech.DISPLAY_OFF_VERIFIED_TR
    assert device.count("desktop.display_off") == 1


def test_a_device_refusal_is_spoken_and_starts_the_input_holdoff(
    ctx, session, device, holdoffs
) -> None:
    """Spec §3.8, §3.9: the device just told us the owner is at the keyboard, and
    continuing to ask would be the system arguing with a person."""
    device.results["desktop.display_off"] = refused("recent_input", input_idle_s=2)
    result = tools_ambient.display_off(ctx, {})
    assert result["execution_status"] == EXECUTION_REFUSED
    assert result["error_class"] == "recent_input"
    assert result["speech"] == alarm_speech.DISPLAY_OFF_REFUSED_RECENT_INPUT_TR
    assert holdoffs.is_active(SOURCE_INPUT, now=NOW)


def test_display_off_without_a_device_runtime_is_a_truthful_failure(session) -> None:
    ctx = ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={},
        db=session,
        now=NOW,
        call_id="call-x",
        live={},
    )
    result = tools_ambient.display_off(ctx, {})
    assert result["error_class"] == tools_ambient.ERROR_NO_CAPABLE_DEVICE
    assert result["speech"] == alarm_speech.DISPLAY_OFF_NO_DEVICE_TR


def test_display_wake_returns_the_devices_receipt(ctx, device) -> None:
    result = tools_ambient.display_wake(ctx, {})
    assert result["speech"] == alarm_speech.DISPLAY_WAKE_VERIFIED_TR
    assert device.count("desktop.display_wake") == 1


def test_display_status_answers_from_the_observed_state(ctx) -> None:
    from app.devices.status import DeviceStatusRegistry

    assert tools_ambient.display_status(ctx, {})["display"] == "unknown"
    statuses = DeviceStatusRegistry()
    statuses.record(uuid.uuid4(), {"display": {"state": "off"}}, now=NOW)
    ctx.live["device_statuses"] = statuses
    result = tools_ambient.display_status(ctx, {})
    assert result["display"] == "off"
    assert result["speech"] == alarm_speech.DISPLAY_STATUS_OFF_TR


# ------------------------------------------------------------------------ ambient


def test_ambient_set_policy_turns_automatic_display_off_on(ctx, session) -> None:
    result = tools_ambient.ambient_set_policy(ctx, {"auto_off": True})
    assert result["speech"] == alarm_speech.AMBIENT_AUTO_OFF_ON_TR
    assert ambient_service.get_policy(session).auto_off_enabled is True


def test_ambient_set_policy_reports_nothing_changed_as_a_noop(ctx) -> None:
    tools_ambient.ambient_set_policy(ctx, {"off_when_asleep": True})  # already the default
    result = tools_ambient.ambient_set_policy(ctx, {"off_when_asleep": True})
    assert result["execution_status"] == "noop"
    assert result["terminal_status"] == "already"


def test_ambient_set_policy_needs_at_least_one_setting(ctx) -> None:
    from app.voice.errors import VoiceError

    with pytest.raises(VoiceError):
        tools_ambient.ambient_set_policy(ctx, {})


def test_ambient_test_display_arms_a_moment_and_darkens_nothing_yet(ctx, device) -> None:
    result = tools_ambient.ambient_test_display(ctx, {"delay_seconds": 10})
    assert result["speech"] == (
        "Ekran testini başlattım efendim; 10 saniye sonra ekranlar kapanacak, "
        "bir tuşa basınca açılacak."
    )
    assert device.count("desktop.display_off") == 0
    assert ambient_service.pending_display_test() == NOW + timedelta(seconds=10)
    ambient_service.cancel_display_test()


def test_the_test_delay_is_bounded(ctx) -> None:
    tools_ambient.ambient_test_display(ctx, {"delay_seconds": 9999})
    assert ambient_service.pending_display_test() == NOW + timedelta(
        seconds=ambient_service.MAX_TEST_DELAY_S
    )
    ambient_service.cancel_display_test()


# ------------------------------------------------------------------ the binding rule


def test_no_tool_ever_returns_a_banned_completion_phrase(ctx, session, device) -> None:
    """The persona reads ``speech`` verbatim, so a banned phrase reaching one of these
    would be the 2026-09-06 defect again with a wake alarm behind it."""
    device.results["desktop.display_off"] = refused("recent_input")
    said = [
        tools_ambient.alarm_create(ctx, {"when_text": "Yarın 07:30'da uyandır."})["speech"],
        tools_ambient.alarm_create(ctx, {"when_text": "belirsiz"})["speech"],
        tools_ambient.alarm_status(ctx, {})["speech"],
        tools_ambient.alarm_stop(ctx, {})["speech"],
        tools_ambient.alarm_snooze(ctx, {})["speech"],
        tools_ambient.alarm_cancel(ctx, {})["speech"],
        tools_ambient.display_off(ctx, {})["speech"],
        tools_ambient.display_wake(ctx, {})["speech"],
        tools_ambient.display_status(ctx, {})["speech"],
        tools_ambient.ambient_set_policy(ctx, {"auto_off": True})["speech"],
        tools_ambient.ambient_test_display(ctx, {})["speech"],
    ]
    for sentence in said:
        assert sentence
        assert not contains_fake_completion(sentence), sentence
    ambient_service.cancel_display_test()


def test_the_persona_names_every_new_tool() -> None:
    """docs/M18_ACTION_CONTRACT.md §6: a command the model can answer conversationally is a
    command that will be answered conversationally."""
    from app.voice.realtime_sessions.persona import ALARM_DISPLAY_GROUNDING_TR

    for name in tools_ambient.AMBIENT_TOOL_NAMES:
        assert name in ALARM_DISPLAY_GROUNDING_TR, name


def test_the_persona_forbids_claiming_the_machine_was_put_to_sleep() -> None:
    from app.voice.realtime_sessions.persona import ALARM_DISPLAY_GROUNDING_TR, build_instructions

    assert "uyutmaz" in ALARM_DISPLAY_GROUNDING_TR
    assert ALARM_DISPLAY_GROUNDING_TR in build_instructions()


def test_the_persona_tells_the_model_not_to_compute_the_time_itself() -> None:
    """The owner is asleep when most of this runs, so a wrong time is not recoverable by
    asking again."""
    from app.voice.realtime_sessions.persona import ALARM_DISPLAY_GROUNDING_TR

    assert "when_text" in ALARM_DISPLAY_GROUNDING_TR
    assert "SEN hesaplamazsın" in ALARM_DISPLAY_GROUNDING_TR


def test_a_tool_without_a_database_fails_loudly(sequence) -> None:
    from app.voice.errors import VoiceError

    ctx = ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={},
        db=None,
        now=NOW,
        live={"wake_sequence": sequence},
    )
    with pytest.raises(VoiceError):
        tools_ambient.alarm_create(ctx, {"when_text": "Yarın 07:30'da uyandır."})
