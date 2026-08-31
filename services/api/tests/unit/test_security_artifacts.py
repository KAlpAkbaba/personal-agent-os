"""M8 unit tests: assessment results become a SECURITY ARTIFACT.

Acceptance bullet covered here: **results become security artifact** — through
the existing M3 artifact service (canonical Markdown + renders), not a second
reporting path.
"""

from __future__ import annotations

import uuid

import pytest

from app.artifacts import service as artifact_service
from app.artifacts.models import ARTIFACT_STATE_READY
from app.artifacts.renderers import content_hash
from app.object_store import InMemoryObjectStore
from app.security.artifacts import publish_assessment_artifact
from app.security.errors import SecurityError, SecurityErrorClass
from app.security.models import ARTIFACT_KIND_SECURITY_REPORT, SecurityAssessment
from app.security.redaction import contains_secret
from tests.unit.test_security_support import FIXTURE_ROOT, enroll_lab_host, make_stack


@pytest.fixture()
def published():
    stack = make_stack(with_artifacts=True)
    enroll_lab_host(stack, config_root=FIXTURE_ROOT)
    assessment = stack.assessments.run(target="10.20.30.40")
    store = InMemoryObjectStore()
    with stack.session() as session:
        result = publish_assessment_artifact(
            session, store, assessment_id=uuid.UUID(assessment["id"])
        )
    return stack, store, assessment, result


def _body(stack, artifact_id: str) -> str:
    with stack.session() as session:
        artifact = artifact_service.get_artifact(session, uuid.UUID(artifact_id))
        return artifact_service.get_current_version(session, artifact.id).canonical_body


# ------------------------------- ACCEPTANCE: results become an artifact


def test_assessment_becomes_a_security_artifact(published) -> None:
    _stack, _store, assessment, result = published

    assert result["kind"] == ARTIFACT_KIND_SECURITY_REPORT
    assert result["assessment_id"] == assessment["id"]
    assert result["state"] == ARTIFACT_STATE_READY
    assert result["version"] == 1
    assert result["content_hash"]
    assert result["executive_summary"]


def test_the_assessment_row_links_to_the_artifact(published) -> None:
    stack, _store, assessment, result = published
    with stack.session() as session:
        row = session.get(SecurityAssessment, uuid.UUID(assessment["id"]))
        assert str(row.artifact_id) == result["artifact_id"]


def test_renders_are_produced_and_stored(published) -> None:
    _stack, store, _assessment, result = published
    formats = {r["format"] for r in result["renders"]}
    assert formats == {"pdf", "html"}
    for render in result["renders"]:
        data = store.get(render["object_key"])
        assert content_hash(data) == render["content_hash"]
        assert render["size_bytes"] == len(data)
    pdf = next(r for r in result["renders"] if r["format"] == "pdf")
    assert store.get(pdf["object_key"])[:4] == b"%PDF"


def test_canonical_body_is_turkish_first_and_complete(published) -> None:
    stack, _store, _assessment, result = published
    body = _body(stack, result["artifact_id"])

    assert body.startswith("# Güvenlik Değerlendirme Raporu")
    for heading in (
        "## Yönetici Özeti (Executive Summary)",
        "## Kapsam ve Yetki (Scope & Authorization)",
        "## Bulgular (Findings)",
        "## Denetim Notu (Audit Note)",
    ):
        assert heading in body

    # Scope/authorization provenance is in the report, not implied.
    assert "lab-web-01" in body
    assert "10.20.30.40" in body
    assert "configuration_audit" in body
    assert "Ağ taraması" in body  # the "no scanning/exploitation" statement


def test_every_finding_appears_with_severity_evidence_and_remediation(
    published,
) -> None:
    stack, _store, assessment, result = published
    body = _body(stack, result["artifact_id"])
    for finding in assessment["findings"]:
        assert finding["title"] in body
        assert finding["evidence"]["check_id"] in body
        assert finding["fingerprint"][:16] in body
    assert "Kritik" in body and "Yüksek" in body
    assert "**Öneri:**" in body
    assert "**Kesinti seviyesi:**" in body
    assert "Manuel adımlar" in body


def test_executive_summary_is_stored_apart_from_the_body(published) -> None:
    stack, _store, _assessment, result = published
    body = _body(stack, result["artifact_id"])
    with stack.session() as session:
        artifact = artifact_service.get_artifact(session, uuid.UUID(result["artifact_id"]))
        assert artifact.executive_summary == result["executive_summary"]
    assert len(result["executive_summary"]) < len(body)
    assert "bulgu tespit edildi" in result["executive_summary"]


# ------------------------------------------------------ secret hygiene


def test_the_report_names_the_credential_exposure_without_reproducing_it(
    published,
) -> None:
    stack, store, _assessment, result = published
    body = _body(stack, result["artifact_id"])

    assert "credential_assignment" in body
    assert "değer rapora **yazılmamıştır**" in body
    assert contains_secret(body) is None
    for planted in (
        "Fak3-Placeholder-Password-Value",
        "AKIA" + "IOSFODNN7EXAMPLE",
        "placeholder-api-key-0000000000000000",
    ):
        assert planted not in body
        for render in result["renders"]:
            if render["format"] == "html":
                assert planted.encode() not in store.get(render["object_key"])


def test_publish_fails_closed_when_the_body_would_carry_a_secret() -> None:
    """The last-line-of-defence gate: a redaction bug refuses the publish
    instead of shipping a credential into object storage."""
    stack = make_stack(with_artifacts=True)
    enroll_lab_host(stack, config_root=FIXTURE_ROOT)
    assessment = stack.assessments.run(target="10.20.30.40")

    with stack.session() as session:
        from app.security.models import SecurityFinding

        row = session.get(SecurityFinding, uuid.UUID(assessment["findings"][0]["id"]))
        row.evidence_json = {
            **row.evidence_json,
            "redacted_line": "APP_PASSWORD=Fak3-Placeholder-Password-Value",
        }
        session.commit()

        with pytest.raises(SecurityError) as excinfo:
            publish_assessment_artifact(
                session, InMemoryObjectStore(), assessment_id=uuid.UUID(assessment["id"])
            )
    assert excinfo.value.error_class is SecurityErrorClass.INTERNAL_BUG
    assert excinfo.value.details["pattern"] == "credential_assignment"


# ---------------------------------------------------------- idempotency


def test_republishing_unchanged_content_reuses_the_version(published) -> None:
    stack, store, assessment, result = published
    with stack.session() as session:
        again = publish_assessment_artifact(
            session, store, assessment_id=uuid.UUID(assessment["id"])
        )
    assert again["artifact_id"] == result["artifact_id"]
    assert again["version"] == result["version"] == 1
    assert again["content_hash"] == result["content_hash"]


def test_a_later_assessment_publishes_a_new_artifact(published) -> None:
    stack, store, _assessment, result = published
    second = stack.assessments.run(target="10.20.30.40")
    with stack.session() as session:
        other = publish_assessment_artifact(
            session, store, assessment_id=uuid.UUID(second["id"])
        )
    assert other["artifact_id"] != result["artifact_id"]


def test_publishing_an_unknown_assessment_is_typed_not_found() -> None:
    stack = make_stack(with_artifacts=True)
    with stack.session() as session, pytest.raises(SecurityError) as excinfo:
        publish_assessment_artifact(
            session, InMemoryObjectStore(), assessment_id=uuid.uuid4()
        )
    assert excinfo.value.error_class is SecurityErrorClass.NOT_FOUND


def test_a_clean_assessment_still_produces_a_report(tmp_path) -> None:
    stack = make_stack(with_artifacts=True)
    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    (clean_root / "ok.ini").write_text("[general]\nlog_level = info\n", encoding="utf-8")
    enroll_lab_host(stack, config_root=clean_root)
    assessment = stack.assessments.run(target="10.20.30.40")
    assert assessment["result"]["findings_total"] == 0

    with stack.session() as session:
        result = publish_assessment_artifact(
            session, InMemoryObjectStore(), assessment_id=uuid.UUID(assessment["id"])
        )
    assert "bulgu yok" in result["executive_summary"]
    assert "Bu çalıştırmada bulgu yoktur." in _body(stack, result["artifact_id"])
