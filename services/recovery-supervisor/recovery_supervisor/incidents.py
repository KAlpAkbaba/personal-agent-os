"""Incident reports: outbox files (source of truth) + optional API POST.

The supervisor never reasons about the failure — it records mechanical
fingerprint MATERIAL (component, error class, failing check) and evidence; the
main API computes the actual fingerprint hash and deduplicates. If the API is
down (which is exactly when the supervisor matters), the outbox file remains
the durable record and can be drained later.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

INCIDENT_SCHEMA = "pagentos.selfhealing.incident.v1"


def build_incident_report(
    *,
    component: str,
    error_class: str,
    failing_check: str,
    workspace: str,
    active_version: str | None,
    active_manifest_digest: str | None,
    last_known_good: str | None,
    rolled_back_to: str | None,
    evidence: dict[str, Any],
    detected_at: str,
    recovered_at: str | None,
    severity: str = "critical",
) -> dict[str, Any]:
    return {
        "schema": INCIDENT_SCHEMA,
        "component": component,
        "severity": severity,
        # Fingerprint material: the API hashes exactly these three fields.
        "fingerprint_material": {
            "component": component,
            "error_class": error_class,
            "failing_check": failing_check,
        },
        "workspace": workspace,
        "active_version": active_version,
        "active_manifest_digest": active_manifest_digest,
        "last_known_good": last_known_good,
        "rolled_back_to": rolled_back_to,
        "evidence": evidence,
        "detected_at": detected_at,
        "recovered_at": recovered_at,
    }


def write_outbox(outbox_dir: Path | str, report: dict[str, Any]) -> Path:
    outbox = Path(outbox_dir)
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    path = outbox / f"incident-{stamp}-{uuid.uuid4().hex[:8]}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def post_report(url: str, report: dict[str, Any], timeout: float = 5.0) -> bool:
    """Best-effort POST to the main API's ingest endpoint.

    Never raises: a False return means the outbox file stays the only record
    (it stays on disk either way — the outbox is the source of truth).
    """
    body = json.dumps(report, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False
