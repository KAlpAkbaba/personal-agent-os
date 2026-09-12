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
from app.release.build import build_identity

COMPONENT_CLOUD_CORE = "cloud-core"
#: 2 since 2026-09-12 (B01 req 21/22): the model gained ``build_id``. The number exists so a
#: reader knows which shape it got; a reader that predates 2 simply does not look for the
#: field. Nothing gates on this value - the release gates on the CONTRACT versions.
VERSION_MODEL_VERSION = 2

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
    build identity, contracts, last-known-good when the host exported one, start and uptime.

    ``build_id`` (B01 req 21/22) is the canonical answer to "which build is this". ``version``
    is the commit the host EXPORTED and reads ``unknown`` whenever nobody exported one;
    ``app_version`` is a product number that has been ``0.1.0`` for every release production
    has ever had. Neither can tell two builds apart on its own. ``build_id`` is derived from
    this build's own sources - the same rule the device uses for its own (ADR-0118) - so the
    two halves of one system answer the question the same way.
    """
    moment = now or datetime.now(UTC)
    release = (settings.release or "").strip()
    lkg = (settings.last_known_good or "").strip()
    return {
        "version_model": VERSION_MODEL_VERSION,
        "component": COMPONENT_CLOUD_CORE,
        "version": release or "unknown",
        "version_source": "env" if release else "unknown",
        "app_version": __version__,
        "build_id": build_identity(),
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
