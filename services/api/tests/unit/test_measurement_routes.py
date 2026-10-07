"""Unit tests: ``/v1/voice/measurement`` through the REAL application object
(team/plans/measure-recordings-api-adr.md).

``create_app`` alone mounts the router - nothing here includes it by hand - so a route that
was built and never wired fails every test below (ADR-0078).
"""

from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.main import create_app
from app.object_store import InMemoryObjectStore
from app.voice import stt_compare
from app.voice.measurement import routes as measurement_routes
from app.voice.measurement import service as measurement
from tests.identity_support import authenticate, install_identity
from tests.unit.test_measurement_recordings import ITEM_FIELDS, BrokenStore, b64, wav

BASE = "/v1/voice/measurement"

#: The six calls of the contract: method, path, JSON body.
SIX_CALLS = [
    ("GET", BASE, None),
    ("PUT", f"{BASE}/recordings/ev/1", {"audio_wav_base64": b64(wav())}),
    ("GET", f"{BASE}/recordings/ev/1/audio", None),
    ("DELETE", f"{BASE}/recordings/ev/1", None),
    ("DELETE", f"{BASE}/recordings", None),
    ("GET", f"{BASE}/manifest", None),
]


def _app(store) -> tuple[object, TestClient]:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._store = store
    app.state.artifacts = artifacts
    return app, TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture()
def anonymous(store) -> TestClient:
    return _app(store)[1]


@pytest.fixture()
def owner(store) -> TestClient:
    app, client = _app(store)
    authenticate(app, client)
    return client


def _is_turkish_refusal(response, code: str) -> None:
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message"}
    assert detail["code"] == code
    assert any(letter in detail["message"] for letter in "çğıöşüÇĞİÖŞÜ"), detail["message"]


# ------------------------------------------------------------------ the session gate


@pytest.mark.parametrize(
    ("method", "path", "body"), SIX_CALLS, ids=[f"{m} {p}" for m, p, _ in SIX_CALLS]
)
def test_without_the_owner_session_each_of_the_six_is_401(anonymous, store, method, path, body):
    response = anonymous.request(method, path, json=body)
    assert response.status_code == 401
    assert store._objects == {}


# ------------------------------------------------------------------ the contract


def test_the_sentences_are_the_thirty_in_order_and_the_page_knows_the_rules(owner):
    response = owner.get(BASE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"sentences", "places", "retention_days", "max_seconds", "recordings"}
    # the owner's twenty keep their numbers; the ten offline command sentences are 21-30
    assert [s["text"] for s in body["sentences"]] == list(stt_compare.MEASUREMENT_SENTENCES)
    assert [s["text"] for s in body["sentences"]][:20] == list(stt_compare.OWNER_SENTENCES)
    assert [s["index"] for s in body["sentences"]] == list(range(1, 31))
    assert all(set(s) == {"index", "text"} for s in body["sentences"])
    assert body["places"] == ["ev", "ofis"]
    assert body["retention_days"] == 30
    assert body["max_seconds"] == 30
    assert body["recordings"] == []


def test_put_returns_the_item_and_the_list_holds_it(owner, store):
    audio = wav(1.25)
    response = owner.put(
        f"{BASE}/recordings/ofis/7",
        json={
            "audio_wav_base64": b64(audio),
            "browser_transcript": "alarmı on dakika ertele",
            "browser_engine": "chrome-web-speech",
            "capture": {"noiseSuppression": True, "channelCount": 1},
        },
    )
    assert response.status_code == 200, response.text
    item = response.json()
    assert set(item) == ITEM_FIELDS
    assert item["place"] == "ofis" and item["index"] == 7
    assert item["file"] == "ofis-07.wav"
    assert item["reference"] == stt_compare.OWNER_SENTENCES[6]
    assert item["sha256"] == hashlib.sha256(audio).hexdigest()
    assert item["bytes"] == len(audio) and item["audio_ms"] == 1250
    assert item["browser_transcript"] == "alarmı on dakika ertele"
    assert item["capture"] == {"noiseSuppression": True, "channelCount": 1}
    assert owner.get(BASE).json()["recordings"] == [item]
    assert set(store._objects) == {
        "voice-measurement/ofis/07.wav",
        "voice-measurement/ofis/07.json",
    }


def test_a_second_put_replaces_the_first(owner):
    owner.put(f"{BASE}/recordings/ev/1", json={"audio_wav_base64": b64(wav(1.0, fill=1))})
    second = wav(2.0, fill=2)
    replaced = owner.put(
        f"{BASE}/recordings/ev/1", json={"audio_wav_base64": b64(second), "browser_transcript": ""}
    )
    assert replaced.status_code == 200
    listed = owner.get(BASE).json()["recordings"]
    assert len(listed) == 1
    assert listed[0]["sha256"] == hashlib.sha256(second).hexdigest()
    assert listed[0]["browser_transcript"] == ""


def test_get_audio_is_audio_wav_with_the_same_sha256(owner):
    audio = wav(0.5, fill=33)
    item = owner.put(f"{BASE}/recordings/ev/2", json={"audio_wav_base64": b64(audio)}).json()
    response = owner.get(f"{BASE}/recordings/ev/2/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == audio
    assert hashlib.sha256(response.content).hexdigest() == item["sha256"]


def test_get_audio_of_a_sentence_never_read_is_a_turkish_404(owner):
    response = owner.get(f"{BASE}/recordings/ev/3/audio")
    assert response.status_code == 404
    _is_turkish_refusal(response, "not_found")


def test_delete_one_answers_1_then_0(owner, store):
    owner.put(f"{BASE}/recordings/ev/1", json={"audio_wav_base64": b64(wav())})
    first = owner.delete(f"{BASE}/recordings/ev/1")
    assert first.status_code == 200 and first.json() == {"deleted": 1}
    assert owner.delete(f"{BASE}/recordings/ev/1").json() == {"deleted": 0}
    assert store._objects == {}


def test_delete_all_removes_every_recording_of_every_place(owner, store):
    for place, index in (("ev", 1), ("ev", 2), ("ofis", 20)):
        owner.put(f"{BASE}/recordings/{place}/{index}", json={"audio_wav_base64": b64(wav())})
    response = owner.delete(f"{BASE}/recordings")
    assert response.status_code == 200 and response.json() == {"deleted": 3}
    assert store._objects == {}
    assert owner.get(BASE).json()["recordings"] == []


def test_the_manifest_is_what_load_manifest_reads_and_takes_a_place(owner, tmp_path):
    owner.put(
        f"{BASE}/recordings/ev/1",
        json={"audio_wav_base64": b64(wav()), "browser_transcript": "hesap makinesini aç"},
    )
    owner.put(f"{BASE}/recordings/ofis/2", json={"audio_wav_base64": b64(wav())})
    response = owner.get(f"{BASE}/manifest")
    assert response.status_code == 200, response.text
    manifest = response.json()
    assert set(manifest) == {"language", "items"}
    assert manifest["language"] == "tr-TR"
    assert manifest["items"] == [
        {
            "file": "ev-01.wav",
            "reference": stt_compare.OWNER_SENTENCES[0],
            "recorded_where": "ev",
            "ready_transcripts": {"chrome-web-speech": "hesap makinesini aç"},
            "browser_engine": None,
        },
        {
            "file": "ofis-02.wav",
            "reference": stt_compare.OWNER_SENTENCES[1],
            "recorded_where": "ofis",
            "browser_engine": None,
        },
    ]
    # The other half of the contract reads the very bytes this half served.
    for item in manifest["items"]:
        place, number = item["file"].removesuffix(".wav").split("-")
        audio = owner.get(f"{BASE}/recordings/{place}/{int(number)}/audio")
        (tmp_path / item["file"]).write_bytes(audio.content)
    (tmp_path / stt_compare.MANIFEST_NAME).write_bytes(response.content)
    _, loaded = stt_compare.load_manifest(tmp_path)
    assert [r.reference for r in loaded] == [i["reference"] for i in manifest["items"]]

    only = owner.get(f"{BASE}/manifest", params={"place": "ofis"}).json()
    assert [i["file"] for i in only["items"]] == ["ofis-02.wav"]
    refused = owner.get(f"{BASE}/manifest", params={"place": "araba"})
    assert refused.status_code == 422
    _is_turkish_refusal(refused, "invalid_place")


def test_get_purges_before_it_lists(owner, store, monkeypatch):
    owner.put(f"{BASE}/recordings/ev/1", json={"audio_wav_base64": b64(wav())})
    sidecar = json.loads(store._objects["voice-measurement/ev/01.json"])
    sidecar["expires_at"] = "2026-01-01T00:00:00Z"
    store.put("voice-measurement/ev/01.json", json.dumps(sidecar).encode("utf-8"))
    assert owner.get(BASE).json()["recordings"] == []
    assert store._objects == {}


# ------------------------------------------------------------------ refusals


@pytest.mark.parametrize(
    ("path", "body", "status", "code"),
    [
        ("ev/1", {"audio_wav_base64": "bu base64 değil!!"}, 422, "not_base64"),
        ("ev/1", {"audio_wav_base64": b64(wav(rate=44_100))}, 422, "wav_wrong_sample_rate"),
        ("ev/1", {"audio_wav_base64": b64(wav(31))}, 422, "wav_too_long"),
        ("ev/1", {}, 422, "audio_missing"),
        ("araba/1", {"audio_wav_base64": b64(wav())}, 422, "invalid_place"),
        ("ev/31", {"audio_wav_base64": b64(wav())}, 422, "invalid_index"),
        ("ev/bir", {"audio_wav_base64": b64(wav())}, 422, "invalid_index"),
        (
            "ev/1",
            {"audio_wav_base64": b64(wav()), "browser_transcript": "a" * 501},
            422,
            "transcript_too_long",
        ),
        (
            "ev/1",
            {"audio_wav_base64": b64(wav()), "capture": {f"k{n}": n for n in range(21)}},
            422,
            "capture_too_many_keys",
        ),
    ],
)
def test_a_refusal_is_code_and_a_turkish_message_and_writes_nothing(
    owner, store, path, body, status, code
):
    response = owner.put(f"{BASE}/recordings/{path}", json=body)
    assert response.status_code == status, response.text
    _is_turkish_refusal(response, code)
    assert store._objects == {}


def test_a_body_that_is_not_a_json_object_is_refused_in_the_same_shape(owner, store):
    response = owner.put(
        f"{BASE}/recordings/ev/1", content=b"[1, 2", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    _is_turkish_refusal(response, "invalid_body")
    assert store._objects == {}


def test_a_body_over_the_size_bound_is_refused_before_it_is_decoded(owner, store, monkeypatch):
    def never(*_args, **_kwargs):
        raise AssertionError("an oversize body was parsed or decoded")

    # The routes module's own name for the parser: the json module itself is everyone's.
    monkeypatch.setattr(measurement_routes, "json", SimpleNamespace(loads=never))
    monkeypatch.setattr(measurement.base64, "b64decode", never)
    oversize = b'{"audio_wav_base64": "' + b"A" * measurement_routes.MAX_BODY_BYTES + b'"}'
    response = owner.put(
        f"{BASE}/recordings/ev/1", content=oversize, headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    _is_turkish_refusal(response, "audio_too_large")
    assert store._objects == {}


def test_the_body_bound_admits_the_largest_valid_recording():
    largest = len(base64.b64encode(bytes(measurement.MAX_AUDIO_BYTES)))
    assert measurement.MAX_BASE64_CHARS == largest
    assert measurement_routes.MAX_BODY_BYTES > largest


# ------------------------------------------------------------------ a store that fails


@pytest.mark.parametrize(
    ("method", "path", "body"), SIX_CALLS, ids=[f"{m} {p}" for m, p, _ in SIX_CALLS]
)
def test_an_object_store_that_raises_gives_503_not_500(method, path, body):
    app, client = _app(BrokenStore())
    authenticate(app, client)
    response = client.request(method, path, json=body)
    assert response.status_code == 503, response.text
    _is_turkish_refusal(response, "store_unavailable")
    assert "minio is down" not in response.text
    assert "Traceback" not in response.text


# ------------------------------------------------------------------ the server holds the 30 days


def test_the_application_registers_the_purge_on_its_retention_sweeper(store):
    """The sweep the REAL application registered, run against the store the application
    serves from: an expired recording is gone without anybody opening the page."""
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.state.artifacts._store = store
    install_identity(app, settings=settings)
    client = TestClient(app)
    authenticate(app, client)
    client.put(f"{BASE}/recordings/ev/1", json={"audio_wav_base64": b64(wav())})
    sidecar = json.loads(store._objects["voice-measurement/ev/01.json"])
    sidecar["expires_at"] = "2026-01-01T00:00:00Z"
    store.put("voice-measurement/ev/01.json", json.dumps(sidecar).encode("utf-8"))

    sweeper = app.state.retention_sweeper
    assert "measurement_recordings" in sweeper.names
    assert sweeper._sweeps["measurement_recordings"]() == 1
    assert store._objects == {}
    assert sweeper._sweeps["measurement_recordings"]() == 0  # not due again for a day
