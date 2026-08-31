"""Supervised process management (spawn / stop / restart).

On Windows the whole child process TREE is killed via ``taskkill /T /F`` so a
supervised service can never leave orphaned grandchildren holding the port.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class ManagedProcess:
    def __init__(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        log_path: Path | str | None = None,
    ) -> None:
        if not args:
            raise ValueError("command args must not be empty")
        self.args = list(args)
        self.cwd = cwd
        self.env = env
        self.log_path = Path(log_path) if log_path else None
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_handle = None

    def start(self) -> int:
        if self.is_running():
            raise RuntimeError("process already running")
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_path.open("ab")
            stdout = stderr = self._log_handle
        else:
            stdout = stderr = subprocess.DEVNULL
        self._proc = subprocess.Popen(  # noqa: S603 - supervisor-owned command
            self.args,
            cwd=self.cwd,
            env=self.env,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
        )
        return self._proc.pid

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self, timeout: float = 10.0) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                if os.name == "nt":
                    # Kill the whole tree; /T covers grandchildren.
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=timeout)
        finally:
            self._proc = None
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None

    def restart(self) -> int:
        self.stop()
        return self.start()
