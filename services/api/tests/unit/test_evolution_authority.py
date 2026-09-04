"""Security tests for the Evolution Engine's production-authority boundary.

These are the tests that have to be right. Everything else in the phase is
features; this file is the claim that the Evolution Engine cannot deploy.

Five independent properties are asserted:

1. a lab authority can never come to hold a production grant, by any route
   exposed from ``app.evolution.authority``;
2. every enumerated production action refuses a lab caller;
3. production authority requires an owner-session capability that lab code
   cannot construct;
4. the root policies cannot be mutated, and their digest is pinned;
5. no module under ``app/evolution`` imports a deployment, release-mutation or
   secret-root module — and the scanner that checks this is proven non-vacuous
   by feeding it a file that does.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.evolution.authority import (
    ALLOWED_IDENTITY_MODULES,
    EVOLUTION_PACKAGE_DIR,
    LAB_GRANTS,
    PRODUCTION_ACTIONS,
    PRODUCTION_GRANTS,
    ROOT_POLICIES,
    ROOT_POLICY_DIGEST,
    ROOT_POLICY_IDS,
    Authority,
    AuthorityError,
    Grant,
    LabAuthority,
    OwnerCapability,
    ProductionAuthority,
    Scope,
    apply_policy_change,
    authority_scope,
    guard_production_action,
    mint_owner_capability,
    refuse_root_policy_mutation,
    requires,
    scan_evolution_package,
    scan_module_for_forbidden_imports,
    verify_root_policies,
)
from app.evolution.errors import EvolutionErrorClass


class FakeSession:
    """The shape ``require_owner_session`` returns after a real verification."""

    def __init__(self, scopes: tuple[str, ...] = ()) -> None:
        self.session_id = "11111111-1111-1111-1111-111111111111"
        self.client_kind = "desktop"
        self.scopes = scopes


def owner_capability() -> OwnerCapability:
    return mint_owner_capability(FakeSession())


# ------------------------------------------- 1. grant algebra is watertight


def test_the_two_grant_sets_are_disjoint_and_exhaustive() -> None:
    assert not (LAB_GRANTS & PRODUCTION_GRANTS)
    assert LAB_GRANTS | PRODUCTION_GRANTS == set(Grant)
    # The five the phase names explicitly.
    assert PRODUCTION_GRANTS == {
        Grant.DEPLOY,
        Grant.SIGN_RELEASE,
        Grant.WRITE_PRODUCTION_DB,
        Grant.READ_PRODUCTION_SECRETS,
        Grant.MODIFY_POLICY_KERNEL,
    }


@pytest.mark.parametrize("grant", sorted(PRODUCTION_GRANTS))
def test_lab_authority_refuses_every_production_grant(grant: Grant) -> None:
    with pytest.raises(AuthorityError) as excinfo:
        LabAuthority.issue("evolution.engine", {grant})
    assert excinfo.value.error_class == EvolutionErrorClass.PERMISSION_DENIED
    assert str(grant) in excinfo.value.details["refused_grants"]


@pytest.mark.parametrize("grant", sorted(PRODUCTION_GRANTS))
def test_a_lab_scoped_authority_object_cannot_carry_a_production_grant(
    grant: Grant,
) -> None:
    """Even bypassing the factory: the value object itself refuses."""
    with pytest.raises(AuthorityError):
        Authority(subject="sneaky", scope=Scope.LAB, grants=frozenset({grant}))


def test_the_default_lab_authority_holds_exactly_the_lab_grants() -> None:
    authority = LabAuthority.issue("evolution.engine")
    assert authority.grants == LAB_GRANTS
    assert authority.is_lab and not authority.is_production
    assert not (authority.grants & PRODUCTION_GRANTS)
    # ...and it really can do the lab work the phase is about.
    for grant in (
        Grant.READ_SOURCE,
        Grant.WRITE_LAB_WORKSPACE,
        Grant.RUN_LAB_TESTS,
        Grant.READ_EVIDENCE,
        Grant.BENCHMARK_CANDIDATE,
        Grant.SECURITY_REVIEW_CANDIDATE,
        Grant.EXPLAIN_CANDIDATE,
        Grant.PROPOSE_CANDIDATE,
        Grant.MARK_SHADOW_READY,
    ):
        assert authority.can(grant)


def test_knowledge_is_not_authority() -> None:
    """A read-only lab authority may inspect and explain, and nothing else."""
    reader = LabAuthority.read_only("evolution.explainer")
    assert reader.can(Grant.READ_SOURCE)
    assert reader.can(Grant.EXPLAIN_CANDIDATE)
    assert not reader.can(Grant.WRITE_LAB_WORKSPACE)
    assert not reader.can(Grant.DEPLOY)


def test_an_unknown_grant_is_refused() -> None:
    with pytest.raises(AuthorityError):
        Authority(subject="x", scope=Scope.LAB, grants=frozenset({"not_a_grant"}))  # type: ignore[arg-type]


# ------------------------------------ 2. every production action refuses lab


def test_every_production_grant_has_at_least_one_named_action() -> None:
    assert set(PRODUCTION_ACTIONS.values()) == PRODUCTION_GRANTS


@pytest.mark.parametrize("action", sorted(PRODUCTION_ACTIONS))
def test_a_lab_caller_is_refused_every_production_action(action: str) -> None:
    lab = LabAuthority.issue("evolution.engine")
    with pytest.raises(AuthorityError) as excinfo:
        guard_production_action(action, lab)
    details = excinfo.value.details
    assert details["scope"] == str(Scope.LAB)
    assert details["action"] == action
    assert excinfo.value.error_class == EvolutionErrorClass.PERMISSION_DENIED


@pytest.mark.parametrize("action", sorted(PRODUCTION_ACTIONS))
def test_no_authority_is_never_treated_as_permission(action: str) -> None:
    with pytest.raises(AuthorityError):
        guard_production_action(action, None)


def test_an_unlisted_production_action_fails_closed() -> None:
    """A typo must not become an unguarded action."""
    with pytest.raises(AuthorityError):
        guard_production_action("deploy_relase", ProductionAuthority.for_owner(owner_capability()))


@pytest.mark.parametrize("action", sorted(PRODUCTION_ACTIONS))
def test_an_owner_derived_production_authority_passes(action: str) -> None:
    """The boundary is not "nobody can deploy" — it is "the engine cannot"."""
    production = ProductionAuthority.for_owner(owner_capability())
    guard_production_action(action, production)


def test_a_narrow_production_authority_still_refuses_other_actions() -> None:
    production = ProductionAuthority.for_owner(owner_capability(), {Grant.DEPLOY})
    guard_production_action("deploy_release", production)
    with pytest.raises(AuthorityError):
        guard_production_action("read_production_secret", production)


# ----------------------------- 3. production authority needs an owner session


def test_production_authority_requires_an_owner_capability() -> None:
    for bogus in (None, object(), "owner", {"session_id": "x", "scopes": ()}):
        with pytest.raises(AuthorityError):
            ProductionAuthority.for_owner(bogus)  # type: ignore[arg-type]


def test_owner_capability_cannot_be_constructed_directly() -> None:
    with pytest.raises(AuthorityError):
        OwnerCapability(mint=object(), session_id="x", client_kind="y")


def test_a_capability_needs_a_real_session_context() -> None:
    with pytest.raises(AuthorityError):
        mint_owner_capability({"session_id": "x", "scopes": ()})
    with pytest.raises(AuthorityError):
        mint_owner_capability(object())


def test_a_scoped_session_never_yields_production_authority() -> None:
    """Scopes narrow a client; they never elevate one (identity contract)."""
    with pytest.raises(AuthorityError) as excinfo:
        mint_owner_capability(FakeSession(scopes=("narration",)))
    assert excinfo.value.details["reason"] == "scoped_session"


def test_a_production_scoped_authority_cannot_be_built_without_the_mint() -> None:
    with pytest.raises(AuthorityError):
        Authority(
            subject="pretend-owner",
            scope=Scope.PRODUCTION,
            grants=frozenset({Grant.DEPLOY}),
        )


def test_the_capability_does_not_leak_the_session_id_in_its_repr() -> None:
    capability = owner_capability()
    assert capability.session_id not in repr(capability)


# ------------------------------------------------------ the requires() guard


class LabWorker:
    """A stand-in for engine code: it holds an authority and is guarded by it."""

    def __init__(self, authority: Authority) -> None:
        self.authority = authority

    @requires(Grant.RUN_LAB_TESTS)
    def run_tests(self) -> str:
        return "ran"

    @requires(Grant.DEPLOY)
    def deploy(self) -> str:  # pragma: no cover - must never execute for lab
        return "deployed"


def test_requires_allows_a_granted_call_and_refuses_an_ungranted_one() -> None:
    worker = LabWorker(LabAuthority.issue("evolution.engine"))
    assert worker.run_tests() == "ran"
    with pytest.raises(AuthorityError) as excinfo:
        worker.deploy()
    assert excinfo.value.details["required_grant"] == str(Grant.DEPLOY)
    assert excinfo.value.details["production_grant"] is True


def test_requires_refuses_when_no_authority_is_in_scope() -> None:
    @requires(Grant.READ_SOURCE)
    def read() -> str:  # pragma: no cover - must never execute
        return "read"

    with pytest.raises(AuthorityError) as excinfo:
        read()
    assert excinfo.value.details["reason"] == "no_authority"


def test_requires_reads_the_ambient_authority_scope() -> None:
    @requires(Grant.READ_SOURCE)
    def read() -> str:
        return "read"

    with authority_scope(LabAuthority.issue("evolution.engine")):
        assert read() == "read"
    with pytest.raises(AuthorityError):
        read()


# ------------------------------------------- 4. root policies are immutable


def test_the_eight_root_policies_are_present() -> None:
    assert ROOT_POLICY_IDS == {
        "owner_identity",
        "approval_boundary",
        "deployment_authority",
        "audit_guarantees",
        "secret_boundaries",
        "sandbox_boundary",
        "rollback_guarantees",
        "security_policy_kernel",
    }
    for policy in ROOT_POLICIES:
        assert policy.statement and policy.source


def test_a_root_policy_object_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        ROOT_POLICIES[0].statement = "anything goes"  # type: ignore[misc]


@pytest.mark.parametrize("policy_id", sorted(ROOT_POLICY_IDS))
def test_no_root_policy_can_be_changed(policy_id: str) -> None:
    with pytest.raises(AuthorityError):
        apply_policy_change({policy_id: {"statement": "weakened"}})
    with pytest.raises(AuthorityError):
        refuse_root_policy_mutation(policy_id)


@pytest.mark.parametrize("policy_id", sorted(ROOT_POLICY_IDS))
def test_not_even_a_production_authority_can_weaken_a_root_policy(
    policy_id: str,
) -> None:
    """The owner may deploy; nothing may quietly rewrite the invariants."""
    production = ProductionAuthority.for_owner(owner_capability())
    with pytest.raises(AuthorityError):
        apply_policy_change({policy_id: {"statement": "weakened"}}, production)


def test_a_non_root_policy_change_is_allowed_through() -> None:
    accepted = apply_policy_change({"backlog_page_size": 25})
    assert accepted == {"backlog_page_size": 25}


def test_a_mixed_change_is_refused_entirely() -> None:
    with pytest.raises(AuthorityError):
        apply_policy_change({"backlog_page_size": 25, "deployment_authority": "engine"})


def test_the_root_policy_digest_is_pinned_and_verified() -> None:
    assert len(ROOT_POLICY_DIGEST) == 64
    verify_root_policies()


# --------------------------------------------------- 5. the import guard


def test_no_evolution_module_imports_a_deployment_release_or_secret_module() -> None:
    """The guard test. If this fails, the boundary has a hole in it.

    An authority check is worthless if the engine can import the deployer and
    call it directly, so the import graph is part of the boundary, not a
    style rule.
    """
    violations = scan_evolution_package()
    assert violations == [], (
        "app/evolution must not reach deployment, release-mutation or "
        f"secret-root modules; found: {violations}"
    )


def test_the_scanner_is_not_vacuous(tmp_path: Path) -> None:
    """Prove the guard detects what it claims to, so a green run means something."""
    cases = {
        "deployer.py": "from app.selfhealing.pipeline import SupervisorDeployer\n",
        "deployer_pkg.py": "from app.selfhealing import pipeline\n",
        "deployer_plain.py": "import app.selfhealing.routes\n",
        "secret_root.py": "from app.identity.root import FileCredentialRoot\n",
        "secret_pkg.py": "from app.identity import root\n",
        "minter.py": "from app.identity.service import IdentityService\n",
        "release_mutation.py": "from app.selfhealing.service import SelfHealingService\n",
        "whole_module.py": "import app.selfhealing.service\n",
        "lazy.py": "def f():\n    from app.selfhealing.pipeline import DeployResult\n",
        "remediation.py": "from app.security.remediation import apply_fix\n",
    }
    for name, source in cases.items():
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        assert scan_module_for_forbidden_imports(path), name

    clean = tmp_path / "clean.py"
    clean.write_text(
        "from app.selfhealing.service import compute_manifest_digest\n"
        "from app.selfhealing.models import Incident\n"
        "from app.identity.dependencies import require_owner_session\n"
        "from app.ledger.service import ActivityEvent\n",
        encoding="utf-8",
    )
    assert scan_module_for_forbidden_imports(clean) == []


def test_only_the_owner_session_dependency_is_reachable_from_identity() -> None:
    assert ALLOWED_IDENTITY_MODULES == {"app.identity.dependencies"}
    assert EVOLUTION_PACKAGE_DIR.name == "evolution"
    assert (EVOLUTION_PACKAGE_DIR / "authority.py").is_file()
