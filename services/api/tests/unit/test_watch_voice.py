"""watch-voice: the owner's watch, by voice (create / list / remove / forget all).

The words are resolved by the ONE router (``app.voice.intents``); the tools run through the
REAL relay (``create_app``) against the real watch service on SQLite - nothing reads a page
here (the runner has its own tests). The owner's words win over the model's arguments: the
condition and the interval the sentence carried are what the watch gets; the url is the
model's (``url`` argument) or, in the free local mode, one question "Hangi sayfayı
izleyeyim?" - a missing slot, never a confirmation (owner rule 2026-09-18/19).
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, ArtifactVersion
from app.artifacts.runtime import ArtifactRuntime
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime
from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.macros.models import VoiceMacroRow
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.operator.models import ObjectFocusRow
from app.voice.intents import Intent, resolve_intent
from app.voice.models import VoiceProfile
from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.realtime_sessions.service import is_forbidden_key
from app.voice.realtime_sessions.sideband import RecordingSideband
from app.voice.simulator import SimulatedRealtimeProvider
from app.watch import service as watch_service
from app.watch.models import Watch, WatchReading
from tests.identity_support import IDENTITY_TABLES

HA_SENTENCE = "Home Assistant'ın yeni kararlı sürümü çıkınca bana söyle."
PRICE_SENTENCE = "Şu ürünün fiyatı 20 bin liranın altına inerse haber ver."
HA_URL = "https://www.home-assistant.io/blog/categories/release-notes/"
SHOP_URL = "https://shop.example.com/urun/42"
ALLOWED_ARGUMENT_KEYS = {"url", "condition", "label", "every_hours", "selector", "watch_id"}
WATCH_TOOLS = ("watch.create", "watch.list", "watch.remove", "watch.forget_all")


def _watch_intents() -> set[Intent]:
    return {
        Intent.WATCH_CREATE,
        Intent.WATCH_LIST,
        Intent.WATCH_REMOVE,
        Intent.WATCH_FORGET_ALL,
    }


# ------------------------------------------------------------------- the words


@pytest.mark.parametrize(
    ("text", "intent_name"),
    [
        (HA_SENTENCE, "WATCH_CREATE"),
        (PRICE_SENTENCE, "WATCH_CREATE"),
        ("Bu sayfa değişince bana söyle.", "WATCH_CREATE"),
        ("Nöbetlerimi say.", "WATCH_LIST"),
        ("Hangi nöbetlerim var?", "WATCH_LIST"),
        ("Fiyat nöbetini kaldır.", "WATCH_REMOVE"),
        ("Nöbeti kaldır.", "WATCH_REMOVE"),
        ("Nöbetleri unut.", "WATCH_FORGET_ALL"),
        ("Bütün nöbetleri sil.", "WATCH_FORGET_ALL"),
    ],
)
def test_the_watch_words_reach_their_intents(text: str, intent_name: str) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is getattr(Intent, intent_name), resolved


@pytest.mark.parametrize(
    "spoken",
    [
        "20 bin liranın altına inerse haber ver.",
        "20.000 liranın altına inerse haber ver.",
        "yirmi bin liranın altına inerse haber ver.",
    ],
)
def test_the_price_words_become_number_below_twenty_thousand(spoken: str) -> None:
    resolved = resolve_intent(f"Şu ürünün fiyatı {spoken}")
    assert resolved.intent is Intent.WATCH_CREATE
    assert resolved.watch_condition == "number_below:20000"


def test_a_number_going_above_is_number_above() -> None:
    resolved = resolve_intent("Dolar 45 liranın üstüne çıkarsa haber ver.")
    assert resolved.intent is Intent.WATCH_CREATE
    assert resolved.watch_condition == "number_above:45"


def test_a_release_or_a_change_is_the_changed_condition() -> None:
    assert resolve_intent(HA_SENTENCE).watch_condition == "changed"
    assert resolve_intent("Bu sayfa değişince bana söyle.").watch_condition == "changed"


def test_the_interval_the_owner_said_travels_and_none_said_is_none() -> None:
    assert resolve_intent("Bu sayfa değişince bana söyle, günde bir bak.").watch_every_hours == 24
    assert resolve_intent("Bu sayfa değişince bana söyle, saatte bir bak.").watch_every_hours == 1
    assert resolve_intent("Bu sayfa değişince bana söyle.").watch_every_hours is None


def test_the_label_the_owner_named_is_the_one_removed() -> None:
    assert resolve_intent("Fiyat nöbetini kaldır.").watch_label == "fiyat"
    assert resolve_intent("Nöbeti kaldır.").watch_label is None


def test_a_url_in_the_sentence_is_carried() -> None:
    resolved = resolve_intent("example.com/urun sayfası değişince bana söyle.")
    assert resolved.intent is Intent.WATCH_CREATE
    assert resolved.watch_url == "https://example.com/urun"


@pytest.mark.parametrize(
    ("text", "keeps"),
    [
        # A pharmacy on duty is not a watch ("nöbetçi").
        ("nöbetçi eczane nerede", None),
        ("bu videoyu izle", None),
        ("beni izle", Intent.EYE_ENABLE),
        # The Turkish-suffix lesson: "unutma" is REMEMBER, never a forget-all.
        ("şunu unutma", Intent.MEMORY_REMEMBER),
        ("Nöbetleri unutma.", None),
        ("saat yedide haber ver", None),
        ("Toplantı bitince bana söyle.", None),
    ],
)
def test_the_near_misses_keep_their_intents(text: str, keeps: Intent | None) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent not in _watch_intents(), resolved
    if keeps is not None:
        assert resolved.intent is keeps, resolved


def test_unutma_never_reaches_forget_all() -> None:
    for text in ("Nöbetleri unutma.", "Nöbetlerimi unutma.", "Bunu unutma."):
        assert resolve_intent(text).intent is not Intent.WATCH_FORGET_ALL, text


def test_creating_removing_and_forgetting_act_and_listing_is_a_query() -> None:
    created = resolve_intent(HA_SENTENCE)
    assert (created.klass, created.capability) == ("action", "watch.create")
    removed = resolve_intent("Fiyat nöbetini kaldır.")
    assert (removed.klass, removed.capability) == ("action", "watch.remove")
    forgot = resolve_intent("Nöbetleri unut.")
    assert (forgot.klass, forgot.capability) == ("action", "watch.forget_all")
    listed = resolve_intent("Nöbetlerimi say.")
    assert listed.klass == "query"


# ------------------------------------------------------------------- the relay

TABLES = (
    RealtimeSessionRow.__table__,
    RealtimeToolCall.__table__,
    AuditEvent.__table__,
    VoiceProfile.__table__,
    NarrationSession.__table__,
    PronunciationEntry.__table__,
    Artifact.__table__,
    ArtifactVersion.__table__,
    ActivityEventRow.__table__,
    PendingBriefingRow.__table__,
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    ObjectFocusRow.__table__,
    VoiceMacroRow.__table__,
    Watch.__table__,
    WatchReading.__table__,
)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.research.destination.resolve_hostname", lambda host: ["93.184.216.34"])


@pytest.fixture()
def wired():
    settings = Settings(_env_file=None, voice_openai_api_key="unit-test-vendor-key-sentinel")
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (*IDENTITY_TABLES, *TABLES):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings)
    identity = IdentityRuntime(settings, engine=engine, root=InMemoryCredentialRoot())
    identity.service.bootstrap()
    app.state.identity = identity
    broker = BrokerRuntime(settings)
    broker._engine = engine
    broker._session_factory = factory
    app.state.broker = broker
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = factory
    app.state.artifacts = artifacts
    sim = SimulatedRealtimeProvider()
    app.state.voice_realtime = RealtimeVoiceRuntime(
        settings,
        engine=engine,
        providers={sim.name: sim},
        sideband=RecordingSideband(deliver=True),
        broker=broker,
        artifacts=artifacts,
    )
    issued = identity.service.issue_session(
        client_kind="desktop", label="pc", device_id=uuid.uuid4()
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"
    yield client, factory
    engine.dispose()


def _session(client) -> str:
    response = client.post("/v1/voice/realtime/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _say(client, sid: str, text: str) -> dict:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 1000, "turn": 1, "text": text}]},
    )
    assert response.status_code == 200, response.text
    return response.json()["resolved_intents"][-1]


def _tool(client, sid: str, name: str, arguments: dict) -> dict:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": f"c-{uuid.uuid4()}", "name": name, "arguments": arguments},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _watches(factory) -> list[watch_service.WatchView]:
    with factory() as db:
        return watch_service.list_watches(db)


def _seed(factory, label: str, url: str = SHOP_URL, condition: str = "changed") -> str:
    with factory() as db:
        view = watch_service.create_watch(db, url=url, condition=condition, label=label)
        db.commit()
        return view.id


def _one_sentence(speech: str) -> bool:
    return len([part for part in re.split(r"[.!?](?:\s|$)", speech.strip()) if part]) == 1


def test_create_reads_back_the_domain_the_condition_and_the_interval_in_one_sentence(
    wired,
) -> None:
    client, factory = wired
    sid = _session(client)
    turn = _say(client, sid, HA_SENTENCE)
    assert (turn["intent"], turn["tool"]) == ("watch_create", "watch.create")
    answer = _tool(client, sid, "watch.create", {"url": HA_URL})
    assert answer["status"] == "succeeded", answer
    speech = answer["result"]["speech"]
    assert "home-assistant.io" in speech
    assert "değiş" in speech  # the condition, said
    assert "6 saatte bir" in speech  # the interval, said
    assert _one_sentence(speech), speech
    # No second confirmation: the watch exists after the one call ("Nöbeti kaldır" undoes it).
    watches = _watches(factory)
    assert [(w.url, w.condition, w.every_hours) for w in watches] == [(HA_URL, "changed", 6)]


def test_the_owners_number_wins_over_the_models_condition(wired) -> None:
    client, factory = wired
    sid = _session(client)
    turn = _say(client, sid, PRICE_SENTENCE)
    assert turn["tool"] == "watch.create"
    answer = _tool(
        client, sid, "watch.create", {"url": SHOP_URL, "condition": "number_below:30000"}
    )
    assert answer["status"] == "succeeded", answer
    assert [w.condition for w in _watches(factory)] == ["number_below:20000"]
    speech = answer["result"]["speech"]
    assert "shop.example.com" in speech and "20.000" in speech and "altına" in speech
    assert _one_sentence(speech), speech


def test_the_turn_record_carries_the_watch_fields(wired) -> None:
    client, factory = wired
    sid = _session(client)
    _say(client, sid, "Şu ürünün fiyatı 20 bin liranın altına inerse haber ver, günde bir bak.")
    with factory() as db:
        row = db.execute(
            select(RealtimeSessionRow).where(RealtimeSessionRow.id == uuid.UUID(sid))
        ).scalar_one()
        turn = row.context_json["last_utterance"]
    assert turn["intent"] == "watch_create"
    assert turn["watch_condition"] == "number_below:20000"
    assert turn["watch_every_hours"] == 24
    assert "watch_url" in turn and "watch_label" in turn


def test_with_no_url_anywhere_one_question_is_asked_and_nothing_is_made(wired) -> None:
    client, factory = wired
    sid = _session(client)
    _say(client, sid, HA_SENTENCE)
    answer = _tool(client, sid, "watch.create", {})
    assert answer["status"] == "needs_clarification", answer
    assert answer["result"]["speech"] == "Hangi sayfayı izleyeyim?"
    assert _watches(factory) == []


def test_a_url_said_in_the_sentence_needs_no_model(wired) -> None:
    client, factory = wired
    sid = _session(client)
    _say(client, sid, "example.com/urun sayfası değişince bana söyle.")
    answer = _tool(client, sid, "watch.create", {})
    assert answer["status"] == "succeeded", answer
    assert [w.url for w in _watches(factory)] == ["https://example.com/urun"]


def test_a_refused_url_is_said_in_the_services_own_words_and_nothing_is_made(wired) -> None:
    client, factory = wired
    sid = _session(client)
    _say(client, sid, "Bu sayfa değişince bana söyle.")
    answer = _tool(client, sid, "watch.create", {"url": "http://localhost:8000/admin"})
    assert "herkese açık" in answer["result"]["speech"]
    assert _watches(factory) == []


def test_list_says_how_many_and_their_names(wired) -> None:
    client, factory = wired
    _seed(factory, "Ürün fiyatı")
    _seed(factory, "Home Assistant sürümü", url=HA_URL)
    sid = _session(client)
    turn = _say(client, sid, "Nöbetlerimi say.")
    assert turn["tool"] == "watch.list"
    answer = _tool(client, sid, "watch.list", {})
    speech = answer["result"]["speech"]
    assert "2" in speech or "iki" in speech.lower()
    assert "Ürün fiyatı" in speech and "Home Assistant sürümü" in speech


def test_list_with_none_says_so(wired) -> None:
    client, _ = wired
    sid = _session(client)
    _say(client, sid, "Nöbetlerimi say.")
    speech = _tool(client, sid, "watch.list", {})["result"]["speech"]
    assert "nöbet" in speech.lower() and "yok" in speech.lower()


def test_remove_by_the_label_the_owner_said(wired) -> None:
    client, factory = wired
    _seed(factory, "Ürün fiyatı")
    kept = _seed(factory, "Home Assistant sürümü", url=HA_URL)
    sid = _session(client)
    turn = _say(client, sid, "Fiyat nöbetini kaldır.")
    assert turn["tool"] == "watch.remove"
    answer = _tool(client, sid, "watch.remove", {})
    assert answer["status"] == "succeeded", answer
    assert "Ürün fiyatı" in answer["result"]["speech"]
    assert [w.id for w in _watches(factory)] == [kept]


def test_nobeti_kaldir_is_the_undo_of_the_last_created(wired) -> None:
    client, factory = wired
    kept = _seed(factory, "Eski nöbet")
    sid = _session(client)
    _say(client, sid, HA_SENTENCE)
    assert _tool(client, sid, "watch.create", {"url": HA_URL})["status"] == "succeeded"
    _say(client, sid, "Nöbeti kaldır.")
    answer = _tool(client, sid, "watch.remove", {})
    assert answer["status"] == "succeeded", answer
    assert [w.id for w in _watches(factory)] == [kept]


def test_a_label_that_names_no_watch_removes_nothing(wired) -> None:
    client, factory = wired
    _seed(factory, "Home Assistant sürümü", url=HA_URL)
    sid = _session(client)
    _say(client, sid, "Fiyat nöbetini kaldır.")
    answer = _tool(client, sid, "watch.remove", {})
    assert "bulamadım" in answer["result"]["speech"]
    assert len(_watches(factory)) == 1


def test_forget_all_deletes_at_once_and_says_how_many(wired) -> None:
    client, factory = wired
    for label in ("Bir", "İki", "Üç"):
        _seed(factory, label)
    sid = _session(client)
    turn = _say(client, sid, "Nöbetleri unut.")
    assert turn["tool"] == "watch.forget_all"
    answer = _tool(client, sid, "watch.forget_all", {})
    assert answer["status"] == "succeeded", answer
    speech = answer["result"]["speech"]
    assert "3" in speech or "üç" in speech.lower()
    assert _watches(factory) == []


# ------------------------------------------------------------------- the manifest


def test_the_watch_tools_take_only_the_allowed_argument_keys() -> None:
    from app.voice.realtime_sessions.tools import default_registry

    registry = default_registry()
    for name in WATCH_TOOLS:
        spec = registry.get(name)
        assert spec is not None, name
        keys = set(spec.parameters.get("properties", {}))
        assert keys <= ALLOWED_ARGUMENT_KEYS, (name, keys)
        assert not any(is_forbidden_key(key) for key in keys), (name, keys)


# ------------------------------------------------------------------- the corpus


def test_the_corpus_carries_the_watch_sentences() -> None:
    from tests.voice_corpus.corpus import all_cases

    by_utterance = {case.utterance: case for case in all_cases()}
    for utterance, intent in (
        (HA_SENTENCE, "watch_create"),
        (PRICE_SENTENCE, "watch_create"),
        ("Nöbetlerimi say.", "watch_list"),
        ("Fiyat nöbetini kaldır.", "watch_remove"),
        ("Nöbetleri unut.", "watch_forget_all"),
    ):
        assert utterance in by_utterance, utterance
        assert by_utterance[utterance].expected_intent == intent
