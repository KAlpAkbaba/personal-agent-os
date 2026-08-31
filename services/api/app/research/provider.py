"""Research provider seam (constitution: third-party services live behind
provider interfaces; provider independence).

`ResearchProvider` is the interface the workflow depends on. Two things matter
for M3:

- `DeterministicResearchProvider` returns a fixed, seeded set of sources for a
  given topic with NO network access. It is what the unit tests and the
  acceptance gate use, so the whole research pipeline is reproducible offline.
- A real web-research adapter (Tavily/Brave/SerpAPI/owner-browser, etc.) is a
  later, separately-wired provider. It MUST implement the same `gather()`
  contract and live behind this seam. It is intentionally NOT implemented here
  (see `WebResearchProvider` stub) — do not add network calls to this module.
"""

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A single retrieved source/evidence item, provider-neutral."""

    url: str
    title: str
    snippet: str
    score: float
    provider: str


@runtime_checkable
class ResearchProvider(Protocol):
    """Retrieve candidate sources for a research topic.

    Implementations must be side-effect free with respect to the caller's DB and
    return at most `limit` records. Scoring is provider-defined in [0, 1]; the
    composer re-scores/dedups, so providers only need a stable relative ordering.
    """

    name: str

    def gather(self, topic: str, *, limit: int = 6) -> list[SourceRecord]:
        ...


# Deterministic corpus. Each entry is a (host, slug, title-suffix, snippet)
# template; the topic is folded in so output is topic-specific but reproducible.
_CORPUS: tuple[tuple[str, str, str, str], ...] = (
    ("arxiv.org", "abs", "akademik derleme",
     "Konuyla ilgili yakın tarihli akademik bulguların derlemesi."),
    ("news.example.com", "report", "haber analizi",
     "Gelişmeleri bağlamıyla özetleyen bir haber analizi."),
    ("blog.example.org", "post", "teknik inceleme",
     "Uygulama ayrıntılarına giren teknik bir inceleme yazısı."),
    ("docs.example.net", "guide", "resmi kılavuz",
     "Birincil kaynaktan resmi kılavuz ve referans belgeleri."),
    ("forum.example.com", "thread", "topluluk tartışması",
     "Pratik deneyimleri paylaşan bir topluluk tartışması."),
    ("standards.example.org", "spec", "teknik şartname",
     "İlgili teknik şartname ve normatif tanımlar."),
    ("review.example.com", "survey", "karşılaştırmalı değerlendirme",
     "Seçenekleri karşılaştıran bağımsız bir değerlendirme."),
    ("data.example.net", "dataset", "veri kümesi",
     "Analizleri destekleyen açık bir veri kümesi kaydı."),
)


def _slugify(topic: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in topic.strip()]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:80] or "topic"


def _stable_hash(*parts: str) -> int:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class DeterministicResearchProvider:
    """Offline, seeded provider. Same topic -> identical sources, every time.

    Deliberately includes a duplicate URL (two entries resolving to the same
    slug) so the composer's dedup path is always exercised by the pipeline.
    """

    name = "deterministic"

    def gather(self, topic: str, *, limit: int = 6) -> list[SourceRecord]:
        if not topic or not topic.strip():
            raise ValueError("topic must be a non-empty string")
        slug = _slugify(topic)
        n = max(1, min(limit, len(_CORPUS)))
        records: list[SourceRecord] = []
        for i in range(n):
            host, path, suffix, snippet = _CORPUS[i]
            url = f"https://{host}/{path}/{slug}"
            title = f"{topic.strip()} — {suffix}"
            # Score in [0.30, 0.98], deterministic per (topic, host).
            raw = _stable_hash(topic.strip(), host) % 1000
            score = round(0.30 + (raw / 1000.0) * 0.68, 4)
            records.append(
                SourceRecord(
                    url=url,
                    title=title,
                    snippet=f"{snippet} (Konu: {topic.strip()})",
                    score=score,
                    provider=self.name,
                )
            )
        # Inject a guaranteed duplicate of the top URL with a lower score to keep
        # dedup deterministic and always covered.
        if records:
            dup = records[0]
            records.append(
                SourceRecord(
                    url=dup.url,
                    title=dup.title,
                    snippet=dup.snippet,
                    score=round(max(0.0, dup.score - 0.15), 4),
                    provider=self.name,
                )
            )
        return records


class WebResearchProvider:
    """Placeholder for the real, networked provider (NOT implemented in M3).

    A future adapter wires a web-research API or the owner browser/device path
    behind this same `gather()` contract. It is left unimplemented on purpose so
    the deterministic path stays the only code that runs offline in tests.
    """

    name = "web"

    def gather(self, topic: str, *, limit: int = 6) -> list[SourceRecord]:  # pragma: no cover
        raise NotImplementedError(
            "WebResearchProvider is a seam only; wire a concrete web adapter in a "
            "later milestone (must remain behind the ResearchProvider interface)."
        )
