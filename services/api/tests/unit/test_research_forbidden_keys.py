"""app.research.forbidden_keys: Cloud-Core-side scan for session-material
-shaped keys in device command results (finding HIGH-3, BROWSER_CAPABILITIES.md
§6). Parity with the Windows Browser Worker's own token list is asserted by
reading the worker's source file directly (services/browser is a separate
package/process; app.research.forbidden_keys' module docstring explains why
the token list is duplicated-by-hand rather than imported)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.research.forbidden_keys import (
    FORBIDDEN_KEY_TOKENS,
    ForbiddenKeyError,
    find_forbidden_keys,
    is_forbidden_key,
    normalize_key,
    require_no_forbidden_keys,
)

_API_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _API_ROOT.parent.parent
WORKER_PATH = _REPO_ROOT / "services" / "browser" / "browser_agent" / "worker.py"


def _worker_forbidden_key_tokens() -> tuple[str, ...]:
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == (
            "_FORBIDDEN_KEY_TOKENS"
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"_FORBIDDEN_KEY_TOKENS not found in {WORKER_PATH}")


def test_worker_path_exists() -> None:
    assert WORKER_PATH.exists(), f"missing {WORKER_PATH}"


def test_token_list_matches_the_browser_workers_own_list() -> None:
    assert tuple(FORBIDDEN_KEY_TOKENS) == _worker_forbidden_key_tokens()


@pytest.mark.parametrize(
    "key",
    [
        "cookie", "Cookie", "COOKIE",
        "authorization", "Authorization",
        "set-cookie", "Set-Cookie", "Set_Cookie", "setCookie",
        "localStorage", "local_storage",
        "sessionStorage", "session-storage",
        "password", "Password",
        "token", "access_token", "x-token",
        "secret", "client_secret",
        "apikey", "api_key", "x-api-key", "X-Api-Key",
    ],
)
def test_forbidden_key_variants_detected_under_any_spelling(key: str) -> None:
    assert is_forbidden_key(key) is True


@pytest.mark.parametrize("key", ["url", "title", "excerpt", "publisher", "page_kind", "id"])
def test_benign_keys_not_flagged(key: str) -> None:
    assert is_forbidden_key(key) is False


def test_normalize_key_strips_non_alphanumeric_and_lowercases() -> None:
    assert normalize_key("api_key") == "apikey"
    assert normalize_key("X-Api-Key") == "xapikey"
    assert normalize_key("Set-Cookie") == "setcookie"


def test_find_forbidden_keys_recurses_through_nested_dicts_and_lists() -> None:
    value = {
        "url": "https://a",
        "results": [
            {"title": "t", "x-api-key": "should-never-appear"},
            {"nested": {"Set_Cookie": "abc"}, "items": [{"authorization": "Bearer x"}]},
        ],
    }
    found = find_forbidden_keys(value)
    assert set(found) == {"x-api-key", "Set_Cookie", "authorization"}


def test_find_forbidden_keys_empty_for_clean_value() -> None:
    value = {"url": "https://a", "results": [{"title": "t", "snippet": "s"}]}
    assert find_forbidden_keys(value) == []


def test_require_no_forbidden_keys_raises_typed_error_with_keys() -> None:
    with pytest.raises(ForbiddenKeyError) as exc_info:
        require_no_forbidden_keys({"cookie": "x", "url": "https://a"})
    assert exc_info.value.error_class == "security_scope_error"
    assert "cookie" in exc_info.value.keys


def test_require_no_forbidden_keys_noop_on_clean_value() -> None:
    require_no_forbidden_keys({"url": "https://a"})  # no raise
