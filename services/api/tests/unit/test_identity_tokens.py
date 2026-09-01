"""M9 unit tests: bearer-credential primitives (entropy, hashing, comparison).

These are the properties the rest of the layer is allowed to assume.
"""

import hashlib
import re
from unittest import mock

import pytest

from app.identity import tokens


def test_session_token_is_prefixed_256_bit_urlsafe() -> None:
    token = tokens.new_session_token()
    assert token.startswith(tokens.SESSION_TOKEN_PREFIX)
    body = token[len(tokens.SESSION_TOKEN_PREFIX) :]
    # token_urlsafe(32) -> 43 URL-safe base64 characters, 256 bits of entropy.
    assert len(body) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", body)
    assert tokens.TOKEN_ENTROPY_BYTES == 32


def test_owner_credential_is_prefixed_and_distinct_from_session_tokens() -> None:
    credential = tokens.new_owner_credential()
    assert credential.startswith(tokens.OWNER_CREDENTIAL_PREFIX)
    assert not tokens.looks_like_session_token(credential)
    assert tokens.looks_like_owner_credential(credential)
    # ...and a session token is never mistaken for the owner credential.
    assert not tokens.looks_like_owner_credential(tokens.new_session_token())


def test_tokens_are_unique_across_many_mints() -> None:
    minted = {tokens.new_session_token() for _ in range(500)}
    assert len(minted) == 500


def test_hash_is_sha256_hex_of_utf8() -> None:
    token = tokens.new_session_token()
    assert tokens.hash_token(token) == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert len(tokens.hash_token(token)) == 64  # fits String(128)


def test_comparison_is_constant_time() -> None:
    """The comparison must go through hmac.compare_digest, not `==`."""
    left = tokens.hash_token("a")
    with mock.patch("app.identity.tokens.hmac.compare_digest", return_value=True) as spy:
        assert tokens.hashes_equal(left, "anything")
    spy.assert_called_once_with(left, "anything")
    assert tokens.hashes_equal(left, left)
    assert not tokens.hashes_equal(left, tokens.hash_token("b"))


def test_fingerprint_is_one_way_and_truncated() -> None:
    token = tokens.new_session_token()
    fp = tokens.fingerprint(token)
    assert len(fp) == 16
    assert fp == tokens.hash_token(token)[:16]
    # The fingerprint must not leak the token itself in any form.
    assert token not in fp
    assert fp not in token


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Basic abc",
        "Token pagentos_st_x",
        "Bearer ",
        "Bearer " + "x" * (tokens.MAX_TOKEN_CHARS + 1),
    ],
)
def test_parse_bearer_rejects_non_bearer_and_oversized(header: str | None) -> None:
    assert tokens.parse_bearer(header) is None


def test_parse_bearer_extracts_credential_case_insensitively() -> None:
    token = tokens.new_session_token()
    assert tokens.parse_bearer(f"Bearer {token}") == token
    assert tokens.parse_bearer(f"bearer {token}") == token
    assert tokens.parse_bearer(f"BEARER   {token}  ") == token


@pytest.mark.parametrize(
    "value",
    [
        "",
        "pagentos_st_",  # prefix only: too short to be a token
        "pagentos_st_" + "x" * 500,  # oversized
        "nope_" + "x" * 43,  # wrong prefix
        "pagentos_st_" + "ü" * 43,  # non-ascii
    ],
)
def test_shape_check_rejects_before_touching_the_database(value: str) -> None:
    assert not tokens.looks_like_session_token(value)
