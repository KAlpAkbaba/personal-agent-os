"""Aktivra's bearer token: the one credential POST /v1/aktivra/events accepts.

The rules are ``app.identity.tokens``'s, applied to a credential that lives in the settings
instead of a table:

- **Its own token.** ``aktivra_inbound_token`` (``pagentos_ak_`` + 256 random bits, minted by
  the owner with ``new_inbound_token``). The owner's session token is not it, so it fails here;
  and this token is no session, so ``require_owner_session`` refuses it everywhere else.
- **Hashes, constant time.** Both sides are SHA-256'd and compared with
  ``hmac.compare_digest``: equal-length inputs whatever was presented.
- **Never logged.** ``Verdict`` carries a fingerprint (16 hex of the hash) for counting
  rejections - nothing else token-derived leaves this module.
"""

from __future__ import annotations

import dataclasses
import hmac
import secrets
from typing import Final

from pydantic import SecretStr

from app.identity.tokens import TOKEN_ENTROPY_BYTES, fingerprint, hash_token, parse_bearer

AKTIVRA_TOKEN_PREFIX: Final[str] = "pagentos_ak_"


@dataclasses.dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    #: ``fingerprint`` of what was presented; None when nothing was.
    fingerprint: str | None = None
    #: accepted | missing | mismatch
    reason: str = "accepted"


def new_inbound_token() -> str:
    """Mint Aktivra's token. The owner puts it in both places (set-cloud-secret.ps1 here,
    Aktivra's own secret store there); it is never stored raw by this system."""
    return f"{AKTIVRA_TOKEN_PREFIX}{secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)}"


def configured(setting: SecretStr) -> bool:
    return bool(setting.get_secret_value())


def fingerprint_of(presented: str) -> str:
    return fingerprint(presented)


def verify(header_value: str | None, setting: SecretStr) -> Verdict:
    """Is ``Authorization: Bearer <token>`` Aktivra's token? Call only when ``configured``."""
    presented = parse_bearer(header_value)
    if presented is None:
        return Verdict(ok=False, reason="missing")
    matches = hmac.compare_digest(hash_token(presented), hash_token(setting.get_secret_value()))
    if matches:
        return Verdict(ok=True, fingerprint=fingerprint(presented))
    return Verdict(ok=False, fingerprint=fingerprint(presented), reason="mismatch")


__all__ = [
    "AKTIVRA_TOKEN_PREFIX",
    "Verdict",
    "configured",
    "fingerprint_of",
    "new_inbound_token",
    "verify",
]
