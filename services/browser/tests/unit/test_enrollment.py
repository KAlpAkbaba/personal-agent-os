"""Enrollment model, registry, and attach authorization rules (no browser)."""

from pathlib import Path

import pytest

from browser_agent import (
    BrowserEnrollment,
    BrowserError,
    EnrollmentRegistry,
    ErrorClass,
    ExistingSessionBackend,
    ManagedBackend,
    Transport,
)
from browser_agent.enrollment import (
    get_research_authorized_enrollment,
    is_loopback_endpoint,
    require_research_authorization,
)


def test_cdp_loopback_convenience_constructor() -> None:
    enrollment = BrowserEnrollment.cdp_loopback("http://127.0.0.1:9222", name="owner-edge")
    assert enrollment.transport is Transport.CDP_LOOPBACK
    assert enrollment.endpoint == "http://127.0.0.1:9222"
    assert enrollment.name == "owner-edge"
    assert enrollment.id
    assert enrollment.created_at.tzinfo is not None


def test_registry_register_get_list_revoke_in_memory() -> None:
    registry = EnrollmentRegistry()
    first = BrowserEnrollment.cdp_loopback("http://127.0.0.1:9222", name="a")
    second = BrowserEnrollment.cdp_loopback("http://localhost:9223", name="b")
    registry.register(first)
    registry.register(second)
    assert registry.get(first.id) is first
    assert [e.name for e in registry.list()] == ["a", "b"]
    registry.revoke(first.id)
    with pytest.raises(BrowserError) as excinfo:
        registry.get(first.id)
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR


def test_registry_duplicate_id_is_validation_error() -> None:
    registry = EnrollmentRegistry()
    enrollment = BrowserEnrollment.cdp_loopback("http://127.0.0.1:9222")
    registry.register(enrollment)
    with pytest.raises(BrowserError) as excinfo:
        registry.register(enrollment)
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR


def test_registry_revoke_unknown_is_validation_error() -> None:
    with pytest.raises(BrowserError) as excinfo:
        EnrollmentRegistry().revoke("nope")
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR


def test_registry_file_backed_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "enrollments.json"
    registry = EnrollmentRegistry(path)
    enrollment = BrowserEnrollment.cdp_loopback(
        "http://127.0.0.1:9222",
        name="persisted",
        capability_overrides={"downloads": False},
    )
    registry.register(enrollment)

    reloaded = EnrollmentRegistry(path)
    restored = reloaded.get(enrollment.id)
    assert restored.name == "persisted"
    assert restored.endpoint == enrollment.endpoint
    assert restored.transport is Transport.CDP_LOOPBACK
    assert restored.capability_overrides == {"downloads": False}
    assert restored.created_at == enrollment.created_at

    reloaded.revoke(enrollment.id)
    assert EnrollmentRegistry(path).list() == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://192.168.1.10:9222",
        "http://0.0.0.0:9222",
        "http://example.com:9222",
        "ftp://127.0.0.1:9222",
        "not-a-url",
    ],
)
def test_existing_session_backend_rejects_non_loopback_endpoint(endpoint: str) -> None:
    enrollment = BrowserEnrollment.cdp_loopback(endpoint)
    with pytest.raises(BrowserError) as excinfo:
        ExistingSessionBackend(enrollment)
    err = excinfo.value
    assert err.error_class is ErrorClass.VALIDATION_ERROR
    assert err.retryable is False


@pytest.mark.parametrize(
    "endpoint",
    ["http://127.0.0.1:9222", "http://localhost:9222", "ws://127.0.0.1:9222/devtools"],
)
def test_existing_session_backend_accepts_loopback_endpoint(endpoint: str) -> None:
    ExistingSessionBackend(BrowserEnrollment.cdp_loopback(endpoint))  # no raise


def test_extension_bridge_transport_is_reserved_not_implemented() -> None:
    enrollment = BrowserEnrollment(
        id="e1",
        name="future",
        transport=Transport.EXTENSION_BRIDGE,
        endpoint="http://127.0.0.1:1",
    )
    with pytest.raises(BrowserError) as excinfo:
        ExistingSessionBackend(enrollment)
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR
    assert "extension_bridge" in excinfo.value.message


def test_managed_backend_rejects_real_browser_profile_dirs() -> None:
    # Every shape, on every host OS. The Windows-shaped strings used to pass on Linux
    # because a backslash is not a separator there: the whole path collapsed into ONE
    # segment and the marker scan matched nothing. The guard was therefore a no-op on
    # exactly the platform the browser agent runs on in production, and this test could
    # only ever have caught it by running there — which it now does, in CI.
    real_profiles = [
        # Windows
        r"C:\Users\alpak\AppData\Local\Google\Chrome\User Data",
        r"C:\Users\alpak\AppData\Local\Microsoft\Edge\User Data\Default",
        r"C:\Users\alpak\AppData\Local\BraveSoftware\Brave-Browser\User Data",
        # Linux — the container the browser agent actually runs in
        "/home/owner/.config/google-chrome",
        "/home/owner/.config/google-chrome/Default",
        "/home/owner/.config/chromium",
        "/home/owner/.config/microsoft-edge",
        # macOS
        "/Users/owner/Library/Application Support/Google/Chrome",
        "/Users/owner/Library/Application Support/Microsoft Edge/Default",
    ]
    for profile in real_profiles:
        with pytest.raises(BrowserError) as excinfo:
            ManagedBackend(profile_dir=profile)
        assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR


def test_managed_backend_accepts_dedicated_profile_dir(tmp_path: Path) -> None:
    ManagedBackend(profile_dir=tmp_path / "pagentos-profile")  # no raise


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.evil.example:9222",
        "http://127.0.0.1.evil.example:9222",
        "ws://localhost.evil.example:9222",
    ],
)
def test_loopback_lookalike_hostnames_are_rejected(endpoint: str) -> None:
    # Regression for M2 security finding #1: a DNS name that merely *starts
    # with* "127." is routable, not loopback, and must never pass.
    assert is_loopback_endpoint(endpoint) is False


# --------------------------------------------------------------------------- #
# M13: research-use authorization gate (ADR-0035)
# --------------------------------------------------------------------------- #


def test_enrollment_defaults_to_not_research_authorized() -> None:
    enrollment = BrowserEnrollment.cdp_loopback("http://127.0.0.1:9222", name="owner-edge")
    assert enrollment.owner_authorized_for_research is False


def test_require_research_authorization_refuses_unauthorized_enrollment() -> None:
    enrollment = BrowserEnrollment.cdp_loopback("http://127.0.0.1:9222", name="owner-edge")
    with pytest.raises(BrowserError) as excinfo:
        require_research_authorization(enrollment)
    assert excinfo.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR
    assert excinfo.value.retryable is False


def test_require_research_authorization_allows_explicitly_granted_enrollment() -> None:
    enrollment = BrowserEnrollment.cdp_loopback(
        "http://127.0.0.1:9222", name="owner-edge", owner_authorized_for_research=True
    )
    require_research_authorization(enrollment)  # no raise


def test_get_research_authorized_enrollment_refuses_unknown_id() -> None:
    registry = EnrollmentRegistry()
    with pytest.raises(BrowserError) as excinfo:
        get_research_authorized_enrollment(registry, "nope")
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR


def test_get_research_authorized_enrollment_refuses_registered_but_unauthorized() -> None:
    registry = EnrollmentRegistry()
    enrollment = BrowserEnrollment.cdp_loopback("http://127.0.0.1:9222", name="dev-attach")
    registry.register(enrollment)
    with pytest.raises(BrowserError) as excinfo:
        get_research_authorized_enrollment(registry, enrollment.id)
    assert excinfo.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR


def test_get_research_authorized_enrollment_returns_authorized_record() -> None:
    registry = EnrollmentRegistry()
    enrollment = BrowserEnrollment.cdp_loopback(
        "http://127.0.0.1:9222", name="owner-chrome", owner_authorized_for_research=True
    )
    registry.register(enrollment)
    resolved = get_research_authorized_enrollment(registry, enrollment.id)
    assert resolved.id == enrollment.id
    assert resolved.owner_authorized_for_research is True


def test_research_authorization_flag_round_trips_through_file_backing(tmp_path: Path) -> None:
    path = tmp_path / "enrollments.json"
    registry = EnrollmentRegistry(path)
    enrollment = BrowserEnrollment.cdp_loopback(
        "http://127.0.0.1:9222", name="owner-chrome", owner_authorized_for_research=True
    )
    registry.register(enrollment)

    reloaded = EnrollmentRegistry(path)
    restored = reloaded.get(enrollment.id)
    assert restored.owner_authorized_for_research is True
    require_research_authorization(restored)  # no raise


def test_research_authorization_absent_field_defaults_false_never_inferred_true(
    tmp_path: Path,
) -> None:
    # A record written before M13 (or hand-edited) has no such key at all;
    # a missing field must never be treated as an implicit grant.
    import json

    path = tmp_path / "enrollments.json"
    old_record = BrowserEnrollment.cdp_loopback("http://127.0.0.1:9222", name="pre-m13").as_dict()
    del old_record["owner_authorized_for_research"]
    path.write_text(json.dumps({"enrollments": [old_record]}), encoding="utf-8")

    registry = EnrollmentRegistry(path)
    restored = registry.list()[0]
    assert restored.owner_authorized_for_research is False
    with pytest.raises(BrowserError) as excinfo:
        require_research_authorization(restored)
    assert excinfo.value.error_class is ErrorClass.SECURITY_SCOPE_ERROR
