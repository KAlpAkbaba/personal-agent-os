"""When a device refuses OUR payload, file the defect nobody was going to file.

The Evolution Supervisor already turns incidents and capability gaps into opportunities,
and it has been running on production for days. It had nothing to read: ``ingest_incident``
was reachable only from ``POST /v1/selfhealing/incidents``, and nothing inside the product
ever called it. Every defect still arrived the same way — the owner noticed and said so.

Three of them did, on 2026-09-09 alone, and all three left the same evidence behind:

    window.activate  validation_error  'Not Defteri' is not a window id  (x8, ADR-0100)
    file.search      validation_error  payload.roots must be absolute paths
    desktop.play_audio security_scope_error  audio may only be fetched from ...

A device error class says WHOSE fault it was, and two of them say ours. ``validation_error``
means the device could not even parse what the server sent; ``security_scope_error`` means
the server built a request outside the scope it was granted. Neither is weather, a closed
window or a slow machine — the payload was wrong when it left here, and it will be wrong
again next time. That is the definition of a defect worth a work item.

What is deliberately NOT here: ``ui_target_not_found`` and ``ui_state_changed`` (the owner
closed a window; retryable and true), refusals the device is right to make, and anything
the device marks retryable. Filing those would drown the real ones.

The sink is registered by ``app.main.create_app``; unregistered it is a no-op, so the broker
never depends on the self-healing runtime existing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Final

from app.logging import get_logger
from app.selfhealing.monitoring import IncidentDraft

logger = get_logger("app.selfhealing.defects")

#: The device's own vocabulary for "the request you sent me was wrong". Both are
#: non-retryable by construction on the companion side: retrying an unparseable payload
#: produces the same unparseable payload.
SERVER_FAULT_ERROR_CLASSES: Final[frozenset[str]] = frozenset(
    {"validation_error", "security_scope_error"}
)

#: The component an incident from this path belongs to. The device is not at fault; the
#: cloud core built the payload.
DEFECT_COMPONENT: Final = "cloud-core"

#: Everything a device message quotes back at us is the OFFENDING VALUE, and it differs
#: every time: "'Not Defteri' is not a window id", "'Excel' is not a window id". The rule
#: that was broken is the part in between, and that is what a fingerprint must be made of —
#: otherwise every bad value opens its own incident and the backlog says nothing.
_QUOTED: Final = re.compile(r"'[^']*'")
_DOUBLE_QUOTED: Final = re.compile(r'"[^"]*"')
_NUMBERS: Final = re.compile(r"\b\d+\b")
_WINDOW_IDS: Final = re.compile(r"\bw-\d+-\d+\b")
_PATHS: Final = re.compile(r"[A-Za-z]:\\[^\s,;]+")
_URLS: Final = re.compile(r"https?://[^\s,;]+")

#: A failing_check must stay inside the column that holds it.
MAX_FAILING_CHECK: Final = 200


def normalise_message(message: str) -> str:
    """The RULE a message states, with the offending values taken out.

    "'Not Defteri' is not a window id (expected w-<hwnd>-<tick>)" and
    "'Excel' is not a window id (expected w-<hwnd>-<tick>)" are one defect, not two.
    """
    text = _URLS.sub("<url>", message or "")
    text = _PATHS.sub("<path>", text)
    text = _WINDOW_IDS.sub("<window_id>", text)
    text = _QUOTED.sub("<value>", text)
    text = _DOUBLE_QUOTED.sub("<value>", text)
    text = _NUMBERS.sub("<n>", text)
    return " ".join(text.split())


def is_server_fault(error_class: str | None) -> bool:
    return bool(error_class) and str(error_class) in SERVER_FAULT_ERROR_CLASSES


def device_defect_draft(
    *,
    capability: str,
    error_class: str | None,
    error_message: str | None,
    payload: dict[str, Any] | None = None,
    command_id: str | None = None,
    device_id: str | None = None,
) -> IncidentDraft | None:
    """The incident this failure deserves, or ``None`` when it is not our fault."""
    if not is_server_fault(error_class):
        return None
    rule = normalise_message(error_message or "")
    failing_check = f"{capability}: {rule}"[:MAX_FAILING_CHECK]
    evidence: dict[str, Any] = {
        "source": "device_command",
        "capability": capability,
        "device_message": (error_message or "")[:2000],
    }
    # The payload IS the defect — it is what the server got wrong — so it is the first
    # thing whoever picks this up needs to see.
    if payload is not None:
        evidence["payload"] = payload
    if command_id:
        evidence["command_id"] = command_id
    if device_id:
        evidence["device_id"] = device_id
    return IncidentDraft(
        component=DEFECT_COMPONENT,
        error_class=str(error_class),
        failing_check=failing_check,
        # Not "critical": the device refused it, so nothing wrong happened ON the machine.
        # What is broken is a request this server keeps building wrongly.
        severity="major",
        evidence=evidence,
    )


_sink: Callable[[IncidentDraft], Any] | None = None


def register_defect_sink(sink: Callable[[IncidentDraft], Any] | None) -> None:
    """Wire (or unwire) where defects are filed. ``app.main.create_app`` owns this."""
    global _sink
    _sink = sink


def report_device_defect(
    *,
    capability: str,
    error_class: str | None,
    error_message: str | None,
    payload: dict[str, Any] | None = None,
    command_id: str | None = None,
    device_id: str | None = None,
) -> bool:
    """File it if it is ours. Returns whether anything was filed.

    Never raises: a command's acknowledgement is the device's business and must not depend
    on the backlog being writable.
    """
    draft = device_defect_draft(
        capability=capability,
        error_class=error_class,
        error_message=error_message,
        payload=payload,
        command_id=command_id,
        device_id=device_id,
    )
    if draft is None or _sink is None:
        return False
    try:
        _sink(draft)
    except Exception:  # noqa: BLE001 - filing a defect must never break the ack path
        logger.warning("device_defect_not_filed", capability=capability)
        return False
    logger.info(
        "device_defect_filed",
        capability=capability,
        error_class=str(error_class),
        failing_check=draft.failing_check,
    )
    return True


__all__ = [
    "DEFECT_COMPONENT",
    "SERVER_FAULT_ERROR_CLASSES",
    "device_defect_draft",
    "is_server_fault",
    "normalise_message",
    "register_defect_sink",
    "report_device_defect",
]
