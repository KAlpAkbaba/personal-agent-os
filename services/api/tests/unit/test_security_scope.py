"""M8 unit tests: `scope.py`, the single enforcement point.

Acceptance bullet covered here: **out-of-scope target is not silently added**,
plus the fail-safe refusals the constitution's §8 question implies — spoofed
targets, expired/suspended/revoked authorizations, disallowed testing classes
and constraint violations.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.security import scope as scope_module
from app.security.scope import (
    MATCH_CIDR_MEMBERSHIP,
    MATCH_DOMAIN_SUFFIX,
    MATCH_EXACT_ADDRESS,
    REASON_AMBIGUOUS_TARGET,
    REASON_ASSET_REVOKED,
    REASON_ASSET_SUSPENDED,
    REASON_AUTHORIZATION_EXPIRED,
    REASON_DISRUPTION_EXCEEDS_CONSTRAINT,
    REASON_IN_SCOPE,
    REASON_INVALID_TARGET,
    REASON_NO_AUTHORIZED_ASSET,
    REASON_NOT_YET_VALID,
    REASON_TESTING_CLASS_NOT_ALLOWED,
    REASON_TESTING_CLASS_NOT_IMPLEMENTED,
    REASON_UNKNOWN_TESTING_CLASS,
    TARGET_HOSTNAME,
    TARGET_IP,
    TARGET_NETWORK,
    classify_target,
)
from tests.unit.test_security_support import enroll_lab_host, make_stack, utcnow


@pytest.fixture()
def stack():
    return make_stack()


def decide(stack, target, *, testing_class="configuration_audit", disruption="none"):
    with stack.session() as session:
        return stack.guard.evaluate(
            session,
            target=target,
            testing_class=testing_class,
            disruption=disruption,
        )


# ---------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("10.20.30.40", TARGET_IP),
        ("2001:db8::1", TARGET_IP),
        ("10.20.30.0/24", TARGET_NETWORK),
        ("lab-web-01.lab.example.test", TARGET_HOSTNAME),
        # THE spoofing case: an address embedded in a DNS name is a HOSTNAME.
        ("10.20.30.40.evil.com", TARGET_HOSTNAME),
    ],
)
def test_targets_classify_into_exactly_one_kind(raw, kind) -> None:
    parsed = classify_target(raw)
    assert parsed is not None
    assert parsed.kind == kind


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "10.20.30.40 evil.com",  # whitespace
        "10.20.30.40@evil.com",  # authority confusion
        "http://evil.com#10.20.30.40",  # fragment
        "10.20.30.40%eth0",  # zone/percent
        "../../10.20.30.40",  # traversal
        "1.2.3.4.5",  # address-shaped but not an address
        "lab..example.test",  # empty label
        "x" * 600,  # over the length bound
    ],
)
def test_malformed_targets_do_not_classify(raw) -> None:
    assert classify_target(raw) is None


# ------------------------------------------------------- in-scope matching


def test_exact_host_address_is_in_scope(stack) -> None:
    enroll_lab_host(stack)
    decision = decide(stack, "10.20.30.40")
    assert decision.allowed is True
    assert decision.reason == REASON_IN_SCOPE
    assert decision.matched_by == MATCH_EXACT_ADDRESS
    assert decision.asset_ref == "lab-web-01"


def test_cidr_membership_uses_real_ip_arithmetic(stack) -> None:
    """`10.20.3.40` must NOT be in `10.20.30.0/24` — a string-prefix check
    would wrongly say it is."""
    enroll_lab_host(stack, asset_ref="lab-net", kind="network", locator="10.20.30.0/24")

    inside = decide(stack, "10.20.30.40")
    assert inside.allowed is True
    assert inside.matched_by == MATCH_CIDR_MEMBERSHIP

    prefix_lookalike = decide(stack, "10.20.3.40")
    assert prefix_lookalike.allowed is False
    assert prefix_lookalike.reason == REASON_NO_AUTHORIZED_ASSET

    outside = decide(stack, "10.20.31.1")
    assert outside.allowed is False


def test_requested_subnet_must_be_fully_contained(stack) -> None:
    enroll_lab_host(stack, asset_ref="lab-net", kind="network", locator="10.20.30.0/24")
    assert decide(stack, "10.20.30.0/28").allowed is True
    assert decide(stack, "10.20.0.0/16").allowed is False  # wider than authorized


def test_domain_authorises_subdomains_by_label(stack) -> None:
    enroll_lab_host(stack, asset_ref="lab-domain", kind="domain", locator="lab.example.test")
    assert decide(stack, "lab.example.test").allowed is True
    subdomain = decide(stack, "api.lab.example.test")
    assert subdomain.allowed is True
    assert subdomain.matched_by == MATCH_DOMAIN_SUFFIX


# -------------------------------------------------- SPOOFING REFUSALS (§8)


def test_ip_embedded_in_hostname_cannot_enter_an_authorized_cidr(stack) -> None:
    """`10.20.30.40.evil.com` is a hostname, not a member of 10.20.30.0/24."""
    enroll_lab_host(stack, asset_ref="lab-net", kind="network", locator="10.20.30.0/24")
    decision = decide(stack, "10.20.30.40.evil.com")
    assert decision.allowed is False
    assert decision.reason == REASON_NO_AUTHORIZED_ASSET
    assert decision.target_kind == TARGET_HOSTNAME


def test_ip_embedded_in_hostname_cannot_match_an_authorized_host(stack) -> None:
    enroll_lab_host(stack)  # host locator 10.20.30.40
    assert decide(stack, "10.20.30.40.evil.com").allowed is False
    assert decide(stack, "10.20.30.40.evil.com.").allowed is False


@pytest.mark.parametrize(
    "spoof",
    [
        "evil-lab.example.test",  # no label boundary in front
        "lab.example.test.evil.com",  # authorized name at the wrong end
        "notlab.example.test.evil",  # different TLD entirely
        "lab.example.testing",  # longer last label
    ],
)
def test_domain_suffix_spoofs_are_refused(stack, spoof) -> None:
    enroll_lab_host(stack, asset_ref="lab-domain", kind="domain", locator="lab.example.test")
    decision = decide(stack, spoof)
    assert decision.allowed is False
    assert decision.reason == REASON_NO_AUTHORIZED_ASSET


def test_hostname_is_never_resolved_to_reach_an_authorized_address(stack) -> None:
    """No DNS: a hostname target cannot match an IP-locator host asset even if
    it would resolve there, and vice versa."""
    enroll_lab_host(stack)  # locator 10.20.30.40
    assert decide(stack, "lab-web-01.lab.example.test").allowed is False

    stack.registry.revoke("lab-web-01")
    enroll_lab_host(
        stack, asset_ref="lab-by-name", locator="lab-web-01.lab.example.test"
    )
    assert decide(stack, "10.20.30.40").allowed is False


def test_malformed_target_is_refused_as_invalid(stack) -> None:
    enroll_lab_host(stack)
    decision = decide(stack, "10.20.30.40@evil.com")
    assert decision.allowed is False
    assert decision.reason == REASON_INVALID_TARGET


def test_ambiguous_target_is_refused_rather_than_guessed(stack) -> None:
    """Matching two authorizations means the registry is unclear; refuse."""
    enroll_lab_host(stack)  # host 10.20.30.40
    enroll_lab_host(stack, asset_ref="lab-net", kind="network", locator="10.20.30.0/24")
    decision = decide(stack, "10.20.30.40")
    assert decision.allowed is False
    assert decision.reason == REASON_AMBIGUOUS_TARGET
    assert decision.detail["asset_refs"] == ["lab-net", "lab-web-01"]
    # The owner gets what they need to fix the registry.
    assert [m["matched_by"] for m in decision.detail["matches"]] == [
        MATCH_CIDR_MEMBERSHIP,
        MATCH_EXACT_ADDRESS,
    ]
    assert all(m["status"] == "active" for m in decision.detail["matches"])


# ------------------------------------- status / validity window refusals


def test_suspended_asset_is_refused(stack) -> None:
    enroll_lab_host(stack)
    stack.registry.suspend("lab-web-01")
    decision = decide(stack, "10.20.30.40")
    assert decision.allowed is False
    assert decision.reason == REASON_ASSET_SUSPENDED


def test_revoked_asset_is_refused(stack) -> None:
    enroll_lab_host(stack)
    stack.registry.revoke("lab-web-01")
    decision = decide(stack, "10.20.30.40")
    assert decision.allowed is False
    assert decision.reason == REASON_ASSET_REVOKED


def test_expired_asset_is_refused_and_the_row_is_corrected(stack) -> None:
    """The window is re-derived on every decision, so a stale `active` row
    cannot grant access — and it is flipped to `expired` on the spot."""
    enroll_lab_host(
        stack,
        valid_from=utcnow() - timedelta(days=10),
        valid_until=utcnow() - timedelta(seconds=1),
    )
    assert stack.registry.get("lab-web-01")["status"] == "active"  # not swept yet
    decision = decide(stack, "10.20.30.40")
    assert decision.allowed is False
    assert decision.reason == REASON_AUTHORIZATION_EXPIRED
    assert stack.registry.get("lab-web-01")["status"] == "expired"
    assert stack.registry.list_events(action="expired")


def test_not_yet_valid_asset_is_refused(stack) -> None:
    enroll_lab_host(
        stack,
        valid_from=utcnow() + timedelta(days=1),
        valid_until=utcnow() + timedelta(days=2),
    )
    assert decide(stack, "10.20.30.40").reason == REASON_NOT_YET_VALID


# ------------------------------------------ testing class and constraints


def test_testing_class_not_allowed_for_this_asset(stack) -> None:
    enroll_lab_host(stack, allowed_testing={"configuration_audit": False})
    decision = decide(stack, "10.20.30.40")
    assert decision.allowed is False
    assert decision.reason == REASON_TESTING_CLASS_NOT_ALLOWED


def test_unknown_testing_class_is_refused(stack) -> None:
    enroll_lab_host(stack)
    assert decide(stack, "10.20.30.40", testing_class="exploit").reason == (
        REASON_UNKNOWN_TESTING_CLASS
    )


def test_unimplemented_testing_class_is_refused_not_downgraded(stack) -> None:
    enroll_lab_host(stack, allowed_testing={"vulnerability_scan": True})
    decision = decide(stack, "10.20.30.40", testing_class="vulnerability_scan")
    assert decision.allowed is False
    assert decision.reason == REASON_TESTING_CLASS_NOT_IMPLEMENTED


def test_disruption_above_the_stored_constraint_is_refused(stack) -> None:
    enroll_lab_host(stack, max_disruption="low")
    assert decide(stack, "10.20.30.40", disruption="low").allowed is True
    decision = decide(stack, "10.20.30.40", disruption="medium")
    assert decision.allowed is False
    assert decision.reason == REASON_DISRUPTION_EXCEEDS_CONSTRAINT
    assert decision.detail["max_disruption"] == "low"


def test_uninterpretable_constraint_refuses_rather_than_defaults(stack) -> None:
    enroll_lab_host(stack)
    with stack.session() as session:
        from app.security.models import AuthorizedAsset

        asset = session.query(AuthorizedAsset).one()
        asset.constraints_json = {"max_disruption": "whatever"}
        session.commit()
    decision = decide(stack, "10.20.30.40", disruption="none")
    assert decision.allowed is False
    assert decision.detail["uninterpretable"] is True


# --------------------------------------- refusals never widen the registry


def test_refusal_is_audited_and_creates_no_asset(stack) -> None:
    """ACCEPTANCE: an out-of-scope target is not silently added."""
    enroll_lab_host(stack)
    before = stack.registry.list()

    decision = decide(stack, "203.0.113.9")
    assert decision.allowed is False

    assert stack.registry.list() == before  # no new asset, nothing widened
    refusals = stack.registry.list_events(action="assessment_refused")
    assert len(refusals) == 1
    assert refusals[0]["requested_target"] == "203.0.113.9"
    assert refusals[0]["allowed"] is False
    assert refusals[0]["reason"] == REASON_NO_AUTHORIZED_ASSET
    assert refusals[0]["detail"]["enrollment_required"] is True


def test_refusal_against_a_known_asset_is_linked_to_it(stack) -> None:
    """An asset's audit history must show what was ATTEMPTED against it, not
    only what succeeded."""
    enroll_lab_host(stack)
    stack.registry.suspend("lab-web-01")
    decide(stack, "10.20.30.40")

    refusal = stack.registry.list_events(action="assessment_refused")[0]
    assert refusal["asset_ref"] == "lab-web-01"
    assert refusal["asset_id"] is not None
    assert refusal["allowed"] is False
    assert stack.registry.list_events(asset_ref="lab-web-01", allowed=False)


def test_preview_mode_writes_no_audit_row(stack) -> None:
    enroll_lab_host(stack)
    with stack.session() as session:
        stack.guard.evaluate(
            session, target="203.0.113.9", testing_class="configuration_audit", audit=False
        )
    assert stack.registry.list_events(action="assessment_refused") == []


def test_remediation_purpose_uses_the_remediation_event_pair(stack) -> None:
    enroll_lab_host(stack)
    with stack.session() as session:
        stack.guard.evaluate(
            session,
            target="203.0.113.9",
            testing_class="remediation",
            purpose="remediation",
        )
    assert len(stack.registry.list_events(action="remediation_refused")) == 1
    assert stack.registry.list_events(action="assessment_refused") == []


def test_empty_registry_refuses_everything(stack) -> None:
    for target in ("10.20.30.40", "lab.example.test", "10.20.30.0/24", "win-desk-01"):
        assert decide(stack, target).allowed is False


def test_every_refusal_reason_has_a_turkish_message() -> None:
    """tr-TR is first-class: no refusal may surface an untranslated code."""
    reasons = [
        value
        for name, value in vars(scope_module).items()
        if name.startswith("REASON_") and isinstance(value, str)
    ]
    assert reasons
    for reason in reasons:
        assert scope_module._MESSAGES[reason].strip()
