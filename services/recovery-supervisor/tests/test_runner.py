"""Health loop: transient tolerance, rollback ordering, incident emission.

Everything injectable (checker/process/clock/sleep) so the loop is fully
deterministic — no sockets, no real time.
"""

import json
from pathlib import Path

import recovery_supervisor.runner as runner_module
from recovery_supervisor.runner import (
    EXIT_CODES,
    RESULT_HEALTHY,
    RESULT_ROLLED_BACK,
    RESULT_UNHEALTHY_AT_LKG,
    RunnerConfig,
    SupervisorRunner,
)
from recovery_supervisor.workspace import ReleaseWorkspace

OK = (True, {"status": "ok"})
FAIL_SELFTEST = (
    False,
    {
        "status": "fail",
        "error_class": "wrong_error_mapping",
        "check": "map_error",
        "expected": "dependency_unavailable",
        "actual": "internal_bug",
    },
)


class EventedWorkspace(ReleaseWorkspace):
    def __init__(self, root, events: list[str]) -> None:
        super().__init__(root)
        self.events = events

    def rollback(self):
        self.events.append("rollback")
        return super().rollback()

    def promote(self, version=None):
        self.events.append("promote")
        return super().promote(version)


class FakeProcess:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def start(self) -> int:
        self.events.append("process_start")
        return 1234

    def stop(self, timeout: float = 10.0) -> None:
        self.events.append("process_stop")

    def restart(self) -> int:
        self.events.append("process_restart")
        return 1235


class ScriptedChecker:
    """Yields scripted results per endpoint; repeats the last one forever."""

    def __init__(self, health: list, selftest: list) -> None:
        self.scripts = {"health": list(health), "selftest": list(selftest)}

    def __call__(self, url: str, timeout: float):
        key = "selftest" if url.rstrip("/").endswith("selftest") else "health"
        script = self.scripts[key]
        return script.pop(0) if len(script) > 1 else script[0]


class FakeClock:
    def __init__(self, step: float = 0.5) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def make_workspace(tmp_path: Path, events: list[str]) -> EventedWorkspace:
    ws = EventedWorkspace(tmp_path / "ws", events)
    ws.ensure()
    for version, marker in (("1.0.0", "good"), ("1.1.0", "broken")):
        src = tmp_path / f"src-{version}"
        src.mkdir()
        (src / "handler.py").write_text(f"MARKER = '{marker}'\n", encoding="utf-8")
        ws.stage_release(version, src)
    return ws


def make_runner(ws, config_overrides: dict, checker, process) -> SupervisorRunner:
    config = RunnerConfig(
        component="browser-agent-demo",
        health_url="http://127.0.0.1:1/health",
        selftest_url="http://127.0.0.1:1/selftest",
        interval_s=0.01,
        failure_threshold=2,
        window_s=30.0,
        startup_timeout_s=2.0,
        **config_overrides,
    )
    return SupervisorRunner(
        ws, config, process, checker=checker, sleep=lambda _s: None, clock=FakeClock()
    )


def test_healthy_run_promotes_and_stops_process(tmp_path: Path) -> None:
    events: list[str] = []
    ws = make_workspace(tmp_path, events)
    ws.activate("1.0.0")
    runner = make_runner(
        ws,
        {"max_cycles": 3, "promote_on_healthy": True},
        ScriptedChecker([OK], [OK]),
        FakeProcess(events),
    )
    verdict = runner.run()
    assert verdict.result == RESULT_HEALTHY
    assert EXIT_CODES[verdict.result] == 0
    assert verdict.promoted is True
    assert ws.last_known_good == "1.0.0"
    assert events == ["process_start", "promote", "process_stop"]
    assert list(ws.outbox_dir.glob("incident-*.json")) == []


def test_single_transient_failure_never_rolls_back(tmp_path: Path) -> None:
    events: list[str] = []
    ws = make_workspace(tmp_path, events)
    ws.activate("1.0.0")
    ws.promote()
    ws.activate("1.1.0")
    checker = ScriptedChecker([OK], [OK, FAIL_SELFTEST, OK, OK])
    runner = make_runner(ws, {"max_cycles": 4}, checker, FakeProcess(events))
    verdict = runner.run()
    assert verdict.result == RESULT_HEALTHY
    assert ws.current == "1.1.0"  # untouched: one transient failure is tolerated
    assert "rollback" not in events
    assert list(ws.outbox_dir.glob("incident-*.json")) == []


def test_sustained_failure_rolls_back_before_reporting(tmp_path: Path) -> None:
    events: list[str] = []
    ws = make_workspace(tmp_path, events)
    ws.activate("1.0.0")
    ws.promote()
    ws.activate("1.1.0")
    checker = ScriptedChecker([OK], [FAIL_SELFTEST])

    real_write = runner_module.write_outbox

    def logged_write(outbox_dir, report):
        events.append("outbox_write")
        return real_write(outbox_dir, report)

    runner = make_runner(ws, {"max_cycles": 10}, checker, FakeProcess(events))
    original = runner_module.write_outbox
    runner_module.write_outbox = logged_write
    try:
        verdict = runner.run()
    finally:
        runner_module.write_outbox = original

    assert verdict.result == RESULT_ROLLED_BACK
    assert EXIT_CODES[verdict.result] == 3
    assert verdict.rolled_back_to == "1.0.0"
    assert verdict.recovered is True
    assert ws.current == "1.0.0"
    assert ws.has_release("1.1.0")  # broken release preserved for diagnosis
    # RECOVERY FIRST: rollback + restart strictly before the incident report.
    assert events.index("rollback") < events.index("process_restart") < events.index(
        "outbox_write"
    )
    assert events[-1] == "process_stop"
    # Incident report carries mechanical fingerprint material + evidence.
    incident_path = Path(verdict.incident_path)
    report = json.loads(incident_path.read_text(encoding="utf-8"))
    assert report["fingerprint_material"] == {
        "component": "browser-agent-demo",
        "error_class": "wrong_error_mapping",
        "failing_check": "selftest",
    }
    assert report["active_version"] == "1.1.0"
    assert report["rolled_back_to"] == "1.0.0"
    assert report["evidence"]["selftest"]["actual"] == "internal_bug"
    assert len(report["active_manifest_digest"]) == 64


def test_unhealthy_at_last_known_good_reports_without_rollback(tmp_path: Path) -> None:
    events: list[str] = []
    ws = make_workspace(tmp_path, events)
    ws.activate("1.0.0")
    ws.promote()
    checker = ScriptedChecker([OK], [FAIL_SELFTEST])
    runner = make_runner(ws, {"max_cycles": 10}, checker, FakeProcess(events))
    verdict = runner.run()
    assert verdict.result == RESULT_UNHEALTHY_AT_LKG
    assert EXIT_CODES[verdict.result] == 4
    assert "rollback" not in events
    assert ws.current == "1.0.0"
    assert len(list(ws.outbox_dir.glob("incident-*.json"))) == 1


def test_startup_timeout_is_sustained_failure_and_rolls_back(tmp_path: Path) -> None:
    events: list[str] = []
    ws = make_workspace(tmp_path, events)
    ws.activate("1.0.0")
    ws.promote()
    ws.activate("1.1.0")
    never_up = ScriptedChecker([(False, {"error": "ConnectionRefusedError"})], [OK])
    runner = make_runner(ws, {"max_cycles": 10}, never_up, FakeProcess(events))
    verdict = runner.run()
    assert verdict.result == RESULT_ROLLED_BACK
    assert ws.current == "1.0.0"
    assert verdict.recovered is False  # health never came up in this scripted world
    report = json.loads(Path(verdict.incident_path).read_text(encoding="utf-8"))
    assert report["fingerprint_material"]["error_class"] == "health_check_failed"


def test_incident_posted_to_api_when_configured(tmp_path: Path) -> None:
    events: list[str] = []
    ws = make_workspace(tmp_path, events)
    ws.activate("1.0.0")
    ws.promote()
    ws.activate("1.1.0")
    posted: list[str] = []

    original = runner_module.post_report
    runner_module.post_report = lambda url, report, timeout=5.0: posted.append(url) or True
    try:
        runner = make_runner(
            ws,
            {"max_cycles": 10, "api_ingest_url": "http://127.0.0.1:1/ingest"},
            ScriptedChecker([OK], [FAIL_SELFTEST]),
            FakeProcess(events),
        )
        verdict = runner.run()
    finally:
        runner_module.post_report = original
    assert verdict.incident_posted is True
    assert posted == ["http://127.0.0.1:1/ingest"]
    # Outbox file stays even when the POST succeeded: it is the source of truth.
    assert len(list(ws.outbox_dir.glob("incident-*.json"))) == 1
