"""Where a build may happen, and what it may contain.

Two guards that only work applied BEFORE anything happens. The containment one is
resolve-then-contain, because comparing strings first and resolving later is the bug the
pattern exists to avoid. The extension one exists because a native project is COMPILED and
RUN, unlike M23's web projects: MSBuild will happily execute a `.ps1` a project file points
at, so a template that grew one must be refused before a compiler sees the tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.appfactory.generator import ProjectFile, ProjectFiles
from app.nativefactory.generator import render
from app.nativefactory.roots import (
    ALLOWED_EXTENSIONS,
    EXECUTABLE_EXTENSIONS,
    NATIVE_SUBDIR,
    build_dir_for,
    check_extensions,
    native_root,
    projects_root,
    resolve_within,
)
from app.nativefactory.spec import NativeFactoryError, parse_spec

SPEC = parse_spec(
    {"name": "Notlarim", "template": "notes-desktop", "targets": ["windows_exe"]}
)


# ------------------------------------------------------------------------ containment


def test_a_path_inside_the_root_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "native"
    root.mkdir()
    inside = root / "notlarim"
    assert resolve_within(root, inside) == inside.resolve()


def test_the_root_itself_is_inside_itself(tmp_path: Path) -> None:
    root = tmp_path / "native"
    root.mkdir()
    assert resolve_within(root, root) == root.resolve()


@pytest.mark.parametrize(
    "escape",
    [
        "..",
        "../elsewhere",
        "../../Windows/System32",
        "notlarim/../../escaped",
    ],
)
def test_climbing_out_is_refused(tmp_path: Path, escape: str) -> None:
    root = tmp_path / "native"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(NativeFactoryError) as caught:
        resolve_within(root, root / escape)
    assert caught.value.error_class == "path_outside_root"


def test_an_absolute_path_elsewhere_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "native"
    root.mkdir()
    other = tmp_path / "somewhere-else"
    other.mkdir()
    with pytest.raises(NativeFactoryError):
        resolve_within(root, other)


def test_a_symlink_pointing_out_is_refused_because_the_path_is_resolved_first(
    tmp_path: Path,
) -> None:
    """The whole reason the check resolves BEFORE comparing. A string comparison would see
    a path under the root and let it through."""
    root = tmp_path / "native"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "sneaky"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine does not allow creating symlinks unprivileged")
    with pytest.raises(NativeFactoryError):
        resolve_within(root, link)


def test_the_native_root_is_under_the_projects_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert native_root().parent == projects_root()
    assert native_root().name == NATIVE_SUBDIR


def test_a_build_directory_is_named_by_the_slug_and_still_contained(
    tmp_path: Path,
) -> None:
    """The slug is closed-alphabet by construction, so it cannot climb out - and it is
    contained anyway, because "it cannot" is a claim about today's validator and
    containment is a claim about the filesystem."""
    root = tmp_path / "native"
    root.mkdir()
    assert build_dir_for("notlarim", root=root) == (root / "notlarim").resolve()


# ------------------------------------------------------------------------- extensions


def test_the_real_template_contains_only_source() -> None:
    check_extensions(render(SPEC))


@pytest.mark.parametrize("suffix", sorted(EXECUTABLE_EXTENSIONS))
def test_anything_a_build_could_be_told_to_run_is_refused(suffix: str) -> None:
    project = ProjectFiles(files=(ProjectFile(path=f"tools/helper{suffix}", text="x"),))
    with pytest.raises(NativeFactoryError) as caught:
        check_extensions(project)
    assert caught.value.error_class == "file_not_source"


def test_an_unrecognised_extension_is_refused_rather_than_assumed_harmless() -> None:
    project = ProjectFiles(files=(ProjectFile(path="data/thing.wat", text="x"),))
    with pytest.raises(NativeFactoryError):
        check_extensions(project)


def test_a_file_with_no_extension_is_refused() -> None:
    project = ProjectFiles(files=(ProjectFile(path="Makefile", text="all:"),))
    with pytest.raises(NativeFactoryError):
        check_extensions(project)


def test_the_two_lists_can_never_overlap() -> None:
    """A runnable file is not source. Asserted rather than left to whoever widens the
    allowlist next to notice."""
    assert not (ALLOWED_EXTENSIONS & EXECUTABLE_EXTENSIONS)
