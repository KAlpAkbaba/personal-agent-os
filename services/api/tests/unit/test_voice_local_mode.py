"""ADR-0173: the free local voice mode, server half.

The browser transcribes and speaks; Cloud Core sees text over the SAME relay. What is
pinned here, through the real application object (create_app + the realtime routes on
SQLite, the way ``test_voice_realtime_sessions.py`` does it):

* ``transport="text"`` on create selects the ``local-router`` provider, the response
  carries NO provider secret and no transport descriptor, and the audit rows carry none;
* ``say -> tool``: an utterance posted as text resolves through the deterministic router,
  and the tool call the client then issues with EMPTY arguments (the tools prefer the
  owner's words recorded on the turn) runs the real handler and answers with speech;
* a create WITHOUT the explicit ask still picks the first configured conversation
  provider - the local router is rejected by capability, and the paid path stays the
  default (ADR-0173: "the paid path is untouched and remains the default");
* the web half sends the very transport constant the server defines (contract halves
  read each other: both suites were green once while the two sides drifted).
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from app.config import Settings
from app.presence.engine import PresenceFusionEngine, get_engine, set_engine
from app.presence.eye import is_eye_enabled
from app.presence.service import reset_heartbeat
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import TRANSPORT_TEXT, TRANSPORTS, RealtimeProvider
from app.voice.providers_local_router import (
    LOCAL_ROUTER_PROVIDER_NAME,
    LocalRouterRealtimeProvider,
)
from app.voice.providers_openai_realtime import OPENAI_REALTIME_PROVIDER_NAME
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime, default_providers
from app.voice.simulator import SIMULATOR_PROVIDER_NAME, SimulatedRealtimeProvider
from tests.unit.test_voice_realtime_sessions import _audit_rows, _create, wired  # noqa: F401

KEY = "unit-test-openai-standing-key-sentinel-never-leaves-cloud-core"

WEB_LOCAL_MODE = (
    Path(__file__).resolve().parents[4] / "apps" / "web" / "app" / "lib" / "voice" / "localMode.ts"
)


@pytest.fixture(autouse=True)
def _fresh_presence():
    """The eye tool reads the process-wide presence engine; give every test its own."""
    previous = get_engine()
    set_engine(PresenceFusionEngine())
    try:
        yield
    finally:
        set_engine(previous)
        reset_heartbeat()


# ------------------------------------------------------------ the provider


def test_the_local_router_is_a_realtime_provider_that_mints_no_secret() -> None:
    provider = LocalRouterRealtimeProvider()
    assert isinstance(provider, RealtimeProvider)
    caps = provider.capabilities()
    assert caps.transports == (TRANSPORT_TEXT,) and TRANSPORT_TEXT in TRANSPORTS
    assert caps.requires_api_key is False and caps.cost_metadata["usd_per_min"] == 0.0
    # The honest declaration that keeps it out of the default selection.
    assert not caps.is_conversation_capable("tr-TR")
    assert caps.tool_calling is True and caps.supports_language("tr-TR")
    credential = provider.mint_credential(session_id="abc", ttl_s=60)
    assert credential.secret == ""
    wire = credential.to_client_dict()
    assert "secret" not in wire and "transport_descriptor" not in wire
    assert wire["provider"] == LOCAL_ROUTER_PROVIDER_NAME and wire["transport"] == TRANSPORT_TEXT
    assert provider.leg_max_seconds() == 0
    with pytest.raises(VoiceError) as exc:
        provider.mint_credential(session_id="abc", ttl_s=60, transport="webrtc")
    assert exc.value.error_class is VoiceErrorClass.VALIDATION_ERROR
    with pytest.raises(VoiceError) as exc:
        provider.open_session()
    assert exc.value.error_class is VoiceErrorClass.CAPABILITY_MISSING


# ------------------------------------------------------------- selection


def test_default_selection_never_picks_the_local_router() -> None:
    """Dev without a key: the simulator. Prod with a key: the real adapter. In both the
    local router is registered and rejected, by capability, with the reasons."""
    dev = Settings(_env_file=None, environment="dev")
    rt = RealtimeVoiceRuntime(dev, providers=default_providers(dev))
    chosen, result = rt.select()
    assert chosen.name == SIMULATOR_PROVIDER_NAME
    assert LOCAL_ROUTER_PROVIDER_NAME not in result.ranked
    assert set(result.rejected[LOCAL_ROUTER_PROVIDER_NAME]) >= {
        "speech_to_speech",
        "full_duplex",
        "barge_in",
        "ephemeral_credentials",
    }

    prod = Settings(_env_file=None, environment="prod", voice_openai_api_key=KEY)
    rt = RealtimeVoiceRuntime(prod, providers=default_providers(prod))
    chosen, result = rt.select()
    assert chosen.name == OPENAI_REALTIME_PROVIDER_NAME
    assert result.ranked == (OPENAI_REALTIME_PROVIDER_NAME,)
    assert LOCAL_ROUTER_PROVIDER_NAME in result.rejected
    assert KEY not in repr(rt.health_check())

    # And a preference list naming it first changes nothing: capability gates, names rank.
    preferred = Settings(
        _env_file=None,
        environment="dev",
        voice_realtime_provider_preference=(LOCAL_ROUTER_PROVIDER_NAME,),
    )
    chosen, _ = RealtimeVoiceRuntime(preferred, providers=default_providers(preferred)).select()
    assert chosen.name == SIMULATOR_PROVIDER_NAME


def test_transport_text_is_the_one_door_to_the_local_router() -> None:
    dev = Settings(_env_file=None, environment="dev")
    rt = RealtimeVoiceRuntime(dev, providers=default_providers(dev))
    chosen, result = rt.select(transport=TRANSPORT_TEXT)
    assert chosen.name == LOCAL_ROUTER_PROVIDER_NAME
    assert result.transport == TRANSPORT_TEXT and result.ranked == (LOCAL_ROUTER_PROVIDER_NAME,)
    assert result.rejected[SIMULATOR_PROVIDER_NAME] == (f"transport={TRANSPORT_TEXT}",)
    # Any other explicit transport runs the default selection unchanged.
    chosen, _ = rt.select(transport="webrtc")
    assert chosen.name == SIMULATOR_PROVIDER_NAME

    # Prod with NO vendor key - the deployment the owner reaches for this mode on - still
    # has it, while the default selection has nothing (as before).
    prod = Settings(_env_file=None, environment="prod")
    rt = RealtimeVoiceRuntime(prod, providers=default_providers(prod))
    chosen, _ = rt.select(transport=TRANSPORT_TEXT)
    assert chosen.name == LOCAL_ROUTER_PROVIDER_NAME
    with pytest.raises(VoiceError):
        rt.select()

    # Without any text-capable provider the ask is refused with the reasons, not served
    # by whatever is there.
    sim = SimulatedRealtimeProvider()
    rt = RealtimeVoiceRuntime(dev, providers={sim.name: sim})
    with pytest.raises(VoiceError) as exc:
        rt.select(transport=TRANSPORT_TEXT)
    assert exc.value.error_class is VoiceErrorClass.CAPABILITY_MISSING
    assert exc.value.details["rejected"][SIMULATOR_PROVIDER_NAME] == [f"transport={TRANSPORT_TEXT}"]


# ------------------------------------------------- through the real relay


def _register_local(runtime: RealtimeVoiceRuntime) -> None:
    local = LocalRouterRealtimeProvider()
    runtime.providers[local.name] = local


def _say(client, sid: str, text: str, *, turn: int) -> dict:
    """The web client's utterance event, exactly as `localMode.ts` posts it."""
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 1000 * turn, "turn": turn, "text": text}]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_local_session_runs_say_then_tool_through_the_real_relay(wired) -> None:
    client, _identity, runtime, _sideband, _issued, _engine = wired
    _register_local(runtime)

    created = _create(client, transport=TRANSPORT_TEXT)
    sid = created["session_id"]
    assert created["provider"] == LOCAL_ROUTER_PROVIDER_NAME
    assert created["transport"] == TRANSPORT_TEXT
    assert created["leg_max_seconds"] == 0
    credential = created["credential"]
    assert credential["provider"] == LOCAL_ROUTER_PROVIDER_NAME
    assert "secret" not in credential and "transport_descriptor" not in credential
    assert credential["transport"] == TRANSPORT_TEXT
    # The same tool manifest and persona the paid session gets: one relay, one contract.
    assert {t["name"] for t in created["tools"]} >= {"assistant.capabilities", "state.now"}
    assert created["instructions"]
    for row in _audit_rows(runtime, sid):
        assert "secret" not in row.metadata_json

    # say: text in, the deterministic router's resolution out - no model anywhere. The
    # family the owner NAMED is recorded on the turn, not handed to the tool by anyone.
    said = _say(client, sid, "Alarmlarla neler yapabilirsin?", turn=1)
    resolved = said["resolved_intents"]
    # A QUERY carries no `capability` (that word means "action"); `tool` is the one the
    # router names for either class, and the web half issues exactly that call.
    assert len(resolved) == 1 and resolved[0]["capability"] is None
    assert resolved[0]["tool"] == "assistant.capabilities"

    # tool: the SAME call the model would have issued, with EMPTY arguments - the tool
    # reads the owner's words from the turn record, never from the caller.
    call_id = f"local-{uuid.uuid4()}"
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id, "name": "assistant.capabilities", "arguments": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded", body
    assert body["result"]["family"] == "alarm" and body["result"]["known"] is True
    assert body["result"]["speech"].startswith("Efendim, ")

    # The session record says what served it, and no row anywhere carries a secret.
    state = client.get(f"/v1/voice/realtime/sessions/{sid}").json()
    assert state["provider"] == LOCAL_ROUTER_PROVIDER_NAME and state["transport"] == TRANSPORT_TEXT
    minted = _audit_rows(runtime, sid, service.ACTION_CREDENTIAL_MINTED)
    assert len(minted) == 1 and minted[0].metadata_json["session_ref"] == f"local:{sid}"

    # An ACTION carries both, and they agree: the web half never has to know the class.
    said = _say(client, sid, "Gözünü kapat.", turn=2)
    assert said["resolved_intents"][0]["capability"] == "eye.disable"
    assert said["resolved_intents"][0]["tool"] == "eye.disable"


def test_what_only_a_model_could_route_is_named_as_such_in_local_mode(wired) -> None:
    """ADR-0173 addendum. An EXPLAIN question ("Göz açık mı?") is answered by state.now
    ONLY because the model picks it (the corpus harness exempts state.now for that
    reason): the router names no tool, so the local mode says "Anlayamadım efendim"
    rather than guessing. And a tool that needs a model-composed argument, called
    anyway with empty arguments, is a FAILED call with the validation message - never a
    guessed answer, never a 5xx, never a mutation."""
    client, _identity, runtime, *_ = wired
    _register_local(runtime)
    sid = _create(client, transport=TRANSPORT_TEXT)["session_id"]
    said = _say(client, sid, "Göz açık mı?", turn=1)
    resolved = said["resolved_intents"][0]
    assert resolved["intent"] == "explain" and resolved["klass"] == "query"
    assert resolved["capability"] is None and resolved["tool"] is None
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": f"local-{uuid.uuid4()}", "name": "state.now", "arguments": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed", body
    # A failed call carries `error` (class + the handler's message), never `result`; the
    # web half speaks the failure from exactly here, and also speaks `error.speech`
    # when a handler names the sentence (ADR-0067 follow-up).
    assert body["error"]["error_class"] == "validation_error"
    assert "question" in body["error"]["message"]
    with runtime.session() as db:
        assert is_eye_enabled(db) is True  # nothing was guessed into a mutation


def test_a_local_session_can_be_closed_and_refuses_after(wired) -> None:
    client, _identity, runtime, *_ = wired
    _register_local(runtime)
    sid = _create(client, transport=TRANSPORT_TEXT)["session_id"]
    assert client.post(f"/v1/voice/realtime/sessions/{sid}/close").status_code == 200
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 1, "turn": 1, "text": "Gözünü kapat."}]},
    )
    assert response.status_code == 410


def test_create_without_the_explicit_ask_still_picks_the_conversation_provider(wired) -> None:
    """The paid path is the default (ADR-0173). Here the configured conversation provider
    is the simulator; with the local router registered beside it, a plain create still
    gets the simulator, and the recorded selection says why the local router was not it."""
    client, _identity, runtime, *_ = wired
    _register_local(runtime)
    created = _create(client)
    assert created["provider"] == SIMULATOR_PROVIDER_NAME
    assert created["transport"] != TRANSPORT_TEXT
    assert "secret" in created["credential"]
    with runtime.session() as db:
        row = service.get_session(db, uuid.UUID(created["session_id"]))
        selection = row.context_json["selection"]
    assert selection["selected"] == SIMULATOR_PROVIDER_NAME
    assert "speech_to_speech" in selection["rejected"][LOCAL_ROUTER_PROVIDER_NAME]


def test_a_text_ask_with_no_text_provider_is_refused_with_the_reason(wired) -> None:
    client, *_ = wired  # the fixture registers the simulator alone
    response = client.post("/v1/voice/realtime/sessions", json={"transport": TRANSPORT_TEXT})
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["error_class"] == "capability_missing"
    assert detail["details"]["rejected"][SIMULATOR_PROVIDER_NAME] == [f"transport={TRANSPORT_TEXT}"]


# --------------------------------------------------------- contract halves


def test_the_web_local_mode_asks_for_the_transport_the_server_defines() -> None:
    """`localMode.ts` restates the transport constant (different runtimes); this reads
    the TypeScript and fails when the two spellings drift - and when the web half starts
    sending a wire voice, which the local router does not vet and the route would 422."""
    source = WEB_LOCAL_MODE.read_text(encoding="utf-8")
    match = re.search(r'export const LOCAL_TRANSPORT = "([a-z]+)"', source)
    assert match, "LOCAL_TRANSPORT not found in the web client"
    assert match.group(1) == TRANSPORT_TEXT
    prefix = re.search(r'export const LOCAL_CALL_ID_PREFIX = "([a-z-]+)"', source)
    assert prefix, "LOCAL_CALL_ID_PREFIX not found in the web client"
    assert re.fullmatch(r"[A-Za-z0-9_.:-]+", prefix.group(1) + "x"), (
        "must fit ToolCallRequest.call_id"
    )
    create = re.search(r"api\.create\(\{(.*?)\}\)", source, re.S)
    assert create, "the local mode's create call not found"
    assert "voice" not in create.group(1)
    assert "transport: LOCAL_TRANSPORT" in create.group(1)
