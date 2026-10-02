"""After an orphan is reaped, the ONE recovery launch waits for the profile's lock to be free.

2026-10-02, the full gate on a loaded machine: ``test_foreign_chrome_on_profile_is_reaped_then_
one_recovery_launch`` failed with Chromium's "Lock file can not be created ... Failed to create a
ProcessSingleton for your profile directory". The orphan's MAIN process had exited - that is what
``reap_orphan_chrome`` waited for - but its dying children still held the profile's ``lockfile``
for a moment, and the recovery launch, which is deliberately never retried, met it. A kill is
not an exit, and an exit is not a released file.
"""

from __future__ import annotations

import ctypes
import json
import sys
from pathlib import Path

import pytest

from browser_agent import lifecycle


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_the_wait_returns_at_once_when_the_lock_is_free(tmp_path: Path) -> None:
    clock = _Clock()
    asked: list[Path] = []

    def is_free(path: Path) -> bool:
        asked.append(path)
        return True

    assert lifecycle.wait_for_profile_lock_free(
        tmp_path, is_free=is_free, sleep=clock.sleep, now=clock.monotonic
    )
    assert clock.sleeps == [], "a free lock costs no sleep: a clean launch pays nothing"
    assert asked == [tmp_path / lifecycle.PROFILE_LOCK_NAME]


def test_the_wait_polls_until_the_dying_children_let_the_lock_go(tmp_path: Path) -> None:
    clock = _Clock()
    answers = iter([False, False, True])
    assert lifecycle.wait_for_profile_lock_free(
        tmp_path,
        timeout_s=10.0,
        poll_s=0.2,
        is_free=lambda _path: next(answers),
        sleep=clock.sleep,
        now=clock.monotonic,
    )
    assert clock.sleeps == [0.2, 0.2]


def test_the_wait_is_bounded_and_says_when_the_lock_is_still_held(tmp_path: Path) -> None:
    clock = _Clock()
    assert not lifecycle.wait_for_profile_lock_free(
        tmp_path,
        timeout_s=1.0,
        poll_s=0.25,
        is_free=lambda _path: False,
        sleep=clock.sleep,
        now=clock.monotonic,
    )
    assert clock.now == pytest.approx(1.0), "it stops at the deadline, it does not hang"


def test_reaping_an_orphan_waits_for_the_profiles_lock_after_the_processes_are_gone(
    tmp_path: Path,
) -> None:
    order: list[str] = []

    def wait_pids(pids: list[int], *, timeout_s: float) -> list[int]:
        order.append(f"pids {pids}")
        return []

    def wait_lock(profile_dir: Path | str, *, timeout_s: float) -> bool:
        order.append(f"lock {Path(profile_dir).name} {timeout_s}")
        return True

    command_line = f'chrome.exe --user-data-dir="{tmp_path}"'
    scan = json.dumps({"ProcessId": 41, "CommandLine": command_line})
    reaped = lifecycle.reap_orphan_chrome(
        tmp_path,
        scan=lambda: scan,
        kill=lambda pid: order.append(f"kill {pid}"),
        wait=wait_pids,
        wait_lock=wait_lock,
        timeout_s=3.0,
    )
    assert reaped == [41]
    assert order == ["kill 41", "pids [41]", f"lock {tmp_path.name} 3.0"], (
        "the lock is waited for AFTER the processes are gone, with the same bound"
    )


def test_a_launch_that_reaps_nothing_does_not_wait_for_any_lock(tmp_path: Path) -> None:
    called: list[str] = []
    reaped = lifecycle.reap_orphan_chrome(
        tmp_path,
        scan=lambda: "",
        kill=lambda pid: called.append("kill"),
        wait=lambda pids, *, timeout_s: called.append("wait") or [],
        wait_lock=lambda profile_dir, *, timeout_s: called.append("lock") or True,
    )
    assert reaped == [] and called == []


@pytest.mark.skipif(
    sys.platform != "win32", reason="Chromium's lockfile is a Windows share-none handle"
)
def test_the_real_probe_tells_a_held_lockfile_from_a_free_one(tmp_path: Path) -> None:
    lock = tmp_path / lifecycle.PROFILE_LOCK_NAME
    assert lifecycle.profile_lock_is_free(lock), "no file: nobody holds it"
    lock.write_bytes(b"")
    assert lifecycle.profile_lock_is_free(lock), "a file nobody has open is free"
    before = lock.read_bytes()

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    generic_read_write, share_none, open_existing = 0xC0000000, 0, 3
    handle = kernel32.CreateFileW(
        str(lock), generic_read_write, share_none, None, open_existing, 0, None
    )
    assert handle not in (None, ctypes.c_void_p(-1).value), "the test could not take the lock"
    try:
        assert not lifecycle.profile_lock_is_free(lock), (
            "held with no sharing, as Chromium holds it"
        )
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    assert lifecycle.profile_lock_is_free(lock), "free again once the holder let go"
    assert lock.exists() and lock.read_bytes() == before, "the probe neither creates nor changes it"
