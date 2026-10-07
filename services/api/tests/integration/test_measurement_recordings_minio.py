"""The measurement recordings on the dev stack's MinIO, through ``S3ObjectStore.from_settings``
(team/plans/measure-recordings-api-adr.md): the enumerable layout holds on a store that has
no list call, and what the in-memory fake promised a real S3 keeps.

The test works under its OWN root, never ``voice-measurement``: a recording the owner made
against the dev stack must not be deleted by a test run, and the test removes every key it
wrote.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest

from app.config import Settings
from app.object_store import S3ObjectStore
from app.voice import stt_compare
from app.voice.measurement.service import Recordings, Refusal, slots
from tests.unit.test_measurement_recordings import T0, b64, wav

pytestmark = pytest.mark.integration


def test_save_list_read_replace_purge_delete_all_round_trip_on_minio(settings: Settings) -> None:
    store = S3ObjectStore.from_settings(settings)
    store.ensure_bucket()
    root = f"voice-measurement-it-{uuid.uuid4().hex}"
    recordings = Recordings(store, root=root)
    every_key = [
        f"{root}/{place}/{index:02d}.{ext}" for place, index in slots() for ext in ("wav", "json")
    ]
    try:
        assert recordings.list_items(T0) == []

        # save -> list
        first = wav(1.0, fill=11)
        item = recordings.save(
            "ev", 1, audio_wav_base64=b64(first), browser_transcript="hesap makinesini aç", now=T0
        )
        assert store.exists(f"{root}/ev/01.wav")
        assert store.exists(f"{root}/ev/01.json")
        assert item["file"] == "ev-01.wav"
        assert item["reference"] == stt_compare.OWNER_SENTENCES[0]
        assert recordings.list_items(T0) == [item]

        # read_audio: the bytes a real S3 hands back are the bytes sent
        assert recordings.read_audio("ev", 1, T0) == first

        # replace
        second = wav(2.0, fill=22)
        replaced = recordings.save("ev", 1, audio_wav_base64=b64(second), now=T0)
        assert recordings.read_audio("ev", 1, T0) == second
        assert replaced["sha256"] == hashlib.sha256(second).hexdigest()
        assert replaced["browser_transcript"] is None
        assert recordings.list_items(T0) == [replaced]

        # purge at day 31: the old one goes, the younger one in the other place stays
        recordings.save("ofis", 20, audio_wav_base64=b64(first), now=T0 + timedelta(days=10))
        day_31 = T0 + timedelta(days=31)
        assert recordings.purge(day_31) == 1
        assert not store.exists(f"{root}/ev/01.wav")
        assert not store.exists(f"{root}/ev/01.json")
        assert store.exists(f"{root}/ofis/20.wav")
        assert store.exists(f"{root}/ofis/20.json")
        assert recordings.purge(day_31) == 0
        with pytest.raises(Refusal) as refused:
            recordings.read_audio("ev", 1, day_31)
        assert refused.value.code == "not_found"
        assert [i["file"] for i in recordings.manifest(day_31)["items"]] == ["ofis-20.wav"]

        # delete_all: both places, and an orphan audio whose sidecar never arrived
        recordings.save("ev", 5, audio_wav_base64=b64(first), now=day_31)
        recordings.save("ev", 6, audio_wav_base64=b64(first), now=day_31)
        store.delete(f"{root}/ev/06.json")
        # a command sentence (21-30, local-tr-stt-measure) is a slot like any other
        command = recordings.save("ofis", 30, audio_wav_base64=b64(first), now=day_31)
        assert command["reference"] == stt_compare.OFFLINE_COMMAND_SENTENCES[9]
        assert store.exists(f"{root}/ofis/30.wav")
        assert recordings.delete_all() == 4
        assert [key for key in every_key if store.exists(key)] == []
        assert recordings.delete_all() == 0
    finally:
        for key in every_key:
            store.delete(key)
    assert [key for key in every_key if store.exists(key)] == []
