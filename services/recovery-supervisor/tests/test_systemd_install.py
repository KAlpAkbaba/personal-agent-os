from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = REPO_ROOT / "scripts" / "cloud" / "install-recovery-supervisor.sh"
SERVICE = REPO_ROOT / "infra" / "systemd" / "pagentos-bluegreen-reconcile.service"
TIMER = REPO_ROOT / "infra" / "systemd" / "pagentos-bluegreen-reconcile.timer"
RECONCILE = REPO_ROOT / "scripts" / "cloud" / "release-cloud-core-bluegreen.sh"


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


def test_installer_proves_recovery_before_enabling_timer(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    recovery = app_root / "scripts" / "cloud" / "release-cloud-core-bluegreen.sh"
    recovery.parent.mkdir(parents=True)
    recovery.write_bytes(RECONCILE.read_bytes())

    calls = tmp_path / "systemctl.calls"
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "if [ \"${PAGENTOS_TEST_FAIL_PROOF:-0}\" = 1 ] && "
        "[ \"$*\" = 'start pagentos-bluegreen-reconcile.service' ]; then exit 9; fi\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    systemd_dir = tmp_path / "systemd"
    recovery_root = tmp_path / "recovery"
    env = {
        **os.environ,
        "PAGENTOS_ALLOW_NONROOT": "1",
        "PAGENTOS_APP_ROOT": _shell_path(app_root),
        "PAGENTOS_SYSTEMD_DIR": _shell_path(systemd_dir),
        "PAGENTOS_RECOVERY_ROOT": _shell_path(recovery_root),
        "PAGENTOS_SYSTEMCTL": _shell_path(fake),
        "PAGENTOS_TEST_CALLS": _shell_path(calls),
    }

    completed = subprocess.run(
        [_bash(), _shell_path(INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (systemd_dir / SERVICE.name).read_bytes() == SERVICE.read_bytes()
    assert (systemd_dir / TIMER.name).read_bytes() == TIMER.read_bytes()
    assert (recovery_root / "reconcile.sh").read_bytes() == RECONCILE.read_bytes()
    digest = (recovery_root / "reconcile.sha256").read_text(encoding="utf-8")
    assert "reconcile.sh" in digest
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "is-enabled --quiet pagentos-bluegreen-reconcile.timer",
        "is-active --quiet pagentos-bluegreen-reconcile.timer",
        "stop pagentos-bluegreen-reconcile.timer",
        "daemon-reload",
        "start pagentos-bluegreen-reconcile.service",
        "enable --now pagentos-bluegreen-reconcile.timer",
        "is-enabled pagentos-bluegreen-reconcile.timer",
        "is-active pagentos-bluegreen-reconcile.timer",
    ]

    recovery.write_text("#!/usr/bin/env bash\n# candidate tree changed later\n")
    assert (recovery_root / "reconcile.sh").read_bytes() == RECONCILE.read_bytes()


def test_installer_refuses_before_systemd_when_recovery_action_is_missing(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "systemctl"
    fake.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    completed = subprocess.run(
        [_bash(), _shell_path(INSTALLER)],
        env={
            **os.environ,
            "PAGENTOS_ALLOW_NONROOT": "1",
            "PAGENTOS_APP_ROOT": _shell_path(tmp_path / "missing-app"),
            "PAGENTOS_SYSTEMD_DIR": _shell_path(tmp_path / "systemd"),
            "PAGENTOS_SYSTEMCTL": _shell_path(fake),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "required recovery input is absent" in completed.stderr
    assert not (tmp_path / "systemd").exists()


def test_installer_refuses_an_older_installed_recovery_action(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    recovery = app_root / "scripts" / "cloud" / RECONCILE.name
    recovery.parent.mkdir(parents=True)
    recovery.write_text("#!/usr/bin/env bash\n# old copy without the operation lock\n")
    completed = subprocess.run(
        [_bash(), _shell_path(INSTALLER)],
        env={
            **os.environ,
            "PAGENTOS_ALLOW_NONROOT": "1",
            "PAGENTOS_APP_ROOT": _shell_path(app_root),
            "PAGENTOS_SYSTEMD_DIR": _shell_path(tmp_path / "systemd"),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 3
    assert "installed recovery action is not the reviewed candidate" in completed.stderr
    assert not (tmp_path / "systemd").exists()


def test_failed_upgrade_restores_and_restarts_the_previous_monitor(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_action = app_root / "scripts" / "cloud" / RECONCILE.name
    app_action.parent.mkdir(parents=True)
    app_action.write_bytes(RECONCILE.read_bytes())
    systemd_dir = tmp_path / "systemd"
    recovery_root = tmp_path / "recovery"
    systemd_dir.mkdir()
    recovery_root.mkdir()
    previous = {
        systemd_dir / SERVICE.name: b"old service\n",
        systemd_dir / TIMER.name: b"old timer\n",
        recovery_root / "reconcile.sh": b"old recovery\n",
        recovery_root / "reconcile.sha256": b"old digest\n",
    }
    for path, content in previous.items():
        path.write_bytes(content)

    calls = tmp_path / "systemctl.calls"
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "if [ \"$*\" = 'start pagentos-bluegreen-reconcile.service' ]; then exit 9; fi\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    completed = subprocess.run(
        [_bash(), _shell_path(INSTALLER)],
        env={
            **os.environ,
            "PAGENTOS_ALLOW_NONROOT": "1",
            "PAGENTOS_APP_ROOT": _shell_path(app_root),
            "PAGENTOS_SYSTEMD_DIR": _shell_path(systemd_dir),
            "PAGENTOS_RECOVERY_ROOT": _shell_path(recovery_root),
            "PAGENTOS_SYSTEMCTL": _shell_path(fake),
            "PAGENTOS_TEST_CALLS": _shell_path(calls),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 9
    assert "previous monitor restored" in completed.stderr
    for path, content in previous.items():
        assert path.read_bytes() == content
    recorded = calls.read_text(encoding="utf-8").splitlines()
    assert "enable pagentos-bluegreen-reconcile.timer" in recorded
    assert "start pagentos-bluegreen-reconcile.timer" in recorded
