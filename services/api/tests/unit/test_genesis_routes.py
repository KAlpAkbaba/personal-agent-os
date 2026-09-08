"""M24 Capability Genesis REST surface (docs/M24_CAPABILITY_GENESIS_SPEC.md §8):
GET /v1/genesis/runs, GET /v1/genesis/runs/{id}, POST .../approve, POST
.../cancel — through the REAL application object (the same
``tests.voice_corpus.harness.build_harness`` every other route surface test
runs on), owner-session gated.
"""

from __future__ import annotations

import uuid

from tests.fixtures.genesis import counterbox_app
from tests.voice_corpus.harness import build_harness


def _harness():
    return build_harness()


def test_list_is_empty_when_nothing_was_requested():
    h = _harness()
    resp = h.client.get("/v1/genesis/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_get_unknown_run_is_404():
    h = _harness()
    resp = h.client.get(f"/v1/genesis/runs/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_approve_unknown_run_is_404():
    h = _harness()
    resp = h.client.post(f"/v1/genesis/runs/{uuid.uuid4()}/approve")
    assert resp.status_code == 404


def test_cancel_unknown_run_is_404():
    h = _harness()
    resp = h.client.post(f"/v1/genesis/runs/{uuid.uuid4()}/cancel")
    assert resp.status_code == 404


def test_list_and_get_reflect_a_real_run(tmp_path):
    h = _harness()
    with counterbox_app.serve() as server:
        result = h.genesis.service.request(
            interface_name="counterbox", interface_url=server.spec_url, operation_id="read"
        )
        assert result["state"] == "verified"

        listed = h.client.get("/v1/genesis/runs").json()["runs"]
        assert any(r["id"] == result["id"] for r in listed)

        fetched = h.client.get(f"/v1/genesis/runs/{result['id']}").json()
        assert fetched["capability_id"] == "counterbox.read"
        assert fetched["state"] == "verified"


def test_approve_via_rest_is_the_owner_authenticated_act_itself():
    """The Cockpit's Approve button: the owner-authenticated REST call IS the
    confirmation — no session/turn binding is asked of it (spec §5/§9,
    app.actions.confirmation_gate's own module docstring)."""
    h = _harness()
    with counterbox_app.serve() as server:
        result = h.genesis.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="increment",
            arguments={"by": 1},
        )
        assert result["state"] == "awaiting_approval"

        resp = h.client.post(f"/v1/genesis/runs/{result['id']}/approve")
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "verified"


def test_cancel_via_rest_leaves_no_registration():
    h = _harness()
    with counterbox_app.serve() as server:
        result = h.genesis.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="reset",
        )
        assert result["state"] == "awaiting_approval"

        resp = h.client.post(f"/v1/genesis/runs/{result['id']}/cancel")
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "cancelled"
        assert h.genesis.service.registry.resolve("counterbox.reset") is None


def test_approve_a_non_awaiting_run_is_refused():
    h = _harness()
    with counterbox_app.serve() as server:
        result = h.genesis.service.request(
            interface_name="counterbox", interface_url=server.spec_url, operation_id="read"
        )
        assert result["state"] == "verified"
        resp = h.client.post(f"/v1/genesis/runs/{result['id']}/approve")
        assert resp.status_code == 422
