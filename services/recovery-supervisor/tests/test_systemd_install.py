from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = REPO_ROOT / "scripts" / "cloud" / "install-recovery-supervisor.sh"
UNINSTALLER = REPO_ROOT / "scripts" / "cloud" / "uninstall-recovery-supervisor.sh"
SERVICE = REPO_ROOT / "infra" / "systemd" / "pagentos-bluegreen-reconcile.service"
TIMER = REPO_ROOT / "infra" / "systemd" / "pagentos-bluegreen-reconcile.timer"
#: The template the service's OnFailure= names; the installer ships it too (2026-09-18:
#: it was in the repository and in no installer, so a failed recovery wrote no marker).
MARKER = REPO_ROOT / "infra" / "systemd" / "pagentos-failure-marker@.service"
RECONCILE = REPO_ROOT / "scripts" / "cloud" / "release-cloud-core-bluegreen.sh"
COMPOSE = REPO_ROOT / "infra" / "docker" / "docker-compose.prod.yml"
NGINX = REPO_ROOT / "infra" / "docker" / "edge" / "nginx.conf"

#: The commit an approver names. Any 40-hex value: what matters is that the installer
#: installs only when the host's markers name exactly this, and refuses otherwise.
APPROVED = "a" * 40
OTHER = "b" * 40

BUNDLE_FILES = (
    "reconcile.sh",
    COMPOSE.name,
    "nginx.conf",
    "reconcile.sha256",
    "APPROVED_SHA",
)


def _bash() -> str:
    candidate = shutil.which("bash")
    if os.name == "nt":
        git_bash = Path(os.environ["ProgramFiles"]) / "Git" / "bin" / "bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    if candidate:
        return candidate
    raise RuntimeError("bash is required for the production installer test")


def _shell_path(path: Path) -> str:
    return path.as_posix()


def _seed_app_root(
    app_root: Path,
    *,
    action: bytes | None = None,
    tree_sha: str | None = APPROVED,
    completed_sha: str | None = APPROVED,
) -> None:
    """A released tree as `git archive` + the blue/green release leave it on the host.

    ``app_root`` is ``<base>/app``; ``<base>/RELEASE`` is the last COMPLETED promotion and
    ``<base>/app/RELEASE`` the tree's own commit.
    """
    files = {
        app_root / "scripts" / "cloud" / RECONCILE.name: action or RECONCILE.read_bytes(),
        app_root / "scripts" / "cloud" / INSTALLER.name: INSTALLER.read_bytes(),
        app_root / "infra" / "systemd" / SERVICE.name: SERVICE.read_bytes(),
        app_root / "infra" / "systemd" / TIMER.name: TIMER.read_bytes(),
        app_root / "infra" / "systemd" / MARKER.name: MARKER.read_bytes(),
        app_root / "infra" / "docker" / COMPOSE.name: COMPOSE.read_bytes(),
        app_root / "infra" / "docker" / "edge" / NGINX.name: NGINX.read_bytes(),
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if tree_sha is not None:
        (app_root / "RELEASE").write_text(tree_sha + "\n", encoding="utf-8")
    if completed_sha is not None:
        (app_root.parent / "RELEASE").write_text(completed_sha + "\n", encoding="utf-8")


def _fake_systemctl(path: Path, body: str = "") -> Path:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "if [ \"$*\" = 'is-active --quiet pagentos-bluegreen-reconcile.service' ]; "
        "then exit 1; fi\n" + body,
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _fake_flock(tmp_path: Path) -> Path:
    """Git for Windows has no flock. The fake records that the lock was asked for (into the
    same call log as systemctl, so the ORDER is visible) and answers PAGENTOS_TEST_FLOCK_EXIT.
    The real kernel lock is exercised by the Linux-only test below."""
    fake = tmp_path / "flock"
    if not fake.exists():
        fake.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'flock %s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
            "exit \"${PAGENTOS_TEST_FLOCK_EXIT:-0}\"\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


def _install_env(tmp_path: Path, app_root: Path, **extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "PAGENTOS_ALLOW_NONROOT": "1",
        "PAGENTOS_FLOCK": _shell_path(_fake_flock(tmp_path)),
        "PAGENTOS_BASE": _shell_path(app_root.parent),
        "PAGENTOS_APP_ROOT": _shell_path(app_root),
        "PAGENTOS_SYSTEMD_DIR": _shell_path(tmp_path / "systemd"),
        "PAGENTOS_RECOVERY_ROOT": _shell_path(tmp_path / "recovery"),
        "PAGENTOS_TEST_CALLS": _shell_path(tmp_path / "systemctl.calls"),
        **extra,
    }


def _run(script: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), _shell_path(script), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_timer_is_persistent_bounded_and_targets_the_reconcile_service() -> None:
    text = TIMER.read_text(encoding="utf-8")
    assert "OnBootSec=2min" in text
    assert "OnUnitInactiveSec=60s" in text
    assert "Persistent=true" in text
    assert "Unit=pagentos-bluegreen-reconcile.service" in text
    assert "WantedBy=timers.target" in text
    service = SERVICE.read_text(encoding="utf-8")
    assert (
        "ExecStartPre=/usr/bin/sha256sum --check "
        "/opt/pagentos-recovery/reconcile.sha256" in service
    )
    assert "ExecStart=/bin/bash /opt/pagentos-recovery/reconcile.sh --reconcile" in service
    exec_lines = [line for line in service.splitlines() if line.startswith("ExecStart")]
    assert all("/opt/pagentos/app/" not in line for line in exec_lines)


def test_release_and_periodic_recovery_share_one_operation_lock() -> None:
    text = RECONCILE.read_text(encoding="utf-8")
    assert 'lock_file="$base/.bluegreen-operation.lock"' in text
    assert 'exec 9>"$lock_file"' in text
    assert "flock -n 9" in text
    assert "exit 82" in text


def test_periodic_recovery_refuses_to_overlap_a_live_release(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Git for Windows has no flock; Linux CI exercises the kernel lock")
    if shutil.which("flock") is None:
        pytest.skip("util-linux flock is required by the production host contract")
    base = tmp_path / "host"
    base.mkdir()
    command = """
set -u
exec 8>"$PAGENTOS_BASE/.bluegreen-operation.lock"
flock -n 8
bash "$PAGENTOS_RECONCILE" --reconcile
result=$?
exit "$result"
"""
    completed = subprocess.run(
        [_bash(), "-c", command],
        env={
            **os.environ,
            "PAGENTOS_BASE": _shell_path(base),
            "PAGENTOS_RECONCILE": _shell_path(RECONCILE),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 82
    assert "another blue/green release or recovery operation" in completed.stderr


def test_recovery_refuses_candidate_modified_root_inputs(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Linux CI exercises the production flock and pinned-input gate")
    if shutil.which("flock") is None:
        pytest.skip("util-linux flock is required by the production host contract")
    base = tmp_path / "host"
    app = base / "app"
    _seed_app_root(app)
    bundle = tmp_path / "recovery"
    bundle.mkdir()
    (bundle / COMPOSE.name).write_bytes(COMPOSE.read_bytes())
    (bundle / "nginx.conf").write_bytes(NGINX.read_bytes())
    (app / "infra" / "docker" / COMPOSE.name).write_text("services: {}\n")
    completed = subprocess.run(
        [_bash(), _shell_path(RECONCILE), "--reconcile"],
        env={
            **os.environ,
            "PAGENTOS_BASE": _shell_path(base),
            "PAGENTOS_RECOVERY_BUNDLE": _shell_path(bundle),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 83
    assert "matches the pinned recovery Compose and nginx inputs" in completed.stderr


def test_installer_proves_recovery_before_enabling_timer(tmp_path: Path) -> None:
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    recovery = app_root / "scripts" / "cloud" / RECONCILE.name
    fake = _fake_systemctl(tmp_path / "systemctl")
    systemd_dir = tmp_path / "systemd"
    recovery_root = tmp_path / "recovery"
    calls = tmp_path / "systemctl.calls"

    completed = _run(
        INSTALLER,
        APPROVED,
        env=_install_env(tmp_path, app_root, PAGENTOS_SYSTEMCTL=_shell_path(fake)),
    )

    assert completed.returncode == 0, completed.stderr
    assert APPROVED in completed.stdout
    assert "off-switch: systemctl disable --now pagentos-bluegreen-reconcile.timer" in (
        completed.stdout
    )
    assert (systemd_dir / SERVICE.name).read_bytes() == SERVICE.read_bytes()
    assert (systemd_dir / TIMER.name).read_bytes() == TIMER.read_bytes()
    # The handler the service's OnFailure= names is installed with it; without it systemd
    # answers "No files found" and a failed recovery is a journal line nobody reads.
    assert (systemd_dir / MARKER.name).read_bytes() == MARKER.read_bytes()
    assert (recovery_root / "reconcile.sh").read_bytes() == RECONCILE.read_bytes()
    assert (recovery_root / COMPOSE.name).read_bytes() == COMPOSE.read_bytes()
    assert (recovery_root / "nginx.conf").read_bytes() == NGINX.read_bytes()
    assert (recovery_root / "APPROVED_SHA").read_text(encoding="utf-8").strip() == APPROVED
    digest = (recovery_root / "reconcile.sha256").read_text(encoding="utf-8")
    assert "reconcile.sh" in digest
    assert calls.read_text(encoding="utf-8").splitlines() == [
        # The operation lock first: nothing is checked or touched outside it.
        "flock -w 1200 9",
        "is-enabled --quiet pagentos-bluegreen-reconcile.timer",
        "is-active --quiet pagentos-bluegreen-reconcile.timer",
        "stop pagentos-bluegreen-reconcile.timer",
        "is-active --quiet pagentos-bluegreen-reconcile.service",
        "daemon-reload",
        "start pagentos-bluegreen-reconcile.service",
        "enable --now pagentos-bluegreen-reconcile.timer",
        "is-enabled pagentos-bluegreen-reconcile.timer",
        "is-active pagentos-bluegreen-reconcile.timer",
    ]

    recovery.write_text("#!/usr/bin/env bash\n# candidate tree changed later\n")
    (app_root / "infra" / "docker" / COMPOSE.name).write_text("services: {}\n")
    (app_root / "infra" / "docker" / "edge" / NGINX.name).write_text("broken\n")
    assert (recovery_root / "reconcile.sh").read_bytes() == RECONCILE.read_bytes()
    assert (recovery_root / COMPOSE.name).read_bytes() == COMPOSE.read_bytes()
    assert (recovery_root / "nginx.conf").read_bytes() == NGINX.read_bytes()


def test_installer_refuses_before_systemd_when_recovery_action_is_missing(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "systemctl"
    fake.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    completed = _run(
        INSTALLER,
        APPROVED,
        env=_install_env(
            tmp_path, tmp_path / "missing" / "app", PAGENTOS_SYSTEMCTL=_shell_path(fake)
        ),
    )
    assert completed.returncode == 2
    assert "required recovery input is absent" in completed.stderr
    assert not (tmp_path / "systemd").exists()


def test_installer_refuses_an_older_installed_recovery_action(tmp_path: Path) -> None:
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root, action=b"#!/usr/bin/env bash\n# old copy\n")
    completed = _run(INSTALLER, APPROVED, env=_install_env(tmp_path, app_root))
    assert completed.returncode == 3
    assert "release-cloud-core-bluegreen.sh differs" in completed.stderr
    assert not (tmp_path / "systemd").exists()


# ---- provenance: an exact approved commit (security review, 2026-09-11) ----------------


@pytest.mark.parametrize(
    "argument",
    [None, "", "a" * 39, "A" * 40, "a" * 41, "HEAD", "main"],
    ids=["absent", "empty", "short", "uppercase", "long", "ref-head", "ref-branch"],
)
def test_installer_refuses_without_an_exact_approved_commit(
    tmp_path: Path, argument: str | None
) -> None:
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    args = () if argument is None else (argument,)
    env = _install_env(tmp_path, app_root)
    env.pop("PAGENTOS_RECOVERY_EXPECTED_SHA", None)

    completed = _run(INSTALLER, *args, env=env)

    assert completed.returncode == 4, completed.stderr
    assert "exact, approved commit" in completed.stderr
    assert not (tmp_path / "systemd").exists()
    assert not (tmp_path / "recovery").exists()


@pytest.mark.parametrize(
    ("tree_sha", "completed_sha"),
    [(OTHER, APPROVED), (APPROVED, OTHER), (OTHER, OTHER), (None, APPROVED), (APPROVED, None)],
    ids=[
        "tree-is-another-commit",
        "completed-release-is-another-commit",
        "both-another-commit",
        "tree-has-no-marker",
        "no-completed-release",
    ],
)
def test_installer_refuses_a_host_that_is_not_the_approved_release(
    tmp_path: Path, tree_sha: str | None, completed_sha: str | None
) -> None:
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root, tree_sha=tree_sha, completed_sha=completed_sha)

    completed = _run(INSTALLER, APPROVED, env=_install_env(tmp_path, app_root))

    assert completed.returncode == 5, completed.stderr
    assert f"approved {APPROVED}" in completed.stderr
    assert not (tmp_path / "systemd").exists()
    assert not (tmp_path / "recovery").exists()


def test_run_from_the_deployed_tree_provenance_is_the_commit_not_a_self_comparison(
    tmp_path: Path,
) -> None:
    # The defect the review found: on the host the installer is run from the deployed tree,
    # so "the reviewed candidate" and "the installed tree" are one directory and the byte
    # comparison is a file compared with itself - it passed for ANY tree. Here the tree is
    # entirely self-consistent and still not the approved commit; only the commit can say so.
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root, tree_sha=OTHER, completed_sha=OTHER)
    installed_copy = app_root / "scripts" / "cloud" / INSTALLER.name
    fake = _fake_systemctl(tmp_path / "systemctl")
    env = _install_env(tmp_path, app_root, PAGENTOS_SYSTEMCTL=_shell_path(fake))

    refused = _run(installed_copy, APPROVED, env=env)

    assert refused.returncode == 5, refused.stderr
    assert not (tmp_path / "systemd").exists()

    # The same tree, released as the approved commit, installs - from itself.
    (app_root / "RELEASE").write_text(APPROVED + "\n", encoding="utf-8")
    (app_root.parent / "RELEASE").write_text(APPROVED + "\n", encoding="utf-8")
    accepted = _run(installed_copy, APPROVED, env=env)

    assert accepted.returncode == 0, accepted.stderr
    recovery_root = tmp_path / "recovery"
    assert (recovery_root / "APPROVED_SHA").read_text(encoding="utf-8").strip() == APPROVED


def test_the_installer_waits_for_the_operation_lock_and_touches_nothing_without_it(
    tmp_path: Path,
) -> None:
    # A release, rollback or reconcile still holding the lock after the wait: refuse, with
    # the host exactly as it was.
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    fake = _fake_systemctl(tmp_path / "systemctl")

    completed = _run(
        INSTALLER,
        APPROVED,
        env=_install_env(
            tmp_path, app_root, PAGENTOS_SYSTEMCTL=_shell_path(fake), PAGENTOS_TEST_FLOCK_EXIT="1"
        ),
    )

    assert completed.returncode == 6, completed.stderr
    assert "still holds" in completed.stderr
    assert not (tmp_path / "systemd").exists()
    assert not (tmp_path / "recovery").exists()
    assert (tmp_path / "systemctl.calls").read_text(encoding="utf-8").splitlines() == [
        "flock -w 1200 9"
    ]


def test_the_lock_is_held_through_the_copy_and_free_for_the_proof_run(tmp_path: Path) -> None:
    # The review's TOCTOU: provenance was checked once and the copy came up to 660 s later.
    # A release finishing in between would have swapped app/ under the installer. With the
    # real kernel lock a release cannot even start then - and the proof run, which IS a
    # reconcile and takes the same lock, must still be able to.
    if os.name == "nt":
        pytest.skip("Git for Windows has no flock; Linux CI exercises the kernel lock")
    if shutil.which("flock") is None:
        pytest.skip("util-linux flock is required by the production host contract")
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    lock = app_root.parent / ".bluegreen-operation.lock"
    probe = tmp_path / "probe.log"
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "try_lock() { if flock -n \"$PAGENTOS_TEST_LOCK\" true; then echo \"$1 free\"; "
        "else echo \"$1 held\"; fi >> \"$PAGENTOS_TEST_PROBE\"; }\n"
        "if [ \"$*\" = 'is-active --quiet pagentos-bluegreen-reconcile.service' ]; then "
        "try_lock during-copy-wait; exit 1; fi\n"
        "if [ \"$*\" = 'start pagentos-bluegreen-reconcile.service' ]; then "
        "try_lock at-proof-run; fi\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = _install_env(
        tmp_path,
        app_root,
        PAGENTOS_SYSTEMCTL=_shell_path(fake),
        PAGENTOS_TEST_LOCK=_shell_path(lock),
        PAGENTOS_TEST_PROBE=_shell_path(probe),
    )
    env.pop("PAGENTOS_FLOCK")  # the real one

    completed = _run(INSTALLER, APPROVED, env=env)

    assert completed.returncode == 0, completed.stderr
    assert probe.read_text(encoding="utf-8").splitlines() == [
        "during-copy-wait held",
        "at-proof-run free",
    ]


@pytest.mark.parametrize(
    "relative",
    [
        Path("infra") / "systemd" / SERVICE.name,
        Path("infra") / "systemd" / TIMER.name,
        Path("scripts") / "cloud" / INSTALLER.name,
    ],
    ids=["service-unit", "timer-unit", "installer"],
)
def test_a_separate_checkout_must_agree_with_the_approved_tree_on_every_installed_file(
    tmp_path: Path, relative: Path
) -> None:
    # Before the review only the action, Compose and nginx were compared; the two root
    # units - the files systemd actually executes - came from wherever the installer ran.
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    (app_root / relative).write_bytes(b"# not what the checkout carries\n")

    completed = _run(INSTALLER, APPROVED, env=_install_env(tmp_path, app_root))

    assert completed.returncode == 3, completed.stderr
    assert not (tmp_path / "systemd").exists()


def test_failed_upgrade_restores_and_restarts_the_previous_monitor(tmp_path: Path) -> None:
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    systemd_dir = tmp_path / "systemd"
    recovery_root = tmp_path / "recovery"
    systemd_dir.mkdir()
    recovery_root.mkdir()
    previous = {
        systemd_dir / SERVICE.name: b"old service\n",
        systemd_dir / TIMER.name: b"old timer\n",
        recovery_root / "reconcile.sh": b"old recovery\n",
        recovery_root / COMPOSE.name: b"old compose\n",
        recovery_root / "nginx.conf": b"old nginx\n",
        recovery_root / "reconcile.sha256": b"old digest\n",
        recovery_root / "APPROVED_SHA": (OTHER + "\n").encode("ascii"),
    }
    for path, content in previous.items():
        path.write_bytes(content)

    calls = tmp_path / "systemctl.calls"
    fake = _fake_systemctl(
        tmp_path / "systemctl",
        "if [ \"$*\" = 'start pagentos-bluegreen-reconcile.service' ]; then exit 9; fi\n",
    )
    completed = _run(
        INSTALLER,
        APPROVED,
        env=_install_env(tmp_path, app_root, PAGENTOS_SYSTEMCTL=_shell_path(fake)),
    )
    assert completed.returncode == 9
    assert "previous monitor restored" in completed.stderr
    for path, content in previous.items():
        assert path.read_bytes() == content
    recorded = calls.read_text(encoding="utf-8").splitlines()
    assert "enable pagentos-bluegreen-reconcile.timer" in recorded
    assert "start pagentos-bluegreen-reconcile.timer" in recorded


def test_upgrade_waits_for_an_already_running_reconcile(tmp_path: Path) -> None:
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    calls = tmp_path / "systemctl.calls"
    active_count = tmp_path / "active.count"
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "if [ \"$*\" = 'is-active --quiet pagentos-bluegreen-reconcile.service' ]; then\n"
        "  n=$(cat \"$PAGENTOS_TEST_ACTIVE_COUNT\" 2>/dev/null || echo 0)\n"
        "  n=$((n + 1)); echo \"$n\" > \"$PAGENTOS_TEST_ACTIVE_COUNT\"\n"
        "  [ \"$n\" -eq 1 ] && exit 0 || exit 1\n"
        "fi\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    completed = _run(
        INSTALLER,
        APPROVED,
        env=_install_env(
            tmp_path,
            app_root,
            PAGENTOS_SYSTEMCTL=_shell_path(fake),
            PAGENTOS_TEST_ACTIVE_COUNT=_shell_path(active_count),
            PAGENTOS_RECOVERY_WAIT_STEP_S="0",
        ),
    )
    assert completed.returncode == 0, completed.stderr
    recorded = calls.read_text(encoding="utf-8").splitlines()
    service_checks = [
        index
        for index, call in enumerate(recorded)
        if call == "is-active --quiet pagentos-bluegreen-reconcile.service"
    ]
    assert len(service_checks) == 2
    assert service_checks[-1] < recorded.index("daemon-reload")
    assert service_checks[-1] < recorded.index("start pagentos-bluegreen-reconcile.service")


# ---- a rollback after the lock was let go (third review, finding 1) ---------------------


def _previous_monitor(tmp_path: Path) -> dict[Path, bytes]:
    systemd_dir = tmp_path / "systemd"
    recovery_root = tmp_path / "recovery"
    systemd_dir.mkdir()
    recovery_root.mkdir()
    previous = {
        systemd_dir / SERVICE.name: b"old service\n",
        systemd_dir / TIMER.name: b"old timer\n",
        recovery_root / "reconcile.sh": b"old recovery\n",
        recovery_root / COMPOSE.name: b"old compose\n",
        recovery_root / "nginx.conf": b"old nginx\n",
        recovery_root / "reconcile.sha256": b"old digest\n",
        recovery_root / "APPROVED_SHA": (OTHER + "\n").encode("ascii"),
    }
    for path, content in previous.items():
        path.write_bytes(content)
    return previous


def test_a_failed_proof_run_takes_the_lock_back_before_restoring_a_byte(tmp_path: Path) -> None:
    # The lock is let go for the proof run (a reconcile takes it). A proof run that fails -
    # possibly because a release took the lock in that instant - used to restore the
    # previous monitor's files with no lock held at all.
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    previous = _previous_monitor(tmp_path)
    fake = _fake_systemctl(
        tmp_path / "systemctl",
        "if [ \"$*\" = 'start pagentos-bluegreen-reconcile.service' ]; then exit 9; fi\n",
    )

    completed = _run(
        INSTALLER,
        APPROVED,
        env=_install_env(tmp_path, app_root, PAGENTOS_SYSTEMCTL=_shell_path(fake)),
    )

    assert completed.returncode == 9
    assert "previous monitor restored" in completed.stderr
    for path, content in previous.items():
        assert path.read_bytes() == content
    recorded = (tmp_path / "systemctl.calls").read_text(encoding="utf-8").splitlines()
    locks = [i for i, call in enumerate(recorded) if call == "flock -w 1200 9"]
    failed_proof = recorded.index("start pagentos-bluegreen-reconcile.service")
    assert len(locks) == 2 and locks[0] < failed_proof < locks[1]
    assert "daemon-reload" in recorded[locks[1] :]


def test_a_rollback_that_cannot_take_the_lock_back_restores_nothing_and_says_where(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    _previous_monitor(tmp_path)
    flock = tmp_path / "flock-once"
    flock.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'flock %s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "n=$(cat \"$PAGENTOS_TEST_FLOCK_COUNT\" 2>/dev/null || echo 0)\n"
        "n=$((n + 1)); echo \"$n\" > \"$PAGENTOS_TEST_FLOCK_COUNT\"\n"
        "[ \"$n\" -eq 1 ]\n",
        encoding="utf-8",
    )
    flock.chmod(flock.stat().st_mode | stat.S_IXUSR)
    fake = _fake_systemctl(
        tmp_path / "systemctl",
        "if [ \"$*\" = 'start pagentos-bluegreen-reconcile.service' ]; then exit 9; fi\n",
    )

    completed = _run(
        INSTALLER,
        APPROVED,
        env=_install_env(
            tmp_path,
            app_root,
            PAGENTOS_SYSTEMCTL=_shell_path(fake),
            PAGENTOS_FLOCK=_shell_path(flock),
            PAGENTOS_TEST_FLOCK_COUNT=_shell_path(tmp_path / "flock.count"),
        ),
    )

    assert completed.returncode == 9
    assert "stayed held" in completed.stderr and "nothing was restored" in completed.stderr
    # Nothing restored: the candidate's bundle is still what the failed install placed...
    approved = (tmp_path / "recovery" / "APPROVED_SHA").read_text(encoding="utf-8")
    assert approved == APPROVED + "\n"
    # ...its timer is stopped, and the previous monitor is where the message says.
    recorded = (tmp_path / "systemctl.calls").read_text(encoding="utf-8").splitlines()
    assert recorded[-1] == "disable --now pagentos-bluegreen-reconcile.timer"
    kept = completed.stderr.strip().rsplit(" kept in ", 1)[1]
    shown = subprocess.run(
        [_bash(), "-c", 'cat "$1/APPROVED_SHA"', "_", kept],
        text=True,
        capture_output=True,
        check=True,
    )
    assert shown.stdout == OTHER + "\n"


def test_the_rollback_restores_under_the_real_lock_and_lets_it_go_for_the_old_timer(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("Git for Windows has no flock; Linux CI exercises the kernel lock")
    if shutil.which("flock") is None:
        pytest.skip("util-linux flock is required by the production host contract")
    app_root = tmp_path / "host" / "app"
    _seed_app_root(app_root)
    previous = _previous_monitor(tmp_path)
    lock = app_root.parent / ".bluegreen-operation.lock"
    probe = tmp_path / "probe.log"
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "try_lock() { if flock -n \"$PAGENTOS_TEST_LOCK\" true; then echo \"$1 free\"; "
        "else echo \"$1 held\"; fi >> \"$PAGENTOS_TEST_PROBE\"; }\n"
        "if [ \"$*\" = 'is-active --quiet pagentos-bluegreen-reconcile.service' ]; "
        "then exit 1; fi\n"
        "if [ \"$*\" = 'daemon-reload' ]; then try_lock daemon-reload; fi\n"
        "if [ \"$*\" = 'start pagentos-bluegreen-reconcile.service' ]; then "
        "try_lock proof-run; exit 9; fi\n"
        "if [ \"$*\" = 'start pagentos-bluegreen-reconcile.timer' ]; then "
        "try_lock old-timer; fi\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = _install_env(
        tmp_path,
        app_root,
        PAGENTOS_SYSTEMCTL=_shell_path(fake),
        PAGENTOS_TEST_LOCK=_shell_path(lock),
        PAGENTOS_TEST_PROBE=_shell_path(probe),
    )
    env.pop("PAGENTOS_FLOCK")  # the real one

    completed = _run(INSTALLER, APPROVED, env=env)

    assert completed.returncode == 9, completed.stderr
    for path, content in previous.items():
        assert path.read_bytes() == content
    assert probe.read_text(encoding="utf-8").splitlines() == [
        "daemon-reload free",
        "proof-run free",
        "daemon-reload held",
        "old-timer free",
    ]


# ---- removal ----------------------------------------------------------------------------


def _installed(tmp_path: Path) -> tuple[Path, Path]:
    """A host after a successful install: units and the pinned bundle in place."""
    systemd_dir = tmp_path / "systemd"
    recovery_root = tmp_path / "recovery"
    systemd_dir.mkdir()
    recovery_root.mkdir()
    (systemd_dir / SERVICE.name).write_bytes(SERVICE.read_bytes())
    (systemd_dir / TIMER.name).write_bytes(TIMER.read_bytes())
    for name in BUNDLE_FILES:
        (recovery_root / name).write_text(f"{name}\n", encoding="utf-8")
    return systemd_dir, recovery_root


def _uninstall_env(tmp_path: Path, fake: Path, **extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "PAGENTOS_ALLOW_NONROOT": "1",
        "PAGENTOS_SYSTEMD_DIR": _shell_path(tmp_path / "systemd"),
        "PAGENTOS_RECOVERY_ROOT": _shell_path(tmp_path / "recovery"),
        "PAGENTOS_SYSTEMCTL": _shell_path(fake),
        "PAGENTOS_TEST_CALLS": _shell_path(tmp_path / "systemctl.calls"),
        **extra,
    }


def test_uninstall_stops_the_timer_first_and_removes_exactly_what_install_placed(
    tmp_path: Path,
) -> None:
    systemd_dir, recovery_root = _installed(tmp_path)
    # Things that are NOT the monitor's: another unit, a file an operator left in the
    # bundle directory, and the release markers. None of them is the uninstaller's to take.
    (systemd_dir / "docker.service").write_text("[Unit]\n", encoding="utf-8")
    (recovery_root / "operator-notes.txt").write_text("keep\n", encoding="utf-8")
    marker = tmp_path / "RELEASE"
    marker.write_text(APPROVED + "\n", encoding="utf-8")
    fake = _fake_systemctl(
        tmp_path / "systemctl",
        "if [ \"$*\" = 'is-enabled --quiet pagentos-bluegreen-reconcile.timer' ]; "
        "then exit 1; fi\n"
        "if [ \"$*\" = 'is-active --quiet pagentos-bluegreen-reconcile.timer' ]; "
        "then exit 1; fi\n",
    )

    completed = _run(UNINSTALLER, env=_uninstall_env(tmp_path, fake))

    assert completed.returncode == 0, completed.stderr
    assert "RECOVERY SUPERVISOR REMOVED" in completed.stdout
    assert not (systemd_dir / SERVICE.name).exists()
    assert not (systemd_dir / TIMER.name).exists()
    for name in BUNDLE_FILES:
        assert not (recovery_root / name).exists(), name
    assert (systemd_dir / "docker.service").exists()
    assert (recovery_root / "operator-notes.txt").read_text(encoding="utf-8") == "keep\n"
    assert marker.read_text(encoding="utf-8").strip() == APPROVED
    recorded = (tmp_path / "systemctl.calls").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "disable --now pagentos-bluegreen-reconcile.timer"
    assert "daemon-reload" in recorded
    assert recorded.index("disable --now pagentos-bluegreen-reconcile.timer") < recorded.index(
        "daemon-reload"
    )


def test_uninstall_removes_the_bundle_directory_only_when_nothing_else_is_in_it(
    tmp_path: Path,
) -> None:
    _, recovery_root = _installed(tmp_path)
    fake = _fake_systemctl(
        tmp_path / "systemctl",
        "case \"$*\" in 'is-enabled --quiet '*|'is-active --quiet '*) exit 1;; esac\n",
    )

    completed = _run(UNINSTALLER, env=_uninstall_env(tmp_path, fake))

    assert completed.returncode == 0, completed.stderr
    assert not recovery_root.exists()


def test_uninstall_on_a_host_without_the_monitor_changes_nothing_and_says_so(
    tmp_path: Path,
) -> None:
    fake = _fake_systemctl(
        tmp_path / "systemctl",
        "case \"$*\" in 'is-enabled --quiet '*|'is-active --quiet '*) exit 1;; esac\n",
    )

    completed = _run(UNINSTALLER, env=_uninstall_env(tmp_path, fake))

    assert completed.returncode == 0, completed.stderr
    assert "nothing to remove" in completed.stdout


def test_uninstall_never_pulls_files_from_under_a_running_reconcile(tmp_path: Path) -> None:
    systemd_dir, recovery_root = _installed(tmp_path)
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "if [ \"$*\" = 'is-active --quiet pagentos-bluegreen-reconcile.service' ]; "
        "then exit 0; fi\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    completed = _run(
        UNINSTALLER,
        env=_uninstall_env(
            tmp_path,
            fake,
            PAGENTOS_RECOVERY_WAIT_TRIES="2",
            PAGENTOS_RECOVERY_WAIT_STEP_S="0",
        ),
    )

    # The timer is already disabled (no new run can start); nothing was removed from under
    # the run that is still going. The operator retries once it has finished.
    assert completed.returncode == 6, completed.stderr
    assert "nothing was removed" in completed.stderr
    assert (systemd_dir / SERVICE.name).exists()
    for name in BUNDLE_FILES:
        assert (recovery_root / name).exists(), name
    recorded = (tmp_path / "systemctl.calls").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "disable --now pagentos-bluegreen-reconcile.timer"


def test_uninstall_fails_loudly_if_the_timer_survives_removal(tmp_path: Path) -> None:
    _installed(tmp_path)
    fake = _fake_systemctl(
        tmp_path / "systemctl",
        "if [ \"$*\" = 'is-enabled --quiet pagentos-bluegreen-reconcile.timer' ]; "
        "then exit 0; fi\n",
    )

    completed = _run(UNINSTALLER, env=_uninstall_env(tmp_path, fake))

    assert completed.returncode == 7, completed.stderr
    assert "still enabled or active" in completed.stderr


@pytest.mark.parametrize("script", [INSTALLER, UNINSTALLER], ids=["install", "uninstall"])
def test_both_directions_refuse_to_run_unprivileged(tmp_path: Path, script: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root: the privilege refusal cannot be observed")
    env = {**os.environ}
    env.pop("PAGENTOS_ALLOW_NONROOT", None)

    completed = _run(script, APPROVED, env=env)

    assert completed.returncode == 1
    assert "run as root" in completed.stderr
