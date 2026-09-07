"""The ledger-backed release evidence (ADR-0081 addendum 3): DEPLOYING and LIVE are
reachable exactly when a real `deployment.cloud_core.released` row names the candidate's
sha - never from a caller-supplied string."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.evolution.release_evidence import LedgerReleaseEvidenceProvider, candidate_sha
from app.ledger.models import ActivityEventRow

SHA = "1072d0d5fc3ded31518c6adc42c4fee9c3cb3dfd"


def _scope():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    class Scope:
        def __enter__(self):
            self.session = factory()
            return self.session

        def __exit__(self, *exc):
            self.session.close()

    return Scope


def _row(scope, *, event_type: str, result: str | None) -> str:
    event_id = uuid.uuid4()
    with scope() as db:
        db.add(
            ActivityEventRow(
                event_id=event_id,
                occurred_at=datetime.now(UTC),
                recorded_at=datetime.now(UTC),
                event_type=event_type,
                subsystem="deployment",
                status="completed",
                severity="info",
                action="cloud_core_bluegreen",
                result=result,
                production_state="n/a",
                evidence_refs=[],
                factual_summary="Cloud Core canliya alindi.",
                detail_json={},
                source="live",
                source_ref=f"test:{event_id}",
            )
        )
        db.commit()
    return str(event_id)


def test_candidate_sha_reads_the_image_tag_or_the_bare_sha_and_refuses_the_rest() -> None:
    assert candidate_sha(f"pagentos/cloud-core:{SHA}") == SHA
    assert candidate_sha(SHA.upper()) == SHA
    assert candidate_sha("pagentos/cloud-core:1072d0d") == "1072d0d"
    assert candidate_sha("branch-name") is None
    assert candidate_sha("pagentos/cloud-core:") is None
    assert candidate_sha(None) is None
    assert candidate_sha("abc") is None


def test_no_released_row_means_no_release() -> None:
    scope = _scope()
    provider = LedgerReleaseEvidenceProvider(scope)
    assert provider.name == "ledger"
    assert provider.approved_release({"candidate_ref": f"pagentos/cloud-core:{SHA}"}) is None
    # A rollback row is not a release.
    _row(scope, event_type="deployment.cloud_core.rolled_back", result=SHA)
    assert provider.approved_release({"candidate_ref": f"pagentos/cloud-core:{SHA}"}) is None
    # A release of a DIFFERENT sha is not this candidate's release.
    _row(scope, event_type="deployment.cloud_core.released", result="c" * 40)
    assert provider.approved_release({"candidate_ref": f"pagentos/cloud-core:{SHA}"}) is None


def test_a_released_row_for_the_candidate_sha_is_the_release_ref() -> None:
    scope = _scope()
    provider = LedgerReleaseEvidenceProvider(scope)
    event_id = _row(scope, event_type="deployment.cloud_core.released", result=SHA)
    assert provider.approved_release({"candidate_ref": f"pagentos/cloud-core:{SHA}"}) == (
        f"ledger_event:{event_id}"
    )
    # A short sha in the candidate ref matches the full sha the ledger recorded.
    assert provider.approved_release({"candidate_ref": "pagentos/cloud-core:1072d0d"}) == (
        f"ledger_event:{event_id}"
    )
    # No candidate ref: nothing to look up, nothing to release.
    assert provider.approved_release({"candidate_ref": None}) is None
