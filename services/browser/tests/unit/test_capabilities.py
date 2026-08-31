"""Capability declarations per backend and the orchestrator guard (no browser)."""

from dataclasses import fields

import pytest

from browser_agent import (
    CAPABILITY_FLAGS,
    BrowserCapabilities,
    BrowserEnrollment,
    BrowserError,
    ErrorClass,
    ExistingSessionBackend,
    ManagedBackend,
    require_capability,
)

EXPECTED_FLAGS = (
    "authenticated_session",
    "downloads",
    "uploads",
    "extensions",
    "existing_tabs",
    "multiple_windows",
    "visual_fallback",
)


def test_capability_model_has_exactly_the_mandated_flags() -> None:
    assert CAPABILITY_FLAGS == EXPECTED_FLAGS
    assert tuple(f.name for f in fields(BrowserCapabilities)) == EXPECTED_FLAGS


def test_managed_isolated_backend_capabilities() -> None:
    capabilities = ManagedBackend().capabilities()
    assert capabilities.as_dict() == {
        "authenticated_session": False,
        "downloads": True,
        "uploads": True,
        "extensions": False,
        "existing_tabs": False,
        "multiple_windows": True,
        "visual_fallback": False,
    }


def test_managed_persistent_profile_backend_capabilities(tmp_path) -> None:
    capabilities = ManagedBackend(profile_dir=tmp_path / "agent-profile").capabilities()
    assert capabilities.as_dict() == {
        "authenticated_session": True,
        "downloads": True,
        "uploads": True,
        "extensions": False,
        "existing_tabs": False,
        "multiple_windows": True,
        "visual_fallback": False,
    }


def test_existing_session_backend_capabilities() -> None:
    enrollment = BrowserEnrollment.cdp_loopback("http://127.0.0.1:9222")
    capabilities = ExistingSessionBackend(enrollment).capabilities()
    assert capabilities.as_dict() == {
        "authenticated_session": True,
        "downloads": True,
        "uploads": True,
        "extensions": True,
        "existing_tabs": True,
        "multiple_windows": True,
        "visual_fallback": False,
    }


def test_enrollment_capability_overrides_narrow_the_declaration() -> None:
    enrollment = BrowserEnrollment.cdp_loopback(
        "http://127.0.0.1:9222",
        capability_overrides={"downloads": False, "uploads": False},
    )
    capabilities = ExistingSessionBackend(enrollment).capabilities()
    assert capabilities.downloads is False
    assert capabilities.uploads is False
    assert capabilities.authenticated_session is True  # untouched flags remain


def test_unknown_capability_override_is_validation_error() -> None:
    base = ManagedBackend().capabilities()
    with pytest.raises(BrowserError) as excinfo:
        base.with_overrides({"teleportation": True})
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR


def test_require_capability_passes_when_granted() -> None:
    require_capability(ManagedBackend(), "downloads")  # no raise


def test_require_capability_raises_typed_capability_missing() -> None:
    backend = ManagedBackend()  # isolated: existing_tabs is False
    with pytest.raises(BrowserError) as excinfo:
        require_capability(backend, "existing_tabs")
    err = excinfo.value
    assert err.error_class is ErrorClass.CAPABILITY_MISSING
    assert err.retryable is False
    assert err.evidence["capability"] == "existing_tabs"
    assert err.evidence["capabilities"]["existing_tabs"] is False


def test_require_capability_unknown_flag_is_validation_error() -> None:
    with pytest.raises(BrowserError) as excinfo:
        require_capability(ManagedBackend(), "not_a_flag")
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR
