"""Unit tests: device signature verification and enrollment token hashing."""

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.broker import security


def make_keypair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    spki_b64 = base64.b64encode(
        private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")
    return private_key, spki_b64


def sign(private_key: ec.EllipticCurvePrivateKey, nonce: bytes, device_id: str) -> str:
    der = private_key.sign(nonce + device_id.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(der).decode("ascii")


DEVICE_ID = "0d9a1cd2-6f5e-4b2b-9f5e-9f2d5a1b3c4d"


def test_valid_der_signature_verifies() -> None:
    key, spki = make_keypair()
    nonce = security.generate_nonce()
    assert len(nonce) == 32
    signature = sign(key, nonce, DEVICE_ID)
    assert security.verify_device_signature(spki, signature, nonce, DEVICE_ID) is True


def test_valid_raw_rs_signature_verifies() -> None:
    key, spki = make_keypair()
    nonce = security.generate_nonce()
    der = key.sign(nonce + DEVICE_ID.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    signature_b64 = base64.b64encode(raw).decode("ascii")
    assert security.verify_device_signature(spki, signature_b64, nonce, DEVICE_ID) is True


def test_wrong_key_rejected() -> None:
    key, _ = make_keypair()
    _, other_spki = make_keypair()
    nonce = security.generate_nonce()
    signature = sign(key, nonce, DEVICE_ID)
    assert security.verify_device_signature(other_spki, signature, nonce, DEVICE_ID) is False


def test_tampered_nonce_rejected() -> None:
    key, spki = make_keypair()
    nonce = security.generate_nonce()
    signature = sign(key, nonce, DEVICE_ID)
    tampered = bytes([nonce[0] ^ 0xFF]) + nonce[1:]
    assert security.verify_device_signature(spki, signature, tampered, DEVICE_ID) is False


def test_wrong_device_id_rejected() -> None:
    key, spki = make_keypair()
    nonce = security.generate_nonce()
    signature = sign(key, nonce, DEVICE_ID)
    other = "11111111-2222-3333-4444-555555555555"
    assert security.verify_device_signature(spki, signature, nonce, other) is False


def test_garbage_signature_and_key_never_raise() -> None:
    _, spki = make_keypair()
    nonce = security.generate_nonce()
    assert security.verify_device_signature(spki, "!!!not-base64!!!", nonce, DEVICE_ID) is False
    assert security.verify_device_signature("!!!", "AAAA", nonce, DEVICE_ID) is False
    assert security.verify_device_signature(spki, "AAAA", nonce, DEVICE_ID) is False


def test_load_public_key_rejects_non_p256() -> None:
    with pytest.raises(ValueError):
        security.load_p256_public_key("bm90IGEga2V5")  # "not a key"
    weak_curve_key = ec.generate_private_key(ec.SECP384R1())
    spki384 = base64.b64encode(
        weak_curve_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")
    with pytest.raises(ValueError, match="curve"):
        security.load_p256_public_key(spki384)


def test_enrollment_token_hash_is_sha256_hex() -> None:
    token = security.generate_enrollment_token()
    assert len(token) >= 32
    hashed = security.hash_enrollment_token(token)
    assert hashed == hashlib.sha256(token.encode()).hexdigest()
    assert hashed != token
    # deterministic and distinct per token
    assert security.hash_enrollment_token(token) == hashed
    assert security.hash_enrollment_token(token + "x") != hashed
