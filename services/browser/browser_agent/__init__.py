"""Semantic browser automation adapter for Personal Agent OS (M2).

Public API surface:

- Transport seam: BrowserBackend, ManagedBackend, ExistingSessionBackend,
  TabInfo, BrowserCapabilities, require_capability.
- Authorization seam: BrowserEnrollment, EnrollmentRegistry, Transport.
- Semantics: BrowserSession, TargetSpec, typed BrowserError taxonomy,
  with_retry.
- Command layer: BrowserCommandExecutor, CancelToken (idempotency +
  cancellation, mirroring device-protocol semantics).
- M13 research: PageEvidence/FetchFailure/extract_page_evidence/
  dedup_and_rank_evidence (evidence extraction + provenance + ranking),
  FetchTarget/GatherResult/gather_evidence (multi-source batch gather),
  require_research_authorization/get_research_authorized_enrollment (the
  owner's-real-browser authorization gate).

Raw coordinates are not part of the semantic surface; see
BrowserSession.escape_hatch_click_xy for the documented last resort.
"""

from .backends import BrowserBackend, ExistingSessionBackend, ManagedBackend, TabInfo
from .capabilities import CAPABILITY_FLAGS, BrowserCapabilities, require_capability
from .commands import BrowserCommandExecutor, CancelToken
from .enrollment import (
    BrowserEnrollment,
    EnrollmentRegistry,
    Transport,
    get_research_authorized_enrollment,
    require_research_authorization,
)
from .errors import BrowserError, ErrorClass, Phase, map_playwright_error
from .evidence import (
    SOURCE_CLASSES,
    FetchFailure,
    PageDriver,
    PageEvidence,
    RankedEvidence,
    dedup_and_rank_evidence,
    extract_page_evidence,
)
from .research import FetchTarget, GatherResult, gather_evidence
from .retry import with_retry
from .session import BrowserSession, DownloadResult, ElementInfo
from .targets import TargetSpec, coerce_target

__all__ = [
    "CAPABILITY_FLAGS",
    "SOURCE_CLASSES",
    "BrowserBackend",
    "BrowserCapabilities",
    "BrowserCommandExecutor",
    "BrowserEnrollment",
    "BrowserError",
    "BrowserSession",
    "CancelToken",
    "DownloadResult",
    "ElementInfo",
    "EnrollmentRegistry",
    "ErrorClass",
    "ExistingSessionBackend",
    "FetchFailure",
    "FetchTarget",
    "GatherResult",
    "ManagedBackend",
    "PageDriver",
    "PageEvidence",
    "Phase",
    "RankedEvidence",
    "TabInfo",
    "TargetSpec",
    "Transport",
    "coerce_target",
    "dedup_and_rank_evidence",
    "extract_page_evidence",
    "gather_evidence",
    "get_research_authorized_enrollment",
    "map_playwright_error",
    "require_capability",
    "require_research_authorization",
    "with_retry",
]
