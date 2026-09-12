"""B01 req 24: a proof mark must point at something, not just assert something.

``PROVEN_REAL`` is the strongest claim this project makes, and for a long stretch of its
history the evidence column for some rows was a sentence: "2026-09-01 final run, unattended."
A sentence cannot be re-checked, cannot be found again after the machine is rebuilt, and
cannot be wrong in a way anybody notices. The 2026-09-12 audit counted the rows that carry
nothing a machine can follow.

The rule from here: a row carrying a proof mark must name at least one thing that EXISTS in
this repository - a test, a symbol, a script, an evidence artifact. The rows that predate the
rule are listed below, by id, and the list may only shrink: it is the debt, written down,
rather than a rule quietly not applied.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
QUALIFICATION = REPO_ROOT / "docs" / "QUALIFICATION.md"

PROOF_MARKS = ("PROVEN_REAL", "PROVEN_AUTOMATED", "PROVEN_PROXY")

_SKIP_DIRS = {
    ".git",
    ".venv",
    ".next",
    ".claude",
    "node_modules",
    "__pycache__",
    "dist",
    "obj",
    "bin",
    "scratchpad",
}
_INDEXED_SUFFIXES = {
    ".py",
    ".cs",
    ".ps1",
    ".ts",
    ".tsx",
    ".sh",
    ".sql",
    ".json",
    ".yml",
    ".yaml",
    ".md",
}
_MAX_INDEXED_BYTES = 2_000_000
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]{3,}")
_ARTIFACT = re.compile(r"\b[\w./-]+\.(?:json|md|py|cs|ps1|ts|tsx|txt|log|png|zip|exe)\b")
_ROW_ID = re.compile(r"^\|\s*`?(\d+\.\d+[a-z]?)`?\s*\|")

#: Rows that carried a proof mark before this rule existed. Each one is a real claim whose
#: evidence is a sentence about a run that happened - the run was real, the record of it is
#: not re-checkable. They are NOT rewritten (rewriting history to satisfy a new gate is how a
#: record stops being a record); they are named, so the debt is visible and countable.
#:
#: This list may only shrink. Adding an id to it is not allowed by
#: ``test_the_grandfathered_list_only_shrinks``: a NEW prose-only row fails the gate.
GRANDFATHERED_PROSE_ONLY = frozenset(
    {
        "1.11",
        "15.5",
        "16.13",
        "18.3",
        "2.1",
        "2.3",
        "2.6",
        "2.7",
        "2.8",
        "20.1",
        "24.17",
        "5.2",
        "5.3",
        "9.5",
        "9.6",
        "9.8",
    }
)


@lru_cache(maxsize=1)
def _repo_index() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Everything a row could legitimately point at: words in the source, repo-relative paths
    and file names. Built once per session."""
    words: set[str] = set()
    paths: set[str] = set()
    names: set[str] = set()
    # os.walk with the directory list pruned IN PLACE, not rglob with a filter: rglob still
    # descends into .venv, node_modules and the eight checkouts under .claude/worktrees
    # before anything gets a chance to skip them - 167k entries and 2.3 s against 2.1k
    # entries and 0.01 s for the tree this test actually cares about.
    for directory, subdirectories, filenames in os.walk(REPO_ROOT):
        subdirectories[:] = [d for d in subdirectories if d not in _SKIP_DIRS]
        for filename in filenames:
            path = Path(directory) / filename
            relative = path.relative_to(REPO_ROOT).as_posix()
            paths.add(relative)
            names.add(filename)
            if path.suffix not in _INDEXED_SUFFIXES:
                continue
            if relative == "docs/QUALIFICATION.md":
                # The document may not be its own evidence.
                continue
            try:
                if path.stat().st_size > _MAX_INDEXED_BYTES:
                    continue
                text = path.read_text("utf-8", errors="ignore")
            except OSError:
                continue
            words.update(_WORD.findall(text))
    return frozenset(words), frozenset(paths), frozenset(names)


def _points_at_something(evidence: str) -> bool:
    words, paths, names = _repo_index()
    for quoted in re.findall(r"`([^`]+)`", evidence):
        token = quoted.strip()
        if token in paths or token in names or any(p.endswith("/" + token) for p in paths):
            return True
        for piece in _WORD.findall(token):
            if piece in words or piece in paths or piece in names:
                return True
            if piece.split("/")[-1] in names:
                return True
    for artifact in _ARTIFACT.findall(evidence):
        if artifact in paths or artifact.split("/")[-1] in names:
            return True
    return False


@lru_cache(maxsize=1)
def _proof_rows() -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    for line in QUALIFICATION.read_text("utf-8").splitlines():
        head = _ROW_ID.match(line)
        if head is None:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        mark = next((m for m in PROOF_MARKS if m in cells[2]), None)
        if mark is None:
            continue
        rows.append((head.group(1), mark, cells[3]))
    return tuple(rows)


def test_the_qualification_record_is_parsed_at_all() -> None:
    """A checker that silently matches nothing passes for ever."""
    rows = _proof_rows()
    assert len(rows) > 200, f"only {len(rows)} proof-marked rows found; the parser is broken"


def test_every_proof_marked_row_points_at_something_that_exists() -> None:
    offenders = [
        f"{row_id} ({mark}): {evidence[:120]}"
        for row_id, mark, evidence in _proof_rows()
        if row_id not in GRANDFATHERED_PROSE_ONLY and not _points_at_something(evidence)
    ]
    assert not offenders, (
        "these rows claim a proof but name nothing a machine can follow - give each one a "
        "test name, a symbol, a script or an evidence artifact:\n  " + "\n  ".join(offenders)
    )


def test_the_grandfathered_list_only_shrinks() -> None:
    """A row that has since been given real evidence must leave the list.

    Without this the list rots into a place to put new debt: an id that now passes would sit
    there for ever, and the next person would read the length as the size of the problem.
    """
    healed = [
        row_id
        for row_id, _, evidence in _proof_rows()
        if row_id in GRANDFATHERED_PROSE_ONLY and _points_at_something(evidence)
    ]
    assert not healed, (
        "these rows now carry real evidence - remove them from GRANDFATHERED_PROSE_ONLY: "
        + ", ".join(sorted(healed))
    )


def test_the_rule_actually_refuses_a_sentence_and_accepts_a_reference() -> None:
    """The gate proved on itself: without this, a checker that accepted everything would pass
    every test above and forbid nothing.
    """
    # The exact shape of the debt: a real run, described, pointing at nothing.
    assert not _points_at_something("2026-09-01 final run, unattended.")
    assert not _points_at_something("Same run, against the live process (pid checked).")
    # Each of the four things a row is allowed to point at.
    assert _points_at_something("`test_every_proof_marked_row_points_at_something_that_exists`")
    assert _points_at_something("`app/release/schema.py` gates the release")
    assert _points_at_something("see `scripts/cloud/release-cloud-core-bluegreen.sh`")
    assert _points_at_something("docs/evidence/m27-paint-lab-2026-09-09.json")


def test_every_grandfathered_id_is_still_a_row_in_the_record() -> None:
    """A stale id would quietly widen the exception to nothing at all."""
    present = {row_id for row_id, _, _ in _proof_rows()}
    missing = sorted(GRANDFATHERED_PROSE_ONLY - present)
    assert not missing, f"grandfathered ids no longer in QUALIFICATION.md: {missing}"
