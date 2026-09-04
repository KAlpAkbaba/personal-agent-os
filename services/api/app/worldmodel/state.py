"""World Model foundation (overnight plan Phase 5).

A structured, computed-on-demand snapshot of the system: owner, devices,
services/capabilities, running tasks, active goals, deployment state, recent
events, known incidents, and the environment/runtime this process is actually
executing in.

Every fact is labelled with WHICH kind of truth backs it — this is the whole
point of the module, not an afterthought:

- ``SOURCE_TRUTH``    what the repository/configuration DECLARES (a config
  default, a registered capability name). May not reflect anything running.
- ``INSTALLED_TRUTH`` what is actually INSTALLED in this process's
  environment (e.g. a package's real installed version via import metadata).
  More than declared, still not proof anything is running correctly.
- ``RUNTIME_TRUTH``   what was actually OBSERVED just now (a live probe
  answered, a device is connected, the interpreter that is really executing
  this code). Only ever set from a real observation taken during assembly.
- ``EVIDENCE_TRUTH``  what a DURABLE RECORD (a database row, a ledger event)
  PROVES happened or exists — independent of whether it is still true this
  second. A device that enrolled is evidence it enrolled, not proof it is
  online right now.

Assembly never upgrades a fact to a stronger truth kind than its source
supports (task instructions: "never infer runtime truth from repository
source"). It is read-only (no writes, ever) and degrades gracefully: a
section that fails to gather becomes an ``Uncertainty``, never an exception
that blanks the rest of the snapshot — a world model that pretends to know
everything is worse than one that is honest about its gaps.
"""

from __future__ import annotations

import importlib.metadata
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.logging import get_logger

logger = get_logger("app.worldmodel.state")

WORLD_VERSION = 1

#: package -> distribution name, for the handful of core dependencies worth
#: reporting INSTALLED_TRUTH for (PROJECT_CONSTITUTION.md / ARCHITECTURE.md
#: §2 technology choices). Not exhaustive on purpose — this is a foundation.
_TRACKED_PACKAGES: tuple[str, ...] = ("fastapi", "sqlalchemy", "temporalio", "redis", "alembic")


class TruthKind(StrEnum):
    SOURCE = "source_truth"
    INSTALLED = "installed_truth"
    RUNTIME = "runtime_truth"
    EVIDENCE = "evidence_truth"


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Fact:
    key: str
    category: str
    value: Any
    truth_kind: TruthKind
    observed_at: datetime
    confidence: float = 1.0
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    #: True when ``observed_at`` is old enough that the value may no longer
    #: hold (assembly itself never sets this for a fact it just observed;
    #: reserved for a caller that re-serves a cached snapshot).
    stale: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category,
            "value": self.value,
            "truth_kind": self.truth_kind.value,
            "observed_at": _iso(self.observed_at),
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "stale": self.stale,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Uncertainty:
    category: str
    subject: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "subject": self.subject,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


@dataclass(slots=True)
class WorldSnapshot:
    generated_at: datetime
    facts: list[Fact]
    uncertainties: list[Uncertainty]

    def facts_by_kind(self, kind: TruthKind) -> list[Fact]:
        return [f for f in self.facts if f.truth_kind == kind]

    def as_dict(self) -> dict[str, Any]:
        return {
            "world_version": WORLD_VERSION,
            "generated_at": _iso(self.generated_at),
            "facts": [f.as_dict() for f in self.facts],
            "uncertainties": [u.as_dict() for u in self.uncertainties],
        }


class _Collector:
    """Accumulates facts/uncertainties across independently-failing sections
    (module docstring: one section's failure must not blank the snapshot)."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.facts: list[Fact] = []
        self.uncertainties: list[Uncertainty] = []

    def fact(
        self,
        key: str,
        category: str,
        value: Any,
        truth_kind: TruthKind,
        *,
        observed_at: datetime | None = None,
        confidence: float = 1.0,
        evidence_refs: list[dict[str, Any]] | None = None,
        note: str = "",
    ) -> None:
        self.facts.append(
            Fact(
                key=key,
                category=category,
                value=value,
                truth_kind=truth_kind,
                observed_at=observed_at or self.now,
                confidence=confidence,
                evidence_refs=list(evidence_refs or []),
                note=note,
            )
        )

    def uncertain(self, category: str, subject: str, reason: str, **detail: Any) -> None:
        self.uncertainties.append(
            Uncertainty(category=category, subject=subject, reason=reason, detail=detail)
        )

    def section(self, category: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - one source down must not blank the rest
            logger.warning(
                "worldmodel_section_failed", category=category, error=f"{type(exc).__name__}: {exc}"
            )
            self.uncertain(
                category, category, "section_unavailable", error_class=type(exc).__name__
            )


# --------------------------------------------------------------------- sections


def _collect_owner(c: _Collector, session: Session) -> None:
    from app.identity.models import OwnerSession

    rows = list(session.execute(select(OwnerSession).limit(1)).scalars().all())
    if rows:
        c.fact(
            "owner.enrolled",
            "owner",
            True,
            TruthKind.EVIDENCE,
            evidence_refs=[{"kind": "owner_session", "ref": str(rows[0].id)}],
        )
    else:
        c.fact("owner.enrolled", "owner", False, TruthKind.EVIDENCE)
        c.uncertain("owner", "owner.enrolled", "no_owner_session_recorded")


def _collect_devices(c: _Collector, session: Session, broker_runtime: Any | None) -> None:
    from app.broker.models import Device

    rows = list(session.execute(select(Device)).scalars().all())
    c.fact(
        "devices.enrolled_count",
        "devices",
        len(rows),
        TruthKind.EVIDENCE,
        evidence_refs=[{"kind": "device", "ref": str(r.id)} for r in rows[:20]],
    )
    if broker_runtime is None:
        if rows:
            c.uncertain(
                "devices", "devices.presence", "no_live_broker_runtime_supplied", count=len(rows)
            )
        return
    for row in rows:
        try:
            online = bool(broker_runtime.is_online(row.id))
        except Exception:  # noqa: BLE001 - one device's probe failing must not skip the rest
            c.uncertain("devices", f"devices.presence.{row.id}", "presence_probe_failed")
            continue
        c.fact(
            f"devices.presence.{row.id}",
            "devices",
            "online" if online else "offline",
            TruthKind.RUNTIME,
            evidence_refs=[{"kind": "device", "ref": str(row.id)}],
        )


def _collect_capabilities(c: _Collector) -> None:
    try:
        from app.voice.realtime_sessions.tools import default_registry

        names = default_registry().names()
    except Exception:  # noqa: BLE001
        c.uncertain("capabilities", "capabilities.voice_tools", "registry_unavailable")
        return
    c.fact(
        "capabilities.voice_tools",
        "capabilities",
        names,
        TruthKind.SOURCE,
        note="declared in code; not a runtime reachability check",
    )


def _collect_tasks(c: _Collector, session: Session) -> None:
    from app.artifacts.models import Task
    from app.artifacts.state import TASK_TERMINAL_STATUSES

    rows = list(session.execute(select(Task)).scalars().all())
    running = [r for r in rows if r.status not in TASK_TERMINAL_STATUSES]
    c.fact(
        "tasks.running_count",
        "tasks",
        len(running),
        TruthKind.EVIDENCE,
        evidence_refs=[{"kind": "task", "ref": str(t.id)} for t in running[:20]],
    )


def _collect_goals(c: _Collector, session: Session) -> None:
    from app.goals.models import GOAL_STATUS_ACTIVE, Goal

    rows = list(
        session.execute(select(Goal).where(Goal.status == GOAL_STATUS_ACTIVE)).scalars().all()
    )
    c.fact(
        "goals.active_count",
        "goals",
        len(rows),
        TruthKind.EVIDENCE,
        evidence_refs=[{"kind": "goal", "ref": str(g.goal_id)} for g in rows[:20]],
    )


def _collect_deployment(c: _Collector, session: Session) -> None:
    from app.selfhealing.models import Release

    rows = list(session.execute(select(Release)).scalars().all())
    latest_by_component: dict[str, Release] = {}
    for row in rows:
        current = latest_by_component.get(row.component)
        row_key = row.promoted_at or row.rolled_back_at or row.created_at
        if current is None:
            latest_by_component[row.component] = row
            continue
        current_key = current.promoted_at or current.rolled_back_at or current.created_at
        if row_key is not None and (current_key is None or row_key > current_key):
            latest_by_component[row.component] = row
    for component, row in latest_by_component.items():
        c.fact(
            f"deployment.{component}.status",
            "deployment",
            row.status,
            TruthKind.EVIDENCE,
            observed_at=row.promoted_at or row.rolled_back_at or row.created_at,
            evidence_refs=[{"kind": "release", "ref": str(row.id)}],
        )


def _collect_incidents(c: _Collector, session: Session) -> None:
    from app.selfhealing.models import Incident

    rows = list(
        session.execute(select(Incident).where(Incident.status.in_(("open", "fix_in_progress"))))
        .scalars()
        .all()
    )
    c.fact(
        "incidents.open_count",
        "incidents",
        len(rows),
        TruthKind.EVIDENCE,
        evidence_refs=[{"kind": "incident", "ref": str(i.id)} for i in rows[:20]],
    )


def _collect_recent_events(c: _Collector, session: Session) -> None:
    from app.ledger import service as ledger_service

    rows = ledger_service.query(session, limit=20)
    c.fact(
        "events.recent",
        "events",
        [row.event_type for row in rows],
        TruthKind.EVIDENCE,
        evidence_refs=[{"kind": "activity_event", "ref": str(row.event_id)} for row in rows],
    )


def _collect_dependencies(
    c: _Collector, settings: Settings, health_results: dict[str, Any] | None
) -> None:
    declared = {
        "database_url": settings.database_url,
        "redis_url": settings.redis_url,
        "temporal_address": settings.temporal_address,
        "s3_endpoint_url": settings.s3_endpoint_url,
    }
    for key, value in declared.items():
        c.fact(
            f"dependencies.{key}",
            "dependencies",
            value,
            TruthKind.SOURCE,
            note="declared configuration; not a reachability probe",
        )
    if not health_results:
        # Deliberately NOT probed here: a real reachability check is I/O the
        # owner did not ask for on every snapshot read, and a fact whose only
        # basis is configuration must stay SOURCE_TRUTH, never be promoted to
        # RUNTIME_TRUTH by assumption (module docstring). A caller that already
        # ran app.health.run_checks may pass the result in as ``health_results``
        # to get real RUNTIME_TRUTH facts here instead of this uncertainty.
        c.uncertain("dependencies", "dependencies.runtime", "no_health_probe_supplied")
        return
    for name, result in health_results.items():
        status = result.get("status") if isinstance(result, dict) else result
        c.fact(f"dependencies.{name}.runtime_status", "dependencies", status, TruthKind.RUNTIME)


def _collect_environment(c: _Collector, settings: Settings) -> None:
    c.fact("environment.name", "environment", settings.environment, TruthKind.SOURCE)
    c.fact(
        "environment.python_runtime",
        "environment",
        sys.version.split()[0],
        TruthKind.RUNTIME,
        note="the interpreter actually executing this process, read at assembly time",
    )
    for package in _TRACKED_PACKAGES:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            c.uncertain(
                "environment", f"environment.installed.{package}", "package_metadata_not_found"
            )
            continue
        c.fact(f"environment.installed.{package}", "environment", version, TruthKind.INSTALLED)


# ------------------------------------------------------------------ assembly


def assemble_snapshot(
    session: Session,
    *,
    settings: Settings | None = None,
    broker_runtime: Any | None = None,
    health_results: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> WorldSnapshot:
    """Read-only. Never raises: every section is isolated by ``_Collector.section``
    so one unavailable source becomes an ``Uncertainty``, not a failed request."""
    now = now or datetime.now(UTC)
    settings = settings or Settings(_env_file=None)
    c = _Collector(now)

    c.section("owner", lambda: _collect_owner(c, session))
    c.section("devices", lambda: _collect_devices(c, session, broker_runtime))
    c.section("capabilities", lambda: _collect_capabilities(c))
    c.section("tasks", lambda: _collect_tasks(c, session))
    c.section("goals", lambda: _collect_goals(c, session))
    c.section("deployment", lambda: _collect_deployment(c, session))
    c.section("incidents", lambda: _collect_incidents(c, session))
    c.section("events", lambda: _collect_recent_events(c, session))
    c.section("dependencies", lambda: _collect_dependencies(c, settings, health_results))
    c.section("environment", lambda: _collect_environment(c, settings))

    return WorldSnapshot(generated_at=now, facts=c.facts, uncertainties=c.uncertainties)


__all__ = [
    "WORLD_VERSION",
    "Fact",
    "TruthKind",
    "Uncertainty",
    "WorldSnapshot",
    "assemble_snapshot",
]
