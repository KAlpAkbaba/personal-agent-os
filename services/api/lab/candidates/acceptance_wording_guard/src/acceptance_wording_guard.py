"""Acceptance Wording Guard — find acceptance checks that depend on generated prose.

SHADOW candidate. Nothing in ``services/api/app`` imports this module, and
nothing should: it is lab code living outside the shipped wheel
(``pyproject.toml`` publishes only ``packages = ["app"]``) and outside every
tree ``app/evolution/sandbox.py`` protects. See ``../SPEC.md`` for why it
exists and ``../THREAT_MODEL.md`` for what it could get wrong.

What it looks for
-----------------

The defect class is *acceptance evidence that is a paraphrase*: a
qualification check that passes or fails on the exact words a generator chose,
rather than on structure the system can prove (ids, counts, provenance, enum
values). ADR-0051 addendum 4 states the rule this checker enforces —
**generated wording is never acceptance evidence** — after the check
``speech_head -like "Efendim, son ara*"`` failed a working system because the
briefing had been made shorter and better worded the same day.

The detector is deliberately **subject-driven, not literal-driven**. A great
many honest assertions in this repository compare against multi-word English
strings — ``$result.StdOut -match "must be replaced"``, ``$_.Exception.Message
-match "not enrolled"`` — and those strings are *deterministic text this
repository authors itself*. Flagging them would be the exact failure the
threat model calls out: noise that teaches the owner to ignore the checker. So
a finding needs BOTH

1. a subject that names a generation surface (``speech``, ``speech_head``,
   ``narration``, ``briefing``, ``factual_summary``, ``summary``, ``text``…), and
2. a literal that looks like prose rather than a machine token,

with one subject-independent exception: a prose literal containing
Turkish-specific letters, because the only Turkish natural language that ever
appears inside an assertion literal in this repository is assistant output.

An exact-equality comparison against a short fixed machine token — ``"PASS"``,
``"insufficient_valid_findings"``, ``"evolution.shadow_ready"``, ``"0.4.0"`` —
is explicitly *not* a finding, whatever the subject is.

Allow-list
----------

A deliberate wording assertion is kept by writing, on the same line or the line
directly above::

    # wording-guard: allow <reason>

An allow with no reason is still honoured but reported separately as
``allow_without_reason`` — an allow-list whose entries need no justification
decays into a mute button.

Reporting
---------

A finding carries ``file:line``, the surface, the operator, the subject
expression, the *shape* of the literal (word count, character count, whether it
contains non-ASCII letters, whether it uses a wildcard, and a SHA-256 prefix so
two runs can be correlated) and a suggested structural alternative. **The
literal itself is never emitted**: this checker reads the owner's acceptance
scripts, and its output is a report that may be pasted anywhere.

Bounds
------

Pure stdlib. No network, no subprocess, no writes. Every regex is bounded and
backtracking-free; lines are truncated before matching; oversized, binary and
symlinked files are skipped and counted rather than read.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

GUARD_VERSION: Final[str] = "0.1.0"

#: The acceptance surfaces this guard was built for (repo-root relative).
DEFAULT_SURFACES: Final[tuple[str, ...]] = ("scripts", "services/api/tests")

#: File kinds the guard understands. Anything else is not opened at all.
SURFACE_FOR_SUFFIX: Final[dict[str, str]] = {
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".py": "python",
}

#: Never descend into these, whatever the root is.
EXCLUDED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        ".next",
        ".work",
        ".claude",
    }
)

MAX_FILE_BYTES: Final[int] = 1 << 20  # 1 MiB
MAX_LINE_CHARS: Final[int] = 2000
MAX_FILES: Final[int] = 20_000
MAX_LITERAL_CHARS: Final[int] = 400
BINARY_SNIFF_BYTES: Final[int] = 8192

# --------------------------------------------------------------- generation surfaces

#: Identifiers that name a *generated* natural-language value. A comparison
#: whose subject contains one of these, against prose, is the defect.
STRONG_SURFACES: Final[frozenset[str]] = frozenset(
    {
        "speech",
        "speech_head",
        "speechhead",
        "speech_text",
        "spoken",
        "spoken_text",
        "narration",
        "narration_text",
        "narrated",
        "briefing",
        "briefing_text",
        "factual_summary",
        "utterance",
        "assistant_text",
        "assistant_reply",
        "explanation",
        "explain_text",
        "tts",
        "tts_text",
        "say_text",
    }
)

#: Weaker signals: often generated, sometimes not. Prose against one of these
#: is reported at ``medium`` so the owner can judge rather than be alarmed.
WEAK_SURFACES: Final[frozenset[str]] = frozenset(
    {
        "summary",
        "text",
        "answer",
        "reply",
        "response",
        "response_text",
        "sentence",
        "sentences",
        "transcript",
        "narrative",
    }
)

#: Letters that only occur in this repository's Turkish assistant output.
TURKISH_LETTERS: Final[frozenset[str]] = frozenset("ğüşıöçĞÜŞİÖÇ")

SEVERITY_HIGH: Final[str] = "high"
SEVERITY_MEDIUM: Final[str] = "medium"
SEVERITY_LOW: Final[str] = "low"

SEVERITY_ORDER: Final[dict[str, int]] = {SEVERITY_LOW: 0, SEVERITY_MEDIUM: 1, SEVERITY_HIGH: 2}

RULE_GENERATED_WORDING: Final[str] = "generated_wording_assertion"
RULE_POSSIBLE_WORDING: Final[str] = "possible_wording_assertion"
RULE_TURKISH_PROSE: Final[str] = "turkish_prose_assertion"

_RULE_SEVERITY: Final[dict[str, str]] = {
    RULE_GENERATED_WORDING: SEVERITY_HIGH,
    RULE_POSSIBLE_WORDING: SEVERITY_MEDIUM,
    # Subject-independent heuristic. Measured against this repository it is the
    # noisiest rule by a wide margin (deterministic normalizer output, fixed
    # Turkish test fixtures), so it is `low` and is filtered out by the default
    # `min_severity`. See NOTES.md — a checker that cries wolf is worse than no
    # checker, because the owner learns to skip its output.
    RULE_TURKISH_PROSE: SEVERITY_LOW,
}

#: A Turkish-prose heuristic hit shorter than this is almost always a fixed
#: test fixture ("Görev tamam"), not a generated sentence.
_HEURISTIC_MIN_WORDS: Final[int] = 4
_HEURISTIC_MIN_CHARS: Final[int] = 20

#: Where the assertion lives, which is what decides how much it costs to be
#: wrong. ADR-0051 addendum 4 is about *acceptance* evidence: a qualification
#: run that fails on phrasing costs the owner a real session. A unit test of
#: the sentence renderer that pins its output is a different thing — arguably
#: the renderer's specification — and reporting the two at the same severity is
#: how a checker teaches its owner to stop reading it.
CONTEXT_QUALIFICATION: Final[str] = "qualification"
CONTEXT_TEST: Final[str] = "test"
CONTEXT_SOURCE: Final[str] = "source"

#: Contexts whose findings are demoted one severity step.
_DEMOTED_CONTEXTS: Final[frozenset[str]] = frozenset({CONTEXT_TEST})

_DEMOTE: Final[dict[str, str]] = {
    SEVERITY_HIGH: SEVERITY_MEDIUM,
    SEVERITY_MEDIUM: SEVERITY_LOW,
    SEVERITY_LOW: SEVERITY_LOW,
}


def context_for(path: str) -> str:
    """Classify a repo-relative path as an acceptance, test or source surface."""
    parts = path.replace("\\", "/").split("/")
    if parts and parts[0] == "scripts":
        # Everything under scripts/ is an owner command or its regression
        # harness — the surface where a wording-dependent check costs a run.
        return CONTEXT_QUALIFICATION
    if any(part in {"tests", "test"} for part in parts[:-1]):
        return CONTEXT_TEST
    return CONTEXT_SOURCE


_SUGGESTION: Final[dict[str, str]] = {
    RULE_GENERATED_WORDING: (
        "Assert on the structure the sentence was built from, not the sentence: "
        "the provenance block's cited event ids, the job/session id they belong "
        "to, the evidence kinds, and the numbers themselves (findings, sources, "
        "rejected, verdict). Paraphrasing must stay free; an unsupported claim "
        "must not."
    ),
    RULE_POSSIBLE_WORDING: (
        "If this value is generated, assert on its provenance/ids/counts "
        "instead. If it is a fixed machine token the repository authors, "
        "compare it to that token exactly, or mark the line "
        "'# wording-guard: allow <reason>'."
    ),
    RULE_TURKISH_PROSE: (
        "Turkish prose inside an assertion literal is almost always assistant "
        "output. Assert on the record the sentence was rendered from (ids, "
        "counts, enum status), and let the phrasing change freely."
    ),
}

ALLOW_MARKER: Final[str] = "wording-guard: allow"
_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"#\s{0,4}wording-guard:\s{0,4}allow(?P<reason>[^\r\n]{0,200})", re.IGNORECASE
)

# ------------------------------------------------------------------- literal shapes

LITERAL_PROSE: Final[str] = "prose"
LITERAL_MACHINE_TOKEN: Final[str] = "machine_token"
LITERAL_PATTERN: Final[str] = "pattern"
LITERAL_SHORT_TEXT: Final[str] = "short_text"
LITERAL_EMPTY: Final[str] = "empty"

#: Characters that mean "this literal is a structural pattern, not a sentence".
_PATTERN_CHARS: Final[frozenset[str]] = frozenset("^$[]()|+{}\\")

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_.:/-]{1,80}")
_WORD_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[\s]{1,64}")


def _has_turkish_letter(text: str) -> bool:
    return any(ch in TURKISH_LETTERS for ch in text)


def _has_non_ascii_letter(text: str) -> bool:
    return any(ord(ch) > 127 and unicodedata.category(ch).startswith("L") for ch in text)


def _words(text: str) -> list[str]:
    """Words, with PowerShell wildcards treated as word separators."""
    spaced = text.replace("*", " ").replace("?", " ")
    return [w for w in _WORD_SPLIT_RE.split(spaced) if w]


def classify_literal(raw: str) -> str:
    """Prose, a machine token, a structural pattern, short text, or empty.

    Pure and total: every string gets exactly one of the five labels, and no
    branch depends on anything outside its argument.
    """
    text = raw.strip()
    if not text:
        return LITERAL_EMPTY
    if any(ch in _PATTERN_CHARS for ch in text):
        return LITERAL_PATTERN
    words = _words(text)
    if len(words) <= 1:
        return LITERAL_MACHINE_TOKEN if _TOKEN_RE.fullmatch(text) else LITERAL_SHORT_TEXT
    if len(words) >= 3:
        return LITERAL_PROSE
    # Exactly two words: prose only when it carries non-ASCII letters, which in
    # this repository means Turkish narration rather than a two-token constant.
    return LITERAL_PROSE if _has_non_ascii_letter(text) else LITERAL_SHORT_TEXT


def literal_shape(raw: str) -> dict[str, Any]:
    """The reportable shape of a literal. Never the literal itself."""
    text = raw.strip()
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
    return {
        "kind": classify_literal(raw),
        "words": len(_words(text)),
        "chars": len(text),
        "non_ascii": _has_non_ascii_letter(text),
        "turkish": _has_turkish_letter(text),
        "wildcard": ("*" in text or "?" in text),
        "digest": f"sha256:{digest}",
    }


# ------------------------------------------------------------------------- findings


@dataclass(frozen=True, slots=True)
class Finding:
    """One acceptance assertion that rests on generated wording."""

    path: str
    line: int
    surface: str
    context: str
    rule: str
    severity: str
    subject: str
    operator: str
    shape: dict[str, Any]
    suggestion: str

    def key(self) -> tuple[str, int, str, str]:
        return (self.path, self.line, self.subject, str(self.shape.get("digest")))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AllowedFinding:
    """A finding a human deliberately kept, with the reason they gave."""

    path: str
    line: int
    rule: str
    reason: str
    subject: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allow_without_reason"] = not self.reason
        return payload


@dataclass
class ScanReport:
    root: str
    surfaces: list[str]
    min_severity: str = SEVERITY_MEDIUM
    findings: list[Finding] = field(default_factory=list)
    allowed: list[AllowedFinding] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    suppressed: list[dict[str, Any]] = field(default_factory=list)
    files_scanned: int = 0
    bytes_scanned: int = 0
    duration_s: float = 0.0
    truncated: bool = False

    @property
    def counts(self) -> dict[str, int]:
        out = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 0, SEVERITY_LOW: 0}
        for finding in self.findings:
            out[finding.severity] = out.get(finding.severity, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "guard_version": GUARD_VERSION,
            "root": self.root,
            "surfaces": list(self.surfaces),
            "min_severity": self.min_severity,
            "files_scanned": self.files_scanned,
            "bytes_scanned": self.bytes_scanned,
            "duration_s": round(self.duration_s, 4),
            "truncated": self.truncated,
            "counts": self.counts,
            "findings": [f.to_dict() for f in self.findings],
            "allowed": [a.to_dict() for a in self.allowed],
            "suppressed": list(self.suppressed),
            "skipped": list(self.skipped),
        }


# ------------------------------------------------------------------- rule decision


def _surface_names(subject: str) -> set[str]:
    """Every identifier in a subject expression, plus its ``_``-separated parts.

    Whole identifiers are kept as well as their parts so ``factual_summary``
    can be strong while its part ``summary`` is only weak, and so a helper
    named ``speech_for`` or ``briefing_service`` is recognised — the first
    real run of this guard missed ``briefing_service.speech_for(...)``
    precisely because it compared whole identifiers only.
    """
    names: set[str] = set()
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{0,63}", subject):
        lowered = token.lower()
        names.add(lowered)
        names.update(part for part in lowered.split("_") if part)
    return names


def _rule_for(subject: str, raw_literal: str) -> str | None:
    """Which rule (if any) this (subject, literal) pair trips. Pure."""
    if classify_literal(raw_literal) != LITERAL_PROSE:
        return None
    names = _surface_names(subject)
    if names & STRONG_SURFACES:
        return RULE_GENERATED_WORDING
    if names & WEAK_SURFACES:
        return RULE_POSSIBLE_WORDING
    text = raw_literal.strip()
    if (
        _has_turkish_letter(text)
        and len(_words(text)) >= _HEURISTIC_MIN_WORDS
        and len(text) >= _HEURISTIC_MIN_CHARS
    ):
        return RULE_TURKISH_PROSE
    return None


def _make_finding(
    *, path: str, line: int, surface: str, subject: str, operator: str, raw_literal: str
) -> Finding | None:
    rule = _rule_for(subject, raw_literal)
    if rule is None:
        return None
    context = context_for(path)
    severity = _RULE_SEVERITY[rule]
    if context in _DEMOTED_CONTEXTS:
        severity = _DEMOTE[severity]
    return Finding(
        path=path,
        line=line,
        surface=surface,
        context=context,
        rule=rule,
        severity=severity,
        subject=_ascii_safe(subject)[:120],
        operator=operator,
        shape=literal_shape(raw_literal),
        suggestion=_SUGGESTION[rule],
    )


# ------------------------------------------------------------------ allow-list


#: A variable holding the *source text of a file* is not a generation surface,
#: however it is named. `scripts/tests/owner-explain.tests.ps1` reads
#: `owner-explain.ps1` into `$text` and greps it for its own assertion messages;
#: the first real run reported all five of those as wording assertions, which is
#: precisely the false positive that would teach the owner to ignore this tool.
_PS_FILE_READ_RE: Final[re.Pattern[str]] = re.compile(
    r"\$(?P<name>[A-Za-z_][A-Za-z0-9_]{0,63})\s{0,4}=[^\r\n]{0,200}?"
    r"(?:ReadAllText|ReadAllLines|ReadAllBytes|Get-Content|Out-String)",
    re.IGNORECASE,
)
_PY_FILE_READ_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]{0,64}(?P<name>[A-Za-z_][A-Za-z0-9_]{0,63})\s{0,4}=[^\r\n]{0,200}?"
    r"(?:\.read_text\(|\.read_bytes\(|\.read\(\)|\.readlines\(|\bopen\()",
)

_ROOT_IDENT_RE: Final[re.Pattern[str]] = re.compile(r"\$?([A-Za-z_][A-Za-z0-9_]{0,63})")


def _file_content_variables(lines: Sequence[str], surface: str) -> frozenset[str]:
    """Variables in this file that were assigned the contents of a file."""
    pattern = _PS_FILE_READ_RE if surface == "powershell" else _PY_FILE_READ_RE
    names: set[str] = set()
    for raw_line in lines:
        match = pattern.search(raw_line[:MAX_LINE_CHARS])
        if match:
            names.add(match.group("name").lower())
    return frozenset(names)


def _root_identifier(subject: str) -> str:
    match = _ROOT_IDENT_RE.match(subject.strip())
    return match.group(1).lower() if match else ""


def _allow_reason(lines: Sequence[str], line_no: int) -> str | None:
    """The allow reason for a 1-based line, or None when it is not allowed.

    Looks at the line itself and the line directly above it, so an allow can be
    written inline or on its own line before a long assertion.
    """
    for index in (line_no - 1, line_no - 2):
        if 0 <= index < len(lines):
            match = _ALLOW_RE.search(lines[index])
            if match:
                return match.group("reason").strip(" :-\t")
    return None


# ------------------------------------------------------------------- PowerShell


def _ps_pattern(suffix: str, literal_first: bool) -> re.Pattern[str]:
    subject = (
        rf"(?P<subject{suffix}>\$[A-Za-z_][A-Za-z0-9_]{{0,63}}"
        rf"(?:\.[A-Za-z_][A-Za-z0-9_]{{0,63}}){{0,6}}"
        rf"(?:\[[^\]\r\n]{{0,40}}\])?)"
    )
    op = rf"(?P<op{suffix}>-(?:c|i)?(?:not)?(?:like|match|eq|ne))"
    lit = (
        rf'(?:"(?P<dq{suffix}>[^"\r\n]{{0,{MAX_LITERAL_CHARS}}})"'
        rf"|'(?P<sq{suffix}>[^'\r\n]{{0,{MAX_LITERAL_CHARS}}})')"
    )
    gap = r"[ \t]{1,8}"
    body = (lit + gap + op + gap + subject) if literal_first else (subject + gap + op + gap + lit)
    return re.compile(body, re.IGNORECASE)


_PS_FORWARD: Final[re.Pattern[str]] = _ps_pattern("f", literal_first=False)
_PS_REVERSE: Final[re.Pattern[str]] = _ps_pattern("r", literal_first=True)


def _scan_powershell(path: str, lines: Sequence[str]) -> Iterator[tuple[int, str, str, str]]:
    """Yield ``(line_no, subject, operator, literal)`` for every comparison."""
    for index, raw_line in enumerate(lines):
        line = raw_line[:MAX_LINE_CHARS]
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for pattern, suffix in ((_PS_FORWARD, "f"), (_PS_REVERSE, "r")):
            for match in pattern.finditer(line):
                literal = match.group(f"dq{suffix}")
                if literal is None:
                    literal = match.group(f"sq{suffix}") or ""
                yield (
                    index + 1,
                    _clean_powershell_subject(match.group(f"subject{suffix}")),
                    match.group(f"op{suffix}").lower(),
                    literal,
                )


# ----------------------------------------------------------------------- Python

_PY_TEXT_METHODS: Final[frozenset[str]] = frozenset({"startswith", "endswith", "__contains__"})

_PY_OPERATOR: Final[dict[type[ast.cmpop], str]] = {
    ast.In: "in",
    ast.NotIn: "not in",
    ast.Eq: "==",
    ast.NotEq: "!=",
}


_MAX_SUBJECT_DEPTH: Final[int] = 12


def _subject_path(node: ast.AST, depth: int = 0) -> str:
    """Render a subject as an identifier path — never as source text.

    ``ast.unparse`` was the obvious choice and was wrong: it reproduces string
    arguments verbatim, so a subject like ``normalize('1.234,56 ₺')`` put owner
    content (and a character the Windows console could not encode) into a
    report this checker promises never to quote. This renderer emits only
    identifiers, ``()`` for a call and ``[key]`` for an identifier-shaped
    subscript key; everything else collapses to ``...``.
    """
    if depth >= _MAX_SUBJECT_DEPTH:
        return "..."
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_subject_path(node.value, depth + 1)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{_subject_path(node.func, depth + 1)}()"
    if isinstance(node, ast.Subscript):
        key = _str_constant(node.slice)
        inner = key if (key is not None and key.isidentifier()) else "..."
        return f"{_subject_path(node.value, depth + 1)}[{inner}]"
    return f"<{type(node).__name__.lower()}>"


_PS_BRACKET_RE: Final[re.Pattern[str]] = re.compile(r"\[[^\]\r\n]{0,40}\]")


def _clean_powershell_subject(subject: str) -> str:
    """Collapse a PowerShell index expression unless its key is an identifier."""

    def _replace(match: re.Match[str]) -> str:
        inner = match.group(0)[1:-1].strip().strip("'\"")
        return f"[{inner}]" if inner.isidentifier() else "[...]"

    return _PS_BRACKET_RE.sub(_replace, subject)


def _ascii_safe(text: str) -> str:
    """Last line of defence: a report field never carries non-ASCII content."""
    return "".join(ch if 32 <= ord(ch) < 127 else "." for ch in text)


def _str_constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _scan_python_tree(tree: ast.AST) -> Iterator[tuple[int, str, str, str]]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        for inner in ast.walk(node.test):
            if isinstance(inner, ast.Compare):
                yield from _compare_pairs(inner)
            elif isinstance(inner, ast.Call):
                yield from _call_pairs(inner)


def _compare_pairs(node: ast.Compare) -> Iterator[tuple[int, str, str, str]]:
    left: ast.expr = node.left
    for op, right in zip(node.ops, node.comparators, strict=False):
        name = _PY_OPERATOR.get(type(op))
        if name is not None:
            left_literal = _str_constant(left)
            right_literal = _str_constant(right)
            if left_literal is not None and right_literal is None:
                yield (node.lineno, _subject_path(right), name, left_literal)
            elif right_literal is not None and left_literal is None:
                yield (node.lineno, _subject_path(left), name, right_literal)
        left = right


def _call_pairs(node: ast.Call) -> Iterator[tuple[int, str, str, str]]:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _PY_TEXT_METHODS:
        return
    for arg in node.args:
        literal = _str_constant(arg)
        if literal is not None:
            yield (node.lineno, _subject_path(func.value), func.attr, literal)


# ------------------------------------------------------------------- file walking


def _iter_files(root: Path, surfaces: Iterable[str]) -> Iterator[Path]:
    """Every readable candidate file under ``root``/``surface``, deterministically.

    A path that escapes ``root`` (a surface built from ``..``, a symlink out of
    the tree) is never yielded: this checker reads the owner's scripts, so
    where it may read is a property of the code and not of its caller.
    """
    seen: set[Path] = set()
    for surface in surfaces:
        base = (root / surface).resolve()
        if not base.is_relative_to(root) or not base.exists():
            continue
        if base.is_file():
            candidates: Iterable[Path] = [base]
        else:
            candidates = sorted(base.rglob("*"))
        for candidate in candidates:
            if candidate in seen:
                continue
            if candidate.suffix.lower() not in SURFACE_FOR_SUFFIX:
                continue
            # Excludes are matched against the path RELATIVE to the root. The
            # absolute path is not safe to match on: this repository is often
            # checked out inside a directory that is itself on the exclude list
            # (a `.claude/worktrees/...` worktree), which silently scanned zero
            # files the first time this ran.
            if any(part in EXCLUDED_DIRS for part in candidate.relative_to(root).parts):
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            seen.add(candidate)
            yield candidate


def _read_text(path: Path) -> tuple[str | None, str | None, int]:
    """``(text, skip_reason, size)``. Never raises for an unreadable file."""
    try:
        size = path.stat().st_size
    except OSError:
        return None, "stat_failed", 0
    if size > MAX_FILE_BYTES:
        return None, "too_large", size
    try:
        blob = path.read_bytes()
    except OSError:
        return None, "unreadable", size
    if b"\x00" in blob[:BINARY_SNIFF_BYTES]:
        return None, "binary", size
    return blob.decode("utf-8", errors="replace"), None, size


# ------------------------------------------------------------------------- scan


def scan_text(
    path: str,
    text: str,
    surface: str,
    *,
    min_severity: str = SEVERITY_MEDIUM,
    suppressed: list[dict[str, Any]] | None = None,
) -> tuple[list[Finding], list[AllowedFinding]]:
    """Scan one already-read file. Pure over its arguments; opens nothing."""
    floor = SEVERITY_ORDER.get(min_severity, SEVERITY_ORDER[SEVERITY_MEDIUM])
    lines = text.splitlines()
    if surface == "python":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return [], []
        pairs: Iterable[tuple[int, str, str, str]] = _scan_python_tree(tree)
    else:
        pairs = _scan_powershell(path, lines)

    content_vars = _file_content_variables(lines, surface)
    findings: list[Finding] = []
    allowed: list[AllowedFinding] = []
    seen: set[tuple[str, int, str, str]] = set()
    for line_no, subject, operator, literal in pairs:
        finding = _make_finding(
            path=path,
            line=line_no,
            surface=surface,
            subject=subject,
            operator=operator,
            raw_literal=literal,
        )
        if finding is None:
            continue
        if SEVERITY_ORDER[finding.severity] < floor:
            continue
        if finding.key() in seen:
            continue
        # Suppression is counted only for findings that would otherwise have
        # been reported, so `suppressed` measures avoided false positives
        # rather than "comparisons this file happens to contain".
        if _root_identifier(subject) in content_vars:
            seen.add(finding.key())
            if suppressed is not None:
                suppressed.append(
                    {
                        "path": path,
                        "line": line_no,
                        "rule": finding.rule,
                        "reason": "subject_holds_file_contents",
                    }
                )
            continue
        seen.add(finding.key())
        reason = _allow_reason(lines, line_no)
        if reason is not None:
            allowed.append(
                AllowedFinding(
                    path=path,
                    line=line_no,
                    rule=finding.rule,
                    reason=reason,
                    subject=finding.subject,
                )
            )
            continue
        findings.append(finding)
    return findings, allowed


def scan_paths(
    root: Path | str,
    *,
    surfaces: Sequence[str] = DEFAULT_SURFACES,
    max_files: int = MAX_FILES,
    min_severity: str = SEVERITY_MEDIUM,
) -> ScanReport:
    """Scan the acceptance surfaces under ``root`` and report what they rest on."""
    resolved = Path(root).resolve()
    report = ScanReport(root=str(resolved), surfaces=list(surfaces), min_severity=min_severity)
    started = time.perf_counter()
    for path in _iter_files(resolved, surfaces):
        if report.files_scanned >= max_files:
            report.truncated = True
            break
        rel = path.relative_to(resolved).as_posix()
        text, skip_reason, size = _read_text(path)
        if text is None:
            report.skipped.append({"path": rel, "reason": skip_reason, "bytes": size})
            continue
        report.files_scanned += 1
        report.bytes_scanned += size
        found, allowed = scan_text(
            rel,
            text,
            SURFACE_FOR_SUFFIX[path.suffix.lower()],
            min_severity=min_severity,
            suppressed=report.suppressed,
        )
        report.findings.extend(found)
        report.allowed.extend(allowed)
    report.duration_s = time.perf_counter() - started
    report.findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.path, f.line))
    report.allowed.sort(key=lambda a: (a.path, a.line))
    return report


# -------------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="acceptance-wording-guard",
        description=(
            "Report acceptance assertions that depend on generated natural-language "
            "wording rather than on structure. Reads only; writes nothing."
        ),
    )
    parser.add_argument("--root", default=".", help="repository root to scan")
    parser.add_argument(
        "--surface",
        action="append",
        default=None,
        help="root-relative surface to scan (repeatable); defaults to scripts + api tests",
    )
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument(
        "--min-severity",
        choices=(SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH),
        default=SEVERITY_MEDIUM,
        help="lowest severity to report; 'low' turns on the noisy Turkish-prose heuristic",
    )
    parser.add_argument(
        "--fail-on",
        choices=("never", "high", "any"),
        default="never",
        help="exit non-zero when findings at or above this severity exist",
    )
    args = parser.parse_args(argv)

    report = scan_paths(
        args.root,
        surfaces=tuple(args.surface or DEFAULT_SURFACES),
        min_severity=args.min_severity,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=True))
    else:
        for finding in report.findings:
            print(
                f"{finding.path}:{finding.line}: {finding.severity} [{finding.context}] "
                f"{finding.rule} "
                f"subject={finding.subject} op={finding.operator} "
                f"literal={finding.shape['kind']}"
                f"/{finding.shape['words']}w/{finding.shape['chars']}c"
                f"/{finding.shape['digest']}"
            )
        counts = report.counts
        print(
            f"scanned {report.files_scanned} files in {report.duration_s:.2f}s: "
            f"{counts[SEVERITY_HIGH]} high, {counts[SEVERITY_MEDIUM]} medium, "
            f"{counts[SEVERITY_LOW]} low, "
            f"{len(report.allowed)} allowed, {len(report.suppressed)} suppressed, "
            f"{len(report.skipped)} skipped"
        )
    if args.fail_on == "any" and report.findings:
        return 1
    if args.fail_on == "high" and report.counts[SEVERITY_HIGH]:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
