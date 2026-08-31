"""Semantic browser automation adapter for Personal Agent OS (M2).

Public API: BrowserSession, TargetSpec, BrowserError, ErrorClass, with_retry.
Raw coordinates are not part of the semantic surface; see
BrowserSession.escape_hatch_click_xy for the documented last resort.
"""

from .errors import BrowserError, ErrorClass, Phase, map_playwright_error
from .retry import with_retry
from .session import BrowserSession, DownloadResult, ElementInfo
from .targets import TargetSpec, coerce_target

__all__ = [
    "BrowserError",
    "BrowserSession",
    "DownloadResult",
    "ElementInfo",
    "ErrorClass",
    "Phase",
    "TargetSpec",
    "coerce_target",
    "map_playwright_error",
    "with_retry",
]
