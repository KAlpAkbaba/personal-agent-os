"""The real deployment ledger as the source of "an owner-approved release exists".

``EvolutionService`` refuses ``DEPLOYING`` (and so ``LIVE``) unless its release-evidence
provider names a release for the opportunity. Until M18.4's final gap closure the
production build carried the null provider ("the integrator injects a real one"), so the
first real lifecycle on production stopped at ``qualifying`` with "no release evidence was
available" (ADR-0081 addendum 3).

This provider reads what already exists: the blue/green release harness and driver record
``deployment.cloud_core.released`` ledger rows with the released sha as ``result`` (under
the owner's session, after the switch was verified through the edge). An opportunity whose
``candidate_ref`` names an image ``pagentos/cloud-core:<sha>`` (or the bare sha) is
release-ready exactly when such a row exists for that sha. The ref returned is the ledger
row's own identity, so LIVE always points at a durable deployment record - never at a
caller-supplied string.

Evolution may read the ledger (the supervisor already does); it may not import the
release or deployment modules, which is why this lives here and not beside the executor.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED

#: The image repository the release script tags: pagentos/cloud-core:<sha>.
IMAGE_PREFIX = "pagentos/cloud-core:"
MIN_SHA_CHARS = 7


def candidate_sha(candidate_ref: Any) -> str | None:
    """The sha an opportunity's ``candidate_ref`` names, or None when it names none."""
    if not isinstance(candidate_ref, str):
        return None
    ref = candidate_ref.strip()
    if ref.startswith(IMAGE_PREFIX):
        ref = ref[len(IMAGE_PREFIX) :]
    ref = ref.strip()
    if len(ref) < MIN_SHA_CHARS or len(ref) > 40:
        return None
    if any(c not in "0123456789abcdef" for c in ref.lower()):
        return None
    return ref.lower()


class LedgerReleaseEvidenceProvider:
    """``ReleaseEvidenceProvider`` backed by ``deployment.cloud_core.released`` rows."""

    name = "ledger"

    def __init__(self, session_scope: Callable[[], AbstractContextManager[Session]]) -> None:
        self._session_scope = session_scope

    def approved_release(self, opportunity: Mapping[str, Any]) -> str | None:
        sha = candidate_sha(opportunity.get("candidate_ref"))
        if sha is None:
            return None
        with self._session_scope() as db:
            statement = (
                select(ActivityEventRow.event_id)
                .where(ActivityEventRow.event_type == EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED)
                .where(ActivityEventRow.result.is_not(None))
                .where(ActivityEventRow.result.like(f"{sha}%"))
                .order_by(ActivityEventRow.occurred_at.desc())
                .limit(1)
            )
            event_id = db.execute(statement).scalar_one_or_none()
        if event_id is None:
            return None
        return f"ledger_event:{event_id}"


__all__ = ["IMAGE_PREFIX", "LedgerReleaseEvidenceProvider", "candidate_sha"]
