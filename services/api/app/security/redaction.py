"""Secret redaction for everything the security agent collects (M8 §8).

The agent's whole job is to look at configuration, which is exactly where
credentials live. So the invariant is not "try not to log secrets" — it is:

    NOTHING the collector reads is persisted, rendered or audited in raw form.

Every string that leaves a collector goes through `redact_text` / `redact_value`
before it reaches a finding, an assessment result, an artifact body or an
`authorization_events` row. A finding therefore *names* the exposure (file,
line, which credential pattern) without ever reproducing the credential.

Pattern list style follows app/memory/policy.py (SECURITY_MODEL §5); the memory
patterns are reused verbatim so the two subsystems cannot disagree about what a
secret looks like, and config-shaped patterns are added on top.
"""

from __future__ import annotations

import re
from typing import Any

from app.memory.policy import SECRET_PATTERNS as MEMORY_SECRET_PATTERNS

# Key names that mean "this value is a credential".
_CRED_KEYWORD = (
    r"(?:password|passwd|pwd|secret|token|apikey|api_key|access_key|"
    r"private_key|credential|client_secret)"
)
# ...but NOT when the full key is a policy knob about credentials rather than a
# credential itself (`min_password_length: 8`, `token_expiry_days: 30`).
_POLICY_SUFFIX = (
    r"(?![\w.\-\[\]]*(?:_length|_len|_min|_max|_days|_age|_policy|_required|"
    r"_enabled|_expiry|_rotation|_count|_attempts|_ttl)[^\S\n]*[:=])"
)
# ...and not when the value is plainly not a secret (a boolean, a bare number,
# an empty string). Those are configuration, not exposure.
_NON_SECRET_VALUE = (
    r"(?![\"']?(?:true|false|none|null|off|on|yes|no|disabled|enabled|required|"
    r"optional|changeme|\d+)[\"']?[^\S\n]*$)"
)

# Config-file shaped credentials the memory list does not cover. Ordering
# matters: the first pattern that matches a span names the redaction.
_CONFIG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # KEY=value / key: value where the key name means "credential". Covers
    # APP_PASSWORD=, db_passwd:, client_secret =, AUTH_TOKEN: ... The memory
    # list's \bpassword rule misses prefixed keys like APP_PASSWORD.
    (
        "credential_assignment",
        re.compile(
            r"(?im)^[^\S\n]*[\w.\-\[\]]*"
            + _CRED_KEYWORD
            + _POLICY_SUFFIX
            + r"[\w.\-\[\]]*[^\S\n]*[:=][^\S\n]*"
            + _NON_SECRET_VALUE
            + r"\S{4,}"
        ),
    ),
    # Credentials embedded in a connection URI: scheme://user:password@host
    ("connection_uri_credential", re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s/@]+@")),
    # Compact JWT.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    # Basic-auth header value.
    ("basic_auth_header", re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/]{16,}={0,2}")),
)

# Deliberately NOT a pattern: a bare long base64/hex line. `private_key_block`
# already catches the PEM header that gives such a body its meaning, whereas a
# standalone 64-character token is far more often a content hash, a git commit
# or one of this system's own finding fingerprints. Matching it would make the
# redactor mangle its own audit trail and cry wolf on every artifact digest —
# a detector that fires on everything protects nothing.

# Full ordered list: config-shaped first (they match a whole assignment, which
# is the more useful redaction), then the shared memory patterns.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    _CONFIG_PATTERNS + MEMORY_SECRET_PATTERNS
)

REDACTION_TEMPLATE = "[REDACTED:{name}]"

# Bounded work: a collector never hands arbitrary-size text to the redactor.
MAX_REDACT_CHARS = 20_000
MAX_REDACT_DEPTH = 8


def find_secret(text: str) -> str | None:
    """Return the NAME of the first matching secret pattern (never the match)."""
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return name
    return None


def redact_text(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, sorted unique pattern names that fired).

    Each matched span is replaced by ``[REDACTED:<pattern_name>]`` so the shape
    of the exposure survives while the value does not.
    """
    if not text:
        return text, []
    if len(text) > MAX_REDACT_CHARS:
        text = text[:MAX_REDACT_CHARS] + "…"
    hits: set[str] = set()
    redacted = text
    for name, pattern in SECRET_PATTERNS:
        if not pattern.search(redacted):
            continue
        hits.add(name)
        redacted = pattern.sub(REDACTION_TEMPLATE.format(name=name), redacted)
    return redacted, sorted(hits)


def redact_value(value: Any, _depth: int = 0) -> Any:
    """Recursively redact strings inside dict/list/tuple structures.

    Dict KEYS are redacted too: a config parsed into ``{"api_key=abc": ...}``
    must not smuggle the secret through the key side.
    """
    if _depth > MAX_REDACT_DEPTH:
        return "[REDACTED:depth_limit]"
    if isinstance(value, str):
        return redact_text(value)[0]
    if isinstance(value, dict):
        return {
            (redact_text(k)[0] if isinstance(k, str) else k): redact_value(v, _depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_value(v, _depth + 1) for v in value]
    return value


def contains_secret(value: Any) -> str | None:
    """Name of the first secret pattern found anywhere in a structure, or None.

    Used as a post-condition assertion by the persistence layer and by tests:
    if this returns non-None for something about to be stored, that is a bug.
    """
    if isinstance(value, str):
        return find_secret(value)
    if isinstance(value, dict):
        for k, v in value.items():
            hit = contains_secret(k) or contains_secret(v)
            if hit:
                return hit
        return None
    if isinstance(value, list | tuple):
        for item in value:
            hit = contains_secret(item)
            if hit:
                return hit
        return None
    return None


def assert_redacted(value: Any, *, where: str) -> Any:
    """Fail-safe gate used on every write path.

    Returns the value when it is clean. When a secret survived the redaction
    pass (a pattern that matches the raw text but not the redacted output, i.e.
    a redactor bug) the value is replaced wholesale rather than persisted.
    """
    hit = contains_secret(value)
    if hit is None:
        return value
    return {"redaction_failure": True, "pattern": hit, "where": where}


__all__ = [
    "MAX_REDACT_CHARS",
    "REDACTION_TEMPLATE",
    "SECRET_PATTERNS",
    "assert_redacted",
    "contains_secret",
    "find_secret",
    "redact_text",
    "redact_value",
]
