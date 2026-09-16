"""The mandatory security review of a self-development candidate (B35 req 598, 680).

Every candidate the engine produces passes through here BEFORE it is judged fit to
commit: a deterministic reading of what the patch writes and where, run by a lab
authority that holds :data:`REQUIRED_GRANT` (``Grant.SECURITY_REVIEW_CANDIDATE``) - the
grant M18 declared and nothing consumed until this module. No grant, no review; no
review, no candidate.

What it refuses is exactly what a generated patch must never carry into a branch a
person will be asked to approve:

* **secret literals** - key shapes and ``password = "..."`` assignments in new text;
* **dangerous calls** - ``eval``/``exec``/``os.system``/``shell=True``/``pickle.loads``;
* **guarded paths** - the identity/secret/authority boundary, CI workflows, deploy and
  secret scripts, dependency manifests: a change there is the owner's, never a candidate's;
* **new network egress** - a URL or an HTTP client that the base file did not have;
* **test removal** - an edit to a test module that ends with fewer tests than it began.

The verdict names every finding with its path and line so the fix loop can feed it back
to the model as one more failure, and the record keeps it as evidence. Prose is not a
review: a finding is a regex hit at a line, and the checks run whether or not the model
said the patch was safe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from app.evolution.authority import Authority, AuthorityError, Grant

REQUIRED_GRANT: Final[Grant] = Grant.SECURITY_REVIEW_CANDIDATE

CHECK_SECRET = "no_secret_literal"
CHECK_DANGEROUS_CALL = "no_dangerous_call"
CHECK_GUARDED_PATH = "no_guarded_path"
CHECK_NETWORK = "no_new_network_egress"
CHECK_TEST_REMOVAL = "no_test_removed"
CHECKS: Final[tuple[str, ...]] = (
    CHECK_SECRET,
    CHECK_DANGEROUS_CALL,
    CHECK_GUARDED_PATH,
    CHECK_NETWORK,
    CHECK_TEST_REMOVAL,
)

#: Repository-relative prefixes (or exact files) a candidate may never touch. The identity
#: and secret boundary is tier 5 in ``app/evolution/risk.py``; here it is not a tier but
#: a refusal, together with everything that runs with the owner's credentials outside
#: the application (CI, deploy, secret storage) and the dependency manifests.
GUARDED_PREFIXES: Final[tuple[str, ...]] = (
    "services/api/app/identity/",
    "services/api/app/security/",
    "services/api/app/evolution/authority.py",
    "services/api/app/evolution/risk.py",
    "services/api/app/selfdev/security_review.py",
    "services/api/app/actions/confirmation_gate.py",
    ".github/",
    "scripts/secret-store.ps1",
    "scripts/cloud/",
    "scripts/release/",
    "deploy/",
    "infra/",
)
GUARDED_NAMES: Final[tuple[str, ...]] = (
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "pnpm-lock.yaml",
    "requirements.txt",
    "Dockerfile",
    "docker-compose.yml",
    ".env",
)

_SECRET_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("aws access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("openai/anthropic style key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "credential assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']"
        ),
    ),
)
_DANGEROUS_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("shell=True", re.compile(r"shell\s*=\s*True")),
    ("eval()", re.compile(r"(?<![\w.])eval\s*\(")),
    ("exec()", re.compile(r"(?<![\w.])exec\s*\(")),
    ("os.system()", re.compile(r"\bos\.system\s*\(")),
    ("pickle.loads()", re.compile(r"\bpickle\.loads?\s*\(")),
    ("__import__()", re.compile(r"__import__\s*\(")),
    ("subprocess with a shell string", re.compile(r"subprocess\.(?:run|Popen|call)\s*\(\s*[\"']")),
)
_NETWORK_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("url literal", re.compile(r"https?://[^\s\"']+")),
    ("httpx client", re.compile(r"\bhttpx\.(?:get|post|put|delete|Client|AsyncClient|request)\b")),
    ("requests client", re.compile(r"\brequests\.(?:get|post|put|delete|Session|request)\b")),
    ("urllib request", re.compile(r"\burllib\.request\b|\burlopen\s*\(")),
    ("raw socket", re.compile(r"\bsocket\.(?:socket|create_connection)\s*\(")),
)
_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+", re.MULTILINE)
_URL_ALLOWLIST: Final[tuple[str, ...]] = (
    "http://localhost",
    "http://127.0.0.1",
    "https://localhost",
)


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    check: str
    path: str
    line: int
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.check, "path": self.path, "line": self.line, "detail": self.detail}


@dataclass(slots=True)
class SecurityVerdict:
    findings: list[SecurityFinding] = field(default_factory=list)
    reviewed_paths: list[str] = field(default_factory=list)
    grant: str = str(REQUIRED_GRANT)

    @property
    def passed(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "grant": self.grant,
            "checks": list(CHECKS),
            "reviewed_paths": list(self.reviewed_paths),
            "findings": [f.as_dict() for f in self.findings],
        }

    def summary(self) -> str:
        if self.passed:
            return "security review passed"
        return "security review: " + "; ".join(
            f"{f.check} at {f.path}:{f.line} ({f.detail})" for f in self.findings[:6]
        )


def is_guarded_path(path: str) -> bool:
    normalised = path.replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised[2:]
    normalised = normalised.lstrip("/")
    if any(normalised.startswith(prefix) for prefix in GUARDED_PREFIXES):
        return True
    name = normalised.rsplit("/", 1)[-1]
    return name in GUARDED_NAMES or name.startswith(".env")


def _is_test_path(path: str) -> bool:
    normalised = path.replace("\\", "/")
    name = normalised.rsplit("/", 1)[-1]
    return "/tests/" in normalised and (name.startswith("test_") or name.endswith("_test.py"))


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _new_lines(base: str | None, new: str) -> set[str]:
    """The lines the patch introduces: what is in the new text and not in the base."""
    before = set((base or "").splitlines())
    return {line for line in new.splitlines() if line not in before}


def review_texts(
    edits: dict[str, str],
    *,
    base_texts: dict[str, str | None],
) -> SecurityVerdict:
    """Review ``edits`` (path -> new full text) against ``base_texts`` (path -> the file
    before the patch, or None when the file is new). Pure: no grant is needed to READ a
    verdict; :func:`review_candidate` is the gate that demands the grant."""
    verdict = SecurityVerdict(reviewed_paths=sorted(edits))
    for path in sorted(edits):
        new = edits[path]
        base = base_texts.get(path)
        if is_guarded_path(path):
            verdict.findings.append(
                SecurityFinding(CHECK_GUARDED_PATH, path, 0, "a candidate may not change this path")
            )
        introduced = _new_lines(base, new)
        for label, pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(new):
                line_text = new[new.rfind("\n", 0, match.start()) + 1 :].split("\n", 1)[0]
                if line_text in introduced:
                    verdict.findings.append(
                        SecurityFinding(CHECK_SECRET, path, _line_of(new, match.start()), label)
                    )
        for label, pattern in _DANGEROUS_PATTERNS:
            for match in pattern.finditer(new):
                line_text = new[new.rfind("\n", 0, match.start()) + 1 :].split("\n", 1)[0]
                if line_text in introduced:
                    verdict.findings.append(
                        SecurityFinding(
                            CHECK_DANGEROUS_CALL, path, _line_of(new, match.start()), label
                        )
                    )
        for label, pattern in _NETWORK_PATTERNS:
            for match in pattern.finditer(new):
                hit = match.group(0)
                if label == "url literal" and hit.startswith(_URL_ALLOWLIST):
                    continue
                line_text = new[new.rfind("\n", 0, match.start()) + 1 :].split("\n", 1)[0]
                if line_text in introduced and hit not in (base or ""):
                    verdict.findings.append(
                        SecurityFinding(CHECK_NETWORK, path, _line_of(new, match.start()), label)
                    )
        if _is_test_path(path) and base is not None:
            before_n = len(_TEST_DEF.findall(base))
            after_n = len(_TEST_DEF.findall(new))
            if after_n < before_n:
                verdict.findings.append(
                    SecurityFinding(
                        CHECK_TEST_REMOVAL,
                        path,
                        0,
                        f"{before_n} tests before, {after_n} after",
                    )
                )
    return verdict


def review_candidate(
    authority: Authority,
    edits: dict[str, str],
    *,
    base_texts: dict[str, str | None],
) -> SecurityVerdict:
    """The gate: an authority without :data:`REQUIRED_GRANT` cannot review, and an
    authority that is not a lab authority is not what reviews a candidate either."""
    if not isinstance(authority, Authority):
        raise AuthorityError("security review requires an Authority", got=type(authority).__name__)
    authority.require(REQUIRED_GRANT, action="security_review_candidate")
    return review_texts(edits, base_texts=base_texts)
