"""Unit tests: the Creative Tools Operator REST surface the Cockpit's "Yaratıcı" panel
calls (docs/M27_CREATIVE_TOOLS_SPEC.md §6), through the REAL application object with
the fixture Paint provider — the SAME wiring the voice tools and the corpus run on
(``tests.voice_corpus.harness.build_harness``), so a click on the panel and "Arka
planını kaldır." by voice are proven to reach one service and one object store.
"""

from __future__ import annotations

import uuid

from tests.voice_corpus.harness import build_harness

PAINT_LAB_PLAN = {
    "tool": "paint",
    "name": "corpus-fixture",
    "operations": [
        {"op": "new", "width": 320, "height": 240, "background": [255, 255, 255, 255]},
        {"op": "shape", "kind": "rect", "box": [10, 10, 100, 80], "fill": [255, 0, 0, 255]},
        {"op": "export", "format": "png"},
    ],
}


def _create_paint_run(h) -> str:
    with h.factory() as db:
        created = h.creative.create(db, plan=PAINT_LAB_PLAN, session_id="s-1")
        assert created["execution_status"] == "executed", created
        return created["run_id"]


def test_list_is_honest_when_nothing_was_made() -> None:
    h = build_harness()
    resp = h.client.get("/v1/creative")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_list_returns_the_created_run_as_a_row() -> None:
    h = build_harness()
    run_id = _create_paint_run(h)
    resp = h.client.get("/v1/creative")
    assert resp.status_code == 200
    rows = resp.json()["runs"]
    assert [r["id"] for r in rows] == [run_id]
    assert rows[0]["tool"] == "paint"
    assert rows[0]["state"] == "verified"
    assert rows[0]["has_output"] is True


def test_get_one_run_carries_its_inspection_and_comparison() -> None:
    h = build_harness()
    run_id = _create_paint_run(h)
    resp = h.client.get(f"/v1/creative/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == run_id
    assert body["inspection"] is not None
    assert body["compare"]["ok"] is True


def test_get_an_unknown_run_is_404() -> None:
    h = build_harness()
    resp = h.client.get(f"/v1/creative/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_output_returns_a_real_png() -> None:
    h = build_harness()
    run_id = _create_paint_run(h)
    resp = h.client.get(f"/v1/creative/{run_id}/output")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_get_output_for_an_unknown_run_is_404() -> None:
    h = build_harness()
    resp = h.client.get(f"/v1/creative/{uuid.uuid4()}/output")
    assert resp.status_code == 404


def test_routes_are_owner_gated() -> None:
    """No bearer token at all must be refused, the same discipline
    ``test_identity_enforcement.py`` checks exhaustively — this is the creative
    family's own narrow spot-check alongside its dedicated wiring test."""
    h = build_harness()
    run_id = _create_paint_run(h)
    unauthenticated = h.client.headers.copy()
    h.client.headers.pop("Authorization", None)
    try:
        resp = h.client.get(f"/v1/creative/{run_id}")
        assert resp.status_code in (401, 403)
    finally:
        h.client.headers.update(unauthenticated)
