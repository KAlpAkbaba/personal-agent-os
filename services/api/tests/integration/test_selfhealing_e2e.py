"""M6 acceptance E2E: the full self-healing story with real processes.

Choreographs, on temp workspaces + free loopback ports (real Postgres from the
compose stack for records):

1. supervisor activates good release 1.0.0 (production workspace), health
   policy green, promoted to last-known-good;
2. INJECT: activate broken 1.1.0 -> the synthetic /selftest check fails
   deterministically -> supervisor detects it via the health policy;
3. supervisor AUTO-ROLLS BACK to 1.0.0 and only then reports the incident
   (recovery first, coding second); the service is verifiably healthy again;
   the outbox report is ingested -> incident with fingerprint recorded;
4-5. the engineering pipeline reproduces the bug in isolation, generates a
   regression test (fails on 1.1.0, passes on the candidate 1.1.1), passes the
   independent review, deploys the candidate to a STAGING workspace/port,
   health green, promotes to production, marks the incident fixed;
6. BAD-CANDIDATE simulation: a deliberately broken candidate reaches staging,
   fails the health policy, the supervisor rolls STAGING back automatically,
   the candidate is marked rejected — production untouched.

Everything is offline/deterministic: loopback HTTP only, stdlib demo service,
controlled fault. Budget well under ~120s.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from app.config import Settings
from app.selfhealing.backends import DeterministicCodingBackend
from app.selfhealing.pipeline import SelfHealingPipeline, SupervisorDeployer
from app.selfhealing.service import compute_manifest_digest
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]
SUPERVISOR = REPO_ROOT / "services" / "recovery-supervisor" / "supervisor.py"
TARGET_SERVICE = REPO_ROOT / "staging" / "target-service" / "service.py"
RELEASES_SRC = REPO_ROOT / "staging" / "target-service" / "releases-src"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_supervisor(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SUPERVISOR), "--workspace", str(workspace), "--json", *args],
        capture_output=True,
        text=True,
        timeout=90,
    )


def supervisor_run_args(component: str, workspace: Path, port: int, *extra: str) -> list[str]:
    return [
        "run",
        "--component",
        component,
        "--health-url",
        f"http://127.0.0.1:{port}/health",
        "--selftest-url",
        f"http://127.0.0.1:{port}/selftest",
        f"--command-arg={sys.executable}",
        f"--command-arg={TARGET_SERVICE}",
        "--command-arg=--workspace",
        f"--command-arg={workspace}",
        "--command-arg=--port",
        f"--command-arg={port}",
        "--interval",
        "0.2",
        "--failure-threshold",
        "2",
        "--window",
        "10",
        "--startup-timeout",
        "30",
        "--max-cycles",
        "3",
        *extra,
    ]


def http_get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class ServiceProcess:
    """Directly-spawned target service (for recoverability spot checks)."""

    def __init__(self, workspace: Path, port: int) -> None:
        self.port = port
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(TARGET_SERVICE),
                "--workspace",
                str(workspace),
                "--port",
                str(port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                http_get(f"http://127.0.0.1:{port}/health")
                return
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise RuntimeError("target service did not come up")

    def kill(self) -> None:
        # Mirrors ManagedProcess.stop() in the supervisor itself: taskkill /T on Windows
        # to take the whole tree, signals elsewhere. Hardcoding taskkill made this test
        # unrunnable on Linux — which is where the recovery supervisor actually runs in
        # production, so the one platform whose behaviour matters most was the one never
        # exercised.
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)


def workspace_status(workspace: Path) -> dict:
    result = run_supervisor(workspace, "status")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_m6_selfhealing_full_story(tmp_path: Path, monkeypatch) -> None:
    token = uuid.uuid4().hex[:8]
    component = f"browser-agent-demo-{token}"
    root = tmp_path / "root"
    prod_ws = root / "prod"
    staging_ws = root / "staging"
    root.mkdir()
    prod_port = free_port()
    staging_port = free_port()
    check_port = free_port()

    # The API runtime reads the workspace root at construction time.
    monkeypatch.setenv("PAGENTOS_SELFHEALING_WORKSPACE_ROOT", str(root))
    settings = Settings()
    with owner_client(settings) as client:
        # ---- 1. Good release 1.0.0: activate, health green, promote. --------
        activated = run_supervisor(
            prod_ws,
            "activate",
            "--version",
            "1.0.0",
            "--source",
            str(RELEASES_SRC / "1.0.0"),
        )
        assert activated.returncode == 0, activated.stdout + activated.stderr
        healthy = run_supervisor(
            prod_ws,
            *supervisor_run_args(component, prod_ws, prod_port, "--promote-on-healthy"),
        )
        assert healthy.returncode == 0, healthy.stdout + healthy.stderr
        assert json.loads(healthy.stdout)["result"] == "healthy"
        status = workspace_status(prod_ws)
        assert status["current"] == "1.0.0"
        assert status["last_known_good"] == "1.0.0"

        # ---- 2+3. Inject broken 1.1.0 -> detect -> AUTO-ROLLBACK. ----------
        injected = run_supervisor(
            prod_ws,
            "activate",
            "--version",
            "1.1.0",
            "--source",
            str(RELEASES_SRC / "1.1.0"),
        )
        assert injected.returncode == 0
        watch = run_supervisor(
            prod_ws, *supervisor_run_args(component, prod_ws, prod_port, "--max-cycles", "10")
        )
        # Exit code 3 == unhealthy detected, rolled back to last-known-good.
        assert watch.returncode == 3, watch.stdout + watch.stderr
        verdict = json.loads(watch.stdout)
        assert verdict["result"] == "rolled_back"
        assert verdict["rolled_back_to"] == "1.0.0"
        assert verdict["recovered"] is True  # service verified healthy post-rollback
        assert verdict["detail"]["error_class"] == "wrong_error_mapping"
        status = workspace_status(prod_ws)
        assert status["current"] == "1.0.0"  # last-known-good restored
        assert "1.1.0" in status["releases"]  # broken release preserved for diagnosis

        # Recoverability spot check: the service really serves green again.
        probe = ServiceProcess(prod_ws, check_port)
        try:
            code, body = http_get(f"http://127.0.0.1:{check_port}/selftest")
            assert code == 200 and body["status"] == "ok"
            assert body["version"] == "1.0.0"
        finally:
            probe.kill()

        # ---- Outbox -> ingest: incident fingerprint recorded (with dedup). --
        outbox_files = sorted((prod_ws / "incidents-outbox").glob("incident-*.json"))
        assert len(outbox_files) == 1  # single sustained failure -> single report
        report = json.loads(outbox_files[0].read_text(encoding="utf-8"))
        assert report["fingerprint_material"]["error_class"] == "wrong_error_mapping"
        # The supervisor's digest formula matches the API's (pinned here).
        assert report["active_manifest_digest"] == compute_manifest_digest(
            prod_ws / "releases" / "1.1.0"
        )
        ingested = client.post("/v1/selfhealing/incidents/ingest", json=report)
        assert ingested.status_code == 201, ingested.text
        incident = ingested.json()
        assert len(incident["fingerprint"]) == 64
        assert incident["status"] == "recovered"
        # Dedup: draining the same report again increments, never duplicates.
        again = client.post("/v1/selfhealing/incidents/ingest", json=report)
        assert again.status_code == 200
        assert again.json()["incident_id"] == incident["incident_id"]
        assert again.json()["occurrence_count"] == 2

        # ---- 4+5. Engineering pipeline: reproduce -> regression -> patch ----
        # -> review -> staging deploy -> health green -> production promote.
        run_response = client.post(
            "/v1/selfhealing/pipeline/run",
            json={
                "incident_id": incident["incident_id"],
                "component": component,
                "staging_workspace": str(staging_ws),
                "production_workspace": str(prod_ws),
                "staging_port": staging_port,
                "production_port": prod_port,
            },
        )
        assert run_response.status_code == 200, run_response.text
        pipeline_result = run_response.json()
        assert pipeline_result["status"] == "fixed", pipeline_result
        steps = {s["name"]: s["status"] for s in pipeline_result["steps"]}
        assert steps["reproduce"] == "ok"  # regression FAILED on broken 1.1.0
        assert steps["regression_on_candidate"] == "ok"  # and PASSED on candidate
        assert steps["review_change"] == "ok"  # independent reviewer approved
        assert steps["staging_deploy"] == "ok"  # canary green on staging port
        assert steps["production_promote"] == "ok"
        assert pipeline_result["candidate_version"] == "1.1.1"

        # Production workspace now runs the fixed release, promoted to LKG.
        status = workspace_status(prod_ws)
        assert status["current"] == "1.1.1"
        assert status["last_known_good"] == "1.1.1"
        probe = ServiceProcess(prod_ws, check_port)
        try:
            code, body = http_get(f"http://127.0.0.1:{check_port}/selftest")
            assert code == 200 and body["status"] == "ok"
            assert body["version"] == "1.1.1"
        finally:
            probe.kill()

        # Records: incident fixed + linked to the fixing release; releases
        # reflect the whole story.
        incidents = client.get(
            "/v1/selfhealing/incidents", params={"component": component}
        ).json()["incidents"]
        assert len(incidents) == 1
        assert incidents[0]["status"] == "fixed"
        assert incidents[0]["fixed_release_id"] is not None
        releases = {
            r["version"]: r["status"]
            for r in client.get(
                "/v1/selfhealing/releases", params={"component": component}
            ).json()["releases"]
        }
        assert releases["1.1.0"] == "rolled_back"
        assert releases["1.1.1"] == "active"

        # ---- 6. BAD-CANDIDATE simulation on staging. ------------------------
        # A fresh incident (distinct failing check -> distinct fingerprint)...
        canary_report = json.loads(json.dumps(report))
        canary_report["fingerprint_material"]["failing_check"] = "selftest_canary"
        canary_report["evidence"]["selftest_canary"] = canary_report["evidence"]["selftest"]
        canary = client.post("/v1/selfhealing/incidents/ingest", json=canary_report)
        assert canary.status_code == 201
        canary_id = uuid.UUID(canary.json()["incident_id"])

        # ...fixed by a backend whose candidate passes regression + review but
        # is broken at runtime (self_test raises) — exactly the class of bug
        # only the staging health policy can catch.
        class BadCandidateBackend(DeterministicCodingBackend):
            def implement_change(self, analysis, broken_release_dir, output_dir):
                patch = super().implement_change(analysis, broken_release_dir, output_dir)
                handler = patch.candidate_dir / "handler.py"
                source = handler.read_text(encoding="utf-8")
                source = source.replace(
                    'def self_test() -> bool:\n    """',
                    'def self_test() -> bool:\n    raise RuntimeError("bad candidate")\n    """',
                )
                assert 'raise RuntimeError("bad candidate")' in source
                handler.write_text(source, encoding="utf-8")
                return patch

        runtime = client.app.state.selfhealing
        deployer = SupervisorDeployer(
            supervisor_script=runtime.supervisor_script,
            target_service_script=runtime.target_service_script,
            staging_workspace=staging_ws,
            staging_port=staging_port,
            production_workspace=prod_ws,
            production_port=prod_port,
            component=component,
        )
        pipeline = SelfHealingPipeline(
            runtime.service,
            BadCandidateBackend(),
            deployer,
            work_root=tmp_path / "bad-work",
            allowed_roots=[root],
        )
        bad_result = pipeline.run(canary_id)
        assert bad_result.status == "failed"
        bad_steps = {s.name: s for s in bad_result.steps}
        assert bad_steps["review_change"].status == "ok"  # static gates passed
        assert bad_steps["staging_deploy"].status == "failed"
        # The supervisor itself rolled staging back to the last good candidate.
        staging_verdict = bad_steps["staging_deploy"].detail["verdict"]
        assert staging_verdict["result"] == "rolled_back"
        assert staging_verdict["rolled_back_to"] == "1.1.1"
        assert workspace_status(staging_ws)["current"] == "1.1.1"
        # The bad candidate is marked rejected in the release records.
        bad_version = bad_result.candidate_version
        assert bad_version is not None and bad_version != "1.1.1"
        assert (
            runtime.service.get_release(component, bad_version)["status"] == "rejected"
        )
        # Production untouched and still healthy on the fixed release.
        status = workspace_status(prod_ws)
        assert status["current"] == "1.1.1"
        assert status["last_known_good"] == "1.1.1"
        probe = ServiceProcess(prod_ws, check_port)
        try:
            code, body = http_get(f"http://127.0.0.1:{check_port}/selftest")
            assert code == 200 and body["status"] == "ok" and body["version"] == "1.1.1"
        finally:
            probe.kill()
        # The canary incident is retryable (recovered), never falsely fixed.
        assert runtime.service.get_incident(canary_id)["status"] == "recovered"
