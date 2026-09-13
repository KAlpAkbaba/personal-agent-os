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
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

CAPABILITY: Final[str] = "desktop.notify"
ACTION_EVENT: Final[str] = "desktop.notify.action"

_CONTRACT_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3].parent / "packages" / "protocol" / "desktop-notify.json"
)

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
            if not _ACTION_ID.match(action_id):
                raise ToastRefused(f"action id {action_id!r} is not [a-z0-9_]+")
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


__all__ = [
    "ACTION_EVENT",
    "CAPABILITY",
    "ToastRefused",
    "build",
    "contract",
    "refusal_reason",
    "was_shown",
]
