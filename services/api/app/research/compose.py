"""Pure composition: score/dedup sources and build the canonical Markdown.

Everything here is deterministic and side-effect free (no I/O, no clock, no
randomness) so it can be unit-tested in isolation and produce a stable
content_hash. The Temporal workflow calls these functions inside an activity.
"""

from dataclasses import dataclass

from app.research.provider import SourceRecord


@dataclass(frozen=True, slots=True)
class ScoredSource:
    rank: int
    url: str
    title: str
    snippet: str
    score: float
    provider: str


def _normalize_url(url: str) -> str:
    """Collapse trivial URL variants so duplicates dedup reliably."""
    u = url.strip()
    u = u.split("#", 1)[0]
    if u.endswith("/"):
        u = u[:-1]
    return u.lower()


def score_and_dedup(sources: list[SourceRecord]) -> list[ScoredSource]:
    """Dedup by normalized URL keeping the highest score, then rank.

    Ordering is (score desc, title asc, url asc) — fully deterministic and
    independent of input order.
    """
    best: dict[str, SourceRecord] = {}
    for s in sources:
        key = _normalize_url(s.url)
        current = best.get(key)
        if current is None or s.score > current.score:
            best[key] = s

    ordered = sorted(
        best.values(),
        key=lambda s: (-s.score, s.title, _normalize_url(s.url)),
    )
    return [
        ScoredSource(
            rank=i + 1,
            url=s.url,
            title=s.title,
            snippet=s.snippet,
            score=round(s.score, 4),
            provider=s.provider,
        )
        for i, s in enumerate(ordered)
    ]


def compose_executive_summary(topic: str, scored: list[ScoredSource]) -> str:
    """Short, decision-first summary (MASTER_SPEC §G executive tier).

    Deterministic and body-independent so it can be stored on the artifact and
    presented without the full report.
    """
    topic = topic.strip()
    if not scored:
        return f"'{topic}' konusu için doğrulanmış kaynak bulunamadı."
    top = scored[: min(3, len(scored))]
    highlights = "; ".join(f"{s.title} (skor {s.score:.2f})" for s in top)
    return (
        f"'{topic}' konusu {len(scored)} doğrulanmış kaynak üzerinden incelendi. "
        f"En yüksek güvenilirlikteki bulgular: {highlights}. "
        f"Ayrıntılar ve tam atıf listesi rapor gövdesindedir."
    )


def _artifact_title(topic: str) -> str:
    topic = topic.strip()
    return f"Araştırma Raporu: {topic}"


def compose_canonical_markdown(
    topic: str, scored: list[ScoredSource], executive_summary: str
) -> str:
    """Build the canonical Markdown body: executive summary + detailed body +
    sources/citations. This is the artifact source of truth."""
    topic = topic.strip()
    lines: list[str] = []
    lines.append(f"# {_artifact_title(topic)}")
    lines.append("")
    lines.append("## Yönetici Özeti (Executive Summary)")
    lines.append("")
    lines.append(executive_summary)
    lines.append("")
    lines.append("## Ayrıntılı Rapor (Detailed Report)")
    lines.append("")
    lines.append(
        f"Aşağıdaki bulgular '{topic}' konusuyla ilgili {len(scored)} kaynaktan "
        "derlenmiş ve güvenilirlik skoruna göre sıralanmıştır."
    )
    lines.append("")
    lines.append("### Bulgular")
    lines.append("")
    if scored:
        for s in scored:
            lines.append(f"- **{s.title}** — {s.snippet} [{s.rank}]")
    else:
        lines.append("- (Kaynak bulunamadı.)")
    lines.append("")
    lines.append("## Kaynaklar (Sources / Citations)")
    lines.append("")
    if scored:
        for s in scored:
            lines.append(
                f"[{s.rank}] {s.title} — {s.url} "
                f"(skor: {s.score:.2f}, sağlayıcı: {s.provider})"
            )
    else:
        lines.append("(Kaynak yok.)")
    lines.append("")
    return "\n".join(lines)


def build_source_manifest(topic: str, scored: list[ScoredSource]) -> dict:
    """Structured manifest persisted with the artifact version (provenance)."""
    return {
        "topic": topic.strip(),
        "source_count": len(scored),
        "sources": [
            {
                "rank": s.rank,
                "url": s.url,
                "title": s.title,
                "score": s.score,
                "provider": s.provider,
            }
            for s in scored
        ],
    }
