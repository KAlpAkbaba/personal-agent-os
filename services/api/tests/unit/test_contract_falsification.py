"""B02 req 30: the guards over the shared contracts, proved by breaking them.

Three artifacts under ``packages/protocol/`` are read by two halves of this system each, in
two different languages. Every expensive defect this repository has had in that seam looked
the same from inside: both suites green, both halves restating the shape to themselves.

- 2026-09-11: the Cloud Core sent ``"test": "dotnet test ..."`` where the device requires an
  object of {key: command}. The device's own test had scaffolded its OWN fixture.
- 2026-09-12 (B01): the release script's fake docker could not fail a migration, so no test
  could see that a failed migration did not stop a release.
- 2026-09-12 (this batch): ``test_markers_match_track_a_protocol_json_when_present`` SKIPPED
  when the shared marker list was absent - deleting the contract turned its guard green.

A guard that has only ever been run against a passing input has proved nothing. So this
module does three things: it holds each artifact to two independent readers, it refuses a
guard that hedges on the artifact's absence, and for the guard that lives in this suite it
actually HIDES the artifact and asserts the guard fails - a mutation, executed, every run.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
PACKAGES = REPO_ROOT / "packages"
PROTOCOL = PACKAGES / "protocol"
API_ROOT = REPO_ROOT / "services" / "api"

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
}
_READER_SUFFIXES = {".py", ".cs", ".ts", ".tsx"}


@dataclass(frozen=True)
class Contract:
    """A shared artifact, the guard that holds a half to it, and why it exists."""

    guard: str
    why: str
    #: The components whose CODE is actually asserted against the file. A component that only
    #: cites the artifact in a comment is not held to it - and the difference between this and
    #: the set of components that merely mention it is the honest measure of a contract's
    #: coverage. `unheld` names what that gap costs, so it cannot be mistaken for done.
    held_by: tuple[str, ...]
    #: Set when the guard lives in THIS suite and can therefore be mutated here.
    mutable_guard: str | None = None
    unheld: str = ""


CRITICAL_CONTRACTS: dict[str, Contract] = {
    "native-manifest.example.json": Contract(
        guard="A_test_section_that_is_a_bare_string_is_refused_the_way_production_saw_it",
        why=(
            "the Cloud Core sent a bare string where the device requires an object; the first "
            "real production build died at the first device step with both suites green"
        ),
        held_by=("services/api", "devices/windows-agent"),
    ),
    "browser-injection-markers.json": Contract(
        guard="test_markers_match_the_shared_protocol_json",
        why="one marker list for the API and the browser agent; two lists is two policies",
        held_by=("services/api", "services/browser"),
    ),
    "realtime-session-contract.json": Contract(
        guard="test_committed_contract_matches_the_live_request_models",
        why=(
            "the first owner qualification got a bare HTTP 422: the page sent `voice` to an "
            "API whose model forbids extras"
        ),
        held_by=("services/api", "apps/web"),
        mutable_guard=(
            "tests/unit/test_realtime_contract.py::"
            "test_committed_contract_matches_the_live_request_models"
        ),
    ),
    "device-protocol.schema.json": Contract(
        guard="test_hello_knows_exactly_the_fields_the_schema_declares",
        why=(
            "ADR-0118 added build_id to HelloFrame and to the agent and never to the schema "
            "that calls itself authoritative"
        ),
        held_by=("services/api",),
        unheld=(
            "devices/windows-agent cites this schema in comments and is held to nothing: the "
            "C# frame types are not compared against it. Registered here so the gap is "
            "counted rather than invisible - closing it belongs to B03 (contract parity)."
        ),
    ),
}


#: The separately built and deployed halves of this system. What matters for a contract is
#: not that two LANGUAGES read it - browser-injection-markers.json is read by two Python
#: services that ship independently - but that no single component owns it alone.
_COMPONENTS = (
    "services/api",
    "services/browser",
    "services/recovery-supervisor",
    "devices/windows-agent",
    "apps/web",
    "scripts",
)

#: This file names every artifact in its own registry; counting itself as a reader would let
#: a contract satisfy the rule by being mentioned here.
_SELF = "services/api/tests/unit/test_contract_falsification.py"


def _component_of(relative: str) -> str | None:
    for component in _COMPONENTS:
        if relative.startswith(component + "/"):
            return component
    return None


@lru_cache(maxsize=1)
def _readers() -> dict[str, dict[str, set[str]]]:
    """artifact -> component -> the files in it that name the artifact."""
    found: dict[str, dict[str, set[str]]] = {name: {} for name in CRITICAL_CONTRACTS}
    for directory, subdirectories, filenames in os.walk(REPO_ROOT):
        subdirectories[:] = [d for d in subdirectories if d not in _SKIP_DIRS]
        for filename in filenames:
            if Path(filename).suffix not in _READER_SUFFIXES:
                continue
            path = Path(directory) / filename
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative == _SELF:
                continue
            component = _component_of(relative)
            if component is None:
                continue
            try:
                text = path.read_text("utf-8", errors="ignore")
            except OSError:
                continue
            for artifact in found:
                if artifact in text:
                    found[artifact].setdefault(component, set()).add(relative)
    return found


def test_every_shared_artifact_is_registered() -> None:
    """A new contract with no guard must fail HERE, not in production.

    The scan covers every package, not just ``packages/protocol``: the device protocol schema
    lives under ``packages/schemas`` and was a shared contract nobody had registered.
    """
    on_disk = {p.name for p in PACKAGES.rglob("*.json")}
    assert on_disk, f"no shared artifacts found under {PACKAGES}"
    unregistered = sorted(on_disk - set(CRITICAL_CONTRACTS))
    assert not unregistered, (
        "these shared contract artifacts have no registered guard - add one that proves both "
        f"halves read the SAME file, then register it here: {unregistered}"
    )
    gone = sorted(set(CRITICAL_CONTRACTS) - on_disk)
    assert not gone, f"registered contracts that no longer exist: {gone}"


@pytest.mark.parametrize("artifact", sorted(CRITICAL_CONTRACTS))
def test_each_contract_is_read_by_two_independent_components(artifact: str) -> None:
    components = _readers()[artifact]
    assert len(components) >= 2, (
        f"{artifact} is read only by {sorted(components) or 'nothing'} - a contract with one "
        "reader is not a contract, it is a file"
    )


@pytest.mark.parametrize("artifact", sorted(CRITICAL_CONTRACTS))
def test_each_contract_actually_holds_a_half_to_itself(artifact: str) -> None:
    """Mentioning a contract is not being held to it.

    Every component that cites the artifact in a comment counts as a reader above; this is the
    stricter question - whose CODE fails when the file changes. A contract held by one half
    has to say what the other half's freedom costs, in `unheld`, so a partial contract is
    never mistaken for a closed one.
    """
    contract = CRITICAL_CONTRACTS[artifact]
    mentioning = set(_readers()[artifact])
    assert contract.held_by, f"{artifact} holds nobody to anything"
    stray = sorted(set(contract.held_by) - mentioning)
    assert not stray, f"{artifact} claims to hold {stray}, which never names it"
    if set(contract.held_by) < mentioning:
        assert contract.unheld, (
            f"{artifact} is cited by {sorted(mentioning - set(contract.held_by))} and holds "
            "none of them - say what that costs in `unheld` rather than leaving it implied"
        )


@pytest.mark.parametrize("artifact", sorted(CRITICAL_CONTRACTS))
def test_each_contracts_guard_exists(artifact: str) -> None:
    # Searched across the repository rather than among the artifact's readers: a guard is
    # often in a test file that names the shape, not the path.
    guard = CRITICAL_CONTRACTS[artifact].guard
    found = False
    for directory, subdirectories, filenames in os.walk(REPO_ROOT):
        subdirectories[:] = [d for d in subdirectories if d not in _SKIP_DIRS]
        for filename in filenames:
            if Path(filename).suffix not in _READER_SUFFIXES:
                continue
            try:
                if guard in (Path(directory) / filename).read_text("utf-8", errors="ignore"):
                    found = True
                    break
            except OSError:
                continue
        if found:
            break
    assert found, f"{artifact}'s registered guard {guard!r} does not exist anywhere"


@pytest.mark.parametrize("artifact", sorted(CRITICAL_CONTRACTS))
def test_no_reader_hedges_on_the_artifact_being_absent(artifact: str) -> None:
    """The 2026-09-12 defect, as a rule.

    A reader that skips, falls back or defaults when the shared file is missing turns the
    contract into a suggestion: delete the file and every guard over it goes green.
    """
    offenders: list[str] = []
    for language_files in _readers()[artifact].values():
        for relative in language_files:
            text = (REPO_ROOT / relative).read_text("utf-8", errors="ignore")
            for index, line in enumerate(text.splitlines()):
                if artifact not in line and "_MARKERS_JSON" not in line and "COMMITTED" not in line:
                    continue
                window = "\n".join(text.splitlines()[max(0, index - 6) : index + 7])
                if re.search(r"\.skip\(|pytest\.skip|Skip\s*=", window) and "exists" in window:
                    offenders.append(f"{relative}:{index + 1}")
    assert not offenders, (
        f"these readers of {artifact} skip when it is absent - the guard would go green if "
        f"the contract were deleted: {sorted(set(offenders))}"
    )


def test_hiding_a_contract_actually_fails_its_guard(tmp_path: Path) -> None:
    """The mutation, performed. Not "the guard looks right" - the guard was RUN against a
    world where the contract is gone, and it failed.

    The artifact is copied aside and restored by digest, never by `git checkout`: the working
    tree is the work, and a restore that is not verified is a hope.
    """
    artifact = "realtime-session-contract.json"
    contract = CRITICAL_CONTRACTS[artifact]
    assert contract.mutable_guard is not None
    live = PROTOCOL / artifact
    original = live.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    backup = tmp_path / artifact
    backup.write_bytes(original)

    def run_guard() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - this interpreter, a test id from this file
            [sys.executable, "-m", "pytest", contract.mutable_guard, "-q", "--no-header", "-p",
             "no:cacheprovider"],
            cwd=API_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )

    try:
        live.unlink()
        mutated = run_guard()
    finally:
        live.write_bytes(backup.read_bytes())
        restored = hashlib.sha256(live.read_bytes()).hexdigest()

    assert restored == digest, "the contract was NOT restored; do not commit this tree"
    assert mutated.returncode != 0, (
        "the contract was deleted and its guard still passed - the guard is not guarding:\n"
        + mutated.stdout[-2000:]
    )
    assert artifact in mutated.stdout or artifact in mutated.stderr, (
        "the guard failed, but not for the missing contract - it must say what is gone:\n"
        + mutated.stdout[-2000:]
    )

    # And it passes again with the contract back, so the failure was the mutation and not
    # something this test left behind.
    assert run_guard().returncode == 0, "the guard does not pass with the contract restored"
