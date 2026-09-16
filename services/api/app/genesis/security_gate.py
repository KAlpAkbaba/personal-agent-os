"""The mandatory security gate on a generated adapter (B36 req 579, 680).

Every adapter Genesis builds - rendered deterministically, or proposed by the model
(req 577) - passes through here after it is written and BEFORE a single test runs: the
same deterministic review the self-development reviewer runs on a candidate
(``app.selfdev.security_review``, B35: secret literals, dangerous calls, guarded paths,
network egress, test removal), plus what a generated adapter specifically may never do:

* import anything outside the standard-library allowlist an adapter needs (the supply-
  chain scan checks declared dependencies; this checks the SOURCE);
* reach any host but the one interface it was researched from;
* touch the filesystem, spawn a process, or read the environment.

A finding is a path and a line; the verdict is kept whole in the run's evidence; a red
verdict fails the run at ``testing`` with ``security_refused`` and rejects the skill
version, so nothing that failed here can ever be registered.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from app.evolution.skills import SkillLayout
from app.selfdev.security_review import (
    CHECK_NETWORK,
    SecurityFinding,
    SecurityVerdict,
    review_texts,
)

CHECK_IMPORT = "no_import_outside_allowlist"
CHECK_FOREIGN_HOST = "no_foreign_host"
CHECK_SYSTEM_ACCESS = "no_system_access"
GENESIS_CHECKS: Final[tuple[str, ...]] = (CHECK_IMPORT, CHECK_FOREIGN_HOST, CHECK_SYSTEM_ACCESS)

#: What a rendered adapter, its tests and its evals legitimately import.
ALLOWED_MODULES: Final[frozenset[str]] = frozenset(
    {
        "__future__",
        "json",
        "sys",
        "urllib",
        "urllib.error",
        "urllib.request",
        "urllib.parse",
        "typing",
        "dataclasses",
        "re",
        "math",
        "importlib",
        "importlib.util",
        "os",  # the rendered tests read the base URL from the environment (os.environ)
        "pathlib",
        "unittest",
        "http",
        "http.client",
        "socket",  # urllib's own errors are socket errors
        "time",
    }
)
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)
_URL_RE = re.compile(r"https?://([A-Za-z0-9.\-]+)(?::\d+)?")
_SYSTEM_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("subprocess", re.compile(r"\bsubprocess\b")),
    ("file write", re.compile(r"\bopen\s*\([^)]*[\"'][wax]b?[\"']")),
    (
        "os.system / os.popen / os.remove",
        re.compile(r"\bos\.(system|popen|remove|unlink|rmdir|makedirs|rename)\s*\("),
    ),
    ("shutil", re.compile(r"\bshutil\b")),
    ("ctypes", re.compile(r"\bctypes\b")),
    ("socket connect", re.compile(r"\bsocket\.(socket|create_connection)\s*\(")),
)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _module_allowed(name: str) -> bool:
    if name in ALLOWED_MODULES:
        return True
    top = name.split(".", 1)[0]
    return top in ALLOWED_MODULES and name.startswith(top + ".")


def review_sources(texts: dict[str, str], *, allowed_hosts: Iterable[str]) -> SecurityVerdict:
    """The genesis rules over ``texts`` (relative path -> source), after B35's own review
    with no base (every line is new)."""
    verdict = review_texts(texts, base_texts={})
    # A generated adapter's ONE declared URL is the interface it was researched from:
    # B35's egress rule would name it; here the host allowlist decides instead.
    hosts = {h.lower() for h in allowed_hosts}
    verdict.findings = [f for f in verdict.findings if f.check != CHECK_NETWORK]
    for path in sorted(texts):
        text = texts[path]
        for match in _IMPORT_RE.finditer(text):
            module = match.group(1) or match.group(2) or ""
            if module and not _module_allowed(module):
                verdict.findings.append(
                    SecurityFinding(CHECK_IMPORT, path, _line_of(text, match.start()), module)
                )
        for match in _URL_RE.finditer(text):
            host = match.group(1).lower()
            if host not in hosts:
                verdict.findings.append(
                    SecurityFinding(CHECK_FOREIGN_HOST, path, _line_of(text, match.start()), host)
                )
        for label, pattern in _SYSTEM_PATTERNS:
            for match in pattern.finditer(text):
                verdict.findings.append(
                    SecurityFinding(CHECK_SYSTEM_ACCESS, path, _line_of(text, match.start()), label)
                )
    return verdict


def review_layout(layout: SkillLayout, *, allowed_hosts: Iterable[str]) -> SecurityVerdict:
    """Every Python file the layout holds (src, tests, evals), by its path under the root."""
    texts: dict[str, str] = {}
    root = Path(layout.root)
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        texts[path.relative_to(root).as_posix()] = path.read_text(
            encoding="utf-8", errors="replace"
        )
    verdict = review_sources(texts, allowed_hosts=allowed_hosts)
    verdict.reviewed_paths = sorted(texts)
    return verdict
