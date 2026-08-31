"""Pointer-file transitions, release immutability and digest determinism."""

from pathlib import Path

import pytest

from recovery_supervisor.workspace import ReleaseWorkspace, WorkspaceError, manifest_digest


def make_source(tmp_path: Path, name: str, content: str) -> Path:
    src = tmp_path / f"src-{name}"
    src.mkdir()
    (src / "handler.py").write_text(content, encoding="utf-8")
    return src


@pytest.fixture()
def ws(tmp_path: Path) -> ReleaseWorkspace:
    workspace = ReleaseWorkspace(tmp_path / "ws")
    workspace.ensure()
    return workspace


def test_activate_stages_and_repoints_current(ws: ReleaseWorkspace, tmp_path: Path) -> None:
    src = make_source(tmp_path, "100", "VERSION = '1.0.0'\n")
    previous = ws.activate("1.0.0", src)
    assert previous is None
    assert ws.current == "1.0.0"
    assert ws.last_known_good is None  # activate never moves LKG
    assert (ws.release_dir("1.0.0") / "handler.py").read_text(encoding="utf-8").startswith(
        "VERSION"
    )


def test_release_directories_are_immutable(ws: ReleaseWorkspace, tmp_path: Path) -> None:
    src = make_source(tmp_path, "a", "A = 1\n")
    ws.activate("1.0.0", src)
    other = make_source(tmp_path, "b", "B = 2\n")
    with pytest.raises(WorkspaceError) as excinfo:
        ws.stage_release("1.0.0", other)
    assert excinfo.value.code == "release_exists"
    # Original bytes preserved (stage-preserve, never in-place overwrite).
    assert (ws.release_dir("1.0.0") / "handler.py").read_text(encoding="utf-8") == "A = 1\n"


def test_activate_unstaged_release_refused(ws: ReleaseWorkspace) -> None:
    with pytest.raises(WorkspaceError) as excinfo:
        ws.activate("9.9.9")
    assert excinfo.value.code == "release_missing"


def test_promote_marks_active_as_last_known_good(ws: ReleaseWorkspace, tmp_path: Path) -> None:
    ws.activate("1.0.0", make_source(tmp_path, "a", "A = 1\n"))
    ws.promote()
    assert ws.last_known_good == "1.0.0"
    # New release activation preserves the old LKG until it is itself promoted.
    ws.activate("1.1.0", make_source(tmp_path, "b", "B = 2\n"))
    assert ws.current == "1.1.0"
    assert ws.last_known_good == "1.0.0"
    ws.promote("1.1.0")
    assert ws.last_known_good == "1.1.0"


def test_promote_refuses_non_active_version(ws: ReleaseWorkspace, tmp_path: Path) -> None:
    ws.activate("1.0.0", make_source(tmp_path, "a", "A = 1\n"))
    with pytest.raises(WorkspaceError) as excinfo:
        ws.promote("2.0.0")
    assert excinfo.value.code == "promote_requires_active"
    with pytest.raises(WorkspaceError) as excinfo2:
        ReleaseWorkspace(ws.root / "empty").promote()
    assert excinfo2.value.code == "no_active_release"


def test_rollback_repoints_and_preserves_everything(
    ws: ReleaseWorkspace, tmp_path: Path
) -> None:
    ws.activate("1.0.0", make_source(tmp_path, "a", "A = 1\n"))
    ws.promote()
    ws.activate("1.1.0", make_source(tmp_path, "b", "B = 2\n"))
    previous, target = ws.rollback()
    assert (previous, target) == ("1.1.0", "1.0.0")
    assert ws.current == "1.0.0"
    assert ws.last_known_good == "1.0.0"
    # The broken release directory is preserved for later diagnosis.
    assert ws.has_release("1.1.0")


def test_rollback_without_last_known_good_refused(
    ws: ReleaseWorkspace, tmp_path: Path
) -> None:
    ws.activate("1.0.0", make_source(tmp_path, "a", "A = 1\n"))
    with pytest.raises(WorkspaceError) as excinfo:
        ws.rollback()
    assert excinfo.value.code == "no_last_known_good"


def test_status_shape(ws: ReleaseWorkspace, tmp_path: Path) -> None:
    ws.activate("1.0.0", make_source(tmp_path, "a", "A = 1\n"))
    ws.promote()
    status = ws.status()
    assert status["current"] == "1.0.0"
    assert status["last_known_good"] == "1.0.0"
    assert status["releases"] == ["1.0.0"]
    assert isinstance(status["current_manifest_digest"], str)
    assert len(status["current_manifest_digest"]) == 64
    assert status["outbox_pending"] == 0


def test_invalid_versions_rejected(ws: ReleaseWorkspace) -> None:
    for bad in ("", "../evil", "a/b", "a\\b", "C:evil"):
        with pytest.raises(WorkspaceError) as excinfo:
            ws.release_dir(bad)
        assert excinfo.value.code == "invalid_version"


def test_manifest_digest_deterministic_and_ignores_caches(tmp_path: Path) -> None:
    release = tmp_path / "rel"
    (release / "sub").mkdir(parents=True)
    (release / "handler.py").write_text("X = 1\n", encoding="utf-8")
    (release / "sub" / "util.py").write_text("Y = 2\n", encoding="utf-8")
    first = manifest_digest(release)
    assert first == manifest_digest(release)
    # __pycache__, *.pyc and manifest.json must not affect the digest.
    (release / "__pycache__").mkdir()
    (release / "__pycache__" / "junk.pyc").write_bytes(b"\x00")
    (release / "handler.pyc").write_bytes(b"\x00")
    (release / "manifest.json").write_text("{}", encoding="utf-8")
    assert manifest_digest(release) == first
    # Content changes do affect it.
    (release / "handler.py").write_text("X = 3\n", encoding="utf-8")
    assert manifest_digest(release) != first
