"""The writer of the two registry facts ``app.execution.wiring`` reads (ADR-0213 PR 1c).

``wiring._availability`` treats a device whose ``platform`` is ``cloud`` as the cloud worker
and a device labelled ``owner_chrome`` as the machine holding the owner's enrolled Chrome;
until this module nothing wrote either. Both are DERIVED, never invented:

* the alias ``bulut`` is added to a ``cloud`` device and to no other platform, so the owner's
  word "bulutta" resolves to the cloud worker;
* the label ``owner_chrome`` follows what the device advertises in its hello - the exact
  capability ``browser.profile.owner`` - and never the device's name.

``sync_registry_facts`` is the one function the broker's hello path calls, after
``broker.service.apply_hello``. It is idempotent and only ever ADDS: an alias or label the
owner set is kept. Nothing here edits ``app.broker``; it writes through
``broker.service.update_device_metadata``.
"""

from __future__ import annotations

from typing import Final, Protocol

from sqlalchemy.orm import Session

from app.broker import service as broker_service
from app.broker.models import Device
from app.devices.aliases import normalize

CLOUD_PLATFORM: Final = "cloud"
CLOUD_ALIAS: Final = "bulut"
OWNER_CHROME_LABEL: Final = "owner_chrome"
#: What an agent advertises when the owner's own Chrome is enrolled on it (ADR-0113: the
#: ``owner`` profile). An EXACT name: the family marker ``browser.chrome`` implies every
#: ``browser.*`` operation and must not imply this.
OWNER_PROFILE_CAPABILITY: Final = "browser.profile.owner"


class _Advertises(Protocol):
    platform: str
    capabilities: tuple[str, ...]


def owner_chrome_label(view: _Advertises) -> str | None:
    """``owner_chrome`` for a non-cloud device advertising the owner profile, else ``None``."""
    if view.platform == CLOUD_PLATFORM:
        return None
    return OWNER_CHROME_LABEL if OWNER_PROFILE_CAPABILITY in view.capabilities else None


class _DeviceRow:
    """A broker ``Device`` seen through the same two attributes a ``DeviceView`` has."""

    def __init__(self, device: Device) -> None:
        self.platform = device.platform
        self.capabilities = tuple(device.capabilities_json or [])


def _current(device: Device, key: str) -> list[str]:
    return [str(v) for v in ((device.metadata_json or {}).get(key) or [])]


def ensure_cloud_alias(db: Session, device: Device) -> bool:
    """Give a ``cloud`` device the alias ``bulut``. True when something was written."""
    if device.platform != CLOUD_PLATFORM:
        return False
    aliases = _current(device, "aliases")
    if any(normalize(a) == CLOUD_ALIAS for a in aliases):
        return False
    broker_service.update_device_metadata(db, device.id, aliases=[*aliases, CLOUD_ALIAS])
    return True


def ensure_owner_chrome_label(db: Session, device: Device) -> bool:
    """Add the derived ``owner_chrome`` label. True when something was written."""
    label = owner_chrome_label(_DeviceRow(device))
    labels = _current(device, "labels")
    if label is None or label in labels:
        return False
    broker_service.update_device_metadata(db, device.id, labels=[*labels, label])
    return True


def sync_registry_facts(db: Session, device: Device) -> bool:
    """The hello path's one call: write the alias and the label the device's facts imply."""
    alias = ensure_cloud_alias(db, device)
    label = ensure_owner_chrome_label(db, device)
    return alias or label


__all__ = [
    "CLOUD_ALIAS",
    "OWNER_CHROME_LABEL",
    "OWNER_PROFILE_CAPABILITY",
    "ensure_cloud_alias",
    "ensure_owner_chrome_label",
    "owner_chrome_label",
    "sync_registry_facts",
]
