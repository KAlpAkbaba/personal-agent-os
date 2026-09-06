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
    STATEMENT_LABEL_MODEL_INFERENCE,
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
    #: How sure the synthesis provider is of this finding given the evidence it cites (0..1).
    confidence: float = 0.5
    #: Publication/event dates of the cited evidence, filled by the pipeline (derived).
    dates: dict[str, Any] | None = None

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
            "confidence": self.confidence,
            "dates": self.dates,
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
    #: Text fields a synthesis provider's output had to be truncated on to
    #: stay within the per-field length caps (finding MEDIUM-7,
    #: app.research.synthesis.parse_synthesis_response).
    truncated_fields: int = 0
    #: Fetched pages the pre-synthesis quality gate refused as evidence.
    rejected: int = 0
    #: Those refusals by reason (off_topic, outside_recency_window, date_uncertain,
    #: interstitial, duplicate_event, insufficient_content). The report shows them so a
    #: thin answer can be told apart from a thin web.
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    #: Evidence/synthesis-output items quarantined for breaking their field contract
    #: (app.research.contracts.ContractViolation) at any stage — never silently
    #: dropped, never a reason to fail a run whose result is otherwise defensible.
    #: Diagnostics-only (app.research.result.ResearchDiagnostics): the executive
    #: narration never mentions this number (M18.2 DEFECT 2, ADR-0067).
    quarantined: int = 0
    #: M18.2 fast-path fields (ADR-0068), diagnostics-only like everything above:
    #: which speed mode this run used, its hard budget, how long it actually took,
    #: how many fetch waves it spent, and the challenge policy's own counters.
    mode: str = ""
    budget_s: float = 0.0
    elapsed_s: float = 0.0
    waves: int = 0
    challenged_pages: int = 0
    cooled_domains: int = 0
    #: ADR-0074: this run produced a defensible but THIN answer (at least one finding,
    #: fewer than app.research.contracts.MIN_REPORT_FINDINGS) and says so in its own
    #: executive summary. ``thin_reasons`` names why in the diagnostics vocabulary
    #: (app.research.synthesis.THIN_REASON_*), never in the words the owner hears.
    thin: bool = False
    thin_reasons: tuple[str, ...] = field(default_factory=tuple)

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
            "truncated_fields": self.truncated_fields,
            "rejected": self.rejected,
            "rejected_by_reason": dict(self.rejected_by_reason),
            "quarantined": self.quarantined,
            "mode": self.mode,
            "budget_s": self.budget_s,
            "elapsed_s": self.elapsed_s,
            "waves": self.waves,
            "challenged_pages": self.challenged_pages,
            "cooled_domains": self.cooled_domains,
            "thin": self.thin,
            "thin_reasons": list(self.thin_reasons),
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
    #: ADR-0074: a READY report that carries fewer than ``MIN_REPORT_FINDINGS``
    #: findings and says so. Top-level (not only in ``stats``) because the explain
    #: engine, the voice terminal payload and the harness all branch on it, and
    #: none of them should have to read the diagnostics block to learn it.
    thin: bool = False
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
            "thin": self.thin,
        }


def assign_evidence_ids(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Give every record a citation id, keeping the ones already assigned.

    A record that already carries an id keeps it, so a workflow activity replay does not
    renumber the sources a report already cites. New records take the next id NOT already
    in use rather than their position in the list: ranking runs again when a top-up round
    adds evidence, and positional numbering handed the newcomer at the front an id an older
    record still held - the live run of 2026-09-04 produced two different sources both
    answering to "[e2]", which makes every citation of it unverifiable.
    """
    taken = {r.id for r in records if r.id}
    out: list[EvidenceRecord] = []
    next_number = 1
    for record in records:
        if record.id:
            out.append(record)
            continue
        while f"e{next_number}" in taken:
            next_number += 1
        new_id = f"e{next_number}"
        taken.add(new_id)
        out.append(replace(record, id=new_id))
    return out


def iter_statements(report: ResearchReport) -> Iterator[Statement]:
    """Every citable statement, findings included (wrapped as ``Statement``)."""
    for f in report.findings:
        yield Statement(
            text=f.summary,
            label=f.label,
            evidence_ids=f.evidence_ids,
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


def _strip_unknown_ids(
    label: str, evidence_ids: tuple[str, ...], evidence_ids_known: set[str]
) -> tuple[str, tuple[str, ...], str | None]:
    unknown = [i for i in evidence_ids if i not in evidence_ids_known]
    if not unknown:
        return label, evidence_ids, None
    kept = tuple(i for i in evidence_ids if i in evidence_ids_known)
    note = f"cited unknown evidence id(s) removed: {sorted(set(unknown))}"
    if label == STATEMENT_LABEL_SOURCE_FACT and not kept:
        # A source_fact whose citations become empty once fabricated ids are
        # removed is downgraded, never dropped (finding MEDIUM-4) — the same
        # never-drop-only-relabel discipline apply_excerpt_overlap_downgrade
        # already follows for excerpt-unsupported source_facts below.
        label = STATEMENT_LABEL_MODEL_INFERENCE
    return label, kept, note


def strip_dangling_citations(report: ResearchReport, evidence_ids: set[str]) -> ResearchReport:
    """Every labelled statement/finding — all four labels, not only
    ``source_fact`` — may only cite evidence ids the pipeline actually
    gathered (finding MEDIUM-4). An id a synthesis provider invents (most
    dangerously: a model reading untrusted page text and fabricating a
    citation) is removed from the citation list and the removal is recorded
    via ``provenance_note``, rather than the whole research run crashing over
    one bad citation — that hard-failure behaviour is still available via
    :func:`require_source_fact_provenance` called directly, but
    ``run_provenance_gate`` (the pipeline's own gate) never lets a dangling
    id reach the rendered report: any ``[eN]`` marker in the Markdown always
    has a matching Sources entry."""

    def fix_statement(s: Statement) -> Statement:
        label, ids, note = _strip_unknown_ids(s.label, s.evidence_ids, evidence_ids)
        if note is None:
            return s
        return replace(s, label=label, evidence_ids=ids, provenance_note=note)

    def fix_finding(f: Finding) -> Finding:
        label, ids, note = _strip_unknown_ids(f.label, f.evidence_ids, evidence_ids)
        if note is None:
            return f
        return replace(f, label=label, evidence_ids=ids, provenance_note=note)

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
    """The full pipeline-owned gate: strip any dangling citation first (so a
    fabricated id never reaches the rendered report), hard-require a
    source_fact that still cites nothing at all, then soft-downgrade
    excerpt-unsupported ``source_fact`` statements."""
    report = strip_dangling_citations(report, set(evidence_by_id))
    require_source_fact_provenance(report, set(evidence_by_id))
    return apply_excerpt_overlap_downgrade(report, evidence_by_id)


#: Turkish labels for the quality gate's rejection reasons (app.research.eligibility).
_REJECTION_LABELS_TR = {
    "off_topic": "konu dışı",
    "outside_recency_window": "zaman aralığı dışında",
    "date_uncertain": "yayın tarihi doğrulanamadı",
    "interstitial": "ara sayfa / doğrulama sayfası",
    "duplicate_event": "aynı gelişmenin tekrarı",
    "insufficient_content": "yeterli içerik yok",
}


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

    if report.stats.rejected:
        # The owner has to be able to tell a thin answer from a thin web: these are the
        # pages that were found and fetched but refused as evidence, and why.
        lines.append("## Elenen Kaynaklar (Quality gate)")
        lines.append("")
        lines.append(
            f"Bulunan sayfalardan {report.stats.rejected} tanesi kanıt olarak kabul edilmedi:"
        )
        for reason, count in sorted(report.stats.rejected_by_reason.items()):
            lines.append(f"- {_REJECTION_LABELS_TR.get(reason, reason)} ({reason}): {count}")
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
    "strip_dangling_citations",
]
