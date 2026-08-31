"""Machine-readable capability registry (EVOLUTION_ENGINE_SPEC §2).

The registry is the ONLY thing dispatch consults. Its two hard rules are
enforced in code, never by convention:

1. ``resolve(capability_id)`` returns a capability **only** when its status is
   ``production`` AND ``current_skill_version_id`` points at a skill version
   whose status is ``registered``. Anything else — a proposed capability, a
   production capability whose current version was later rejected, a registered
   version on a deprecated capability — resolves to ``None`` and dispatch fails
   with ``capability_missing``.
2. ``register(...)`` refuses unless the skill version has reached ``evaluated``
   with PASSING gates: ``evaluation_json["passed"] is True`` (deterministic
   evidence produced by evaluation.py, i.e. the generated tests and evals really
   ran) AND ``review_json["approved"] is True`` (the INDEPENDENT reviewer). A
   reviewer verdict alone is never sufficient and neither is a green test run
   alone — §9 forbids promotion on "the reviewer liked it".

Manifest validation covers the §2 fields: id, version, status, inputs, outputs,
permissions, dependencies, owner_scope, health_metrics. Every value that can
later reach generated source goes through app.evolution.tokens.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.models import (
    CAPABILITY_STATUSES,
    SKILL_VERSION_STATUSES,
    Capability,
    SkillVersion,
)
from app.evolution.tokens import (
    require_capability_id,
    require_identifier,
    require_owner_scope,
    require_slug_list,
    require_summary,
    require_version,
)
from app.logging import get_logger

logger = get_logger("app.evolution.registry")

SessionFactory = Callable[[], AbstractContextManager[Session]]

# EVOLUTION_ENGINE_SPEC §2 example manifest keys.
REQUIRED_MANIFEST_KEYS = (
    "id",
    "version",
    "status",
    "inputs",
    "outputs",
    "permissions",
    "dependencies",
    "owner_scope",
    "health_metrics",
)
# Keys the engine adds on top of §2 so a registered capability is executable and
# auditable. Optional on input; filled in by the pipeline.
#   configurable_for / extension_points make steps 3 and 4 of the gap-detection
#   decision tree (configure / safely extend an existing skill) real, machine
#   checkable questions instead of prose.
OPTIONAL_MANIFEST_KEYS = (
    "summary",
    "skill",
    "entrypoint",
    "source_ref",
    "generated_by",
    "configurable_for",
    "extension_points",
)

# A skill version must be here before it can be registered.
REGISTRABLE_FROM_STATUS = "evaluated"

# The single dispatchable entrypoint name a generated skill may declare.
SUPPORTED_ENTRYPOINT = "run"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate a §2 capability manifest and return a normalized copy.

    This is a CHOKE POINT: the returned manifest's scalar values are all strict
    tokens, so the pipeline may safely write them into a generated manifest.yaml
    and README without further escaping (they are re-validated at the splice
    points in skills.py regardless).
    """
    if not isinstance(manifest, dict):
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, "capability manifest must be an object"
        )
    missing = [key for key in REQUIRED_MANIFEST_KEYS if key not in manifest]
    if missing:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"capability manifest is missing required keys: {missing}",
            details={"missing": missing, "required": list(REQUIRED_MANIFEST_KEYS)},
        )
    unknown = [
        key
        for key in manifest
        if key not in REQUIRED_MANIFEST_KEYS and key not in OPTIONAL_MANIFEST_KEYS
    ]
    if unknown:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"capability manifest carries unknown keys: {sorted(unknown)}",
            details={"unknown": sorted(unknown)},
        )
    status = manifest["status"]
    if status not in CAPABILITY_STATUSES:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"invalid capability status: {status!r}",
            details={"allowed": list(CAPABILITY_STATUSES)},
        )
    normalized: dict[str, Any] = {
        "id": require_capability_id(manifest["id"], field="manifest.id"),
        "version": require_version(manifest["version"], field="manifest.version"),
        "status": status,
        "inputs": require_slug_list(manifest["inputs"], field="manifest.inputs"),
        "outputs": require_slug_list(manifest["outputs"], field="manifest.outputs"),
        "permissions": require_slug_list(manifest["permissions"], field="manifest.permissions"),
        "dependencies": require_slug_list(
            manifest["dependencies"], field="manifest.dependencies"
        ),
        "owner_scope": require_owner_scope(manifest["owner_scope"]),
        "health_metrics": require_slug_list(
            manifest["health_metrics"], field="manifest.health_metrics"
        ),
    }
    if "summary" in manifest:
        normalized["summary"] = require_summary(manifest["summary"])
    if "skill" in manifest:
        normalized["skill"] = require_identifier(manifest["skill"], field="manifest.skill")
    if "entrypoint" in manifest:
        # The runtime contract is a single fixed entrypoint name; an arbitrary
        # identifier here would mean the manifest could point dispatch at any
        # function in the generated module.
        if manifest["entrypoint"] != SUPPORTED_ENTRYPOINT:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"manifest.entrypoint must be {SUPPORTED_ENTRYPOINT!r}",
            )
        normalized["entrypoint"] = SUPPORTED_ENTRYPOINT
    for list_key in ("configurable_for", "extension_points"):
        if list_key in manifest:
            normalized[list_key] = require_slug_list(
                manifest[list_key], field=f"manifest.{list_key}"
            )
    for passthrough in ("source_ref", "generated_by"):
        if passthrough in manifest:
            value = manifest[passthrough]
            if not isinstance(value, str) or len(value) > 512:
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR,
                    f"manifest.{passthrough} must be a string of at most 512 chars",
                )
            normalized[passthrough] = value
    return normalized


def gates_passed(evaluation: Any, review: Any) -> tuple[bool, list[str]]:
    """Deterministic evidence check used by ``register`` (and by tests).

    Returns (ok, reasons-it-failed). Both halves are required: the evals/tests
    actually ran and passed, AND the independent reviewer approved.
    """
    reasons: list[str] = []
    if not isinstance(evaluation, dict) or evaluation.get("passed") is not True:
        reasons.append("evaluation_gates_not_passed")
    elif not isinstance(evaluation.get("score"), dict):
        reasons.append("evaluation_missing_release_score")
    if not isinstance(review, dict) or review.get("approved") is not True:
        reasons.append("independent_review_not_approved")
    return (not reasons, reasons)


class CapabilityRegistry:
    """CRUD + the two gating rules. All DB work is synchronous (routes wrap it
    in asyncio.to_thread like every other module)."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    # ----------------------------------------------------------- capabilities

    def upsert_capability(
        self,
        capability_id: str,
        manifest: dict[str, Any],
        *,
        status: str = "proposed",
    ) -> dict[str, Any]:
        """Record/refresh a capability WITHOUT promoting it to production.

        Registration (the promotion path) is a separate, gated method.
        """
        capability_id = require_capability_id(capability_id)
        normalized = validate_manifest(manifest)
        if normalized["id"] != capability_id:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                "manifest.id does not match the capability id being registered",
                details={"manifest_id": normalized["id"], "capability_id": capability_id},
            )
        if status not in CAPABILITY_STATUSES:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, f"invalid capability status: {status!r}"
            )
        if status == "production":
            # Production is reachable only through register(), which checks the
            # skill-version gates. Otherwise this method would be a bypass.
            raise EvolutionError(
                EvolutionErrorClass.REGISTRATION_REFUSED,
                "a capability can only become 'production' through register()",
            )
        normalized["status"] = status
        with self._session_factory() as session:
            row = self._get_row(session, capability_id)
            if row is None:
                row = Capability(
                    capability_id=capability_id,
                    version=normalized["version"],
                    status=status,
                    manifest_json=normalized,
                )
                session.add(row)
            else:
                row.version = normalized["version"]
                row.status = status
                row.manifest_json = normalized
                row.updated_at = _utcnow()
            session.commit()
            logger.info("capability_upserted", capability_id=capability_id, status=status)
            return _capability_dict(row)

    def get_capability(self, capability_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = self._get_row(session, capability_id)
            return _capability_dict(row) if row is not None else None

    def list_capabilities(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = select(Capability).order_by(Capability.capability_id).limit(limit)
        if status:
            query = query.where(Capability.status == status)
        with self._session_factory() as session:
            return [_capability_dict(row) for row in session.execute(query).scalars()]

    def resolve(self, capability_id: str) -> dict[str, Any] | None:
        """Dispatch-facing lookup — the ONLY sanctioned way to reach a skill.

        Returns the capability dict (with ``skill_version``) when the capability
        is ``production`` and its current skill version is ``registered``;
        otherwise ``None``. A rejected/superseded current version, or a
        non-production status, means the capability is not dispatchable — which
        is exactly what keeps a rejected candidate away from production.
        """
        if not isinstance(capability_id, str):
            return None
        with self._session_factory() as session:
            row = self._get_row(session, capability_id)
            if row is None or row.status != "production":
                return None
            if row.current_skill_version_id is None:
                return None
            version = session.get(SkillVersion, row.current_skill_version_id)
            if version is None or version.status != "registered":
                return None
            resolved = _capability_dict(row)
            resolved["skill_version"] = _skill_version_dict(version)
            return resolved

    def set_capability_status(self, capability_id: str, status: str) -> dict[str, Any]:
        if status not in CAPABILITY_STATUSES:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, f"invalid capability status: {status!r}"
            )
        if status == "production":
            raise EvolutionError(
                EvolutionErrorClass.REGISTRATION_REFUSED,
                "a capability can only become 'production' through register()",
            )
        with self._session_factory() as session:
            row = self._require_row(session, capability_id)
            row.status = status
            row.updated_at = _utcnow()
            session.commit()
            return _capability_dict(row)

    # --------------------------------------------------------- skill versions

    def create_skill_version(
        self,
        capability_id: str,
        version: str,
        *,
        source_ref: str | None = None,
        git_commit: str | None = None,
        manifest_digest: str | None = None,
        status: str = "draft",
    ) -> dict[str, Any]:
        capability_id = require_capability_id(capability_id)
        version = require_version(version)
        if status not in SKILL_VERSION_STATUSES:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, f"invalid skill version status: {status!r}"
            )
        if status in ("registered", "evaluated"):
            raise EvolutionError(
                EvolutionErrorClass.REGISTRATION_REFUSED,
                "a skill version cannot be created directly in an evaluated/registered state",
            )
        with self._session_factory() as session:
            existing = session.execute(
                select(SkillVersion).where(
                    SkillVersion.capability_id == capability_id,
                    SkillVersion.version == version,
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR,
                    f"skill version {capability_id}/{version} already exists "
                    "(skill versions are immutable history)",
                )
            row = SkillVersion(
                capability_id=capability_id,
                version=version,
                status=status,
                source_ref=source_ref,
                git_commit=git_commit,
                manifest_digest=manifest_digest,
            )
            session.add(row)
            session.commit()
            logger.info(
                "skill_version_created", capability_id=capability_id, version=version
            )
            return _skill_version_dict(row)

    def get_skill_version(self, skill_version_id: uuid.UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(SkillVersion, skill_version_id)
            if row is None:
                raise EvolutionError(
                    EvolutionErrorClass.NOT_FOUND,
                    f"skill version {skill_version_id} not found",
                )
            return _skill_version_dict(row)

    def list_skill_versions(
        self, *, capability_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = select(SkillVersion).order_by(SkillVersion.created_at.desc()).limit(limit)
        if capability_id:
            query = query.where(SkillVersion.capability_id == capability_id)
        if status:
            query = query.where(SkillVersion.status == status)
        with self._session_factory() as session:
            return [_skill_version_dict(row) for row in session.execute(query).scalars()]

    def record_build(
        self, skill_version_id: uuid.UUID, *, source_ref: str, manifest_digest: str
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_version(session, skill_version_id)
            row.status = "built"
            row.source_ref = source_ref[:512]
            row.manifest_digest = manifest_digest[:128]
            session.commit()
            return _skill_version_dict(row)

    def record_source_ref(
        self, skill_version_id: uuid.UUID, *, source_ref: str, manifest_digest: str
    ) -> dict[str, Any]:
        """Update provenance WITHOUT touching the gate status.

        Used when a passing candidate is published from the sandbox workspace to
        the skills root: the version keeps its ``evaluated`` state (moving it
        back to ``built`` here would silently defeat registration).
        """
        with self._session_factory() as session:
            row = self._require_version(session, skill_version_id)
            row.source_ref = source_ref[:512]
            row.manifest_digest = manifest_digest[:128]
            session.commit()
            return _skill_version_dict(row)

    def record_evaluation(
        self, skill_version_id: uuid.UUID, evaluation: dict[str, Any]
    ) -> dict[str, Any]:
        """Store the deterministic evaluation evidence.

        The version reaches ``evaluated`` only when the evaluation itself
        reports ``passed``; a failing evaluation leaves it at ``tested`` so
        register() can never find it in a registrable state.
        """
        with self._session_factory() as session:
            row = self._require_version(session, skill_version_id)
            row.evaluation_json = evaluation
            row.status = "evaluated" if evaluation.get("passed") is True else "tested"
            session.commit()
            logger.info(
                "skill_version_evaluated",
                skill_version_id=str(skill_version_id),
                passed=bool(evaluation.get("passed")),
            )
            return _skill_version_dict(row)

    def record_review(
        self, skill_version_id: uuid.UUID, review: dict[str, Any]
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = self._require_version(session, skill_version_id)
            row.review_json = review
            session.commit()
            return _skill_version_dict(row)

    def reject_skill_version(
        self, skill_version_id: uuid.UUID, reason: str
    ) -> dict[str, Any]:
        """Terminal rejection. Production is untouched by construction: a
        rejected version is never pointed at by a production capability, and
        ``resolve`` re-checks the pointed-at version's status on every call."""
        with self._session_factory() as session:
            row = self._require_version(session, skill_version_id)
            if row.status == "registered":
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR,
                    "a registered skill version cannot be rejected; supersede it instead",
                )
            row.status = "rejected"
            row.rejected_reason = reason[:512]
            session.commit()
            logger.info(
                "skill_version_rejected",
                skill_version_id=str(skill_version_id),
                reason=reason[:200],
            )
            return _skill_version_dict(row)

    # ------------------------------------------------------------- the gate

    def register(
        self,
        capability_id: str,
        skill_version_id: uuid.UUID,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Promote a capability to production. THE gate.

        Refuses unless the skill version belongs to this capability, is in
        ``evaluated`` state, and carries passing evaluation gates + an approving
        independent review.
        """
        capability_id = require_capability_id(capability_id)
        normalized = validate_manifest(manifest)
        if normalized["id"] != capability_id:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                "manifest.id does not match the capability id being registered",
            )
        with self._session_factory() as session:
            version = self._require_version(session, skill_version_id)
            if version.capability_id != capability_id:
                raise EvolutionError(
                    EvolutionErrorClass.REGISTRATION_REFUSED,
                    "skill version belongs to a different capability",
                    details={"skill_capability_id": version.capability_id},
                )
            if version.status != REGISTRABLE_FROM_STATUS:
                raise EvolutionError(
                    EvolutionErrorClass.REGISTRATION_REFUSED,
                    f"skill version is {version.status!r}; registration requires "
                    f"{REGISTRABLE_FROM_STATUS!r} with passing gates",
                    details={"status": version.status},
                )
            ok, reasons = gates_passed(version.evaluation_json, version.review_json)
            if not ok:
                raise EvolutionError(
                    EvolutionErrorClass.REGISTRATION_REFUSED,
                    "skill version has not passed the release gates",
                    details={"reasons": reasons},
                )
            if normalized["version"] != version.version:
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR,
                    "manifest.version does not match the skill version being registered",
                    details={
                        "manifest_version": normalized["version"],
                        "skill_version": version.version,
                    },
                )
            normalized["status"] = "production"
            now = _utcnow()
            row = self._get_row(session, capability_id)
            previous_version_id = row.current_skill_version_id if row is not None else None
            if row is None:
                row = Capability(
                    capability_id=capability_id,
                    version=version.version,
                    status="production",
                    manifest_json=normalized,
                    current_skill_version_id=version.id,
                )
                session.add(row)
            else:
                row.version = version.version
                row.status = "production"
                row.manifest_json = normalized
                row.current_skill_version_id = version.id
                row.updated_at = now
            version.status = "registered"
            version.registered_at = now
            if previous_version_id is not None and previous_version_id != version.id:
                previous = session.get(SkillVersion, previous_version_id)
                if previous is not None and previous.status == "registered":
                    previous.status = "superseded"
            session.commit()
            logger.info(
                "capability_registered",
                capability_id=capability_id,
                version=version.version,
                skill_version_id=str(version.id),
            )
            resolved = _capability_dict(row)
            resolved["skill_version"] = _skill_version_dict(version)
            return resolved

    # ------------------------------------------------------------------ utils

    def _get_row(self, session: Session, capability_id: str) -> Capability | None:
        return session.execute(
            select(Capability).where(Capability.capability_id == capability_id)
        ).scalar_one_or_none()

    def _require_row(self, session: Session, capability_id: str) -> Capability:
        row = self._get_row(session, capability_id)
        if row is None:
            raise EvolutionError(
                EvolutionErrorClass.NOT_FOUND, f"capability {capability_id!r} not found"
            )
        return row

    def _require_version(
        self, session: Session, skill_version_id: uuid.UUID
    ) -> SkillVersion:
        row = session.get(SkillVersion, skill_version_id)
        if row is None:
            raise EvolutionError(
                EvolutionErrorClass.NOT_FOUND, f"skill version {skill_version_id} not found"
            )
        return row


def _capability_dict(row: Capability) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "capability_id": row.capability_id,
        "version": row.version,
        "status": row.status,
        "manifest": row.manifest_json,
        "current_skill_version_id": (
            str(row.current_skill_version_id) if row.current_skill_version_id else None
        ),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _skill_version_dict(row: SkillVersion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "capability_id": row.capability_id,
        "version": row.version,
        "status": row.status,
        "source_ref": row.source_ref,
        "git_commit": row.git_commit,
        "manifest_digest": row.manifest_digest,
        "evaluation": row.evaluation_json,
        "review": row.review_json,
        "rejected_reason": row.rejected_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "registered_at": row.registered_at.isoformat() if row.registered_at else None,
    }


__all__ = [
    "OPTIONAL_MANIFEST_KEYS",
    "REGISTRABLE_FROM_STATUS",
    "REQUIRED_MANIFEST_KEYS",
    "SUPPORTED_ENTRYPOINT",
    "CapabilityRegistry",
    "gates_passed",
    "validate_manifest",
]
