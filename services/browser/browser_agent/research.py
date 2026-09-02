"""M13 multi-source research gather: batch evidence extraction over one session.

This is the orchestration layer that would run in-process on the Windows
Browser Agent when the Hetzner planner dispatches a ``browser.fetch_evidence``
command (ADR-0035): given a list of :class:`FetchTarget` (one per
plan query/source), drive one :class:`~browser_agent.session.BrowserSession`
through each in turn, collecting a :class:`~browser_agent.evidence.PageEvidence`
per success and a :class:`~browser_agent.evidence.FetchFailure` per typed
error — one bad source never aborts the whole research task (M13 spec:
"multi-source fetch").

Authorization (M13 spec item 4): when the session was obtained from an
existing (owner) browser via enrollment, the caller must resolve that
enrollment through :func:`browser_agent.enrollment.get_research_authorized_enrollment`
BEFORE constructing the session and passing it here — this module does not
re-derive authorization from a bare session (a session by itself does not
carry back which enrollment produced it in a form this module could check
without adding a hard dependency on ``ExistingSessionBackend`` internals).
:func:`gather_evidence` accepts an already-authorized ``PageDriver`` by
design: keeping the gate at construction time is what makes "unauthorized
real-profile use is refused" enforceable independent of which orchestration
function runs afterward.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import BrowserError
from .evidence import FetchFailure, PageDriver, PageEvidence, extract_page_evidence
from .obs_logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FetchTarget:
    """One (url, source_class, originating query) to gather evidence from."""

    url: str
    source_class: str = "unknown"
    query: str = ""


@dataclass(frozen=True, slots=True)
class GatherResult:
    """Outcome of a multi-source gather: partial success is expected/normal."""

    evidence: tuple[PageEvidence, ...]
    failures: tuple[FetchFailure, ...]

    @property
    def success_count(self) -> int:
        return len(self.evidence)

    @property
    def failure_count(self) -> int:
        return len(self.failures)


async def gather_evidence(
    driver: PageDriver,
    targets: list[FetchTarget],
    *,
    excerpt_chars: int = 500,
    nav_timeout_ms: float = 15_000,
) -> GatherResult:
    """Fetch evidence for every target with one driver, in order.

    Sequential by design (v1): a single browser session drives one page at a
    time, which is also what keeps ``fetched_at``/provenance unambiguous per
    item. Parallelizing across multiple sessions is a later optimization, not
    a correctness requirement here.
    """
    evidence: list[PageEvidence] = []
    failures: list[FetchFailure] = []
    for target in targets:
        try:
            item = await extract_page_evidence(
                driver,
                target.url,
                source_class=target.source_class,
                query=target.query,
                excerpt_chars=excerpt_chars,
                nav_timeout_ms=nav_timeout_ms,
            )
        except BrowserError as exc:
            logger.warning(
                "browser.research_fetch_failed",
                url=target.url,
                query=target.query,
                error_class=str(exc.error_class),
                message=exc.message,
            )
            failures.append(
                FetchFailure(
                    url=target.url,
                    error_class=str(exc.error_class),
                    message=exc.message,
                    query=target.query,
                )
            )
            continue
        evidence.append(item)
    logger.info(
        "browser.research_gather_complete",
        target_count=len(targets),
        success_count=len(evidence),
        failure_count=len(failures),
    )
    return GatherResult(evidence=tuple(evidence), failures=tuple(failures))


__all__ = ["FetchTarget", "GatherResult", "gather_evidence"]
