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
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

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


#: How long an observation of each truth kind may be trusted. Absent means the kind
#: does not age: SOURCE truth is as of the snapshot itself, EVIDENCE truth is history.
STALE_AFTER: Final[dict[TruthKind, timedelta]] = {
    TruthKind.RUNTIME: timedelta(minutes=15),
    TruthKind.INSTALLED: timedelta(days=7),
}


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _aware(dt: datetime) -> datetime:
    """A stored timestamp as UTC. SQLite hands back naive datetimes where PostgreSQL hands
    back aware ones, and subtracting one from the other raises."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Fact:
    key: str
    category: str
    value: Any
    truth_kind: TruthKind
    observed_at: datetime
    confidence: float = 1.0
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    #: A floor, not the answer: set when the assembler KNOWS an observation was
    #: superseded. Age is checked on top of it by :meth:`is_stale` at read time.
    stale: bool = False
    note: str = ""

    def is_stale(self, *, now: datetime | None = None) -> bool:
        """True when this observation is too old to be reported as current.

        Runtime truth ages fastest: a process can restart a second after it was
        observed, so "it was running fifteen minutes ago" is not "it is running".
        Installed truth survives until the next deployment. Evidence truth is a
        historical record and never goes stale - what happened stays happened.

        This is computed rather than stored because nothing revisits a fact when
        time simply passes. Until 2026-09-05 the flag was only ever written as
        False, so every answer claimed a freshness it had not checked - exactly
        what the four truth kinds exist to prevent (independent security review).
        """
        if self.stale:
            return True
        ttl = STALE_AFTER.get(self.truth_kind)
        if ttl is None:
            return False
        moment = now or datetime.now(UTC)
        seen = self.observed_at if self.observed_at.tzinfo else self.observed_at.replace(tzinfo=UTC)
        return (moment - seen) > ttl

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category,
            "value": self.value,
            "truth_kind": self.truth_kind.value,
            "observed_at": _iso(self.observed_at),
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "stale": self.is_stale(now=now),
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
        stale: bool = False,
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
                # A caller-computed floor (module docstring: assembly never
                # upgrades a fact past what its own source supports, and a
                # caller that ALREADY knows an observation was superseded by
                # its own domain's TTL — e.g. app.presence.states
                # .PresenceAssertion.is_stale, which is source-specific and
                # can be stricter than this module's generic per-truth-kind
                # STALE_AFTER — may say so directly; Fact.is_stale still
                # re-checks age on top of it).
                stale=stale,
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


def _collect_presence(c: _Collector, session: Session, presence_runtime: Any | None) -> None:
    """M18 Presence Engine facts: ``owner.presence``, ``owner.awake_state``,
    ``owner.activity_level``, ``owner.last_seen`` and ``device.camera_state``
    (M18_HOLOGRAPHIC_CORE_SPEC.md §1, §2 — named exactly as that spec's
    integration section requires).

    ``presence_runtime`` is optional and injected the same way
    ``broker_runtime`` is above: this module stays read-only and must never
    import a live in-process singleton at module scope (module docstring).
    When absent, presence facts are simply not reported here rather than a
    silent RUNTIME-truth guess — the same discipline `_collect_dependencies`
    applies when no health probe was supplied.

    Presence is RUNTIME_TRUTH (a live inference, not a stored row) except
    ``device.camera_state``, which is EVIDENCE_TRUTH: an explicit
    enable/disable is a durable, owner-caused fact
    (``app.presence.eye.is_eye_enabled`` reads the Activity Ledger), never a
    live probe. Staleness for the RUNTIME facts is computed by presence's OWN
    policy (``PresenceAssertion.is_stale``, which uses the TTL of the actual
    signals behind it, not this module's generic 15-minute RUNTIME default)
    and passed through explicitly — ``Fact.stale`` is a floor a caller may
    set, and this is exactly the case that floor exists for.
    """
    from app.presence.eye import is_eye_enabled
    from app.presence.states import PresenceState

    eye_enabled = is_eye_enabled(session)
    c.fact(
        "device.camera_state",
        "presence",
        "enabled" if eye_enabled else "disabled",
        TruthKind.EVIDENCE,
        note="from the most recent eye.enabled/eye.disabled ledger event, or the default",
    )

    assertion = presence_runtime.current() if presence_runtime is not None else None
    if not eye_enabled and (assertion is None or assertion.state is PresenceState.UNKNOWN):
        # The owner turned the eye off and the engine holds no claim: the reason the
        # presence is unknown is the owner's own decision, and the answer says so
        # (docs/M18_ACTION_CONTRACT.md §5.4) rather than "no observations yet", which
        # would read as "the camera has not delivered" - a defect, not a choice.
        c.uncertain("presence", "owner.presence", "eye_disabled")
        return

    if presence_runtime is None:
        c.uncertain("presence", "owner.presence", "no_live_presence_runtime_supplied")
        return

    if assertion is None:
        c.uncertain("presence", "owner.presence", "no_observations_yet")
        return
    if assertion.state is PresenceState.UNKNOWN:
        # UNKNOWN is the absence of a claim (app.presence.service), not a state the owner
        # is in. Reported as a fact it read as "presence: unknown, stale" - and the live
        # composer would then have dated a "last verified observation" that never was.
        c.uncertain("presence", "owner.presence", assertion.reason or "no_observations_yet")
        return

    stale = assertion.is_stale(now=c.now)
    last_seen_sources = [s for s in assertion.signals if s.person_present]
    last_seen_at = max((s.observed_at for s in last_seen_sources), default=assertion.observed_at)

    c.fact(
        "owner.presence",
        "presence",
        assertion.state.value,
        TruthKind.RUNTIME,
        observed_at=assertion.observed_at,
        confidence=assertion.confidence,
        stale=stale,
        note=assertion.reason,
    )
    c.fact(
        "owner.awake_state",
        "presence",
        next((s.awake_state for s in reversed(assertion.signals)), "uncertain"),
        TruthKind.RUNTIME,
        observed_at=assertion.observed_at,
        confidence=assertion.confidence,
        stale=stale,
    )
    c.fact(
        "owner.activity_level",
        "presence",
        next((s.activity_level for s in reversed(assertion.signals)), "none"),
        TruthKind.RUNTIME,
        observed_at=assertion.observed_at,
        confidence=assertion.confidence,
        stale=stale,
    )
    c.fact(
        "owner.last_seen",
        "presence",
        _iso(last_seen_at),
        TruthKind.RUNTIME,
        observed_at=assertion.observed_at,
        confidence=assertion.confidence,
        stale=stale,
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


#: A task still non-terminal this long after it was CREATED is not progressing. `tasks` has
#: no `updated_at`, so age since creation is the only clock the row carries - which is why the
#: fact below says "created more than 24h ago" rather than claiming to measure dwell time in
#: the current state. The research workflow's own budget is minutes, so a day is well past the
#: point where "still working on it" is a believable sentence.
STUCK_TASK_AFTER: Final[timedelta] = timedelta(hours=24)


def _collect_tasks(c: _Collector, session: Session, *, now: datetime | None = None) -> None:
    """What the tasks are, by what each state MEANS.

    ``tasks.running_count`` used to be "not terminal", so finished research waiting for the
    owner was reported as work in progress: production said 10 running while nothing ran. The
    counts are separated now, and they are RUNTIME truth rather than EVIDENCE - "how many are
    running" is an observation of this moment and must age like one. As EVIDENCE it never went
    stale, so a snapshot from an hour ago read as current fact.
    """
    from app.artifacts.models import Task
    from app.artifacts.state import (
        TASK_ACTIVE_STATUSES,
        TASK_AWAITING_OWNER_STATUSES,
        TASK_PENDING_STATUSES,
        TASK_TERMINAL_STATUSES,
        TASK_WAITING_STATUSES,
    )

    moment = now or datetime.now(UTC)
    rows = list(session.execute(select(Task)).scalars().all())

    def _refs(tasks: list[Any]) -> list[dict[str, str]]:
        return [{"kind": "task", "ref": str(t.id)} for t in tasks[:20]]

    for key, statuses in (
        ("tasks.running_count", TASK_ACTIVE_STATUSES),
        ("tasks.pending_count", TASK_PENDING_STATUSES),
        ("tasks.waiting_external_count", TASK_WAITING_STATUSES),
        ("tasks.awaiting_owner_count", TASK_AWAITING_OWNER_STATUSES),
    ):
        matched = [r for r in rows if r.status in statuses]
        c.fact(key, "tasks", len(matched), TruthKind.RUNTIME, evidence_refs=_refs(matched))

    # A task nobody is moving. Reported rather than swept: the world model says what IS, and
    # `app.maintenance` is what acts (B06 req 69/11).
    stuck = [
        r
        for r in rows
        if r.status not in TASK_TERMINAL_STATUSES
        and r.created_at is not None
        and moment - _aware(r.created_at) > STUCK_TASK_AFTER
    ]
    c.fact(
        "tasks.stuck_count",
        "tasks",
        len(stuck),
        TruthKind.RUNTIME,
        evidence_refs=_refs(stuck),
        note=(
            "created more than "
            f"{int(STUCK_TASK_AFTER.total_seconds() // 3600)}h ago and still not terminal"
        ),
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


def _collect_ambient(c: _Collector, session: Session) -> None:
    """M18.3 §3.6e: what the owner's machines say about their screens and their keyboards,
    and when the next alarm is due.

    ``RUNTIME`` truth, deliberately: these come from a live heartbeat roughly every ten
    seconds, not from a record, and ``STALE_AFTER[RUNTIME]`` is what makes a device that
    stopped reporting fade to stale rather than sit there claiming a lit screen. A device
    with no owner-session companion sends no status at all and simply contributes no facts —
    the absence is the honest answer, not a default.
    """
    from app.ambient.ingest import world_model_facts

    for entry in world_model_facts():
        c.fact(
            entry["key"],
            "ambient",
            entry["value"],
            TruthKind.RUNTIME,
            observed_at=entry["observed_at"],
            evidence_refs=[{"kind": "device", "ref": entry["device_id"]}],
        )

    from app.alarms import service as alarms_service
    from app.alarms.models import ALARM_PENDING_STATES

    pending = [
        a for a in alarms_service.list_alarms(session, limit=200)
        if a.state in ALARM_PENDING_STATES
    ]
    if not pending:
        c.fact("alarm.next_scheduled_for", "ambient", None, TruthKind.RUNTIME)
        return
    soonest = min(pending, key=lambda a: a.scheduled_for)
    c.fact(
        "alarm.next_scheduled_for",
        "ambient",
        _iso(soonest.scheduled_for),
        TruthKind.RUNTIME,
        evidence_refs=[{"kind": "wake_alarm", "ref": str(soonest.id)}],
        note=f"{soonest.local_time} {soonest.timezone}",
    )


def assemble_snapshot(
    session: Session,
    *,
    settings: Settings | None = None,
    broker_runtime: Any | None = None,
    presence_runtime: Any | None = None,
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
    c.section("presence", lambda: _collect_presence(c, session, presence_runtime))
    c.section("capabilities", lambda: _collect_capabilities(c))
    # `now`, not the wall clock: the snapshot's own moment decides what is stale here as it
    # does everywhere else in this function. One decision, one clock.
    c.section("tasks", lambda: _collect_tasks(c, session, now=now))
    c.section("goals", lambda: _collect_goals(c, session))
    c.section("deployment", lambda: _collect_deployment(c, session))
    c.section("incidents", lambda: _collect_incidents(c, session))
    c.section("events", lambda: _collect_recent_events(c, session))
    c.section("ambient", lambda: _collect_ambient(c, session))
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
