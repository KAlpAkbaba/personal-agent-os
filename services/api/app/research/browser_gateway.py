"""M13 browser gateway seam: how the API dispatches research fetch work.

Production dispatch is NOT a Python import of ``services/browser`` — that
package runs as a separate process on the owner's enrolled Windows machine,
reached over Tailscale via the SAME proven device/broker command path M1
already qualified (``desktop.open_application``, ``desktop.open_artifact``),
extended with ``browser.*`` capabilities. See docs/DECISIONS.md ADR-0035 for
exactly which pieces that requires and do not yet exist: a
``browser.fetch_evidence`` capability entry in the Windows agent's capability
manifest/allowlist, a handler that constructs a
``browser_agent.BrowserSession`` (via ``ManagedBackend`` by default, or
``ExistingSessionBackend`` behind
``browser_agent.enrollment.get_research_authorized_enrollment``) and calls
``browser_agent.research.gather_evidence``, and the async command-dispatch
plumbing to await the (potentially slow, multi-page) result over the device
protocol's command-envelope pattern. ``BrowserGateway`` is the interface that
hides that dispatch from the rest of the M13 pipeline so the pipeline is
testable today without any of it existing yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from urllib.parse import quote

from app.research.evidence import EvidenceRecord

# Fixed fallback "now" so FakeBrowserGateway is deterministic even when the
# caller does not pass `now` explicitly (mirrors DeterministicResearchProvider's
# no-wall-clock discipline, M3).
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FetchQuery:
    """One (query, source class) the gateway should search and gather from.

    Deliberately query-shaped, not URL-shaped: discovering which URLs answer
    a query (running a search, or visiting a known directory/index page and
    reading its result links) is the browser agent's job — it has the live
    Chrome session and the semantic DOM/accessibility surface
    (``browser_agent.session.BrowserSession``) needed to do that safely.
    Handing the API layer a fixed URL list would defeat the point of a
    *browser* research provider.
    """

    query: str
    source_class: str = "unknown"
    max_results: int = 3


class BrowserGatewayNotConfiguredError(RuntimeError):
    """Raised by an inert (unwired) BrowserGateway before any dispatch."""


@runtime_checkable
class BrowserGateway(Protocol):
    """Dispatch a batch of queries to the Windows Browser Agent, get evidence back."""

    name: str

    def fetch_evidence(
        self,
        queries: list[FetchQuery],
        *,
        enrollment_id: str | None = None,
        now: datetime | None = None,
    ) -> list[EvidenceRecord]: ...


class UnwiredBrowserGateway:
    """The real seam. INERT: raises before any I/O, exactly like
    ``WebResearchProvider`` (M3) / ``ClaudeCodingBackend`` (M6). The dispatch
    path (``browser.fetch_evidence`` over the device/broker) does not exist
    yet (ADR-0035); wiring it is explicitly out of scope for M13's design
    pass. Registering this as the default gateway keeps
    ``BrowserResearchProvider`` honest: calling it for real today fails
    loudly instead of silently returning nothing.
    """

    name = "unwired"

    def fetch_evidence(self, queries, *, enrollment_id=None, now=None):
        raise BrowserGatewayNotConfiguredError(
            "no BrowserGateway is wired; browser.fetch_evidence dispatch over the "
            "device/broker command path is not implemented yet (see "
            "docs/DECISIONS.md ADR-0035). Inject a real gateway, or use "
            "FakeBrowserGateway for offline tests."
        )


class FakeBrowserGateway:
    """Deterministic, offline, seeded gateway for tests and demos.

    Mirrors ``DeterministicResearchProvider``'s role for the M3 provider
    seam: same inputs -> same outputs, no network, no browser, no wall
    clock unless the caller supplies ``now``.
    """

    name = "fake"

    def fetch_evidence(
        self,
        queries: list[FetchQuery],
        *,
        enrollment_id: str | None = None,
        now: datetime | None = None,
    ) -> list[EvidenceRecord]:
        moment = now or _EPOCH
        records: list[EvidenceRecord] = []
        for q in queries:
            for i in range(max(1, q.max_results)):
                url = f"https://{q.source_class}.example.com/{quote(q.query)}/{i}"
                records.append(
                    EvidenceRecord(
                        url=url,
                        title=f"{q.query} — {q.source_class} kaynağı {i + 1}",
                        excerpt=(
                            f"{q.query} ile ilgili {q.source_class} sınıfından bir bulgu "
                            f"(seri no {i + 1})."
                        ),
                        fetched_at=moment,
                        extraction_method="dom_text",
                        source_class=q.source_class,
                        query=q.query,
                    )
                )
        return records


__all__ = [
    "BrowserGateway",
    "BrowserGatewayNotConfiguredError",
    "FakeBrowserGateway",
    "FetchQuery",
    "UnwiredBrowserGateway",
]
