"""RFC 8291 aes128gcm encryption (app.webpush.ece).

The Appendix A vector is the RFC's own worked example, reproduced byte-for-byte: this
system's ``encrypt()`` given the RFC's exact salt and application-server private key
must produce the RFC's exact wire body, not merely "a body that decrypts". Verified
independently against a from-scratch computation (given CEK/NONCE, plain AES-GCM) before
being hardcoded here, so a transcription slip in the RFC's own line-wrapped body could
not silently pass.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.webpush import ece


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# RFC 8291 Appendix A ("A Worked Example") — every value below is copied verbatim from
# the RFC text (https://www.rfc-editor.org/rfc/rfc8291.txt).
_RFC_PLAINTEXT_B64 = "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"
_RFC_AUTH_SECRET_B64 = "BTBZMqHH6r4Tts7J_aSIgg"
_RFC_SALT_B64 = "DGv6ra1nlYgDCS1FRnbzlw"
_RFC_UA_PRIVATE_B64 = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
_RFC_UA_PUBLIC_B64 = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
_RFC_AS_PRIVATE_B64 = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
_RFC_AS_PUBLIC_B64 = (
    "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
)
# The HTTP request body from RFC 8291 §5, unwrapped to one line.
_RFC_FINAL_BODY_B64 = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
    "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
    "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)
_RFC_CEK_B64 = "oIhVW04MRdy2XN9CiKLxTg"
_RFC_NONCE_B64 = "4h_95klXJ5E_qnoN"


def _rfc_as_private_key() -> ec.EllipticCurvePrivateKey:
    scalar = int.from_bytes(_b64url_decode(_RFC_AS_PRIVATE_B64), "big")
    return ec.derive_private_key(scalar, ece.CURVE)


def _rfc_ua_private_key() -> ec.EllipticCurvePrivateKey:
    scalar = int.from_bytes(_b64url_decode(_RFC_UA_PRIVATE_B64), "big")
    return ec.derive_private_key(scalar, ece.CURVE)


def test_rfc8291_appendix_a_vector_reproduced_byte_for_byte() -> None:
    plaintext = _b64url_decode(_RFC_PLAINTEXT_B64)
    assert plaintext == b"When I grow up, I want to be a watermelon"

    result = ece.encrypt(
        plaintext,
        p256dh=_b64url_decode(_RFC_UA_PUBLIC_B64),
        auth_secret=_b64url_decode(_RFC_AUTH_SECRET_B64),
        salt=_b64url_decode(_RFC_SALT_B64),
        sender_private_key=_rfc_as_private_key(),
    )

    assert _b64url_encode(result.body) == _RFC_FINAL_BODY_B64
    assert result.salt == _b64url_decode(_RFC_SALT_B64)
    assert result.server_public_key == _b64url_decode(_RFC_AS_PUBLIC_B64)


def test_rfc8291_vector_cek_and_nonce_match_the_rfcs_own_intermediate_values() -> None:
    """The RFC also publishes the derived CEK/NONCE. Deriving them independently (via
    the module-private helper) and checking against the RFC's own numbers catches a
    defect in the HKDF chain that the end-to-end ciphertext comparison above, on its
    own, would only report as "wrong final bytes" rather than "wrong key material"."""
    ecdh_secret = _rfc_as_private_key().exchange(
        ec.ECDH(), ece.load_public_key(_b64url_decode(_RFC_UA_PUBLIC_B64))
    )
    cek, nonce = ece._derive_cek_and_nonce(
        ecdh_secret=ecdh_secret,
        auth_secret=_b64url_decode(_RFC_AUTH_SECRET_B64),
        ua_public_raw=_b64url_decode(_RFC_UA_PUBLIC_B64),
        as_public_raw=_b64url_decode(_RFC_AS_PUBLIC_B64),
        salt=_b64url_decode(_RFC_SALT_B64),
    )
    assert cek == _b64url_decode(_RFC_CEK_B64)
    assert nonce == _b64url_decode(_RFC_NONCE_B64)


def test_decrypt_round_trips_the_rfc_vector() -> None:
    """The subscriber's side (test-only, module docstring) recovers the RFC's own
    plaintext from the RFC's own wire body."""
    body = _b64url_decode(_RFC_FINAL_BODY_B64)
    plaintext = ece.decrypt(
        body, ua_private_key=_rfc_ua_private_key(), auth_secret=_b64url_decode(_RFC_AUTH_SECRET_B64)
    )
    assert plaintext == b"When I grow up, I want to be a watermelon"


# ------------------------------------------------------------- generic round trip


def test_encrypt_decrypt_round_trip_with_fresh_random_keys() -> None:
    """Not the RFC vector: a random ephemeral keypair, random subscriber keypair, random
    salt, arbitrary plaintext. Proves ``encrypt``/``decrypt`` are real inverses of each
    other rather than two halves that each merely resemble the spec."""
    ua_private = ec.generate_private_key(ece.CURVE)
    ua_public_raw = ece.raw_public_key(ua_private.public_key())
    auth_secret = b"\x11" * 16
    plaintext = b'{"title":"Merdiven testi","body":"push kabul edildi"}'

    encrypted = ece.encrypt(plaintext, p256dh=ua_public_raw, auth_secret=auth_secret)
    recovered = ece.decrypt(encrypted.body, ua_private_key=ua_private, auth_secret=auth_secret)
    assert recovered == plaintext


def test_encrypt_uses_a_fresh_salt_and_key_every_call() -> None:
    """RFC 8291 §3.4 requires a UNIQUE salt/key pair per message. Two calls with
    identical plaintext and identical subscriber keys must never produce the same wire
    body - reusing either would defeat the encryption the same way a reused AES-GCM
    nonce would."""
    ua_private = ec.generate_private_key(ece.CURVE)
    ua_public_raw = ece.raw_public_key(ua_private.public_key())
    auth_secret = b"\x22" * 16
    plaintext = b"same content twice"

    first = ece.encrypt(plaintext, p256dh=ua_public_raw, auth_secret=auth_secret)
    second = ece.encrypt(plaintext, p256dh=ua_public_raw, auth_secret=auth_secret)
    assert first.body != second.body
    assert first.salt != second.salt
    assert first.server_public_key != second.server_public_key


# ------------------------------------------------------------- boundaries / errors


def test_plaintext_at_the_exact_limit_succeeds() -> None:
    ua_private = ec.generate_private_key(ece.CURVE)
    ua_public_raw = ece.raw_public_key(ua_private.public_key())
    auth_secret = b"\x33" * 16
    plaintext = b"x" * ece.MAX_PLAINTEXT_BYTES

    encrypted = ece.encrypt(plaintext, p256dh=ua_public_raw, auth_secret=auth_secret)
    recovered = ece.decrypt(encrypted.body, ua_private_key=ua_private, auth_secret=auth_secret)
    assert recovered == plaintext
    assert len(encrypted.body) == ece.DEFAULT_RECORD_SIZE


def test_plaintext_one_byte_over_the_limit_is_refused() -> None:
    ua_private = ec.generate_private_key(ece.CURVE)
    ua_public_raw = ece.raw_public_key(ua_private.public_key())
    plaintext = b"x" * (ece.MAX_PLAINTEXT_BYTES + 1)

    with pytest.raises(ece.WebPushCryptoError):
        ece.encrypt(plaintext, p256dh=ua_public_raw, auth_secret=b"\x00" * 16)


def test_wrong_auth_secret_length_is_refused() -> None:
    ua_private = ec.generate_private_key(ece.CURVE)
    ua_public_raw = ece.raw_public_key(ua_private.public_key())
    with pytest.raises(ece.WebPushCryptoError):
        ece.encrypt(b"hi", p256dh=ua_public_raw, auth_secret=b"\x00" * 15)


def test_malformed_p256dh_is_refused() -> None:
    with pytest.raises(ece.WebPushCryptoError):
        ece.encrypt(b"hi", p256dh=b"\x04" + b"\x00" * 10, auth_secret=b"\x00" * 16)


def test_decrypt_rejects_tampered_ciphertext() -> None:
    """A flipped bit in the wire body must fail AES-GCM's own authentication, never
    return silently-wrong plaintext."""
    ua_private = ec.generate_private_key(ece.CURVE)
    ua_public_raw = ece.raw_public_key(ua_private.public_key())
    auth_secret = b"\x44" * 16
    encrypted = ece.encrypt(b"do not tamper with me", p256dh=ua_public_raw, auth_secret=auth_secret)

    tampered = bytearray(encrypted.body)
    tampered[-1] ^= 0x01
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        ece.decrypt(bytes(tampered), ua_private_key=ua_private, auth_secret=auth_secret)
