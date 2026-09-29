"""ADR-0212 addendum: the device phrase is not part of what the owner asked ABOUT.

2026-09-29, from a real screenshot: the owner said "ofis bilgisayarında yapay zeka son
gelişmeleri araştır" and the office PC's Google search box held the literal query
"ofis bilgisayarında Yapay Zeka son gelişmeler". The phrase that NAMES the machine rode into
the research topic, so it was searched for, and it never became the run's target device
either. Three separate things had to be true and were not:

* the topic the router (and, in a paid session, the model) hands ``research.start`` has no
  device phrase in it;
* the device the owner named is the device the run goes to, ahead of the session's own;
* a named device that cannot serve refuses in Turkish rather than sending the run elsewhere.

The same phrase reached other subjects the same way (a media search, a document search, a
file pattern, a news source, an image prompt, a remembered fact); those are cleaned where
the turn record is written, and proved below at the router and through the real relay.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.artifacts.models import Task
from app.broker import service as broker_service
from app.config import Settings
from app.devices import selection
from app.devices.aliases import extract_aliases, strip_device_phrases
from app.devices.commands import register_broker_runtime
from app.research.models import ResearchRunRow
from app.voice.intents import research_topic_of, resolve_intent
from tests.unit.test_devices_session_affinity_wiring import VENDOR_KEY, Stack, _open_session

BROWSER = ["browser.chrome"]


@pytest.fixture()
def make_stack(tmp_path):
    """The real application object over real device rows, exactly as the ADR-0208 wiring
    suite builds it (its ``Stack``); only the wire to the machines is a recorder."""
    stacks: list[Stack] = []

    def build(**settings: Any) -> Stack:
        stack = Stack(
            Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY, **settings),
            tmp_path / f"subject-{len(stacks)}.db",
        )
        stacks.append(stack)
        return stack

    try:
        yield build
    finally:
        register_broker_runtime(None)
        for stack in stacks:
            stack.engine.dispose()


# ------------------------------------------------------------------ the phrase, removed


@pytest.mark.parametrize(
    "sentence,topic",
    [
        # The owner's sentence of 2026-09-29.
        ("Ofis bilgisayarında yapay zeka son gelişmeleri araştır.", "yapay zeka son gelişmeleri"),
        ("Ofis bilgisayarımda yapay zeka son gelişmeleri araştır", "yapay zeka son gelişmeleri"),
        ("Ofiste yapay zeka hakkında araştırma yap", "yapay zeka"),
        ("Evdeki bilgisayarda kuantum bilgisayarları araştır", "kuantum bilgisayarları"),
        ("İşteki bilgisayarda elektrikli araçları araştır", "elektrikli araçları"),
        ("Yapay zeka son gelişmeleri ofis bilgisayarımda araştır", "Yapay zeka son gelişmeleri"),
        ("Ev bilgisayarımda iklim değişikliğini araştır", "iklim değişikliğini"),
    ],
)
def test_the_device_phrase_is_not_in_the_research_topic(sentence: str, topic: str) -> None:
    assert research_topic_of(sentence) == topic


@pytest.mark.parametrize(
    "sentence,topic",
    [
        # Ordinary words that ARE the subject stay: a device alias is not a word to remove.
        ("ofis mobilyaları hakkında araştır", "ofis mobilyaları"),
        ("iş dünyası son gelişmeleri araştır", "iş dünyası son gelişmeleri"),
        ("ev fiyatları hakkında araştır", "ev fiyatları"),
        ("ev ekonomisi hakkında araştır", "ev ekonomisi"),
        ("ofis bilgisayarları hakkında araştır", "ofis bilgisayarları"),
        ("dizüstü bilgisayar modellerini araştır", "dizüstü bilgisayar modellerini"),
        ("laptop fiyatlarını araştır", "laptop fiyatlarını"),
        ("işte yeni teknolojileri araştır", "yeni teknolojileri"),  # "işte" is filler, as ever
    ],
)
def test_ordinary_words_of_the_subject_are_left_alone(sentence: str, topic: str) -> None:
    assert research_topic_of(sentence) == topic
    assert extract_aliases(sentence) == (), "and they name no device either"


def test_stripping_reports_what_it_removed_and_only_that() -> None:
    text, named = strip_device_phrases("Ofis bilgisayarında yapay zeka son gelişmeleri araştır.")
    assert (text, named) == ("yapay zeka son gelişmeleri araştır.", ("ofis",))
    assert strip_device_phrases("ev fiyatları hakkında araştır") == (
        "ev fiyatları hakkında araştır",
        (),
    )
    assert strip_device_phrases("") == ("", ())


# --------------------------------------------------------- the other subjects, at the router


def _fields(sentence: str) -> dict[str, Any]:
    """What the relay records for the sentence: the router's fields, cleaned of the device
    phrase exactly as ``record_client_events`` does (one function, ``app.voice.spoken_device``)."""
    from app.voice.spoken_device import resolve_without_device_phrase

    resolved, subject, named = resolve_without_device_phrase(sentence, lambda t: resolve_intent(t))
    return {
        "intent": resolved.intent.value,
        "resolved": resolved,
        "subject": subject,
        "named": named,
    }


@pytest.mark.parametrize(
    "sentence,intent,field,value",
    [
        ("Ofis bilgisayarımda Tarkan şarkısı çal", "media_play", "media_query", "tarkan"),
        ("Evdeki bilgisayarda Sezen Aksu aç", "media_play", "media_query", "sezen aksu"),
        (
            "Ofis bilgisayarımda belgelerde bütçe geçen yerleri bul",
            "document_find_text",
            "text_query",
            "belgelerde bütçe",
        ),
        # The file pattern was the device word itself ("bilgisayarımda") before.
        ("Ofis bilgisayarımda bütçe dosyasını bul", "file_search", "pattern", "bütçe"),
        (
            "Ofis bilgisayarımda hesap makinesini aç ve 5 yaz",
            "mission_start",
            "mission_request",
            "hesap makinesini aç ve 5 yaz",
        ),
        (
            "Ofis bilgisayarımda teknoloji haberlerini özetle",
            "news_summarize",
            "news_source_ref",
            None,
        ),
        (
            "Ofis bilgisayarımda bir görsel üret kedi",
            "creative_generate",
            "creative_prompt",
            "bir görsel üret kedi",
        ),
        (
            "Ofis bilgisayarımda bir uygulama yap",
            "app_factory_create",
            "app_request",
            "bir uygulama yap",
        ),
    ],
)
def test_the_other_subjects_do_not_carry_the_device_phrase(
    sentence: str, intent: str, field: str, value: str | None
) -> None:
    got = _fields(sentence)
    assert got["intent"] == intent
    carried = getattr(got["resolved"], field)
    assert "ofis" not in str(carried or "").lower()
    assert "bilgisayar" not in str(carried or "").lower()
    if value is not None:
        assert carried == value
    assert got["named"], "and the device is still named"


def test_a_sentence_the_stripped_reading_routes_differently_keeps_its_own_reading() -> None:
    """The cleaned reading is used only when it routes to the SAME intent: removing words can
    never change what the owner asked for."""
    from app.voice.spoken_device import resolve_without_device_phrase

    def fake(text: str):
        # The whole sentence is a media request; without the phrase it would be read as nothing.
        return resolve_intent("Tarkan şarkısı çal" if "ofis" in text.lower() else "merhaba")

    resolved, subject, named = resolve_without_device_phrase("Ofis bilgisayarımda çal", fake)
    assert resolved.intent.value == "media_play"
    assert named == ("ofis",)
    assert subject == "Ofis bilgisayarımda çal", (
        "the subject text is the original when the readings differ"
    )


# --------------------------------------------- through the real application object: research


def _sim_research(client, sid: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    fake_temporal = AsyncMock()
    fake_temporal.start_workflow = AsyncMock(return_value=None)
    with patch("app.research.service.Client.connect", AsyncMock(return_value=fake_temporal)):
        response = client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={
                "call_id": f"r-{uuid.uuid4().hex[:8]}",
                "name": "research.start",
                "arguments": arguments,
            },
        )
    assert response.status_code == 200, response.text
    return response.json(), fake_temporal


def _say(client, sid: str, sentence: str) -> None:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 1000, "turn": 1, "text": sentence}]},
    )
    assert response.status_code == 200, response.text


def _stored_intent(stack, task_id: str) -> str:
    with stack.factory() as db:
        return db.scalars(select(Task).where(Task.id == uuid.UUID(task_id))).one().intent


def _alias(stack, device_id: uuid.UUID, *aliases: str) -> None:
    with stack.factory() as db:
        broker_service.update_device_metadata(db, device_id, aliases=list(aliases), trace_id=None)


def _two_machines(make_stack):
    stack = make_stack()
    home = stack.enroll("MAIL", BROWSER, online=True)
    office = stack.enroll("GMKADIRAKBABA", BROWSER, online=True)
    _alias(stack, home, "ev")
    _alias(stack, office, "ofis", "iş")
    return stack, home, office


SENTENCE = "Ofis bilgisayarında yapay zeka son gelişmeleri araştır."


@pytest.mark.parametrize("session_at", ["home", "office"])
def test_a_spoken_research_names_the_office_and_carries_no_phrase_in_its_topic(
    make_stack, session_at: str
) -> None:
    """The router-only client (no model, no arguments): topic and device both come from the
    sentence. From the HOME session and from the OFFICE one, the run is the office's and the
    stored topic is the subject only."""
    stack, home, office = _two_machines(make_stack)
    client = stack.client()
    sid = _open_session(client, device_id=str({"home": home, "office": office}[session_at]))
    _say(client, sid, SENTENCE)

    body, temporal = _sim_research(client, sid, {})

    result = body["result"]
    assert body["status"] == "running", body
    assert result["device"]["device_id"] == str(office)
    assert result["topic"] == "yapay zeka son gelişmeleri"
    assert _stored_intent(stack, result["task_id"]) == "yapay zeka son gelişmeleri"
    request = temporal.start_workflow.call_args.args[1]
    assert request.topic == "yapay zeka son gelişmeleri"
    assert request.target_device == "ofis"
    with stack.factory() as db:
        run = db.scalars(select(ResearchRunRow)).one()
        assert run.device_id == office


def test_the_models_own_topic_with_the_phrase_in_it_is_cleaned_too(make_stack) -> None:
    """A paid session's model writes the topic itself, and it wrote the screenshot's query:
    "ofis bilgisayarında Yapay Zeka son gelişmeler"."""
    stack, home, office = _two_machines(make_stack)
    client = stack.client()
    sid = _open_session(client, device_id=str(home))
    _say(client, sid, SENTENCE)

    body, temporal = _sim_research(
        client, sid, {"topic": "ofis bilgisayarında Yapay Zeka son gelişmeler"}
    )

    result = body["result"]
    assert result["topic"] == "Yapay Zeka son gelişmeler"
    assert _stored_intent(stack, result["task_id"]) == "Yapay Zeka son gelişmeler"
    assert temporal.start_workflow.call_args.args[1].topic == "Yapay Zeka son gelişmeler"
    assert result["device"]["device_id"] == str(office)


def test_the_device_the_model_wrote_into_its_topic_is_the_one_when_the_turn_lags(
    make_stack,
) -> None:
    """A paid session's transcript event can arrive AFTER the tool call: the turn record then
    holds the previous sentence. The words in the call's own topic are the call's own."""
    stack, home, office = _two_machines(make_stack)
    client = stack.client()
    sid = _open_session(client, device_id=str(home))
    _say(client, sid, "Evdeki bilgisayarda yapay zeka haberlerini araştır")  # names the HOME PC

    body, _ = _sim_research(client, sid, {"topic": "ofis bilgisayarında kuantum bilgisayarlar"})

    assert body["result"]["device"]["device_id"] == str(office)
    assert body["result"]["topic"] == "kuantum bilgisayarlar"


def test_the_named_device_wins_over_the_session_device(make_stack) -> None:
    stack, home, office = _two_machines(make_stack)
    client = stack.client()
    sid = _open_session(client, device_id=str(home))
    _say(client, sid, SENTENCE)
    body, _ = _sim_research(client, sid, {})
    device = body["result"]["device"]
    assert device["device_id"] == str(office)
    assert device["reason"] == selection.REASON_EXPLICIT_ALIAS


def test_a_spoken_research_that_names_nothing_is_what_it_was(make_stack) -> None:
    stack, home, office = _two_machines(make_stack)
    client = stack.client()
    sid = _open_session(client, device_id=str(office))
    _say(client, sid, "Yapay zeka son gelişmeleri araştır.")
    body, temporal = _sim_research(client, sid, {})
    assert body["result"]["device"]["device_id"] == str(office)
    assert body["result"]["device"]["reason"] == selection.REASON_SESSION_AFFINITY
    assert temporal.start_workflow.call_args.args[1].target_device is None


def test_a_named_office_that_is_offline_is_a_refusal_and_not_the_home_pc(make_stack) -> None:
    stack = make_stack()
    home = stack.enroll("MAIL", BROWSER, online=True)
    office = stack.enroll("GMKADIRAKBABA", BROWSER, online=False)
    _alias(stack, home, "ev")
    _alias(stack, office, "ofis")
    client = stack.client()
    sid = _open_session(client, device_id=str(home))
    _say(client, sid, SENTENCE)

    body, temporal = _sim_research(client, sid, {})

    assert body["status"] == "failed", body
    speech = body["error"]["speech"]
    assert "ofis" in speech.lower() and "çevrimiçi değil" in speech
    temporal.start_workflow.assert_not_called()
    with stack.factory() as db:
        runs = db.scalars(select(ResearchRunRow)).all()
        assert all(r.device_id != home for r in runs)


def test_a_named_office_without_the_browser_capability_is_a_refusal(make_stack) -> None:
    stack = make_stack()
    home = stack.enroll("MAIL", BROWSER, online=True)
    office = stack.enroll("GMKADIRAKBABA", ["desktop.notify"], online=True)
    _alias(stack, home, "ev")
    _alias(stack, office, "ofis")
    client = stack.client()
    sid = _open_session(client, device_id=str(home))
    _say(client, sid, SENTENCE)
    body, _ = _sim_research(client, sid, {})
    assert body["status"] == "failed", body
    assert "ofis" in body["error"]["speech"].lower()


def test_two_devices_named_in_a_research_are_a_refusal(make_stack) -> None:
    stack, home, office = _two_machines(make_stack)
    client = stack.client()
    sid = _open_session(client, device_id=str(home))
    _say(client, sid, "Evdeki bilgisayarda ofis bilgisayarımdaki notları araştır")
    body, temporal = _sim_research(client, sid, {})
    assert body["status"] == "failed", body
    assert "ev" in body["error"]["speech"] and "ofis" in body["error"]["speech"]
    temporal.start_workflow.assert_not_called()


def test_the_target_is_handled_once_the_rest_route_and_the_plain_call_are_unchanged(
    make_stack,
) -> None:
    """The REST caller's own ``target_device`` still selects by itself, and a call with neither
    a target nor a session hint is the old rule: no named-device machinery in the way."""
    from app.research import service as research_service

    stack, home, office = _two_machines(make_stack)
    with stack.factory() as db:
        by_rest = research_service.start_browser_research(
            db, stack.broker, input="konu", target_device="ofis"
        )
        plain = research_service.start_browser_research(db, stack.broker, input="konu")
        both = research_service.start_browser_research(
            db, stack.broker, input="konu", target_device="ev", named_devices=("ofis",)
        )
    assert by_rest.device["device_id"] == str(office)
    assert plain.device == {"device_id": str(home), "name": "MAIL"}
    assert both.device["device_id"] == str(office), "the spoken word is the owner's latest"


# ------------------------------------------- the same leak, through the real relay


def test_the_relay_records_the_subject_without_the_phrase(make_stack) -> None:
    stack, home, office = _two_machines(make_stack)
    client = stack.client()
    sid = _open_session(client, device_id=str(home))
    _say(client, sid, "Ofis bilgisayarımda Tarkan şarkısı çal")
    from app.voice.realtime_sessions.models import RealtimeSessionRow

    with stack.factory() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        record = dict(row.context_json["last_utterance"])
    assert record["intent"] == "media_play"
    assert record["media_query"] == "tarkan"
    assert record["device_targets"] == ["ofis"]


def test_a_remembered_fact_and_a_memory_question_do_not_carry_the_phrase(make_stack) -> None:
    stack, home, office = _two_machines(make_stack)
    client = stack.client()
    sid = _open_session(client, device_id=str(home))
    from app.voice.realtime_sessions.models import RealtimeSessionRow

    _say(client, sid, "Ofis bilgisayarımda bunu hatırla toplantı cuma günü")
    with stack.factory() as db:
        record = dict(db.get(RealtimeSessionRow, uuid.UUID(sid)).context_json["last_utterance"])
    assert record["memory_statement"] == "toplantı cuma günü"
    _say(client, sid, "Ofis bilgisayarımda toplantı hakkında ne biliyorsun")
    with stack.factory() as db:
        record = dict(db.get(RealtimeSessionRow, uuid.UUID(sid)).context_json["last_utterance"])
    assert "ofis" not in str(record["memory_query"]).lower()
