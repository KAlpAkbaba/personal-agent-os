"""B47: ``packages/protocol/device-voice.json``, read by the Cloud Core half.

The device half is ``DeviceVoiceContractTests`` (C#). This module holds the cloud to the same
file AND reads the device's constants from its source, so a number or a name edited on one
side alone is red here - "contract halves must read each other" - not only in the suite of
the half that happened to change.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.devices import voice_contract
from app.devices.status import DeviceStatusRegistry, parse_status
from app.protocol_files import protocol_file

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = REPO_ROOT / "packages" / "protocol" / "device-voice.json"
DEVICE_SOURCE = (
    REPO_ROOT
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.Companion.Audio"
    / "Listening"
    / "DeviceVoiceContract.cs"
)
HEARTBEAT_CS = (
    REPO_ROOT
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.Agent.Core"
    / "Protocol"
    / "HeartbeatStatus.cs"
)
SCHEMA = REPO_ROOT / "packages" / "schemas" / "device-protocol.schema.json"
NOW = datetime(2026, 9, 16, 5, 0, tzinfo=UTC)


def _shared() -> dict:
    # No skip on absence: a missing contract must fail this guard, never pass it.
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _cs_const(name: str) -> str:
    match = re.search(
        rf"public const (?:string|int|bool) {name} = (\"[^\"]*\"|[^;]+);",
        DEVICE_SOURCE.read_text(encoding="utf-8"),
    )
    assert match, f"DeviceVoiceContract.cs declares no {name}"
    return match.group(1).strip('"')


def test_the_voice_heartbeat_keys_are_read_from_the_contract_not_restated() -> None:
    shared = _shared()
    assert protocol_file("device-voice.json").read_bytes() == CONTRACT.read_bytes()
    assert (
        voice_contract.heartbeat_field()
        == shared["heartbeat"]["field"]
        == _cs_const("HeartbeatField")
    )
    assert voice_contract.heartbeat_keys() == tuple(shared["heartbeat"]["keys"])
    normalised = voice_contract.normalise_voice({})
    assert normalised is not None
    assert tuple(normalised) == voice_contract.heartbeat_keys()
    # The device lists the same keys, in the same order, in its source.
    cs = DEVICE_SOURCE.read_text(encoding="utf-8")
    listed = re.search(r"HeartbeatKeys\s*=\s*\[(.*?)\];", cs, re.S)
    assert listed, "DeviceVoiceContract.cs has no HeartbeatKeys list"
    assert tuple(re.findall(r'"([a-z_]+)"', listed.group(1))) == voice_contract.heartbeat_keys()


def test_the_device_constants_are_the_contracts_numbers() -> None:
    shared = _shared()
    privacy = shared["privacy"]
    assert int(_cs_const("DefaultPreRollMs")) == privacy["default_preroll_ms"]
    assert int(_cs_const("MaxPreRollMs")) == privacy["max_preroll_ms"]
    assert int(_cs_const("MaxSegmentMs")) == privacy["max_segment_ms"]
    assert _cs_const("RemoteEnableAllowed") == str(privacy["remote_enable_allowed"]).lower()
    assert privacy["raw_audio_persisted"] is False
    assert privacy["silence_leaves_device"] is False
    assert privacy["default_preroll_ms"] <= privacy["max_preroll_ms"]
    listening = shared["listening"]
    assert int(_cs_const("FollowUpWindowMs")) == listening["follow_up_window_ms"]
    assert int(_cs_const("PushToTalkMinHoldMs")) == listening["push_to_talk_min_hold_ms"]
    assert _cs_const("WakeWordPhraseId") == listening["wake_word_phrase_id"]
    assert _cs_const("DefaultMode") == "ModeContinuous"
    assert listening["default_mode"] == "continuous" == _cs_const("ModeContinuous")
    assert _cs_const("LocalSnoozeField") == shared["local_snooze"]["heartbeat_field"]
    assert (_cs_const("PayloadSnoozeMinutes"), _cs_const("PayloadSnoozesLeft")) == tuple(
        shared["local_snooze"]["payload_keys"]
    )
    assert voice_contract.snooze_payload_keys() == tuple(shared["local_snooze"]["payload_keys"])


def test_the_enums_and_the_offline_table_are_the_contracts() -> None:
    shared = _shared()
    assert voice_contract.indicator_states() == tuple(shared["indicator_states"])
    assert voice_contract.service_states() == tuple(shared["service_states"])
    assert voice_contract.listening_modes() == tuple(shared["listening"]["modes"])
    rows = voice_contract.offline_commands()
    assert [r["id"] for r in rows] == [c["id"] for c in shared["offline_commands"]["commands"]]
    # Only turning listening OFF may act while the Cloud Core is reachable, and nothing
    # in the table reaches a file, an account or another machine.
    assert [r["id"] for r in rows if r["acts"] == "always"] == ["listening.off"]
    assert {r["id"].split(".")[0] for r in rows} <= {"alarm", "listening", "time"}
    cs = DEVICE_SOURCE.read_text(encoding="utf-8")
    for row in rows:
        assert f'"{row["id"]}"' in cs, f"the device does not name offline command {row['id']}"
    assert voice_contract.remote_enable_allowed() is False


def test_the_heartbeat_and_the_schema_name_the_same_fields() -> None:
    heartbeat_cs = HEARTBEAT_CS.read_text(encoding="utf-8")
    assert f'"{voice_contract.heartbeat_field()}"' in heartbeat_cs
    assert f'"{voice_contract.local_snooze_field()}"' in heartbeat_cs
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    status = schema["$defs"]["deviceStatus"]["properties"]
    voice = status[voice_contract.heartbeat_field()]
    assert tuple(voice["required"]) == voice_contract.heartbeat_keys()
    assert tuple(voice["properties"]["state"]["enum"]) == voice_contract.service_states()
    assert tuple(voice["properties"]["indicator"]["enum"]) == voice_contract.indicator_states()
    assert tuple(voice["properties"]["mode"]["enum"]) == voice_contract.listening_modes()
    snoozed = status[voice_contract.local_snooze_field()]
    assert snoozed["maxItems"] == voice_contract.local_snooze_max_entries()
    assert tuple(snoozed["items"]["required"]) == tuple(_shared()["local_snooze"]["entry_keys"])


def test_a_strange_voice_report_is_normalised_never_trusted_and_never_raises() -> None:
    assert voice_contract.normalise_voice(None) is None
    assert voice_contract.normalise_voice("running") is None
    odd = voice_contract.normalise_voice(
        {
            "state": "streaming",
            "indicator": "recording",
            "mode": "always",
            "listening": "yes",
            "mic_muted": "no",
            "cloud_connected": 1,
            "restarts": -3,
            "last_error": "x" * 500,
            "transcript": "saat kaç",
        }
    )
    assert odd == {
        "state": "unknown",
        "indicator": "unknown",
        "mode": "unknown",
        "listening": False,
        "mic_muted": None,
        "cloud_connected": False,
        "restarts": 0,
        "last_error": "x" * 128,
    }
    good = voice_contract.normalise_voice(
        {
            "state": "running",
            "indicator": "sending",
            "mode": "wake_word",
            "listening": True,
            "mic_muted": False,
            "cloud_connected": True,
            "restarts": 2,
            "last_error": "session:IOException",
        }
    )
    assert good is not None and good["indicator"] == "sending" and good["restarts"] == 2


def test_the_status_parser_carries_voice_and_local_snoozes_and_diffs_them() -> None:
    device = uuid.uuid4()
    until = NOW + timedelta(minutes=9)
    alarm_id = str(uuid.uuid4())
    raw = {
        "display_state": "on",
        "local_alarm_snoozed": [
            {"alarm_id": alarm_id, "until": until.isoformat()},
            {"alarm_id": "", "until": until.isoformat()},
            {"alarm_id": "no-until"},
            "garbage",
        ],
        "voice": {
            "state": "offline",
            "indicator": "listening",
            "mode": "continuous",
            "listening": True,
            "mic_muted": None,
            "cloud_connected": False,
            "restarts": 1,
            "last_error": "no_owner_token",
        },
    }
    status = parse_status(device, raw, now=NOW)
    assert status is not None
    assert status.local_alarm_snoozed == ((alarm_id, until),)
    assert status.voice is not None and status.voice["state"] == "offline"
    shown = status.as_dict()
    assert shown["voice"]["last_error"] == "no_owner_token"
    assert shown["local_alarm_snoozed"] == [
        {"alarm_id": alarm_id, "until": until.isoformat().replace("+00:00", "Z")}
    ]

    registry = DeviceStatusRegistry()
    first = registry.record(device, raw, now=NOW)
    again = registry.record(device, raw, now=NOW + timedelta(seconds=10))
    later = registry.record(device, {"display_state": "on"}, now=NOW + timedelta(seconds=20))
    assert first is not None and first.newly_snoozed_alarms == ((alarm_id, until),)
    assert again is not None and again.newly_snoozed_alarms == ()
    assert later is not None and later.newly_snoozed_alarms == ()
    assert later.status.voice is None

    bounded = parse_status(
        device,
        {
            "local_alarm_snoozed": [
                {"alarm_id": f"a{i}", "until": until.isoformat()} for i in range(100)
            ]
        },
        now=NOW,
    )
    assert bounded is not None
    assert len(bounded.local_alarm_snoozed) == voice_contract.local_snooze_max_entries()
