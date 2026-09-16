"""app.webpush.provider: the SSRF allowlist and RFC 8030 status-code mapping.

``HttpPushProvider`` is tested against ``httpx.MockTransport`` — a real ``httpx.Client``
running its full request pipeline, but never touching a socket — so these tests exercise
the actual HTTP client code path (headers/body as sent, response parsing) rather than a
hand-rolled substitute for it.
"""

from __future__ import annotations

import httpx
import pytest

from app.webpush.provider import (
    ALLOWED_PUSH_HOSTS,
    FakePushProvider,
    HttpPushProvider,
    PushError,
    is_allowed_push_host,
    validate_push_endpoint,
)

# ------------------------------------------------------------- the allowlist itself


@pytest.mark.parametrize(
    "host",
    [
        "fcm.googleapis.com",
        "FCM.GOOGLEAPIS.COM",  # case-insensitive
        "fcm.googleapis.com.",  # trailing dot (valid DNS, browsers can produce it)
        "updates.push.services.mozilla.com",
        "push.services.mozilla.com",
        "web.push.apple.com",
        "notify.windows.com",
        "bn1.notify.windows.com",
        "wns2-bn1p.notify.windows.com",
    ],
)
def test_known_push_service_hosts_are_allowed(host: str) -> None:
    assert is_allowed_push_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "",
        "evil.example.com",
        "fcm.googleapis.com.evil.example.com",  # suffix trick: NOT a subdomain of fcm's own domain
        "notify.windows.com.evil.example.com",
        "127.0.0.1",
        "169.254.169.254",  # cloud metadata endpoint — the canonical SSRF target
        "localhost",
        "internal-service.local",
        "xnotify.windows.com",  # near-miss: not preceded by a dot
    ],
)
def test_unknown_hosts_are_never_allowed(host: str) -> None:
    assert is_allowed_push_host(host) is False


def test_allowed_push_hosts_is_a_plain_hostname_set_not_urls() -> None:
    """Guards against a future edit accidentally storing a scheme or path in the
    allowlist, which would make every comparison silently always-false (fail open in
    the sense that nothing would ever match) or invite a matching bug."""
    for host in ALLOWED_PUSH_HOSTS:
        assert "/" not in host
        assert ":" not in host


# ------------------------------------------------------------- validate_push_endpoint


def test_validate_push_endpoint_accepts_a_known_https_host() -> None:
    validate_push_endpoint("https://fcm.googleapis.com/fcm/send/abc123")  # must not raise


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/abc",  # not https
        "https://evil.example.com/steal",  # not an allowed host
        "https://user:pass@fcm.googleapis.com/fcm/send/abc",  # userinfo
        "ftp://fcm.googleapis.com/fcm/send/abc",
        "https://169.254.169.254/latest/meta-data",  # cloud metadata via https
        "javascript:alert(1)",
        "",
    ],
)
def test_validate_push_endpoint_refuses_everything_else(endpoint: str) -> None:
    with pytest.raises(PushError) as excinfo:
        validate_push_endpoint(endpoint)
    assert excinfo.value.reason == PushError.REASON_INVALID_ENDPOINT


def test_http_provider_refuses_before_any_network_call() -> None:
    """The allowlist check must happen BEFORE a request is dispatched — a provider
    wired to a transport that would raise on any real connection attempt (there is
    none here) proves the SSRF-refused endpoint never reaches httpx at all."""

    def _must_not_be_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HttpPushProvider dialled a non-allowlisted host")

    provider = HttpPushProvider()
    provider._client = lambda timeout_s: httpx.Client(
        transport=httpx.MockTransport(_must_not_be_called)
    )
    with pytest.raises(PushError) as excinfo:
        provider.send(endpoint="https://evil.example.com/x", headers={}, body=b"", timeout_s=1.0)
    assert excinfo.value.reason == PushError.REASON_INVALID_ENDPOINT


# ------------------------------------------------------------- status-code mapping


def _provider_with(handler) -> HttpPushProvider:
    provider = HttpPushProvider()
    provider._client = lambda timeout_s: httpx.Client(transport=httpx.MockTransport(handler))
    return provider


@pytest.mark.parametrize("status", [201, 202])
def test_accepted_statuses_do_not_raise(status: int) -> None:
    provider = _provider_with(lambda request: httpx.Response(status))
    provider.send(
        endpoint="https://fcm.googleapis.com/fcm/send/abc", headers={}, body=b"body", timeout_s=1.0
    )  # must not raise


@pytest.mark.parametrize("status", [404, 410])
def test_gone_statuses_map_to_expired(status: int) -> None:
    provider = _provider_with(lambda request: httpx.Response(status))
    with pytest.raises(PushError) as excinfo:
        provider.send(
            endpoint="https://fcm.googleapis.com/fcm/send/abc", headers={}, body=b"", timeout_s=1.0
        )
    assert excinfo.value.reason == PushError.REASON_EXPIRED
    assert excinfo.value.status_code == status


def test_413_maps_to_too_large() -> None:
    provider = _provider_with(lambda request: httpx.Response(413))
    with pytest.raises(PushError) as excinfo:
        provider.send(
            endpoint="https://fcm.googleapis.com/fcm/send/abc", headers={}, body=b"", timeout_s=1.0
        )
    assert excinfo.value.reason == PushError.REASON_TOO_LARGE


def test_429_maps_to_rate_limited_and_carries_retry_after() -> None:
    provider = _provider_with(lambda request: httpx.Response(429, headers={"Retry-After": "30"}))
    with pytest.raises(PushError) as excinfo:
        provider.send(
            endpoint="https://fcm.googleapis.com/fcm/send/abc", headers={}, body=b"", timeout_s=1.0
        )
    assert excinfo.value.reason == PushError.REASON_RATE_LIMITED
    assert excinfo.value.retry_after_s == 30.0


@pytest.mark.parametrize("status", [500, 502, 503, 599])
def test_5xx_maps_to_server_error(status: int) -> None:
    provider = _provider_with(lambda request: httpx.Response(status))
    with pytest.raises(PushError) as excinfo:
        provider.send(
            endpoint="https://fcm.googleapis.com/fcm/send/abc", headers={}, body=b"", timeout_s=1.0
        )
    assert excinfo.value.reason == PushError.REASON_SERVER_ERROR


@pytest.mark.parametrize("status", [400, 401, 403, 451])
def test_other_4xx_maps_to_provider_error(status: int) -> None:
    provider = _provider_with(lambda request: httpx.Response(status))
    with pytest.raises(PushError) as excinfo:
        provider.send(
            endpoint="https://fcm.googleapis.com/fcm/send/abc", headers={}, body=b"", timeout_s=1.0
        )
    assert excinfo.value.reason == PushError.REASON_PROVIDER_ERROR


def test_timeout_maps_to_timeout_reason() -> None:
    def _raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route", request=request)

    provider = _provider_with(_raise_timeout)
    with pytest.raises(PushError) as excinfo:
        provider.send(
            endpoint="https://fcm.googleapis.com/fcm/send/abc", headers={}, body=b"", timeout_s=1.0
        )
    assert excinfo.value.reason == PushError.REASON_TIMEOUT


def test_provider_sends_the_exact_headers_and_body_given() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.read()
        return httpx.Response(201)

    provider = _provider_with(_capture)
    provider.send(
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        headers={"TTL": "60", "Urgency": "high", "Authorization": "vapid t=x, k=y"},
        body=b"\x01\x02\x03",
        timeout_s=1.0,
    )
    assert captured["body"] == b"\x01\x02\x03"
    assert captured["headers"]["ttl"] == "60"
    assert captured["headers"]["urgency"] == "high"
    assert captured["headers"]["authorization"] == "vapid t=x, k=y"


# ------------------------------------------------------------- FakePushProvider


def test_fake_push_provider_also_enforces_the_allowlist() -> None:
    """The fake is a fixture for OTHER modules' tests, not an escape hatch from the SSRF
    rule — a test that accidentally hands it a non-allowlisted endpoint must fail the
    same way production would, not silently record a fake success."""
    fake = FakePushProvider()
    with pytest.raises(PushError):
        fake.send(endpoint="https://evil.example.com/x", headers={}, body=b"", timeout_s=1.0)


def test_fake_push_provider_defaults_to_accept_and_records_calls() -> None:
    fake = FakePushProvider()
    fake.send(
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        headers={"a": "b"},
        body=b"hi",
        timeout_s=1.0,
    )
    assert len(fake.calls) == 1
    assert fake.calls[0]["endpoint"] == "https://fcm.googleapis.com/fcm/send/abc"


def test_fake_push_provider_can_be_configured_to_raise() -> None:
    fake = FakePushProvider(
        responses={"https://fcm.googleapis.com/fcm/send/gone": PushError(PushError.REASON_EXPIRED)}
    )
    with pytest.raises(PushError) as excinfo:
        fake.send(
            endpoint="https://fcm.googleapis.com/fcm/send/gone", headers={}, body=b"", timeout_s=1.0
        )
    assert excinfo.value.reason == PushError.REASON_EXPIRED
