"""M9 unit tests: the push provider seam.

Two properties matter and both are asserted here.

1. **The fake is a complete, deterministic transport.** It satisfies the
   Protocol, it records what it delivered, it collapses duplicates, and it
   refuses to deliver to a registration whose live token it does not hold.

2. **The real adapters are INERT.** With no credentials the send path raises
   `provider_auth_missing` and performs *no I/O at all* — asserted by making
   `_send` explode if it is ever reached. Request construction is pure and is
   asserted directly. The only HTTP any test here performs is through an
   `httpx.MockTransport`, so no socket is ever opened.
"""

from __future__ import annotations

import httpx
import pytest

from app.mobile import providers as P
from app.mobile.config import ApnsCredentials, FcmCredentials, WebPushCredentials
from app.mobile.errors import MobileError, MobileErrorClass

MESSAGE = P.PushMessage(
    title="Araştırma tamamlandı",
    body="Rapor hazır.",
    data={"kind": "artifact_ready", "artifact_id": "abc"},
    collapse_key="artifact:abc",
)


# ------------------------------------------------------------------ the fake


def test_fake_satisfies_the_protocol() -> None:
    assert isinstance(P.FakePushProvider(), P.PushProvider)
    for provider in P.build_registry().values():
        assert isinstance(provider, P.PushProvider)


def test_registry_holds_every_declared_transport() -> None:
    assert sorted(P.build_registry()) == ["apns", "fake", "fcm", "webpush"]


def test_register_returns_a_hash_and_never_the_token() -> None:
    provider = P.FakePushProvider()
    registered = provider.register(registration_key="r1", token="device-token-1")
    assert registered.token_hash == P.hash_token("device-token-1")
    assert "device-token-1" not in repr(registered)
    assert registered.token_fingerprint == P.fingerprint("device-token-1")
    assert provider.has_live_token("r1")


def test_deliver_records_the_message_without_the_token() -> None:
    provider = P.FakePushProvider()
    provider.register(registration_key="r1", token="device-token-1")
    delivery = provider.deliver(registration_key="r1", message=MESSAGE)
    assert delivery.provider == "fake"
    assert delivery.status == "delivered"
    assert "device-token-1" not in repr(delivery)
    assert provider.deliveries_for("r1") == [delivery]
    assert delivery.to_dict()["data"]["kind"] == "artifact_ready"


def test_collapse_key_replaces_the_earlier_unread_notification() -> None:
    provider = P.FakePushProvider()
    provider.register(registration_key="r1", token="device-token-1")
    provider.deliver(registration_key="r1", message=MESSAGE)
    provider.deliver(registration_key="r1", message=MESSAGE)
    # A retrying workflow announces twice; the owner sees one unread item.
    assert len(provider.deliveries_for("r1")) == 1


def test_uncollapsed_messages_stack() -> None:
    provider = P.FakePushProvider()
    provider.register(registration_key="r1", token="t-1234567890")
    plain = P.PushMessage(title="A", body="b")
    provider.deliver(registration_key="r1", message=plain)
    provider.deliver(registration_key="r1", message=plain)
    assert len(provider.deliveries_for("r1")) == 2


def test_deliver_without_a_live_token_is_typed_and_not_retryable() -> None:
    provider = P.FakePushProvider()
    with pytest.raises(MobileError) as exc:
        provider.deliver(registration_key="ghost", message=MESSAGE)
    assert exc.value.error_class == MobileErrorClass.PUSH_TOKEN_INVALID
    assert exc.value.retryable is False


def test_invalidate_drops_the_live_token() -> None:
    provider = P.FakePushProvider()
    provider.register(registration_key="r1", token="device-token-1")
    assert provider.invalidate(registration_key="r1", reason="unregistered") is True
    assert provider.invalidate(registration_key="r1") is False
    assert not provider.has_live_token("r1")
    with pytest.raises(MobileError):
        provider.deliver(registration_key="r1", message=MESSAGE)


def test_delivery_log_is_bounded() -> None:
    provider = P.FakePushProvider(log_size=3)
    provider.register(registration_key="r1", token="device-token-1")
    for i in range(10):
        provider.deliver(registration_key="r1", message=P.PushMessage(title=f"m{i}", body=""))
    assert len(provider.deliveries) == 3


# --------------------------------------------------------------- value bounds


@pytest.mark.parametrize("token", ["", "short", "x" * (P.MAX_TOKEN_CHARS + 1)])
def test_token_bounds_are_enforced(token: str) -> None:
    with pytest.raises(MobileError) as exc:
        P.validate_token(token, provider="fake")
    assert exc.value.error_class == MobileErrorClass.VALIDATION_ERROR


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("", "ok"),
        ("   ", "ok"),
        ("t" * (P.MAX_TITLE_CHARS + 1), "ok"),
        ("ok", "b" * (P.MAX_BODY_CHARS + 1)),
    ],
)
def test_push_message_rejects_unbounded_copy(title: str, body: str) -> None:
    """A push carries readiness, never the report (constitution §3)."""
    with pytest.raises(MobileError):
        P.PushMessage(title=title, body=body)


def test_capabilities_declare_platforms_and_serialise() -> None:
    caps = P.FakePushProvider().capabilities()
    assert caps.supports_platform("iOS") is True
    assert caps.supports_platform("smart-fridge") is False
    assert caps.to_dict()["requires_credentials"] is False
    assert P.FcmPushProvider().capabilities().to_dict()["requires_credentials"] is True


# ----------------------------------------------------------- inert by default


def _explode(*_args, **_kwargs):  # pragma: no cover - only called on failure
    raise AssertionError("a real adapter performed I/O without credentials")


@pytest.mark.parametrize(
    ("factory", "name"),
    [
        (P.FcmPushProvider, "fcm"),
        (P.ApnsPushProvider, "apns"),
        (P.WebPushProvider, "webpush"),
    ],
)
def test_real_adapters_are_inert_without_credentials(monkeypatch, factory, name) -> None:
    monkeypatch.setattr(P, "_send", _explode)
    provider = factory()
    provider.register(registration_key="r1", token="a-real-looking-token-value")
    with pytest.raises(MobileError) as exc:
        provider.deliver(registration_key="r1", message=MESSAGE)
    assert exc.value.error_class == MobileErrorClass.PROVIDER_AUTH_MISSING
    assert exc.value.provider == name
    # The refusal names the owner action, and carries no credential material.
    assert "PAGENTOS_PUSH_" in exc.value.message


def test_real_adapters_check_the_token_before_the_credentials(monkeypatch) -> None:
    """An unregistered target fails as a dead token, not as a missing key."""
    monkeypatch.setattr(P, "_send", _explode)
    with pytest.raises(MobileError) as exc:
        P.FcmPushProvider().deliver(registration_key="ghost", message=MESSAGE)
    assert exc.value.error_class == MobileErrorClass.PUSH_TOKEN_INVALID


# --------------------------------------------------- pure request construction


def test_fcm_builds_the_http_v1_request() -> None:
    provider = P.FcmPushProvider(FcmCredentials(project_id="proj", access_token="tok"))
    req = provider.build_request(token="device-token", message=MESSAGE)
    assert req.method == "POST"
    assert req.url == "https://fcm.googleapis.com/v1/projects/proj/messages:send"
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.json_body is not None
    message = req.json_body["message"]
    assert message["token"] == "device-token"
    assert message["notification"]["title"] == MESSAGE.title
    assert message["data"]["artifact_id"] == "abc"
    assert message["android"]["priority"] == "HIGH"
    assert message["android"]["collapse_key"] == "artifact:abc"


def test_apns_builds_the_device_request_and_honours_sandbox() -> None:
    creds = ApnsCredentials(bundle_id="dev.pagentos.app", jwt="jwt-value", sandbox=True)
    req = P.ApnsPushProvider(creds).build_request(token="dev-token", message=MESSAGE)
    assert req.url == "https://api.sandbox.push.apple.com/3/device/dev-token"
    assert req.headers["apns-topic"] == "dev.pagentos.app"
    assert req.headers["authorization"] == "bearer jwt-value"
    assert req.headers["apns-priority"] == "10"
    assert req.headers["apns-collapse-id"] == "artifact:abc"
    assert req.json_body is not None
    assert req.json_body["aps"]["alert"]["title"] == MESSAGE.title
    # Time Sensitive: a readiness notification must not be held by Focus.
    assert req.json_body["aps"]["interruption-level"] == "time-sensitive"
    assert req.json_body["pagentos_artifact_id"] == "abc"


def test_apns_production_host_when_not_sandbox() -> None:
    creds = ApnsCredentials(bundle_id="dev.pagentos.app", jwt="jwt")
    req = P.ApnsPushProvider(creds).build_request(token="t", message=MESSAGE)
    assert req.url.startswith("https://api.push.apple.com/3/device/")


def test_webpush_posts_to_the_subscription_endpoint_and_declares_no_data() -> None:
    creds = WebPushCredentials(public_key="pub", private_key="priv", subject="mailto:o@x")
    provider = P.WebPushProvider(creds)
    endpoint = "https://push.example.test/subscription/abc"
    req = provider.build_request(token=endpoint, message=MESSAGE)
    assert req.url == endpoint
    assert req.headers["Urgency"] == "high"
    assert req.headers["Topic"] == "artifact:abc"
    # RFC 8291 payload encryption is not implemented, so the capability says no
    # rather than the adapter shipping a cleartext payload.
    assert provider.capabilities().data_payload is False
    assert req.data == b""


# --------------------------------------------------------- mocked HTTP only


def _mock_httpx(monkeypatch, handler) -> None:
    """Route every httpx.Client the adapters build through a MockTransport."""
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_fcm_delivers_over_mocked_http(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"name": "projects/proj/messages/1"})

    _mock_httpx(monkeypatch, handler)
    provider = P.FcmPushProvider(FcmCredentials(project_id="proj", access_token="tok"))
    provider.register(registration_key="r1", token="device-token")
    delivery = provider.deliver(registration_key="r1", message=MESSAGE)
    assert delivery.provider == "fcm"
    assert "projects/proj/messages/1" in delivery.detail
    assert seen["url"] == "https://fcm.googleapis.com/v1/projects/proj/messages:send"
    assert seen["auth"] == "Bearer tok"


def test_apns_delivers_over_mocked_http(monkeypatch) -> None:
    _mock_httpx(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"apns-id": "APNS-1"}, json={}),
    )
    provider = P.ApnsPushProvider(ApnsCredentials(bundle_id="b", jwt="j"))
    provider.register(registration_key="r1", token="device-token")
    assert "APNS-1" in provider.deliver(registration_key="r1", message=MESSAGE).detail


def test_webpush_delivers_over_mocked_http(monkeypatch) -> None:
    _mock_httpx(monkeypatch, lambda request: httpx.Response(201))
    creds = WebPushCredentials(public_key="pub", private_key="priv")
    provider = P.WebPushProvider(creds)
    provider.register(registration_key="r1", token="https://push.example.test/s/abc")
    assert provider.deliver(registration_key="r1", message=MESSAGE).status == "delivered"


@pytest.mark.parametrize("status", [404, 410])
def test_a_dead_token_is_typed_as_invalid_not_as_a_transport_failure(
    monkeypatch, status: int
) -> None:
    _mock_httpx(monkeypatch, lambda request: httpx.Response(status, json={}))
    provider = P.FcmPushProvider(FcmCredentials(project_id="p", access_token="t"))
    provider.register(registration_key="r1", token="device-token")
    with pytest.raises(MobileError) as exc:
        provider.deliver(registration_key="r1", message=MESSAGE)
    assert exc.value.error_class == MobileErrorClass.PUSH_TOKEN_INVALID
    assert exc.value.retryable is False


def test_a_server_error_is_retryable(monkeypatch) -> None:
    _mock_httpx(monkeypatch, lambda request: httpx.Response(503, json={}))
    provider = P.FcmPushProvider(FcmCredentials(project_id="p", access_token="t"))
    provider.register(registration_key="r1", token="device-token")
    with pytest.raises(MobileError) as exc:
        provider.deliver(registration_key="r1", message=MESSAGE)
    assert exc.value.error_class == MobileErrorClass.DEPENDENCY_UNAVAILABLE
    assert exc.value.retryable is True


def test_a_timeout_is_typed_as_a_timeout(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    _mock_httpx(monkeypatch, handler)
    provider = P.FcmPushProvider(FcmCredentials(project_id="p", access_token="t"))
    provider.register(registration_key="r1", token="device-token")
    with pytest.raises(MobileError) as exc:
        provider.deliver(registration_key="r1", message=MESSAGE)
    assert exc.value.error_class == MobileErrorClass.TIMEOUT
    assert exc.value.retryable is True


def test_a_transport_error_is_dependency_unavailable(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _mock_httpx(monkeypatch, handler)
    provider = P.ApnsPushProvider(ApnsCredentials(bundle_id="b", jwt="j"))
    provider.register(registration_key="r1", token="device-token")
    with pytest.raises(MobileError) as exc:
        provider.deliver(registration_key="r1", message=MESSAGE)
    assert exc.value.error_class == MobileErrorClass.DEPENDENCY_UNAVAILABLE


# ------------------------------------------------------------- env activation


def test_credentials_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("PAGENTOS_PUSH_FCM_PROJECT_ID", raising=False)
    monkeypatch.delenv("PAGENTOS_PUSH_FCM_ACCESS_TOKEN", raising=False)
    assert FcmCredentials.from_env().configured is False
    monkeypatch.setenv("PAGENTOS_PUSH_FCM_PROJECT_ID", "p")
    monkeypatch.setenv("PAGENTOS_PUSH_FCM_ACCESS_TOKEN", "t")
    assert FcmCredentials.from_env().configured is True


def test_each_provider_answers_whether_it_is_activated() -> None:
    """The activation question has one answer site, not a private-attr peek."""
    assert P.FakePushProvider().configured is True
    assert P.FcmPushProvider(FcmCredentials()).configured is False
    assert (
        P.FcmPushProvider(FcmCredentials(project_id="p", access_token="t")).configured
        is True
    )
    assert P.ApnsPushProvider(ApnsCredentials(bundle_id="b", jwt="j")).configured is True
    assert P.WebPushProvider(WebPushCredentials()).configured is False


def test_apns_and_webpush_activation_needs_both_halves(monkeypatch) -> None:
    monkeypatch.setenv("PAGENTOS_PUSH_APNS_BUNDLE_ID", "b")
    monkeypatch.delenv("PAGENTOS_PUSH_APNS_JWT", raising=False)
    assert ApnsCredentials.from_env().configured is False
    monkeypatch.setenv("PAGENTOS_PUSH_VAPID_PUBLIC_KEY", "pub")
    monkeypatch.delenv("PAGENTOS_PUSH_VAPID_PRIVATE_KEY", raising=False)
    assert WebPushCredentials.from_env().configured is False
