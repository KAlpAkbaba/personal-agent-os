"""OS-level launch guards for the one-owned-research-browser invariant.

Owner-machine incidents, 2026-09-03 (docs/DECISIONS.md ADR-0050 addendum
items 14/15): a research profile held by an orphaned Chrome plus *any* retry
source (companion restarting the worker, installer, a test harness) turned
into a window cascade. :mod:`browser_agent.lifecycle` supplies the in-process
mechanics (orphan reaping, locked-profile detection, tab budget). This module
adds the three cross-process guarantees that survive a worker that dies, is
killed, or is restarted in a loop:

- :class:`LaunchLock` -- an OS-level exclusive lock keyed by the profile
  directory (a named Win32 mutex, which the kernel releases the instant the
  holding process dies, so it can never go stale; a pid-stamped lock file on
  other platforms). A second *process* that wants to launch on the same
  profile while the holder is alive gets ``browser_lifecycle_violation``
  without touching anything: the holder's Chrome is not an orphan.
- :class:`LaunchBreaker` -- a launch-rate circuit breaker. Only *recovery*
  launches count (a launch that had to reap an orphan first, or that follows
  a failed launch); clean open/close cycles never trip it. Three recovery
  launches on one profile within ten minutes write a durable fault file the
  companion/owner can see and refuse every further research-profile launch
  until the fault ages out -- across worker restarts, which is exactly the
  restart-loop shape of the incident.
- :class:`KillOnCloseJob` -- a Windows Job Object with
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` that the launched Chrome root is
  assigned to. The worker holds the only handle; when the worker process
  ends for any reason (graceful, crash, ``taskkill /F``) the kernel closes
  the handle and terminates the whole Chrome tree. Deterministic cleanup
  that does not depend on any Python ``finally`` running.

Plus :class:`OwnershipRecord`, the durable per-profile ownership file
(``browser-ownership.json`` in the worker data directory) carrying research
job id, browser session id, worker pid, Chrome root pid and start time,
transport, profile path and tab ids, so a later process (companion reaper,
qualification script, a human) can tell exactly who owned the browser.

Every OS-facing piece takes its primitives as injectable arguments so the
logic is unit-testable with fakes (``tests/unit/test_launch_guard.py``); the
real Windows path is exercised by ``tests/browser/test_lifecycle_e2e.py``.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import BrowserError, ErrorClass
from .obs_logging import get_logger

logger = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"

# --- breaker policy ------------------------------------------------------- #
BREAKER_WINDOW_S = 600.0
BREAKER_MAX_RECOVERY_LAUNCHES = 3
FAULT_TTL_S = 600.0
LAUNCH_LOG_NAME = "browser-launches.json"
FAULT_FILE_NAME = "browser-lifecycle-fault.json"
OWNERSHIP_FILE_NAME = "browser-ownership.json"
_LAUNCH_LOG_KEEP = 50

# --- Win32 --------------------------------------------------------------- #
_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0x0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9


def _normalize_profile_key(profile_dir: Path | str) -> str:
    return str(profile_dir).strip().strip('"').rstrip("\\/").lower()


def lock_name_for_profile(profile_dir: Path | str) -> str:
    """Deterministic, session-local mutex name for a profile directory."""
    digest = hashlib.sha256(_normalize_profile_key(profile_dir).encode("utf-8")).hexdigest()
    return f"Local\\PagentOS.BrowserProfile.{digest[:24]}"


# --------------------------------------------------------------------------- #
# LaunchLock
# --------------------------------------------------------------------------- #


class LaunchLock:
    """Exclusive, cross-process launch/session lock for one profile directory.

    ``acquire()`` is non-blocking: it either takes the lock or raises
    ``browser_lifecycle_violation``. The lock is held until :meth:`release`
    (or, on Windows, until the holding process dies -- the kernel releases a
    mutex whose owner is gone, so there is no stale-lock failure mode).
    """

    def __init__(self, profile_dir: Path | str, *, lock_dir: Path | None = None) -> None:
        self.profile_dir = Path(profile_dir)
        self.name = lock_name_for_profile(profile_dir)
        self._handle: int | None = None
        self._lock_file: Path | None = None
        if not IS_WINDOWS:
            base = lock_dir if lock_dir is not None else self.profile_dir.parent
            digest = self.name.rsplit(".", 1)[-1]
            self._lock_file = base / f".pagentos-browser-{digest}.lock"

    @property
    def held(self) -> bool:
        return self._handle is not None or (
            self._lock_file is not None and self._lock_file.exists() and self._owned_file()
        )

    def _owned_file(self) -> bool:
        try:
            return int(self._lock_file.read_text(encoding="utf-8").strip() or 0) == os.getpid()  # type: ignore[union-attr]
        except (OSError, ValueError):
            return False

    def acquire(self) -> None:
        if self.held:
            return
        if IS_WINDOWS:
            self._acquire_windows()
        else:
            self._acquire_file()
        logger.info(
            "browser.launch_lock_acquired", lock=self.name, profile_dir=str(self.profile_dir)
        )

    def _refuse(self, holder: str) -> BrowserError:
        return BrowserError(
            ErrorClass.BROWSER_LIFECYCLE_VIOLATION,
            "launch refused: another PagentOS browser worker process already holds the "
            "launch lock for this profile; the existing browser is not an orphan and a "
            "second launch on the same profile would open a window inside it",
            retryable=False,
            evidence={
                "guard": "launch_lock",
                "lock": self.name,
                "profile_dir": str(self.profile_dir),
                "holder": holder,
            },
        )

    def _acquire_windows(self) -> None:  # pragma: no cover - exercised by the e2e suite
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise BrowserError(
                ErrorClass.INTERNAL_BUG,
                f"CreateMutexW failed: {ctypes.GetLastError()}",
                retryable=False,
            )
        rc = kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0)
        if rc in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            self._handle = handle
            return
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise self._refuse("another live process (named mutex is signalled-busy)")

    def _acquire_file(self) -> None:
        assert self._lock_file is not None
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder_pid = 0
            try:
                holder_pid = int(self._lock_file.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                holder_pid = 0
            if holder_pid and _pid_alive(holder_pid):
                raise self._refuse(f"pid {holder_pid}") from None
            # stale file from a dead process: take it over
            self._lock_file.unlink(missing_ok=True)
            fd = os.open(self._lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))

    def release(self) -> None:
        if IS_WINDOWS:
            if self._handle is not None:  # pragma: no cover - exercised by the e2e suite
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                kernel32.ReleaseMutex(ctypes.c_void_p(self._handle))
                kernel32.CloseHandle(ctypes.c_void_p(self._handle))
                self._handle = None
                logger.info("browser.launch_lock_released", lock=self.name)
        elif self._lock_file is not None and self._owned_file():
            self._lock_file.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if IS_WINDOWS:  # pragma: no cover
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------- #
# LaunchBreaker
# --------------------------------------------------------------------------- #


@dataclass
class BreakerDecision:
    allowed: bool
    recovery_launches_in_window: int
    fault: dict[str, Any] | None = None


class LaunchBreaker:
    """Durable launch-rate circuit breaker for one worker data directory.

    State lives in two small JSON files under ``data_dir`` so it survives the
    worker process (the runaway shape is "worker restarted N times, each
    restart launched once").
    """

    def __init__(
        self,
        data_dir: Path | str,
        *,
        window_s: float = BREAKER_WINDOW_S,
        max_recovery_launches: int = BREAKER_MAX_RECOVERY_LAUNCHES,
        fault_ttl_s: float = FAULT_TTL_S,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.window_s = window_s
        self.max_recovery_launches = max_recovery_launches
        self.fault_ttl_s = fault_ttl_s
        self._now = now
        self.launch_log = self.data_dir / LAUNCH_LOG_NAME
        self.fault_file = self.data_dir / FAULT_FILE_NAME

    # -- storage ---------------------------------------------------------- #

    def _read_launches(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.launch_log.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _write_launches(self, launches: list[dict[str, Any]]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.launch_log.with_suffix(".tmp")
        tmp.write_text(json.dumps(launches[-_LAUNCH_LOG_KEEP:], indent=1), encoding="utf-8")
        os.replace(tmp, self.launch_log)

    def current_fault(self) -> dict[str, Any] | None:
        """The durable fault if one exists and has not aged out, else None."""
        try:
            fault = json.loads(self.fault_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(fault, dict):
            return None
        raised_at = float(fault.get("raised_at_epoch") or 0.0)
        if self._now() - raised_at > self.fault_ttl_s:
            return None
        return fault

    def clear_fault(self) -> None:
        self.fault_file.unlink(missing_ok=True)

    # -- policy ----------------------------------------------------------- #

    def recovery_launches_in_window(self, profile_key: str) -> int:
        cutoff = self._now() - self.window_s
        return sum(
            1
            for item in self._read_launches()
            if item.get("kind") == "recovery"
            and item.get("profile") == profile_key
            and float(item.get("at_epoch") or 0.0) >= cutoff
        )

    def check(self, profile_dir: Path | str, *, about_to_recover: bool) -> BreakerDecision:
        """Decide whether a launch may proceed. Raises on refusal.

        ``about_to_recover`` is True when this launch had to reap an orphan
        or follows a failed launch -- the only launches that count.
        """
        profile_key = _normalize_profile_key(profile_dir)
        fault = self.current_fault()
        count = self.recovery_launches_in_window(profile_key)
        if fault is not None:
            raise self._refuse(profile_key, count, fault, reason="durable_fault_active")
        if about_to_recover and count + 1 > self.max_recovery_launches:
            fault = self._raise_fault(profile_key, count + 1)
            raise self._refuse(profile_key, count, fault, reason="launch_rate_exceeded")
        return BreakerDecision(allowed=True, recovery_launches_in_window=count)

    def record_launch(
        self, profile_dir: Path | str, *, kind: str, detail: dict[str, Any] | None = None
    ) -> None:
        if kind not in ("clean", "recovery", "failed"):
            raise ValueError(kind)
        launches = self._read_launches()
        launches.append(
            {
                "at_epoch": self._now(),
                "profile": _normalize_profile_key(profile_dir),
                "kind": kind,
                "worker_pid": os.getpid(),
                **(detail or {}),
            }
        )
        self._write_launches(launches)

    def _raise_fault(self, profile_key: str, count: int) -> dict[str, Any]:
        fault = {
            "fault": "browser_launch_rate_exceeded",
            "profile": profile_key,
            "recovery_launches_in_window": count,
            "window_s": self.window_s,
            "max_recovery_launches": self.max_recovery_launches,
            "raised_at_epoch": self._now(),
            "raised_by_worker_pid": os.getpid(),
            "message_tr": (
                "Tarayıcı yeniden başlatma sınırı aşıldı; araştırma tarayıcısı geçici olarak "
                "kapatıldı. Sahibin PagentOS tarayıcı süreçlerini kontrol etmesi gerekiyor."
            ),
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.fault_file.write_text(
            json.dumps(fault, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        logger.error(
            "browser.lifecycle_fault_raised",
            **{k: v for k, v in fault.items() if k != "message_tr"},
        )
        return fault

    def _refuse(
        self, profile_key: str, count: int, fault: dict[str, Any], *, reason: str
    ) -> BrowserError:
        return BrowserError(
            ErrorClass.BROWSER_LIFECYCLE_VIOLATION,
            "launch refused by the browser launch-rate circuit breaker "
            f"({reason}); a durable lifecycle fault is recorded at {self.fault_file}",
            retryable=False,
            evidence={
                "guard": "launch_breaker",
                "reason": reason,
                "profile_dir": profile_key,
                "recovery_launches_in_window": count,
                "fault_file": str(self.fault_file),
                "fault": fault,
            },
        )


# --------------------------------------------------------------------------- #
# KillOnCloseJob
# --------------------------------------------------------------------------- #


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class KillOnCloseJob:
    """A Windows Job Object whose only handle lives in this worker process.

    ``assign(pid)`` puts the Chrome root (and, by inheritance, every child it
    spawns afterwards) into the job. When this process ends -- however it
    ends -- the kernel closes the handle and terminates everything in the
    job. No-op on other platforms.
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        self.assigned_pids: list[int] = []
        self.supported = IS_WINDOWS

    def create(self) -> bool:
        if not IS_WINDOWS or self._handle is not None:  # pragma: no cover
            return self._handle is not None
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            logger.warning("browser.job_object_create_failed", error=ctypes.GetLastError())
            return False
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            ctypes.c_void_p(handle),
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            logger.warning("browser.job_object_limit_failed", error=ctypes.GetLastError())
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        self._handle = handle
        return True

    def assign(self, pid: int) -> bool:
        if not IS_WINDOWS:  # pragma: no cover
            return False
        if self._handle is None and not self.create():
            return False
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            logger.warning("browser.job_object_open_failed", pid=pid, error=ctypes.GetLastError())
            return False
        try:
            ok = kernel32.AssignProcessToJobObject(
                ctypes.c_void_p(self._handle), ctypes.c_void_p(process)
            )
            if not ok:
                logger.warning(
                    "browser.job_object_assign_failed", pid=pid, error=ctypes.GetLastError()
                )
                return False
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(process))
        self.assigned_pids.append(pid)
        logger.info("browser.job_object_assigned", pid=pid)
        return True

    def close(self) -> None:
        """Close the handle. With KILL_ON_JOB_CLOSE this terminates anything
        still in the job -- call it only after the browser has exited (or
        when you mean to kill what is left)."""
        if IS_WINDOWS and self._handle is not None:  # pragma: no cover
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None


# --------------------------------------------------------------------------- #
# OwnershipRecord
# --------------------------------------------------------------------------- #


@dataclass
class OwnershipRecord:
    """Durable ``browser-ownership.json`` under the worker data directory."""

    data_dir: Path
    path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.path = self.data_dir / OWNERSHIP_FILE_NAME

    def read(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def write(self, record: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(record, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    def update(self, **fields: Any) -> dict[str, Any]:
        record = self.read() or {}
        record.update(fields)
        self.write(record)
        return record


def process_start_time_iso(pid: int) -> str | None:
    """Creation time of ``pid`` (Windows only; None elsewhere or on failure)."""
    if not IS_WINDOWS:  # pragma: no cover
        return None
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = ctypes.c_ulonglong()
        exit_t = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        ok = kernel32.GetProcessTimes(
            ctypes.c_void_p(handle),
            ctypes.byref(creation),
            ctypes.byref(exit_t),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        # FILETIME: 100ns ticks since 1601-01-01 UTC
        epoch = (creation.value - 116444736000000000) / 10_000_000
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
