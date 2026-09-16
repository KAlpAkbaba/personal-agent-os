"""base64url helpers shared by ``ece`` and ``vapid``.

Web Push's own wire formats (the subscription JSON a browser hands back, the VAPID JWT,
RFC 8291's raw key material) are base64 WITHOUT padding, alphabet ``-_`` — never the
stdlib's default alphabet, and never padded. A single shared pair of functions means
"decode base64url, tolerate missing padding" is written once rather than reimplemented
slightly differently in the crypto module and the JWT module.
"""

from __future__ import annotations

import base64


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    """Decode base64url, tolerant of missing (or already-present) padding.

    Every source of this string in this package is untrusted in some sense — a
    subscription posted by a browser, a VAPID key typed into an env file — so this
    never raises anything other than ``ValueError`` (``binascii.Error`` is a subclass).
    """
    stripped = value.strip()
    padded = stripped + "=" * (-len(stripped) % 4)
    return base64.urlsafe_b64decode(padded)


__all__ = ["b64url_decode", "b64url_encode"]
