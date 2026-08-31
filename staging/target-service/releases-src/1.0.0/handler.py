"""browser-agent-demo release 1.0.0 — KNOWN GOOD source template.

A deterministic, browser-agent-shaped module: it maps raw browser error
messages onto the typed error taxonomy and runs a tiny semantic task. No real
browser, no network — the point is release mechanics, not browsing.
"""

RELEASE_VERSION = "1.0.0"

# Marker -> typed error class (mirrors the M2 browser error taxonomy shape).
ERROR_CLASS_BY_MARKER = (
    ("net::ERR_", "dependency_unavailable"),
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
