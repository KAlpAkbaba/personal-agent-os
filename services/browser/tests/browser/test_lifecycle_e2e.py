"""Browser lifecycle regression acceptance (owner-machine incidents 2026-09-03).

Invariant under test: one research job = one worker + one Chrome process on
the PagentOS profile + one window, bounded tabs, explicit identity on every
result, and NOTHING left behind on any exit path (clean close, worker crash,
foreign holder on the profile, second worker process, command timeout,
cancellation, launch-rate fault).

Everything here runs HEADLESS (tests/conftest.py forces it outside `live`),
and the OS-level facts (pid on the profile, process gone, job object,
mutex) are asserted against the real Windows process table via
browser_agent.lifecycle, never inferred from Playwright state.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from browser_agent import launch_guard, lifecycle
from browser_agent.errors import BrowserError, ErrorClass

pytestmark = [pytest.mark.browser, pytest.mark.skipif(sys.platform != "win32", reason="Windows")]

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_READLINE_TIMEOUT_S = 30.0


def _profile_pids(worker) -> list[int]:
    return lifecycle.find_profile_chrome_pids(worker._profile_dir)


async def _open(worker, session_id: str = "job-1", **extra):
    payload = {
        "session_id": session_id,
        "profile": "research",
        "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": False},
        **extra,
    }
    return await worker._execute("browser.session_open", payload)


# --------------------------------------------------------------------------- #
# 1. twenty sequential operations, one browser, then nothing left
# --------------------------------------------------------------------------- #


async def test_twenty_sequential_ops_use_one_browser_and_leave_nothing(worker, site_url) -> None:
    opened = await _open(worker, max_tabs=4)
    life = opened["lifecycle"]
    root_pid = life["browser_pid"]
    session_uid = life["session_uid"]
    assert root_pid is not None and session_uid
    assert life["reused"] is False
    assert life["launch_kind"] == "clean"
    assert life["job_object_assigned"] is True
    assert life["launch_lock"] == launch_guard.lock_name_for_profile(worker._profile_dir)
    assert life["worker_pid"] == launch_guard.os.getpid()
    assert _profile_pids(worker) == [root_pid]

    ops = [
        ("browser.navigate", {"url": f"{site_url}/article.html"}),
        ("browser.extract", {"mode": "metadata"}),
        ("browser.tab_new", {"url": f"{site_url}/index.html"}),
        ("browser.tab_list", {}),
        ("browser.tab_select", {"index": 0}),
        ("browser.snapshot", {}),
        ("browser.tab_close", {"index": 1}),
        ("browser.navigate", {"url": f"{site_url}/index.html"}),
        ("browser.back", {}),
        ("browser.forward", {}),
    ] * 2
    assert len(ops) == 20
    for n, (capability, payload) in enumerate(ops, start=1):
        result = await worker._execute(capability, {"session_id": "job-1", **payload})
        life = result["lifecycle"]
        assert life["browser_pid"] == root_pid, f"op {n} {capability}: browser pid changed"
        assert life["session_uid"] == session_uid, f"op {n} {capability}: session uid changed"
        assert life["tab_count"] <= life["max_tabs"] == 4
        assert life["max_windows"] == 1
        if n % 5 == 0:
            assert _profile_pids(worker) == [root_pid], f"after op {n}: extra Chrome on profile"

    ownership = json.loads((worker._data_dir / launch_guard.OWNERSHIP_FILE_NAME).read_text("utf-8"))
    assert ownership["chrome_root_pid"] == root_pid
    assert ownership["browser_session_id"] == session_uid
    assert ownership["worker_pid"] == launch_guard.os.getpid()
    assert ownership["research_job_id"] == "job-1"
    assert ownership["transport"] == "playwright-pipe"
    assert ownership["closed_at"] is None

    closed = await worker._execute("browser.session_close", {"session_id": "job-1"})
    assert closed["browser_pid_exited"] is True
    assert _profile_pids(worker) == []
    ownership = json.loads((worker._data_dir / launch_guard.OWNERSHIP_FILE_NAME).read_text("utf-8"))
    assert ownership["closed_at"] is not None
    assert ownership["browser_pid_exited"] is True
    launches = json.loads((worker._data_dir / launch_guard.LAUNCH_LOG_NAME).read_text("utf-8"))
    assert [item["kind"] for item in launches] == ["clean"]


# --------------------------------------------------------------------------- #
# 2. tab budget
# --------------------------------------------------------------------------- #


async def test_tab_budget_refuses_before_opening(worker, site_url) -> None:
    await _open(worker, max_tabs=2)
    await worker._execute(
        "browser.tab_new", {"session_id": "job-1", "url": f"{site_url}/index.html"}
    )
    with pytest.raises(BrowserError) as excinfo:
        await worker._execute("browser.tab_new", {"session_id": "job-1"})
    assert excinfo.value.error_class is ErrorClass.BROWSER_LIFECYCLE_VIOLATION
    assert excinfo.value.retryable is False
    tabs = await worker._execute("browser.tab_list", {"session_id": "job-1"})
    assert tabs["lifecycle"]["tab_count"] == 2
    await worker._execute("browser.session_close", {"session_id": "job-1"})
    assert _profile_pids(worker) == []


# --------------------------------------------------------------------------- #
# 3. a second session id must not launch a second browser
# --------------------------------------------------------------------------- #


async def test_second_session_id_on_research_profile_is_refused(worker) -> None:
    opened = await _open(worker, "job-A")
    root_pid = opened["lifecycle"]["browser_pid"]
    with pytest.raises(BrowserError) as excinfo:
        await _open(worker, "job-B")
    assert excinfo.value.error_class is ErrorClass.BROWSER_LIFECYCLE_VIOLATION
    assert _profile_pids(worker) == [root_pid]
    # idempotent re-open of the SAME job is a reuse, never a launch
    again = await _open(worker, "job-A")
    assert again["created"] is False
    assert again["lifecycle"]["reused"] is True
    assert again["lifecycle"]["browser_pid"] == root_pid
    await worker._execute("browser.session_close", {"session_id": "job-A"})
    assert _profile_pids(worker) == []


# --------------------------------------------------------------------------- #
# 4. foreign holder on the profile: reaped once, exactly one launch, recorded
# --------------------------------------------------------------------------- #


async def test_foreign_chrome_on_profile_is_reaped_then_one_recovery_launch(worker) -> None:
    worker._profile_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        foreign = await pw.chromium.launch_persistent_context(
            str(worker._profile_dir), headless=True, channel="chromium"
        )
        try:
            foreign_pids = _profile_pids(worker)
            assert len(foreign_pids) == 1
            opened = await _open(worker)
        finally:
            with pytest.raises(Exception):  # noqa: B017 - the foreign context is dead: reaped
                await foreign.new_page()
    life = opened["lifecycle"]
    assert life["launch_kind"] == "recovery"
    assert life["browser_pid"] not in foreign_pids
    assert _profile_pids(worker) == [life["browser_pid"]]
    launches = json.loads((worker._data_dir / launch_guard.LAUNCH_LOG_NAME).read_text("utf-8"))
    assert launches[-1]["kind"] == "recovery"
    assert launches[-1]["reaped"] == foreign_pids
    await worker._execute("browser.session_close", {"session_id": "job-1"})
    assert _profile_pids(worker) == []


# --------------------------------------------------------------------------- #
# 5. launch-rate circuit breaker: durable fault, no further launches
# --------------------------------------------------------------------------- #


async def test_breaker_trips_on_repeated_recovery_and_blocks_clean_launches(worker) -> None:
    breaker = launch_guard.LaunchBreaker(worker._data_dir, max_recovery_launches=2)
    worker._breaker = breaker
    for _ in range(2):
        breaker.record_launch(worker._profile_dir, kind="recovery")
    worker._profile_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        foreign = await pw.chromium.launch_persistent_context(
            str(worker._profile_dir), headless=True, channel="chromium"
        )
        try:
            with pytest.raises(BrowserError) as excinfo:
                await _open(worker)
        finally:
            with pytest.raises(Exception):  # noqa: B017 - reaped by the worker
                await foreign.new_page()
    assert excinfo.value.error_class is ErrorClass.BROWSER_LIFECYCLE_VIOLATION
    assert excinfo.value.evidence["guard"] == "launch_breaker"
    assert excinfo.value.evidence["reason"] == "launch_rate_exceeded"
    assert breaker.fault_file.exists()
    # the refused launch never created a browser, and the orphan was reaped first
    assert _profile_pids(worker) == []
    # while the fault is active even a clean launch is refused, durably
    with pytest.raises(BrowserError) as excinfo2:
        await _open(worker)
    assert excinfo2.value.evidence["reason"] == "durable_fault_active"
    assert _profile_pids(worker) == []
    # hello surfaces the fault to the companion
    await worker._print_hello()
    breaker.clear_fault()
    opened = await _open(worker)
    assert opened["created"] is True
    await worker._execute("browser.session_close", {"session_id": "job-1"})
    assert _profile_pids(worker) == []


# --------------------------------------------------------------------------- #
# 6. command timeout and cancellation never relaunch, and close leaves nothing
# --------------------------------------------------------------------------- #


async def test_timeout_and_cancellation_keep_one_browser_then_clean_close(worker, site_url) -> None:
    opened = await _open(worker)
    root_pid = opened["lifecycle"]["browser_pid"]
    with pytest.raises(BrowserError):
        await worker._execute(
            "browser.navigate",
            {"session_id": "job-1", "url": f"{site_url}/slow", "timeout_ms": 700},
        )
    assert _profile_pids(worker) == [root_pid]
    task = asyncio.create_task(
        worker._execute(
            "browser.navigate",
            {"session_id": "job-1", "url": f"{site_url}/slow", "timeout_ms": 8000},
        )
    )
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _profile_pids(worker) == [root_pid]
    closed = await worker._execute("browser.session_close", {"session_id": "job-1"})
    assert closed["browser_pid_exited"] is True
    assert _profile_pids(worker) == []
    launches = json.loads((worker._data_dir / launch_guard.LAUNCH_LOG_NAME).read_text("utf-8"))
    assert len(launches) == 1


# --------------------------------------------------------------------------- #
# 7. real worker subprocess: a second worker process is refused by the OS lock,
#    and a hard-killed worker takes its Chrome tree with it (job object)
# --------------------------------------------------------------------------- #


async def _read_json_line(stream: asyncio.StreamReader) -> dict:
    raw = await asyncio.wait_for(stream.readline(), timeout=_READLINE_TIMEOUT_S)
    assert raw, "worker stdout closed unexpectedly"
    return json.loads(raw.decode("utf-8"))


async def _send(proc: asyncio.subprocess.Process, obj: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _spawn_worker(data_dir: Path, profile_dir: Path) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "browser_agent.worker",
        "--data-dir",
        str(data_dir),
        "--profile-dir",
        str(profile_dir),
        "--channel",
        "chromium",
        "--headless",
        "--allow-private-destinations",
        "--idle-timeout-s",
        "600",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PACKAGE_ROOT),
    )


_OPEN = {
    "type": "exec",
    "request_id": "open",
    "capability": "browser.session_open",
    "payload": {
        "session_id": "job-x",
        "profile": "research",
        "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": False},
    },
    "timeout_ms": 60000,
}


async def test_second_worker_process_is_refused_and_hard_kill_removes_chrome_tree(
    tmp_path: Path,
) -> None:
    profile_dir = tmp_path / "shared-profile"
    first = await _spawn_worker(tmp_path / "w1", profile_dir)
    second = None
    try:
        assert first.stdout is not None
        hello = await _read_json_line(first.stdout)
        assert hello["type"] == "hello" and hello["lifecycle_fault"] is None
        await _send(first, _OPEN)
        opened = await _read_json_line(first.stdout)
        assert opened["ok"] is True, opened
        root_pid = opened["result"]["lifecycle"]["browser_pid"]
        # sys.executable in a uv venv is a trampoline: the worker's real pid is the
        # one it reports, and THAT is the process a crash takes down.
        worker_pid = opened["result"]["lifecycle"]["worker_pid"]
        assert isinstance(worker_pid, int) and worker_pid > 0
        assert opened["result"]["lifecycle"]["job_object_assigned"] is True
        assert lifecycle.find_profile_chrome_pids(profile_dir) == [root_pid]

        # a second worker PROCESS on the same profile: refused by the OS lock,
        # nothing reaped, nothing launched
        second = await _spawn_worker(tmp_path / "w2", profile_dir)
        assert second.stdout is not None
        await _read_json_line(second.stdout)
        await _send(second, _OPEN)
        refused = await _read_json_line(second.stdout)
        assert refused["ok"] is False, refused
        assert refused["error"]["class"] == "browser_lifecycle_violation"
        assert refused["error"]["evidence"]["guard"] == "launch_lock"
        assert lifecycle.find_profile_chrome_pids(profile_dir) == [root_pid]

        # simulated worker crash: hard kill, no cleanup code runs in the worker
        subprocess.run(
            [lifecycle.TASKKILL_EXE, "/PID", str(worker_pid), "/F"],
            capture_output=True,
            check=False,
            timeout=15,
        )
        assert lifecycle.wait_for_pids_exit([worker_pid], timeout_s=15) == []
        await asyncio.wait_for(first.wait(), timeout=15)
        still = lifecycle.wait_for_pids_exit([root_pid], timeout_s=15)
        assert still == [], f"Chrome root {root_pid} survived its worker's death"
        assert lifecycle.find_profile_chrome_pids(profile_dir) == []

        # the lock died with the worker: the second worker can now recover, once
        await _send(second, _OPEN)
        recovered = await _read_json_line(second.stdout)
        assert recovered["ok"] is True, recovered
        new_pid = recovered["result"]["lifecycle"]["browser_pid"]
        assert new_pid != root_pid
        assert lifecycle.find_profile_chrome_pids(profile_dir) == [new_pid]
        await _send(
            second,
            {
                "type": "exec",
                "request_id": "close",
                "capability": "browser.session_close",
                "payload": {"session_id": "job-x"},
                "timeout_ms": 20000,
            },
        )
        closed = await _read_json_line(second.stdout)
        assert closed["ok"] is True and closed["result"]["browser_pid_exited"] is True
        await _send(second, {"type": "shutdown"})
        await asyncio.wait_for(second.wait(), timeout=30)
        assert lifecycle.find_profile_chrome_pids(profile_dir) == []
    finally:
        for proc in (first, second):
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
        for pid in lifecycle.find_profile_chrome_pids(profile_dir):
            lifecycle.terminate_pid(pid)
