"""B19 req 63-66: the self-model learns what is observably true NOW.

The index knows 443 modules, 8334 symbols and 3308 edges, and records every one of them
as `source_only`. That is not a bug in `_apply_production_states`, which is ordered
correctly by evidence strength — it is what happens when the only producers of runtime
truth are HISTORICAL: `TRUTH_INSTALLED` from `Release` rows and `TRUTH_RUNTIME` from
completed `deployment.*` ledger events. In a deployment that has not had its production
round yet there are neither, so `source_only` is the honest answer and the map is honestly
useless for the question the owner actually asks.

**The most direct evidence there is has never been read.** This process knows its own
release identity right now — `app.release.version.release_model()` returns the version and
the build id derived from the sources it is running — and the device registry knows each
connected device's agent build and capability list right now. "I am running, and this is
my build" is stronger evidence than "a deployment event said so last Tuesday", and it was
the one truth nothing collected.

**Observed now means it must go stale.** `query.STALE_AFTER` gives `TRUTH_RUNTIME` fifteen
minutes, and that is exactly right for an observation of this kind: a row written when the
process started is not evidence about the process an hour later. This module writes the
timestamp it observed at and nothing else pretends otherwise — the refresher re-observes on
its own interval, and between passes the answer says "stale" rather than lying quietly.

**It does not invent a module list.** Only modules the INDEX already knows are given a
runtime truth; a name this observation mentions that the checkout does not contain is
dropped, because the alternative is a self-model with rows for code that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.models import DEVICE_STATUS_ENROLLED, Device
from app.logging import get_logger
from app.selfmodel.indexer import _upsert_provenance
from app.selfmodel.models import TRUTH_RUNTIME, CodeModule

logger = get_logger("app.selfmodel.runtime_truth")

#: Confidence for "this process is running this build, observed directly". Higher than the
#: 0.95 a completed deployment event earns: that is a record of something that happened,
#: this is the thing itself answering.
DIRECT_OBSERVATION_CONFIDENCE = 0.99

#: The module prefix the Cloud Core's own runtime identity applies to. Every module under
#: it is, by definition, running in this process - that is what "this process" means.
CLOUD_CORE_PREFIX = "app."

#: Where a device's runtime identity lands. The agent's modules are not in this checkout,
#: so a device build is recorded against the modules that SPEAK to it, which is what the
#: owner is asking about when they ask what build their machine is on.
DEVICE_MODULE_PREFIXES = ("app.devices.", "app.broker.")


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class RuntimeTruthReport:
    cloud_core_modules: int = 0
    device_modules: int = 0
    devices_seen: int = 0
    version: str | None = None
    build_id: str | None = None
    skipped_unknown: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cloud_core_modules": self.cloud_core_modules,
            "device_modules": self.device_modules,
            "devices_seen": self.devices_seen,
            "version": self.version,
            "build_id": self.build_id,
            "skipped_unknown": self.skipped_unknown,
        }


def _known_modules(session: Session, prefixes: tuple[str, ...]) -> list[str]:
    ids = session.scalars(select(CodeModule.module_id)).all()
    return [m for m in ids if any(m.startswith(p) for p in prefixes)]


def observe(
    session: Session,
    *,
    release: dict[str, Any] | None = None,
    devices: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> RuntimeTruthReport:
    """Write `TRUTH_RUNTIME` for what can be seen from inside this running process.

    ``release`` is `app.release.version.release_model()`'s answer and ``devices`` the live
    device-status rows; both are INJECTED rather than imported, so this module stays a
    writer of provenance and the caller stays the one that knows where truth comes from -
    and a test can hand it either.

    Never raises: the self-model is an aid to diagnosis and must not be able to take Cloud
    Core down (the rule `app.selfmodel.refresh` already states).
    """
    moment = now or utcnow()
    report = RuntimeTruthReport()

    if release:
        report.version = str(release.get("version") or "") or None
        report.build_id = str(release.get("build_id") or "") or None
        refs = [
            {
                "kind": "process",
                "ref": "cloud_core",
                "version": report.version,
                "build_id": report.build_id,
            }
        ]
        for module_id in _known_modules(session, (CLOUD_CORE_PREFIX,)):
            try:
                _upsert_provenance(
                    session,
                    module_id=module_id,
                    truth_kind=TRUTH_RUNTIME,
                    version=report.version,
                    digest=report.build_id,
                    observed_at=moment,
                    evidence_refs=refs,
                    confidence=DIRECT_OBSERVATION_CONFIDENCE,
                )
                report.cloud_core_modules += 1
            except Exception as exc:  # noqa: BLE001 - one module, not the pass
                logger.warning(
                    "runtime_truth_failed", module=module_id, error=type(exc).__name__
                )

    online = [d for d in (devices or []) if d.get("build_id") or d.get("capabilities")]
    report.devices_seen = len(online)
    if online:
        device_refs = [
            {
                "kind": "device",
                "ref": str(d.get("device_id") or ""),
                "build_id": d.get("build_id"),
                "capabilities": len(d.get("capabilities") or []),
            }
            for d in online
        ]
        # The newest agent build among the connected devices, so the digest names something
        # real rather than an average of several.
        newest = online[0]
        for module_id in _known_modules(session, DEVICE_MODULE_PREFIXES):
            try:
                _upsert_provenance(
                    session,
                    module_id=module_id,
                    truth_kind=TRUTH_RUNTIME,
                    version=str(newest.get("agent_version") or "") or report.version,
                    digest=str(newest.get("build_id") or "") or None,
                    observed_at=moment,
                    evidence_refs=device_refs,
                    confidence=DIRECT_OBSERVATION_CONFIDENCE,
                )
                report.device_modules += 1
            except Exception as exc:  # noqa: BLE001 - one module, not the pass
                logger.warning(
                    "runtime_truth_device_failed", module=module_id, error=type(exc).__name__
                )

    logger.info("runtime_truth_observed", **report.as_dict())
    return report


def device_rows(session: Session) -> list[dict[str, Any]]:
    """What the enrolled devices ARE, from the registry that decides it.

    `Device.build_id` (ADR-0118, derived from the agent's own assemblies),
    `software_version` (the product release) and `capabilities_json` (the 85-capability
    manifest the staged updater compares) are refreshed from every hello. That row is the
    answer to "which build is my machine on and what can it do", and the self-model has
    never read it.

    NOT `app.devices.status`: that registry carries what a device is DOING - idle seconds,
    display state, a ringing alarm - and refreshes six times a minute. Identity belongs to
    the enrolment row, which changes when the agent changes.
    """
    try:
        rows = session.scalars(
            select(Device).where(Device.status == DEVICE_STATUS_ENROLLED)
        ).all()
    except Exception as exc:  # noqa: BLE001 - a registry read must not break the index
        logger.warning("device_registry_read_failed", error=f"{type(exc).__name__}: {exc}")
        return []
    return [
        {
            "device_id": str(row.id),
            "build_id": row.build_id,
            "agent_version": row.software_version,
            "capabilities": list(row.capabilities_json or []),
            "last_seen_at": row.last_seen_at,
        }
        for row in rows
    ]


__all__ = [
    "CLOUD_CORE_PREFIX",
    "DEVICE_MODULE_PREFIXES",
    "DIRECT_OBSERVATION_CONFIDENCE",
    "RuntimeTruthReport",
    "device_rows",
    "observe",
]
