"""M13 top-level research pipeline: plan -> gather -> dedup/rank -> synthesize.

This is a SEPARATE entry point from ``app.research.provider.ResearchProvider``
/ ``app.research.workflow.ResearchWorkflow`` (M3): M3's contract is
``gather() -> list[SourceRecord]`` composed into flat Markdown, which is what
the M3 acceptance gate exercises and must keep working unmodified. M13 needs
richer, provenance-complete, labelled, executive-structured output — a
different shape — so it gets its own provider/pipeline rather than
overloading M3's.

This module is a fully offline, deterministic COMPOSITION of the pipeline
pieces (:mod:`app.research.plan`, :mod:`app.research.evidence`,
:mod:`app.research.report`, :mod:`app.research.synthesis`) for tests, demos
and the acceptance gate. The REAL durable path is
``app.research.workflow.BrowserResearchWorkflow`` + ``app.research.activities``
(Temporal), whose activity bodies call into the same pipeline pieces plus
device dispatch and persistence this module deliberately does not need.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.research.browser_gateway import BrowserGateway, FetchQuery, UnwiredBrowserGateway
from app.research.evidence import EvidenceRecord, dedup_and_rank
from app.research.plan import ResearchPlan, build_plan
from app.research.report import (
    ReportStats,
    ReportWindow,
    ResearchReport,
    SourceItem,
    assign_evidence_ids,
    render_research_markdown,
    run_provenance_gate,
)
from app.research.synthesis import (
    DeterministicSynthesisProvider,
    SynthesisProvider,
)


@dataclass(frozen=True, slots=True)
class BrowserResearchResult:
    """Everything one M13 pipeline run produced, plan through report."""

    plan: ResearchPlan
    evidence: tuple[EvidenceRecord, ...]
    report: ResearchReport

    @property
    def evidence_count(self) -> int:
        return len(self.evidence)

    def canonical_markdown(self) -> str:
        return render_research_markdown(self.report)


class BrowserResearchProvider:
    """Orchestrates the full M13 pipeline (offline-testable with fakes).

    ``gateway`` defaults to :class:`UnwiredBrowserGateway` deliberately: a
    provider constructed with no arguments should fail loudly the moment it
    is actually run, not silently produce an empty report. Tests and any
    offline demo inject :class:`~app.research.browser_gateway.FakeBrowserGateway`
    explicitly.
    """

    name = "browser"

    def __init__(
        self,
        gateway: BrowserGateway | None = None,
        *,
        synthesis: SynthesisProvider | None = None,
        enrollment_id: str | None = None,
    ) -> None:
        self._gateway = gateway or UnwiredBrowserGateway()
        self._synthesis = synthesis or DeterministicSynthesisProvider()
        self._enrollment_id = enrollment_id

    def run(
        self, topic: str, *, task_id: str = "", now: datetime | None = None
    ) -> BrowserResearchResult:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        task_id = task_id or f"offline-{abs(hash(topic))}"

        plan = build_plan(topic, now=now)
        queries = [
            FetchQuery(
                query=query,
                source_class=source_class,
                max_results=plan.max_sources_per_query,
            )
            for query in plan.queries
            for source_class in plan.source_classes
        ]

        raw = self._gateway.fetch_evidence(queries, enrollment_id=self._enrollment_id, now=now)
        ranked = dedup_and_rank(
            raw,
            topic=plan.topic,
            window_start=plan.recency.start,
            window_end=plan.recency.end,
        )
        ranked = assign_evidence_ids(ranked)
        primary_only = [e for e in ranked if not e.syndicated_of]

        result = self._synthesis.synthesize(
            plan.topic, primary_only, recency_label=plan.recency.label
        )

        evidence_by_id = {e.id: e for e in ranked}
        stats = ReportStats(
            queries=len(queries),
            discovered=len(raw),
            fetched=len(raw),
            fetch_failed=0,
            deduplicated=len(raw) - len(ranked),
            evidence=len(ranked),
        )
        report = ResearchReport(
            task_id=task_id,
            topic=plan.topic,
            window=ReportWindow(
                start=plan.recency.start.isoformat(),
                end=plan.recency.end.isoformat(),
                label=plan.recency.label,
            ),
            generated_at=now.isoformat(),
            synthesis_provider=self._synthesis.name,
            executive_summary=result.executive_summary,
            findings=result.findings,
            why_it_matters=result.why_it_matters,
            watch_next=result.watch_next,
            details=result.details,
            uncertainty=result.uncertainty,
            sources=tuple(SourceItem.from_evidence(e.id, e) for e in ranked),
            stats=stats,
        )
        # Pipeline-owned, provider-independent: no synthesis backend gets to
        # decide for itself whether its "facts" are cited (ProvenanceError) or
        # actually supported by the excerpt it cites (excerpt-overlap downgrade).
        report = run_provenance_gate(report, evidence_by_id)
        return BrowserResearchResult(plan=plan, evidence=tuple(ranked), report=report)


__all__ = ["BrowserResearchProvider", "BrowserResearchResult"]
