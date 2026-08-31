"""Semantic browser automation adapter for Personal Agent OS (M2).

Public API surface:

- Transport seam: BrowserBackend, ManagedBackend, ExistingSessionBackend,
  TabInfo, BrowserCapabilities, require_capability.
- Authorization seam: BrowserEnrollment, EnrollmentRegistry, Transport.
- Semantics: BrowserSession, TargetSpec, typed BrowserError taxonomy,
  with_retry.
- Command layer: BrowserCommandExecutor, CancelToken (idempotency +
  cancellation, mirroring device-protocol semantics).

Raw coordinates are not part of the semantic surface; see
BrowserSession.escape_hatch_click_xy for the documented last resort.
"""

from .backends import BrowserBackend, ExistingSessionBackend, ManagedBackend, TabInfo
from .capabilities import CAPABILITY_FLAGS, BrowserCapabilities, require_capability
from .commands import BrowserCommandExecutor, CancelToken
from .enrollment import BrowserEnrollment, EnrollmentRegistry, Transport
from .errors import BrowserError, ErrorClass, Phase, map_playwright_error
from .retry import with_retry
from .session import BrowserSession, DownloadResult, ElementInfo
from .targets import TargetSpec, coerce_target

__all__ = [
    "CAPABILITY_FLAGS",
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
    "ManagedBackend",
    "Phase",
    "TabInfo",
    "TargetSpec",
    "Transport",
    "coerce_target",
    "map_playwright_error",
    "require_capability",
    "with_retry",
]
