"""B21 req 224/225/226/227/414: the narration engine makes a sound.

`app/narration/engine.py` has planned, split, cached and cancelled since M4. `NarrationEngine`
— the read-ahead pipeline requirement 226 asks for, complete with cache keys, a lookahead
window and cancellation of chunks the cursor moved away from — has existed the whole time
with **no implementation of its `Synthesizer` seam anywhere under `app/`**. The only thing
that ever passed one was `test_narration_engine.py`. So an owner could start a narration
session, hear the command machine answer "reading", resume on another device, and never at
any point be read to.

These tests drive the REAL routes over the REAL engine, with a provider that really
returns bytes, and assert the things that distinguish a reader from a state machine: the
response carries playable audio, the next sentences are already synthesised when this one
is handed over, a paused session pays for nothing, and a deployment with no voice degrades
to the text (req 233's rule reaching the third delivery path) instead of buzzing.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts import service as artifact_service
from app.artifacts.models import (
    Artifact,
    ArtifactRender,
    ArtifactVersion,
    Task,
    TaskRun,
)
from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.main import create_app
from app.narration.models import NarrationSession, PronunciationEntry
from app.narration.runtime import NarrationRuntime
from app.narration.synth import ProviderSynthesizer
from app.voice.providers import FakeTTSProvider, wav_duration_ms

BODY = """# Rapor

Birinci cümle burada duruyor. İkinci cümle onu izliyor. Üçüncü cümle de var.

Dördüncü cümle ayrı bir paragrafta. Beşinci cümle onun yanında.
"""


class _CountingProvider:
    """A provider that really speaks, and counts how often it was asked to.

    `FakeTTSProvider` declares itself synthetic (B20 req 234) so the narration path refuses
    it, which is the correct production behaviour and useless for exercising the path where
    audio is produced. This is the explicit stand-in, and the count is what proves the
    read-ahead cache is doing its job rather than looking as if it is.
    """

    name = "tts-that-speaks"

    def __init__(self) -> None:
        self.inner = FakeTTSProvider(name="tts-that-speaks", synthetic_speech=False)
        self.texts: list[str] = []

    def synthesize(self, text: str, **kwargs: object):  # noqa: ANN003, ANN201
        self.texts.append(text)
        return self.inner.synthesize(text, **kwargs)  # type: ignore[arg-type]


def _identity_tables():
    from app.identity.models import Base as IdentityBase

    return list(IdentityBase.metadata.tables.values())


@pytest.fixture()
def wired():
    """The real app, the real narration routes, an in-memory database and a real voice."""
    settings = Settings(_env_file=None)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in _identity_tables():
        table.create(engine, checkfirst=True)
    for table in (
        Task.__table__,
        TaskRun.__table__,
        Artifact.__table__,
        ArtifactVersion.__table__,
        ArtifactRender.__table__,
        NarrationSession.__table__,
        PronunciationEntry.__table__,
    ):
        table.create(engine, checkfirst=True)

    app = create_app(settings)
    identity = IdentityRuntime(settings, engine=engine, root=InMemoryCredentialRoot())
    identity.service.bootstrap()
    app.state.identity = identity
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts

    provider = _CountingProvider()
    narration = NarrationRuntime(settings)
    narration._engine = engine
    narration._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.narration = narration

    issued = identity.service.issue_session(client_kind="web", label="browser")
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.token}"

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        artifact = artifact_service.get_or_create_artifact_for_task(
            session, task_id=None, title="Anlatım Testi"
        )
        artifact_service.add_artifact_version(
            session,
            artifact_id=artifact.id,
            canonical_body=BODY,
            content_hash=hashlib.sha256(BODY.encode("utf-8")).hexdigest(),
        )
        artifact_id = artifact.id

    try:
        yield client, narration, provider, artifact_id
    finally:
        client.close()


def _with_voice(narration: NarrationRuntime, provider: object) -> None:
    """Give this runtime a voice, the way a configured deployment would have one."""
    from app.narration.engine import NarrationEngine
    from app.narration.runtime import NARRATION_LOOKAHEAD

    narration._narrator = NarrationEngine(
        ProviderSynthesizer(provider), lookahead=NARRATION_LOOKAHEAD
    )
    narration._narrator_built = True


def _without_voice(narration: NarrationRuntime) -> None:
    narration._narrator = None
    narration._narrator_built = True
    narration._no_voice_reason = "no_tts_key"


def _start(client: TestClient, artifact_id: uuid.UUID) -> str:
    created = client.post("/v1/narration/sessions", json={"artifact_id": str(artifact_id)})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def _command(client: TestClient, session_id: str, command: str) -> dict:
    response = client.post(
        f"/v1/narration/sessions/{session_id}/command", json={"command": command}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_oku_produces_audio_a_client_can_actually_play(wired):
    client, narration, provider, artifact_id = wired
    _with_voice(narration, provider)
    session_id = _start(client, artifact_id)

    payload = _command(client, session_id, "oku")

    audio = payload["audio"]
    assert audio is not None, "the reader read nothing"
    assert audio["chunk_id"] == payload["current_chunk"]["chunk_id"]
    assert audio["format"] == "wav"
    assert audio["bytes"] > 0
    assert audio["seconds"] > 0
    assert audio["url"] == f"/v1/narration/sessions/{session_id}/chunks/{audio['chunk_id']}/audio"

    fetched = client.get(audio["url"])
    assert fetched.status_code == 200, fetched.text
    assert fetched.headers["content-type"].startswith("audio/wav")
    assert fetched.content[:4] == b"RIFF"
    # The sha256 in the command response names THESE bytes, so a client can tell whether
    # what it fetched is what it was promised.
    assert hashlib.sha256(fetched.content).hexdigest() == audio["sha256"]
    assert len(fetched.content) == audio["bytes"]
    # And the duration was measured from the audio, not estimated from the text.
    assert round(wav_duration_ms(fetched.content) / 1000, 2) == audio["seconds"]


def test_the_next_sentences_are_already_spoken_when_this_one_is_handed_over(wired):
    client, narration, provider, artifact_id = wired
    _with_voice(narration, provider)
    session_id = _start(client, artifact_id)

    first = _command(client, session_id, "oku")

    # req 226: the window, by name. Not "we intend to read ahead" — these chunk ids have
    # audio in the cache right now.
    assert first["audio"]["ready_ahead"], "nothing was prepared ahead of the cursor"
    assert first["audio"]["from_cache"] is False
    prepared = provider.texts.count(first["current_chunk"]["text"])
    assert prepared == 1

    for chunk_id in first["audio"]["ready_ahead"]:
        ahead = client.get(f"/v1/narration/sessions/{session_id}/chunks/{chunk_id}/audio")
        assert ahead.status_code == 200, chunk_id
        assert ahead.content[:4] == b"RIFF"


def test_the_cache_is_why_the_reader_does_not_stutter(wired):
    client, narration, provider, artifact_id = wired
    _with_voice(narration, provider)
    session_id = _start(client, artifact_id)
    _command(client, session_id, "oku")
    calls_after_first = len(provider.texts)

    # "tekrar" goes back to the paragraph's first sentence: already synthesised.
    again = _command(client, session_id, "tekrar")

    assert again["audio"]["from_cache"] is True
    assert len(provider.texts) == calls_after_first, "the same sentence was paid for twice"


def test_a_paused_session_pays_for_nothing(wired):
    # req 227. "dur" is the one command that must never cost a provider call: the owner
    # just said stop, and synthesising the next three sentences anyway is both money and
    # the audio equivalent of not listening.
    client, narration, provider, artifact_id = wired
    _with_voice(narration, provider)
    session_id = _start(client, artifact_id)
    _command(client, session_id, "oku")
    before = len(provider.texts)

    paused = _command(client, session_id, "dur")

    assert paused["state"] == "PAUSED"
    assert paused["audio"] is None
    assert paused["current_chunk"] is None
    assert len(provider.texts) == before


def test_devam_resumes_at_the_saved_sentence_and_speaks_it(wired):
    client, narration, provider, artifact_id = wired
    _with_voice(narration, provider)
    session_id = _start(client, artifact_id)
    started = _command(client, session_id, "oku")
    _command(client, session_id, "dur")

    resumed = _command(client, session_id, "devam")

    assert resumed["state"] == "READING"
    assert resumed["current_chunk"]["chunk_id"] == started["current_chunk"]["chunk_id"]
    assert resumed["audio"]["chunk_id"] == started["audio"]["chunk_id"]
    assert resumed["audio"]["from_cache"] is True


def test_a_deployment_with_no_voice_reads_in_text_and_says_why(wired):
    # req 233's rule on the third delivery path: no voice is not silence with no
    # explanation. The sentence is still in the response; what is missing is named.
    client, narration, provider, artifact_id = wired
    _without_voice(narration)
    session_id = _start(client, artifact_id)

    payload = _command(client, session_id, "oku")

    assert payload["current_chunk"]["text"]
    assert payload["audio"] is None
    assert payload["audio_unavailable"] == "no_tts_key"
    assert provider.texts == []

    # And the audio endpoint says the same thing in the server's own taxonomy (B20 req 235)
    # rather than 404-ing as if the chunk were the problem.
    refused = client.get(f"/v1/narration/sessions/{session_id}/chunks/s0-p0-0/audio")
    assert refused.status_code == 503
    assert refused.json()["detail"]["error_class"] == "provider_auth_missing"


def test_the_audio_endpoint_never_spends_a_provider_call(wired):
    # A GET that can synthesise is a GET that can be made to spend money in a loop. The
    # cursor decides what is worth saying; a URL does not.
    client, narration, provider, artifact_id = wired
    _with_voice(narration, provider)
    session_id = _start(client, artifact_id)
    _command(client, session_id, "oku")
    before = len(provider.texts)

    missing = client.get(f"/v1/narration/sessions/{session_id}/chunks/s9-p9-9/audio")

    assert missing.status_code == 404
    assert len(provider.texts) == before


def test_an_unknown_session_is_not_a_missing_chunk(wired):
    client, narration, provider, artifact_id = wired
    _with_voice(narration, provider)

    response = client.get(f"/v1/narration/sessions/{uuid.uuid4()}/chunks/s0-p0-0/audio")

    assert response.status_code == 404
    assert response.json()["detail"] == "unknown narration session"


def test_a_synthesis_failure_leaves_the_command_working(wired):
    # The command positioned the cursor; that happened whatever the provider did. A
    # narration that cannot speak degrades to text and does not fail the cursor move.
    client, narration, provider, artifact_id = wired

    class _Broken:
        name = "broken-tts"

        def synthesize(self, text: str, **kwargs: object):  # noqa: ANN003, ANN201
            raise RuntimeError("provider exploded")

    _with_voice(narration, _Broken())
    session_id = _start(client, artifact_id)

    payload = _command(client, session_id, "oku")

    assert payload["ok"] is True
    assert payload["current_chunk"]["text"]
    assert payload["audio"] is None
    assert payload["audio_unavailable"] == "synthesis_failed"


# ----------------------------------------------------- the voice, or none at all


class _KeylessSettings:
    voice_openai_api_key = ""
    openai_api_key = ""


class _KeyedSettings:
    voice_openai_api_key = "sk-real"
    openai_api_key = "sk-real"


def test_a_deployment_with_no_key_gets_no_narrator_rather_than_a_tone():
    """B20 req 234's policy, on the longest form of speech this system produces.

    With no key the only provider `build_greeting_tts` can return is the offline fake, and
    what it produces is a 110 Hz sine. A minute and a half of that, read out of an
    artifact, is the worst version of the buzz B13 refused after an alarm.
    """
    from app.narration.synth import build_synthesizer

    assert build_synthesizer(_KeylessSettings()) is None
    # Any fake, under any name — the policy asks the class.
    assert build_synthesizer(_KeylessSettings(), provider=FakeTTSProvider("fake-tts")) is None
    assert build_synthesizer(_KeylessSettings(), provider=FakeTTSProvider("marin-sounding")) is None


def test_a_configured_deployment_gets_a_synthesizer_that_speaks():
    from app.narration.synth import ProviderSynthesizer as PS
    from app.narration.synth import build_synthesizer

    keyed = build_synthesizer(_KeyedSettings())
    assert isinstance(keyed, PS)
    assert keyed.name == "openai"

    injected = build_synthesizer(_KeylessSettings(), provider=_CountingProvider())
    assert isinstance(injected, PS)


def test_the_adapter_translates_the_signature_the_matrix_calls_mismatched():
    """`synthesize(text, settings) -> bytes` over `synthesize(text, *, voice, speed, fmt)`."""
    from app.narration.engine import VoiceSettings
    from app.narration.synth import ProviderSynthesizer as PS

    seen: dict[str, object] = {}

    class _Recording:
        name = "recording"

        def synthesize(self, text, *, voice, speed, fmt):  # noqa: ANN001, ANN201
            seen.update({"text": text, "voice": voice, "speed": speed, "fmt": fmt})
            return type("R", (), {"audio": b"RIFFxxxx"})()

    audio = PS(_Recording()).synthesize("Merhaba.", VoiceSettings(voice="marin", speed=1.25))

    assert audio == b"RIFFxxxx"
    assert seen == {"text": "Merhaba.", "voice": "marin", "speed": 1.25, "fmt": "wav"}


def test_a_provider_that_answers_with_no_audio_is_a_failure_not_a_silence():
    from app.narration.engine import VoiceSettings
    from app.narration.synth import NarrationSynthesisFailed
    from app.narration.synth import ProviderSynthesizer as PS

    class _Empty:
        name = "empty"

        def synthesize(self, text, **kwargs):  # noqa: ANN001, ANN003, ANN201
            return type("R", (), {"audio": b""})()

    with pytest.raises(NarrationSynthesisFailed):
        PS(_Empty()).synthesize("Merhaba.", VoiceSettings())
    with pytest.raises(NarrationSynthesisFailed):
        PS(_Empty()).synthesize("   ", VoiceSettings())
