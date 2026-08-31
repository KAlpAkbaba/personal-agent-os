"""Remediation, bounded by the asset's STORED constraints (M8).

Design commitments, in priority order:

1. **Propose by default.** `mode="propose"` is the default and mutates
   nothing. Fixing something on an owner's machine is an action the registry
   has to authorize, not a side effect of having found a problem.

2. **The constraint check gates every apply.** Before a byte is written, the
   request goes back through `scope.py` as a `remediation` request carrying
   the fix's own disruption level. An asset recorded with
   `max_disruption: low` therefore refuses a `medium`-disruption fix such as
   `PermitRootLogin no` — the same enforcement point, the same audit trail,
   no second policy engine that could disagree with the first. A fix on an
   asset whose `allowed_testing.remediation` is not true is refused for the
   same reason even when the disruption is trivial.

3. **Reversible or dry-runnable — in fact both.** `mode="dry_run"` computes
   the exact patched line and writes nothing. `mode="apply"` writes a backup
   beside the file first, and `mode="revert"` restores it. A fix with no safe
   automated form (rotate a leaked credential, restrict CORS origins, pick TLS
   versions) is `apply_supported: false` and is refused rather than guessed
   at; it carries `manual_steps` instead.

4. **The patch is re-derived from the file on disk**, never replayed from the
   stored evidence. Between the assessment and the remediation the file may
   have changed; applying a stale line would clobber an edit the owner made.
   If the finding no longer reproduces, the answer is "already resolved", not
   a write.

5. **The path is registry-bounded** exactly like collection: it must resolve
   inside a `constraints.config_roots` entry, so a crafted `file` value in a
   finding cannot walk out of the authorized tree.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.security.assessments import authorized_config_root, finding_to_dict, utcnow
from app.security.checks import CHECKS_BY_ID
from app.security.errors import SecurityError, SecurityErrorClass
from app.security.models import (
    FINDING_STATUS_OPEN,
    FINDING_STATUS_REMEDIATED,
    TESTING_CLASS_REMEDIATION,
    AuthorizedAsset,
    SecurityAssessment,
    SecurityFinding,
)
from app.security.redaction import redact_text
from app.security.registry import AuthorizedAssetRegistry
from app.security.scope import (
    REASON_DISRUPTION_EXCEEDS_CONSTRAINT,
    ScopeGuard,
)

logger = get_logger("app.security.remediation")

SessionFactory = Callable[[], AbstractContextManager[Session]]

MODE_PROPOSE = "propose"
MODE_DRY_RUN = "dry_run"
MODE_APPLY = "apply"
MODE_REVERT = "revert"
MODES = (MODE_PROPOSE, MODE_DRY_RUN, MODE_APPLY, MODE_REVERT)

BACKUP_SUFFIX = ".pagentos-bak"
MAX_FILE_BYTES = 256 * 1024


def _backup_path(path: Path, finding_id: uuid.UUID) -> Path:
    """Backups are per-finding so two fixes on one file cannot clobber each
    other's restore point."""
    return path.with_name(f"{path.name}{BACKUP_SUFFIX}-{finding_id.hex[:8]}")


class RemediationService:
    def __init__(
        self,
        session_factory: SessionFactory,
        registry: AuthorizedAssetRegistry,
        guard: ScopeGuard,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._guard = guard

    # ------------------------------------------------------------- public

    def remediate(
        self,
        finding_id: uuid.UUID,
        *,
        mode: str = MODE_PROPOSE,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if mode not in MODES:
            raise SecurityError(
                SecurityErrorClass.VALIDATION_ERROR,
                f"mode must be one of {MODES}",
                {"mode": str(mode)[:32]},
            )
        with self._session_factory() as session:
            finding, assessment, asset = self._load(session, finding_id)
            remediation = dict(finding.remediation_json or {})
            disruption = str(remediation.get("disruption", "high"))

            if mode == MODE_PROPOSE:
                # Read-only: evaluate the constraint so the owner can see
                # whether it WOULD be appliable, but write no audit row for a
                # question nobody acted on.
                decision = self._guard.evaluate(
                    session,
                    target=assessment.target,
                    testing_class=TESTING_CLASS_REMEDIATION,
                    disruption=disruption,
                    purpose="remediation",
                    trace_id=trace_id,
                    audit=False,
                )
                return {
                    "mode": MODE_PROPOSE,
                    "finding": finding_to_dict(finding),
                    "remediation": remediation,
                    "constraint_check": decision.to_dict(),
                    "appliable": bool(
                        decision.allowed and remediation.get("apply_supported")
                    ),
                    "changed": False,
                }

            # --- everything below can touch the asset: full gated path ----
            decision = self._guard.evaluate(
                session,
                target=assessment.target,
                testing_class=TESTING_CLASS_REMEDIATION,
                disruption=disruption,
                purpose="remediation",
                trace_id=trace_id,
            )
            if not decision.allowed:
                error_class = (
                    SecurityErrorClass.CONSTRAINT_VIOLATION
                    if decision.reason == REASON_DISRUPTION_EXCEEDS_CONSTRAINT
                    else SecurityErrorClass.OUT_OF_SCOPE
                )
                raise SecurityError(
                    error_class,
                    decision.message,
                    {"decision": decision.to_dict(), "requested_disruption": disruption},
                )

            path = self._authorized_file(asset, assessment, remediation)

            if mode == MODE_REVERT:
                return self._revert(session, finding, path)

            if not remediation.get("apply_supported"):
                raise SecurityError(
                    SecurityErrorClass.REMEDIATION_NOT_AUTOMATABLE,
                    "bu bulgu için güvenli otomatik düzeltme yok; manuel adımlar önerilir",
                    {
                        "check_id": remediation.get("check_id"),
                        "manual_steps": remediation.get("manual_steps", []),
                    },
                )

            patch = self._compute_patch(path, remediation)
            if patch is None:
                # The finding no longer reproduces against the file on disk.
                if mode == MODE_APPLY:
                    finding.status = FINDING_STATUS_REMEDIATED
                    finding.resolved_at = utcnow()
                    session.commit()
                return {
                    "mode": mode,
                    "finding": finding_to_dict(finding),
                    "status": "already_resolved",
                    "changed": False,
                    "constraint_check": decision.to_dict(),
                }

            line_index, current_line, fixed_line, new_content = patch
            preview = {
                "mode": mode,
                "file": remediation.get("file"),
                "line": line_index + 1,
                "current_line": redact_text(current_line)[0],
                "proposed_line": redact_text(fixed_line)[0],
                "constraint_check": decision.to_dict(),
                "changed": True,
            }

            if mode == MODE_DRY_RUN:
                preview["applied"] = False
                preview["finding"] = finding_to_dict(finding)
                return preview

            # --- apply: backup first, then write -------------------------
            backup = _backup_path(path, finding.id)
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
            path.write_text(new_content, encoding="utf-8", newline="")
            finding.status = FINDING_STATUS_REMEDIATED
            finding.resolved_at = utcnow()
            session.commit()
            logger.info(
                "security_remediation_applied",
                finding_id=str(finding.id),
                asset_ref=asset.asset_ref,
                check_id=str(remediation.get("check_id")),
            )
            preview["applied"] = True
            preview["reversible"] = True
            preview["backup_file"] = backup.name
            preview["finding"] = finding_to_dict(finding)
            return preview

    # ------------------------------------------------------------ internals

    def _load(
        self, session: Session, finding_id: uuid.UUID
    ) -> tuple[SecurityFinding, SecurityAssessment, AuthorizedAsset]:
        finding = session.get(SecurityFinding, finding_id)
        if finding is None:
            raise SecurityError(
                SecurityErrorClass.NOT_FOUND,
                f"finding {finding_id} not found",
                {"finding_id": str(finding_id)},
            )
        assessment = session.get(SecurityAssessment, finding.assessment_id)
        asset = session.get(AuthorizedAsset, finding.asset_id)
        if assessment is None or asset is None:  # pragma: no cover - FK enforced
            raise SecurityError(
                SecurityErrorClass.INTERNAL_BUG, "finding is missing its assessment or asset"
            )
        return finding, assessment, asset

    def _authorized_file(
        self,
        asset: AuthorizedAsset,
        assessment: SecurityAssessment,
        remediation: dict[str, Any],
    ) -> Path:
        relative = str(remediation.get("file") or "")
        if not relative or ".." in relative or Path(relative).is_absolute():
            raise SecurityError(
                SecurityErrorClass.COLLECTOR_ROOT_VIOLATION,
                "remediation target path is not a plain relative path inside the config root",
                {"file": relative[:256]},
            )
        recorded_root = (assessment.result_json or {}).get("config_root")
        root = authorized_config_root(asset, recorded_root)
        path = (root / relative).resolve()
        if path != root and root not in path.parents:
            raise SecurityError(
                SecurityErrorClass.COLLECTOR_ROOT_VIOLATION,
                "remediation target resolves outside the authorized config root",
                {"file": relative[:256], "config_root": str(root)},
            )
        if not path.is_file() or path.is_symlink():
            raise SecurityError(
                SecurityErrorClass.TARGET_UNAVAILABLE,
                "remediation target file is missing or is a symlink",
                {"file": relative[:256]},
            )
        return path

    def _compute_patch(
        self, path: Path, remediation: dict[str, Any]
    ) -> tuple[int, str, str, str] | None:
        """Re-derive the fix from the file on disk (commitment 4).

        Returns (line_index, current_line, fixed_line, new_file_content) or
        None when the finding no longer reproduces.
        """
        check = CHECKS_BY_ID.get(str(remediation.get("check_id")))
        if check is None or not check.fixes:
            raise SecurityError(
                SecurityErrorClass.REMEDIATION_NOT_AUTOMATABLE,
                "no automated fix is defined for this check",
                {"check_id": str(remediation.get("check_id"))[:64]},
            )
        if path.stat().st_size > MAX_FILE_BYTES:
            raise SecurityError(
                SecurityErrorClass.TARGET_UNAVAILABLE,
                "remediation target file is larger than the collector bound",
                {"max_bytes": MAX_FILE_BYTES},
            )
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines(keepends=True)

        def _content(index: int) -> str:
            return lines[index].rstrip("\r\n")

        # Prefer the recorded line, then fall back to the first line that still
        # matches — the file may have shifted since the assessment ran.
        candidates: list[int] = []
        recorded = remediation.get("line")
        if isinstance(recorded, int) and 1 <= recorded <= len(lines):
            candidates.append(recorded - 1)
        candidates.extend(i for i in range(len(lines)) if i not in candidates)

        for index in candidates:
            current = _content(index)
            match = check.detect.search(current)
            if match is None:
                continue
            if check.predicate is not None and not check.predicate(match):
                continue
            fixed = current
            for pattern, replacement in check.fixes:
                fixed = pattern.sub(replacement, fixed, count=1)
            if fixed == current:
                continue
            ending = lines[index][len(current) :]
            new_lines = list(lines)
            new_lines[index] = fixed + ending
            return index, current, fixed, "".join(new_lines)
        return None

    def _revert(
        self, session: Session, finding: SecurityFinding, path: Path
    ) -> dict[str, Any]:
        backup = _backup_path(path, finding.id)
        if not backup.exists():
            raise SecurityError(
                SecurityErrorClass.NOT_FOUND,
                "no backup exists for this finding; nothing to revert",
                {"finding_id": str(finding.id)},
            )
        path.write_bytes(backup.read_bytes())
        backup.unlink()
        finding.status = FINDING_STATUS_OPEN
        finding.resolved_at = None
        session.commit()
        logger.info("security_remediation_reverted", finding_id=str(finding.id))
        return {
            "mode": MODE_REVERT,
            "reverted": True,
            "changed": True,
            "finding": finding_to_dict(finding),
        }


__all__ = [
    "MODES",
    "MODE_APPLY",
    "MODE_DRY_RUN",
    "MODE_PROPOSE",
    "MODE_REVERT",
    "RemediationService",
]
