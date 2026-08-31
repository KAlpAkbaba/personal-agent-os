"""M8 unit tests: the Authorized Asset Registry.

Acceptance bullet covered here: **authorized test asset enrolls** — and the
registry rules that keep enrollment meaningful (evidence required, locator
immutable, every mutation audited).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.security.errors import SecurityError, SecurityErrorClass
from app.security.models import (
    ASSET_STATUS_ACTIVE,
    ASSET_STATUS_EXPIRED,
    ASSET_STATUS_REVOKED,
    ASSET_STATUS_SUSPENDED,
)
from app.security.registry import normalize_locator
from tests.unit.test_security_support import enroll_lab_host, make_stack, utcnow


@pytest.fixture()
def stack():
    return make_stack()


# ------------------------------------------- acceptance: an asset enrolls


def test_authorized_test_asset_enrolls(stack) -> None:
    """ACCEPTANCE: authorized test asset enrolls (SECURITY_MODEL §7 example)."""
    asset = enroll_lab_host(stack)

    assert asset["asset_ref"] == "lab-web-01"
    assert asset["kind"] == "host"
    assert asset["locator"] == "10.20.30.40"
    assert asset["environment"] == "lab"
    assert asset["status"] == ASSET_STATUS_ACTIVE
    assert asset["allowed_testing"]["configuration_audit"] is True
    assert asset["constraints"]["max_disruption"] == "low"
    assert asset["evidence"]["authorization"] == "owner_or_company_authorized"
    assert asset["valid_from"] and asset["valid_until"]

    assert stack.registry.get("lab-web-01")["id"] == asset["id"]
    assert [a["asset_ref"] for a in stack.registry.list()] == ["lab-web-01"]


def test_enrollment_writes_an_authorization_event(stack) -> None:
    enroll_lab_host(stack)
    events = stack.registry.list_events()
    assert [e["action"] for e in events] == ["enrolled"]
    assert events[0]["allowed"] is True
    assert events[0]["asset_ref"] == "lab-web-01"
    assert events[0]["detail"]["locator"] == "10.20.30.40"


def test_enrollment_requires_authorization_evidence(stack) -> None:
    """An asset the owner never actually authorized is not enrollable."""
    with pytest.raises(SecurityError) as excinfo:
        stack.registry.enroll(
            asset_ref="no-evidence",
            name="Undocumented host",
            kind="host",
            locator="10.20.30.41",
            evidence={},
        )
    assert excinfo.value.error_class is SecurityErrorClass.VALIDATION_ERROR
    assert stack.registry.list() == []


def test_enrollment_requires_both_evidence_keys(stack) -> None:
    with pytest.raises(SecurityError) as excinfo:
        stack.registry.enroll(
            asset_ref="half-evidence",
            name="Half documented",
            kind="host",
            locator="10.20.30.42",
            evidence={"authorization": "owner_or_company_authorized"},
        )
    assert excinfo.value.details["missing"] == ["recorded_by"]


def test_duplicate_asset_ref_is_refused(stack) -> None:
    enroll_lab_host(stack)
    with pytest.raises(SecurityError) as excinfo:
        enroll_lab_host(stack)
    assert excinfo.value.error_class is SecurityErrorClass.ALREADY_ENROLLED


@pytest.mark.parametrize(
    "asset_ref",
    ["", "A", "-bad", "has space", "x" * 80, "unicode-ıı", "semi;colon"],
)
def test_asset_ref_token_shape_is_enforced(stack, asset_ref) -> None:
    with pytest.raises(SecurityError):
        enroll_lab_host(stack, asset_ref=asset_ref)


# ------------------------------------------------------------- locators


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        ("host", "10.20.30.40", "10.20.30.40"),
        ("host", "LAB-Web-01.Lab.Example.Test.", "lab-web-01.lab.example.test"),
        ("network", "10.20.30.7/24", "10.20.30.0/24"),
        ("domain", "Lab.Example.Test", "lab.example.test"),
        ("device", "WIN-DESK-01", "win-desk-01"),
        ("service", "https://lab.example.test:8443/api/", "https://lab.example.test:8443/api"),
    ],
)
def test_locator_normalisation(kind, raw, expected) -> None:
    assert normalize_locator(kind, raw) == expected


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        ("network", "10.20.30.40"),  # not a CIDR
        ("host", "10.20.30.40 evil.com"),  # whitespace
        ("host", "not a hostname!"),
        ("domain", "10.20.30.40"),  # an address is not a domain
        ("service", "https://user:pw@host/api"),  # credentials in locator
        ("device", "../../etc"),
    ],
)
def test_invalid_locators_are_refused(kind, raw) -> None:
    with pytest.raises(SecurityError):
        normalize_locator(kind, raw)


# --------------------------------------------------------- scope changes


def test_scope_change_records_a_before_after_event(stack) -> None:
    enroll_lab_host(stack)
    updated = stack.registry.change_scope(
        "lab-web-01",
        allowed_testing={"configuration_audit": True},
        reason="owner narrowed the scope",
    )
    assert updated["allowed_testing"] == {"configuration_audit": True}

    event = stack.registry.list_events(action="scope_changed")[0]
    assert event["reason"] == "owner narrowed the scope"
    assert event["detail"]["before"]["allowed_testing"]["remediation"] is True
    assert "remediation" not in event["detail"]["after"]["allowed_testing"]


def test_scope_change_cannot_repoint_the_locator(stack) -> None:
    """Registry rule 2: re-pointing an authorization needs revoke + re-enroll."""
    enroll_lab_host(stack)
    with pytest.raises(TypeError):
        stack.registry.change_scope("lab-web-01", locator="8.8.8.8")
    assert stack.registry.get("lab-web-01")["locator"] == "10.20.30.40"


def test_revoked_asset_cannot_be_rescoped(stack) -> None:
    enroll_lab_host(stack)
    stack.registry.revoke("lab-web-01")
    with pytest.raises(SecurityError) as excinfo:
        stack.registry.change_scope("lab-web-01", allowed_testing={})
    assert excinfo.value.error_class is SecurityErrorClass.VALIDATION_ERROR


# ------------------------------------------------- suspend / revoke / expiry


def test_suspend_and_reinstate(stack) -> None:
    enroll_lab_host(stack)
    assert stack.registry.suspend("lab-web-01")["status"] == ASSET_STATUS_SUSPENDED
    assert [e["action"] for e in stack.registry.list_events()][0] == "suspended"
    assert stack.registry.reinstate("lab-web-01")["status"] == ASSET_STATUS_ACTIVE


def test_revocation_is_terminal(stack) -> None:
    enroll_lab_host(stack)
    revoked = stack.registry.revoke("lab-web-01", reason="lab decommissioned")
    assert revoked["status"] == ASSET_STATUS_REVOKED
    assert revoked["revoked_at"] is not None
    assert stack.registry.list_events(action="revoked")[0]["reason"] == "lab decommissioned"
    with pytest.raises(SecurityError):
        stack.registry.reinstate("lab-web-01")
    with pytest.raises(SecurityError):
        stack.registry.revoke("lab-web-01")


def test_sweep_expired_flips_due_assets_and_audits_them(stack) -> None:
    enroll_lab_host(
        stack,
        valid_from=utcnow() - timedelta(days=10),
        valid_until=utcnow() - timedelta(days=1),
    )
    assert stack.registry.sweep_expired() == ["lab-web-01"]
    assert stack.registry.get("lab-web-01")["status"] == ASSET_STATUS_EXPIRED
    assert stack.registry.list_events(action="expired")[0]["asset_ref"] == "lab-web-01"
    # Idempotent: a second sweep finds nothing left to flip.
    assert stack.registry.sweep_expired() == []


def test_expired_asset_is_reinstated_by_extending_the_window(stack) -> None:
    enroll_lab_host(
        stack,
        valid_from=utcnow() - timedelta(days=10),
        valid_until=utcnow() - timedelta(days=1),
    )
    stack.registry.sweep_expired()
    updated = stack.registry.change_scope(
        "lab-web-01", valid_until=utcnow() + timedelta(days=30)
    )
    assert updated["status"] == ASSET_STATUS_ACTIVE


def test_valid_until_before_valid_from_is_refused(stack) -> None:
    with pytest.raises(SecurityError):
        enroll_lab_host(
            stack,
            valid_from=utcnow(),
            valid_until=utcnow() - timedelta(days=1),
        )


# --------------------------------------------------------------- hygiene


def test_evidence_is_redacted_before_storage(stack) -> None:
    """The registry must not become a credential store (rule 4)."""
    asset = stack.registry.enroll(
        asset_ref="lab-web-02",
        name="Lab web host 02",
        kind="host",
        locator="10.20.30.41",
        evidence={
            "authorization": "owner_or_company_authorized",
            "recorded_by": "owner",
            "note": "ticket says api_key=abcdef0123456789 was rotated",
        },
    )
    note = asset["evidence"]["note"]
    assert "abcdef0123456789" not in note
    assert "REDACTED" in note


def test_unknown_permission_class_is_refused(stack) -> None:
    with pytest.raises(SecurityError):
        enroll_lab_host(stack, allowed_permissions={"root_permissions": ["*"]})


def test_unknown_testing_class_is_refused(stack) -> None:
    with pytest.raises(SecurityError):
        enroll_lab_host(stack, allowed_testing={"exploitation": True})


def test_unknown_max_disruption_is_refused(stack) -> None:
    with pytest.raises(SecurityError):
        enroll_lab_host(stack, max_disruption="catastrophic")


def test_get_unknown_asset_is_typed_not_found(stack) -> None:
    with pytest.raises(SecurityError) as excinfo:
        stack.registry.get("never-enrolled")
    assert excinfo.value.error_class is SecurityErrorClass.NOT_FOUND
