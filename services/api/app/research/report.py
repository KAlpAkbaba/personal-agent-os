"""ResearchReport (M13_RESEARCH_SPEC.md §3): the stored/returned report shape.

Executive Summary -> Findings (3-7) -> Why it matters -> What I would watch
next -> Details (collapsed) -> Sources, JSON ``schema_version`` 1. This
supersedes ``app.research.executive.ExecutiveReport`` (the earlier, simpler
M13-skeleton shape) — the spec's report carries multiple findings/why-it-
matters/watch-next entries and cites evidence by ``id`` (``sources[].id``,
e.g. ``"e3"``) rather than by URL, so statements here use
:class:`Statement` (``evidence_ids``), not
``app.research.evidence.LabelledStatement`` (``evidence_urls``).

The provenance gate is two steps, both pipeline-owned (never delegated to a
synthesis provider's self-report):

1. :func:`require_source_fact_provenance` — a ``source_fact`` MUST cite at
   least one evidence id that was actually gathered; citing nothing, or
   citing an id the pipeline never produced, is a hard :class:`ProvenanceError`
   (a synthesis provider — especially a model-backed one reading untrusted
   page text — must not be able to fabricate a citation).
2. :func:`apply_excerpt_overlap_downgrade` — a ``source_fact`` that cites
   real evidence but shares no content word with any of its cited excerpts is
   downgraded to ``model_inference`` (never dropped silently); the reason is
   recorded on the statement/finding as ``provenance_note``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Any

from app.research.evidence import (
    STATEMENT_LABEL_SOURCE_FACT,
    STATEMENT_LABELS,
    EvidenceRecord,
    validate_label,
)

SCHEMA_VERSION = 1
MIN_FINDINGS = 3
MAX_FINDINGS = 7

_CONTENT_WORD_RE = re.compile(r"[a-z0-9çğıöşü]+")
_MIN_CONTENT_WORD_LEN = 3


class ProvenanceError(ValueError):
    """A ``source_fact`` does not cite evidence the pipeline actually gathered."""


def _content_words(text: str) -> set[str]:
    return {w for w in _CONTENT_WORD_RE.findall(text.lower()) if len(w) >= _MIN_CONTENT_WORD_LEN}


def _excerpt_supports(statement_text: str, cited_excerpts: list[str]) -> bool:
    """True when the statement shares >=1 content word with its citations."""
    stmt_words = _content_words(statement_text)
    if not stmt_words:
        return True
    combined = _content_words(" ".join(cited_excerpts))
    return bool(stmt_words & combined)


@dataclass(frozen=True, slots=True)
class Statement:
    """A why-it-matters / watch-next / detail / uncertainty entry.

    Cites evidence by ``id`` (``sources[].id``), unlike
    ``app.research.evidence.LabelledStatement`` (URL-based, used by the older
    ``ExecutiveReport`` shape).
    """

    text: str
    label: str
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    provenance_note: str | None = None

    def __post_init__(self) -> None:
        validate_label(self.label)
        if not self.text or not self.text.strip():
            raise ValueError("statement text must be a non-empty string")

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "text": self.text,
            "label": self.label,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.provenance_note:
            out["provenance_note"] = self.provenance_note
        return out


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    title: str
    summary: str
    why_it_matters: str
    importance: int
    label: str
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    first_seen: str | None = None
    provenance_note: str | None = None

    def __post_init__(self) -> None:
        validate_label(self.label)
        if not (1 <= self.importance <= 5):
            raise ValueError(f"importance must be 1-5, got {self.importance!r}")
        if not self.title.strip() or not self.summary.strip():
            raise ValueError("finding title/summary must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "why_it_matters": self.why_it_matters,
            "importance": self.importance,
            "label": self.label,
            "evidence_ids": list(self.evidence_ids),
            "first_seen": self.first_seen,
        }
        if self.provenance_note:
            out["provenance_note"] = self.provenance_note
        return out


@dataclass(frozen=True, slots=True)
class DetailSection:
    heading: str
    statements: tuple[Statement, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {"heading": self.heading, "statements": [s.as_dict() for s in self.statements]}


@dataclass(frozen=True, slots=True)
class SourceItem:
    id: str
    url: str
    final_url: str
    title: str
    publisher: str
    source_class: str
    published_at: str | None
    retrieved_at: str | None
    excerpt: str
    device_id: str | None = None
    command_id: str | None = None
    injection_suspected: bool = False
    syndicated_of: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "final_url": self.final_url,
            "title": self.title,
            "publisher": self.publisher,
            "source_class": self.source_class,
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "excerpt": self.excerpt,
            "device_id": self.device_id,
            "command_id": self.command_id,
            "injection_suspected": self.injection_suspected,
            "syndicated_of": self.syndicated_of,
        }

    @classmethod
    def from_evidence(cls, evidence_id: str, record: EvidenceRecord) -> SourceItem:
        return cls(
            id=evidence_id,
            url=record.url,
            final_url=record.final_url or record.url,
            title=record.title,
            publisher=record.publisher or record.source_class,
            source_class=record.source_class,
            published_at=record.published_at.isoformat() if record.published_at else None,
            retrieved_at=record.retrieved_at.isoformat() if record.retrieved_at else None,
            excerpt=record.excerpt,
            device_id=record.device_id,
            command_id=record.command_id,
            injection_suspected=record.injection_suspected,
            syndicated_of=record.syndicated_of,
        )


@dataclass(frozen=True, slots=True)
class ReportWindow:
    start: str
    end: str
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "label": self.label}


@dataclass(frozen=True, slots=True)
class ReportStats:
    queries: int = 0
    discovered: int = 0
    fetched: int = 0
    fetch_failed: int = 0
    deduplicated: int = 0
    evidence: int = 0
    injection_suspected_evidence: int = 0
    injection_dropped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": self.queries,
            "discovered": self.discovered,
            "fetched": self.fetched,
            "fetch_failed": self.fetch_failed,
            "deduplicated": self.deduplicated,
            "evidence": self.evidence,
            "injection_suspected_evidence": self.injection_suspected_evidence,
            "injection_dropped": self.injection_dropped,
        }


@dataclass(frozen=True, slots=True)
class ResearchReport:
    task_id: str
    topic: str
    window: ReportWindow
    generated_at: str
    synthesis_provider: str
    executive_summary: str
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    why_it_matters: tuple[Statement, ...] = field(default_factory=tuple)
    watch_next: tuple[Statement, ...] = field(default_factory=tuple)
    details: tuple[DetailSection, ...] = field(default_factory=tuple)
    uncertainty: tuple[Statement, ...] = field(default_factory=tuple)
    sources: tuple[SourceItem, ...] = field(default_factory=tuple)
    stats: ReportStats = field(default_factory=ReportStats)
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "topic": self.topic,
            "window": self.window.as_dict(),
            "generated_at": self.generated_at,
            "synthesis_provider": self.synthesis_provider,
            "executive_summary": self.executive_summary,
            "findings": [f.as_dict() for f in self.findings],
            "why_it_matters": [s.as_dict() for s in self.why_it_matters],
            "watch_next": [s.as_dict() for s in self.watch_next],
            "details": [d.as_dict() for d in self.details],
            "uncertainty": [s.as_dict() for s in self.uncertainty],
            "sources": [s.as_dict() for s in self.sources],
            "stats": self.stats.as_dict(),
        }


def assign_evidence_ids(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Stable ``e1..eN`` ids in list order (the pipeline always passes
    already-ranked evidence). A record that already carries an id keeps it —
    idempotent across a workflow activity replay."""
    return [replace(r, id=r.id or f"e{i + 1}") for i, r in enumerate(records)]


def iter_statements(report: ResearchReport) -> Iterator[Statement]:
    """Every citable statement, findings included (wrapped as ``Statement``)."""
    for f in report.findings:
        yield Statement(
            text=f.summary, label=f.label, evidence_ids=f.evidence_ids,
            provenance_note=f.provenance_note,
        )
    yield from report.why_it_matters
    yield from report.watch_next
    for section in report.details:
        yield from section.statements
    yield from report.uncertainty


def require_source_fact_provenance(report: ResearchReport, evidence_ids: set[str]) -> None:
    """Every ``source_fact`` must cite >=1 evidence id the pipeline gathered."""
    for statement in iter_statements(report):
        if statement.label != STATEMENT_LABEL_SOURCE_FACT:
            continue
        if not statement.evidence_ids:
            raise ProvenanceError(f"source_fact without any citation: {statement.text[:80]!r}")
        foreign = set(statement.evidence_ids) - evidence_ids
        if foreign:
            raise ProvenanceError(
                f"source_fact cites evidence ids that were never gathered: {sorted(foreign)}"
            )


def _downgrade_label_note(
    text: str, label: str, evidence_ids: tuple[str, ...], evidence_by_id: dict[str, EvidenceRecord]
) -> tuple[str, str | None]:
    if label != STATEMENT_LABEL_SOURCE_FACT or not evidence_ids:
        return label, None
    excerpts = [evidence_by_id[e].excerpt for e in evidence_ids if e in evidence_by_id]
    if _excerpt_supports(text, excerpts):
        return label, None
    return (
        "model_inference",
        "excerpt(s) share no content word with this statement; downgraded from source_fact",
    )


def apply_excerpt_overlap_downgrade(
    report: ResearchReport, evidence_by_id: dict[str, EvidenceRecord]
) -> ResearchReport:
    """Downgrade an uncited-in-substance ``source_fact`` to ``model_inference``
    (spec §1). Never raises, never drops the statement — only relabels it and
    records why via ``provenance_note``."""

    def fix_finding(f: Finding) -> Finding:
        label, note = _downgrade_label_note(f.summary, f.label, f.evidence_ids, evidence_by_id)
        if note is None:
            return f
        return replace(f, label=label, provenance_note=note)

    def fix_statement(s: Statement) -> Statement:
        label, note = _downgrade_label_note(s.text, s.label, s.evidence_ids, evidence_by_id)
        if note is None:
            return s
        return replace(s, label=label, provenance_note=note)

    return replace(
        report,
        findings=tuple(fix_finding(f) for f in report.findings),
        why_it_matters=tuple(fix_statement(s) for s in report.why_it_matters),
        watch_next=tuple(fix_statement(s) for s in report.watch_next),
        details=tuple(
            replace(d, statements=tuple(fix_statement(s) for s in d.statements))
            for d in report.details
        ),
        uncertainty=tuple(fix_statement(s) for s in report.uncertainty),
    )


def run_provenance_gate(
    report: ResearchReport, evidence_by_id: dict[str, EvidenceRecord]
) -> ResearchReport:
    """The full pipeline-owned gate: hard-require citations exist, then
    soft-downgrade unsupported ``source_fact`` statements."""
    require_source_fact_provenance(report, set(evidence_by_id))
    return apply_excerpt_overlap_downgrade(report, evidence_by_id)


def render_research_markdown(report: ResearchReport) -> str:
    """Canonical Turkish-first Markdown body with ``[eN]`` citation markers and
    a Sources section sharing the same ids (spec §3)."""
    lines: list[str] = []
    lines.append(f"# Araştırma Raporu: {report.topic}")
    lines.append("")
    lines.append(f"_Kapsam: {report.window.label}_")
    lines.append("")
    lines.append("## Yönetici Özeti (Executive Summary)")
    lines.append("")
    lines.append(report.executive_summary)
    lines.append("")

    lines.append("## Öne Çıkan Bulgular (Findings)")
    lines.append("")
    if not report.findings:
        lines.append("(Bulgu bulunamadı.)")
    for f in report.findings:
        markers = "".join(f"[{eid}]" for eid in f.evidence_ids) or "[]"
        lines.append(f"### {f.title} (önem: {f.importance}/5) {markers}")
        lines.append("")
        lines.append(f"[{f.label}] {f.summary}")
        lines.append("")
        lines.append(f"_Neden önemli:_ {f.why_it_matters}")
        lines.append("")
    lines.append("## Neden Önemli (Why it matters)")
    lines.append("")
    for s in report.why_it_matters:
        markers = "".join(f"[{eid}]" for eid in s.evidence_ids)
        lines.append(f"- [{s.label}] {s.text} {markers}")
    if not report.why_it_matters:
        lines.append("(Yok.)")
    lines.append("")

    lines.append("## Takip Edilecekler (What I would watch next)")
    lines.append("")
    for s in report.watch_next:
        lines.append(f"- [{s.label}] {s.text}")
    if not report.watch_next:
        lines.append("(Yok.)")
    lines.append("")

    lines.append("## Ayrıntılar (Details, collapsed)")
    lines.append("")
    if not report.details:
        lines.append("(Ayrıntı yok.)")
    for section in report.details:
        lines.append(f"### {section.heading}")
        lines.append("")
        for s in section.statements:
            markers = "".join(f"[{eid}]" for eid in s.evidence_ids) or "[]"
            lines.append(f"- [{s.label}] {s.text} {markers}")
        lines.append("")

    if report.uncertainty:
        lines.append("## Belirsizlikler (Uncertainty)")
        lines.append("")
        for s in report.uncertainty:
            lines.append(f"- [{s.label}] {s.text}")
        lines.append("")

    lines.append("## Kaynaklar (Sources)")
    lines.append("")
    if not report.sources:
        lines.append("(Kaynak yok.)")
    for s in report.sources:
        flag = " (şüpheli içerik)" if s.injection_suspected else ""
        lines.append(f"[{s.id}] {s.title} — {s.url} ({s.publisher}, {s.source_class}){flag}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "MAX_FINDINGS",
    "MIN_FINDINGS",
    "SCHEMA_VERSION",
    "STATEMENT_LABELS",
    "DetailSection",
    "Finding",
    "ProvenanceError",
    "ReportStats",
    "ReportWindow",
    "ResearchReport",
    "SourceItem",
    "Statement",
    "apply_excerpt_overlap_downgrade",
    "assign_evidence_ids",
    "iter_statements",
    "render_research_markdown",
    "require_source_fact_provenance",
    "run_provenance_gate",
]
