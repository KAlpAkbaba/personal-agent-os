"""`AuthorizationProvider` backed by the Authorized Asset Registry (M8).

This is the module that closes the M7 gap. The ADR-0025 security addendum
introduced `app.evolution.authorization.AuthorizationProvider` precisely
because permission approval had been keying on a caller-asserted
`authorized_asset` string with nothing to verify it against. The default
provider verifies nothing, so every generated-skill permission grant is
refused and generated skills reach production with empty grants.

`RegistryAuthorizationProvider` replaces that deny-everything default with a
real verification source: an evolution grant is approvable **because a registry
row proves the owner recorded that permission for that asset**, never because a
requester named an asset.

The verification rules are deliberately the same fail-safe ones `scope.py`
uses, because they answer the same constitutional question about the same
table:

- unknown `asset_ref`               -> None (unverifiable -> refused upstream)
- suspended / revoked / expired     -> None
- outside the validity window       -> None
- active and in-window              -> the recorded `allowed_permissions_json`,
                                       filtered to the known permission classes

`AssetAuthorization.covers()` (M7 code, untouched) then requires every
requested grant to be a subset of what the row records, so recording
`network_permissions: ["lab.example.test"]` does not authorize
`network_permissions: ["*"]`.

Note what this provider does NOT do: it never widens, creates or repairs a
registry row, and it never consults `allowed_testing`. Security-testing scope
and evolution permission scope are separate columns on the same authorization,
so one cannot be used to bootstrap the other.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evolution.authorization import (
    PERMISSION_CLASSES,
    AssetAuthorization,
)
from app.logging import get_logger
from app.security.models import ASSET_STATUS_ACTIVE, AuthorizedAsset
from app.security.registry import ASSET_REF_RE, as_aware, utcnow

logger = get_logger("app.security.provider")

SessionFactory = Callable[[], AbstractContextManager[Session]]


class RegistryAuthorizationProvider:
    """Verifies an asset reference against `authorized_assets`."""

    name = "registry"

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    def verify(self, asset_ref: str | None) -> AssetAuthorization | None:
        """Return the owner-recorded authorization, or None when unverifiable."""
        if not asset_ref:
            return None
        candidate = asset_ref.strip().lower()
        # Cheap shape gate before touching the database; the same token shape
        # enrollment enforces, so a hostile ref cannot even become a query.
        if not ASSET_REF_RE.match(candidate):
            return None

        with self._session_factory() as session:
            asset = session.execute(
                select(AuthorizedAsset).where(AuthorizedAsset.asset_ref == candidate)
            ).scalar_one_or_none()
            if asset is None:
                logger.info("authorization_unverified", asset_ref=candidate, reason="not_enrolled")
                return None
            if asset.status != ASSET_STATUS_ACTIVE:
                logger.info(
                    "authorization_unverified", asset_ref=candidate, reason=asset.status
                )
                return None
            now = self._clock()
            starts = as_aware(asset.valid_from)
            ends = as_aware(asset.valid_until)
            if (starts is not None and starts > now) or (ends is not None and ends <= now):
                logger.info(
                    "authorization_unverified", asset_ref=candidate, reason="outside_window"
                )
                return None
            recorded = dict(asset.allowed_permissions_json or {})
            allowed = {
                klass: frozenset(
                    str(v) for v in (recorded.get(klass) or ()) if isinstance(v, str)
                )
                for klass in PERMISSION_CLASSES
                if recorded.get(klass)
            }
            return AssetAuthorization(
                asset_ref=asset.asset_ref, source=self.name, allowed=allowed
            )


__all__ = ["RegistryAuthorizationProvider"]
