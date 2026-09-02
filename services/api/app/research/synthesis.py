"""M13 synthesis provider seam: ranked evidence -> executive-assistant structure.

Mirrors the provider-seam pattern already used throughout the project
(``ResearchProvider``/M3, ``CodingBackend``/M6, ``SkillGenerator``/M7,
``TTSProvider``/M4): a Protocol the composer depends on, a fully
deterministic implementation for tests/offline/CI/the acceptance gate, and a
model-backed implementation that is INERT — raises a typed error before any
I/O — until the owner configures it. Nothing here hardcodes a vendor
(constitution: "third-party services must be behind provider interfaces").

Claude models are this project's stated default choice for real backends
once configured (mirrors ``ClaudeCodingBackend``/ADR-0024's
"vendor path exists but is inert in tests" discipline); the seam is what
lets that choice change later without touching ``BrowserResearchProvider``.
"""

from __future__ import annotations

import subprocess
from typing import Protocol, runtime_checkable

from app.research.evidence import (
    STATEMENT_LABEL_MODEL_INFERENCE,
    STATEMENT_LABEL_RECOMMENDATION,
    STATEMENT_LABEL_SOURCE_FACT,
    STATEMENT_LABEL_UNCERTAINTY,
    EvidenceRecord,
    LabelledStatement,
)
from app.research.executive import DetailSection, ExecutiveReport

MIN_SOURCES_FOR_CONFIDENCE = 2
_SUBPROCESS_TIMEOUT_S = 60.0


class SynthesisNotConfiguredError(RuntimeError):
    """Raised by an inert (unconfigured) SynthesisProvider before any I/O."""


@runtime_checkable
class SynthesisProvider(Protocol):
    """Turn ranked, deduplicated evidence into an ``ExecutiveReport``."""

    name: str

    def synthesize(
        self, topic: str, evidence: list[EvidenceRecord], *, recency_label: str
    ) -> ExecutiveReport: ...


class DeterministicSynthesisProvider:
    """Seeded, offline synthesis: no model call, fully reproducible.

    Every ``source_fact`` statement it emits quotes exactly one
    ``EvidenceRecord``'s excerpt and cites that record's URL — there is never
    a factual claim without a matching ``evidence_urls`` entry. ``uncertainty``
    is the only label allowed to carry no provenance (an absence of sources
    has nothing to cite). This is what unit tests, any offline pipeline run,
    and the M13 acceptance gate exercise.
    """

    name = "deterministic"

    def synthesize(
        self, topic: str, evidence: list[EvidenceRecord], *, recency_label: str
    ) -> ExecutiveReport:
        topic = topic.strip()
        if not evidence:
            return self._no_evidence_report(topic, recency_label)

        top = evidence[: min(3, len(evidence))]
        highlights = "; ".join(f"{e.title} ({e.source_class}, skor {e.score:.2f})" for e in top)
        executive_summary = (
            f"'{topic}' konusunda {recency_label} kapsamında {len(evidence)} kaynak "
            f"incelendi. Öne çıkanlar: {highlights}."
        )

        why_it_matters = LabelledStatement(
            text=(
                f"'{topic}' başlığı altındaki bu gelişmeler {recency_label} içinde "
                f"{len(evidence)} farklı kaynakta doğrulandı; bu, konunun güncel ve "
                "takip edilmeye değer olduğuna işaret ediyor."
            ),
            label=STATEMENT_LABEL_MODEL_INFERENCE,
            evidence_urls=tuple(e.url for e in top),
        )
        recommended_action = LabelledStatement(
            text=(
                "Ayrıntılar bölümündeki kaynakları, en yüksek güvenilirlik "
                f"skoruna sahip olandan ('{top[0].title}') başlayarak gözden geçirin."
            ),
            label=STATEMENT_LABEL_RECOMMENDATION,
            evidence_urls=(top[0].url,),
        )

        details = [
            DetailSection(
                heading=f"{e.rank}. {e.title}",
                statements=(
                    LabelledStatement(
                        text=e.excerpt,
                        label=STATEMENT_LABEL_SOURCE_FACT,
                        evidence_urls=(e.url,),
                    ),
                ),
            )
            for e in evidence
        ]
        if len(evidence) < MIN_SOURCES_FOR_CONFIDENCE:
            details.append(
                DetailSection(
                    heading="Kapsam Uyarısı",
                    statements=(
                        LabelledStatement(
                            text=(
                                "Bu konuda yalnızca sınırlı sayıda kaynak doğrulanabildi; "
                                "bulgular tek bir kaynağa dayanıyor olabilir ve bağımsız "
                                "biçimde teyit edilmemiştir."
                            ),
                            label=STATEMENT_LABEL_UNCERTAINTY,
                            evidence_urls=tuple(e.url for e in evidence),
                        ),
                    ),
                )
            )

        return ExecutiveReport(
            topic=topic,
            recency_label=recency_label,
            executive_summary=executive_summary,
            why_it_matters=why_it_matters,
            recommended_action=recommended_action,
            details=tuple(details),
        )

    def _no_evidence_report(self, topic: str, recency_label: str) -> ExecutiveReport:
        summary = f"'{topic}' konusunda {recency_label} kapsamında doğrulanmış kaynak bulunamadı."
        no_source = LabelledStatement(text=summary, label=STATEMENT_LABEL_UNCERTAINTY)
        return ExecutiveReport(
            topic=topic,
            recency_label=recency_label,
            executive_summary=summary,
            why_it_matters=no_source,
            recommended_action=LabelledStatement(
                text=(
                    "Zaman penceresini genişletmek (ör. 'son 3 gün' yerine 'son 1 hafta') "
                    "veya farklı anahtar kelimeler denemek önerilir."
                ),
                label=STATEMENT_LABEL_RECOMMENDATION,
            ),
            details=(),
        )


class ClaudeSynthesisProvider:
    """Claude-backed synthesis — a configured-later seam.

    Without ``cli_path`` every method raises a typed
    ``SynthesisNotConfiguredError`` BEFORE any I/O, so the vendor path
    exists but is inert in tests (mirrors ``ClaudeCodingBackend``'s
    ``backend_not_configured`` discipline, ADR-0024). Domain code only ever
    sees ``ExecutiveReport``/``LabelledStatement``, never a raw model
    response.
    """

    name = "claude"

    def __init__(self, cli_path: str = "", model: str = "") -> None:
        self.cli_path = cli_path
        self.model = model

    def _require_configured(self) -> None:
        if not self.cli_path:
            raise SynthesisNotConfiguredError(
                "Claude synthesis provider is not configured "
                "(set PAGENTOS_RESEARCH_CLAUDE_CLI to the Claude CLI path)"
            )

    def build_command(self, prompt: str) -> list[str]:
        """Pure command construction (unit-testable without invocation)."""
        command = [self.cli_path, "-p", prompt, "--output-format", "json"]
        if self.model:
            command += ["--model", self.model]
        return command

    def synthesize(
        self, topic: str, evidence: list[EvidenceRecord], *, recency_label: str
    ) -> ExecutiveReport:
        self._require_configured()
        raise NotImplementedError(  # pragma: no cover - real invocation, not exercised in tests
            "ClaudeSynthesisProvider.synthesize is a configured-later seam; wire the "
            "structured-output prompt and response parsing when the owner supplies "
            "PAGENTOS_RESEARCH_CLAUDE_CLI."
        )

    def _invoke(self, prompt: str) -> str:  # pragma: no cover - real invocation only
        self._require_configured()
        proc = subprocess.run(  # noqa: S603 - owner-configured CLI
            self.build_command(prompt),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
        if proc.returncode != 0:
            raise SynthesisNotConfiguredError(
                f"Claude CLI exited {proc.returncode}: {proc.stderr[:500]}"
            )
        return proc.stdout


__all__ = [
    "ClaudeSynthesisProvider",
    "DeterministicSynthesisProvider",
    "SynthesisNotConfiguredError",
    "SynthesisProvider",
]
