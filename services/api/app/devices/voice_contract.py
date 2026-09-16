"""B47: the Cloud Core's half of ``packages/protocol/device-voice.json``.

The owner's Windows device listens on its own (owner decision 2026-09-16, ADR-0154 accepted
with continuous listening). The device decides what its microphone may send; the cloud sees
two things of it, both on the heartbeat's ``status`` object:

- ``voice`` - the device voice service's compact health (rows 250-252): states, flags, a
  counter and an error class. Normalised here onto the contract's closed key set and enums,
  so a strange value reads as "unknown" rather than travelling further.
- ``local_alarm_snoozed`` - alarms the device snoozed while this cloud was unreachable
  (B13 requirement 259's local trigger), each with the instant the device will ring again.

Nothing here restates a value: the key sets and enums are READ from the contract (the
run-time copy in ``app/protocol_bundle``), and ``tests/unit/test_device_voice_contract.py``
holds this module to the file while the device's ``DeviceVoiceContractTests`` holds the
other half to the same file.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from app.protocol_files import protocol_file

#: The run-time copy of ``packages/protocol/device-voice.json`` (app/protocol_files.py).
_CONTRACT_PATH: Final[Path] = protocol_file("device-voice.json")

#: What an unreadable ``voice`` value becomes: known to be unknown, never a guess.
UNKNOWN: Final = "unknown"


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def heartbeat_field() -> str:
    return str(contract()["heartbeat"]["field"])


def heartbeat_keys() -> tuple[str, ...]:
    return tuple(contract()["heartbeat"]["keys"])


def indicator_states() -> tuple[str, ...]:
    return tuple(contract()["indicator_states"])


def service_states() -> tuple[str, ...]:
    return tuple(contract()["service_states"])


def listening_modes() -> tuple[str, ...]:
    return tuple(contract()["listening"]["modes"])


def local_snooze_field() -> str:
    return str(contract()["local_snooze"]["heartbeat_field"])


def local_snooze_max_entries() -> int:
    return int(contract()["local_snooze"]["max_entries"])


def snooze_payload_keys() -> tuple[str, str]:
    minutes, left = contract()["local_snooze"]["payload_keys"]
    return str(minutes), str(left)


def offline_commands() -> tuple[dict[str, str], ...]:
    return tuple(
        {"id": row["id"], "requires": row["requires"], "acts": row["acts"]}
        for row in contract()["offline_commands"]["commands"]
    )


def remote_enable_allowed() -> bool:
    return bool(contract()["privacy"]["remote_enable_allowed"])


def _enum(value: Any, allowed: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in allowed else UNKNOWN


def normalise_voice(raw: Any) -> dict[str, Any] | None:
    """The heartbeat's ``voice`` object on the contract's closed key set, or ``None``.

    Never raises: a device that sends a strange value must stay connected (the module
    docstring of ``app.devices.status``).
    """
    if not isinstance(raw, dict):
        return None
    restarts = raw.get("restarts")
    last_error = raw.get("last_error")
    muted = raw.get("mic_muted")
    normalised = {
        "state": _enum(raw.get("state"), service_states()),
        "indicator": _enum(raw.get("indicator"), indicator_states()),
        "mode": _enum(raw.get("mode"), listening_modes()),
        "listening": raw.get("listening") is True,
        "mic_muted": muted if isinstance(muted, bool) else None,
        "cloud_connected": raw.get("cloud_connected") is True,
        "restarts": (
            restarts
            if isinstance(restarts, int) and not isinstance(restarts, bool) and restarts >= 0
            else 0
        ),
        "last_error": last_error[:128] if isinstance(last_error, str) else None,
    }
    # The key set IS the contract's (tests/unit/test_device_voice_contract.py holds it there).
    return normalised


__all__ = [
    "UNKNOWN",
    "contract",
    "heartbeat_field",
    "heartbeat_keys",
    "indicator_states",
    "listening_modes",
    "local_snooze_field",
    "local_snooze_max_entries",
    "normalise_voice",
    "offline_commands",
    "remote_enable_allowed",
    "service_states",
    "snooze_payload_keys",
]
