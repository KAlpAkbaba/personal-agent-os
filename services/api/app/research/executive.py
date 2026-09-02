"""M13 executive-assistant structure.

Executive Summary -> Why it matters -> Recommended action -> Details on
demand is ROADMAP M16's default presentation structure; the M13 goal
requires it for the browser-research pipeline specifically, so it is pulled
forward here rather than invented twice. ``ExecutiveReport`` is the
structured form (what the API stores/returns);
:func:`render_executive_markdown` is the Turkish-first canonical body,
mirroring ``app.research.compose.compose_canonical_markdown``'s role for M3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.research.evidence import LabelledStatement


@dataclass(frozen=True, slots=True)
class DetailSection:
    """One "details on demand" section: a heading plus its labelled claims."""

    heading: str
    statements: tuple[LabelledStatement, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "statements": [s.as_dict() for s in self.statements],
        }


@dataclass(frozen=True, slots=True)
class ExecutiveReport:
    """Executive Summary -> Why it matters -> Recommended action -> Details."""

    topic: str
    recency_label: str
    executive_summary: str
    why_it_matters: LabelledStatement
    recommended_action: LabelledStatement
    details: tuple[DetailSection, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "recency_label": self.recency_label,
            "executive_summary": self.executive_summary,
            "why_it_matters": self.why_it_matters.as_dict(),
            "recommended_action": self.recommended_action.as_dict(),
            "details": [d.as_dict() for d in self.details],
        }


def render_executive_markdown(report: ExecutiveReport) -> str:
    """Canonical Turkish-first Markdown body (M3's artifact storage shape)."""
    lines: list[str] = []
    lines.append(f"# Araştırma Raporu: {report.topic}")
    lines.append("")
    lines.append(f"_Kapsam: {report.recency_label}_")
    lines.append("")
    lines.append("## Yönetici Özeti (Executive Summary)")
    lines.append("")
    lines.append(report.executive_summary)
    lines.append("")
    lines.append("## Neden Önemli (Why it matters)")
    lines.append("")
    lines.append(f"[{report.why_it_matters.label}] {report.why_it_matters.text}")
    lines.append("")
    lines.append("## Önerilen Eylem (Recommended action)")
    lines.append("")
    lines.append(f"[{report.recommended_action.label}] {report.recommended_action.text}")
    lines.append("")
    lines.append("## Ayrıntılar (Details on demand)")
    lines.append("")
    if not report.details:
        lines.append("(Ayrıntı yok.)")
        lines.append("")
    for section in report.details:
        lines.append(f"### {section.heading}")
        lines.append("")
        for statement in section.statements:
            urls = ", ".join(statement.evidence_urls) if statement.evidence_urls else "yok"
            lines.append(f"- [{statement.label}] {statement.text} (kaynak: {urls})")
        lines.append("")
    return "\n".join(lines)


__all__ = ["DetailSection", "ExecutiveReport", "render_executive_markdown"]
