"""Drive this candidate through the REAL Evolution Engine lifecycle, and record it.

This is not a simulation of the lifecycle and it does not reimplement any part
of it. It calls ``app.evolution.service.EvolutionService`` for every step, so
every transition goes through the real authority kernel
(``LabAuthority``/``Grant``), the real legal-transition table
(``app/evolution/backlog.py``), the real Activity Ledger writer
(``app/ledger/service.record``, whose vocabulary is closed) and the real
in-process UI-state bus (``app/uistate``). If any of those refuses, this script
fails; nothing here can talk it round.

    IDEA -> RESEARCHING -> DESIGN_READY -> BUILDING -> TESTING -> EVALUATING
         -> SHADOW_READY   [stop]

It stops at ``SHADOW_READY`` on purpose. ``OWNER_APPROVED`` needs an
``OwnerCapability`` minted from a verified owner session, and this script — lab
code — has no expression that yields one. Running it cannot deploy anything.

Storage
-------

An in-memory SQLite database built from the same ORM metadata the unit tests
use. That is deliberate: writing lab bookkeeping into the owner's production
PostgreSQL would itself be a production write the lab is not allowed to make
(``Grant.WRITE_PRODUCTION_DB`` is a production grant). The *lifecycle law* being
exercised is the real one; only the store is ephemeral, and the run's whole
output is written to ``evidence/lifecycle_run.json`` so it is reviewable.

Evidence
--------

The opportunity is created from evidence that already exists in the project
record, backfilled into the ledger the way the ledger's own backfill path does:

* ADR-0051 addendum 2 (2026-09-04) — the qualification harness failed on a
  fire-and-forget shell start;
* ADR-0051 addendum 4 (2026-09-05) — ``speech_head -like "Efendim, son ara*"``
  failed a working system, and the decision recorded was
  "generated wording is never acceptance evidence";
* the successful ``-VerifyOnly`` re-verification that closed the second one,
  which is what lets the Experience Compiler see a completed chain.

The compiler is then run for real over those events, and whatever lesson it
actually produces is cited as ``lesson`` evidence. The service verifies every
cited ref against the database and refuses the opportunity if one does not
resolve.

Run with the API venv from ``services/api``::

    python lab/candidates/acceptance_wording_guard/run_lifecycle.py
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path(__file__).resolve().parent
API_ROOT = CANDIDATE_ROOT.parents[2]
REPO_ROOT = API_ROOT.parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.evolution.backlog import ActorKind, OpportunityStatus  # noqa: E402
from app.evolution.models import CapabilityGap, EvolutionOpportunity  # noqa: E402
from app.evolution.service import EvolutionService  # noqa: E402
from app.experience.compiler import compile_lessons  # noqa: E402
from app.experience.models import ExperienceLessonRow  # noqa: E402
from app.ledger.models import ActivityEventRow  # noqa: E402
from app.ledger.service import ActivityEvent  # noqa: E402
from app.ledger.service import record as record_activity  # noqa: E402
from app.ledger.vocabulary import (  # noqa: E402
    EVENT_TYPE_INCIDENT_OPENED,
    EVENT_TYPE_VOICE_EXPLAINED,
    PRODUCTION_STATE_NA,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    SUBSYSTEM_VOICE,
)
from app.memory.models import (  # noqa: E402
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.selfhealing.models import Incident, Release  # noqa: E402
from app.uistate import get_publisher  # noqa: E402

TABLES = [
    ActivityEventRow.__table__,
    Incident.__table__,
    Release.__table__,
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
    ExperienceLessonRow.__table__,
    EvolutionOpportunity.__table__,
    CapabilityGap.__table__,
]

NOW = datetime(2026, 9, 5, 21, 0, tzinfo=UTC)

TITLE = "Acceptance Wording Guard"

STATEMENT = (
    "Acceptance and qualification checks must not depend on generated "
    "natural-language wording. Two owner qualification runs have been lost to "
    "this defect class (ADR-0051 addendum 2, 2026-09-04; ADR-0051 addendum 4, "
    '2026-09-05, where `speech_head -like "Efendim, son ara*"` failed a system '
    "that was working because the briefing had been made shorter and better "
    "worded the same day). The recorded decision -- generated wording is never "
    "acceptance evidence; paraphrasing is free, an unsupported claim is not -- "
    "is enforced by nothing. Build a read-only checker that reports acceptance "
    "assertions resting on generated prose, with a low-false-positive, "
    "subject-driven design and a reasoned allow-list, and report the shape of "
    "each match rather than the matched text."
)

#: Deliberately conservative. Recurrence 2/2 observed occurrences; operational
#: risk is low because the candidate is read-only and wired to nothing, which
#: is exactly why it must not be scored as if it were.
SCORES = {
    "owner_relevance": 0.85,
    "expected_utility": 0.65,
    "recurrence": 0.60,
    "confidence": 0.75,
    "engineering_cost": 0.25,
    "operational_risk": 0.10,
}

ADR_2 = "docs/DECISIONS.md#adr-0051-addendum-2"
ADR_4 = "docs/DECISIONS.md#adr-0051-addendum-4"


@contextlib.contextmanager
def _make_session_factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session_scope() -> Iterator[Any]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    try:
        yield session_scope
    finally:
        engine.dispose()


def _backfill_evidence(session_scope) -> dict[str, str]:
    """Record the two incidents and the run that resolved them, from the ADRs."""
    events = [
        ActivityEvent(
            event_type=EVENT_TYPE_INCIDENT_OPENED,
            subsystem=SUBSYSTEM_VOICE,
            action="qualification.harness_failed",
            factual_summary=(
                "Sesli açıklama yeterlilik koşusu, web kabuğu hazır olmadan "
                "oturum beklendiği için üretilen bir metin kontrolünde başarısız "
                "oldu; ürün çalışıyordu, koşum takımı çalışmıyordu."
            ),
            source="backfill",
            source_ref=ADR_2,
            status=STATUS_FAILED,
            severity=SEVERITY_WARNING,
            production_state=PRODUCTION_STATE_NA,
            module="scripts/voice/owner-explain.ps1",
            occurred_at=datetime(2026, 9, 4, 20, 0, tzinfo=UTC),
            detail_json={
                "adr": "ADR-0051 addendum 2",
                "defect_class": "acceptance_depends_on_generated_wording",
                "cost": "one owner qualification run",
            },
        ),
        ActivityEvent(
            event_type=EVENT_TYPE_INCIDENT_OPENED,
            subsystem=SUBSYSTEM_VOICE,
            action="qualification.wording_gate_failed",
            factual_summary=(
                "Çalışan bir sistem, brifing daha kısa ve daha iyi ifade edildiği "
                "için cümle önekini eşleyen kabul kontrolünde başarısız oldu; "
                "kanıt yapısal olmalı, ifade değil."
            ),
            source="backfill",
            source_ref=ADR_4,
            status=STATUS_FAILED,
            severity=SEVERITY_WARNING,
            production_state=PRODUCTION_STATE_NA,
            module="scripts/voice/owner-explain.ps1",
            occurred_at=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
            detail_json={
                "adr": "ADR-0051 addendum 4",
                "defect_class": "acceptance_depends_on_generated_wording",
                "failed_check": "speech_head -like <turkish sentence prefix>",
                "cost": "one owner qualification run",
            },
        ),
        ActivityEvent(
            event_type=EVENT_TYPE_VOICE_EXPLAINED,
            subsystem=SUBSYSTEM_VOICE,
            action="qualification.verify_only_passed",
            factual_summary=(
                "Tamamlanmış oturum, yapısal provenance üzerinden yeniden "
                "doğrulandı: alıntılanan olay kimlikleri, araştırma görevi ve "
                "sayılar eşleşti."
            ),
            source="backfill",
            source_ref=f"{ADR_4}:verify-only",
            status=STATUS_COMPLETED,
            severity=SEVERITY_INFO,
            production_state=PRODUCTION_STATE_NA,
            module="scripts/voice/owner-explain.ps1",
            occurred_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
            detail_json={"adr": "ADR-0051 addendum 4", "mode": "-VerifyOnly"},
        ),
    ]
    refs: dict[str, str] = {}
    with session_scope() as session:
        for key, event in zip(("incident_2", "incident_4", "resolution"), events, strict=True):
            row = record_activity(session, event)
            refs[key] = str(row.event_id)
        session.commit()
    return refs


def _compile_lessons(session_scope) -> list[dict[str, Any]]:
    """Run the REAL Experience Compiler over the backfilled chain."""
    with session_scope() as session:
        candidates = compile_lessons(session, now=NOW, lookback=timedelta(days=30))
        rows = [
            {
                "lesson_id": str(row.lesson_id),
                "title": row.title,
                "scope": row.scope,
                "status": row.status,
                "pattern": getattr(row, "pattern", None),
            }
            for row in session.query(ExperienceLessonRow).all()
        ]
    return [
        {"compiled": len(candidates), "rows": rows},
    ]


def _digest(path: Path) -> dict[str, Any]:
    blob = path.read_bytes()
    return {
        "path": path.relative_to(REPO_ROOT).as_posix(),
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
    }


def _artefacts() -> list[dict[str, Any]]:
    names = [
        CANDIDATE_ROOT / "SPEC.md",
        CANDIDATE_ROOT / "THREAT_MODEL.md",
        CANDIDATE_ROOT / "NOTES.md",
        CANDIDATE_ROOT / "src" / "acceptance_wording_guard.py",
        API_ROOT / "tests" / "unit" / "test_lab_acceptance_wording_guard.py",
    ]
    return [_digest(path) for path in names if path.is_file()]


def _scan_repository() -> dict[str, Any]:
    sys.path.insert(0, str(CANDIDATE_ROOT / "src"))
    import acceptance_wording_guard as awg

    reported = awg.scan_paths(REPO_ROOT, surfaces=awg.DEFAULT_SURFACES)
    with_low = awg.scan_paths(
        REPO_ROOT, surfaces=awg.DEFAULT_SURFACES, min_severity=awg.SEVERITY_LOW
    )
    return {"default": reported.to_dict(), "including_low": with_low.to_dict()}


def main() -> int:
    publisher = get_publisher()
    publisher.reset()

    with _make_session_factory() as session_factory:
        evidence_ids = _backfill_evidence(session_factory)
        lessons = _compile_lessons(session_factory)
        lesson_rows = lessons[0]["rows"]

        evidence_refs: list[dict[str, Any]] = [
            {
                "kind": "ledger_event",
                "ref": evidence_ids["incident_2"],
                "note": "ADR-0051 addendum 2 (2026-09-04) harness defect",
            },
            {
                "kind": "ledger_event",
                "ref": evidence_ids["incident_4"],
                "note": "ADR-0051 addendum 4 (2026-09-05) wording gate",
            },
        ]
        for row in lesson_rows:
            evidence_refs.append(
                {
                    "kind": "lesson",
                    "ref": row["lesson_id"],
                    "note": f"experience compiler: {row['title'][:120]}",
                }
            )

        service = EvolutionService(session_factory)
        opportunity = service.create_from_evidence(
            title=TITLE,
            statement=STATEMENT,
            evidence_refs=evidence_refs,
            scores=SCORES,
            source="ledger_event",
            source_ref=f"{ADR_4}:{evidence_ids['incident_4']}",
            detail={
                "adrs": [ADR_2, ADR_4],
                "defect_class": "acceptance_depends_on_generated_wording",
                "qualification_runs_lost": 2,
            },
        )
        opportunity_id = opportunity["opportunity_id"]

        scan = _scan_repository()
        artefacts = _artefacts()
        workspace = "services/api/lab/candidates/acceptance_wording_guard"

        steps: list[tuple[OpportunityStatus, str, dict[str, Any]]] = [
            (
                OpportunityStatus.RESEARCHING,
                "Surveyed the acceptance surfaces named by the two incidents: "
                f"{scan['default']['files_scanned']} files under "
                f"{', '.join(scan['default']['surfaces'])}.",
                {},
            ),
            (
                OpportunityStatus.DESIGN_READY,
                "SPEC.md and THREAT_MODEL.md written; detection is subject-driven "
                "(a prose literal alone is never a finding) with a reasoned allow-list.",
                {},
            ),
            (
                OpportunityStatus.BUILDING,
                "Implementation written to the lab workspace, outside app/ and "
                "outside every tree app/evolution/sandbox.py protects.",
                {"workspace_ref": workspace},
            ),
            (
                OpportunityStatus.TESTING,
                "tests/unit/test_lab_acceptance_wording_guard.py covers both "
                "historical shapes, machine-token non-detection, the allow-list, "
                "robustness and the no-production-import guard (digest in artefacts).",
                {},
            ),
            (
                OpportunityStatus.EVALUATING,
                "Benchmark: "
                f"{scan['default']['files_scanned']} real files in "
                f"{scan['default']['duration_s']}s; "
                f"{scan['default']['counts']['high']} high, "
                f"{scan['default']['counts']['medium']} medium, "
                f"{len(scan['default']['suppressed'])} suppressed false positives.",
                {},
            ),
            (
                OpportunityStatus.SHADOW_READY,
                "Built, tested, benchmarked, critiqued and explainable; wired to "
                "nothing. Owner approval is the only way past this point.",
                {
                    "candidate_ref": (
                        f"{workspace}/src/acceptance_wording_guard.py"
                        f"@sha256:{artefacts[-2]['sha256'][:16]}"
                    )
                },
            ),
        ]

        transitions: list[dict[str, Any]] = []
        for target, reason, extra in steps:
            updated = service.advance(
                opportunity_id,
                target=target,
                actor=ActorKind.LAB,
                reason=reason,
                **extra,
            )
            transitions.append({"to": updated["status"], "reason": reason[:512]})

        final = service.get(opportunity_id)
        pending = service.pending_owner_actions()

        with session_factory() as session:
            ledger = [
                {
                    "event_id": str(row.event_id),
                    "event_type": row.event_type,
                    "subsystem": row.subsystem,
                    "action": row.action,
                    "status": row.status,
                    "severity": row.severity,
                    "production_state": row.production_state,
                    "source": row.source,
                    "source_ref": row.source_ref,
                    "related_module_id": row.related_module_id,
                    "factual_summary": row.factual_summary,
                    "occurred_at": row.occurred_at.isoformat(),
                }
                for row in session.query(ActivityEventRow)
                .order_by(ActivityEventRow.recorded_at)
                .all()
            ]

    ui_states = [event.as_dict() for event in publisher.tail(limit=200)]

    run = {
        "generated_at": datetime.now(UTC).isoformat(),
        "note": (
            "A real run of app.evolution.service against an ephemeral SQLite "
            "store. Every transition went through the real authority kernel, "
            "the real legal-transition table, the real ledger writer and the "
            "real UI-state bus. The run stops at shadow_ready; nothing is "
            "deployed and no owner approval exists."
        ),
        "evidence_backfilled": evidence_ids,
        "experience_lessons": lessons,
        "opportunity": final,
        "transitions": transitions,
        "ledger_events": ledger,
        "ui_states": ui_states,
        "pending_owner_actions": pending,
        "artefacts": artefacts,
    }

    out_dir = CANDIDATE_ROOT / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "lifecycle_run.json").write_text(
        json.dumps(run, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "repo_scan.json").write_text(
        json.dumps(scan, indent=2, ensure_ascii=True, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    print(f"opportunity {final['opportunity_id']} -> {final['status']}")
    print(f"composite {final['scores']['composite']:.3f}")
    print(f"ledger events {len(ledger)}  ui states {len(ui_states)}")
    print(f"awaiting owner approval: {pending['count']}")
    if final["status"] != str(OpportunityStatus.SHADOW_READY):
        return 1
    if final["approved_by"] is not None:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
