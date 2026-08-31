"""Supply-chain gate (ACCEPTANCE_TESTS M7 "Isolation, supply chain, resources";
SECURITY_MODEL M7).

    "no blind package installation: dependency name, version and source
     recorded and pinned; dependency/security scanning; install scripts cannot
     silently expand privileges"

Rules enforced here, each producing a named finding:

- ``unpinned_version``    — the version must be an EXACT semver; ranges,
                            wildcards and ``latest`` are refused;
- ``disallowed_source``   — only the curated local component catalog (and the
                            standard library) are allowed sources; there is no
                            remote-index code path in this product;
- ``missing_digest`` / ``malformed_digest`` — every record pins a sha256;
- ``install_script``      — a dependency that wants to run an install script is
                            refused outright (privilege expansion);
- ``dependency_unavailable`` — the record names something the catalog does not
                            actually contain, or its digest no longer matches.

The scan is a hard gate: ``EvolutionPipeline`` runs it before the candidate can
be evaluated, and again the reviewer re-runs it independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.evolution.components import LOCAL_SOURCE, ComponentCatalog
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.logging import get_logger

logger = get_logger("app.evolution.supply_chain")

STDLIB_SOURCE = "python-stdlib"
ALLOWED_SOURCES = (LOCAL_SOURCE, STDLIB_SOURCE)

EXACT_VERSION_RE = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Anything that makes a version non-exact.
RANGE_MARKERS = ("^", "~", "*", ">", "<", "=", " ", ",", "latest", "master", "main")


@dataclass(slots=True)
class SupplyChainFinding:
    dependency: str
    rule: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"dependency": self.dependency, "rule": self.rule, "detail": self.detail}


@dataclass(slots=True)
class SupplyChainReport:
    ok: bool
    scanned: list[dict[str, Any]] = field(default_factory=list)
    findings: list[SupplyChainFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "scanned": list(self.scanned),
            "findings": [finding.to_dict() for finding in self.findings],
            "rules": list(RULES),
        }


RULES = (
    "unpinned_version",
    "disallowed_source",
    "missing_digest",
    "malformed_digest",
    "install_script",
    "dependency_unavailable",
)


def scan_dependencies(
    records: list[dict[str, Any]] | None, *, catalog: ComponentCatalog | None = None
) -> SupplyChainReport:
    """Scan pinned dependency records. An empty list is trivially OK."""
    findings: list[SupplyChainFinding] = []
    scanned: list[dict[str, Any]] = []
    for record in records or []:
        name = str((record or {}).get("name") or "<unnamed>")
        version = str((record or {}).get("version") or "")
        source = str((record or {}).get("source") or "")
        digest = str((record or {}).get("digest") or "")
        scanned.append({"name": name, "version": version, "source": source, "digest": digest})

        if not version or not EXACT_VERSION_RE.match(version) or any(
            marker in version for marker in RANGE_MARKERS
        ):
            findings.append(
                SupplyChainFinding(name, "unpinned_version", "version must be an exact semver")
            )
        if source not in ALLOWED_SOURCES:
            findings.append(
                SupplyChainFinding(
                    name, "disallowed_source", f"allowed sources are {ALLOWED_SOURCES}"
                )
            )
        if not digest:
            findings.append(SupplyChainFinding(name, "missing_digest", "no pinned digest"))
        elif not DIGEST_RE.match(digest):
            findings.append(
                SupplyChainFinding(name, "malformed_digest", "digest must be sha256:<64 hex>")
            )
        if (record or {}).get("install_script"):
            findings.append(
                SupplyChainFinding(
                    name,
                    "install_script",
                    "install scripts may not silently expand privileges",
                )
            )
        if source == LOCAL_SOURCE:
            found = (catalog or ComponentCatalog.load()).get(name, version)
            if found is None:
                findings.append(
                    SupplyChainFinding(
                        name,
                        "dependency_unavailable",
                        f"{name} {version} is not in the local component catalog",
                    )
                )
            elif digest and found.digest != digest:
                findings.append(
                    SupplyChainFinding(
                        name, "dependency_unavailable", "catalog digest does not match the pin"
                    )
                )
    report = SupplyChainReport(ok=not findings, scanned=scanned, findings=findings)
    logger.info(
        "supply_chain_scanned",
        dependencies=len(scanned),
        ok=report.ok,
        rules_hit=sorted({f.rule for f in findings}),
    )
    return report


def require_clean_supply_chain(
    records: list[dict[str, Any]] | None, *, catalog: ComponentCatalog | None = None
) -> SupplyChainReport:
    report = scan_dependencies(records, catalog=catalog)
    if not report.ok:
        unavailable = [f for f in report.findings if f.rule == "dependency_unavailable"]
        raise EvolutionError(
            EvolutionErrorClass.DEPENDENCY_UNAVAILABLE
            if unavailable
            else EvolutionErrorClass.SUPPLY_CHAIN_REJECTED,
            "dependency scan rejected the candidate: "
            + ", ".join(sorted({f.rule for f in report.findings})),
            details=report.to_dict(),
        )
    return report


__all__ = [
    "ALLOWED_SOURCES",
    "DIGEST_RE",
    "EXACT_VERSION_RE",
    "RULES",
    "STDLIB_SOURCE",
    "SupplyChainFinding",
    "SupplyChainReport",
    "require_clean_supply_chain",
    "scan_dependencies",
]
