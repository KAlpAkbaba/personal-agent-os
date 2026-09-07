"""The version model (docs/M18_4_SELF_EVOLUTION_SPEC.md §2): what is running, by name.

A deployed git sha is not enough to know what a Cloud Core speaks - the 2026-09-03 422 and
every "merged but not released" incident since were contract mismatches - so the model
names the sha AND every contract version this process serves, and says where the sha came
from (``env`` when the release script exported it, ``unknown`` otherwise). Nothing here is
guessed: an absent value is reported as absent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import __version__
from app.config import Settings

COMPONENT_CLOUD_CORE = "cloud-core"
VERSION_MODEL_VERSION = 1

#: The instant this process started - the honest "since when" for the running version.
STARTED_AT: datetime = datetime.now(UTC)


def contract_versions() -> dict[str, int]:
    """Every versioned contract this process serves, read from the modules that own them
    (a second copy of a number is a drift waiting to happen)."""
    from app.actions.receipt import ACTION_CONTRACT_VERSION
    from app.ambient.routes import AMBIENT_VERSION
    from app.uistate.contract import CONTRACT_VERSION as UI_STATE_CONTRACT_VERSION
    from app.voice.qualification.service import QUALIFICATION_VERSION

    return {
        "action": int(ACTION_CONTRACT_VERSION),
        "ui_state": int(UI_STATE_CONTRACT_VERSION),
        "ambient": int(AMBIENT_VERSION),
        "voice_qualification": int(QUALIFICATION_VERSION),
    }


def release_model(settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    """The running Cloud Core, as facts: component, version (and its source), app version,
    contracts, last-known-good when the host exported one, start instant and uptime."""
    moment = now or datetime.now(UTC)
    release = (settings.release or "").strip()
    lkg = (settings.last_known_good or "").strip()
    return {
        "version_model": VERSION_MODEL_VERSION,
        "component": COMPONENT_CLOUD_CORE,
        "version": release or "unknown",
        "version_source": "env" if release else "unknown",
        "app_version": __version__,
        "contracts": contract_versions(),
        "last_known_good": lkg or None,
        "last_known_good_source": "env" if lkg else "unknown",
        "environment": settings.environment,
        "started_at": STARTED_AT.isoformat().replace("+00:00", "Z"),
        "uptime_s": max(0.0, (moment - STARTED_AT).total_seconds()),
    }


__all__ = [
    "COMPONENT_CLOUD_CORE",
    "STARTED_AT",
    "VERSION_MODEL_VERSION",
    "contract_versions",
    "release_model",
]
