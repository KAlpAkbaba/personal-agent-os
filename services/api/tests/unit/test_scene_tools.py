"""3D creation's voice tools, through the REAL application object
(docs/M25_CREATIVE_3D_SPEC.md §5) — the same relay/router/tool path the corpus uses
(``tests/voice_corpus``), narrowed here to the specific contracts that category covers
in aggregate: an utterance -> the tool -> ``SceneService`` -> the fake device -> the
receipt with the inspection read back; the tool-word resolution (utterance, else the
current focus, else a clarification); the honest Unity licence refusal.

Reuses ``tests.voice_corpus.harness.build_harness`` (the same wiring
``test_appfactory_tools.py``/``test_documents_tools.py`` already reuse for their own
families) rather than re-deriving the identity/broker/session boilerplate.
"""

from __future__ import annotations

import uuid

from app.creative3d.models import STATE_DEPENDENCY_UNAVAILABLE, SceneRow
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_SCENE
from tests.creative3d_support import UNITY_LICENSE_MESSAGE
from tests.voice_corpus.harness import build_harness

# --------------------------------------------------------------------------- create


def test_scene_create_resolves_the_tool_word_and_focuses_the_scene() -> None:
    h = build_harness()
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Blender'da yeni sahne aç.")
    call = h.tool(sid, "c-1", "scene.create", {})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert h.device.capabilities_called() == ["project.scaffold", "project.run", "scene.inspect"]

    with h.factory() as db:
        row = db.get(SceneRow, uuid.UUID(body["scene_id"]))
        assert row.tool == "blender"
        current = focus_module.current(db, FOCUS_KIND_SCENE)
        assert current is not None
        assert current.object_id == body["scene_id"]


def test_scene_create_with_no_tool_word_and_no_focus_is_a_clarification() -> None:
    h = build_harness()
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Yeni bir sahne oluştur.")
    call = h.tool(sid, "c-1", "scene.create", {})
    assert call["status"] == "needs_clarification", call
    assert "Blender" in call["result"]["speech"] or "Unity" in call["result"]["speech"]
    assert h.device.calls == []


def test_scene_create_unity_licence_refusal_is_honest() -> None:
    h = build_harness()
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Unity'de boş bir sahne oluştur.")
    call = h.tool(sid, "c-1", "scene.create", {})
    assert call["status"] == "succeeded", call  # a truthful refusal receipt, not an error
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert UNITY_LICENSE_MESSAGE in body["speech"]

    with h.factory() as db:
        row = db.query(SceneRow).one()
        assert row.state == STATE_DEPENDENCY_UNAVAILABLE


# ------------------------------------------------------------------------------- add


def _create_blender_scene(h) -> str:
    sid = h.new_session()
    h.say(sid, "Blender'da yeni sahne aç.")
    call = h.tool(sid, "c-1", "scene.create", {})
    assert call["status"] == "succeeded", call
    return sid, call["result"]["scene_id"]


def test_scene_add_resolves_the_kind_word_and_reads_back_the_inspection() -> None:
    h = build_harness()
    sid, scene_id = _create_blender_scene(h)
    h.device.reset()
    h.say(sid, "Bir küre ekle.", turn=2)
    call = h.tool(sid, "c-2", "scene.add", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert "Küre" in body["speech"]
    assert "eklendi" in body["speech"]
    assert body["objects"] == 1


def test_scene_add_with_an_unrecognised_kind_is_a_clarification() -> None:
    h = build_harness()
    sid, scene_id = _create_blender_scene(h)
    h.device.reset()
    # The model, seeing no kind word in the utterance and passing none of its own,
    # gets an honest clarification — never a guess (spec §7's own negative case).
    call = h.tool(sid, "c-2", "scene.add", {})
    assert call["status"] == "needs_clarification", call
    assert h.device.calls == []


# --------------------------------------------------------------------------- render


def test_scene_render_produces_a_nontrivial_stored_png() -> None:
    h = build_harness()
    sid, scene_id = _create_blender_scene(h)
    h.say(sid, "Bir küre ekle.", turn=2)
    h.tool(sid, "c-2", "scene.add", {"kind": "sphere", "name": "Kure"})
    h.say(sid, "Bir kamera ekle.", turn=3)
    h.tool(sid, "c-3", "scene.add", {"kind": "camera", "name": "Kamera", "location": [0.0, -5.0, 0.0]})
    h.say(sid, "Kamerayı nesneye çevir.", turn=4)
    h.tool(sid, "c-4", "scene.camera", {"name": "Kamera", "look_at": "Kure"})
    h.say(sid, "Render al.", turn=5)
    call = h.tool(sid, "c-5", "scene.render", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"

    with h.factory() as db:
        row = db.get(SceneRow, uuid.UUID(scene_id))
        assert row.render_object_key is not None
        assert row.render_bytes and row.render_bytes > 0


# -------------------------------------------------------------------------- inspect


def test_scene_inspect_never_touches_the_device_twice() -> None:
    h = build_harness()
    sid, scene_id = _create_blender_scene(h)
    h.say(sid, "Bir küp ekle.", turn=2)
    h.tool(sid, "c-2", "scene.add", {})
    h.device.reset()
    h.say(sid, "Sahnede ne var?", turn=3)
    call = h.tool(sid, "c-3", "scene.inspect", {})
    assert call["status"] == "succeeded", call
    assert h.device.capabilities_called() == ["scene.inspect"]
    assert call["result"]["objects"] == 1
