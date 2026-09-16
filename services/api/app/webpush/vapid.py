"""RFC 8292 VAPID: proves to a push service which application server sent a message,
without this system ever holding an account with Google/Mozilla/Microsoft/Apple.

The VAPID private key is the one secret this whole feature needs (module docstring of
``app.webpush``), and it is a bare P-256 scalar — 32 bytes, base64url, one line. That
representation is deliberate and not just convenient: ``scripts/quality-gate.ps1``'s
secret-hygiene scan refuses any tracked file containing a
``-----BEGIN ... PRIVATE KEY-----`` block on sight, so this module never produces or
accepts PEM, and ``private_key_to_b64url``/``load_private_key`` are the only encode/
decode pair the rest of the system uses.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.webpush.encoding import b64url_decode, b64url_encode

CURVE: Final = ec.SECP256R1()
_PRIVATE_SCALAR_LEN: Final = 32
_P256_SIGNATURE_LEN: Final = 64  # raw r || s, 32 bytes each

#: RFC 8292 §2: "the 'exp' claim... MUST NOT be longer than 24 hours". This is the hard
#: ceiling; `DEFAULT_EXP_TTL_S` stays well inside it rather than pressing up against it,
#: for the same reason `session_absolute_lifetime_s` in app.config is not set to exactly
#: what an attacker's clock skew could exploit.
MAX_EXP_TTL_S: Final = 24 * 3600
DEFAULT_EXP_TTL_S: Final = 12 * 3600


class VapidKeyError(ValueError):
    """A VAPID key, subject or TTL does not satisfy RFC 8292 — never a network error."""


@dataclass(frozen=True, slots=True)
class VapidKeyPair:
    private_key: ec.EllipticCurvePrivateKey

    @property
    def public_key_raw(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )

    @property
    def public_key_b64url(self) -> str:
        return b64url_encode(self.public_key_raw)

    @property
    def private_key_b64url(self) -> str:
        return private_key_to_b64url(self.private_key)


def generate_key_pair() -> VapidKeyPair:
    """A fresh P-256 keypair. No account, no signup - RFC 8292's whole point (task
    brief). Used by ``scripts/cloud/new-vapid-key.py`` and by tests; never called from a
    request path."""
    return VapidKeyPair(private_key=ec.generate_private_key(CURVE))


def private_key_to_b64url(private_key: ec.EllipticCurvePrivateKey) -> str:
    value = private_key.private_numbers().private_value
    return b64url_encode(value.to_bytes(_PRIVATE_SCALAR_LEN, "big"))


def load_private_key(b64url_value: str) -> ec.EllipticCurvePrivateKey:
    """Parse ``PAGENTOS_WEBPUSH_VAPID_PRIVATE_KEY``. Raises :class:`VapidKeyError` for
    anything that is not a 32-byte P-256 scalar - never a bare exception, since this
    runs at process wiring time and a raw traceback there is not an owner-facing
    sentence."""
    try:
        raw = b64url_decode(b64url_value)
    except Exception as exc:  # noqa: BLE001 - re-raised typed below
        raise VapidKeyError(f"VAPID private key is not valid base64url: {exc}") from exc
    if len(raw) != _PRIVATE_SCALAR_LEN:
        raise VapidKeyError(
            f"VAPID private key must decode to {_PRIVATE_SCALAR_LEN} bytes, got {len(raw)}"
        )
    try:
        return ec.derive_private_key(int.from_bytes(raw, "big"), CURVE)
    except ValueError as exc:
        raise VapidKeyError(f"VAPID private key is not a valid P-256 scalar: {exc}") from exc


def public_key_b64url(private_key: ec.EllipticCurvePrivateKey) -> str:
    return b64url_encode(
        private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )


def _jwt_segment(payload: dict[str, object]) -> str:
    return b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _audience_for(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.netloc:
        raise VapidKeyError("push endpoint must be an https:// URL to derive a VAPID audience")
    return f"{parts.scheme}://{parts.netloc}"


def build_authorization_header(
    *,
    endpoint: str,
    private_key: ec.EllipticCurvePrivateKey,
    subject: str,
    now: float | None = None,
    ttl_s: int = DEFAULT_EXP_TTL_S,
) -> str:
    """The ``Authorization: vapid t=<jwt>, k=<public key>`` header (RFC 8292 §3.2).

    ``subject`` (the JWT's ``sub`` claim) must be a ``mailto:`` or ``https:`` URI (RFC
    8292 §2) - a push service that gets abuse reports about this server needs a real
    contact, and CLAUDE.md's "the VAPID subject from settings, not hardcoded personal
    data" means this function never invents one; it is refused with no default.
    """
    if ttl_s <= 0 or ttl_s > MAX_EXP_TTL_S:
        raise VapidKeyError(
            f"VAPID exp TTL must be in (0, {MAX_EXP_TTL_S}] seconds (RFC 8292 §2), got {ttl_s}"
        )
    if not subject or not (subject.startswith("mailto:") or subject.startswith("https:")):
        raise VapidKeyError(
            f"VAPID subject must be a 'mailto:' or 'https:' URI (RFC 8292 §2); got {subject!r}"
        )

    audience = _audience_for(endpoint)
    moment = int(now if now is not None else time.time())
    header_segment = _jwt_segment({"typ": "JWT", "alg": "ES256"})
    claims_segment = _jwt_segment({"aud": audience, "exp": moment + ttl_s, "sub": subject})
    signing_input = f"{header_segment}.{claims_segment}".encode("ascii")

    der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    jwt = f"{header_segment}.{claims_segment}.{b64url_encode(raw_signature)}"

    return f"vapid t={jwt}, k={public_key_b64url(private_key)}"


__all__ = [
    "CURVE",
    "DEFAULT_EXP_TTL_S",
    "MAX_EXP_TTL_S",
    "VapidKeyError",
    "VapidKeyPair",
    "build_authorization_header",
    "generate_key_pair",
    "load_private_key",
    "private_key_to_b64url",
    "public_key_b64url",
]
