"""B13 req 284: the Cloud Core's half of ``packages/protocol/alarm-timing.json``.

One number, read rather than remembered. The cloud used to say an alarm could still be
worth ringing two hours late; the device said five minutes, cut down from two hours after a
07:30 alarm rang at 08:10 on the owner's own machine. Two halves of one decision, each with
its own memory of the answer - which is this repository's most repeated bug shape and the
reason ``packages/protocol/`` exists.

Nothing here restates a value. ``MAX_LATE_FIRE_S`` is what the contract says, and a change
to the contract changes both halves or fails ``test_contract_falsification``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from app.protocol_files import protocol_file

#: The run-time copy of ``packages/protocol/alarm-timing.json`` (app/protocol_files.py).
_CONTRACT_PATH: Final[Path] = protocol_file("alarm-timing.json")


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def max_late_fire_s() -> int:
    """Past this much lateness, ringing stops being a wake-up and becomes a startle."""
    return int(contract()["late_fire"]["max_late_seconds"])


def cloud_grace_s() -> int:
    """How long a cloud-issued arm tells the device to wait before ringing on its own.

    A DIFFERENT question from lateness. Grace keeps the two firing paths from both ringing;
    lateness is whether ringing at all still helps the owner. Collapsing them would make a
    long grace silently shorten the window in which an alarm may ring at all - which is why
    a test asserts grace stays well under the late horizon rather than trusting the next
    person to edit the contract.
    """
    return int(contract()["grace"]["cloud_sends_seconds"])


def device_default_grace_s() -> int:
    """What the device waits when nothing told it otherwise."""
    return int(contract()["grace"]["device_default_seconds"])


def grace_bounds() -> tuple[int, int]:
    grace = contract()["grace"]
    return int(grace["min_seconds"]), int(grace["max_seconds"])


__all__ = [
    "cloud_grace_s",
    "contract",
    "device_default_grace_s",
    "grace_bounds",
    "max_late_fire_s",
]
