"""Unit tests: the 3D Creation REST surface the Cockpit's "3B Sahne" panel calls
(docs/M25_CREATIVE_3D_SPEC.md §6), through the REAL application object with the fake
``project.*``/``scene.*`` device — the SAME wiring the voice tools and the corpus run
on (``tests.voice_corpus.harness.build_harness``), so a click on the panel and "Render
al." by voice are proven to reach one service and one device port.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.voice_corpus.harness import build_harness


def _harness():
    h = build_harness()
    h.client.app.state.device_action = h.device
    return h


def _create_blender_scene(h) -> str:
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Blender'da yeni sahne aç.")
    call = h.tool(sid, "c-1", "scene.create", {})
    assert call["status"] == "succeeded", call
    return call["result"]["scene_id"]


def test_list_is_honest_when_nothing_was_made() -> None:
    h = _harness()
    resp = h.client.get("/v1/scenes")
    assert resp.status_code == 200
    assert resp.json() == {"scenes": []}


def test_list_returns_the_created_scene_as_a_row() -> None:
    h = _harness()
    scene_id = _create_blender_scene(h)
    resp = h.client.get("/v1/scenes")
    assert resp.status_code == 200
    rows = resp.json()["scenes"]
    assert [r["id"] for r in rows] == [scene_id]
    assert rows[0]["tool"] == "blender"
    # The row the PANEL reads carries the step, not the database's word: sending `applied`
    # here left `rowState()` null for every real scene, so the panel showed no controls at
    # all and could never mark a row verified (measured 2026-09-08). "Blender'da yeni sahne
    # aç." asks for an EMPTY scene, so the honest step is `unverified` — the run did what
    # was asked and there was nothing checkable to read back.
    assert rows[0]["state"] == "unverified"


def test_get_one_scene_carries_its_inspection() -> None:
    h = _harness()
    scene_id = _create_blender_scene(h)
    resp = h.client.get(f"/v1/scenes/{scene_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == scene_id
    assert body["inspection"] is not None


def test_get_an_unknown_scene_is_404() -> None:
    h = _harness()
    resp = h.client.get(f"/v1/scenes/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_post_render_reaches_the_same_device_port_and_stores_a_png() -> None:
    h = _harness()
    scene_id = _create_blender_scene(h)
    sid = h.new_session()
    h.say(sid, "Bir küre ekle.")
    h.tool(sid, "c-2", "scene.add", {"kind": "sphere", "name": "Kure"})
    h.say(sid, "Bir kamera ekle.", turn=2)
    h.tool(
        sid, "c-3", "scene.add", {"kind": "camera", "name": "Kamera", "location": [0.0, -5.0, 0.0]}
    )
    h.say(sid, "Kamerayı nesneye çevir.", turn=3)
    h.tool(sid, "c-4", "scene.camera", {"name": "Kamera", "look_at": "Kure"})

    resp = h.client.post(f"/v1/scenes/{scene_id}/render")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The step the panel reads; the row itself is `rendered`.
    assert body["state"] == "verified"

    render_resp = h.client.get(f"/v1/scenes/{scene_id}/render")
    assert render_resp.status_code == 200
    assert render_resp.headers["content-type"] == "image/png"
    assert len(render_resp.content) > 0


def test_get_render_before_any_render_is_404() -> None:
    h = _harness()
    scene_id = _create_blender_scene(h)
    resp = h.client.get(f"/v1/scenes/{scene_id}/render")
    assert resp.status_code == 404


def test_post_inspect_reads_back_without_a_scaffold_or_run() -> None:
    h = _harness()
    scene_id = _create_blender_scene(h)
    h.device.reset()
    resp = h.client.post(f"/v1/scenes/{scene_id}/inspect")
    assert resp.status_code == 200, resp.text
    assert h.device.capabilities_called() == ["scene.inspect"]


def test_an_unknown_scene_action_is_404_and_the_device_is_never_asked() -> None:
    h = _harness()
    _create_blender_scene(h)
    h.device.reset()
    for action in ("render", "inspect"):
        resp = h.client.post(f"/v1/scenes/{uuid.uuid4()}/{action}")
        assert resp.status_code == 404, (action, resp.text)
        assert resp.json()["detail"]["code"] == "not_found"
    assert h.device.calls == []


def test_every_route_refuses_without_an_owner_session() -> None:
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        assert client.get("/v1/scenes").status_code == 401
        scene_id = uuid.uuid4()
        assert client.get(f"/v1/scenes/{scene_id}").status_code == 401
        assert client.get(f"/v1/scenes/{scene_id}/render").status_code == 401
        assert client.post(f"/v1/scenes/{scene_id}/render").status_code == 401
        assert client.post(f"/v1/scenes/{scene_id}/inspect").status_code == 401
