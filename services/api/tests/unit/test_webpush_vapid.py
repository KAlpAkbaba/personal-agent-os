"""RFC 8292 VAPID (app.webpush.vapid): key handling and the ``Authorization`` header.

No PyJWT — this module hand-builds the JWT, so these tests independently re-parse and
re-verify the header rather than trust the module's own claim that it produced a valid
one. ``_parse_and_verify`` below is test-only scaffolding that plays the role of a real
push service: split the header, decode the two JSON segments, verify the ES256
signature against the embedded public key, and hand back the claims for assertion.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.webpush import vapid


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _parse_and_verify(header: str) -> dict[str, object]:
    """Parse ``vapid t=<jwt>, k=<key>`` the way a push service would, and verify the
    ES256 signature against the embedded public key. Raises on any structural or
    cryptographic defect; returns the claims dict on success."""
    assert header.startswith("vapid ")
    params = dict(part.strip().split("=", 1) for part in header[len("vapid ") :].split(","))
    jwt = params["t"]
    public_key_b64url = params["k"]

    header_seg, claims_seg, signature_seg = jwt.split(".")
    jose_header = json.loads(_b64url_decode(header_seg))
    assert jose_header == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(_b64url_decode(claims_seg))

    raw_signature = _b64url_decode(signature_seg)
    assert len(raw_signature) == 64
    r = int.from_bytes(raw_signature[:32], "big")
    s = int.from_bytes(raw_signature[32:], "big")
    der_signature = encode_dss_signature(r, s)

    public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        vapid.CURVE, _b64url_decode(public_key_b64url)
    )
    signing_input = f"{header_seg}.{claims_seg}".encode("ascii")
    public_key.verify(der_signature, signing_input, ec.ECDSA(hashes.SHA256()))  # raises if invalid
    return claims


# ------------------------------------------------------------- key handling


def test_generated_key_pair_round_trips_through_b64url() -> None:
    pair = vapid.generate_key_pair()
    restored = vapid.load_private_key(pair.private_key_b64url)
    assert vapid.public_key_b64url(restored) == pair.public_key_b64url


def test_private_key_b64url_is_never_pem() -> None:
    """scripts/quality-gate.ps1's secret-hygiene scan refuses any tracked
    '-----BEGIN ... PRIVATE KEY-----' block; this is the property that keeps it that
    way at the source, not merely by convention."""
    pair = vapid.generate_key_pair()
    assert "BEGIN" not in pair.private_key_b64url
    assert "-----" not in pair.private_key_b64url


def test_load_private_key_rejects_wrong_length() -> None:
    too_short = base64.urlsafe_b64encode(b"\x01" * 16).rstrip(b"=").decode()
    with pytest.raises(vapid.VapidKeyError):
        vapid.load_private_key(too_short)


def test_load_private_key_rejects_garbage() -> None:
    with pytest.raises(vapid.VapidKeyError):
        vapid.load_private_key("not-valid-base64url-!!!")


# ------------------------------------------------------------- the Authorization header


def test_authorization_header_structure_and_signature_verify() -> None:
    pair = vapid.generate_key_pair()
    header = vapid.build_authorization_header(
        endpoint="https://fcm.googleapis.com/fcm/send/abc123",
        private_key=pair.private_key,
        subject="mailto:owner@example.com",
        now=1_700_000_000,
        ttl_s=3600,
    )
    claims = _parse_and_verify(header)
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == "mailto:owner@example.com"
    assert claims["exp"] == 1_700_000_000 + 3600


def test_authorization_header_k_matches_the_signing_key() -> None:
    pair = vapid.generate_key_pair()
    other = vapid.generate_key_pair()
    header = vapid.build_authorization_header(
        endpoint="https://fcm.googleapis.com/fcm/send/xyz",
        private_key=pair.private_key,
        subject="mailto:owner@example.com",
    )
    assert f"k={pair.public_key_b64url}" in header
    assert other.public_key_b64url not in header


def test_signature_does_not_verify_against_a_different_key() -> None:
    """A push service must reject a JWT whose signature was NOT produced by the key
    embedded in ``k`` — this proves ``_parse_and_verify`` (and by extension a real push
    service) would actually catch a substituted key, not merely that the happy path
    passes."""
    signer = vapid.generate_key_pair()
    header = vapid.build_authorization_header(
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        private_key=signer.private_key,
        subject="mailto:owner@example.com",
    )
    # Swap in an unrelated public key for 'k' — the signature was made by `signer`, so
    # verification against a different key must fail.
    forged_key = vapid.generate_key_pair()
    forged_header = header.replace(
        f"k={signer.public_key_b64url}", f"k={forged_key.public_key_b64url}"
    )
    with pytest.raises(InvalidSignature):
        _parse_and_verify(forged_header)


@pytest.mark.parametrize("ttl_s", [0, -1, vapid.MAX_EXP_TTL_S + 1, 10 * vapid.MAX_EXP_TTL_S])
def test_ttl_outside_rfc8292_bounds_is_refused(ttl_s: int) -> None:
    pair = vapid.generate_key_pair()
    with pytest.raises(vapid.VapidKeyError):
        vapid.build_authorization_header(
            endpoint="https://fcm.googleapis.com/fcm/send/abc",
            private_key=pair.private_key,
            subject="mailto:owner@example.com",
            ttl_s=ttl_s,
        )


def test_ttl_at_the_24_hour_ceiling_is_accepted() -> None:
    pair = vapid.generate_key_pair()
    header = vapid.build_authorization_header(
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        private_key=pair.private_key,
        subject="mailto:owner@example.com",
        ttl_s=vapid.MAX_EXP_TTL_S,
        now=1_700_000_000,
    )
    claims = _parse_and_verify(header)
    assert claims["exp"] == 1_700_000_000 + vapid.MAX_EXP_TTL_S


@pytest.mark.parametrize(
    "subject", ["", "owner@example.com", "http://example.com", "ftp://example.com", "  "]
)
def test_subject_must_be_mailto_or_https(subject: str) -> None:
    pair = vapid.generate_key_pair()
    with pytest.raises(vapid.VapidKeyError):
        vapid.build_authorization_header(
            endpoint="https://fcm.googleapis.com/fcm/send/abc",
            private_key=pair.private_key,
            subject=subject,
        )


@pytest.mark.parametrize("subject", ["mailto:owner@example.com", "https://example.com/contact"])
def test_subject_mailto_or_https_is_accepted(subject: str) -> None:
    pair = vapid.generate_key_pair()
    header = vapid.build_authorization_header(
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        private_key=pair.private_key,
        subject=subject,
    )
    claims = _parse_and_verify(header)
    assert claims["sub"] == subject


def test_non_https_endpoint_is_refused() -> None:
    pair = vapid.generate_key_pair()
    with pytest.raises(vapid.VapidKeyError):
        vapid.build_authorization_header(
            endpoint="http://fcm.googleapis.com/fcm/send/abc",
            private_key=pair.private_key,
            subject="mailto:owner@example.com",
        )


def test_default_now_is_wall_clock() -> None:
    pair = vapid.generate_key_pair()
    before = int(time.time())
    header = vapid.build_authorization_header(
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        private_key=pair.private_key,
        subject="mailto:owner@example.com",
        ttl_s=100,
    )
    after = int(time.time())
    claims = _parse_and_verify(header)
    assert before + 100 <= claims["exp"] <= after + 100
