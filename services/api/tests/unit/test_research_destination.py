"""app.research.destination: fetch-destination policy (finding HIGH-2).

Every scheme/hostname/IP-range check, plus the DNS-rebinding defence (a
monkeypatched resolver returning a mix of public and private addresses is
rejected), exercised with no real network access.
"""

from __future__ import annotations

import pytest

from app.research.destination import DestinationPolicyError, validate_fetch_target

PUBLIC_IP = "93.184.216.34"  # example.com's real-world address; never dialed in tests


def _ok(url: str, *, resolves_to: list[str] | None = None) -> None:
    validate_fetch_target(url, resolver=lambda host: resolves_to or [PUBLIC_IP])


def _rejected(url: str, *, resolves_to: list[str] | None = None) -> DestinationPolicyError:
    with pytest.raises(DestinationPolicyError) as exc_info:
        validate_fetch_target(url, resolver=lambda host: resolves_to or [PUBLIC_IP])
    return exc_info.value


# --------------------------------------------------------------- schemes


def test_https_public_host_passes() -> None:
    _ok("https://example.com/a")


def test_http_public_host_passes() -> None:
    _ok("http://example.com/a")


def test_javascript_scheme_rejected() -> None:
    _rejected("javascript:alert(1)")


def test_data_scheme_rejected() -> None:
    _rejected("data:text/html,<script>alert(1)</script>")


def test_file_scheme_rejected() -> None:
    _rejected("file:///etc/passwd")


def test_ftp_scheme_rejected() -> None:
    _rejected("ftp://example.com/a")


# --------------------------------------------------------------- userinfo


def test_userinfo_rejected() -> None:
    _rejected("http://user:pass@example.com/")


def test_userinfo_without_password_rejected() -> None:
    _rejected("http://user@example.com/")


# ------------------------------------------------------------- hostname shape


def test_localhost_rejected() -> None:
    _rejected("http://localhost/")


def test_dot_local_suffix_rejected() -> None:
    _rejected("http://printer.local/")


def test_dot_internal_suffix_rejected() -> None:
    _rejected("http://admin.internal/")


def test_dot_localhost_suffix_rejected() -> None:
    _rejected("http://foo.localhost/")


def test_no_host_rejected() -> None:
    _rejected("http:///path")


# ----------------------------------------------------------- IPv4 literals


def test_ipv4_loopback_literal_rejected() -> None:
    _rejected("http://127.0.0.1/")


def test_ipv4_rfc1918_10_literal_rejected() -> None:
    _rejected("http://10.0.0.5/")


def test_ipv4_rfc1918_172_literal_rejected() -> None:
    _rejected("http://172.16.0.5/")


def test_ipv4_rfc1918_192_literal_rejected() -> None:
    _rejected("http://192.168.1.5/")


def test_ipv4_link_local_literal_rejected() -> None:
    _rejected("http://169.254.1.1/")


def test_cloud_metadata_endpoint_rejected() -> None:
    _rejected("http://169.254.169.254/latest/meta-data/")


def test_cgnat_tailnet_literal_rejected() -> None:
    _rejected("http://100.64.0.1/")
    _rejected("http://100.100.100.100/")  # squarely inside 100.64.0.0/10


def test_ipv4_multicast_literal_rejected() -> None:
    _rejected("http://224.0.0.1/")


def test_ipv4_reserved_literal_rejected() -> None:
    _rejected("http://240.0.0.1/")


def test_ipv4_public_literal_passes() -> None:
    _ok(f"http://{PUBLIC_IP}/")


# ----------------------------------------------------------- IPv6 literals


def test_ipv6_loopback_literal_rejected() -> None:
    _rejected("http://[::1]/")


def test_ipv6_link_local_literal_rejected() -> None:
    _rejected("http://[fe80::1]/")


def test_ipv6_ula_literal_rejected() -> None:
    _rejected("http://[fc00::1]/")


def test_ipv6_multicast_literal_rejected() -> None:
    _rejected("http://[ff02::1]/")


# ------------------------------------------------------------ DNS resolution


def test_public_hostname_with_permissive_resolver_passes() -> None:
    validate_fetch_target("https://news.example.com/a", resolver=lambda host: [PUBLIC_IP])


def test_hostname_resolving_to_private_address_rejected() -> None:
    with pytest.raises(DestinationPolicyError):
        validate_fetch_target("https://internal.example.com/a", resolver=lambda host: ["10.1.2.3"])


def test_hostname_resolving_to_both_public_and_private_is_rejected() -> None:
    """A host with two A records, one public and one private, must be
    refused — the pipeline cannot rely on which address Chrome actually
    connects to."""
    with pytest.raises(DestinationPolicyError):
        validate_fetch_target(
            "https://mixed.example.com/a", resolver=lambda host: [PUBLIC_IP, "10.0.0.9"]
        )


def test_resolution_failure_is_a_destination_policy_error() -> None:
    def _fail(host: str) -> list[str]:
        raise OSError("name or service not known")

    with pytest.raises(DestinationPolicyError):
        validate_fetch_target("https://nxdomain.example.invalid/", resolver=_fail)


def test_error_class_is_security_scope_error() -> None:
    error = _rejected("http://127.0.0.1/")
    assert error.error_class == "security_scope_error"
