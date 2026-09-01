"""Typed refusals for the identity layer.

The server always knows precisely *why* an authentication decision went the way
it did — the reason is written to `session_events`. The client only ever learns
the coarse class (`unauthorized` / `forbidden` / `throttled`), because telling a
caller the difference between "unknown token", "expired token" and "revoked
token" is a free oracle for anyone holding a stolen credential.
"""

from __future__ import annotations

from enum import StrEnum


class Refusal(StrEnum):
    """Why a presented credential was refused (server-side reason)."""

    #: No owner credential has been bootstrapped: the system fails closed.
    NOT_BOOTSTRAPPED = "not_bootstrapped"
    #: No Authorization header, wrong scheme, or a value that cannot be a token.
    MALFORMED = "malformed"
    #: Well-formed, but no session row carries that hash.
    UNKNOWN = "unknown"
    #: Past its absolute expiry.
    EXPIRED = "expired"
    #: Explicitly revoked (owner action, panic, or device revocation).
    REVOKED = "revoked"
    #: Not used within the idle window.
    IDLE_TIMEOUT = "idle_timeout"
    #: Authenticated, but the session's scopes do not cover the operation.
    SCOPE_MISSING = "scope_missing"
    #: Too many failed attempts in the configured window.
    THROTTLED = "throttled"
    #: The identity subsystem is not wired into the running app.
    UNAVAILABLE = "unavailable"


#: Refusals that mean "authenticate first" (401) rather than "not allowed" (403).
AUTHENTICATION_REFUSALS = frozenset(
    {
        Refusal.NOT_BOOTSTRAPPED,
        Refusal.MALFORMED,
        Refusal.UNKNOWN,
        Refusal.EXPIRED,
        Refusal.REVOKED,
        Refusal.IDLE_TIMEOUT,
        Refusal.UNAVAILABLE,
    }
)


class IdentityError(Exception):
    """Base class for identity-service failures raised to callers."""


class AlreadyBootstrapped(IdentityError):
    """An owner credential already exists; bootstrap is a one-time action."""


class NotBootstrapped(IdentityError):
    """No owner credential exists yet."""


class InvalidOwnerCredential(IdentityError):
    """The presented owner credential did not match the stored hash."""


class Throttled(IdentityError):
    """Too many failed credential attempts in the configured window."""

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"throttled; retry after {retry_after_s}s")
        self.retry_after_s = retry_after_s


__all__ = [
    "AUTHENTICATION_REFUSALS",
    "AlreadyBootstrapped",
    "IdentityError",
    "InvalidOwnerCredential",
    "NotBootstrapped",
    "Refusal",
    "Throttled",
]
