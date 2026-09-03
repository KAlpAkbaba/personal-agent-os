"""Cloud-Core-side forbidden-key scan for device command results (finding
HIGH-3, BROWSER_CAPABILITIES.md §6, ADR-0050 §6).

The Windows Browser Worker already scans every ``browser.*`` result for keys
shaped like session material before it ever leaves the device
(``services/browser/browser_agent/worker.py`` — ``_FORBIDDEN_KEY_TOKENS`` /
``redact_forbidden_keys``). That is the primary line of defence, but Cloud
Core does not get to assume it always ran correctly: every device command
result that becomes research evidence (search hits and ``fetch_evidence``
results in ``app.research.browser_gateway.DeviceBrowserGateway``, and
anything that ends up stored in ``research_evidence``) is scanned again here
with the SAME token list and the SAME normalisation rule, recursively through
dicts/lists, before it is trusted. A hit refuses the result outright — it is
never stored, never partially redacted-and-kept — because a forbidden key
surfacing at all indicates the worker-side guard was bypassed or buggy, which
is itself worth treating as a hard boundary violation rather than something
to quietly patch over.

The normalisation technique (lowercase, then strip everything that is not
``[a-z0-9]``) mirrors the one already proven in
``app.voice.realtime_sessions.service.is_forbidden_key`` (out of scope for
this change to touch — ``app/voice/**`` — so that module keeps its own copy
rather than importing this one); the two callers keep their own,
deliberately different, token lists regardless (voice scrubs audio/
transcript-shaped metadata, this module scrubs session-material-shaped
device-command results).
"""

from __future__ import annotations

import re
from typing import Any

#: Kept byte-identical (by design, not by import) to
#: ``services/browser/browser_agent/worker.py``'s ``_FORBIDDEN_KEY_TOKENS`` —
#: the two packages run as separate processes and only ever agree on a wire
#: shape (see app.research.evidence's module docstring for why this project
#: duplicates-by-hand rather than imports across that process boundary); a
#: parity test (tests/unit/test_research_forbidden_keys.py) reads the
#: worker's source file and asserts the two tuples are equal.
FORBIDDEN_KEY_TOKENS: tuple[str, ...] = (
    "cookie",
    "authorization",
    "setcookie",
    "localstorage",
    "sessionstorage",
    "password",
    "token",
    "secret",
    "apikey",
)

_KEY_NORMALIZER = re.compile(r"[^a-z0-9]+")


def normalize_key(key: Any) -> str:
    """Lowercase, then strip everything but ``[a-z0-9]`` — so ``"X-Api-Key"``,
    ``"api_key"`` and ``"apiKey"`` all normalise to the same token."""
    return _KEY_NORMALIZER.sub("", str(key).lower())


def is_forbidden_key(key: Any) -> bool:
    normalized = normalize_key(key)
    return any(token in normalized for token in FORBIDDEN_KEY_TOKENS)


class ForbiddenKeyError(ValueError):
    """A device command result contains a session-material-shaped key.

    Carries the device taxonomy error class (``security_scope_error`` —
    BROWSER_CAPABILITIES.md §5) so callers can raise the same typed,
    non-retryable error the rest of the browser-research pipeline uses for
    policy refusals.
    """

    error_class = "security_scope_error"

    def __init__(self, keys: list[str]) -> None:
        unique = sorted(set(keys))
        super().__init__(f"device result contained forbidden key(s): {unique}")
        self.keys: tuple[str, ...] = tuple(unique)


def find_forbidden_keys(value: Any) -> list[str]:
    """Recursively collect every forbidden-matching dict key found anywhere
    inside ``value`` (dicts and lists only — the JSON shapes a device result
    can take). Values are not scanned, only keys: a forbidden key's VALUE is
    exactly the session material this guard exists to catch, so once a key
    matches, nothing about its value is inspected or logged."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and is_forbidden_key(k):
                    found.append(k)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


def require_no_forbidden_keys(value: Any) -> None:
    """Raise :class:`ForbiddenKeyError` if any forbidden key is present
    anywhere inside ``value``; a no-op otherwise."""
    found = find_forbidden_keys(value)
    if found:
        raise ForbiddenKeyError(found)


__all__ = [
    "FORBIDDEN_KEY_TOKENS",
    "ForbiddenKeyError",
    "find_forbidden_keys",
    "is_forbidden_key",
    "normalize_key",
    "require_no_forbidden_keys",
]
