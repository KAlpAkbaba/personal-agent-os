"""The health endpoint must say WHICH required checks fail, not only that one does.

2026-09-18, production, during the B08 recovery drill: the recovery supervisor's loud
takeover wrote its failure marker, the backup check read it, every colour reported
`degraded` - and the blue/green release's health gate, which demanded exact `ok`, then
refused to promote the release that would have ended the incident. The marker is HOST
state: identical on both colours, unrepairable by a colour switch. The release had to be
unblocked by a human deleting a file.

The fix needs the failing checks by name, published where a shell can read them without
walking JSON, so a release can ask "is this candidate worse than what is serving?" instead
of "is the whole host perfect?". These tests hold that contract from the application side;
``scripts/tests/cloud-release-bluegreen.tests.ps1`` holds the release side.
"""

from __future__ import annotations

from app.health import ADVISORY_CHECKS, failing_checks, is_degraded


def test_names_the_failing_required_checks() -> None:
    checks = {
        "db": {"status": "ok"},
        "backup": {"status": "fail", "reasons": ["scheduled_unit_failed"]},
        "temporal": {"status": "ok"},
    }
    assert failing_checks(checks) == ["backup"]


def test_healthy_map_names_nothing() -> None:
    assert failing_checks({"db": {"status": "ok"}, "worker": {"status": "skipped"}}) == []


def test_advisory_checks_are_not_named() -> None:
    """Whatever does not make the process degraded must not make a release refuse either -
    the two rules were allowed to differ once already (Redis, 2026-09-11)."""
    advisory = next(iter(ADVISORY_CHECKS))
    checks = {advisory: {"status": "fail"}, "db": {"status": "ok"}}
    assert failing_checks(checks) == []
    assert not is_degraded(checks)


def test_required_false_is_not_named() -> None:
    checks = {"webpush": {"status": "fail", "required": False}, "db": {"status": "ok"}}
    assert failing_checks(checks) == []


def test_is_degraded_is_exactly_this_list_being_non_empty() -> None:
    """One rule, two readers: the owner's status line and the release gate cannot drift."""
    for checks in (
        {"db": {"status": "ok"}},
        {"db": {"status": "fail"}},
        {"backup": {"status": "fail"}, "redis": {"status": "fail"}},
        {"a": None, "b": "ok", "c": "fail"},
    ):
        assert is_degraded(checks) is bool(failing_checks(checks))


def test_bare_status_strings_are_understood() -> None:
    assert failing_checks({"a": "ok", "b": "fail", "c": None}) == ["b"]


# --------------------------------------------------------------- the published contract

def _health(monkeypatch, checks: dict[str, dict]) -> dict:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    async def fake_run_health_checks(settings: Settings) -> dict[str, dict]:
        return dict(checks)

    monkeypatch.setattr("app.main.run_health_checks", fake_run_health_checks)
    monkeypatch.setattr(
        "app.artifacts.runtime.ArtifactRuntime.health_check",
        lambda self: {"status": "ok", "latency_ms": 0.0},
    )
    with TestClient(create_app(Settings(_env_file=None))) as client:
        return client.get("/v1/system/health").json()


BASE = {
    "db": {"status": "ok", "latency_ms": 1.0},
    "redis": {"status": "ok", "latency_ms": 0.4},
    "object_store": {"status": "ok", "latency_ms": 1.0},
    "temporal": {"status": "ok", "latency_ms": 1.0},
    "schema": {"status": "ok", "current": "0040_x", "head": "0040_x"},
}


def test_the_endpoint_publishes_the_names_as_a_flat_top_level_string(monkeypatch) -> None:
    """Flat and top-level ON PURPOSE: release-cloud-core-bluegreen.sh reads it with one
    sed expression from a shell, and a nested field would put it back in the business of
    parsing JSON with regexes - which is how a nested provider's "status":"ok" once masked
    top-level degraded health."""
    body = _health(monkeypatch, dict(BASE))
    assert body["failing_checks"] == "", "a healthy process must name nothing"
    assert isinstance(body["failing_checks"], str)
    assert body["status"] == "ok"


def test_the_published_names_are_the_reason_for_the_status(monkeypatch) -> None:
    checks = dict(BASE)
    checks["db"] = {"status": "fail", "error": "TimeoutError: timeout"}
    body = _health(monkeypatch, checks)
    assert body["status"] == "degraded"
    # The same answer the status is derived from, never a second opinion.
    named = set(body["failing_checks"].split(","))
    assert named == {"db"}
    assert named <= set(body["checks"]), "names a check that is not in the map"
    for name in named:
        assert body["checks"][name].get("status") not in ("ok", "skipped")
