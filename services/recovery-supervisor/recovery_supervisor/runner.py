"""Supervisor health loop: poll, decide, roll back, report.

Ordering invariant ("recovery first, coding second"): when the policy declares
the active release unhealthy the runner FIRST rolls back and restarts the
service, verifies restoration, and only THEN writes/posts the incident report.
No coding, no model reasoning — that is the Evolution Engine's job later.

Everything with side effects (HTTP check, process, sleep, clock) is injectable
so the loop is deterministic under test.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from recovery_supervisor.health import HealthPolicy
from recovery_supervisor.incidents import build_incident_report, post_report, write_outbox
from recovery_supervisor.procs import ManagedProcess
from recovery_supervisor.workspace import ReleaseWorkspace, manifest_digest

Checker = Callable[[str, float], tuple[bool, dict[str, Any]]]

RESULT_HEALTHY = "healthy"
RESULT_ROLLED_BACK = "rolled_back"
RESULT_UNHEALTHY_AT_LKG = "unhealthy_no_rollback"

EXIT_CODES = {
    RESULT_HEALTHY: 0,
    RESULT_ROLLED_BACK: 3,
    RESULT_UNHEALTHY_AT_LKG: 4,
}


def http_check(url: str, timeout: float = 5.0) -> tuple[bool, dict[str, Any]]:
    """GET ``url``; ok iff HTTP 200 and JSON body has status == "ok".

    A non-2xx response's JSON body is preserved as the failure detail — that is
    where the supervised service reports its typed ``error_class``.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - loopback
            raw = response.read(65536)
            body = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read(65536).decode("utf-8"))
        except (ValueError, OSError):
            body = {}
        if not isinstance(body, dict):
            body = {"body": body}
        body.setdefault("http_status", exc.code)
        return False, body
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return False, {"error": f"{type(exc).__name__}: {exc}"}
    return body.get("status") == "ok", body if isinstance(body, dict) else {"body": body}


@dataclass
class RunnerConfig:
    component: str
    health_url: str
    selftest_url: str
    interval_s: float = 1.0
    failure_threshold: int = 3
    window_s: float = 30.0
    startup_timeout_s: float = 20.0
    check_timeout_s: float = 5.0
    max_cycles: int | None = None
    promote_on_healthy: bool = False
    api_ingest_url: str | None = None


@dataclass
class RunnerVerdict:
    result: str
    cycles: int
    incident_path: str | None = None
    incident_posted: bool | None = None
    rolled_back_to: str | None = None
    recovered: bool | None = None
    promoted: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "cycles": self.cycles,
            "incident_path": self.incident_path,
            "incident_posted": self.incident_posted,
            "rolled_back_to": self.rolled_back_to,
            "recovered": self.recovered,
            "promoted": self.promoted,
            "detail": self.detail,
        }


class SupervisorRunner:
    def __init__(
        self,
        workspace: ReleaseWorkspace,
        config: RunnerConfig,
        process: ManagedProcess | None = None,
        *,
        checker: Checker = http_check,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.process = process
        self.checker = checker
        self.sleep = sleep
        self.clock = clock
        self.policy = HealthPolicy(config.failure_threshold, config.window_s)

    # ----------------------------------------------------------------- checks

    def _check_once(self) -> tuple[bool, str, dict[str, Any]]:
        """Returns (ok, failing_check, evidence)."""
        ok_health, health_detail = self.checker(self.config.health_url, self.config.check_timeout_s)
        if not ok_health:
            return False, "health", {"health": health_detail}
        ok_selftest, selftest_detail = self.checker(
            self.config.selftest_url, self.config.check_timeout_s
        )
        if not ok_selftest:
            return False, "selftest", {"selftest": selftest_detail}
        return True, "", {"health": health_detail, "selftest": selftest_detail}

    def _wait_until_up(self) -> bool:
        """Poll health until the first success or startup timeout."""
        deadline = self.clock() + self.config.startup_timeout_s
        while True:
            ok, _detail = self.checker(self.config.health_url, self.config.check_timeout_s)
            if ok:
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(min(0.2, self.config.interval_s))

    # ------------------------------------------------------------------- run

    def run(self) -> RunnerVerdict:
        try:
            if self.process is not None:
                self.process.start()
            if not self._wait_until_up():
                # Sustained startup failure (polled for the whole grace
                # window) — not a single transient check.
                waited = self.config.startup_timeout_s
                return self._handle_unhealthy(
                    failing_check="health",
                    evidence={"error": "startup_timeout", "waited_s": waited},
                )
            cycles = 0
            while True:
                ok, failing_check, evidence = self._check_once()
                state = self.policy.record(ok, self.clock())
                cycles += 1
                if state == HealthPolicy.UNHEALTHY:
                    verdict = self._handle_unhealthy(failing_check=failing_check, evidence=evidence)
                    verdict.cycles = cycles
                    return verdict
                if self.config.max_cycles is not None and cycles >= self.config.max_cycles:
                    if self.policy.consecutive_failures > 0:
                        # Ended on a transient failure: not healthy enough to
                        # promote, but not unhealthy either.
                        return RunnerVerdict(
                            result=RESULT_HEALTHY,
                            cycles=cycles,
                            promoted=False,
                            detail={"note": "ended_degraded", "last_evidence": evidence},
                        )
                    promoted = False
                    if self.config.promote_on_healthy:
                        self.workspace.promote()
                        promoted = True
                    return RunnerVerdict(result=RESULT_HEALTHY, cycles=cycles, promoted=promoted)
                self.sleep(self.config.interval_s)
        finally:
            if self.process is not None:
                self.process.stop()

    # -------------------------------------------------------------- rollback

    def _handle_unhealthy(self, *, failing_check: str, evidence: dict[str, Any]) -> RunnerVerdict:
        detected_at = datetime.now(UTC).isoformat()
        active = self.workspace.current
        lkg = self.workspace.last_known_good
        active_digest = None
        if active and self.workspace.has_release(active):
            active_digest = manifest_digest(self.workspace.release_dir(active))
        error_class = _derive_error_class(failing_check, evidence)

        rolled_back_to: str | None = None
        recovered: bool | None = None
        recovered_at: str | None = None
        if lkg is not None and active != lkg:
            # RECOVERY FIRST: repoint, restart, verify — then report.
            _prev, rolled_back_to = self.workspace.rollback()
            if self.process is not None:
                self.process.restart()
            recovered = self._wait_until_up()
            recovered_at = datetime.now(UTC).isoformat() if recovered else None
            result = RESULT_ROLLED_BACK
        else:
            result = RESULT_UNHEALTHY_AT_LKG

        report = build_incident_report(
            component=self.config.component,
            error_class=error_class,
            failing_check=failing_check,
            workspace=str(self.workspace.root),
            active_version=active,
            active_manifest_digest=active_digest,
            last_known_good=lkg,
            rolled_back_to=rolled_back_to,
            evidence=evidence,
            detected_at=detected_at,
            recovered_at=recovered_at,
        )
        incident_path = write_outbox(self.workspace.outbox_dir, report)
        posted: bool | None = None
        if self.config.api_ingest_url:
            posted = post_report(self.config.api_ingest_url, report)
        return RunnerVerdict(
            result=result,
            cycles=0,
            incident_path=str(incident_path),
            incident_posted=posted,
            rolled_back_to=rolled_back_to,
            recovered=recovered,
            detail={"error_class": error_class, "failing_check": failing_check},
        )


def _derive_error_class(failing_check: str, evidence: dict[str, Any]) -> str:
    """Mechanical extraction: prefer the supervised service's own typed
    error_class from the failing check body; fall back to a generic class."""
    body = evidence.get(failing_check)
    if isinstance(body, dict):
        error_class = body.get("error_class")
        if isinstance(error_class, str) and error_class:
            return error_class
    return f"{failing_check}_check_failed"
