"""The candidate's shadow run (B35 req 608): the candidate's own API, started from its
worktree on a free local port with a throwaway database, probed on read-only paths, and
compared with what the live application answers on the same paths.

This is the constitution's ``sandbox -> canary/shadow`` step for a code candidate. It is
not a deployment: nothing listens beyond the loopback, nothing is promoted, and the
process is killed when the probes are done. The report is evidence in the run record;
a shadow that never came up is a failed shadow, and the candidate waits for the owner
with that fact beside it rather than with a green it did not earn.

``ProcessShadowRunner`` is the real one (a subprocess and HTTP over loopback);
``ScriptedShadowRunner`` is what a test hands the engine to prove the engine reads the
report, and what the engine gets when no interpreter is configured.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

SHADOW_PASSED = "passed"
SHADOW_FAILED = "failed"
SHADOW_SKIPPED = "skipped"

#: Read-only paths every Cloud Core answers without a device, a provider or a session.
DEFAULT_PROBES: tuple[str, ...] = ("/v1/system/health",)


@dataclass(frozen=True, slots=True)
class Probe:
    path: str
    shadow_status: int | None
    live_status: int | None
    detail: str = ""

    @property
    def agrees(self) -> bool:
        return self.shadow_status is not None and (
            self.live_status is None or self.live_status == self.shadow_status
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "shadow_status": self.shadow_status,
            "live_status": self.live_status,
            "agrees": self.agrees,
            "detail": self.detail[-300:],
        }


@dataclass(slots=True)
class ShadowReport:
    state: str
    detail: str = ""
    port: int | None = None
    started_in_s: float | None = None
    probes: list[Probe] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state == SHADOW_PASSED

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail[-2000:],
            "port": self.port,
            "started_in_s": self.started_in_s,
            "probes": [p.as_dict() for p in self.probes],
            "mismatches": [p.path for p in self.probes if not p.agrees],
        }


class ShadowRunner(Protocol):
    def run(self, worktree: Path) -> ShadowReport: ...


@dataclass(slots=True)
class ScriptedShadowRunner:
    report: ShadowReport = field(
        default_factory=lambda: ShadowReport(SHADOW_SKIPPED, "no shadow runner configured")
    )
    seen: list[Path] = field(default_factory=list)

    def run(self, worktree: Path) -> ShadowReport:
        self.seen.append(worktree)
        return self.report


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_status(url: str, timeout_s: float = 5.0) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - loopback
            return int(response.status), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), ""
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return None, f"{type(exc).__name__}: {exc}"[:200]


@dataclass(slots=True)
class ProcessShadowRunner:
    """Starts ``command`` (a template with ``{port}``) in ``worktree/package_root``, waits
    for ``ready_path`` to answer, probes, kills. ``live_base_url`` (the running Cloud
    Core) is optional: without it the shadow's own answers are the whole report."""

    command: tuple[str, ...]
    package_root: str = "services/api"
    ready_path: str = "/v1/system/health"
    probes: tuple[str, ...] = DEFAULT_PROBES
    live_base_url: str | None = None
    startup_timeout_s: float = 60.0
    env: dict[str, str] = field(default_factory=dict)
    status: Callable[[str, float], tuple[int | None, str]] = http_status

    def run(self, worktree: Path) -> ShadowReport:
        port = free_port()
        cwd = worktree / Path(self.package_root)
        argv = [part.replace("{port}", str(port)) for part in self.command]
        env = dict(os.environ)
        env.update(self.env)
        env["PAGENTOS_SHADOW"] = "1"
        env["PYTHONUTF8"] = "1"
        started = time.monotonic()
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                argv,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            return ShadowReport(SHADOW_FAILED, f"could not start: {exc}"[:500], port)
        base = f"http://127.0.0.1:{port}"
        try:
            ready_at: float | None = None
            while time.monotonic() - started < self.startup_timeout_s:
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout else ""
                    return ShadowReport(
                        SHADOW_FAILED,
                        f"exited with {process.returncode} before it answered\n{output[-1500:]}",
                        port,
                    )
                code, _ = self.status(base + self.ready_path, 2.0)
                if code is not None:
                    ready_at = time.monotonic() - started
                    break
                time.sleep(0.25)
            if ready_at is None:
                return ShadowReport(
                    SHADOW_FAILED,
                    f"did not answer {self.ready_path} within {self.startup_timeout_s:.0f}s",
                    port,
                )
            report = ShadowReport(SHADOW_PASSED, "", port, round(ready_at, 2))
            for path in self.probes:
                shadow_code, detail = self.status(base + path, 10.0)
                live_code: int | None = None
                if self.live_base_url:
                    live_code, _ = self.status(self.live_base_url.rstrip("/") + path, 10.0)
                report.probes.append(Probe(path, shadow_code, live_code, detail))
            mismatches = [p.path for p in report.probes if not p.agrees]
            if mismatches:
                report.state = SHADOW_FAILED
                report.detail = "disagreed with live on: " + ", ".join(mismatches)
            return report
        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            if process.stdout:
                process.stdout.close()
