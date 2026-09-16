"""The App Factory's security scan (B40 req 439; the same checks the self-development
queue runs on its candidates, req 680): secrets, dangerous calls, network egress off the
loopback, over every generated file - and, for a fix, over the diff against the files the
project already carried. The verdict blocks a scaffold; it is recorded on the project
row so the owner can read what was refused, by file and line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.appfactory.generator import ProjectFiles
from app.selfdev.security_review import CHECK_GUARDED_PATH, SecurityFinding, review_texts


@dataclass(slots=True)
class SecurityReport:
    findings: list[SecurityFinding] = field(default_factory=list)
    files: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "files": self.files,
            "findings": [
                {"check": f.check, "path": f.path, "line": f.line, "detail": f.detail}
                for f in self.findings[:50]
            ],
        }


def scan_files(files: ProjectFiles, *, base: ProjectFiles | None = None) -> SecurityReport:
    """Every file against the self-development review's checks. A generated project has
    no guarded paths of its own (the guard list is the repository's), so that check is
    dropped; a base is the project as it was, so a fix is judged on what it introduces."""
    edits = {f.path: f.text for f in files.files}
    base_texts: dict[str, str | None] = {
        p: (base.get(p) if base is not None else None) for p in edits
    }
    verdict = review_texts(edits, base_texts=base_texts)
    findings = [f for f in verdict.findings if f.check != CHECK_GUARDED_PATH]
    return SecurityReport(findings=findings, files=len(files))


__all__ = ["SecurityReport", "scan_files"]
