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


# Return 3 (inspector 2026-10-04, GHSA-gwph-fp79-379w class): an IPv6 spelling that carries
# an IPv4 address inside it went around the deny-list - ``::ffff:100.90.158.26`` (the Cloud
# Core API, IPv4-mapped) was ADMITTED, and a dual-stack socket on the Linux host dials it as
# the IPv4 address. The policy is now an allow-list: embedded IPv4 is unwrapped and checked
# too, and only a global, non-CGNAT address is public.
@pytest.mark.parametrize(
    "address",
    [
        "::ffff:100.90.158.26",  # IPv4-mapped, the Cloud Core API
        "::ffff:645a:9e1a",  # the same address in hex
        "::ffff:100.64.0.1",  # IPv4-mapped CGNAT
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::6440:1",  # NAT64 (RFC 6052 /96) of 100.64.0.1
        "64:ff9b::645a:9e1a",  # NAT64 of 100.90.158.26
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "64:ff9b:1:6440:0:100::",  # local-use NAT64 /48 of 100.64.0.1
        "2002:6440:1::",  # 6to4 of 100.64.0.1
        "2002:645a:9e1a::1",  # 6to4 of 100.90.158.26
        "2001:0:4136:e378:8000:63bf:9bbf:fffe",  # Teredo, client end 100.64.0.1
        "::100.90.158.26",  # IPv4-compatible (::/96)
        "::6440:1",
        "198.18.0.1",  # benchmarking: not private, not global either
        "192.0.0.8",
        "2001:db8::1",  # documentation
        "not-an-address",
        "",
    ],
)
def test_embedded_and_non_global_addresses_are_forbidden(address: str) -> None:
    assert address_is_forbidden(address)


@pytest.mark.parametrize("address", ["::ffff:8.8.8.8", "64:ff9b::808:808", "2606:4700::1111%1"])
def test_an_embedded_public_ipv4_stays_public(address: str) -> None:
    assert not address_is_forbidden(address)


@pytest.mark.parametrize(
    "url",
    [
        "http://[::ffff:100.90.158.26]:8001/v1/devices",
        "http://[::ffff:645a:9e1a]:8001/v1/devices",
        "http://[::ffff:100.64.0.1]/",
        "http://[64:ff9b::6440:1]/",
        "http://[2002:6440:1::]/",
    ],
)
def test_embedded_tailnet_literals_are_refused(url: str) -> None:
    err = _refused(url)
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert err.retryable is False


def test_a_name_resolving_to_a_mapped_tailnet_address_is_refused() -> None:
    err = _refused(
        "https://mapped.example/",
        resolver=_resolver({"mapped.example": ["::ffff:100.90.158.26"]}),
    )
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR


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
