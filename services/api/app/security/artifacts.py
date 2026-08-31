"""Assessment -> SECURITY ARTIFACT, through the existing artifact service (M8).

An assessment report is an artifact like any other, so it goes through the M3
pipeline rather than a parallel reporting path (ADR-0026): canonical Turkish
Markdown is the source of truth in `artifact_versions.canonical_body`, renders
are derived bytes in the object store recorded in `artifact_renders`, and the
executive summary is stored separately so the owner can be notified briefly
without the full report being forced on them (constitution: task completion,
persistence and presentation are separate concepts).

Nothing about rendering is reimplemented here. This module only composes the
canonical body and calls `app.artifacts.service` / `app.artifacts.render_store`.

Publishing is idempotent: `security_assessments.artifact_id` links the two, a
re-publish of unchanged content reuses the existing version (the artifact
service compares `content_hash`), and renders are repaired rather than
duplicated.

Last line of defence for secret hygiene: the composed body is checked with
`redaction.contains_secret` before it is ever written. Findings and evidence
were already redacted on the way into the database, so a hit here means a bug —
and the publish fails instead of shipping the credential into a PDF that lands
in object storage.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session

from app.artifacts import render_store
from app.artifacts import service as artifact_service
from app.artifacts.models import (
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
)
from app.artifacts.renderers import content_hash
from app.logging import get_logger
from app.object_store import ObjectStore
from app.security.assessments import finding_to_dict
from app.security.errors import SecurityError, SecurityErrorClass
from app.security.models import (
    ARTIFACT_KIND_SECURITY_REPORT,
    SEVERITY_ORDER,
    AuthorizedAsset,
    SecurityAssessment,
    SecurityFinding,
)
from app.security.redaction import contains_secret
from app.security.registry import iso8601

logger = get_logger("app.security.artifacts")

SessionFactory = Callable[[], AbstractContextManager[Session]]

DEFAULT_FORMATS = ("pdf", "html")

SEVERITY_TR = {
    "critical": "Kritik",
    "high": "Yüksek",
    "medium": "Orta",
    "low": "Düşük",
    "info": "Bilgi",
}

DISRUPTION_TR = {
    "none": "yok",
    "low": "düşük",
    "medium": "orta",
    "high": "yüksek",
}


def _title(asset: AuthorizedAsset, assessment: SecurityAssessment) -> str:
    return f"Güvenlik Değerlendirme Raporu — {asset.name} ({asset.asset_ref})"


def compose_executive_summary(
    asset: AuthorizedAsset,
    assessment: SecurityAssessment,
    findings: list[SecurityFinding],
) -> str:
    """Short, decision-grade Turkish summary — never the whole report."""
    result = dict(assessment.result_json or {})
    counts = dict(result.get("by_severity") or {})
    ordered = [s for s in ("critical", "high", "medium", "low", "info") if counts.get(s)]
    if not findings:
        headline = "Yetkili kapsam içinde yapılandırma denetimi tamamlandı; bulgu yok."
    else:
        breakdown = ", ".join(f"{counts[s]} {SEVERITY_TR[s].lower()}" for s in ordered)
        top = max(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 0))
        headline = (
            f"{len(findings)} bulgu tespit edildi ({breakdown}). "
            f"En yüksek öncelikli bulgu: {top.title}."
        )
    return (
        f"{headline} Değerlendirme, sahibin kayıtlı yetki kapsamındaki "
        f"'{asset.asset_ref}' varlığı üzerinde salt-okunur yapılandırma denetimi "
        f"({result.get('files_scanned', 0)} dosya) olarak yürütüldü. "
        "Kanıtlar gizli bilgi sızdırmayacak biçimde maskelenmiştir."
    )


def compose_canonical_markdown(
    asset: AuthorizedAsset,
    assessment: SecurityAssessment,
    findings: list[SecurityFinding],
    executive_summary: str,
) -> str:
    """Turkish-first canonical body: summary + scope + findings + audit note."""
    result = dict(assessment.result_json or {})
    decision = dict(assessment.scope_decision_json or {})
    constraints = dict(asset.constraints_json or {})
    lines: list[str] = []

    lines.append(f"# {_title(asset, assessment)}")
    lines.append("")
    lines.append("## Yönetici Özeti (Executive Summary)")
    lines.append("")
    lines.append(executive_summary)
    lines.append("")

    lines.append("## Kapsam ve Yetki (Scope & Authorization)")
    lines.append("")
    lines.append("| Alan | Değer |")
    lines.append("| --- | --- |")
    lines.append(f"| Varlık (asset_ref) | `{asset.asset_ref}` |")
    lines.append(f"| Tür (kind) | {asset.kind} |")
    lines.append(f"| Konum (locator) | `{asset.locator}` |")
    lines.append(f"| Ortam (environment) | {asset.environment} |")
    lines.append(f"| Değerlendirilen hedef | `{assessment.target}` |")
    lines.append(f"| Eşleşme yöntemi | {decision.get('matched_by') or '-'} |")
    lines.append(f"| Test sınıfı | {assessment.testing_class} |")
    lines.append(
        f"| Yetki penceresi | {iso8601(asset.valid_from)} → "
        f"{iso8601(asset.valid_until) or 'süresiz'} |"
    )
    lines.append(
        f"| Azami kesinti (max_disruption) | "
        f"{DISRUPTION_TR.get(str(constraints.get('max_disruption', 'none')), '-')} |"
    )
    lines.append(f"| Toplayıcı (collector) | `{result.get('collector', '-')}` |")
    lines.append(f"| Taranan dosya sayısı | {result.get('files_scanned', 0)} |")
    lines.append(f"| Çalıştırılan kontrol sayısı | {result.get('checks_run', 0)} |")
    lines.append("")
    lines.append(
        "> Bu değerlendirme yalnızca sahibin kayıtlı yetki kapsamındaki varlık üzerinde, "
        "salt-okunur yapılandırma denetimi olarak yürütülmüştür. Ağ taraması, "
        "sömürü (exploitation) veya üçüncü taraf araç kullanımı yoktur."
    )
    lines.append("")

    lines.append("## Bulgular (Findings)")
    lines.append("")
    if not findings:
        lines.append("Bu çalıştırmada bulgu yoktur.")
        lines.append("")
    else:
        lines.append("| # | Önem | Bulgu | Dosya | Durum |")
        lines.append("| --- | --- | --- | --- | --- |")
        for index, finding in enumerate(findings, start=1):
            evidence = dict(finding.evidence_json or {})
            lines.append(
                f"| {index} | {SEVERITY_TR.get(finding.severity, finding.severity)} "
                f"| {finding.title} | `{evidence.get('file', '-')}` | {finding.status} |"
            )
        lines.append("")
        for index, finding in enumerate(findings, start=1):
            evidence = dict(finding.evidence_json or {})
            remediation = dict(finding.remediation_json or {})
            severity_tr = SEVERITY_TR.get(finding.severity, finding.severity)
            lines.append(f"### {index}. {finding.title} — {severity_tr}")
            lines.append("")
            lines.append(f"- **Kontrol (check):** `{evidence.get('check_id', '-')}`")
            lines.append(f"- **Parmak izi (fingerprint):** `{finding.fingerprint[:16]}…`")
            lines.append(
                f"- **Konum:** `{evidence.get('file', '-')}`, satır {evidence.get('line', '-')}"
            )
            if evidence.get("secret_patterns"):
                lines.append(
                    "- **Tespit edilen kimlik bilgisi türü:** "
                    + ", ".join(f"`{p}`" for p in evidence["secret_patterns"])
                    + " — değer rapora **yazılmamıştır**."
                )
            lines.append(f"- **Gerekçe:** {evidence.get('rationale_tr', '-')}")
            lines.append("- **Kanıt (maskelenmiş):**")
            lines.append("")
            lines.append("```text")
            lines.append(str(evidence.get("redacted_line", "-")))
            lines.append("```")
            lines.append("")
            lines.append(f"- **Öneri:** {remediation.get('summary_tr', '-')}")
            lines.append(
                f"- **Kesinti seviyesi:** "
                f"{DISRUPTION_TR.get(str(remediation.get('disruption', 'none')), '-')}"
                f" · **Otomatik uygulanabilir:** "
                f"{'evet' if remediation.get('apply_supported') else 'hayır'}"
                f" · **Geri alınabilir:** "
                f"{'evet' if remediation.get('reversible') else 'hayır'}"
            )
            if remediation.get("proposed_line"):
                lines.append("- **Önerilen satır:**")
                lines.append("")
                lines.append("```text")
                lines.append(str(remediation["proposed_line"]))
                lines.append("```")
            if remediation.get("manual_steps"):
                lines.append("- **Manuel adımlar:**")
                for step in remediation["manual_steps"]:
                    lines.append(f"  1. {step}")
            lines.append("")

    lines.append("## Denetim Notu (Audit Note)")
    lines.append("")
    lines.append(
        f"- Değerlendirme kimliği: `{assessment.id}` · durum: `{assessment.status}`"
    )
    lines.append(f"- Başlangıç: {iso8601(assessment.started_at) or '-'}")
    lines.append(f"- Bitiş: {iso8601(assessment.completed_at) or '-'}")
    lines.append(
        "- Yetki kararı ve her reddedilen istek `authorization_events` tablosuna "
        "eklenir; kapsam sessizce genişletilmez (SECURITY_MODEL §8-9)."
    )
    lines.append(
        "- Tüm kanıtlar kalıcılaştırılmadan önce maskeleme (redaction) sürecinden "
        "geçirilir; bu raporda hiçbir gizli değer bulunmaz."
    )
    lines.append("")
    return "\n".join(lines)


def publish_assessment_artifact(
    session: Session,
    store: ObjectStore,
    *,
    assessment_id: uuid.UUID,
    formats: tuple[str, ...] = DEFAULT_FORMATS,
) -> dict[str, Any]:
    """Compose + persist + render the security report. Idempotent."""
    assessment = session.get(SecurityAssessment, assessment_id)
    if assessment is None:
        raise SecurityError(
            SecurityErrorClass.NOT_FOUND,
            f"assessment {assessment_id} not found",
            {"assessment_id": str(assessment_id)},
        )
    asset = session.get(AuthorizedAsset, assessment.asset_id)
    if asset is None:  # pragma: no cover - FK enforced
        raise SecurityError(SecurityErrorClass.INTERNAL_BUG, "assessment has no asset")

    findings = sorted(
        session.query(SecurityFinding)
        .filter(SecurityFinding.assessment_id == assessment.id)
        .all(),
        key=lambda f: (-SEVERITY_ORDER.get(f.severity, 0), f.fingerprint),
    )

    executive_summary = compose_executive_summary(asset, assessment, findings)
    body = compose_canonical_markdown(asset, assessment, findings, executive_summary)

    leaked = contains_secret(body) or contains_secret(executive_summary)
    if leaked is not None:
        # Fail closed: never render a credential into object storage.
        raise SecurityError(
            SecurityErrorClass.INTERNAL_BUG,
            "composed security report failed the secret-hygiene gate; publish refused",
            {"pattern": leaked},
        )

    title = _title(asset, assessment)
    if assessment.artifact_id is not None:
        artifact = artifact_service.get_artifact(session, assessment.artifact_id)
    else:
        artifact = None
    if artifact is None:
        artifact = artifact_service.get_or_create_artifact_for_task(
            session, task_id=None, title=title, kind=ARTIFACT_KIND_SECURITY_REPORT
        )
        assessment.artifact_id = artifact.id
        session.commit()

    version = artifact_service.add_artifact_version(
        session,
        artifact_id=artifact.id,
        canonical_body=body,
        content_hash=content_hash(body.encode("utf-8")),
        source_manifest={
            "assessment_id": str(assessment.id),
            "asset_ref": asset.asset_ref,
            "testing_class": assessment.testing_class,
            "collector": (assessment.result_json or {}).get("collector"),
            "finding_fingerprints": [f.fingerprint for f in findings],
        },
    )
    artifact_service.set_executive_summary(session, artifact.id, executive_summary)
    if artifact.state != ARTIFACT_STATE_CANONICAL_READY:
        artifact_service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_CANONICAL_READY)
    artifact_service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_RENDERS_PENDING)
    rows = render_store.ensure_renders(
        session, store, version=version, title=title, formats=tuple(formats)
    )
    artifact_service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_READY)

    logger.info(
        "security_artifact_published",
        assessment_id=str(assessment.id),
        artifact_id=str(artifact.id),
        version=version.version,
        renders=len(rows),
    )
    return {
        "artifact_id": str(artifact.id),
        "assessment_id": str(assessment.id),
        "title": title,
        "kind": ARTIFACT_KIND_SECURITY_REPORT,
        "version": version.version,
        "content_hash": version.content_hash,
        "executive_summary": executive_summary,
        "state": ARTIFACT_STATE_READY,
        "renders": [
            {
                "format": r.format,
                "object_key": r.object_key,
                "mime_type": r.mime_type,
                "content_hash": r.content_hash,
                "size_bytes": r.size_bytes,
            }
            for r in rows
        ],
        "findings": [finding_to_dict(f) for f in findings],
    }


__all__ = [
    "DEFAULT_FORMATS",
    "compose_canonical_markdown",
    "compose_executive_summary",
    "publish_assessment_artifact",
]
