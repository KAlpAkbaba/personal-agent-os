"""M13 top-level research pipeline: plan -> gather -> dedup/rank -> synthesize.

This is a SEPARATE entry point from ``app.research.provider.ResearchProvider``
/ ``app.research.workflow.ResearchWorkflow`` (M3): M3's contract is
``gather() -> list[SourceRecord]`` composed into flat Markdown, which is what
the M3 acceptance gate exercises and must keep working unmodified. M13 needs
richer, provenance-complete, labelled, executive-structured output — a
different shape — so it gets its own provider/pipeline rather than
overloading M3's. Wiring this into a durable Temporal workflow (mirroring
``ResearchWorkflow``'s plan/gather/compose/render activities, so a worker
restart cannot lose an in-flight browser research task either) is the
natural next step once ``BrowserGateway`` is real (ADR-0035); this module is
the piece such a workflow's activities would call into.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.research.browser_gateway import BrowserGateway, FetchQuery, UnwiredBrowserGateway
from app.research.evidence import EvidenceRecord, dedup_and_rank
from app.research.executive import ExecutiveReport, render_executive_markdown
from app.research.plan import ResearchPlan, build_plan
from app.research.synthesis import DeterministicSynthesisProvider, SynthesisProvider


@dataclass(frozen=True, slots=True)
class BrowserResearchResult:
    """Everything one M13 pipeline run produced, plan through report."""

    plan: ResearchPlan
    evidence: tuple[EvidenceRecord, ...]
    report: ExecutiveReport

    @property
    def evidence_count(self) -> int:
        return len(self.evidence)

    def canonical_markdown(self) -> str:
        return render_executive_markdown(self.report)


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

    def run(self, topic: str, *, now: datetime | None = None) -> BrowserResearchResult:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

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
        report = self._synthesis.synthesize(
            plan.topic, ranked, recency_label=plan.recency.label
        )
        return BrowserResearchResult(plan=plan, evidence=tuple(ranked), report=report)


__all__ = ["BrowserResearchProvider", "BrowserResearchResult"]
