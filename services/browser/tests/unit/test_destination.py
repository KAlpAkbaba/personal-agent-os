"""Destination policy (device side): public Internet hosts only."""

from __future__ import annotations

import pytest

from browser_agent.destination import address_is_forbidden, require_public_destination
from browser_agent.errors import BrowserError, ErrorClass


def _resolver(mapping: dict[str, list[str]]):
    def resolve(host: str) -> list[str]:
        return mapping.get(host, [])

    return resolve


def _refused(url: str, resolver=None) -> BrowserError:
    with pytest.raises(BrowserError) as exc_info:
        require_public_destination(url, op="navigate", resolver=resolver or _resolver({}))
    return exc_info.value


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "100.127.255.254",
        "224.0.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fd12::1",
    ],
)
def test_non_public_addresses_are_forbidden(address: str) -> None:
    assert address_is_forbidden(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "2606:4700::1111"])
def test_public_addresses_pass(address: str) -> None:
    assert not address_is_forbidden(address)


def test_public_host_passes_when_every_address_is_public() -> None:
    require_public_destination(
        "https://example.com/news",
        op="navigate",
        resolver=_resolver({"example.com": ["93.184.216.34", "2606:2800:220:1::1946"]}),
    )


def test_ip_literals_in_private_ranges_are_refused() -> None:
    for url in (
        "http://127.0.0.1:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.101.102.103/",
        "http://[::1]/",
        "http://10.0.0.5/",
    ):
        err = _refused(url)
        assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR
        assert err.retryable is False


def test_rebinding_host_with_one_private_address_is_refused() -> None:
    err = _refused(
        "https://evil.example/",
        resolver=_resolver({"evil.example": ["93.184.216.34", "10.0.0.5"]}),
    )
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR


def test_local_host_names_and_userinfo_are_refused() -> None:
    for url in (
        "http://localhost/",
        "http://printer.local/",
        "http://core.internal/",
        "http://user:pw@example.com/",
        "http://metadata.google.internal/",
    ):
        assert _refused(url).error_class == ErrorClass.SECURITY_SCOPE_ERROR


def test_non_http_schemes_are_validation_errors() -> None:
    for url in ("file:///C:/x", "javascript:alert(1)", "data:text/html,hi", "ftp://x/"):
        assert _refused(url).error_class == ErrorClass.VALIDATION_ERROR


def test_refusal_never_carries_the_query_string() -> None:
    err = _refused("http://127.0.0.1/cb?token=abc123")
    assert "abc123" not in err.message
    assert "abc123" not in str(err.evidence)


def test_worker_enforces_policy_unless_started_with_the_fixture_flag(tmp_path) -> None:
    from browser_agent.worker import Worker, build_arg_parser

    strict = Worker(build_arg_parser().parse_args(["--data-dir", str(tmp_path / "a")]))
    with pytest.raises(BrowserError) as exc_info:
        strict._check_destination("http://127.0.0.1:8000/fixture", op="navigate")
    assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR

    relaxed = Worker(
        build_arg_parser().parse_args(
            ["--data-dir", str(tmp_path / "b"), "--allow-private-destinations"]
        )
    )
    relaxed._check_destination("http://127.0.0.1:8000/fixture", op="navigate")
