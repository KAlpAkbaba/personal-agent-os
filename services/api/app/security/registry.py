"""Authorized Asset Registry (SECURITY_MODEL §7, ADR-0026).

The registry is the ONLY place that records what the owner authorized. Its
rules are enforced in code, not by convention:

1. An asset cannot be enrolled without **authorization evidence** and a
   **validity window**. "Someone said it was fine" is not enrollable state.
2. `kind` and `locator` are IMMUTABLE after enrollment. Widening what a
   registry row points at is exactly the scope drift constitution §8 forbids,
   and it would silently re-target every assessment that already ran against
   that asset. Re-pointing requires revoke + re-enroll, which leaves two
   auditable events instead of one invisible mutation.
3. Every mutation — enrollment, scope change, suspension, reinstatement,
   revocation, expiry — writes an append-only `authorization_events` row.
   `record_event` is also what `scope.py` calls for every authorized and every
   REFUSED attempt, so one table answers "what was ever allowed, and what was
   asked for and denied".
4. Evidence and constraints are redacted before storage: an owner pasting a
   ticket body or an approval email into `evidence` must not turn the registry
   into a credential store.

Expiry is handled two ways so a stale row can never grant access: `sweep_expired`
flips due assets in bulk, and `scope.py` re-derives the window on every single
decision (and flips the row when it finds one past `valid_until`).
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evolution.authorization import PERMISSION_CLASSES
from app.logging import get_logger
from app.security.errors import SecurityError, SecurityErrorClass
from app.security.models import (
    ASSET_KINDS,
    ASSET_STATUS_ACTIVE,
    ASSET_STATUS_EXPIRED,
    ASSET_STATUS_REVOKED,
    ASSET_STATUS_SUSPENDED,
    ASSET_STATUSES,
    DISRUPTION_LEVELS,
    ENVIRONMENTS,
    EVENT_ACTIONS,
    EVENT_ENROLLED,
    EVENT_EXPIRED,
    EVENT_REVOKED,
    EVENT_SCOPE_CHANGED,
    EVENT_SUSPENDED,
    TESTING_CLASSES,
    AuthorizationEvent,
    AuthorizedAsset,
)
from app.security.redaction import assert_redacted, redact_value

logger = get_logger("app.security.registry")

SessionFactory = Callable[[], AbstractContextManager[Session]]

# ------------------------------------------------------------------- bounds
MAX_ASSET_REF = 128
MAX_NAME = 256
MAX_LOCATOR = 512
MAX_REASON = 512
MAX_CONFIG_ROOTS = 8
MAX_PERMISSION_VALUES = 32
MAX_EVIDENCE_KEYS = 16
MAX_EVIDENCE_VALUE = 2000

# An asset_ref is an identifier, not free text: it appears in audit rows, in
# artifact bodies and in evolution permission lookups.
ASSET_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
# DNS label rules (RFC 1123), lowercase-normalized.
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
# device ids / service + repository locators: printable, no whitespace, no
# credentials-in-URL ("@"), no backslashes, no traversal.
OPAQUE_LOCATOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,255}$")

# Evidence must record HOW the owner authorized this asset and WHO recorded it.
REQUIRED_EVIDENCE_KEYS = ("authorization", "recorded_by")

REINSTATABLE_FROM = (ASSET_STATUS_SUSPENDED, ASSET_STATUS_EXPIRED)


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_aware(value: datetime | None) -> datetime | None:
    """Postgres returns tz-aware datetimes; SQLite returns naive ones."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _invalid(message: str, **details: Any) -> SecurityError:
    return SecurityError(SecurityErrorClass.VALIDATION_ERROR, message, details)


# ------------------------------------------------------------- normalization


def normalize_locator(kind: str, locator: str) -> str:
    """Canonical stored form of a locator, validated for its kind.

    Normalization happens ONCE, at enrollment, so scope matching compares two
    canonical strings rather than guessing at request time.
    """
    raw = (locator or "").strip()
    if not raw or len(raw) > MAX_LOCATOR:
        raise _invalid("locator must be 1..512 characters")
    lowered = raw.lower().rstrip(".")

    if kind == "network":
        # An explicit prefix is required. `ip_network("10.20.30.40")` silently
        # means /32, and an owner who meant a range would never notice.
        if "/" not in raw:
            raise _invalid("network locator must carry an explicit CIDR prefix, e.g. /24")
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError as exc:
            raise _invalid(f"network locator is not a valid CIDR: {exc}") from exc
        return str(network)

    if kind == "host":
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            pass
        if not HOSTNAME_RE.match(lowered):
            raise _invalid("host locator must be an IP address or a DNS hostname")
        return lowered

    if kind == "domain":
        if not HOSTNAME_RE.match(lowered):
            raise _invalid("domain locator must be a DNS name")
        try:
            ipaddress.ip_address(lowered)
        except ValueError:
            return lowered
        raise _invalid("domain locator must not be an IP address")

    # device / service / repository
    if not OPAQUE_LOCATOR_RE.match(raw):
        raise _invalid(f"{kind} locator has an unsupported character set")
    if ".." in raw:
        raise _invalid("locator must not contain '..'")
    return lowered.rstrip("/")


def _validate_allowed_testing(value: Any) -> dict[str, bool]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid("allowed_testing must be an object of {testing_class: bool}")
    out: dict[str, bool] = {}
    for key, enabled in value.items():
        if key not in TESTING_CLASSES:
            raise _invalid(
                f"unknown testing class: {key!r}", allowed=list(TESTING_CLASSES)
            )
        if not isinstance(enabled, bool):
            raise _invalid(f"allowed_testing[{key!r}] must be a boolean")
        out[key] = enabled
    return out


def _validate_allowed_permissions(value: Any) -> dict[str, list[str]]:
    """Evolution permission grants the owner recorded for this asset.

    Shape matches app.evolution.authorization.PERMISSION_CLASSES, because
    provider.py serves exactly this dict to the M7 permission model.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid("allowed_permissions must be an object of {class: [values]}")
    out: dict[str, list[str]] = {}
    for key, values in value.items():
        if key not in PERMISSION_CLASSES:
            raise _invalid(
                f"unknown permission class: {key!r}", allowed=list(PERMISSION_CLASSES)
            )
        if not isinstance(values, list):
            raise _invalid(f"allowed_permissions[{key!r}] must be a list")
        if len(values) > MAX_PERMISSION_VALUES:
            raise _invalid(f"allowed_permissions[{key!r}] has too many entries")
        cleaned: list[str] = []
        for item in values:
            if not isinstance(item, str) or not item.strip() or len(item) > 128:
                raise _invalid(f"allowed_permissions[{key!r}] entries must be 1..128 char strings")
            cleaned.append(item.strip())
        out[key] = sorted(set(cleaned))
    return out


def _validate_constraints(value: Any) -> dict[str, Any]:
    """Stored operational constraints. `max_disruption` is the ladder gate and
    `config_roots` is the ONLY place a collector may read from."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid("constraints must be an object")
    out: dict[str, Any] = dict(value)

    max_disruption = out.get("max_disruption", "none")
    if max_disruption not in DISRUPTION_LEVELS:
        raise _invalid(
            f"constraints.max_disruption must be one of {DISRUPTION_LEVELS}",
            got=str(max_disruption)[:32],
        )
    out["max_disruption"] = max_disruption

    roots = out.get("config_roots", [])
    if roots in (None, ""):
        roots = []
    if not isinstance(roots, list):
        raise _invalid("constraints.config_roots must be a list of absolute paths")
    if len(roots) > MAX_CONFIG_ROOTS:
        raise _invalid(f"constraints.config_roots holds at most {MAX_CONFIG_ROOTS} paths")
    cleaned_roots: list[str] = []
    for root in roots:
        if not isinstance(root, str) or not root.strip() or len(root) > MAX_LOCATOR:
            raise _invalid("constraints.config_roots entries must be 1..512 char paths")
        cleaned_roots.append(root.strip())
    out["config_roots"] = cleaned_roots

    if "maintenance_window" in out and not isinstance(out["maintenance_window"], str | None):
        raise _invalid("constraints.maintenance_window must be a string")
    return out


def _validate_evidence(value: Any) -> dict[str, Any]:
    """Authorization evidence is MANDATORY (rule 1) and bounded."""
    if not isinstance(value, dict) or not value:
        raise _invalid(
            "evidence is required and must record how the owner authorized this asset",
            required_keys=list(REQUIRED_EVIDENCE_KEYS),
        )
    missing = [k for k in REQUIRED_EVIDENCE_KEYS if not str(value.get(k, "")).strip()]
    if missing:
        raise _invalid("evidence is missing required keys", missing=missing)
    if len(value) > MAX_EVIDENCE_KEYS:
        raise _invalid(f"evidence holds at most {MAX_EVIDENCE_KEYS} keys")
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 64:
            raise _invalid("evidence keys must be strings of at most 64 characters")
        if isinstance(item, str) and len(item) > MAX_EVIDENCE_VALUE:
            raise _invalid(f"evidence[{key!r}] exceeds {MAX_EVIDENCE_VALUE} characters")
    return dict(value)


def asset_to_dict(asset: AuthorizedAsset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "asset_ref": asset.asset_ref,
        "name": asset.name,
        "kind": asset.kind,
        "locator": asset.locator,
        "environment": asset.environment,
        "status": asset.status,
        "allowed_testing": dict(asset.allowed_testing_json or {}),
        "allowed_permissions": dict(asset.allowed_permissions_json or {}),
        "constraints": dict(asset.constraints_json or {}),
        "evidence": dict(asset.evidence_json or {}),
        "valid_from": iso8601(asset.valid_from),
        "valid_until": iso8601(asset.valid_until),
        "created_at": iso8601(asset.created_at),
        "updated_at": iso8601(asset.updated_at),
        "revoked_at": iso8601(asset.revoked_at),
    }


def event_to_dict(event: AuthorizationEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "action": event.action,
        "asset_ref": event.asset_ref,
        "asset_id": str(event.asset_id) if event.asset_id else None,
        "requested_target": event.requested_target,
        "testing_class": event.testing_class,
        "allowed": bool(event.allowed),
        "reason": event.reason,
        "detail": dict(event.detail_json or {}),
        "trace_id": event.trace_id,
        "created_at": iso8601(event.created_at),
    }


def iso8601(value: datetime | None) -> str | None:
    aware = as_aware(value)
    return aware.isoformat() if aware else None


class AuthorizedAssetRegistry:
    """Owner-authorized asset lifecycle + the append-only authorization audit."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------- audit log

    def record_event(
        self,
        session: Session,
        *,
        action: str,
        allowed: bool,
        reason: str,
        asset: AuthorizedAsset | None = None,
        asset_ref: str | None = None,
        requested_target: str | None = None,
        testing_class: str | None = None,
        detail: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> AuthorizationEvent:
        """Append one authorization event. Never raises on redaction: the detail
        payload is redacted and then re-checked, so an audit row can carry the
        SHAPE of a secret exposure but never the secret (M8 §8)."""
        if action not in EVENT_ACTIONS:
            raise SecurityError(
                SecurityErrorClass.INTERNAL_BUG, f"unknown authorization event action: {action!r}"
            )
        safe_detail = assert_redacted(redact_value(detail or {}), where=f"event.{action}")
        event = AuthorizationEvent(
            action=action,
            asset_ref=(asset.asset_ref if asset is not None else asset_ref),
            asset_id=(asset.id if asset is not None else None),
            requested_target=(requested_target or None),
            testing_class=(testing_class or None),
            allowed=allowed,
            reason=reason[:MAX_REASON],
            detail_json=safe_detail,
            trace_id=trace_id,
        )
        session.add(event)
        session.commit()
        logger.info(
            "authorization_event",
            action=action,
            allowed=allowed,
            reason=reason[:120],
            asset_ref=event.asset_ref,
        )
        return event

    def list_events(
        self,
        *,
        action: str | None = None,
        asset_ref: str | None = None,
        allowed: bool | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(AuthorizationEvent)
            if action:
                stmt = stmt.where(AuthorizationEvent.action == action)
            if asset_ref:
                stmt = stmt.where(AuthorizationEvent.asset_ref == asset_ref)
            if allowed is not None:
                stmt = stmt.where(AuthorizationEvent.allowed == allowed)
            stmt = stmt.order_by(AuthorizationEvent.id.desc()).limit(limit)
            return [event_to_dict(e) for e in session.execute(stmt).scalars()]

    # ------------------------------------------------------------ enrollment

    def enroll(
        self,
        *,
        asset_ref: str,
        name: str,
        kind: str,
        locator: str,
        environment: str = "lab",
        allowed_testing: dict[str, bool] | None = None,
        allowed_permissions: dict[str, list[str]] | None = None,
        constraints: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        ref = (asset_ref or "").strip().lower()
        if not ASSET_REF_RE.match(ref):
            raise _invalid(
                "asset_ref must be 2..64 chars of [a-z0-9._-] starting alphanumeric",
                asset_ref=ref[:MAX_ASSET_REF],
            )
        clean_name = (name or "").strip()
        if not clean_name or len(clean_name) > MAX_NAME:
            raise _invalid("name must be 1..256 characters")
        if kind not in ASSET_KINDS:
            raise _invalid(f"kind must be one of {ASSET_KINDS}", got=str(kind)[:32])
        if environment not in ENVIRONMENTS:
            raise _invalid(f"environment must be one of {ENVIRONMENTS}", got=str(environment)[:32])

        canonical_locator = normalize_locator(kind, locator)
        testing = _validate_allowed_testing(allowed_testing)
        permissions = _validate_allowed_permissions(allowed_permissions)
        limits = _validate_constraints(constraints)
        proof = _validate_evidence(evidence)

        starts = as_aware(valid_from) or utcnow()
        ends = as_aware(valid_until)
        if ends is not None and ends <= starts:
            raise _invalid("valid_until must be after valid_from")

        with self._session_factory() as session:
            existing = session.execute(
                select(AuthorizedAsset).where(AuthorizedAsset.asset_ref == ref)
            ).scalar_one_or_none()
            if existing is not None:
                raise SecurityError(
                    SecurityErrorClass.ALREADY_ENROLLED,
                    f"asset_ref {ref!r} is already enrolled",
                    {"asset_ref": ref, "status": existing.status},
                )
            asset = AuthorizedAsset(
                asset_ref=ref,
                name=clean_name,
                kind=kind,
                locator=canonical_locator,
                environment=environment,
                status=ASSET_STATUS_ACTIVE,
                allowed_testing_json=testing,
                allowed_permissions_json=permissions,
                # Redacted: an owner may paste an approval note into either.
                constraints_json=assert_redacted(redact_value(limits), where="asset.constraints"),
                evidence_json=assert_redacted(redact_value(proof), where="asset.evidence"),
                valid_from=starts,
                valid_until=ends,
            )
            session.add(asset)
            session.commit()
            self.record_event(
                session,
                action=EVENT_ENROLLED,
                allowed=True,
                reason="owner enrolled an authorized asset",
                asset=asset,
                detail={
                    "kind": kind,
                    "locator": canonical_locator,
                    "environment": environment,
                    "allowed_testing": testing,
                    "allowed_permissions": permissions,
                    "constraints": limits,
                    "valid_from": iso8601(starts),
                    "valid_until": iso8601(ends),
                },
                trace_id=trace_id,
            )
            return asset_to_dict(asset)

    # --------------------------------------------------------------- reading

    def _load(self, session: Session, ref: str) -> AuthorizedAsset:
        """Resolve by asset_ref or by UUID; raises typed NOT_FOUND."""
        needle = (ref or "").strip()
        asset: AuthorizedAsset | None = None
        if needle:
            asset = session.execute(
                select(AuthorizedAsset).where(AuthorizedAsset.asset_ref == needle.lower())
            ).scalar_one_or_none()
            if asset is None:
                try:
                    asset = session.get(AuthorizedAsset, uuid.UUID(needle))
                except ValueError:
                    asset = None
        if asset is None:
            raise SecurityError(
                SecurityErrorClass.NOT_FOUND,
                f"no enrolled asset matches {needle[:MAX_ASSET_REF]!r}",
                {"asset_ref": needle[:MAX_ASSET_REF]},
            )
        return asset

    def get(self, ref: str) -> dict[str, Any]:
        with self._session_factory() as session:
            return asset_to_dict(self._load(session, ref))

    def list(
        self, *, status: str | None = None, kind: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(AuthorizedAsset)
            if status:
                if status not in ASSET_STATUSES:
                    raise _invalid(f"status must be one of {ASSET_STATUSES}")
                stmt = stmt.where(AuthorizedAsset.status == status)
            if kind:
                if kind not in ASSET_KINDS:
                    raise _invalid(f"kind must be one of {ASSET_KINDS}")
                stmt = stmt.where(AuthorizedAsset.kind == kind)
            stmt = stmt.order_by(AuthorizedAsset.asset_ref).limit(limit)
            return [asset_to_dict(a) for a in session.execute(stmt).scalars()]

    def all_assets(self, session: Session) -> list[AuthorizedAsset]:
        """Every enrolled asset regardless of status.

        Scope matching deliberately looks at revoked/suspended/expired rows too,
        so a refusal can say WHY ("revoked") instead of the less useful
        "unknown target" — while still refusing.
        """
        return list(
            session.execute(select(AuthorizedAsset).order_by(AuthorizedAsset.asset_ref)).scalars()
        )

    # -------------------------------------------------------- scope mutation

    def change_scope(
        self,
        ref: str,
        *,
        name: str | None = None,
        environment: str | None = None,
        allowed_testing: dict[str, bool] | None = None,
        allowed_permissions: dict[str, list[str]] | None = None,
        constraints: dict[str, Any] | None = None,
        valid_until: datetime | None = None,
        clear_valid_until: bool = False,
        reason: str = "owner scope change",
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Change what an enrolled asset is authorized FOR.

        `kind` and `locator` are intentionally absent: see module rule 2.
        """
        with self._session_factory() as session:
            asset = self._load(session, ref)
            if asset.status == ASSET_STATUS_REVOKED:
                raise _invalid(
                    "a revoked asset cannot be re-scoped; enroll a new asset instead",
                    asset_ref=asset.asset_ref,
                )
            before = asset_to_dict(asset)

            if name is not None:
                clean = name.strip()
                if not clean or len(clean) > MAX_NAME:
                    raise _invalid("name must be 1..256 characters")
                asset.name = clean
            if environment is not None:
                if environment not in ENVIRONMENTS:
                    raise _invalid(f"environment must be one of {ENVIRONMENTS}")
                asset.environment = environment
            if allowed_testing is not None:
                asset.allowed_testing_json = _validate_allowed_testing(allowed_testing)
            if allowed_permissions is not None:
                asset.allowed_permissions_json = _validate_allowed_permissions(allowed_permissions)
            if constraints is not None:
                asset.constraints_json = assert_redacted(
                    redact_value(_validate_constraints(constraints)), where="asset.constraints"
                )
            if clear_valid_until:
                asset.valid_until = None
            elif valid_until is not None:
                ends = as_aware(valid_until)
                if ends is not None and ends <= (as_aware(asset.valid_from) or utcnow()):
                    raise _invalid("valid_until must be after valid_from")
                asset.valid_until = ends
                # Extending a window on an expired asset reinstates it; a
                # window still in the past does not.
                if asset.status == ASSET_STATUS_EXPIRED and ends and ends > utcnow():
                    asset.status = ASSET_STATUS_ACTIVE

            asset.updated_at = utcnow()
            session.commit()
            after = asset_to_dict(asset)
            self.record_event(
                session,
                action=EVENT_SCOPE_CHANGED,
                allowed=True,
                reason=reason,
                asset=asset,
                detail={"before": _scope_slice(before), "after": _scope_slice(after)},
                trace_id=trace_id,
            )
            return after

    def suspend(
        self, ref: str, *, reason: str = "owner suspended", trace_id: str | None = None
    ) -> dict[str, Any]:
        return self._set_status(
            ref,
            ASSET_STATUS_SUSPENDED,
            EVENT_SUSPENDED,
            reason=reason,
            trace_id=trace_id,
        )

    def revoke(
        self, ref: str, *, reason: str = "owner revoked", trace_id: str | None = None
    ) -> dict[str, Any]:
        return self._set_status(
            ref, ASSET_STATUS_REVOKED, EVENT_REVOKED, reason=reason, trace_id=trace_id
        )

    def reinstate(
        self, ref: str, *, reason: str = "owner reinstated", trace_id: str | None = None
    ) -> dict[str, Any]:
        """Suspended/expired -> active. Revocation is terminal by design."""
        with self._session_factory() as session:
            asset = self._load(session, ref)
            if asset.status not in REINSTATABLE_FROM:
                raise _invalid(
                    f"only {REINSTATABLE_FROM} assets can be reinstated",
                    status=asset.status,
                )
            ends = as_aware(asset.valid_until)
            if ends is not None and ends <= utcnow():
                raise _invalid(
                    "authorization window has passed; extend valid_until instead",
                    valid_until=iso8601(ends),
                )
            asset.status = ASSET_STATUS_ACTIVE
            asset.updated_at = utcnow()
            session.commit()
            self.record_event(
                session,
                action=EVENT_SCOPE_CHANGED,
                allowed=True,
                reason=reason,
                asset=asset,
                detail={"status": ASSET_STATUS_ACTIVE},
                trace_id=trace_id,
            )
            return asset_to_dict(asset)

    def _set_status(
        self, ref: str, status: str, action: str, *, reason: str, trace_id: str | None
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            asset = self._load(session, ref)
            if asset.status == ASSET_STATUS_REVOKED:
                raise _invalid("asset is already revoked", asset_ref=asset.asset_ref)
            asset.status = status
            asset.updated_at = utcnow()
            if status == ASSET_STATUS_REVOKED:
                asset.revoked_at = utcnow()
            session.commit()
            self.record_event(
                session,
                action=action,
                allowed=False,
                reason=reason,
                asset=asset,
                detail={"status": status},
                trace_id=trace_id,
            )
            return asset_to_dict(asset)

    # ---------------------------------------------------------------- expiry

    def mark_expired(
        self, session: Session, asset: AuthorizedAsset, *, trace_id: str | None = None
    ) -> None:
        """Flip one asset whose window has passed (called from scope.py)."""
        if asset.status != ASSET_STATUS_ACTIVE:
            return
        asset.status = ASSET_STATUS_EXPIRED
        asset.updated_at = utcnow()
        session.commit()
        self.record_event(
            session,
            action=EVENT_EXPIRED,
            allowed=False,
            reason="authorization window elapsed",
            asset=asset,
            detail={"valid_until": iso8601(asset.valid_until)},
            trace_id=trace_id,
        )

    def sweep_expired(self, *, now: datetime | None = None) -> list[str]:
        """Bulk expiry pass. Returns the asset_refs that were flipped."""
        moment = as_aware(now) or utcnow()
        flipped: list[str] = []
        with self._session_factory() as session:
            assets = session.execute(
                select(AuthorizedAsset).where(AuthorizedAsset.status == ASSET_STATUS_ACTIVE)
            ).scalars()
            due = [
                a
                for a in assets
                if (ends := as_aware(a.valid_until)) is not None and ends <= moment
            ]
            for asset in due:
                self.mark_expired(session, asset)
                flipped.append(asset.asset_ref)
        return flipped


def _scope_slice(payload: dict[str, Any]) -> dict[str, Any]:
    """The fields a scope change may touch — keeps the diff readable."""
    return {
        key: payload.get(key)
        for key in (
            "name",
            "environment",
            "status",
            "allowed_testing",
            "allowed_permissions",
            "constraints",
            "valid_until",
        )
    }


__all__ = [
    "ASSET_REF_RE",
    "HOSTNAME_RE",
    "OPAQUE_LOCATOR_RE",
    "REQUIRED_EVIDENCE_KEYS",
    "AuthorizedAssetRegistry",
    "as_aware",
    "asset_to_dict",
    "event_to_dict",
    "iso8601",
    "normalize_locator",
    "utcnow",
]
