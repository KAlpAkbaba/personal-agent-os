"""B11 req 369/370: the Cloud Core's half of ``desktop.notify``.

Both halves read ``packages/protocol/desktop-notify.json`` rather than restating it. The four
capabilities that were not written as a contract first (file.search roots, the app manifest,
the native manifest, the device protocol schema) each shipped with both suites green and
neither half able to talk to the other; this one is built the other way round.

What this module does NOT do is decide whether a toast was delivered. It builds the payload
and reads the device's answer. ``shown: false`` is a real answer - no interactive session,
notifications turned off in Windows, no shell - and the ladder steps down rather than
recording a delivery that did not happen.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from app.protocol_files import protocol_file

CAPABILITY: Final[str] = "desktop.notify"
ACTION_EVENT: Final[str] = "desktop.notify.action"

#: The run-time copy of ``packages/protocol/desktop-notify.json`` (app/protocol_files.py).
_CONTRACT_PATH: Final[Path] = protocol_file("desktop-notify.json")

_ACTION_ID = re.compile(r"^[a-z0-9_]+$")


class ToastRefused(ValueError):
    """The payload does not satisfy the shared contract. Raised HERE rather than discovered
    on the device: a refusal that costs a round trip teaches nobody anything."""


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def _limit(field: str) -> int:
    """The length limit for a request field, READ from the contract.

    Restating "64" here would be the whole failure mode this file exists to avoid: the
    device enforces the contract's number and the Cloud Core would enforce its own memory
    of it.
    """
    return int(contract()["request"][field]["max_chars"])


def _action_limit() -> int:
    return int(contract()["request"]["actions"]["max_items"])


def build(
    *,
    notification_id: uuid.UUID | str,
    title: str,
    body: str,
    priority: str = "normal",
    group_key: str = "",
    actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """The payload for a ``desktop.notify`` device command, validated against the contract."""
    identifier = str(notification_id)
    try:
        uuid.UUID(identifier)
    except ValueError as exc:
        raise ToastRefused("notification_id must be a uuid") from exc

    if not title.strip():
        raise ToastRefused("a toast with no title is a toast the owner cannot place")
    if len(title) > _limit("title"):
        # Refuse rather than truncate: what the owner reads is the Cloud Core's decision,
        # and a silently shortened sentence is a different sentence.
        raise ToastRefused(f"title is longer than {_limit('title')} characters")
    if not body.strip():
        raise ToastRefused("a toast with no body says nothing")
    if len(body) > _limit("body"):
        raise ToastRefused(f"body is longer than {_limit('body')} characters")

    allowed = set(contract()["request"]["priority"]["values"])
    if priority not in allowed:
        raise ToastRefused(f"priority must be one of {sorted(allowed)}")

    payload: dict[str, Any] = {
        "notification_id": identifier,
        "title": title,
        "body": body,
        "priority": priority,
    }
    if group_key:
        if len(group_key) > int(contract()["request"]["group_key"]["max_chars"]):
            raise ToastRefused("group_key is too long")
        payload["group_key"] = group_key

    if actions:
        if len(actions) > _action_limit():
            raise ToastRefused(
                f"Windows shows at most {_action_limit()} toast buttons; asking for more "
                "drops the extras silently, so this refuses instead"
            )
        built: list[dict[str, str]] = []
        for action in actions:
            action_id = str(action.get("id", ""))
            label = str(action.get("label", ""))
            # fullmatch, not match: `$` also matches before a trailing newline, and the device
            # refuses "open\n" (B11-toast review).
            if not _ACTION_ID.fullmatch(action_id):
                raise ToastRefused(f"action id {action_id!r} is not [a-z0-9_]+")
            if len(action_id) > _action_id_limit():
                raise ToastRefused(f"action id {action_id!r} is longer than the device accepts")
            if not label.strip():
                raise ToastRefused(f"action {action_id!r} has no label")
            if len(label) > int(contract()["request"]["actions"]["item"]["label"]["max_chars"]):
                raise ToastRefused(f"action {action_id!r} label is too long for a button")
            built.append({"id": action_id, "label": label})
        payload["actions"] = built

    return payload


def was_shown(result: Any) -> bool:
    """Whether the device actually raised a toast.

    Anything that is not an explicit ``shown: true`` is not a delivery. A missing field, a
    result that is not a mapping, a device that answered something else - none of those are
    evidence that the owner saw anything, and treating them as one is exactly how a queue
    starts being called a delivery.
    """
    if not isinstance(result, dict):
        return False
    return result.get("shown") is True


def refusal_reason(result: Any) -> str | None:
    if not isinstance(result, dict) or result.get("shown") is True:
        return None
    reason = result.get("reason")
    return str(reason) if reason else "unknown"


def surface(result: Any) -> str | None:
    """B11-toast: which surface carried a shown notice (``toast`` or ``balloon``), when the
    device said so and said something the contract names. Never a guess."""
    if not isinstance(result, dict) or result.get("shown") is not True:
        return None
    value = result.get("surface")
    allowed = contract()["response"]["surface"]["values"]
    return value if isinstance(value, str) and value in allowed else None


# ------------------------------------------------ the press, on its way back (B11-toast)


@dataclass(frozen=True, slots=True)
class ActionPress:
    """One toast button the owner pressed, as the device reported it. Data only: an action id
    from this Cloud Core's own vocabulary, the notification it belongs to, and a time."""

    notification_id: uuid.UUID
    action_id: str
    pressed_at: datetime

    def key(self) -> tuple[str, str, str]:
        return (str(self.notification_id), self.action_id, _iso(self.pressed_at))


def action_field() -> str:
    """The heartbeat ``status`` key the presses ride in - READ from the contract."""
    return str(contract()["action_event"]["transport"]["field"])


def action_max_entries() -> int:
    return int(contract()["action_event"]["transport"]["max_entries"])


def action_transmissions() -> int:
    return int(contract()["action_event"]["transport"]["transmissions"])


def _action_id_limit() -> int:
    return int(contract()["request"]["actions"]["item"]["id"]["max_chars"])


def _iso(moment: datetime) -> str:
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_action_presses(raw: Any) -> tuple[ActionPress, ...]:
    """The ``notify_actions`` list of a heartbeat status, strictly.

    Never raises: the heartbeat path must not fail on a device's report. An entry that is not
    exactly a uuid, an action id of the contract's form and an ISO time is dropped - not
    repaired - and at most the contract's ``max_entries`` are read.
    """
    if not isinstance(raw, list | tuple):
        return ()
    out: list[ActionPress] = []
    for item in raw[: action_max_entries()]:
        if not isinstance(item, dict):
            continue
        notification_id = item.get("notification_id")
        action_id = item.get("action_id")
        pressed_at = item.get("pressed_at")
        if not (
            isinstance(notification_id, str)
            and isinstance(action_id, str)
            and isinstance(pressed_at, str)
        ):
            continue
        if not _ACTION_ID.fullmatch(action_id) or len(action_id) > _action_id_limit():
            continue
        try:
            parsed_id = uuid.UUID(notification_id)
            moment = datetime.fromisoformat(pressed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        out.append(ActionPress(parsed_id, action_id, moment.astimezone(UTC)))
    return tuple(out)


__all__ = [
    "ACTION_EVENT",
    "CAPABILITY",
    "ActionPress",
    "ToastRefused",
    "action_field",
    "action_max_entries",
    "action_transmissions",
    "build",
    "contract",
    "parse_action_presses",
    "refusal_reason",
    "surface",
    "was_shown",
]
