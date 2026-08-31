"""Device identity cryptography helpers.

Protocol contract (DEVICE_PROTOCOL.md §2-3):
- device key: ECDSA P-256, public key stored as base64(DER SPKI);
- challenge: 32 random bytes, base64 in the challenge frame;
- auth: base64 ECDSA-SHA256 signature over nonce_bytes || device_id_utf8.

Signatures are accepted in either DER encoding (variable length) or raw
IEEE P1363 r||s (exactly 64 bytes); the contract does not pin the encoding.
Enrollment tokens are stored SHA-256-hashed, never in clear.
"""

import base64
import hashlib
import secrets

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import load_der_public_key

NONCE_BYTES = 32
_RAW_SIGNATURE_LEN = 64  # P-256 r||s


def generate_nonce() -> bytes:
    return secrets.token_bytes(NONCE_BYTES)


def generate_enrollment_token() -> str:
    return secrets.token_urlsafe(32)


def hash_enrollment_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_p256_public_key(public_key_spki_b64: str) -> ec.EllipticCurvePublicKey:
    """Decode and validate a base64 DER-SPKI P-256 public key.

    Raises ValueError for anything that is not a P-256 EC public key.
    """
    try:
        der = base64.b64decode(public_key_spki_b64, validate=True)
        key = load_der_public_key(der)
    except Exception as exc:
        raise ValueError(f"invalid SPKI public key: {type(exc).__name__}") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError("public key is not an EC key")
    if key.curve.name != "secp256r1":
        raise ValueError(f"unsupported curve: {key.curve.name}")
    return key


def verify_device_signature(
    public_key_spki_b64: str,
    signature_b64: str,
    nonce: bytes,
    device_id: str,
) -> bool:
    """Verify base64 ECDSA-SHA256 signature over nonce_bytes || device_id_utf8.

    Never raises; any failure (bad key, bad encoding, bad signature) is False.
    """
    try:
        key = load_p256_public_key(public_key_spki_b64)
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False

    message = nonce + device_id.encode("utf-8")
    candidates = [signature]
    if len(signature) == _RAW_SIGNATURE_LEN:
        half = _RAW_SIGNATURE_LEN // 2
        r = int.from_bytes(signature[:half], "big")
        s = int.from_bytes(signature[half:], "big")
        try:
            candidates.insert(0, encode_dss_signature(r, s))
        except ValueError:
            pass
    for candidate in candidates:
        try:
            key.verify(candidate, message, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError):
            continue
    return False
