"""M8 unit tests: `RegistryAuthorizationProvider` — closing the M7 gap.

The ADR-0025 security addendum left evolution permission grants deny-by-default
because nothing could VERIFY a caller-asserted `authorized_asset` string. These
tests prove the registry now supplies that verification: a grant is approved
because an owner-authorized row records the permission, and refused otherwise.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.evolution.authorization import AuthorizationProvider, NullAuthorizationProvider
from app.security.provider import RegistryAuthorizationProvider
from tests.unit.test_security_support import enroll_lab_host, make_stack, utcnow

GRANTED = {
    "network_permissions": ["lab.example.test"],
    "filesystem_permissions": ["/srv/lab"],
}


@pytest.fixture()
def provider_stack():
    stack = make_stack()
    return stack, RegistryAuthorizationProvider(stack.session)


def test_provider_satisfies_the_evolution_protocol(provider_stack) -> None:
    _stack, provider = provider_stack
    assert isinstance(provider, AuthorizationProvider)
    assert provider.name == "registry"


# ------------------------------------------------------------- approval


def test_grant_is_approved_for_an_enrolled_asset_with_the_permission(
    provider_stack,
) -> None:
    """THE M7 gap closed: approval rests on a registry row, not an assertion."""
    stack, provider = provider_stack
    enroll_lab_host(stack, allowed_permissions=GRANTED)

    authorization = provider.verify("lab-web-01")
    assert authorization is not None
    assert authorization.asset_ref == "lab-web-01"
    assert authorization.source == "registry"

    approved, unauthorized = authorization.covers(
        {"network_permissions": ["lab.example.test"]}
    )
    assert approved is True
    assert unauthorized == []


def test_subset_rule_holds_for_multiple_classes(provider_stack) -> None:
    stack, provider = provider_stack
    enroll_lab_host(stack, allowed_permissions=GRANTED)
    approved, _ = provider.verify("lab-web-01").covers(
        {"network_permissions": ["lab.example.test"], "filesystem_permissions": ["/srv/lab"]}
    )
    assert approved is True


# -------------------------------------------------------------- refusal


def test_grant_is_refused_for_a_permission_the_owner_never_recorded(
    provider_stack,
) -> None:
    stack, provider = provider_stack
    enroll_lab_host(stack, allowed_permissions=GRANTED)

    approved, unauthorized = provider.verify("lab-web-01").covers(
        {"device_permissions": ["camera"]}
    )
    assert approved is False
    assert unauthorized == ["device_permissions:camera"]


def test_recording_a_specific_host_does_not_authorise_a_wildcard(
    provider_stack,
) -> None:
    stack, provider = provider_stack
    enroll_lab_host(stack, allowed_permissions=GRANTED)
    approved, unauthorized = provider.verify("lab-web-01").covers(
        {"network_permissions": ["*"]}
    )
    assert approved is False
    assert unauthorized == ["network_permissions:*"]


def test_asset_without_any_recorded_permissions_approves_nothing(
    provider_stack,
) -> None:
    stack, provider = provider_stack
    enroll_lab_host(stack)  # allowed_permissions defaults to {}
    authorization = provider.verify("lab-web-01")
    assert authorization is not None
    assert authorization.allowed == {}
    approved, unauthorized = authorization.covers({"network_permissions": ["anything"]})
    assert approved is False
    assert unauthorized == ["network_permissions:anything"]


@pytest.mark.parametrize("asset_ref", [None, "", "never-enrolled", "Bad Ref!", "x" * 200])
def test_unknown_or_malformed_asset_refs_are_unverifiable(
    provider_stack, asset_ref
) -> None:
    stack, provider = provider_stack
    enroll_lab_host(stack, allowed_permissions=GRANTED)
    assert provider.verify(asset_ref) is None


def test_suspended_asset_verifies_to_nothing(provider_stack) -> None:
    stack, provider = provider_stack
    enroll_lab_host(stack, allowed_permissions=GRANTED)
    stack.registry.suspend("lab-web-01")
    assert provider.verify("lab-web-01") is None


def test_revoked_asset_verifies_to_nothing(provider_stack) -> None:
    stack, provider = provider_stack
    enroll_lab_host(stack, allowed_permissions=GRANTED)
    stack.registry.revoke("lab-web-01")
    assert provider.verify("lab-web-01") is None


def test_asset_outside_its_validity_window_verifies_to_nothing(
    provider_stack,
) -> None:
    stack, provider = provider_stack
    enroll_lab_host(
        stack,
        allowed_permissions=GRANTED,
        valid_from=utcnow() - timedelta(days=5),
        valid_until=utcnow() - timedelta(seconds=1),
    )
    assert provider.verify("lab-web-01") is None


def test_asset_not_yet_valid_verifies_to_nothing(provider_stack) -> None:
    stack, provider = provider_stack
    enroll_lab_host(
        stack,
        allowed_permissions=GRANTED,
        valid_from=utcnow() + timedelta(days=1),
        valid_until=utcnow() + timedelta(days=2),
    )
    assert provider.verify("lab-web-01") is None


# ------------------------------------------------------------ boundaries


def test_security_testing_scope_does_not_bootstrap_evolution_permissions(
    provider_stack,
) -> None:
    """`allowed_testing` and `allowed_permissions` are separate columns; one
    must never imply the other."""
    stack, provider = provider_stack
    enroll_lab_host(
        stack,
        allowed_testing={"configuration_audit": True, "remediation": True},
        allowed_permissions={},
    )
    assert provider.verify("lab-web-01").allowed == {}


def test_provider_is_a_strict_upgrade_over_the_null_default(provider_stack) -> None:
    stack, provider = provider_stack
    enroll_lab_host(stack, allowed_permissions=GRANTED)
    assert NullAuthorizationProvider().verify("lab-web-01") is None
    assert provider.verify("lab-web-01") is not None


def test_provider_never_creates_or_widens_a_registry_row(provider_stack) -> None:
    stack, provider = provider_stack
    enroll_lab_host(stack, allowed_permissions=GRANTED)
    before_assets = stack.registry.list()
    before_events = stack.registry.list_events(limit=500)

    provider.verify("lab-web-01")
    provider.verify("not-enrolled")

    assert stack.registry.list() == before_assets
    assert stack.registry.list_events(limit=500) == before_events


# ---------------------------------- M8 review: the wiring must actually exist


def test_evolution_runtime_uses_the_registry_provider_by_default(monkeypatch) -> None:
    """The M7 High finding is only closed if the running system WIRES this
    provider. Building it and leaving EvolutionRuntime on the deny-everything
    default would look fixed while changing nothing (M8 security review #1)."""
    from app.config import Settings
    from app.evolution.runtime import EvolutionRuntime
    from app.security.provider import RegistryAuthorizationProvider

    monkeypatch.delenv("PAGENTOS_EVOLUTION_AUTHORIZATIONS", raising=False)
    runtime = EvolutionRuntime(Settings(_env_file=None))
    provider = runtime.authorization
    assert isinstance(provider, RegistryAuthorizationProvider)
    assert provider.name == "registry"
    # ...and the reviewer the pipeline uses carries it.
    assert runtime.reviewer.authorization is not None
    assert runtime.reviewer.authorization.name == "registry"


def test_explicit_static_override_still_wins_for_local_dev(monkeypatch) -> None:
    from app.config import Settings
    from app.evolution.runtime import EvolutionRuntime

    monkeypatch.setenv(
        "PAGENTOS_EVOLUTION_AUTHORIZATIONS",
        '{"asset": {"device_permissions": ["serial_port"]}}',
    )
    runtime = EvolutionRuntime(Settings(_env_file=None))
    assert runtime.authorization.name == "static"


def test_every_runtime_path_that_reviews_carries_the_registry_provider(monkeypatch) -> None:
    """Same lesson as the M8 High finding: a reviewer built without an
    authorization source fails safe but silently. Every runtime path that
    constructs a reviewer must carry the registry-backed one."""
    from app.config import Settings
    from app.evolution.runtime import EvolutionRuntime

    monkeypatch.delenv("PAGENTOS_EVOLUTION_AUTHORIZATIONS", raising=False)
    runtime = EvolutionRuntime(Settings(_env_file=None))
    assert runtime.reviewer.authorization.name == "registry"
    assert runtime.improver.reviewer.authorization.name == "registry"
