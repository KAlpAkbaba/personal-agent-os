"""Release risk tiers (M18 spec §5, ADR-0055 §5) — data, not conditionals.

The owner asked for five tiers, "1 = UI/additive, rising to 5 = identity/root/
secret boundary", and that the tier "must be derived from what the change
actually touches ... never typed in by hand and never guessed." This module is
the single place that derivation happens.

Why a rule table rather than a function full of ``if``
--------------------------------------------------------

An ``if "identity" in path: tier = 5`` scattered through the release path is
exactly the shape of bug this repo keeps finding by hand (ADR-0053, ADR-0055):
someone edits the deployment code six months from now, forgets one of the
branches, and a tier-5 change ships as tier-1 because nobody re-read every
``if``. A table is reviewable in one glance, is what :func:`derive_risk_tier`
actually executes (no parallel prose copy to drift from it), and a new pattern
is one tuple entry, not a new code path.

What "derived, never guessed" means here, concretely
------------------------------------------------------

:func:`derive_risk_tier` is a pure function of the **paths the candidate
touches** (from its diff/manifest) — no caller may hand it a tier directly.
There is no ``RiskAssessment(tier=1)`` constructor a lifecycle caller can use
to shortcut the classification: the dataclass is only ever produced by this
function, from a real path list, and an empty path list is refused rather than
defaulted to the lowest tier (a change that has forgotten to declare what it
touches is not thereby "safe").
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Final

from app.evolution.errors import EvolutionError, EvolutionErrorClass

MAX_CHANGED_PATHS: Final[int] = 2000


class RiskTier(IntEnum):
    """1 = UI/additive ... 5 = identity/root/secret boundary (M18 spec §5)."""

    UI_ADDITIVE = 1
    INTERNAL_LOGIC = 2
    PRODUCTION_BEHAVIOR = 3
    SCHEMA_OR_DEPLOYMENT_MECHANICS = 4
    IDENTITY_ROOT_SECRET_BOUNDARY = 5


#: Tiers at and above this one need an explicit SECOND owner confirmation
#: before OWNER_AUTHORIZED (M18 spec §5: "tiers 3+ need explicit second
#: confirmation").
SECOND_CONFIRMATION_FLOOR: Final[RiskTier] = RiskTier.PRODUCTION_BEHAVIOR

_TIER_LABEL_EN: Final[dict[RiskTier, str]] = {
    RiskTier.UI_ADDITIVE: "UI / additive",
    RiskTier.INTERNAL_LOGIC: "internal lab logic",
    RiskTier.PRODUCTION_BEHAVIOR: "production service behaviour",
    RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS: "schema or deployment mechanics",
    RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY: "identity / root / secret boundary",
}

#: Owner-facing text is Turkish (CLAUDE.md, product invariant).
_TIER_LABEL_TR: Final[dict[RiskTier, str]] = {
    RiskTier.UI_ADDITIVE: "arayüz / eklemeli değişiklik",
    RiskTier.INTERNAL_LOGIC: "iç laboratuvar mantığı",
    RiskTier.PRODUCTION_BEHAVIOR: "üretim servis davranışı",
    RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS: "şema veya dağıtım mekaniği",
    RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY: "kimlik / kök / gizli anahtar sınırı",
}


@dataclass(frozen=True, slots=True)
class RiskRule:
    """One classification rule: a path pattern and the tier it implies."""

    pattern: re.Pattern[str]
    tier: RiskTier
    reason: str


def _rule(pattern: str, tier: RiskTier, reason: str, *, flags: int = 0) -> RiskRule:
    return RiskRule(pattern=re.compile(pattern, flags), tier=tier, reason=reason)


#: Ordered by nothing in particular — every rule is checked against every
#: path, and the overall tier is the MAXIMUM matched tier. Paths use forward
#: slashes and are relative to the repository root (git's own convention),
#: so this table is platform-independent even though the rest of the repo
#: runs on Windows.
RISK_RULES: Final[tuple[RiskRule, ...]] = (
    # ---- tier 5: identity / root / secret boundary -------------------------
    _rule(
        r"^services/api/app/identity/",
        RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY,
        "touches the owner identity/session module (app/identity)",
    ),
    _rule(
        r"^services/api/app/evolution/authority\.py$",
        RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY,
        "touches the Evolution Engine authority kernel itself "
        "(app/evolution/authority.py — the production/lab boundary)",
    ),
    _rule(
        r"secret",
        RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY,
        "path names a secret-handling file",
        flags=re.IGNORECASE,
    ),
    _rule(
        r"credential",
        RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY,
        "path names a credential-handling file",
        flags=re.IGNORECASE,
    ),
    _rule(
        # Written as ``^app/security/`` until 2026-09-11 - a path with no file under it, so
        # the security surface fell to the tier-3 catch-all. Rules name real paths now, and
        # test_every_rule_names_a_path_that_exists holds them to it.
        r"^services/api/app/security/",
        RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY,
        "touches the security policy/remediation surface",
    ),
    _rule(
        # The law that decides every other tier. It sat under the catch-all's evolution/
        # exclusion and so defaulted to tier 2: a candidate could lower the classification
        # in one low-risk step and promote under the lowered one in the next.
        r"^services/api/app/evolution/risk\.py$",
        RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY,
        "changes the risk classification law itself (app/evolution/risk.py)",
    ),
    _rule(
        # The rest of that law: the component -> path map this table reads, and the tier ->
        # promotion-class mapping. The self-development engine's first accepted candidate
        # (Phase 10) changed this file and was classified tier 2, AUTO_CANARY - the same
        # lower-then-promote hole risk.py had.
        r"^services/api/app/evolution/supervisor\.py$",
        RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY,
        "changes the promotion law (app/evolution/supervisor.py: component paths, classes)",
    ),
    _rule(
        # The engine that proposes candidates also judges them (reviewer, budgets, scope). A
        # candidate that loosened those gates would be judged by them the next time.
        r"^services/api/app/selfdev/",
        RiskTier.IDENTITY_ROOT_SECRET_BOUNDARY,
        "changes the self-development engine's own gates (app/selfdev)",
    ),
    # ---- tier 4: schema and deployment mechanics ---------------------------
    _rule(
        r"^services/api/alembic/versions/",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "adds or changes a database migration",
    ),
    _rule(
        r"/models\.py$",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes an ORM schema module",
    ),
    _rule(
        r"^scripts/cloud/",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes the cloud release/deployment scripts",
    ),
    _rule(
        r"^services/recovery-supervisor/",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes the recovery supervisor (the rollback root)",
    ),
    _rule(
        # Written as ``^windows-agent/`` until 2026-09-11; the agent lives under devices/.
        r"^devices/windows-agent/",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes the privileged Windows device service",
    ),
    _rule(
        r"^infra/systemd/",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes a root systemd unit on the production host (the recovery timer)",
    ),
    _rule(
        r"^infra/docker/(docker-compose\.prod\.yml$|edge/)",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes the production container or edge definition",
    ),
    _rule(
        r"^scripts/((install|uninstall)-device-service\.ps1"
        r"|lib/(AgentUpdate|InstallAcl|ServiceInstall)\.ps1)$",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes how the device service is installed, updated or permissioned",
    ),
    _rule(
        r"^packages/schemas/device-protocol\.schema\.json$",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes the device wire protocol both sides must agree on",
    ),
    _rule(
        r"^services/api/app/evolution/(backlog|service)\.py$",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes the release lifecycle law or its production-action guards",
    ),
    _rule(
        r"^services/api/app/release/",
        RiskTier.SCHEMA_OR_DEPLOYMENT_MECHANICS,
        "changes the release-execution path itself (preflight/deploy/rollback)",
    ),
    # ---- tier 3: production service behaviour ------------------------------
    _rule(
        r"^services/api/app/selfhealing/",
        RiskTier.PRODUCTION_BEHAVIOR,
        "changes the self-healing/incident/release-record subsystem",
    ),
    _rule(
        r"^services/api/app/(?!evolution/|release/|identity/).+\.py$",
        RiskTier.PRODUCTION_BEHAVIOR,
        "changes production backend logic",
    ),
    # ---- tier 1: UI / additive ----------------------------------------------
    _rule(
        r"^apps/web/",
        RiskTier.UI_ADDITIVE,
        "changes the web UI only",
    ),
    _rule(
        r"^docs/",
        RiskTier.UI_ADDITIVE,
        "documentation only",
    ),
)

#: Anything not matched by a rule above (e.g. a brand-new lab-only module
#: under app/evolution, a generated skill, a test file) is treated as
#: ordinary internal logic — never as tier 1. Under-classifying an unknown
#: path as "safe UI" would defeat the whole point of deriving the tier from
#: what the change touches.
_DEFAULT_TIER: Final[RiskTier] = RiskTier.INTERNAL_LOGIC


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """The outcome of classifying one candidate's footprint.

    Only ever built by :func:`derive_risk_tier` — there is deliberately no
    public constructor path that lets a caller assert a tier directly.
    """

    tier: RiskTier
    reasons: tuple[str, ...]
    matched_paths: dict[int, tuple[str, ...]] = field(default_factory=dict)
    unmatched_paths: tuple[str, ...] = ()

    @property
    def requires_second_confirmation(self) -> bool:
        return self.tier >= SECOND_CONFIRMATION_FLOOR

    @property
    def label_en(self) -> str:
        return _TIER_LABEL_EN[self.tier]

    @property
    def label_tr(self) -> str:
        return _TIER_LABEL_TR[self.tier]

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": int(self.tier),
            "label_tr": self.label_tr,
            "label_en": self.label_en,
            "requires_second_confirmation": self.requires_second_confirmation,
            "reasons": list(self.reasons),
            "matched_paths": {str(k): list(v) for k, v in self.matched_paths.items()},
            "unmatched_paths": list(self.unmatched_paths),
        }


def _validation(message: str, **details: object) -> EvolutionError:
    return EvolutionError(EvolutionErrorClass.VALIDATION_ERROR, message, details=dict(details))


def derive_risk_tier(changed_paths: Sequence[str]) -> RiskAssessment:
    """Classify a candidate's blast radius from the paths it actually touches.

    Refuses an empty path list: "never guessed" means a candidate that has not
    declared its footprint cannot be assessed at all, and the fail-safe
    direction is refusal, not the lowest tier.
    """
    if changed_paths is None:
        raise _validation("a risk assessment needs the candidate's changed paths")
    paths = [str(p).replace("\\", "/").lstrip("/") for p in changed_paths if str(p).strip()]
    if not paths:
        raise _validation(
            "cannot derive a risk tier from an empty path list; a candidate "
            "that has not declared what it touches is not thereby low-risk"
        )
    if len(paths) > MAX_CHANGED_PATHS:
        raise _validation("too many changed paths for one assessment", maximum=MAX_CHANGED_PATHS)

    matched: dict[int, list[str]] = {}
    reasons: dict[str, str] = {}
    unmatched: list[str] = []
    for path in paths:
        hit = False
        for rule in RISK_RULES:
            if rule.pattern.search(path):
                matched.setdefault(int(rule.tier), []).append(path)
                reasons[rule.reason] = rule.reason
                hit = True
        if not hit:
            matched.setdefault(int(_DEFAULT_TIER), []).append(path)
            unmatched.append(path)

    tier = RiskTier(max(matched))
    if not reasons and tier is _DEFAULT_TIER:
        reasons["no path matched a named risk pattern; classified as internal logic"] = (
            "no path matched a named risk pattern; classified as internal logic"
        )

    return RiskAssessment(
        tier=tier,
        reasons=tuple(sorted(reasons)),
        matched_paths={k: tuple(v) for k, v in matched.items()},
        unmatched_paths=tuple(unmatched),
    )


__all__ = [
    "MAX_CHANGED_PATHS",
    "SECOND_CONFIRMATION_FLOOR",
    "RiskAssessment",
    "RiskRule",
    "RiskTier",
    "derive_risk_tier",
]
