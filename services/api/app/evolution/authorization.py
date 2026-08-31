"""Authorization seam for generated-skill permission grants.

Deny-by-default means a grant is approved only against a **verified** owner
authorization — never against a caller-asserted string. Until the Authorized
Asset Registry exists (M8, SECURITY_MODEL §7), no production source of truth
can verify an asset reference, so the default provider verifies nothing and
every grant request is refused.

That refusal is the correct fail-safe: a generated skill still reaches
production with EMPTY grants (deny-by-default), and an unverifiable grant can
never be recorded as "owner-authorized, independently reviewed" in the audit
trail. The owner-policy path the constitution requires — handing a powerful
tool to an explicitly authorized device/asset without Evolution touching the
security root — is preserved as this interface: M8's registry implements
`AuthorizationProvider` and grants become approvable again, verified against
the asset's recorded `allowed_*` scope rather than asserted by the requester.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

PERMISSION_CLASSES = (
    "network_permissions",
    "filesystem_permissions",
    "device_permissions",
    "secret_requirements",
)


@dataclass(frozen=True, slots=True)
class AssetAuthorization:
    """What an owner-authorized asset is actually allowed to grant.

    `allowed` maps a permission class to the grants recorded for that asset by
    the owner (M8's registry). A request is approvable only when every grant is
    a subset of the corresponding allowed set.
    """

    asset_ref: str
    source: str
    allowed: dict[str, frozenset[str]] = field(default_factory=dict)

    def covers(self, grants: dict[str, list[str]]) -> tuple[bool, list[str]]:
        """(approved, unauthorized_grants)."""
        unauthorized: list[str] = []
        for klass in PERMISSION_CLASSES:
            requested = set(grants.get(klass) or ())
            permitted = self.allowed.get(klass, frozenset())
            for item in sorted(requested - permitted):
                unauthorized.append(f"{klass}:{item}")
        return (not unauthorized), unauthorized


@runtime_checkable
class AuthorizationProvider(Protocol):
    """Resolves an asset reference to what the owner authorized it to do."""

    name: str

    def verify(self, asset_ref: str | None) -> AssetAuthorization | None:
        """Return the authorization, or None when it cannot be verified."""
        ...


class NullAuthorizationProvider:
    """Default: no verifiable source of owner authorization exists yet.

    Every asset reference is unverifiable, so every permission grant is
    refused. Generated skills still reach production with empty grants.
    """

    name = "null"

    def verify(self, asset_ref: str | None) -> AssetAuthorization | None:
        return None


class StaticAuthorizationProvider:
    """Explicit in-process authorizations — the shape M8's registry will serve.

    Used by tests and by a deliberate local configuration; it never reads a
    caller-supplied field to decide what is authorized.
    """

    name = "static"

    def __init__(self, authorizations: dict[str, dict[str, list[str]]]) -> None:
        self._authorizations = {
            asset_ref: AssetAuthorization(
                asset_ref=asset_ref,
                source=self.name,
                allowed={
                    klass: frozenset(values or ())
                    for klass, values in (allowed or {}).items()
                    if klass in PERMISSION_CLASSES
                },
            )
            for asset_ref, allowed in authorizations.items()
        }

    def verify(self, asset_ref: str | None) -> AssetAuthorization | None:
        if not asset_ref:
            return None
        return self._authorizations.get(asset_ref)


__all__ = [
    "PERMISSION_CLASSES",
    "AssetAuthorization",
    "AuthorizationProvider",
    "NullAuthorizationProvider",
    "StaticAuthorizationProvider",
]
