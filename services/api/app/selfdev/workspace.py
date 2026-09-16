"""One isolated git worktree per self-development run, on its own branch, from an exact SHA.

The engine never edits a checkout anyone else is using. ``create`` makes
``<worktrees_root>/<run_id>`` on branch ``selfdev/<run_id>`` from the base SHA; every file the
model proposes is validated here - relative, inside the repository, no ``..``, no ``.git``, no
drive or absolute path, inside the allowed scope, bounded in size - before a byte is written.
``remove`` only ever removes a worktree this class created under its own root.

Limits are enforced before work starts, not discovered after: at most ``max_worktrees`` live
at once (a quarantined run keeps its worktree for inspection and still counts), and a floor
of free disk space under the root.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.selfdev.model import Patch

BRANCH_PREFIX = "selfdev/"


class WorkspaceError(Exception):
    """A refusal with a reason the run record carries verbatim."""


@dataclass(frozen=True, slots=True)
class WorkspaceLimits:
    max_worktrees: int = 3
    min_free_bytes: int = 5 * 1024**3
    max_files_per_patch: int = 12
    max_bytes_per_file: int = 200_000


def _run(
    args: list[str], cwd: Path, *, timeout_s: float = 120.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed git argv, no shell
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )


def safe_relative_path(path: str) -> str:
    """The repository-relative POSIX form of ``path``, or a WorkspaceError saying why not."""
    if not path or "\\" in path or "\x00" in path:
        raise WorkspaceError(f"refusing path {path!r}: empty, backslash or NUL")
    pure = PurePosixPath(path)
    if pure.is_absolute() or (len(path) > 1 and path[1] == ":"):
        raise WorkspaceError(f"refusing absolute path {path!r}")
    if any(part in ("..", "") for part in pure.parts) or pure.parts[0] == ".git":
        raise WorkspaceError(f"refusing path {path!r}: it leaves the tree or names .git")
    return pure.as_posix()


class GitWorkspace:
    def __init__(
        self,
        repo: Path,
        worktrees_root: Path,
        *,
        git: str = "git",
        limits: WorkspaceLimits | None = None,
    ) -> None:
        self.repo = repo
        self.root = worktrees_root
        self.git = git
        self.limits = limits or WorkspaceLimits()

    # ------------------------------------------------------------------ lifecycle

    def live(self) -> list[Path]:
        """Worktrees under this root that git still knows about."""
        listed = _run([self.git, "worktree", "list", "--porcelain"], self.repo)
        paths = [
            Path(line[len("worktree ") :].strip())
            for line in listed.stdout.splitlines()
            if line.startswith("worktree ")
        ]
        root = self.root.resolve()
        return [p for p in paths if root in p.resolve().parents]

    def create(self, run_id: str, base_sha: str) -> Path:
        if len(self.live()) >= self.limits.max_worktrees:
            raise WorkspaceError(
                f"refusing a new worktree: {self.limits.max_worktrees} self-development "
                "worktrees are already live (quarantined runs keep theirs for inspection)"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        if free < self.limits.min_free_bytes:
            raise WorkspaceError(
                f"refusing a new worktree: {free // 1024**2} MiB free under {self.root}, "
                f"below the {self.limits.min_free_bytes // 1024**2} MiB floor"
            )
        target = self.root / run_id
        made = _run(
            [self.git, "worktree", "add", "-b", f"{BRANCH_PREFIX}{run_id}", str(target), base_sha],
            self.repo,
        )
        if made.returncode != 0:
            raise WorkspaceError(f"git worktree add failed: {made.stderr.strip()[:400]}")
        return target

    def remove(self, worktree: Path) -> None:
        if self.root.resolve() not in worktree.resolve().parents:
            raise WorkspaceError(f"refusing to remove {worktree}: not under {self.root}")
        _run([self.git, "worktree", "remove", "--force", str(worktree)], self.repo)

    # --------------------------------------------------------------------- files

    def read(self, worktree: Path, paths: tuple[str, ...] | list[str]) -> dict[str, str]:
        files: dict[str, str] = {}
        for path in paths:
            target = worktree / safe_relative_path(path)
            if target.is_file():
                files[path] = target.read_text(encoding="utf-8", errors="replace")
        return files

    def validate(self, patch: Patch, *, allowed: Callable[[str], bool]) -> None:
        if patch.rejected:
            raise WorkspaceError("the patch did not apply: " + "; ".join(patch.rejected))
        if not patch.edits:
            raise WorkspaceError("the patch changes nothing")
        if len(patch.edits) > self.limits.max_files_per_patch:
            raise WorkspaceError(
                f"the patch changes {len(patch.edits)} files; the bound is "
                f"{self.limits.max_files_per_patch}"
            )
        for edit in patch.edits:
            path = safe_relative_path(edit.path)
            if not allowed(path):
                raise WorkspaceError(f"{path} is outside this defect's scope")
            if len(edit.new_text.encode("utf-8")) > self.limits.max_bytes_per_file:
                raise WorkspaceError(f"{path} exceeds {self.limits.max_bytes_per_file} bytes")

    def write(self, worktree: Path, patch: Patch) -> None:
        for edit in patch.edits:
            target = worktree / safe_relative_path(edit.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(edit.new_text.encode("utf-8"))

    def reset(self, worktree: Path) -> None:
        """Back to the base commit: tracked changes discarded, files the patch added removed.

        A hard reset, not ``checkout -- .``: ``diff``/``changed_paths`` STAGE the patch
        (``git add -A``) so new files show, and a checkout restores the index - the
        patched tree - so a second attempt after a model-review refusal or a red gate
        judged its regression test against the fix that was still there and called it
        "passed on the base" (B35, found by the gate's fix loop).
        """
        _run([self.git, "reset", "-q", "--hard"], worktree)
        _run([self.git, "clean", "-fdq"], worktree)

    # ---------------------------------------------------------------- the change

    def diff(self, worktree: Path) -> str:
        _run([self.git, "add", "-A"], worktree)
        shown = _run([self.git, "diff", "--cached", "--no-color"], worktree)
        return shown.stdout

    def changed_paths(self, worktree: Path) -> list[str]:
        _run([self.git, "add", "-A"], worktree)
        listed = _run([self.git, "diff", "--cached", "--name-only"], worktree)
        return [line.strip() for line in listed.stdout.splitlines() if line.strip()]

    def commit(self, worktree: Path, message: str) -> str:
        _run([self.git, "add", "-A"], worktree)
        made = _run(
            [
                self.git,
                "-c",
                "user.name=PagentOS self-development",
                "-c",
                "user.email=selfdev@pagentos.invalid",
                "commit",
                "-q",
                "-m",
                message,
            ],
            worktree,
        )
        if made.returncode != 0:
            raise WorkspaceError(f"git commit failed: {made.stderr.strip()[:400]}")
        return _run([self.git, "rev-parse", "HEAD"], worktree).stdout.strip()
