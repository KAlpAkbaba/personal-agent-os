"""Opaque bearer credentials: generation, hashing, constant-time comparison.

Rules this module exists to make unavoidable:

- **Opaque, not self-describing.** A session token carries no claims. There is
  nothing to forge because there is nothing to parse: authority comes from the
  row it hashes to, so revocation is immediate rather than "until the JWT
  expires".
- **256 bits of entropy.** `secrets.token_urlsafe(32)` — 32 random bytes,
  URL-safe base64 (43 characters), from the OS CSPRNG.
- **Stored only as SHA-256.** A database leak yields hashes; the preimage is a
  256-bit random string, so there is no dictionary to run against it. SHA-256
  (not a password KDF) is the right primitive precisely *because* the input is
  full-entropy random — key stretching protects low-entropy human secrets, and
  buys nothing here while costing a hash on every request.
- **Constant-time comparison.** `hmac.compare_digest` on the hashes, always,
  even when the row was found by an indexed equality lookup.
- **Never logged.** Nothing in this module returns a loggable form of a token.
  `fingerprint()` returns a truncated hash for correlating repeated rejections;
  it is one-way and is the only token-derived value that may appear in an
  event row or a log line.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: 32 bytes = 256 bits from the OS CSPRNG.
TOKEN_ENTROPY_BYTES = 32

#: Prefixes make a leaked credential identifiable by secret scanners and make
#: "is this even a token?" answerable before touching the database.
OWNER_CREDENTIAL_PREFIX = "pagentos_ok_"
SESSION_TOKEN_PREFIX = "pagentos_st_"

#: token_urlsafe(32) is 43 chars; allow slack for future prefixes, and refuse
#: anything long enough to be an attack on the hash/DB layer rather than a token.
_MIN_SECRET_CHARS = 40
MAX_TOKEN_CHARS = 256

_BEARER_SCHEME = "bearer"


def _new_secret(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)}"


def new_owner_credential() -> str:
    """Mint the single owner credential. Returned once, never persisted raw."""
    return _new_secret(OWNER_CREDENTIAL_PREFIX)


def new_session_token() -> str:
    """Mint an opaque session bearer token. Returned once, stored only hashed."""
    return _new_secret(SESSION_TOKEN_PREFIX)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of the UTF-8 token (64 chars; column is String(128))."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hashes_equal(left: str, right: str) -> bool:
    """Constant-time hash comparison (never `==` on credential material)."""
    return hmac.compare_digest(left, right)


def fingerprint(token: str) -> str:
    """One-way, truncated hash for correlating repeated rejections in the audit.

    16 hex characters of SHA-256 over a 256-bit random token: not reversible,
    not replayable, and the only token-derived value allowed outside this module.
    """
    return hash_token(token)[:16]


def looks_like_secret(value: str, prefix: str) -> bool:
    """Shape check that runs before any database work."""
    return (
        value.startswith(prefix)
        and _MIN_SECRET_CHARS <= len(value) <= MAX_TOKEN_CHARS
        and value.isascii()
        and value.isprintable()
    )


def looks_like_session_token(value: str) -> bool:
    return looks_like_secret(value, SESSION_TOKEN_PREFIX)


def looks_like_owner_credential(value: str) -> bool:
    return looks_like_secret(value, OWNER_CREDENTIAL_PREFIX)


def parse_bearer(header_value: str | None) -> str | None:
    """Extract the credential from an `Authorization: Bearer <token>` header.

    Returns None for a missing header, a non-Bearer scheme, an empty credential
    or an oversized value — all of which are `Refusal.MALFORMED` to the caller.
    """
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, credential = parts[0].strip(), parts[1].strip()
    if scheme.lower() != _BEARER_SCHEME:
        return None
    if not credential or len(credential) > MAX_TOKEN_CHARS:
        return None
    return credential


__all__ = [
    "MAX_TOKEN_CHARS",
    "OWNER_CREDENTIAL_PREFIX",
    "SESSION_TOKEN_PREFIX",
    "TOKEN_ENTROPY_BYTES",
    "fingerprint",
    "hash_token",
    "hashes_equal",
    "looks_like_owner_credential",
    "looks_like_session_token",
    "new_owner_credential",
    "new_session_token",
    "parse_bearer",
]
