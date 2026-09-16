"""RFC 8291 Web Push message encryption: RFC 8188's ``aes128gcm`` content coding,
combined with an ECDH-derived key the way RFC 8291 section 3 specifies.

Only what a Web Push application server needs is implemented: single-record
``aes128gcm`` (the whole plaintext fits in one record — see ``MAX_PLAINTEXT_BYTES``),
and only the "application server" side of the exchange (``encrypt``). ``decrypt`` exists
only so this module's own tests can round-trip a message end to end and so the RFC 8291
Appendix A worked example can be reproduced byte-for-byte; nothing in the running system
ever decrypts a push message — that happens in the browser, using the subscriber's own
private key, which this process never holds.

Algorithm (RFC 8291 §3.3-3.4, itself built on RFC 8188 §2):

    ecdh_secret = ECDH(as_private, ua_public)
    PRK_key     = HMAC-SHA-256(key=auth_secret, msg=ecdh_secret)              # HKDF-Extract
    key_info    = b"WebPush: info\\x00" + ua_public_raw(65) + as_public_raw(65)
    IKM         = HMAC-SHA-256(key=PRK_key, msg=key_info + b"\\x01")[:32]     # HKDF-Expand, 1 block

    PRK         = HMAC-SHA-256(key=salt, msg=IKM)                            # HKDF-Extract
    CEK         = HMAC-SHA-256(key=PRK, msg=b"Content-Encoding: aes128gcm\\x00\\x01")[:16]
    NONCE       = HMAC-SHA-256(key=PRK, msg=b"Content-Encoding: nonce\\x00\\x01")[:12]

    record      = AES-128-GCM(key=CEK, nonce=NONCE, plaintext=data + delimiter)
    header      = salt(16) || record_size(4, big-endian) || idlen(1) || as_public_raw(idlen)
    wire body   = header || record

``ua_public``/``as_public`` are the RAW 65-byte uncompressed P-256 points (0x04 || X ||
Y) — RFC 8291 is explicit that the key material entering ``key_info`` is these raw
bytes, not any encoded form. ``as_public`` doubles as the aes128gcm header's ``keyid``
(RFC 8291 §4), which is how the receiving browser recovers the sender's ephemeral
public key without a second field.

The delimiter is RFC 8188's: ``0x02`` for the last (here, only) record, ``0x01`` for a
record that is not last. This module only ever writes one record, so it is always
``0x02``.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

CURVE: Final = ec.SECP256R1()

_AUTH_SECRET_LEN: Final = 16
_SALT_LEN: Final = 16
_CEK_LEN: Final = 16
_NONCE_LEN: Final = 12
_TAG_LEN: Final = 16
_UNCOMPRESSED_POINT_LEN: Final = 65  # 0x04 || X(32) || Y(32), P-256

#: RFC 8291 §3.4's own default, and what every push service's payload-size document
#: (FCM's included) assumes when it says "4096 bytes": the ENCRYPTED wire body, header
#: included. One record only (module docstring) — the ladder's push payload (title,
#: body, a tag) is at most a few hundred bytes, so a second record has no caller.
DEFAULT_RECORD_SIZE: Final = 4096
_HEADER_LEN: Final = _SALT_LEN + 4 + 1 + _UNCOMPRESSED_POINT_LEN  # 16+4+1+65 = 86
#: What is left for plaintext once the fixed header, the GCM tag and the one delimiter
#: byte are accounted for, at the default record size: 4096 - 86 - 16 - 1 = 3993.
MAX_PLAINTEXT_BYTES: Final = DEFAULT_RECORD_SIZE - _HEADER_LEN - _TAG_LEN - 1


class WebPushCryptoError(ValueError):
    """A key, secret or payload could not be used per RFC 8291 — never a network error."""


@dataclass(frozen=True, slots=True)
class EncryptedPushMessage:
    """The aes128gcm wire body plus the header fields a caller may want for logging."""

    body: bytes
    salt: bytes
    server_public_key: bytes  # raw 65-byte point, also embedded in body's header
    content_encoding: Final[str] = "aes128gcm"


def raw_public_key(public_key: ec.EllipticCurvePublicKey) -> bytes:
    """The 65-byte uncompressed point RFC 8291 puts in ``key_info`` and the header."""
    return public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def load_public_key(raw: bytes) -> ec.EllipticCurvePublicKey:
    if len(raw) != _UNCOMPRESSED_POINT_LEN or raw[0:1] != b"\x04":
        raise WebPushCryptoError(
            f"expected a {_UNCOMPRESSED_POINT_LEN}-byte uncompressed P-256 point (p256dh)"
        )
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, raw)
    except ValueError as exc:
        raise WebPushCryptoError(f"invalid p256dh point: {exc}") from exc


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    h = hmac.HMAC(salt, hashes.SHA256())
    h.update(ikm)
    return h.finalize()


def _hkdf_expand_one_block(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand for exactly one block (RFC 5869 §2.3, ``T(1) = HMAC(PRK, info || 0x01)``).

    Every length this module ever asks for (32, 16, 12) fits in a single SHA-256 block's
    32 output bytes, so the general multi-block loop RFC 5869 describes is never needed
    here — asking for more than 32 bytes would be a caller defect, not a case this
    function silently mishandles, so it is refused outright.
    """
    if length > hashes.SHA256().digest_size:
        raise WebPushCryptoError("single-block HKDF-Expand cannot produce this many bytes")
    h = hmac.HMAC(prk, hashes.SHA256())
    h.update(info + b"\x01")
    return h.finalize()[:length]


def _derive_cek_and_nonce(
    *,
    ecdh_secret: bytes,
    auth_secret: bytes,
    ua_public_raw: bytes,
    as_public_raw: bytes,
    salt: bytes,
) -> tuple[bytes, bytes]:
    if len(auth_secret) != _AUTH_SECRET_LEN:
        raise WebPushCryptoError(
            f"auth secret must be {_AUTH_SECRET_LEN} bytes (RFC 8291 §3.1), got {len(auth_secret)}"
        )
    if len(salt) != _SALT_LEN:
        raise WebPushCryptoError(f"salt must be {_SALT_LEN} bytes, got {len(salt)}")

    prk_key = _hkdf_extract(auth_secret, ecdh_secret)
    key_info = b"WebPush: info\x00" + ua_public_raw + as_public_raw
    ikm = _hkdf_expand_one_block(prk_key, key_info, 32)

    prk = _hkdf_extract(salt, ikm)
    cek = _hkdf_expand_one_block(prk, b"Content-Encoding: aes128gcm\x00", _CEK_LEN)
    nonce = _hkdf_expand_one_block(prk, b"Content-Encoding: nonce\x00", _NONCE_LEN)
    return cek, nonce


def encrypt(
    plaintext: bytes,
    *,
    p256dh: bytes,
    auth_secret: bytes,
    salt: bytes | None = None,
    sender_private_key: ec.EllipticCurvePrivateKey | None = None,
    record_size: int = DEFAULT_RECORD_SIZE,
) -> EncryptedPushMessage:
    """Encrypt ``plaintext`` for the subscriber identified by ``p256dh``/``auth_secret``
    (the two fields of a Web Push subscription's ``keys``), as the "application server"
    side of RFC 8291.

    ``salt`` and ``sender_private_key`` are parameters only so ``test_rfc8291_vectors``
    can reproduce the RFC's own worked example byte-for-byte; production callers never
    pass them and get a fresh random salt and a fresh ephemeral keypair every call
    (RFC 8291 §3.4 requires a UNIQUE salt/key pair per message — reusing either defeats
    the encryption, the same reason a nonce is never reused).
    """
    header_len = _HEADER_LEN
    max_plaintext = record_size - header_len - _TAG_LEN - 1
    if max_plaintext <= 0:
        raise WebPushCryptoError(f"record_size {record_size} leaves no room for plaintext")
    if len(plaintext) > max_plaintext:
        raise WebPushCryptoError(
            f"plaintext is {len(plaintext)} bytes; at record_size={record_size} the limit "
            f"is {max_plaintext} bytes (single aes128gcm record only)"
        )

    ua_public = load_public_key(p256dh)
    as_private = sender_private_key or ec.generate_private_key(CURVE)
    as_public_raw = raw_public_key(as_private.public_key())
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_public)
    salt_bytes = salt if salt is not None else os.urandom(_SALT_LEN)

    cek, nonce = _derive_cek_and_nonce(
        ecdh_secret=ecdh_secret,
        auth_secret=auth_secret,
        ua_public_raw=p256dh,
        as_public_raw=as_public_raw,
        salt=salt_bytes,
    )

    # RFC 8188 §2: the last (here, only) record's delimiter is 0x02. No padding beyond
    # the delimiter is added — nothing in this system needs to obscure plaintext length
    # beyond what the delimiter already costs, and padding would only shrink the
    # already-small MAX_PLAINTEXT_BYTES budget further for no caller that wants it.
    padded = plaintext + b"\x02"
    ciphertext = AESGCM(cek).encrypt(nonce, padded, None)

    header = (
        salt_bytes + struct.pack(">I", record_size) + bytes([len(as_public_raw)]) + as_public_raw
    )
    return EncryptedPushMessage(
        body=header + ciphertext, salt=salt_bytes, server_public_key=as_public_raw
    )


def decrypt(
    body: bytes, *, ua_private_key: ec.EllipticCurvePrivateKey, auth_secret: bytes
) -> bytes:
    """The subscriber's side: undo ``encrypt``. Test-only in this codebase (module
    docstring) — proves the RFC 8291 vector round-trips and that ``encrypt`` produces
    something its own inverse can read, rather than two halves that merely look right.
    """
    if len(body) < _HEADER_LEN + _TAG_LEN:
        raise WebPushCryptoError("body is shorter than a valid aes128gcm header + tag")
    salt = body[:_SALT_LEN]
    record_size = struct.unpack(">I", body[_SALT_LEN : _SALT_LEN + 4])[0]
    idlen = body[_SALT_LEN + 4]
    keyid_start = _SALT_LEN + 5
    as_public_raw = body[keyid_start : keyid_start + idlen]
    ciphertext = body[keyid_start + idlen :]
    if len(as_public_raw) != idlen:
        raise WebPushCryptoError("truncated aes128gcm header (keyid)")
    if record_size < len(body):
        # Multi-record bodies are out of scope (module docstring); a caller handing this
        # module one would otherwise get silently-wrong plaintext from record 0 alone.
        raise WebPushCryptoError("multi-record aes128gcm bodies are not supported by decrypt()")

    as_public = load_public_key(as_public_raw)
    ecdh_secret = ua_private_key.exchange(ec.ECDH(), as_public)
    ua_public_raw = raw_public_key(ua_private_key.public_key())
    cek, nonce = _derive_cek_and_nonce(
        ecdh_secret=ecdh_secret,
        auth_secret=auth_secret,
        ua_public_raw=ua_public_raw,
        as_public_raw=as_public_raw,
        salt=salt,
    )
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    # RFC 8188 §2: a record is ``content || delimiter || padding``, padding is zero
    # bytes, and the delimiter is the first non-zero byte found scanning FROM THE END.
    # Trailing zero bytes are stripped first (this module never writes any -
    # ``encrypt`` above appends nothing after its delimiter - but a body produced by
    # another aes128gcm implementation may), then the byte immediately before them
    # must be the delimiter. Content itself may contain 0x00 or 0x02 bytes anywhere;
    # only the byte position matters, never a search for a matching value.
    end = len(padded)
    while end > 0 and padded[end - 1] == 0:
        end -= 1
    if end == 0 or padded[end - 1] not in (0x01, 0x02):
        raise WebPushCryptoError("invalid record: no delimiter found after stripping padding")
    return padded[: end - 1]


__all__ = [
    "CURVE",
    "DEFAULT_RECORD_SIZE",
    "MAX_PLAINTEXT_BYTES",
    "EncryptedPushMessage",
    "WebPushCryptoError",
    "decrypt",
    "encrypt",
    "load_public_key",
    "raw_public_key",
]
