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
COMPOSE = REPO_ROOT / "infra" / "docker" / "docker-compose.prod.yml"
NGINX = REPO_ROOT / "infra" / "docker" / "edge" / "nginx.conf"


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


def _seed_app_root(app_root: Path, *, action: bytes | None = None) -> None:
    files = {
        app_root / "scripts" / "cloud" / RECONCILE.name: action or RECONCILE.read_bytes(),
        app_root / "infra" / "docker" / COMPOSE.name: COMPOSE.read_bytes(),
        app_root / "infra" / "docker" / "edge" / NGINX.name: NGINX.read_bytes(),
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


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
    app_root = tmp_path / "app"
    _seed_app_root(app_root)
    recovery = app_root / "scripts" / "cloud" / RECONCILE.name

    calls = tmp_path / "systemctl.calls"
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "if [ \"$*\" = 'is-active --quiet pagentos-bluegreen-reconcile.service' ]; "
        "then exit 1; fi\n"
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
    assert (recovery_root / COMPOSE.name).read_bytes() == COMPOSE.read_bytes()
    assert (recovery_root / "nginx.conf").read_bytes() == NGINX.read_bytes()
    digest = (recovery_root / "reconcile.sha256").read_text(encoding="utf-8")
    assert "reconcile.sh" in digest
    assert calls.read_text(encoding="utf-8").splitlines() == [
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
    _seed_app_root(app_root, action=b"#!/usr/bin/env bash\n# old copy\n")
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
    }
    for path, content in previous.items():
        path.write_bytes(content)

    calls = tmp_path / "systemctl.calls"
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$PAGENTOS_TEST_CALLS\"\n"
        "if [ \"$*\" = 'is-active --quiet pagentos-bluegreen-reconcile.service' ]; "
        "then exit 1; fi\n"
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


def test_upgrade_waits_for_an_already_running_reconcile(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
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
    completed = subprocess.run(
        [_bash(), _shell_path(INSTALLER)],
        env={
            **os.environ,
            "PAGENTOS_ALLOW_NONROOT": "1",
            "PAGENTOS_APP_ROOT": _shell_path(app_root),
            "PAGENTOS_SYSTEMD_DIR": _shell_path(tmp_path / "systemd"),
            "PAGENTOS_RECOVERY_ROOT": _shell_path(tmp_path / "recovery"),
            "PAGENTOS_SYSTEMCTL": _shell_path(fake),
            "PAGENTOS_TEST_CALLS": _shell_path(calls),
            "PAGENTOS_TEST_ACTIVE_COUNT": _shell_path(active_count),
            "PAGENTOS_RECOVERY_WAIT_STEP_S": "0",
        },
        text=True,
        capture_output=True,
        check=False,
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
