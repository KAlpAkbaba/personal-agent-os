"""browser-agent-demo release 1.1.0 — CONTROLLED INJECTED BUG (M6 acceptance).

Identical to 1.0.0 except for one deliberate fault: navigation-level
``net::ERR_*`` browser errors are misclassified as ``internal_bug`` instead of
``dependency_unavailable``, so the deterministic /selftest fails on the
map_error check. "Injecting the bug" == activating this release.
"""

RELEASE_VERSION = "1.1.0"

ERROR_CLASS_BY_MARKER = (
    # INJECTED-FAULT: wrong error-class mapping (should be dependency_unavailable)
    ("net::ERR_", "internal_bug"),
    ("Timeout", "timeout"),
    ("element not found", "element_not_found"),
)


def map_error(message: str) -> str:
    """Classify a raw browser error message into a typed error class."""
    for marker, error_class in ERROR_CLASS_BY_MARKER:
        if marker in message:
            return error_class
    return "internal_bug"


def run_task(task: dict) -> dict:
    """Deterministic browser-agent-shaped operation."""
    if task.get("op") == "extract_title":
        html = task.get("html", "")
        start = html.find("<title>")
        end = html.find("</title>")
        if start == -1 or end == -1 or end <= start:
            return {"status": "error", "error_class": "element_not_found"}
        return {"status": "ok", "title": html[start + len("<title>") : end]}
    return {"status": "error", "error_class": "validation_error"}


def self_test() -> bool:
    """Cheap internal consistency check used by the /selftest synthetic probe."""
    return map_error("Timeout while waiting") == "timeout" and run_task(
        {"op": "extract_title", "html": "<title>x</title>"}
    ) == {"status": "ok", "title": "x"}
