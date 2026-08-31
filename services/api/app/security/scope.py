"""THE enforcement point (constitution §8, SECURITY_MODEL §8, ADR-0026).

    Is this target/action within the owner's stored authorization scope?

Nothing else in the product may answer that question. `assessments.py` and
`remediation.py` both route through `ScopeGuard.evaluate`, and neither can
reach a target the guard did not approve.

FAIL-SAFE BY CONSTRUCTION. Every path that is not an affirmative match against
exactly one active, in-window asset whose recorded scope covers the requested
testing class and disruption level returns `allowed=False`. That includes
unknown, malformed, ambiguous, expired, suspended and revoked. There is no
"probably fine" branch, and there is no branch that enrolls anything: a refusal
NEVER widens the registry, it only appends an `assessment_refused` /
`remediation_refused` event (SECURITY_MODEL §8: "do not silently broaden the
target. Ask for a one-time enrollment/scope update").

THE SPOOFING RULES (the part that actually matters)

1. Membership is computed with the `ipaddress` module on parsed objects, never
   by string prefixing. `10.20.30.0/24` contains `10.20.30.40` because
   `ip_address in ip_network`, not because one string starts with the other.

2. A target is classified into EXACTLY ONE kind before matching, and a
   hostname is never treated as an address. `10.20.30.40.evil.com` fails
   `ip_address()`, so it classifies as a HOSTNAME and is matched only against
   `host`/`domain` assets by DNS-label equality. It can therefore never fall
   inside an authorized CIDR — the classic "IP embedded in a hostname" bypass.
   Likewise `10.20.30.40@evil.com`, `10.20.30.40 evil.com` and
   `http://evil.com#10.20.30.40` are rejected outright by the character set.

3. NO DNS RESOLUTION, EVER. Resolving a hostname to an IP would hand scope
   authority to whoever controls DNS: an attacker (or a stale record) could
   point an in-scope name at an out-of-scope address, or vice versa. A
   hostname target matches only a hostname/domain asset; an IP target matches
   only an IP/CIDR asset. Cross-kind matching is refused, not resolved.

4. Domain containment is LABEL-WISE, not suffix-string-wise. `example.com`
   authorizes `api.example.com` but not `evil-example.com` (no label boundary)
   and not `example.com.evil.com` (wrong end).

5. AMBIGUITY IS REFUSAL. If a target matches two different assets — say it is
   both an enrolled host and inside an enrolled CIDR with different
   constraints — the guard refuses rather than picking the more permissive (or
   even the stricter) row. The owner fixes the registry; the agent does not
   guess which authorization applies.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.security.models import (
    ASSET_STATUS_ACTIVE,
    ASSET_STATUS_EXPIRED,
    ASSET_STATUS_REVOKED,
    ASSET_STATUS_SUSPENDED,
    DISRUPTION_LEVELS,
    DISRUPTION_ORDER,
    EVENT_ASSESSMENT_AUTHORIZED,
    EVENT_ASSESSMENT_REFUSED,
    EVENT_REMEDIATION_AUTHORIZED,
    EVENT_REMEDIATION_REFUSED,
    IMPLEMENTED_TESTING_CLASSES,
    TESTING_CLASSES,
    AuthorizedAsset,
)
from app.security.registry import (
    HOSTNAME_RE,
    OPAQUE_LOCATOR_RE,
    AuthorizedAssetRegistry,
    as_aware,
    utcnow,
)

logger = get_logger("app.security.scope")

MAX_TARGET_LENGTH = 512

# Character set a requested target may use. Deliberately narrow: no whitespace,
# no "@" (credentials/authority confusion), no "\", no "%", no "?#", no quotes.
TARGET_CHARSET_RE = re.compile(r"^[A-Za-z0-9._:/+\-]{1,512}$")

# ------------------------------------------------------------- target kinds
TARGET_IP = "ip"
TARGET_NETWORK = "network"
TARGET_HOSTNAME = "hostname"
TARGET_OPAQUE = "opaque"

# ------------------------------------------------------------ reason codes
REASON_IN_SCOPE = "in_scope"
REASON_INVALID_TARGET = "invalid_target"
REASON_UNRESOLVABLE_TARGET = "unresolvable_target"
REASON_NO_AUTHORIZED_ASSET = "no_authorized_asset"
REASON_AMBIGUOUS_TARGET = "ambiguous_target"
REASON_ASSET_SUSPENDED = "asset_suspended"
REASON_ASSET_REVOKED = "asset_revoked"
REASON_AUTHORIZATION_EXPIRED = "authorization_expired"
REASON_NOT_YET_VALID = "authorization_not_yet_valid"
REASON_UNKNOWN_TESTING_CLASS = "unknown_testing_class"
REASON_TESTING_CLASS_NOT_IMPLEMENTED = "testing_class_not_implemented"
REASON_TESTING_CLASS_NOT_ALLOWED = "testing_class_not_allowed"
REASON_INVALID_DISRUPTION = "invalid_disruption"
REASON_DISRUPTION_EXCEEDS_CONSTRAINT = "disruption_exceeds_constraint"

# ------------------------------------------------------------ match methods
MATCH_EXACT_ADDRESS = "exact_address"
MATCH_CIDR_MEMBERSHIP = "cidr_membership"
MATCH_CIDR_SUBNET = "cidr_subnet"
MATCH_EXACT_HOSTNAME = "exact_hostname"
MATCH_DOMAIN_SUFFIX = "domain_suffix_labels"
MATCH_EXACT_IDENTIFIER = "exact_identifier"

# Turkish-first refusal messages (tr-TR is first-class, constitution).
_MESSAGES: dict[str, str] = {
    REASON_IN_SCOPE: "Hedef kayıtlı yetki kapsamı içinde.",
    REASON_INVALID_TARGET: "Hedef biçimi geçersiz; istek reddedildi.",
    REASON_UNRESOLVABLE_TARGET: "Hedef türü belirlenemedi; istek reddedildi.",
    REASON_NO_AUTHORIZED_ASSET: (
        "Hedef için kayıtlı bir yetkili varlık yok. Kapsam sessizce genişletilmez; "
        "tek seferlik kayıt/kapsam güncellemesi gerekir."
    ),
    REASON_AMBIGUOUS_TARGET: (
        "Hedef birden fazla yetkili varlıkla eşleşiyor; hangi yetkinin geçerli "
        "olduğu tahmin edilmez."
    ),
    REASON_ASSET_SUSPENDED: "Varlığın yetkisi askıya alınmış.",
    REASON_ASSET_REVOKED: "Varlığın yetkisi iptal edilmiş.",
    REASON_AUTHORIZATION_EXPIRED: "Yetki geçerlilik penceresi dolmuş.",
    REASON_NOT_YET_VALID: "Yetki geçerlilik penceresi henüz başlamamış.",
    REASON_UNKNOWN_TESTING_CLASS: "Bilinmeyen test sınıfı.",
    REASON_TESTING_CLASS_NOT_IMPLEMENTED: "Bu test sınıfı bu sürümde uygulanmıyor.",
    REASON_TESTING_CLASS_NOT_ALLOWED: "Bu test sınıfı bu varlık için yetkilendirilmemiş.",
    REASON_INVALID_DISRUPTION: "Geçersiz kesinti seviyesi.",
    REASON_DISRUPTION_EXCEEDS_CONSTRAINT: (
        "İstenen kesinti seviyesi varlığın kayıtlı max_disruption sınırını aşıyor."
    ),
}


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    """The typed answer to the enforcement question."""

    allowed: bool
    reason: str
    message: str
    target: str
    target_kind: str
    testing_class: str
    disruption: str
    asset_id: uuid.UUID | None = None
    asset_ref: str | None = None
    asset_kind: str | None = None
    matched_by: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "message": self.message,
            "target": self.target,
            "target_kind": self.target_kind,
            "testing_class": self.testing_class,
            "disruption": self.disruption,
            "asset_id": str(self.asset_id) if self.asset_id else None,
            "asset_ref": self.asset_ref,
            "asset_kind": self.asset_kind,
            "matched_by": self.matched_by,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class ParsedTarget:
    kind: str
    normalized: str
    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    network: ipaddress.IPv4Network | ipaddress.IPv6Network | None = None
    labels: tuple[str, ...] = ()


# --------------------------------------------------------------- classification


def classify_target(raw: str) -> ParsedTarget | None:
    """Classify a requested target into EXACTLY ONE kind, or None if refused.

    Order is fixed and total; there is no fallthrough that guesses.
    """
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > MAX_TARGET_LENGTH:
        return None
    if not TARGET_CHARSET_RE.match(candidate):
        return None
    if ".." in candidate:
        return None

    # 1) CIDR — only when a "/" is present AND the whole string parses.
    if "/" in candidate:
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            network = None
        if network is not None:
            return ParsedTarget(TARGET_NETWORK, str(network), network=network)

    # 2) Bare address. Note this is tried BEFORE hostname, and hostname is
    #    tried only if this fails — a string is never both.
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        address = None
    if address is not None:
        return ParsedTarget(TARGET_IP, str(address), address=address)

    # 3) DNS name. `10.20.30.40.evil.com` lands here (spoofing rule 2).
    lowered = candidate.lower().rstrip(".")
    if HOSTNAME_RE.match(lowered):
        labels = tuple(lowered.split("."))
        # An all-numeric last label is address-shaped but did not parse as an
        # address (e.g. "1.2.3.4.5"). Refuse rather than treat it as a name.
        if labels[-1].isdigit():
            return None
        return ParsedTarget(TARGET_HOSTNAME, lowered, labels=labels)

    # 4) device id / service or repository locator.
    if OPAQUE_LOCATOR_RE.match(candidate):
        return ParsedTarget(TARGET_OPAQUE, lowered.rstrip("/"))

    return None


# -------------------------------------------------------------- pure matching


def _match_host(target: ParsedTarget, locator: str) -> str | None:
    """`host` asset: locator is either an IP or a DNS name (normalized at
    enrollment). Cross-kind never matches — no DNS resolution (rule 3)."""
    try:
        locator_address = ipaddress.ip_address(locator)
    except ValueError:
        locator_address = None

    if locator_address is not None:
        if target.kind == TARGET_IP and target.address == locator_address:
            return MATCH_EXACT_ADDRESS
        return None

    if target.kind == TARGET_HOSTNAME and target.normalized == locator:
        return MATCH_EXACT_HOSTNAME
    return None


def _match_network(target: ParsedTarget, locator: str) -> str | None:
    """`network` asset: real ipaddress membership (rule 1).

    A HOSTNAME target can never match here, which is what stops
    `10.20.30.40.evil.com` from being read as a member of `10.20.30.0/24`.
    """
    try:
        network = ipaddress.ip_network(locator, strict=False)
    except ValueError:
        return None
    if target.kind == TARGET_IP and target.address is not None:
        if target.address.version != network.version:
            return None
        return MATCH_CIDR_MEMBERSHIP if target.address in network else None
    if target.kind == TARGET_NETWORK and target.network is not None:
        if target.network.version != network.version:
            return None
        # The whole requested range must be inside the authorized range.
        return MATCH_CIDR_SUBNET if target.network.subnet_of(network) else None
    return None


def _match_domain(target: ParsedTarget, locator: str) -> str | None:
    """`domain` asset: label-wise containment (rule 4)."""
    if target.kind != TARGET_HOSTNAME or not target.labels:
        return None
    domain_labels = tuple(locator.split("."))
    if len(target.labels) < len(domain_labels):
        return None
    if target.labels[-len(domain_labels) :] != domain_labels:
        return None
    return MATCH_EXACT_HOSTNAME if len(target.labels) == len(domain_labels) else MATCH_DOMAIN_SUFFIX


def _match_identifier(target: ParsedTarget, locator: str) -> str | None:
    """`device` / `service` / `repository`: exact canonical equality only."""
    if target.kind in (TARGET_IP, TARGET_NETWORK):
        return None
    return MATCH_EXACT_IDENTIFIER if target.normalized == locator else None


_MATCHERS = {
    "host": _match_host,
    "network": _match_network,
    "domain": _match_domain,
    "device": _match_identifier,
    "service": _match_identifier,
    "repository": _match_identifier,
}


def match_asset(target: ParsedTarget, asset: AuthorizedAsset) -> str | None:
    """Return the match method name, or None. Pure: no DB, no DNS, no clock."""
    matcher = _MATCHERS.get(asset.kind)
    if matcher is None:
        return None
    return matcher(target, asset.locator)


def find_matches(
    target: ParsedTarget, assets: list[AuthorizedAsset]
) -> list[tuple[AuthorizedAsset, str]]:
    """All assets whose recorded locator covers this target (status-agnostic)."""
    return [
        (asset, method)
        for asset in assets
        if (method := match_asset(target, asset)) is not None
    ]


# --------------------------------------------------------------- the guard


class ScopeGuard:
    """Answers the enforcement question and audits every answer."""

    def __init__(self, registry: AuthorizedAssetRegistry) -> None:
        self._registry = registry

    def evaluate(
        self,
        session: Session,
        *,
        target: str,
        testing_class: str,
        disruption: str = "none",
        purpose: str = "assessment",
        trace_id: str | None = None,
        now: datetime | None = None,
        audit: bool = True,
    ) -> ScopeDecision:
        """Evaluate one request and (by default) write the authorization event.

        `purpose` selects which event pair is written: assessment_* or
        remediation_*. `audit=False` exists only for read-only previews (the
        route that shows the owner why something would be refused); the
        execution paths never pass it.
        """
        moment = as_aware(now) or utcnow()
        decision = self._decide(session, target, testing_class, disruption, moment, trace_id)
        if audit:
            self._audit(session, decision, purpose=purpose, trace_id=trace_id)
        return decision

    # ------------------------------------------------------------- internals

    def _decide(
        self,
        session: Session,
        raw_target: str,
        testing_class: str,
        disruption: str,
        moment: datetime,
        trace_id: str | None,
    ) -> ScopeDecision:
        requested = (raw_target or "").strip()[:MAX_TARGET_LENGTH]

        # --- 1. testing class must be a known, implemented class -------------
        if testing_class not in TESTING_CLASSES:
            return _refuse(
                REASON_UNKNOWN_TESTING_CLASS,
                requested,
                "unknown",
                testing_class,
                disruption,
                detail={"known_classes": list(TESTING_CLASSES)},
            )
        if testing_class not in IMPLEMENTED_TESTING_CLASSES:
            return _refuse(
                REASON_TESTING_CLASS_NOT_IMPLEMENTED,
                requested,
                "unknown",
                testing_class,
                disruption,
                detail={"implemented": list(IMPLEMENTED_TESTING_CLASSES)},
            )

        # --- 2. disruption level must be on the ladder -----------------------
        if disruption not in DISRUPTION_LEVELS:
            return _refuse(
                REASON_INVALID_DISRUPTION,
                requested,
                "unknown",
                testing_class,
                str(disruption)[:32],
                detail={"levels": list(DISRUPTION_LEVELS)},
            )

        # --- 3. the target must classify into exactly one kind ---------------
        parsed = classify_target(requested)
        if parsed is None:
            return _refuse(
                REASON_INVALID_TARGET if requested else REASON_UNRESOLVABLE_TARGET,
                requested,
                "unknown",
                testing_class,
                disruption,
                detail={"charset": "A-Za-z0-9._:/+- ; no whitespace, '@', '\\' or '..'"},
            )

        # --- 4. match against every enrolled asset ---------------------------
        matches = find_matches(parsed, self._registry.all_assets(session))
        if not matches:
            return _refuse(
                REASON_NO_AUTHORIZED_ASSET,
                parsed.normalized,
                parsed.kind,
                testing_class,
                disruption,
                detail={"enrollment_required": True},
            )
        distinct = {asset.id for asset, _ in matches}
        if len(distinct) > 1:
            # Refuse, and hand the owner enough to FIX the registry: which
            # authorizations overlap, how each matched, and what state each is
            # in. Picking one of them — even the stricter one — would mean the
            # agent decides which of the owner's authorizations applies.
            return _refuse(
                REASON_AMBIGUOUS_TARGET,
                parsed.normalized,
                parsed.kind,
                testing_class,
                disruption,
                detail={
                    "asset_refs": sorted(a.asset_ref for a, _ in matches),
                    "matches": sorted(
                        (
                            {
                                "asset_ref": a.asset_ref,
                                "kind": a.kind,
                                "locator": a.locator,
                                "status": a.status,
                                "matched_by": method,
                            }
                            for a, method in matches
                        ),
                        key=lambda m: m["asset_ref"],
                    ),
                },
            )

        asset, matched_by = matches[0]
        base = {
            "target": parsed.normalized,
            "target_kind": parsed.kind,
            "testing_class": testing_class,
            "disruption": disruption,
            "asset_id": asset.id,
            "asset_ref": asset.asset_ref,
            "asset_kind": asset.kind,
            "matched_by": matched_by,
        }

        # --- 5. validity window (re-derived, never trusted from status) ------
        starts = as_aware(asset.valid_from)
        ends = as_aware(asset.valid_until)
        if ends is not None and ends <= moment:
            # A stale `active` row is corrected here, so the next decision and
            # every listing agree with what just happened.
            self._registry.mark_expired(session, asset, trace_id=trace_id)
            return ScopeDecision(
                allowed=False,
                reason=REASON_AUTHORIZATION_EXPIRED,
                message=_MESSAGES[REASON_AUTHORIZATION_EXPIRED],
                detail={"valid_until": ends.isoformat()},
                **base,
            )
        if starts is not None and starts > moment:
            return ScopeDecision(
                allowed=False,
                reason=REASON_NOT_YET_VALID,
                message=_MESSAGES[REASON_NOT_YET_VALID],
                detail={"valid_from": starts.isoformat()},
                **base,
            )

        # --- 6. status ------------------------------------------------------
        if asset.status != ASSET_STATUS_ACTIVE:
            reason = {
                ASSET_STATUS_SUSPENDED: REASON_ASSET_SUSPENDED,
                ASSET_STATUS_REVOKED: REASON_ASSET_REVOKED,
                ASSET_STATUS_EXPIRED: REASON_AUTHORIZATION_EXPIRED,
            }.get(asset.status, REASON_NO_AUTHORIZED_ASSET)
            return ScopeDecision(
                allowed=False,
                reason=reason,
                message=_MESSAGES[reason],
                detail={"status": asset.status},
                **base,
            )

        # --- 7. the class must be allowed FOR THIS ASSET ---------------------
        allowed_testing = dict(asset.allowed_testing_json or {})
        if allowed_testing.get(testing_class) is not True:
            return ScopeDecision(
                allowed=False,
                reason=REASON_TESTING_CLASS_NOT_ALLOWED,
                message=_MESSAGES[REASON_TESTING_CLASS_NOT_ALLOWED],
                detail={
                    "allowed_testing": sorted(k for k, v in allowed_testing.items() if v is True)
                },
                **base,
            )

        # --- 8. stored constraints ------------------------------------------
        constraints = dict(asset.constraints_json or {})
        max_disruption = constraints.get("max_disruption", "none")
        if max_disruption not in DISRUPTION_ORDER:
            # A constraint we cannot interpret is a refusal, not a default.
            return ScopeDecision(
                allowed=False,
                reason=REASON_DISRUPTION_EXCEEDS_CONSTRAINT,
                message=_MESSAGES[REASON_DISRUPTION_EXCEEDS_CONSTRAINT],
                detail={"max_disruption": str(max_disruption)[:32], "uninterpretable": True},
                **base,
            )
        if DISRUPTION_ORDER[disruption] > DISRUPTION_ORDER[max_disruption]:
            return ScopeDecision(
                allowed=False,
                reason=REASON_DISRUPTION_EXCEEDS_CONSTRAINT,
                message=_MESSAGES[REASON_DISRUPTION_EXCEEDS_CONSTRAINT],
                detail={"max_disruption": max_disruption, "requested": disruption},
                **base,
            )

        return ScopeDecision(
            allowed=True,
            reason=REASON_IN_SCOPE,
            message=_MESSAGES[REASON_IN_SCOPE],
            detail={
                "max_disruption": max_disruption,
                "config_roots": list(constraints.get("config_roots") or []),
                "environment": asset.environment,
            },
            **base,
        )

    def _audit(
        self, session: Session, decision: ScopeDecision, *, purpose: str, trace_id: str | None
    ) -> None:
        if purpose == "remediation":
            action = (
                EVENT_REMEDIATION_AUTHORIZED if decision.allowed else EVENT_REMEDIATION_REFUSED
            )
        else:
            action = EVENT_ASSESSMENT_AUTHORIZED if decision.allowed else EVENT_ASSESSMENT_REFUSED
        # When the decision resolved to a specific asset — including a REFUSED
        # one — link the event to it, so the asset's audit history shows what
        # was attempted against it and not merely what succeeded.
        asset = (
            session.get(AuthorizedAsset, decision.asset_id)
            if decision.asset_id is not None
            else None
        )
        self._registry.record_event(
            session,
            action=action,
            allowed=decision.allowed,
            reason=decision.reason,
            asset=asset,
            asset_ref=decision.asset_ref,
            requested_target=decision.target or None,
            testing_class=decision.testing_class,
            detail={
                "target_kind": decision.target_kind,
                "matched_by": decision.matched_by,
                "disruption": decision.disruption,
                "message": decision.message,
                **decision.detail,
            },
            trace_id=trace_id,
        )


def _refuse(
    reason: str,
    target: str,
    target_kind: str,
    testing_class: str,
    disruption: str,
    *,
    detail: dict[str, Any] | None = None,
) -> ScopeDecision:
    return ScopeDecision(
        allowed=False,
        reason=reason,
        message=_MESSAGES.get(reason, "Reddedildi."),
        target=target,
        target_kind=target_kind,
        testing_class=testing_class,
        disruption=disruption,
        detail=detail or {},
    )


__all__ = [
    "MATCH_CIDR_MEMBERSHIP",
    "MATCH_CIDR_SUBNET",
    "MATCH_DOMAIN_SUFFIX",
    "MATCH_EXACT_ADDRESS",
    "MATCH_EXACT_HOSTNAME",
    "MATCH_EXACT_IDENTIFIER",
    "REASON_AMBIGUOUS_TARGET",
    "REASON_ASSET_REVOKED",
    "REASON_ASSET_SUSPENDED",
    "REASON_AUTHORIZATION_EXPIRED",
    "REASON_DISRUPTION_EXCEEDS_CONSTRAINT",
    "REASON_IN_SCOPE",
    "REASON_INVALID_TARGET",
    "REASON_NO_AUTHORIZED_ASSET",
    "REASON_NOT_YET_VALID",
    "REASON_TESTING_CLASS_NOT_ALLOWED",
    "REASON_TESTING_CLASS_NOT_IMPLEMENTED",
    "REASON_UNKNOWN_TESTING_CLASS",
    "REASON_UNRESOLVABLE_TARGET",
    "TARGET_HOSTNAME",
    "TARGET_IP",
    "TARGET_NETWORK",
    "TARGET_OPAQUE",
    "ParsedTarget",
    "ScopeDecision",
    "ScopeGuard",
    "classify_target",
    "find_matches",
    "match_asset",
]
