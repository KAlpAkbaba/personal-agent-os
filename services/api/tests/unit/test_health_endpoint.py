"""Health endpoint shape tests with mocked dependency checks.

M1: the endpoint adds a "broker" check (sweeper alive + active sessions) on
top of the M0 dependency checks. Clients are used as context managers so the
app lifespan runs and the broker sweeper is alive.
"""

from fastapi.testclient import TestClient

import app.health as health_module
from app import __version__
from app.config import Settings
from app.main import create_app

# B01 req 2 adds a "schema" check: the alembic revision the database is on vs the head this
# build carries. It is produced by run_health_checks like the four dependency probes, but it
# reports its two revisions rather than a bare ok/fail, so it does not go through _run_check.
DEPENDENCY_CHECKS = {"db", "redis", "object_store", "temporal", "schema"}
# M3 adds an "artifacts" check (object store reachable) alongside "broker".
# M4 adds a "voice" check (real-provider activation status; always ok/offline).
# M5 adds a "memory" check (backend + embedder identity; no I/O, always ok).
# M6 adds a "selfhealing" check (coding backend + supervisor script presence).
# M7 adds an "evolution" check (skill generator identity + sandbox posture).
# M8 adds a "security" check (scope authority + collector posture; no I/O).
# M9 adds an "identity" check (auth posture only - deliberately does NOT say
# whether an owner credential is bootstrapped; health is the one open endpoint)
# and a "mobile" check (which real push transport a credential would activate,
# plus the share/export bound; no I/O, no secrets).
ALL_CHECKS = DEPENDENCY_CHECKS | {
    "broker",
    "artifacts",
    "voice",
    "memory",
    "selfhealing",
    "evolution",
    "security",
    "identity",
    "mobile",
    # M12 adds "voice_realtime" (capability-selected ConversationRealtime
    # provider + tool manifest; the simulator is always selectable offline).
    "voice_realtime",
    # M13 adds "temporal_worker" (embedded-worker run state; "skipped", not
    # "fail", when PAGENTOS_WORKER_MODE is not "embedded" — every test here)
    # and "research" (synthesis-provider configuration posture; no I/O).
    "temporal_worker",
    "research",
    # M18.3 adds "routine_clock" (spec §3.3): the one named component that asks
    # `evaluate_due`. It is on the health manifest BECAUSE it is that component — a
    # configured clock that is not running is the state in which no alarm would ever
    # fire, and that must be visible rather than silent.
    "routine_clock",
    # Phase 8 (2026-09-11): the retention sweeps nothing ran (app.maintenance); advisory.
    "retention",
}
# "skipped" (temporal_worker when worker_mode != embedded) is a legitimate
# non-degraded status alongside "ok" — see app.main's degraded computation.
NON_DEGRADED_STATUSES = ("ok", "skipped")


def make_client(monkeypatch, checks: dict[str, dict]) -> TestClient:
    async def fake_run_health_checks(settings: Settings) -> dict[str, dict]:
        return dict(checks)

    # create_app resolves run_health_checks via app.main's import; patch there.
    monkeypatch.setattr("app.main.run_health_checks", fake_run_health_checks)
    # The artifacts check hits the object store; keep this a true unit test.
    monkeypatch.setattr(
        "app.artifacts.runtime.ArtifactRuntime.health_check",
        lambda self: {"status": "ok", "latency_ms": 0.0},
    )
    return TestClient(create_app(Settings(_env_file=None)))


ALL_OK = {
    "db": {"status": "ok", "latency_ms": 1.2},
    "redis": {"status": "ok", "latency_ms": 0.4},
    "object_store": {"status": "ok", "latency_ms": 3.1},
    "temporal": {"status": "ok", "latency_ms": 5.0},
    # B01 req 2: the schema check carries the two revisions instead of a latency - the
    # release compares them, so an "ok" that did not name them would prove nothing.
    "schema": {"status": "ok", "current": "0040_x", "head": "0040_x"},
}


def test_health_ok_shape(monkeypatch) -> None:
    with make_client(monkeypatch, ALL_OK) as client:
        response = client.get("/v1/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert set(body["checks"].keys()) == ALL_CHECKS
    for name, check in body["checks"].items():
        assert check["status"] in NON_DEGRADED_STATUSES
        # ``temporal_worker`` is skipped in tests, and ``routine_clock`` is a STATE report
        # rather than a probe (M18.3 spec §3.3: running, interval, ticks, last error) —
        # neither has a round trip to time, and inventing a zero for one would be a
        # latency this endpoint never measured.
        if name not in ("temporal_worker", "routine_clock", "retention", "schema"):
            assert isinstance(check["latency_ms"], int | float)
    # The schema check must always name both revisions, ok or not: "the migration ran" and
    # "the schema is where this build expects it" are different claims, and only the second
    # one can be checked - by comparing these two.
    assert set(body["checks"]["schema"]) >= {"status", "current", "head"}
    retention = body["checks"]["retention"]
    assert retention["required"] is False
    # Health NAMES the sweeps, so a sweep that exists but is not registered cannot hide -
    # the 2026-09-11 defect was three sweeps with no caller at all. The last two are B06's
    # orphan sweeps (2026-09-12).
    assert retention["sweeps"] == [
        "memory",
        "identity_sessions",
        "security_assets",
        "interrupted_tool_calls",
        "interrupted_native_builds",
        "idle_voice_sessions",
        "abandoned_research_runs",
    ]
    clock = body["checks"]["routine_clock"]
    assert set(clock) == {
        "status",
        "running",
        "enabled",
        "interval_s",
        "ticks",
        "last_tick_at",
        "last_error",
    }


def test_health_broker_check_shape(monkeypatch) -> None:
    with make_client(monkeypatch, ALL_OK) as client:
        response = client.get("/v1/system/health")
    broker = response.json()["checks"]["broker"]
    assert broker["status"] == "ok"
    assert broker["sweeper_alive"] is True
    assert broker["active_sessions"] == 0


def test_health_broker_fails_without_lifespan(monkeypatch) -> None:
    """Without the lifespan (sweeper not started) the broker check reports fail."""
    client = make_client(monkeypatch, ALL_OK)
    response = client.get("/v1/system/health")
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["broker"]["status"] == "fail"
    assert body["checks"]["broker"]["sweeper_alive"] is False


def test_health_degraded_still_200(monkeypatch) -> None:
    # A REQUIRED dependency. This test used to fail Redis to show "degraded" - which is the
    # false dependency the next test removes.
    checks = dict(ALL_OK)
    checks["db"] = {"status": "fail", "latency_ms": 2000.0, "error": "TimeoutError: timeout"}
    with make_client(monkeypatch, checks) as client:
        response = client.get("/v1/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["db"]["status"] == "fail"
    assert "error" in body["checks"]["db"]


def test_redis_down_is_reported_but_degrades_nothing(monkeypatch) -> None:
    """Nothing in Cloud Core uses Redis (CLAUDE.md: ephemeral only; today not even a cache).
    Its failure is visible in its own check and marked advisory - and the process is `ok`,
    so a Redis restart no longer fails a release's exact `ok` gate."""
    import app.health as health

    checks = dict(ALL_OK)
    checks["redis"] = {"status": "fail", "latency_ms": 2000.0, "error": "ConnectionError: down"}
    for name in health.ADVISORY_CHECKS & checks.keys():
        checks[name]["required"] = False  # what run_health_checks marks
    with make_client(monkeypatch, checks) as client:
        body = client.get("/v1/system/health").json()
    assert body["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "fail"
    assert body["checks"]["redis"]["required"] is False


def test_run_health_checks_marks_only_the_advisory_ones(monkeypatch) -> None:
    import asyncio

    import app.health as health

    async def down(settings) -> None:  # noqa: ANN001
        raise ConnectionError("down")

    async def up(settings) -> None:  # noqa: ANN001
        return None

    monkeypatch.setattr(health, "check_redis", down)
    for name in ("check_db", "check_object_store", "check_temporal"):
        monkeypatch.setattr(health, name, up)

    # The subject here is WHICH checks are advisory, not the schema. This process has no
    # database, so the real schema check would fail for a reason that has nothing to do with
    # the question - and a failing REQUIRED check would make every assertion below about
    # `is_degraded` say the wrong thing for the wrong reason.
    async def schema_ok(settings) -> dict:  # noqa: ANN001
        return {"status": "ok", "current": "0040_x", "head": "0040_x"}

    monkeypatch.setattr(health, "check_schema", schema_ok)
    checks = asyncio.run(health.run_health_checks(Settings(_env_file=None)))
    assert checks["redis"]["status"] == "fail" and checks["redis"]["required"] is False
    assert all("required" not in checks[n] for n in ("db", "object_store", "temporal"))
    assert health.is_degraded(checks) is False
    checks["db"] = {"status": "fail"}
    assert health.is_degraded(checks) is True


def test_an_advisory_check_is_one_nothing_in_the_app_depends_on() -> None:
    """The day code starts using Redis, it stops being advisory. This reads app/ for a Redis
    client anywhere but the health probe itself, so that day cannot pass unnoticed."""
    from pathlib import Path

    import app.health as health

    assert health.ADVISORY_CHECKS == frozenset({"redis"})
    app_root = Path(health.__file__).resolve().parent
    users = [
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*.py")
        if path.name != "health.py"
        and any(
            marker in path.read_text(encoding="utf-8")
            for marker in ("import redis", "from redis", "aioredis", "settings.redis_url")
        )
    ]
    # worldmodel/state.py reports the configured URL as a fact about the process; it opens
    # no connection.
    assert users == ["worldmodel/state.py"], users


def test_health_never_crashes_when_all_down(monkeypatch) -> None:
    checks = {
        name: {"status": "fail", "latency_ms": 1.0, "error": "ConnectionError: down"}
        for name in ALL_OK
    }
    with make_client(monkeypatch, checks) as client:
        response = client.get("/v1/system/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_real_check_runner_reports_fail_not_raise(monkeypatch) -> None:
    """run_health_checks itself must swallow dependency errors."""

    async def boom(settings: Settings) -> None:
        raise ConnectionError("dependency down")

    monkeypatch.setattr(health_module, "check_db", boom)
    monkeypatch.setattr(health_module, "check_redis", boom)
    monkeypatch.setattr(health_module, "check_object_store", boom)
    monkeypatch.setattr(health_module, "check_temporal", boom)

    import asyncio

    results = asyncio.run(health_module.run_health_checks(Settings(_env_file=None)))
    assert set(results) == DEPENDENCY_CHECKS
    for name, result in results.items():
        assert result["status"] == "fail"
        if name == "schema":
            # Not one of the mocked probes: it reaches a database that is not there and says
            # so in its own words. The point of this test is that NOTHING here raises.
            assert "did not answer" in result["error"]
            continue
        assert "ConnectionError" in result["error"]


def test_health_serves_the_realtime_contract_version() -> None:
    # ADR-0045: the release qualification reads this without auth and refuses an old one.
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.voice.realtime_sessions.contract_version import CONTRACT_VERSION

    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        doc = client.get("/v1/system/health").json()
    assert doc["checks"]["voice_realtime"]["contract_version"] == CONTRACT_VERSION == 2
    # The action contract's own version rides the manifest too: an owner qualification
    # releases the Cloud Core when the deployed value is older than its checkout's.
    # 5 = M18.2 (ADR-0068): the research fast path's terminal result and failure receipts.
    # 6 = M18.3 (ADR-0071): the alarm/display/ambient capability family, the
    # device-refusal receipt shape, and one receipt per physical step of the wake sequence.
    # 7 = M18.2 follow-up (ADR-0075): research.start's refused terminal shape on a follow-up
    # turn, and the explanation naming the job and artifact it read.
    # 8 = M18.2 architectural fix (ADR-0076): the durable research focus, the three
    # follow-up tools that take no title and no job id from the model, and a refusal for
    # ANY turn that points at a run - including when there is no research to point at.
    # 9 = M18.2 final narrow defect (ADR-0077): the tool result contract - a clarification
    # is its own terminal status, a succeeded research answer always names its target and
    # speaks, and activity.explain on a research turn gives the report's answer.
    # 10 = M18.3 wiring defect (ADR-0078): the alarm/display tools reach the device - the
    # wake sequence and the status registry ride the runtime's live sources.
    # 11 = M18.3 ambient hardening (ADR-0079): ambient.explain, keep_on, quiet hours, the
    # camera grace, restored holdoffs, the alarm-wake holdoff wired.
    # 13 = ADR-0112: media.play/media.stop - the owner may ask for a video by name.
    assert doc["checks"]["voice_realtime"]["action_contract_version"] == 13
    # The follow-up family rides the same manifest the owner harness reads.
    assert {"research.explain", "research.sources", "research.finding_detail"} <= set(
        doc["checks"]["voice_realtime"]["tools"]
    )
    # M18.3 spec §3.8: the ten new tools ride the same manifest an owner harness reads.
    tools = set(doc["checks"]["voice_realtime"]["tools"])
    assert {"alarm.create", "alarm.stop", "display.off", "ambient.test_display"} <= tools


def test_health_temporal_worker_skipped_when_worker_mode_off(monkeypatch) -> None:
    with make_client(monkeypatch, ALL_OK) as client:
        response = client.get("/v1/system/health")
    body = response.json()
    assert body["status"] == "ok"  # "skipped" is not degraded
    assert body["checks"]["temporal_worker"] == {
        "status": "skipped",
        "mode": "off",
        "running": False,
    }


def test_health_research_check_reports_deterministic_by_default(monkeypatch) -> None:
    with make_client(monkeypatch, ALL_OK) as client:
        response = client.get("/v1/system/health")
    research = response.json()["checks"]["research"]
    assert research["status"] == "ok"
    assert research["effective_synthesis"] == "deterministic"
    assert research["providers_configured"]["deterministic"] is True
