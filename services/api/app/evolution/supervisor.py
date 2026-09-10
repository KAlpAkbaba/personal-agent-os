"""The Evolution Supervisor (docs/M18_4_SELF_EVOLUTION_SPEC.md §3).

A clock-driven OBSERVER: it reads durable signals - self-healing incidents, recurring
failed action receipts, recurring research failures, open capability gaps - and turns them
into deduplicated ``EvolutionOpportunity`` rows through ``EvolutionService.create_from_evidence``
(so every one is evidence-verified, scored and audited by the kernel that owns the backlog).
It writes nothing else but its own ledger rows: the owner's pause switch (``evolution.paused``
/ ``evolution.resumed``) and one ``evolution.supervisor_scanned`` row per scan that opened
something. A quiet scan is not a fact worth a row.

Three rules are enforced here rather than assumed:

- the supervisor holds the engine's LAB authority and nothing more (it uses the service it is
  handed; it cannot mint or widen an authority, and it never calls ``advance`` at all);
- priority and promotion class are DERIVED - the priority from the signal's kind, the
  promotion class from the risk tier of the paths the signal's component maps to
  (``app.evolution.risk``, the same table the release path reads) - never chosen per row;
- while the owner has paused self-evolution, a scan records nothing and says so.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.evolution.risk import RiskTier, derive_risk_tier
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_ACTION_RECEIPT,
    EVENT_TYPE_EVOLUTION_PAUSED,
    EVENT_TYPE_EVOLUTION_RESUMED,
    EVENT_TYPE_EVOLUTION_SUPERVISOR_SCANNED,
    EVENT_TYPE_RESEARCH_FAILED,
    SEVERITY_INFO,
    STATUS_COMPLETED,
    SUBSYSTEM_EVOLUTION,
)
from app.logging import get_logger

logger = get_logger("app.evolution.supervisor")

SUPERVISOR_VERSION = 1

# ------------------------------------------------------------------ classes

PRIORITY_P0 = "P0"  # availability / safety: an incident, a failed or rolled-back release
PRIORITY_P1 = "P1"  # the owner's own commands failing
PRIORITY_P2 = "P2"  # a subsystem failing its job quietly
PRIORITY_P3 = "P3"  # code health and capability gaps
PRIORITIES = (PRIORITY_P0, PRIORITY_P1, PRIORITY_P2, PRIORITY_P3)

PROMOTION_AUTO_SAFE = "AUTO_SAFE"
PROMOTION_AUTO_CANARY = "AUTO_CANARY"
PROMOTION_OWNER_APPROVAL_REQUIRED = "OWNER_APPROVAL_REQUIRED"
PROMOTION_NEVER_AUTO_PROMOTE = "NEVER_AUTO_PROMOTE"
PROMOTION_CLASSES = (
    PROMOTION_AUTO_SAFE,
    PROMOTION_AUTO_CANARY,
    PROMOTION_OWNER_APPROVAL_REQUIRED,
    PROMOTION_NEVER_AUTO_PROMOTE,
)

SIGNAL_INCIDENT = "incident"
SIGNAL_ACTION_FAILURE = "action_failure"
SIGNAL_RESEARCH_FAILURE = "research_failure"
SIGNAL_CAPABILITY_GAP = "capability_gap"
SIGNAL_KINDS = (
    SIGNAL_INCIDENT,
    SIGNAL_ACTION_FAILURE,
    SIGNAL_RESEARCH_FAILURE,
    SIGNAL_CAPABILITY_GAP,
)

#: How many times the same failure must recur inside the window before it is a signal.
RECURRENCE_THRESHOLD = 2
WINDOW_DAYS = 7
#: Bounded reads: the ledger scan looks at the newest rows only.
LEDGER_SCAN_LIMIT = 500

#: Opportunity statuses in which the lab is still working (spec §3.5 "what am I building").
BUILDING_STATUSES = ("researching", "design_ready", "building", "testing", "evaluating")
PENDING_STATUSES = ("shadow_ready", "owner_approval_required", "owner_approved", "owner_authorized")
RELEASE_FAILURE_STATUSES = ("failed", "rolling_back", "rolled_back")

#: The representative path a signal's component maps to, for the risk table. A path the
#: table does not know falls to its default (internal logic) - the table decides, this map
#: only names where the change would land.
_COMPONENT_PATHS: dict[str, tuple[str, ...]] = {
    "cloud-core": ("services/api/app/main.py",),
    "api": ("services/api/app/main.py",),
    "recovery-supervisor": ("services/recovery-supervisor/recovery_supervisor/runner.py",),
    "windows-agent": ("windows-agent/DeviceService/Program.cs",),
    "browser-worker": ("services/browser/pagentos_browser/worker.py",),
    "web": ("apps/web/app/core/CoreView.tsx",),
    "voice_routing": ("services/api/app/voice/intents.py",),
    "research": ("services/api/app/research/browser_workflow.py",),
    "alarms": ("services/api/app/alarms/service.py",),
    "ambient": ("services/api/app/ambient/service.py",),
    "presence": ("services/api/app/presence/eye.py",),
    "voice_tools": ("services/api/app/voice/realtime_sessions/tools.py",),
    #: The Digital Operator (M19). Deliberately NOT "app/operator/models.py": that is a
    #: real ORM module and the risk table reads any ``models.py`` as tier 4 (schema and
    #: deployment mechanics = NEVER_AUTO_PROMOTE), which would classify every routine
    #: "typing went to the wrong window" fix as a migration. Naming behaviour files and
    #: not the schema is what every other entry here already does - "alarms" and
    #: "research" both HAVE a models.py and neither names it.
    "operator": (
        "services/api/app/operator/service.py",
        "services/api/app/operator/task.py",
        "services/api/app/operator/plans.py",
        "services/api/app/operator/focus.py",
        "services/api/app/voice/realtime_sessions/tools_operator.py",
    ),
    "generated_skill": ("var/evolution/skills/candidate/skill.py",),
}

_CAPABILITY_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("alarm.", "alarms"),
    ("display.", "ambient"),
    ("ambient.", "ambient"),
    ("eye.", "presence"),
    ("research.", "research"),
    ("operator.", "operator"),
)


def component_for_capability(capability: str) -> str:
    for prefix, component in _CAPABILITY_COMPONENTS:
        if capability.startswith(prefix):
            return component
    return "voice_tools"


def paths_for_component(component: str) -> tuple[str, ...]:
    return _COMPONENT_PATHS.get(component, (f"services/api/app/{component}/service.py",))


def promotion_class_for_tier(tier: int) -> str:
    if tier <= int(RiskTier.UI_ADDITIVE):
        return PROMOTION_AUTO_SAFE
    if tier == int(RiskTier.INTERNAL_LOGIC):
        return PROMOTION_AUTO_CANARY
    if tier == int(RiskTier.PRODUCTION_BEHAVIOR):
        return PROMOTION_OWNER_APPROVAL_REQUIRED
    return PROMOTION_NEVER_AUTO_PROMOTE


def promotion_class_for(paths: tuple[str, ...]) -> tuple[str, int, list[str]]:
    """(class, tier, reasons) for the paths a change would touch - from the risk table."""
    assessment = derive_risk_tier(list(paths))
    tier = int(assessment.tier)
    return promotion_class_for_tier(tier), tier, list(assessment.reasons)


# ------------------------------------------------------------------ signals


@dataclass(frozen=True, slots=True)
class Signal:
    kind: str
    priority: str
    component: str
    source: str
    source_ref: str
    title: str
    statement: str
    evidence_refs: tuple[dict[str, str], ...]
    occurrences: int
    first_seen_at: str | None
    last_seen_at: str | None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def paths(self) -> tuple[str, ...]:
        return paths_for_component(self.component)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def signals_from_incidents(incidents: list[dict[str, Any]]) -> list[Signal]:
    out: list[Signal] = []
    for incident in incidents:
        status = str(incident.get("status") or "")
        if status not in ("open", "recovered", "fix_in_progress"):
            continue
        incident_id = str(incident.get("id") or "")
        if not incident_id:
            continue
        component = str(incident.get("component") or "cloud-core")
        evidence = dict(incident.get("evidence") or {})
        error_class = str(evidence.get("error_class") or incident.get("fingerprint") or "")[:64]
        out.append(
            Signal(
                kind=SIGNAL_INCIDENT,
                priority=PRIORITY_P0,
                component=component,
                source="incident",
                source_ref=incident_id,
                title=f"Olay: {component} {error_class or 'sağlık'}"[:200],
                statement=(
                    f"Self-healing incident {incident_id} on {component} "
                    f"(status {status}, error class {error_class or 'unknown'}, "
                    f"seen {int(incident.get('occurrence_count') or 1)}x). Reproduce from the "
                    "incident's evidence before patching; the recovery supervisor already "
                    "restored service."
                ),
                evidence_refs=({"kind": "incident", "ref": incident_id},),
                occurrences=int(incident.get("occurrence_count") or 1),
                first_seen_at=_iso(incident.get("first_seen_at")),
                last_seen_at=_iso(incident.get("last_seen_at")),
                detail={"incident_status": status, "error_class": error_class},
            )
        )
    return out


def signals_from_ledger(session: Session, *, now: datetime) -> list[Signal]:
    """Recurring failed action receipts (P1) and recurring research failures (P2), inside
    the window, from the newest rows only."""
    since = now - timedelta(days=WINDOW_DAYS)
    rows = ledger_service.query(
        session,
        since=since,
        event_types=[EVENT_TYPE_ACTION_RECEIPT, EVENT_TYPE_RESEARCH_FAILED],
        limit=LEDGER_SCAN_LIMIT,
    )
    actions: dict[tuple[str, str], list[Any]] = {}
    research: dict[str, list[Any]] = {}
    for row in rows:
        detail = dict(row.detail_json or {})
        if row.event_type == EVENT_TYPE_ACTION_RECEIPT:
            if str(detail.get("execution_status") or "") != "failed":
                continue
            capability = str(detail.get("capability") or "")[:64]
            error_class = str(detail.get("error_class") or "unknown")[:64]
            if not capability:
                continue
            actions.setdefault((capability, error_class), []).append(row)
        else:
            error_class = str(detail.get("error_class") or row.result or "unknown")[:64]
            research.setdefault(error_class, []).append(row)

    out: list[Signal] = []
    for (capability, error_class), group in sorted(actions.items()):
        if len(group) < RECURRENCE_THRESHOLD:
            continue
        newest = group[0]
        out.append(
            Signal(
                kind=SIGNAL_ACTION_FAILURE,
                priority=PRIORITY_P1,
                component=component_for_capability(capability),
                source="ledger_event",
                source_ref=f"action_failed:{capability}:{error_class}"[:256],
                title=f"Tekrarlayan eylem hatası: {capability} ({error_class})"[:200],
                statement=(
                    f"{len(group)} failed receipts for {capability} with error class "
                    f"{error_class} in the last {WINDOW_DAYS} days (newest "
                    f"{_iso(newest.occurred_at)}). The owner's command is not doing what it "
                    "says; reproduce through the corpus harness or the tool's tests first."
                ),
                evidence_refs=tuple(
                    {"kind": "ledger_event", "ref": str(r.event_id)} for r in group[:3]
                ),
                occurrences=len(group),
                first_seen_at=_iso(group[-1].occurred_at),
                last_seen_at=_iso(newest.occurred_at),
                detail={"capability": capability, "error_class": error_class},
            )
        )
    for error_class, group in sorted(research.items()):
        if len(group) < RECURRENCE_THRESHOLD:
            continue
        newest = group[0]
        out.append(
            Signal(
                kind=SIGNAL_RESEARCH_FAILURE,
                priority=PRIORITY_P2,
                component="research",
                source="ledger_event",
                source_ref=f"research_failed:{error_class}"[:256],
                title=f"Tekrarlayan araştırma hatası: {error_class}"[:200],
                statement=(
                    f"{len(group)} research runs failed with {error_class} in the last "
                    f"{WINDOW_DAYS} days (newest {_iso(newest.occurred_at)}). Replay the runs "
                    "by their counts (test_research_regression_*.py pattern) before patching."
                ),
                evidence_refs=tuple(
                    {"kind": "ledger_event", "ref": str(r.event_id)} for r in group[:3]
                ),
                occurrences=len(group),
                first_seen_at=_iso(group[-1].occurred_at),
                last_seen_at=_iso(newest.occurred_at),
                detail={"error_class": error_class},
            )
        )
    return out


def signals_from_gaps(gaps: list[dict[str, Any]]) -> list[Signal]:
    out: list[Signal] = []
    for gap in gaps:
        if (
            str(gap.get("status") or "") != "open"
            or str(gap.get("resolution") or "") != "generation"
        ):
            continue
        gap_id = str(gap.get("id") or "")
        if not gap_id:
            continue
        requested = str(gap.get("requested_capability") or "")[:64]
        out.append(
            Signal(
                kind=SIGNAL_CAPABILITY_GAP,
                priority=PRIORITY_P3,
                component="generated_skill",
                source="capability_gap",
                source_ref=gap_id,
                title=f"Yetenek boşluğu: {requested}"[:200],
                statement=(
                    f"Capability gap {gap_id} ({requested}) is open and resolved as generation; "
                    "the M7 pipeline owns the work (sandbox, generated tests, independent review)."
                ),
                evidence_refs=({"kind": "capability_gap", "ref": gap_id},),
                occurrences=1,
                first_seen_at=_iso(gap.get("created_at")),
                last_seen_at=_iso(gap.get("created_at")),
                detail={"requested_capability": requested},
            )
        )
    return out


def collect_signals(
    session: Session,
    *,
    now: datetime,
    incidents: list[dict[str, Any]] | None = None,
    gaps: list[dict[str, Any]] | None = None,
) -> list[Signal]:
    signals = signals_from_incidents(incidents or [])
    signals.extend(signals_from_ledger(session, now=now))
    signals.extend(signals_from_gaps(gaps or []))
    order = {p: i for i, p in enumerate(PRIORITIES)}
    return sorted(signals, key=lambda s: (order[s.priority], s.kind, s.source_ref))


# ------------------------------------------------------------------ scoring


def scores_for(signal: Signal, *, tier: int) -> dict[str, float]:
    relevance = {PRIORITY_P0: 1.0, PRIORITY_P1: 0.9, PRIORITY_P2: 0.7, PRIORITY_P3: 0.5}
    return {
        "owner_relevance": relevance[signal.priority],
        "expected_utility": relevance[signal.priority],
        "recurrence": min(1.0, 0.4 + 0.15 * max(0, signal.occurrences - 1)),
        "confidence": 0.9,
        "engineering_cost": 0.4,
        "operational_risk": min(1.0, tier / 5.0),
    }


# ------------------------------------------------------------ pause switch


def is_paused(session: Session) -> bool:
    rows = ledger_service.query(
        session,
        subsystems=[SUBSYSTEM_EVOLUTION],
        event_types=[EVENT_TYPE_EVOLUTION_PAUSED, EVENT_TYPE_EVOLUTION_RESUMED],
        limit=1,
    )
    return bool(rows) and rows[0].event_type == EVENT_TYPE_EVOLUTION_PAUSED


def set_paused(
    session: Session, *, paused: bool, actor: str, reason: str = "", now: datetime | None = None
) -> Any:
    """Write the switch as a ledger row (the latest row IS the state); returns the row.

    The switch's ``occurred_at`` is strictly increasing: two commands inside the same
    instant (a pause and a resume from one breath, or a test clock that does not move)
    would otherwise tie on the ledger's ordering and leave "which is latest" to the
    database. The later WRITE is the later state, so it gets the later instant.
    """
    moment = now or datetime.now(UTC)
    latest = ledger_service.query(
        session,
        subsystems=[SUBSYSTEM_EVOLUTION],
        event_types=[EVENT_TYPE_EVOLUTION_PAUSED, EVENT_TYPE_EVOLUTION_RESUMED],
        limit=1,
    )
    if latest:
        previous = latest[0].occurred_at
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        if previous >= moment:
            moment = previous + timedelta(microseconds=1)
    event = ledger_service.ActivityEvent(
        event_type=EVENT_TYPE_EVOLUTION_PAUSED if paused else EVENT_TYPE_EVOLUTION_RESUMED,
        subsystem=SUBSYSTEM_EVOLUTION,
        action="evolution_paused" if paused else "evolution_resumed",
        status=STATUS_COMPLETED,
        severity=SEVERITY_INFO,
        result="paused" if paused else "resumed",
        factual_summary=(
            "Kendi kendini geliştirme duraklatıldı."
            if paused
            else "Kendi kendini geliştirme yeniden açıldı."
        ),
        occurred_at=moment,
        version=str(SUPERVISOR_VERSION),
        detail_json={"paused": paused, "actor": actor[:64], "reason": reason[:200]},
        source=actor[:64] or "owner",
        source_ref=f"evolution_pause:{'on' if paused else 'off'}:{moment.isoformat()}",
    )
    row = ledger_service.record(session, event)
    session.commit()
    return row


# ---------------------------------------------------------------- the scan


@dataclass
class ScanResult:
    status: str  # scanned | skipped_disabled | skipped_not_due | skipped_paused | failed
    at: datetime
    signals: int = 0
    opened: list[dict[str, Any]] = field(default_factory=list)
    already_tracked: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "at": _iso(self.at),
            "signals": self.signals,
            "opened": [o.get("opportunity_id") for o in self.opened],
            "already_tracked": self.already_tracked,
            "by_kind": dict(self.by_kind),
            "error": self.error,
        }


class EvolutionSupervisor:
    """The clock-driven observer; one instance per process, bound to its sources once."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        interval_s: float = 300.0,
        incidents: Callable[..., list[dict[str, Any]]] | None = None,
        gaps: Callable[..., list[dict[str, Any]]] | None = None,
    ) -> None:
        self.enabled = enabled
        self.interval_s = max(10.0, float(interval_s))
        self._incidents = incidents
        self._gaps = gaps
        self.last_scan_at: datetime | None = None
        self.last_result: ScanResult | None = None
        self.scans = 0
        self.opportunities_opened_total = 0
        self.last_error: str | None = None

    def bind(
        self,
        *,
        incidents: Callable[..., list[dict[str, Any]]] | None = None,
        gaps: Callable[..., list[dict[str, Any]]] | None = None,
    ) -> None:
        if incidents is not None:
            self._incidents = incidents
        if gaps is not None:
            self._gaps = gaps

    # ------------------------------------------------------------- sources

    def _read_incidents(self, status: str | None = None) -> list[dict[str, Any]]:
        if self._incidents is None:
            return []
        try:
            return list(self._incidents(status=status, limit=200))
        except TypeError:
            return list(self._incidents())
        except Exception as exc:  # noqa: BLE001 - a source failing is a fact, not a crash
            self.last_error = f"incidents: {type(exc).__name__}: {exc}"[:200]
            return []

    def _read_gaps(self) -> list[dict[str, Any]]:
        if self._gaps is None:
            return []
        try:
            return list(self._gaps(status="open", limit=100))
        except TypeError:
            return list(self._gaps())
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"gaps: {type(exc).__name__}: {exc}"[:200]
            return []

    # ---------------------------------------------------------------- tick

    def due(self, now: datetime) -> bool:
        if self.last_scan_at is None:
            return True
        return (now - self.last_scan_at).total_seconds() >= self.interval_s

    def scan(
        self,
        session: Session,
        *,
        now: datetime | None = None,
        evolution_service: Any,
        force: bool = False,
    ) -> ScanResult:
        moment = now or datetime.now(UTC)
        if not self.enabled:
            return self._finish(ScanResult(status="skipped_disabled", at=moment), tick=False)
        if not force and not self.due(moment):
            return ScanResult(status="skipped_not_due", at=moment)
        self.last_scan_at = moment
        self.scans += 1
        if is_paused(session):
            return self._finish(ScanResult(status="skipped_paused", at=moment))
        try:
            signals = collect_signals(
                session, now=moment, incidents=self._read_incidents(), gaps=self._read_gaps()
            )
            existing = {
                (o.get("source"), o.get("source_ref"))
                for o in evolution_service.list_opportunities(limit=500)
            }
            result = ScanResult(status="scanned", at=moment, signals=len(signals))
            result.by_kind = dict(Counter(s.kind for s in signals))
            for signal in signals:
                if (signal.source, signal.source_ref) in existing:
                    result.already_tracked += 1
                    continue
                promotion, tier, reasons = promotion_class_for(signal.paths)
                opportunity = evolution_service.create_from_evidence(
                    title=signal.title,
                    statement=signal.statement,
                    evidence_refs=list(signal.evidence_refs),
                    scores=scores_for(signal, tier=tier),
                    source=signal.source,
                    source_ref=signal.source_ref,
                    detail={
                        "priority": signal.priority,
                        "promotion_class": promotion,
                        "risk_tier": tier,
                        "risk_reasons": reasons[:6],
                        "signal": {
                            "kind": signal.kind,
                            "component": signal.component,
                            "occurrences": signal.occurrences,
                            "first_seen_at": signal.first_seen_at,
                            "last_seen_at": signal.last_seen_at,
                            **signal.detail,
                        },
                        "supervisor_version": SUPERVISOR_VERSION,
                    },
                )
                result.opened.append(opportunity)
            if result.opened:
                self.opportunities_opened_total += len(result.opened)
                self._record_scan(session, result)
            return self._finish(result)
        except Exception as exc:  # noqa: BLE001 - the clock must keep ticking
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            logger.error("evolution_supervisor_scan_failed", error=self.last_error)
            return self._finish(ScanResult(status="failed", at=moment, error=self.last_error))

    def _finish(self, result: ScanResult, *, tick: bool = True) -> ScanResult:
        if tick:
            self.last_result = result
        return result

    def _record_scan(self, session: Session, result: ScanResult) -> None:
        opened = [o.get("opportunity_id") for o in result.opened]
        event = ledger_service.ActivityEvent(
            event_type=EVENT_TYPE_EVOLUTION_SUPERVISOR_SCANNED,
            subsystem=SUBSYSTEM_EVOLUTION,
            action="supervisor_scanned",
            status=STATUS_COMPLETED,
            severity=SEVERITY_INFO,
            result=f"{len(opened)} opened",
            factual_summary=(
                f"Evrim gözetmeni {result.signals} sinyal taradı, {len(opened)} yeni fırsat açtı."
            ),
            occurred_at=result.at,
            version=str(SUPERVISOR_VERSION),
            evidence_refs=[{"kind": "evolution_opportunity", "ref": str(o)} for o in opened],
            detail_json={
                "signals": result.signals,
                "opened": [str(o) for o in opened],
                "already_tracked": result.already_tracked,
                "by_kind": dict(result.by_kind),
            },
            source="evolution_supervisor",
            source_ref=f"scan:{result.at.isoformat()}",
        )
        ledger_service.record(session, event)
        session.commit()

    # -------------------------------------------------------------- status

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "scans": self.scans,
            "last_scan_at": _iso(self.last_scan_at),
            "last_status": self.last_result.status if self.last_result else None,
            "opportunities_opened": self.opportunities_opened_total,
            "last_error": self.last_error,
        }

    def status(
        self,
        session: Session,
        *,
        now: datetime | None = None,
        evolution_service: Any,
        release: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The owner-facing picture, from rows alone (spec §3.5)."""
        moment = now or datetime.now(UTC)
        opportunities = list(evolution_service.list_opportunities(limit=500))
        by_priority: Counter[str] = Counter()
        building: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        release_failures: list[dict[str, Any]] = []
        for o in opportunities:
            detail = dict(o.get("detail") or {})
            status = str(o.get("status") or "")
            summary = {
                "opportunity_id": o.get("opportunity_id"),
                "title": o.get("title"),
                "status": status,
                "priority": detail.get("priority"),
                "promotion_class": detail.get("promotion_class"),
                "updated_at": o.get("updated_at"),
            }
            if status in BUILDING_STATUSES:
                building.append(summary)
            elif status in PENDING_STATUSES:
                pending.append(summary)
            elif status in RELEASE_FAILURE_STATUSES:
                release_failures.append(summary)
            if status not in ("live", "rejected", "superseded", "quarantined", "rolled_back"):
                by_priority[str(detail.get("priority") or "unclassified")] += 1
        fixed = self._read_incidents(status="fixed")
        last_fix = fixed[0] if fixed else None
        return {
            "supervisor_version": SUPERVISOR_VERSION,
            "paused": is_paused(session),
            "enabled": self.enabled,
            "last_scan_at": _iso(self.last_scan_at),
            "last_scan": self.last_result.as_dict() if self.last_result else None,
            "open_by_priority": {p: by_priority.get(p, 0) for p in PRIORITIES}
            | (
                {"unclassified": by_priority["unclassified"]} if by_priority["unclassified"] else {}
            ),
            "building": building[:10],
            "pending_candidates": pending[:10],
            "release_failures": release_failures[:10],
            "last_fix": (
                {
                    "incident_id": last_fix.get("id"),
                    "component": last_fix.get("component"),
                    "fixed_release_id": last_fix.get("fixed_release_id"),
                    "last_seen_at": last_fix.get("last_seen_at"),
                    "error_class": str((last_fix.get("evidence") or {}).get("error_class") or ""),
                }
                if last_fix
                else None
            ),
            "running": release,
            "observed_at": _iso(moment),
        }


def priority_of(opportunity: dict[str, Any]) -> str | None:
    return (opportunity.get("detail") or {}).get("priority")


def promotion_class_of(opportunity: dict[str, Any]) -> str | None:
    return (opportunity.get("detail") or {}).get("promotion_class")


__all__ = [
    "BUILDING_STATUSES",
    "EvolutionSupervisor",
    "PENDING_STATUSES",
    "PRIORITIES",
    "PROMOTION_CLASSES",
    "SIGNAL_KINDS",
    "ScanResult",
    "Signal",
    "collect_signals",
    "component_for_capability",
    "is_paused",
    "priority_of",
    "promotion_class_for",
    "promotion_class_of",
    "set_paused",
    "signals_from_gaps",
    "signals_from_incidents",
    "signals_from_ledger",
]
