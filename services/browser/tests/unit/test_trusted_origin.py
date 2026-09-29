"""The one narrow SSRF exception: the known broker's report-view route (owner decision 2026-09-29).

Cloud Core mints ``{origin}/v1/artifacts/renders/view?t=<token>`` (ADR-0210) for the report
the owner asked to see, and the office PC's browser worker has to open it. The origin is a
tailnet address (100.64.0.0/10), which the destination policy refuses on purpose. The
exception here is deliberately smaller than "the broker host": ONE origin (scheme, host, port
all equal), ONE route (the report view, nothing else the broker serves), and the origin comes
from the DEVICE's own configuration (the installer writes ``--trusted-origin`` from the broker
URL the device dials), never from a command Cloud Core sends.

The matrix below is the point of the file: everything the old policy refused stays refused,
including the neighbours of the trusted address, and an empty origin is no exception at all.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from browser_agent import worker as worker_module
from browser_agent.destination import (
    TRUSTED_VIEW_PATH,
    parse_trusted_origin,
    require_public_destination,
)
from browser_agent.errors import BrowserError, ErrorClass

BROKER = "http://100.90.158.26:8001"
VIEW = f"{BROKER}/v1/artifacts/renders/view?t=abc123secretToken"


def _no_dns(host: str) -> list[str]:
    raise AssertionError(f"the trusted-origin decision must not resolve names (asked for {host})")


def _resolver(mapping: dict[str, list[str]]):
    def resolve(host: str) -> list[str]:
        return mapping.get(host, [])

    return resolve


# Where the tailnet name of Cloud Core points, for the cases that fall through to the old policy.
_TAILNET_DNS = _resolver({"pagentos-core": ["100.90.158.26"]})


def _check(url: str, origin: str | None = BROKER, resolver=None) -> None:
    require_public_destination(
        url,
        op="navigate",
        resolver=resolver or _no_dns,
        trusted_origin=parse_trusted_origin(origin),
    )


def _refused(url: str, origin: str | None = BROKER, resolver=None) -> BrowserError:
    with pytest.raises(BrowserError) as exc_info:
        _check(url, origin, resolver)
    return exc_info.value


# --------------------------------------------------------------------------- the exception


def test_the_broker_report_view_passes() -> None:
    _check(VIEW)


def test_the_view_route_without_a_query_or_with_a_fragment_passes() -> None:
    # The path is what is compared; a query (the token) and a fragment are not part of it.
    _check(f"{BROKER}{TRUSTED_VIEW_PATH}")
    _check(f"{VIEW}#top")


def test_the_route_is_the_one_the_cloud_mints() -> None:
    assert TRUSTED_VIEW_PATH == "/v1/artifacts/renders/view"


def test_scheme_and_host_case_and_a_trailing_dot_do_not_change_the_answer() -> None:
    _check("HTTP://100.90.158.26:8001/v1/artifacts/renders/view?t=x")
    _check("http://100.90.158.26.:8001/v1/artifacts/renders/view?t=x")


@pytest.mark.parametrize(
    "url",
    [
        # other addresses of the same /24: the exception is one host, not a network
        "http://100.90.158.27:8001/v1/artifacts/renders/view?t=x",
        "http://100.90.158.1:8001/v1/artifacts/renders/view?t=x",
        "http://100.90.158.26:8002/v1/artifacts/renders/view?t=x",
        # the same host on another port (the godseye port among them)
        "http://100.90.158.26:4173/v1/artifacts/renders/view?t=x",
        "http://100.90.158.26:4173/",
        "http://100.90.158.26/v1/artifacts/renders/view?t=x",
        # the same origin, any route but the report view
        "http://100.90.158.26:8001/v1/system/health",
        "http://100.90.158.26:8001/",
        "http://100.90.158.26:8001/v1/artifacts/renders/fetch?t=x",
        "http://100.90.158.26:8001/v1/artifacts/renders/view/extra",
        "http://100.90.158.26:8001/v1/artifacts/renders/view/",
        "http://100.90.158.26:8001/v1/artifacts/renders/viewer",
        "http://100.90.158.26:8001/v1/artifacts/renders/view/../../health",
        "http://100.90.158.26:8001/x/v1/artifacts/renders/view",
        "http://100.90.158.26:8001/V1/artifacts/renders/view",
        # another scheme on the same host and port
        "https://100.90.158.26:8001/v1/artifacts/renders/view?t=x",
    ],
)
def test_everything_next_to_the_trusted_route_is_still_refused(url: str) -> None:
    err = _refused(url)
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert err.retryable is False


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pw@100.90.158.26:8001/v1/artifacts/renders/view?t=x",
        "http://user@100.90.158.26:8001/v1/artifacts/renders/view?t=x",
        "http://evil.example\\@100.90.158.26:8001/v1/artifacts/renders/view?t=x",
        "http://100.90.158.26:8001\\@evil.example/v1/artifacts/renders/view?t=x",
        "http://100.90.158.26:8001\\.evil.example/v1/artifacts/renders/view?t=x",
    ],
)
def test_userinfo_and_backslash_tricks_never_ride_the_exception(url: str) -> None:
    assert _refused(url).error_class == ErrorClass.SECURITY_SCOPE_ERROR


def test_a_hostname_that_resolves_to_the_broker_ip_is_not_trusted() -> None:
    # No resolve-and-trust: the device configured an ADDRESS, and a name that a resolver
    # says maps to it is exactly what a rebinding attack looks like.
    err = _refused(
        "http://broker.example/v1/artifacts/renders/view?t=x",
        resolver=_resolver({"broker.example": ["100.90.158.26"]}),
    )
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR


def test_a_hostname_origin_is_compared_by_name_and_never_resolved() -> None:
    origin = "http://pagentos-core:8001"
    _check("http://pagentos-core:8001/v1/artifacts/renders/view?t=x", origin)
    _check("http://PAGENTOS-CORE.:8001/v1/artifacts/renders/view?t=x", origin)
    # ...another name that resolves to the same address is not the configured name
    err = _refused(
        "http://other-name:8001/v1/artifacts/renders/view?t=x",
        origin,
        resolver=_resolver({"other-name": ["100.90.158.26"]}),
    )
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    # ...and the literal address is not the configured name either
    assert (
        _refused("http://100.90.158.26:8001/v1/artifacts/renders/view?t=x", origin).error_class
        == ErrorClass.SECURITY_SCOPE_ERROR
    )


def test_default_ports_are_made_explicit() -> None:
    # http -> 80, https -> 443, on both sides of the comparison.
    _check("http://100.90.158.26:80/v1/artifacts/renders/view", "http://100.90.158.26")
    _check("http://100.90.158.26/v1/artifacts/renders/view", "http://100.90.158.26:80")
    _check("https://100.90.158.26/v1/artifacts/renders/view", "https://100.90.158.26:443")
    _check("https://100.90.158.26:443/v1/artifacts/renders/view", "https://100.90.158.26")
    assert _refused("https://100.90.158.26/v1/artifacts/renders/view", "http://100.90.158.26")
    assert _refused("http://100.90.158.26:443/v1/artifacts/renders/view", "https://100.90.158.26")


def test_an_ipv6_literal_origin_matches_its_canonical_form() -> None:
    origin = "http://[fd7a:115c:a1e0::1]:8001"
    _check("http://[FD7A:115C:A1E0:0:0:0:0:1]:8001/v1/artifacts/renders/view?t=x", origin)
    assert _refused("http://[fd7a:115c:a1e0::2]:8001/v1/artifacts/renders/view?t=x", origin)


# ---------------------------------------------------------------- no origin, no exception

_PRIVATE_MATRIX = [
    "http://100.90.158.26:8001/v1/artifacts/renders/view?t=x",
    "http://100.64.0.1/",
    "http://100.127.255.254/",
    "http://10.0.0.5/",
    "http://172.16.0.9/",
    "http://192.168.1.1/",
    "http://127.0.0.1:8001/v1/artifacts/renders/view?t=x",
    "http://127.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[fe80::1]/",
    "http://[fd12::1]/",
    "http://0.0.0.0/",
    "http://pagentos-core:4173/",
    "http://localhost/",
]


@pytest.mark.parametrize("origin", [None, "", "   "])
@pytest.mark.parametrize("url", _PRIVATE_MATRIX)
def test_without_a_configured_origin_no_private_address_passes(url: str, origin) -> None:
    err = _refused(url, origin, _TAILNET_DNS)
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR


def test_an_empty_origin_parses_to_no_exception() -> None:
    assert parse_trusted_origin(None) is None
    assert parse_trusted_origin("") is None
    assert parse_trusted_origin("  ") is None


def test_the_public_internet_is_unaffected_by_a_trusted_origin() -> None:
    require_public_destination(
        "https://example.com/news",
        op="navigate",
        resolver=_resolver({"example.com": ["93.184.216.34"]}),
        trusted_origin=parse_trusted_origin(BROKER),
    )


# --------------------------------------------------------------------------- the configured origin


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:8001",
        "http://127.1.2.3:8001",
        "http://[::1]:8001",
        "http://169.254.169.254",
        "http://169.254.1.1:80",
        "http://[fe80::1]:8001",
        "http://224.0.0.1:8001",
        "http://0.0.0.0:8001",
        "http://[::]:8001",
        "http://240.0.0.1:8001",
        "http://255.255.255.255:8001",
        "http://localhost:8001",
        "http://metadata.google.internal",
        "http://printer.local:8001",
        "http://core.internal:8001",
        "http://foo.localhost:8001",
        "http://router.home.arpa",
        "file:///C:/x",
        "ftp://100.90.158.26",
        "javascript:alert(1)",
        "100.90.158.26:8001",
        "100.90.158.26",
        "http://",
        "http://user:pw@100.90.158.26:8001",
        "http://user@100.90.158.26:8001",
        "http://100.90.158.26:8001/v1/artifacts/renders/view",
        "http://100.90.158.26:8001/x",
        "http://100.90.158.26:8001?x=1",
        "http://100.90.158.26:8001/#frag",
        "http://100.90.158.26:0",
        "http://100.90.158.26:99999",
        "http://100.90.158.26:abc",
        "http://2130706433:8001",
        "http://0x7f000001:8001",
        "http://[::ffff:127.0.0.1]:8001",
        "http://[::ffff:169.254.169.254]:8001",
    ],
)
def test_an_unsafe_configured_origin_is_refused(origin: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        parse_trusted_origin(origin)
    assert str(exc_info.value)


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://100.90.158.26:8001", ("http", "100.90.158.26", 8001)),
        ("http://100.90.158.26:8001/", ("http", "100.90.158.26", 8001)),
        ("HTTP://100.90.158.26:8001", ("http", "100.90.158.26", 8001)),
        ("http://100.90.158.26", ("http", "100.90.158.26", 80)),
        ("https://100.90.158.26", ("https", "100.90.158.26", 443)),
        ("http://pagentos-core:8001", ("http", "pagentos-core", 8001)),
        ("http://192.168.1.10:8001", ("http", "192.168.1.10", 8001)),
        ("http://[fd7a:115c:a1e0::1]:8001", ("http", "fd7a:115c:a1e0::1", 8001)),
    ],
)
def test_private_and_tailnet_origins_are_acceptable_and_normalised(origin, expected) -> None:
    parsed = parse_trusted_origin(origin)
    assert parsed is not None
    assert (parsed.scheme, parsed.host, parsed.port) == expected


def test_the_worker_carries_the_parsed_origin_and_enforces_it(tmp_path) -> None:
    args = worker_module.build_arg_parser().parse_args(
        ["--data-dir", str(tmp_path / "a"), "--trusted-origin", BROKER]
    )
    strict = worker_module.Worker(args)
    strict._check_destination(VIEW, op="navigate")
    for other in (
        "http://100.90.158.27:8001/v1/artifacts/renders/view?t=x",
        "http://100.90.158.26:8001/v1/system/health",
        "http://127.0.0.1:8000/fixture",
    ):
        with pytest.raises(BrowserError) as exc_info:
            strict._check_destination(other, op="navigate")
        assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR


def test_the_worker_without_the_option_refuses_the_broker_view(tmp_path) -> None:
    plain = worker_module.Worker(
        worker_module.build_arg_parser().parse_args(["--data-dir", str(tmp_path / "b")])
    )
    with pytest.raises(BrowserError) as exc_info:
        plain._check_destination(VIEW, op="navigate")
    assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR


def test_the_option_is_single_valued() -> None:
    parser = worker_module.build_arg_parser()
    args = parser.parse_args(
        [
            "--data-dir",
            "d",
            "--trusted-origin",
            "http://100.90.158.26:8001",
            "--trusted-origin",
            "http://100.90.158.99:8001",
        ]
    )
    # argparse's store action: the last one wins, there is never a list to widen.
    assert args.trusted_origin.host == "100.90.158.99"


def test_a_bad_origin_stops_the_worker_at_start_with_a_message_on_stderr(tmp_path) -> None:
    src = Path(worker_module.__file__).parents[1]
    completed = subprocess.run(  # noqa: S603 - our own interpreter, our own module, no browser
        [
            sys.executable,
            "-m",
            "browser_agent.worker",
            "--data-dir",
            str(tmp_path / "d"),
            "--trusted-origin",
            "http://169.254.169.254",
        ],
        cwd=src,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode != 0
    assert "--trusted-origin" in completed.stderr
    assert "169.254.169.254" in completed.stderr
    assert completed.stdout.strip() == ""  # no hello: it never got as far as speaking


# --------------------------------------------------------------------------- godseye


def test_godseye_is_not_covered_by_the_broker_exception() -> None:
    # godseye.open opens http://pagentos-core:4173/ (services/api/app/voice/realtime_sessions).
    # The trusted origin is the BROKER (http://100.90.158.26:8001): another host name AND another
    # port AND another route. The exception must not cover it, and this test says so out loud:
    # godseye.open on a device with only this exception still ends in the spoken refusal, and
    # letting it through is a separate, explicit owner decision (a second trusted origin) that
    # nobody has made.
    err = _refused("http://pagentos-core:4173/", BROKER, _TAILNET_DNS)
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    # ...even when the tailnet name is the configured origin: :4173 is not :8001.
    err = _refused("http://pagentos-core:4173/", "http://pagentos-core:8001", _TAILNET_DNS)
    assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR


# --------------------------------------------------------------------------- drift guard


def test_the_route_matches_the_one_cloud_core_mints() -> None:
    # services/browser cannot import services/api, so the mirror is read from the source: if
    # Cloud Core moves the view route this fails here instead of on the owner's desk as a
    # spoken refusal.
    store = (
        Path(worker_module.__file__).parents[3]
        / "services"
        / "api"
        / "app"
        / "artifacts"
        / "render_view_store.py"
    )
    if not store.is_file():  # a services/browser-only checkout
        pytest.skip("services/api is not part of this checkout")
    match = re.search(r'^VIEW_PATH: Final = "([^"]+)"$', store.read_text(encoding="utf-8"), re.M)
    assert match is not None, "render_view_store.VIEW_PATH is not in the canonical form"
    assert match.group(1) == TRUSTED_VIEW_PATH
