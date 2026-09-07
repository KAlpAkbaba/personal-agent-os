"""The deployment backend seam (mirrors app.selfhealing.pipeline.StagingDeployer).

Every effectful step of a release — deploy, verify, rollback — goes through
:class:`DeploymentBackend`. This is what lets the whole M18 release path be
exercised in tests without ever touching the real Hetzner Cloud Core: a test
wires :class:`FakeDeploymentBackend`, production wiring would eventually wire
a real one, and the orchestrator in ``execution.py`` cannot tell the
difference.

No real backend ships in this change (task constraint: do not deploy
anything, no ssh/scp against production). A concrete Hetzner backend would
shell out to something like ``scripts/cloud/release-cloud-core.ps1`` — note
for whoever wires that up: that script's own ``ssh``/``scp`` calls carry no
``-o ConnectTimeout=`` (or PowerShell job/`Start-Process -Wait` timeout), so a
network stall on the tailnet hangs the release with no bound. A real backend
built on it should add one before it is trusted to run unattended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class DeployOutcome:
    succeeded: bool
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"succeeded": self.succeeded, "detail": dict(self.detail)}


@dataclass(slots=True)
class VerifyOutcome:
    """Post-deployment verification (M18 spec §5): health, version, provenance.

    ``installed_digest`` is the digest of the release that was ACTUALLY shipped
    (the recorded ``releases`` row this deployment created) — never the source
    tree's digest. ``runtime_reported_digest`` is what the running service says
    about itself. Comparing runtime-to-installed (not runtime-to-source-tree)
    is the exact distinction ADR-0053's deployment-provenance lesson names
    (``app/experience/compiler.py``'s ``PATTERN_DEPLOYMENT_PROVENANCE``): a
    clean, committed source tree proves nothing about what process is actually
    running.
    """

    healthy: bool
    running_version: str | None = None
    runtime_module_path: str | None = None
    runtime_reported_digest: str | None = None
    installed_digest: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def provenance_ok(self) -> bool:
        return (
            self.runtime_reported_digest is not None
            and self.installed_digest is not None
            and self.runtime_reported_digest == self.installed_digest
        )

    @property
    def passed(self) -> bool:
        return self.healthy and self.provenance_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "running_version": self.running_version,
            "runtime_module_path": self.runtime_module_path,
            "runtime_reported_digest": self.runtime_reported_digest,
            "installed_digest": self.installed_digest,
            "provenance_ok": self.provenance_ok,
            "passed": self.passed,
            "detail": dict(self.detail),
        }


@dataclass(slots=True)
class RollbackOutcome:
    succeeded: bool
    restored_version: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "succeeded": self.succeeded,
            "restored_version": self.restored_version,
            "detail": dict(self.detail),
        }


@runtime_checkable
class DeploymentBackend(Protocol):
    """Provider seam: deploy -> verify -> (rollback on failure).

    ``candidate``/``target_version`` are plain identifying strings rather than
    ``app.release.preflight.ReleaseCandidate`` so a backend implementation
    never needs to import the orchestration layer above it.
    """

    name: str

    def deploy(self, *, component: str, version: str, candidate_ref: str) -> DeployOutcome: ...

    def verify(self, *, component: str, version: str, installed_digest: str) -> VerifyOutcome: ...

    def rollback(self, *, component: str, target_version: str) -> RollbackOutcome: ...


class FakeDeploymentBackend:
    """A scriptable backend for tests. No I/O, no subprocess, no network.

    Each stage is configured independently so a test can exercise exactly one
    failure mode at a time: a deploy that never lands, a deploy that lands but
    fails health, a deploy that lands healthy but reports the WRONG digest
    (the provenance check this backend exists to prove), or full success.
    """

    name = "fake"

    def __init__(
        self,
        *,
        deploy_succeeds: bool = True,
        verify_healthy: bool = True,
        verify_digest_matches: bool = True,
        runtime_module_path: str = "app.main:app",
        rollback_succeeds: bool = True,
    ) -> None:
        self.deploy_succeeds = deploy_succeeds
        self.verify_healthy = verify_healthy
        self.verify_digest_matches = verify_digest_matches
        self.runtime_module_path = runtime_module_path
        self.rollback_succeeds = rollback_succeeds
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def deploy(self, *, component: str, version: str, candidate_ref: str) -> DeployOutcome:
        self.calls.append(
            ("deploy", {"component": component, "version": version, "candidate_ref": candidate_ref})
        )
        if not self.deploy_succeeds:
            return DeployOutcome(
                succeeded=False, detail={"reason": "fake_deploy_configured_to_fail"}
            )
        return DeployOutcome(succeeded=True, detail={"component": component, "version": version})

    def verify(self, *, component: str, version: str, installed_digest: str) -> VerifyOutcome:
        self.calls.append(
            (
                "verify",
                {"component": component, "version": version, "installed_digest": installed_digest},
            )
        )
        reported_digest = installed_digest if self.verify_digest_matches else "sha256:" + "0" * 64
        return VerifyOutcome(
            healthy=self.verify_healthy,
            running_version=version if self.verify_healthy else None,
            runtime_module_path=self.runtime_module_path,
            runtime_reported_digest=reported_digest,
            installed_digest=installed_digest,
            detail={"component": component},
        )

    def rollback(self, *, component: str, target_version: str) -> RollbackOutcome:
        self.calls.append(("rollback", {"component": component, "target_version": target_version}))
        if not self.rollback_succeeds:
            return RollbackOutcome(
                succeeded=False, detail={"reason": "fake_rollback_configured_to_fail"}
            )
        return RollbackOutcome(succeeded=True, restored_version=target_version)


__all__ = [
    "DeployOutcome",
    "DeploymentBackend",
    "FakeDeploymentBackend",
    "RollbackOutcome",
    "VerifyOutcome",
]
