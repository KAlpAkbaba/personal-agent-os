"""Unit tests: the measurement recordings store (team/plans/measure-recordings-api-adr.md).

The owner reads the twenty scripted sentences once; the WAV rests 30 days on his own Cloud
Core, is replaced on "tekrar", deleted on "sil", and handed to the measuring tool as exactly
the manifest ``app.voice.stt_compare.load_manifest`` reads. Everything here runs on
``InMemoryObjectStore`` with the clock passed in - no sleep, no network.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from app.object_store import InMemoryObjectStore
from app.voice import stt_compare
from app.voice.measurement import service as measurement
from app.voice.measurement.service import DailyPurge, Recordings, Refusal

T0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
ITEM_FIELDS = {
    "place",
    "index",
    "file",
    "reference",
    "recorded_at",
    "expires_at",
    "audio_ms",
    "bytes",
    "sha256",
    "browser_transcript",
    "browser_engine",
    "capture",
}


def wav(
    seconds: float = 1.0,
    *,
    rate: int = 16_000,
    channels: int = 1,
    bits: int = 16,
    fill: int = 7,
) -> bytes:
    """A RIFF/WAVE stream with the header the arguments say and ``seconds`` of samples."""
    frame = channels * (bits // 8)
    data = bytes([fill]) * (int(seconds * rate) * frame)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * frame, frame, bits)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def b64(audio: bytes) -> str:
    return base64.b64encode(audio).decode("ascii")


@pytest.fixture()
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture()
def recordings(store: InMemoryObjectStore) -> Recordings:
    return Recordings(store)


def keys(store: InMemoryObjectStore) -> set[str]:
    return set(store._objects)


def put(recordings: Recordings, place: str = "ev", index: int = 1, **overrides) -> dict:
    arguments = {"audio_wav_base64": b64(wav()), "now": T0, **overrides}
    return recordings.save(place, index, **arguments)


# ------------------------------------------------------------------ save, read, replace


def test_a_valid_wav_is_saved_and_the_item_carries_every_contract_field(recordings, store):
    audio = wav(1.5)
    item = put(
        recordings,
        audio_wav_base64=b64(audio),
        browser_transcript="ofis bilgisayarımdan hesap makinesini aç",
        browser_engine="chrome-web-speech/on-device",
        capture={"noiseSuppression": True, "sampleRate": 48000, "deviceLabel": "Masa mikrofonu"},
    )
    assert set(item) == ITEM_FIELDS
    assert item["place"] == "ev"
    assert item["index"] == 1
    assert item["file"] == "ev-01.wav"
    assert item["reference"] == stt_compare.OWNER_SENTENCES[0]
    assert item["sha256"] == hashlib.sha256(audio).hexdigest()
    assert item["bytes"] == len(audio)
    assert item["audio_ms"] == 1500
    assert item["recorded_at"] == "2026-10-02T12:00:00Z"
    assert item["expires_at"] == "2026-11-01T12:00:00Z"
    assert item["browser_transcript"] == "ofis bilgisayarımdan hesap makinesini aç"
    assert item["browser_engine"] == "chrome-web-speech/on-device"
    assert item["capture"] == {
        "noiseSuppression": True,
        "sampleRate": 48000,
        "deviceLabel": "Masa mikrofonu",
    }
    assert keys(store) == {"voice-measurement/ev/01.wav", "voice-measurement/ev/01.json"}
    assert recordings.list_items(T0) == [item]


def test_the_bytes_read_back_are_the_bytes_sent(recordings):
    audio = wav(0.7, fill=42)
    put(recordings, "ofis", 20, audio_wav_base64=b64(audio))
    assert recordings.read_audio("ofis", 20, T0) == audio


def test_the_last_sentence_of_the_office_is_ofis_20(recordings):
    item = put(recordings, "ofis", 20)
    assert item["file"] == "ofis-20.wav"
    assert item["reference"] == stt_compare.OWNER_SENTENCES[19]


def test_a_second_save_replaces_audio_and_metadata_and_there_is_still_one_item(recordings, store):
    put(recordings, browser_transcript="ilk okuma", capture={"echoCancellation": True})
    second = wav(2.0, fill=99)
    later = T0 + timedelta(days=10)
    item = put(recordings, audio_wav_base64=b64(second), browser_transcript=None, now=later)
    assert recordings.read_audio("ev", 1, later) == second
    listed = recordings.list_items(later)
    assert listed == [item]
    assert item["sha256"] == hashlib.sha256(second).hexdigest()
    assert item["audio_ms"] == 2000
    assert item["browser_transcript"] is None
    assert item["capture"] is None
    # "tekrar" restarts the thirty days.
    assert item["expires_at"] == "2026-11-11T12:00:00Z"
    assert len(keys(store)) == 2


# ------------------------------------------------------------------ refusals


REFUSALS = [
    ("not_base64", {"audio_wav_base64": "bu base64 değil!!"}),
    ("not_wav", {"audio_wav_base64": b64(b"ID3" + bytes(400))}),
    ("wav_not_16bit", {"audio_wav_base64": b64(wav(bits=8))}),
    ("wav_not_mono", {"audio_wav_base64": b64(wav(channels=2))}),
    ("wav_wrong_sample_rate", {"audio_wav_base64": b64(wav(rate=44_100))}),
    ("wav_empty", {"audio_wav_base64": b64(wav(0))}),
    ("wav_too_long", {"audio_wav_base64": b64(wav(31))}),
    ("audio_too_large", {"audio_wav_base64": b64(wav(1) + bytes(1_048_576))}),
    ("invalid_place", {"place": "araba"}),
    ("invalid_index", {"index": 0}),
    ("invalid_index", {"index": 21}),
    ("transcript_too_long", {"browser_transcript": "a" * 501}),
    ("engine_too_long", {"browser_engine": "e" * 65}),
    ("capture_too_many_keys", {"capture": {f"k{n}": n for n in range(21)}}),
    ("capture_invalid", {"capture": {"nested": {"a": 1}}}),
    ("capture_invalid", {"capture": {"label": "x" * 121}}),
]


@pytest.mark.parametrize(
    ("code", "overrides"), REFUSALS, ids=[f"{c}-{n}" for n, (c, _) in enumerate(REFUSALS)]
)
def test_each_refusal_has_its_own_code_and_writes_nothing(recordings, store, code, overrides):
    put(recordings, "ofis", 3)  # something is already there: a refusal must not disturb it
    before = dict(store._objects)
    arguments = {"place": "ev", "index": 1, **overrides}
    with pytest.raises(Refusal) as refused:
        put(recordings, arguments.pop("place"), arguments.pop("index"), **arguments)
    assert refused.value.code == code
    assert refused.value.message  # a Turkish sentence for the owner
    assert store._objects == before
    assert len(store._objects) == 2


def test_the_refusal_codes_are_distinct_per_cause():
    causes = [code for code, _ in REFUSALS]
    assert len(causes) == 16
    assert len(set(causes)) == 14  # index 0 / 21 share one, the two capture shapes share one


def test_an_oversize_body_is_refused_before_it_is_decoded(recordings, store, monkeypatch):
    def never(*_args, **_kwargs):
        raise AssertionError("an oversize upload was decoded")

    monkeypatch.setattr(measurement.base64, "b64decode", never)
    with pytest.raises(Refusal) as refused:
        put(recordings, audio_wav_base64="A" * (measurement.MAX_BASE64_CHARS + 4))
    assert refused.value.code == "audio_too_large"
    assert keys(store) == set()


def test_exactly_thirty_seconds_is_accepted(recordings):
    item = put(recordings, audio_wav_base64=b64(wav(30)))
    assert item["audio_ms"] == 30_000


def test_a_null_and_an_empty_browser_transcript_are_kept_apart(recordings):
    put(recordings, "ev", 1, browser_transcript=None)
    put(recordings, "ev", 2, browser_transcript="")
    first, second = recordings.list_items(T0)
    assert first["browser_transcript"] is None
    assert second["browser_transcript"] == ""


# ------------------------------------------------------------------ thirty days


def test_an_item_is_listed_on_day_30_and_purged_on_day_31(recordings, store):
    put(recordings)
    day_30 = T0 + timedelta(days=30)
    assert recordings.purge(day_30) == 0
    assert [item["file"] for item in recordings.list_items(day_30)] == ["ev-01.wav"]
    day_31 = T0 + timedelta(days=31)
    assert recordings.purge(day_31) == 1
    assert recordings.list_items(day_31) == []
    assert keys(store) == set()


def test_list_items_does_not_return_an_expired_item_when_purge_did_not_run(recordings, store):
    put(recordings, "ev", 1)
    put(recordings, "ofis", 2, now=T0 + timedelta(days=5))
    day_31 = T0 + timedelta(days=31)
    assert [item["file"] for item in recordings.list_items(day_31)] == ["ofis-02.wav"]
    assert len(keys(store)) == 4  # nothing was purged: the filter alone hid it
    with pytest.raises(Refusal) as refused:
        recordings.read_audio("ev", 1, day_31)
    assert refused.value.code == "not_found"
    assert [entry["file"] for entry in recordings.manifest(day_31)["items"]] == ["ofis-02.wav"]


def test_purge_removes_both_objects_and_a_second_run_changes_nothing(recordings, store):
    put(recordings, "ev", 1)
    put(recordings, "ofis", 7)
    put(recordings, "ev", 2, now=T0 + timedelta(days=20))
    day_31 = T0 + timedelta(days=31)
    assert recordings.purge(day_31) == 2
    after_first = dict(store._objects)
    assert set(after_first) == {"voice-measurement/ev/02.wav", "voice-measurement/ev/02.json"}
    assert recordings.purge(day_31) == 0
    assert store._objects == after_first
    assert recordings.purge(T0 + timedelta(days=51)) == 1
    assert keys(store) == set()


# ------------------------------------------------------------------ "sil"


def test_delete_one_returns_1_then_0(recordings, store):
    put(recordings, "ev", 4)
    put(recordings, "ev", 5)
    assert recordings.delete_one("ev", 4) == 1
    assert recordings.delete_one("ev", 4) == 0
    assert keys(store) == {"voice-measurement/ev/05.wav", "voice-measurement/ev/05.json"}
    with pytest.raises(Refusal) as refused:
        recordings.delete_one("araba", 4)
    assert refused.value.code == "invalid_place"


def test_delete_all_with_recordings_in_both_places_returns_the_count_and_leaves_zero_keys(
    recordings, store
):
    put(recordings, "ev", 1)
    put(recordings, "ev", 20)
    put(recordings, "ofis", 1)
    assert recordings.delete_all() == 3
    assert keys(store) == set()
    assert recordings.delete_all() == 0


def test_delete_all_leaves_no_orphan_audio_behind(recordings, store):
    put(recordings, "ofis", 9)
    store.delete("voice-measurement/ofis/09.json")  # the write that never finished
    assert recordings.delete_all() == 1
    assert keys(store) == set()


# ------------------------------------------------------------------ half-written pairs


def test_a_sidecar_without_its_audio_is_not_listed_and_is_removed_by_purge(recordings, store):
    put(recordings, "ev", 1)
    put(recordings, "ev", 2)
    store.delete("voice-measurement/ev/01.wav")
    assert [item["file"] for item in recordings.list_items(T0)] == ["ev-02.wav"]
    with pytest.raises(Refusal) as refused:
        recordings.read_audio("ev", 1, T0)
    assert refused.value.code == "not_found"
    assert recordings.purge(T0) == 1
    assert keys(store) == {"voice-measurement/ev/02.wav", "voice-measurement/ev/02.json"}


def test_an_audio_without_its_sidecar_is_never_served_and_is_removed_by_purge(recordings, store):
    put(recordings, "ev", 1)
    store.delete("voice-measurement/ev/01.json")
    assert recordings.list_items(T0) == []
    with pytest.raises(Refusal):
        recordings.read_audio("ev", 1, T0)
    assert recordings.purge(T0) == 1
    assert keys(store) == set()


def test_an_unreadable_sidecar_is_not_listed_and_is_removed_by_purge(recordings, store):
    put(recordings, "ev", 1)
    store.put("voice-measurement/ev/01.json", b"{not json")
    assert recordings.list_items(T0) == []
    assert recordings.purge(T0) == 1
    assert keys(store) == set()


def test_a_sidecar_with_a_zoneless_expiry_is_unreadable_not_a_crash(recordings, store):
    put(recordings, "ev", 1)
    sidecar = json.loads(store._objects["voice-measurement/ev/01.json"])
    sidecar["expires_at"] = "2026-11-01T12:00:00"
    store.put("voice-measurement/ev/01.json", json.dumps(sidecar).encode("utf-8"))
    assert recordings.list_items(T0) == []
    assert recordings.purge(T0) == 1
    assert keys(store) == set()


# ------------------------------------------------------------------ the measuring tool


def test_the_manifest_loads_in_stt_compare_and_yields_the_same_references(recordings, tmp_path):
    """Contract halves read each other: what this side writes is what that side loads."""
    put(recordings, "ev", 1, browser_transcript="ofis bilgisayarımdan hesap makinesini aç")
    put(recordings, "ev", 10, browser_transcript=None)
    put(recordings, "ofis", 1, browser_transcript="", browser_engine="chrome-web-speech")
    manifest = recordings.manifest(T0)
    for item in manifest["items"]:
        place, number = item["file"].removesuffix(".wav").split("-")
        (tmp_path / item["file"]).write_bytes(recordings.read_audio(place, int(number), T0))
    (tmp_path / stt_compare.MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    language, loaded = stt_compare.load_manifest(tmp_path)

    assert language == "tr-TR" == manifest["language"]
    assert [r.item_id for r in loaded] == ["ev-01.wav", "ev-10.wav", "ofis-01.wav"]
    assert [r.reference for r in loaded] == [
        stt_compare.OWNER_SENTENCES[0],
        stt_compare.OWNER_SENTENCES[9],
        stt_compare.OWNER_SENTENCES[0],
    ]
    assert [r.recorded_where for r in loaded] == ["ev", "ev", "ofis"]
    assert all(r.path.is_file() for r in loaded)


def test_ready_transcripts_is_present_only_for_a_non_null_browser_transcript(recordings):
    put(recordings, "ev", 1, browser_transcript="hesap makinesini aç", browser_engine="chrome")
    put(recordings, "ev", 2, browser_transcript=None)
    put(recordings, "ev", 3, browser_transcript="")
    spoken, silent, empty = recordings.manifest(T0)["items"]
    assert spoken == {
        "file": "ev-01.wav",
        "reference": stt_compare.OWNER_SENTENCES[0],
        "recorded_where": "ev",
        "ready_transcripts": {"chrome-web-speech": "hesap makinesini aç"},
        "browser_engine": "chrome",
    }
    assert "ready_transcripts" not in silent
    assert silent["browser_engine"] is None
    assert empty["ready_transcripts"] == {"chrome-web-speech": ""}


def test_the_manifest_of_one_place_holds_that_place_only(recordings):
    put(recordings, "ev", 1)
    put(recordings, "ofis", 2)
    assert [i["file"] for i in recordings.manifest(T0, "ofis")["items"]] == ["ofis-02.wav"]
    assert [i["file"] for i in recordings.manifest(T0, "ev")["items"]] == ["ev-01.wav"]
    with pytest.raises(Refusal) as refused:
        recordings.manifest(T0, "araba")
    assert refused.value.code == "invalid_place"


def test_the_layout_is_every_key_that_can_exist():
    slots = measurement.slots()
    assert len(slots) == 40
    assert slots[0] == ("ev", 1) and slots[-1] == ("ofis", 20)
    assert len(stt_compare.OWNER_SENTENCES) == 20
    assert measurement.RETENTION_DAYS == 30


# ------------------------------------------------------------------ what is logged


def test_no_log_record_of_any_path_contains_the_transcript_or_the_base64(recordings, store):
    transcript = "çok özel bir cümle söyledim"
    audio = wav(1.0, fill=65)  # base64 of 0x41 bytes: a run of "QUFB"
    payload = b64(audio)
    with capture_logs() as logs:
        put(
            recordings,
            audio_wav_base64=payload,
            browser_transcript=transcript,
            capture={"deviceLabel": "gizli mikrofon adı"},
        )
        put(recordings, audio_wav_base64=payload, browser_transcript=transcript)  # replace
        for overrides in ({"browser_transcript": transcript + "a" * 500}, {"place": "araba"}):
            arguments = {"place": "ev", "index": 2, "audio_wav_base64": payload, **overrides}
            with pytest.raises(Refusal):
                put(recordings, arguments.pop("place"), arguments.pop("index"), **arguments)
        with pytest.raises(Refusal):
            put(recordings, "ev", 3, audio_wav_base64=b64(wav(rate=8_000, fill=65)))
        recordings.list_items(T0)
        recordings.read_audio("ev", 1, T0)
        recordings.manifest(T0)
        recordings.delete_one("ev", 1)
        put(recordings, "ofis", 1, audio_wav_base64=payload, browser_transcript=transcript)
        recordings.purge(T0 + timedelta(days=31))
        put(recordings, "ofis", 2, audio_wav_base64=payload, browser_transcript=transcript)
        recordings.delete_all()
    assert logs, "the store logs what it did"
    rendered = repr(logs)
    assert transcript not in rendered
    assert "gizli mikrofon" not in rendered
    assert "QUFBQUFB" not in rendered
    assert payload[:64] not in rendered
    saved = [entry for entry in logs if entry["event"] == "measurement_recording_saved"]
    assert saved[0]["place"] == "ev" and saved[0]["index"] == 1
    assert saved[0]["bytes"] == len(audio)
    assert saved[0]["sha256"] == hashlib.sha256(audio).hexdigest()


# ------------------------------------------------------------------ a store that fails


class BrokenStore:
    def put(self, key, data, content_type="application/octet-stream"):
        raise ConnectionError("minio is down")

    def get(self, key):
        raise ConnectionError("minio is down")

    def delete(self, key):
        raise ConnectionError("minio is down")

    def exists(self, key):
        raise ConnectionError("minio is down")


def test_a_store_fault_is_one_named_error_on_every_path():
    broken = Recordings(BrokenStore())
    calls = [
        lambda: put(broken),
        lambda: broken.list_items(T0),
        lambda: broken.read_audio("ev", 1, T0),
        lambda: broken.delete_one("ev", 1),
        broken.delete_all,
        lambda: broken.purge(T0),
        lambda: broken.manifest(T0),
    ]
    for call in calls:
        with pytest.raises(measurement.StoreUnavailable):
            call()


def test_a_sidecar_that_cannot_be_written_leaves_no_audio_behind(store):
    class SidecarFails(InMemoryObjectStore):
        def put(self, key, data, content_type="application/octet-stream"):
            if key.endswith(".json"):
                raise ConnectionError("minio went away between the two writes")
            super().put(key, data, content_type)

    failing = SidecarFails()
    with pytest.raises(measurement.StoreUnavailable):
        put(Recordings(failing))
    assert failing._objects == {}


# ------------------------------------------------------------------ the server keeps the 30 days


def test_the_daily_purge_runs_on_its_first_call_and_then_once_a_day(recordings, store):
    put(recordings, "ev", 1)
    put(recordings, "ofis", 1, now=T0 + timedelta(days=10))
    clock = {"now": T0 + timedelta(days=31)}
    purge = DailyPurge(lambda: recordings, clock=lambda: clock["now"])

    assert purge() == 1  # "at start": the first call never waits a day
    assert keys(store) == {"voice-measurement/ofis/01.wav", "voice-measurement/ofis/01.json"}

    # An hour short of a day later: there is something to remove, and it is not due.
    clock["now"] = T0 + timedelta(days=31, hours=23)
    store.delete("voice-measurement/ofis/01.wav")  # something a purge would remove
    assert purge() == 0
    assert keys(store) == {"voice-measurement/ofis/01.json"}

    clock["now"] = T0 + timedelta(days=32)
    assert purge() == 1
    assert keys(store) == set()
    assert purge.last_purge_at == T0 + timedelta(days=32)


def test_a_daily_purge_that_failed_is_tried_again_on_the_next_call(recordings, store):
    put(recordings, "ev", 1)
    target = {"recordings": Recordings(BrokenStore())}
    purge = DailyPurge(lambda: target["recordings"], clock=lambda: T0 + timedelta(days=31))
    with pytest.raises(measurement.StoreUnavailable):
        purge()
    assert purge.last_purge_at is None
    target["recordings"] = recordings
    assert purge() == 1
    assert keys(store) == set()


def test_the_interval_is_one_day():
    assert measurement.PURGE_INTERVAL_S == 86_400
