"""The App Factory's structural lint (B40 req 438): a pure-Python pass over generated
files, run on the Cloud Core BEFORE anything reaches the device. It is deliberately
narrow and says so - the shape of the code (balanced brackets, no tabs, no over-long
lines, no ``eval``/``new Function``/``document.write``/``with``), the page (a language,
a charset, matched script tags), and every JSON file parsing. A syntax check under the
real runtime is the device's ``project.test`` and the lab's; this catches what a template
or a model most often gets wrong, with the file and the line.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Final

from app.appfactory.generator import ProjectFiles

MAX_LINE_CHARS: Final = 140
_OPEN: Final = {"(": ")", "[": "]", "{": "}"}
_CLOSE: Final = {v: k for k, v in _OPEN.items()}
_FORBIDDEN_JS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("eval()", re.compile(r"(?<![\w.])eval\s*\(")),
    ("new Function()", re.compile(r"\bnew\s+Function\s*\(")),
    ("document.write()", re.compile(r"\bdocument\.write\s*\(")),
    ("with statement", re.compile(r"(?<![\w.])with\s*\(")),
    ("child_process", re.compile(r"require\s*\(\s*[\"']child_process[\"']\s*\)")),
    ("vm module", re.compile(r"require\s*\(\s*[\"']vm[\"']\s*\)")),
)
_SCRIPT_OPEN: Final = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_SCRIPT_CLOSE: Final = re.compile(r"</script\s*>", re.IGNORECASE)


@dataclass(slots=True)
class LintFinding:
    path: str
    line: int
    severity: str  # error | warning
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(slots=True)
class LintReport:
    findings: list[LintFinding] = field(default_factory=list)
    files: int = 0

    @property
    def errors(self) -> list[LintFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "files": self.files,
            "errors": len(self.errors),
            "warnings": len(self.findings) - len(self.errors),
            "findings": [f.as_dict() for f in self.findings[:50]],
        }


def _strip_js(text: str) -> str:
    """The code with string literals, comments and regex literals blanked (their content
    kept the same length, so line numbers survive) - the brackets left are the code's."""
    out: list[str] = []
    i = 0
    n = len(text)
    state = "code"
    quote = ""
    prev_significant = ""
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line_comment"
                out.append("  ")
                i += 2
                continue
            if c == "/" and nxt == "*":
                state = "block_comment"
                out.append("  ")
                i += 2
                continue
            if c in ("'", '"', "`"):
                state = "string"
                quote = c
                out.append(c)
                i += 1
                continue
            if c == "/" and prev_significant in (
                "",
                "(",
                ",",
                "=",
                ":",
                "[",
                "!",
                "&",
                "|",
                "?",
                "{",
                "}",
                ";",
                "return",
            ):
                state = "regex"
                out.append(c)
                i += 1
                continue
            if not c.isspace():
                prev_significant = c
            out.append(c)
            i += 1
            continue
        if state == "line_comment":
            if c == "\n":
                state = "code"
                out.append(c)
            else:
                out.append(" ")
            i += 1
            continue
        if state == "block_comment":
            if c == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
            continue
        if state == "string":
            if c == "\\":
                out.append("  ")
                i += 2
                continue
            if c == quote:
                state = "code"
                out.append(c)
            else:
                out.append("\n" if c == "\n" else " ")
            i += 1
            continue
        if state == "regex":
            if c == "\\":
                out.append("  ")
                i += 2
                continue
            if c == "[":
                # a character class may hold an unescaped '/'
                j = text.find("]", i + 1)
                if j == -1:
                    j = i
                out.append(" " * (j - i + 1))
                i = j + 1
                continue
            if c == "/" or c == "\n":
                state = "code"
            out.append(c if c in ("/", "\n") else " ")
            i += 1
            continue
    return "".join(out)


def _lint_js(path: str, text: str, report: LintReport) -> None:
    stripped = _strip_js(text)
    stack: list[tuple[str, int]] = []
    line = 1
    for ch in stripped:
        if ch == "\n":
            line += 1
        elif ch in _OPEN:
            stack.append((ch, line))
        elif ch in _CLOSE:
            if not stack or stack[-1][0] != _CLOSE[ch]:
                report.findings.append(LintFinding(path, line, "error", f"unbalanced '{ch}'"))
                return
            stack.pop()
    if stack:
        ch, at = stack[-1]
        report.findings.append(LintFinding(path, at, "error", f"'{ch}' is never closed"))
    for label, pattern in _FORBIDDEN_JS:
        # The require() forms name their module in a string literal, which the stripped
        # text has blanked: those two are read off the raw text.
        haystack = text if label in ("child_process", "vm module") else stripped
        for match in pattern.finditer(haystack):
            report.findings.append(
                LintFinding(
                    path,
                    haystack.count("\n", 0, match.start()) + 1,
                    "error",
                    f"{label} is not allowed",
                )
            )
    if not text.lstrip().startswith(("//", "(function", '"use strict"', "'use strict'", "/*")):
        report.findings.append(
            LintFinding(
                path, 1, "warning", "a source file should open with a comment or 'use strict'"
            )
        )


def _lint_html(path: str, text: str, report: LintReport) -> None:
    lowered = text.lower()
    if "<html" in lowered and 'lang="' not in lowered:
        report.findings.append(LintFinding(path, 1, "error", "<html> needs a lang attribute"))
    if "<head" in lowered and "<meta charset" not in lowered:
        report.findings.append(LintFinding(path, 1, "error", "<head> needs a <meta charset>"))
    opened = len(_SCRIPT_OPEN.findall(text))
    closed = len(_SCRIPT_CLOSE.findall(text))
    if opened != closed:
        report.findings.append(
            LintFinding(path, 1, "error", f"{opened} <script> against {closed} </script>")
        )
    if re.search(r"\bon(?:click|load|error|submit)\s*=", lowered):
        report.findings.append(
            LintFinding(path, 1, "warning", "inline event handlers; prefer addEventListener")
        )


def _lint_json(path: str, text: str, report: LintReport) -> None:
    try:
        json.loads(text)
    except ValueError as exc:
        report.findings.append(
            LintFinding(path, getattr(exc, "lineno", 1), "error", f"not JSON: {exc.msg[:80]}")
        )


def lint_files(files: ProjectFiles) -> LintReport:
    report = LintReport(files=len(files))
    for entry in files.files:
        text = entry.text
        for number, raw in enumerate(text.split("\n"), start=1):
            if "\t" in raw:
                report.findings.append(LintFinding(entry.path, number, "error", "a tab character"))
            if len(raw) > MAX_LINE_CHARS:
                report.findings.append(
                    LintFinding(entry.path, number, "warning", f"line longer than {MAX_LINE_CHARS}")
                )
            if raw != raw.rstrip():
                report.findings.append(
                    LintFinding(entry.path, number, "warning", "trailing whitespace")
                )
        if entry.path.endswith(".js"):
            _lint_js(entry.path, text, report)
        elif entry.path.endswith((".html", ".htm")):
            _lint_html(entry.path, text, report)
        elif entry.path.endswith(".json"):
            _lint_json(entry.path, text, report)
    return report


__all__ = ["MAX_LINE_CHARS", "LintFinding", "LintReport", "lint_files"]
