"""Unit tests: fingerprint formula/dedup, release records, monitoring drafts.

Runs against SQLite (portable column types, like the other modules); the real
Postgres schema is exercised by the integration E2E.
"""

import contextlib
import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.selfhealing.errors import SelfHealingError, SelfHealingErrorClass
from app.selfhealing.models import Incident, Release
from app.selfhealing.monitoring import (
    CheckResult,
    draft_from_supervisor_report,
    evaluate_checks,
)
from app.selfhealing.service import (
    SelfHealingService,
    build_manifest,
    compute_fingerprint,
    compute_manifest_digest,
)

TABLES = [Release.__table__, Incident.__table__]


@pytest.fixture()
def service() -> SelfHealingService:
    engine = create_engine("sqlite://")
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return SelfHealingService(session_scope)


def make_report(error_class: str = "wrong_error_mapping", **overrides) -> dict:
    report = {
        "schema": "pagentos.selfhealing.incident.v1",
        "component": "browser-agent-demo",
        "severity": "critical",
        "fingerprint_material": {
            "component": "browser-agent-demo",
            "error_class": error_class,
            "failing_check": "selftest",
        },
        "workspace": "E:/tmp/ws",
        "active_version": "1.1.0",
        "active_manifest_digest": "ab" * 32,
        "last_known_good": "1.0.0",
        "rolled_back_to": "1.0.0",
        "evidence": {
            "selftest": {
                "status": "fail",
                "error_class": error_class,
                "check": "map_error",
                "input": "net::ERR_NAME_NOT_RESOLVED at https://example.invalid",
                "expected": "dependency_unavailable",
                "actual": "internal_bug",
            }
        },
        "detected_at": "2026-08-31T00:00:00+00:00",
        "recovered_at": "2026-08-31T00:00:05+00:00",
    }
    report.update(overrides)
    return report


# ------------------------------------------------------------------ fingerprint


def test_fingerprint_formula_is_stable_sha256() -> None:
    fp = compute_fingerprint("browser-agent-demo", "wrong_error_mapping", "selftest")
    expected = hashlib.sha256(
        b"browser-agent-demo\nwrong_error_mapping\nselftest"
    ).hexdigest()
    assert fp == expected
    assert len(fp) == 64


def test_ingest_dedups_by_fingerprint_and_increments(service: SelfHealingService) -> None:
    draft = draft_from_supervisor_report(make_report())
    first = service.ingest_incident(draft)
    assert first.created is True
    assert first.occurrence_count == 1
    # Supervisor already rolled back -> incident starts RECOVERED, not open.
    assert first.status == "recovered"

    second = service.ingest_incident(draft_from_supervisor_report(make_report()))
    assert second.created is False
    assert second.incident_id == first.incident_id
    assert second.occurrence_count == 2

    # A different error class is a different failure: new fingerprint, new row.
    other = service.ingest_incident(
        draft_from_supervisor_report(make_report(error_class="self_test_failed"))
    )
    assert other.created is True
    assert other.fingerprint != first.fingerprint
    assert len(service.list_incidents()) == 2


def test_ingest_without_rollback_opens_incident(service: SelfHealingService) -> None:
    report = make_report(rolled_back_to=None, recovered_at=None)
    result = service.ingest_incident(draft_from_supervisor_report(report))
    assert result.status == "open"


def test_ingest_records_introduced_release(service: SelfHealingService) -> None:
    result = service.ingest_incident(draft_from_supervisor_report(make_report()))
    incident = service.get_incident(result.incident_id)
    assert incident["introduced_release_id"] is not None
    releases = service.list_releases(component="browser-agent-demo")
    assert len(releases) == 1
    assert releases[0]["version"] == "1.1.0"
    assert releases[0]["status"] == "rolled_back"
    assert releases[0]["manifest_digest"] == "ab" * 32
    # Evidence keeps the fingerprint material + workspace for the pipeline.
    assert incident["evidence"]["fingerprint_material"]["error_class"] == "wrong_error_mapping"
    assert incident["evidence"]["workspace"] == "E:/tmp/ws"
    assert incident["evidence"]["active_version"] == "1.1.0"


def test_incident_status_transitions_and_fixed_release(service: SelfHealingService) -> None:
    result = service.ingest_incident(draft_from_supervisor_report(make_report()))
    release = service.record_release("browser-agent-demo", "1.1.1", "cd" * 32)
    service.set_incident_status(result.incident_id, "fix_in_progress")
    updated = service.set_incident_status(
        result.incident_id, "fixed", fixed_release_id=uuid.UUID(release["id"])
    )
    assert updated["status"] == "fixed"
    assert updated["fixed_release_id"] == release["id"]
    with pytest.raises(SelfHealingError) as excinfo:
        service.set_incident_status(result.incident_id, "nonsense")
    assert excinfo.value.error_class == SelfHealingErrorClass.VALIDATION_ERROR


def test_release_records_are_immutable_and_unique(service: SelfHealingService) -> None:
    service.record_release("browser-agent-demo", "1.0.0", "aa" * 32, status="active")
    with pytest.raises(SelfHealingError) as excinfo:
        service.record_release("browser-agent-demo", "1.0.0", "bb" * 32)
    assert excinfo.value.error_class == SelfHealingErrorClass.VALIDATION_ERROR
    # Same version for a different component is fine.
    service.record_release("other-component", "1.0.0", "cc" * 32)


def test_release_status_lifecycle_timestamps(service: SelfHealingService) -> None:
    row = service.record_release("browser-agent-demo", "2.0.0", "dd" * 32)
    release_id = uuid.UUID(row["id"])
    staged = service.set_release_status(release_id, "staging")
    assert staged["promoted_at"] is None
    active = service.set_release_status(release_id, "active")
    assert active["promoted_at"] is not None
    rejected = service.set_release_status(release_id, "rejected")
    assert rejected["rolled_back_at"] is not None
    with pytest.raises(SelfHealingError):
        service.set_release_status(release_id, "bogus")
    with pytest.raises(SelfHealingError) as excinfo:
        service.set_release_status(uuid.uuid4(), "active")
    assert excinfo.value.error_class == SelfHealingErrorClass.NOT_FOUND


def test_supersede_active_releases(service: SelfHealingService) -> None:
    old = service.record_release("browser-agent-demo", "1.0.0", "aa" * 32, status="active")
    new = service.record_release("browser-agent-demo", "1.1.1", "bb" * 32, status="active")
    count = service.supersede_active_releases(
        "browser-agent-demo", except_release_id=uuid.UUID(new["id"])
    )
    assert count == 1
    assert service.get_release("browser-agent-demo", "1.0.0")["status"] == "superseded"
    assert service.get_release("browser-agent-demo", "1.1.1")["status"] == "active"
    assert old["id"] != new["id"]


# ------------------------------------------------------------------ monitoring


def test_evaluate_checks_all_ok_is_none() -> None:
    checks = [CheckResult("health", True), CheckResult("selftest", True)]
    assert evaluate_checks("browser-agent-demo", checks) is None


def test_evaluate_checks_prefers_typed_error_class() -> None:
    checks = [
        CheckResult("health", True),
        CheckResult(
            "selftest", False, {"error_class": "wrong_error_mapping", "actual": "internal_bug"}
        ),
    ]
    draft = evaluate_checks("browser-agent-demo", checks, active_version="1.1.0")
    assert draft is not None
    assert draft.error_class == "wrong_error_mapping"
    assert draft.failing_check == "selftest"
    assert draft.active_version == "1.1.0"


def test_evaluate_checks_falls_back_to_generic_class() -> None:
    draft = evaluate_checks("c", [CheckResult("health", False, {"error": "refused"})])
    assert draft.error_class == "health_check_failed"


def test_supervisor_report_validation() -> None:
    with pytest.raises(SelfHealingError):
        draft_from_supervisor_report({"schema": "bogus.v9"})
    with pytest.raises(SelfHealingError):
        draft_from_supervisor_report(
            {"schema": "pagentos.selfhealing.incident.v1", "fingerprint_material": {}}
        )
    report = make_report()
    report["component"] = "someone-else"  # mismatch vs fingerprint_material
    with pytest.raises(SelfHealingError):
        draft_from_supervisor_report(report)
    report = make_report(severity="apocalyptic")
    with pytest.raises(SelfHealingError):
        draft_from_supervisor_report(report)


# ------------------------------------------------------------- manifest digest


def test_manifest_digest_deterministic_and_excludes_manifest(tmp_path: Path) -> None:
    release = tmp_path / "rel"
    release.mkdir()
    (release / "handler.py").write_text("X = 1\n", encoding="utf-8")
    first = compute_manifest_digest(release)
    assert first == compute_manifest_digest(release)
    manifest = build_manifest(release, "1.0.0")
    (release / "manifest.json").write_text("{}", encoding="utf-8")
    (release / "__pycache__").mkdir()
    (release / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    assert compute_manifest_digest(release) == first == manifest["digest"]
    assert manifest["files"][0]["path"] == "handler.py"
    (release / "handler.py").write_text("X = 2\n", encoding="utf-8")
    assert compute_manifest_digest(release) != first
