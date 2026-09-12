"""B01 req 21/22: which BUILD is running, and can two builds be told apart.

Production has served ``app_version`` ``0.1.0`` for every release it has ever had, and
``version`` reads ``unknown`` whenever the host did not export a sha. Neither can distinguish
two images. The device solved this with a DERIVED identity (ADR-0118); these tests hold the
cloud half to the same rule and to the same shape, by reading the device's own source rather
than restating its constants here.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.release.build import (
    BUILD_ID_HEX_CHARS,
    UNKNOWN_BUILD_ID,
    build_identity,
    compute_build_identity,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DEVICE_CONSTANTS = (
    REPO_ROOT
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.Agent.Core"
    / "Protocol"
    / "ProtocolConstants.cs"
)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_the_running_build_has_an_identity_of_the_agreed_shape() -> None:
    identity = build_identity()
    assert re.fullmatch(rf"[0-9a-f]{{{BUILD_ID_HEX_CHARS}}}", identity), identity
    # Computed once and cached: two readers of the same process must not disagree.
    assert build_identity() == identity


def test_two_builds_that_differ_by_one_byte_have_different_identities(tmp_path: Path) -> None:
    first = _tree(tmp_path / "a", {"app.py": "VALUE = 1\n", "pkg/mod.py": "x = 1\n"})
    second = _tree(tmp_path / "b", {"app.py": "VALUE = 2\n", "pkg/mod.py": "x = 1\n"})
    assert compute_build_identity(first) != compute_build_identity(second)


def test_the_same_bytes_always_give_the_same_identity(tmp_path: Path) -> None:
    first = _tree(tmp_path / "a", {"app.py": "VALUE = 1\n", "pkg/mod.py": "x = 1\n"})
    second = _tree(tmp_path / "b", {"pkg/mod.py": "x = 1\n", "app.py": "VALUE = 1\n"})
    # Written in a different order, and still the same identity: the fold sorts by path.
    assert compute_build_identity(first) == compute_build_identity(second)


def test_renaming_a_module_changes_the_identity(tmp_path: Path) -> None:
    """The device folds the file NAME in as well as the bytes; so does this."""
    first = _tree(tmp_path / "a", {"pkg/one.py": "x = 1\n"})
    second = _tree(tmp_path / "b", {"pkg/two.py": "x = 1\n"})
    assert compute_build_identity(first) != compute_build_identity(second)


def test_byte_compiled_copies_do_not_change_the_identity(tmp_path: Path) -> None:
    """A warmed-up container is the same build as a cold one."""
    cold = _tree(tmp_path / "a", {"pkg/mod.py": "x = 1\n"})
    warm = _tree(
        tmp_path / "b",
        {"pkg/mod.py": "x = 1\n", "pkg/__pycache__/mod.cpython-312.py": "whatever\n"},
    )
    assert compute_build_identity(cold) == compute_build_identity(warm)


def test_a_tree_that_cannot_be_read_says_unknown_rather_than_raising(tmp_path: Path) -> None:
    assert compute_build_identity(tmp_path / "does-not-exist") == UNKNOWN_BUILD_ID
    empty = tmp_path / "empty"
    empty.mkdir()
    assert compute_build_identity(empty) == UNKNOWN_BUILD_ID


def test_the_two_halves_of_the_system_agree_on_what_a_build_identity_looks_like() -> None:
    """The device's rule, read from the device's own source - not restated here.

    Both halves answer "which build is this" and both answers end up in the same places (a
    health body, a release log, the updater's comparison). Two conventions would be one more
    thing that drifts; the contract is: the same width, lowercase hex, and the same sentinel
    for "cannot say".
    """
    source = DEVICE_CONSTANTS.read_text(encoding="utf-8")
    assert 'UnknownBuildId = "unknown"' in source
    assert UNKNOWN_BUILD_ID == "unknown"
    # ComputeBuildId ends with `Convert.ToHexString(...)[..16].ToLowerInvariant()`.
    match = re.search(r"GetHashAndReset\(\)\)\[\.\.(\d+)\]\.ToLowerInvariant\(\)", source)
    assert match is not None, "the device no longer truncates its build id the documented way"
    assert int(match.group(1)) == BUILD_ID_HEX_CHARS
