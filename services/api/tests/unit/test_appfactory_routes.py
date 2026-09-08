"""Unit tests: the App Factory REST surface the Cockpit's "Uygulamalar" panel calls
(docs/M23_APP_FACTORY_SPEC.md §6, ADR-0086 addendum 3), through the REAL application
object with the fake ``project.*`` device — the SAME wiring the voice tools and the corpus
run on (``tests.voice_corpus.harness.build_harness``), so "Çalıştır" on the panel and
"Uygulamayı çalıştır." by voice are proven to reach one service and one device port.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.appfactory.models import (
    STATE_FAILED,
    STATE_RUNNING,
    STATE_SCAFFOLDED,
    STATE_STOPPED,
    STATE_TESTED,
)
from app.appfactory.routes import ACTIONS, APPS_ROUTES_VERSION
from app.config import Settings
from app.main import create_app
from app.routines.dispatch import DeviceRunResult
from tests.appfactory_support import project_test_failing
from tests.voice_corpus.harness import build_harness


def _harness():
    """The corpus harness, with its fake device ALSO on the REST seam
    (``app.state.device_action`` - the M22 ``/open`` route's seam) so the panel's calls
    and the voice tools are proven to hit the same device."""
    h = build_harness()
    h.client.app.state.device_action = h.device
    return h


def _scaffold(h) -> str:
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Bana bir görev takip uygulaması yap.")
    call = h.tool(sid, "c-1", "app.create", {})
    assert call["status"] == "succeeded", call
    assert call["result"]["execution_status"] == "executed"
    return call["result"]["project_id"]


# ------------------------------------------------------------------ GET /v1/apps


def test_the_surface_is_versioned_and_offers_exactly_three_actions() -> None:
    assert APPS_ROUTES_VERSION == 1
    assert ACTIONS == ("run", "stop", "test")


def test_list_is_honest_when_nothing_was_made() -> None:
    h = _harness()
    resp = h.client.get("/v1/apps")
    assert resp.status_code == 200
    assert resp.json() == {"projects": []}


def test_list_returns_the_scaffolded_project_as_a_row_without_a_port() -> None:
    h = _harness()
    project_id = _scaffold(h)
    resp = h.client.get("/v1/apps")
    assert resp.status_code == 200
    rows = resp.json()["projects"]
    assert [r["id"] for r in rows] == [project_id]
    row = rows[0]
    assert row["template"] == "task-tracker"
    assert row["kind"] == "web_static"
    assert row["state"] == STATE_SCAFFOLDED
    assert row["port"] is None
    assert row["tests"] is None
    assert row["root_path"]


# ------------------------------------------------------------ POST run / stop / test


def test_run_reaches_the_same_device_port_and_reads_back_the_port() -> None:
    h = _harness()
    project_id = _scaffold(h)
    h.device.reset()
    resp = h.client.post(f"/v1/apps/{project_id}/run")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project_id"] == project_id
    assert body["state"] == STATE_RUNNING
    assert isinstance(body["port"], int) and 1 <= body["port"] <= 65535
    assert h.device.capabilities_called() == ["project.run"]
    rows = h.client.get("/v1/apps").json()["projects"]
    assert rows[0]["state"] == STATE_RUNNING
    assert rows[0]["port"] == body["port"]


def test_test_reads_back_the_counts_and_the_listing_carries_them() -> None:
    h = _harness()
    project_id = _scaffold(h)
    assert h.client.post(f"/v1/apps/{project_id}/run").status_code == 200
    h.device.reset()
    resp = h.client.post(f"/v1/apps/{project_id}/test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == STATE_TESTED
    assert body["tests"] == {"passed": 5, "failed": 0}
    assert h.device.capabilities_called() == ["project.test"]
    rows = h.client.get("/v1/apps").json()["projects"]
    assert rows[0]["tests"] == body["tests"]


def test_stop_reads_back_stopped_and_clears_the_port() -> None:
    h = _harness()
    project_id = _scaffold(h)
    assert h.client.post(f"/v1/apps/{project_id}/run").status_code == 200
    h.device.reset()
    resp = h.client.post(f"/v1/apps/{project_id}/stop")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == STATE_STOPPED
    assert h.device.capabilities_called() == ["project.stop"]
    rows = h.client.get("/v1/apps").json()["projects"]
    assert rows[0]["state"] == STATE_STOPPED
    assert rows[0]["port"] is None


def test_stopping_what_never_ran_is_an_honest_no_op_not_a_refusal() -> None:
    h = _harness()
    project_id = _scaffold(h)
    h.device.reset()
    resp = h.client.post(f"/v1/apps/{project_id}/stop")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["execution_status"] == "noop"
    assert body["state"] == STATE_SCAFFOLDED
    assert "zaten" in body["speech"]
    assert h.device.capabilities_called() == []


def test_a_device_failure_is_a_422_with_the_receipts_own_words() -> None:
    h = _harness()
    project_id = _scaffold(h)
    h.device.results["project.test"] = DeviceRunResult(
        False, error_class="timeout", message="the runner hung"
    )
    h.device.reset()
    resp = h.client.post(f"/v1/apps/{project_id}/test")
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"]
    assert detail["message"]
    assert h.device.capabilities_called() == ["project.test"]
    rows = h.client.get("/v1/apps").json()["projects"]
    assert rows[0]["state"] == STATE_SCAFFOLDED
    assert rows[0]["tests"] is None


def test_a_failing_test_run_is_read_back_as_failed_with_its_counts() -> None:
    h = _harness()
    project_id = _scaffold(h)
    h.device.results["project.test"] = project_test_failing
    resp = h.client.post(f"/v1/apps/{project_id}/test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == STATE_FAILED
    assert body["tests"] == {"passed": 3, "failed": 2}
    rows = h.client.get("/v1/apps").json()["projects"]
    assert rows[0]["state"] == STATE_FAILED
    assert rows[0]["tests"] == {"passed": 3, "failed": 2}


def test_an_unknown_project_is_404_and_the_device_is_never_asked() -> None:
    h = _harness()
    _scaffold(h)
    h.device.reset()
    for action in ACTIONS:
        resp = h.client.post(f"/v1/apps/{uuid.uuid4()}/{action}")
        assert resp.status_code == 404, (action, resp.text)
        assert resp.json()["detail"]["code"] == "not_found"
    assert h.device.capabilities_called() == []


def test_a_body_is_ignored_the_path_is_the_whole_request() -> None:
    h = _harness()
    project_id = _scaffold(h)
    resp = h.client.post(f"/v1/apps/{project_id}/run", json={"target": "previous", "port": 80})
    assert resp.status_code == 200, resp.text
    assert resp.json()["port"] != 80


# ---------------------------------------------------------------- the gate


def test_every_route_refuses_without_an_owner_session() -> None:
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        assert client.get("/v1/apps").status_code == 401
        for action in ACTIONS:
            assert client.post(f"/v1/apps/{uuid.uuid4()}/{action}").status_code == 401
