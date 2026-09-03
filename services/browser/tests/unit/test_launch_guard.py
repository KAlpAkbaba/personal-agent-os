"""Pure/unit coverage for browser_agent.launch_guard (no browser, no Chrome).

The Windows-real mutex and job-object paths are exercised with real
processes in tests/browser/test_lifecycle_e2e.py; here the breaker policy,
the ownership record and the lock naming are pinned with fakes, plus one
real cross-process mutex check on Windows (a child python process, no
browser).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from browser_agent import launch_guard
from browser_agent.errors import BrowserError, ErrorClass


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# --------------------------------------------------------------------------- #
# naming
# --------------------------------------------------------------------------- #


def test_lock_name_is_stable_and_normalized() -> None:
    a = launch_guard.lock_name_for_profile(r"C:\ProgramData\PagentOS\companion\browser\profile")
    b = launch_guard.lock_name_for_profile(r"c:\programdata\pagentos\companion\browser\profile\\")
    c = launch_guard.lock_name_for_profile(r"C:\ProgramData\PagentOS\companion\browser\profile2")
    assert a == b
    assert a != c
    assert a.startswith("Local\\PagentOS.BrowserProfile.")


# --------------------------------------------------------------------------- #
# breaker
# --------------------------------------------------------------------------- #


def test_clean_launches_never_trip_the_breaker(tmp_path: Path) -> None:
    clock = FakeClock()
    breaker = launch_guard.LaunchBreaker(tmp_path, now=clock, max_recovery_launches=1)
    profile = tmp_path / "profile"
    for _ in range(20):
        decision = breaker.check(profile, about_to_recover=False)
        assert decision.allowed
        breaker.record_launch(profile, kind="clean")
    assert breaker.current_fault() is None
    assert not breaker.fault_file.exists()


def test_recovery_launches_trip_the_breaker_and_write_a_durable_fault(tmp_path: Path) -> None:
    clock = FakeClock()
    breaker = launch_guard.LaunchBreaker(tmp_path, now=clock, max_recovery_launches=3)
    profile = tmp_path / "profile"
    for _ in range(3):
        assert breaker.check(profile, about_to_recover=True).allowed
        breaker.record_launch(profile, kind="recovery")
    with pytest.raises(BrowserError) as excinfo:
        breaker.check(profile, about_to_recover=True)
    err = excinfo.value
    assert err.error_class is ErrorClass.BROWSER_LIFECYCLE_VIOLATION
    assert err.retryable is False
    assert err.evidence["guard"] == "launch_breaker"
    assert err.evidence["reason"] == "launch_rate_exceeded"
    fault = json.loads(breaker.fault_file.read_text(encoding="utf-8"))
    assert fault["fault"] == "browser_launch_rate_exceeded"
    assert fault["recovery_launches_in_window"] == 4
    assert "message_tr" in fault

    # A *clean* launch is refused too while the fault is active (durable, across
    # a fresh breaker instance = a restarted worker)
    restarted = launch_guard.LaunchBreaker(tmp_path, now=clock)
    with pytest.raises(BrowserError) as excinfo2:
        restarted.check(profile, about_to_recover=False)
    assert excinfo2.value.evidence["reason"] == "durable_fault_active"

    # ...until the fault ages out
    clock.advance(launch_guard.FAULT_TTL_S + 1)
    assert restarted.check(profile, about_to_recover=False).allowed


def test_breaker_window_slides(tmp_path: Path) -> None:
    clock = FakeClock()
    breaker = launch_guard.LaunchBreaker(tmp_path, now=clock, max_recovery_launches=2)
    profile = tmp_path / "profile"
    breaker.record_launch(profile, kind="recovery")
    breaker.record_launch(profile, kind="recovery")
    with pytest.raises(BrowserError):
        breaker.check(profile, about_to_recover=True)
    breaker.clear_fault()
    clock.advance(launch_guard.BREAKER_WINDOW_S + 1)
    assert breaker.check(profile, about_to_recover=True).allowed


def test_breaker_counts_per_profile(tmp_path: Path) -> None:
    clock = FakeClock()
    breaker = launch_guard.LaunchBreaker(tmp_path, now=clock, max_recovery_launches=1)
    breaker.record_launch(tmp_path / "a", kind="recovery")
    assert breaker.recovery_launches_in_window(str(tmp_path / "a").lower()) == 1
    assert breaker.check(tmp_path / "b", about_to_recover=True).allowed


def test_breaker_tolerates_corrupt_files(tmp_path: Path) -> None:
    breaker = launch_guard.LaunchBreaker(tmp_path)
    breaker.launch_log.write_text("{not json", encoding="utf-8")
    breaker.fault_file.write_text("[]", encoding="utf-8")
    assert breaker.current_fault() is None
    assert breaker.check(tmp_path / "p", about_to_recover=False).allowed
    breaker.record_launch(tmp_path / "p", kind="clean")
    assert len(json.loads(breaker.launch_log.read_text(encoding="utf-8"))) == 1


def test_record_launch_rejects_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        launch_guard.LaunchBreaker(tmp_path).record_launch(tmp_path, kind="whatever")


# --------------------------------------------------------------------------- #
# ownership record
# --------------------------------------------------------------------------- #


def test_ownership_record_roundtrip_and_update(tmp_path: Path) -> None:
    record = launch_guard.OwnershipRecord(tmp_path)
    assert record.read() is None
    record.write({"worker_pid": 1, "chrome_root_pid": 2, "closed_at": None})
    assert record.read() == {"worker_pid": 1, "chrome_root_pid": 2, "closed_at": None}
    merged = record.update(closed_at="2026-09-03T18:00:00Z", browser_pid_exited=True)
    assert merged["worker_pid"] == 1
    assert record.read()["browser_pid_exited"] is True
    assert record.path.name == launch_guard.OWNERSHIP_FILE_NAME


# --------------------------------------------------------------------------- #
# launch lock (real OS primitive, cross-process, no browser)
# --------------------------------------------------------------------------- #

_CHILD = r"""
import sys
from browser_agent import launch_guard
from browser_agent.errors import BrowserError
lock = launch_guard.LaunchLock(sys.argv[1])
try:
    lock.acquire()
except BrowserError as exc:
    print("REFUSED " + exc.evidence["guard"])
else:
    print("ACQUIRED")
"""


def test_launch_lock_is_exclusive_across_processes(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    lock = launch_guard.LaunchLock(profile)
    lock.acquire()
    assert lock.held
    try:
        out = subprocess.run(
            [sys.executable, "-c", _CHILD, str(profile)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert out.stdout.strip().splitlines()[-1] == "REFUSED launch_lock", out.stdout + out.stderr
    finally:
        lock.release()
    assert not lock.held
    out = subprocess.run(
        [sys.executable, "-c", _CHILD, str(profile)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert out.stdout.strip().splitlines()[-1] == "ACQUIRED", out.stdout + out.stderr


def test_launch_lock_acquire_is_idempotent_in_process(tmp_path: Path) -> None:
    lock = launch_guard.LaunchLock(tmp_path / "profile")
    lock.acquire()
    lock.acquire()
    lock.release()
    lock.release()
    assert not lock.held


@pytest.mark.skipif(not launch_guard.IS_WINDOWS, reason="Windows job objects")
def test_kill_on_close_job_terminates_assigned_process() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    job = launch_guard.KillOnCloseJob()
    try:
        assert job.assign(child.pid) is True
        assert child.poll() is None
    finally:
        job.close()
    # sleep(60) cannot have finished on its own: returning within 10 s proves the
    # kernel terminated it when the only job handle was closed.
    child.wait(timeout=10)
