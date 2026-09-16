"""B44 - the 3D production path (req 520-527).

Measured before: no REST path created a scene and none had ever been made in production;
the scene could be moved, coloured, lit and aimed but not animated, not given a lens or a
light colour, and not exported. And two contract defects no suite could see:

* the shipped Blender driver declared its render with an ABSOLUTE path, which the device's
  ``scene.inspect`` refuses (``permission_denied``);
* the Cloud Core read the render bytes from a top-level ``render_png_base64`` the device
  never sends (the device answers ``render.png_base64``) - only the fake did.

Both halves were green because the fake device agreed with the Cloud Core and the device
lab drove its own lab driver. This file proves the path by executing it: the driver against
the fake ``bpy`` (layered actions, real format signatures); compare against every new
constraint; a fake device that answers - and refuses - in the device's own shape, with its
keys read from the device's C# source; the service end to end; the router, the voice tools
and the REST surface through the real application object. The real Blender runs of the same
driver are ``scripts/tests/blender-scene-lab.py`` and the device lab's
``SceneExportTests``.
"""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.creative3d.compare import compare
from app.creative3d.models import SceneRow
from app.creative3d.service import SceneService
from app.creative3d.spec import ScenePlan
from app.ledger.models import ActivityEventRow
from app.object_store import InMemoryObjectStore
from app.operator.models import ObjectFocusRow
from app.voice.intents import Intent, resolve_intent
from tests.alarms_support import FakeDeviceAction
from tests.creative3d_support import FakeCreative3DDevice, device_signature_ok, install_fake_bpy
from tests.voice_corpus.corpus import CTX_SCENE_BLENDER
from tests.voice_corpus.harness import build_harness

REPO_ROOT = Path(__file__).resolve().parents[4]
DEVICE_PROJECTS = (
    REPO_ROOT
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.SessionCompanion"
    / "Projects"
    / "ProjectCapabilities.cs"
)
TABLES = [SceneRow.__table__, ObjectFocusRow.__table__, ActivityEventRow.__table__]

FULL: list[dict[str, Any]] = [
    {"op": "create_scene"},
    {"op": "add_primitive", "kind": "sphere", "name": "Kure", "location": [0.0, 0.0, 0.0]},
    {"op": "add_primitive", "kind": "camera", "name": "Kamera", "location": [0.0, -6.0, 3.0]},
    {"op": "add_primitive", "kind": "light_sun", "name": "Gunes", "location": [2.0, -2.0, 5.0]},
    {
        "op": "set_material",
        "name": "Kure",
        "color": [0.9, 0.1, 0.1, 1.0],
        "metallic": 0.3,
        "roughness": 0.4,
    },
    {"op": "set_camera", "name": "Kamera", "look_at": "Kure", "lens": 35.0},
    {"op": "set_light", "name": "Gunes", "energy": 3.0, "color": [1.0, 0.5, 0.25]},
    {"op": "set_frames", "start": 1, "end": 24, "fps": 24},
    {
        "op": "animate",
        "name": "Kure",
        "channel": "location",
        "keyframes": [
            {"frame": 1, "value": [0.0, 0.0, 0.0]},
            {"frame": 24, "value": [0.0, 0.0, 2.0]},
        ],
    },
    {"op": "render", "width": 320, "height": 240, "engine": "workbench"},
    {"op": "export", "format": "glb"},
]


def _plan(operations: list[dict[str, Any]], *, tool: str = "blender") -> dict[str, Any]:
    return {"tool": tool, "project": "lab", "scene": "demo", "operations": operations}


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in TABLES:
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture()
def fake_device(tmp_path) -> FakeCreative3DDevice:
    return FakeCreative3DDevice(unity_available=False, render_dir=tmp_path)


@pytest.fixture()
def device(fake_device: FakeCreative3DDevice) -> FakeDeviceAction:
    return FakeDeviceAction(results=fake_device.capability_results())


@pytest.fixture()
def service() -> SceneService:
    return SceneService(object_store=InMemoryObjectStore())


# ============================================================================ spec


def test_the_production_controls_are_blender_only_and_bounded() -> None:
    ScenePlan.model_validate(_plan(FULL))
    for unity_only_refusal in (
        [{"op": "export", "format": "glb"}],
        [{"op": "set_frames", "start": 1, "end": 24, "fps": 24}],
        [{"op": "set_light", "name": "Gunes", "energy": 1.0, "color": [1.0, 1.0, 1.0]}],
        [{"op": "set_camera", "name": "Kamera", "lens": 35.0}],
    ):
        with pytest.raises(ValueError, match="only valid when tool='blender'"):
            ScenePlan.model_validate(_plan(unity_only_refusal, tool="unity"))
    with pytest.raises(ValueError):
        ScenePlan.model_validate(
            _plan(
                [
                    {
                        "op": "animate",
                        "name": "Kure",
                        "channel": "location",
                        "keyframes": [
                            {"frame": 10, "value": [0, 0, 0]},
                            {"frame": 5, "value": [0, 0, 1]},
                        ],
                    }
                ]
            )
        )
    with pytest.raises(ValueError):
        ScenePlan.model_validate(_plan([{"op": "set_frames", "start": 10, "end": 10}]))
    with pytest.raises(ValueError):
        ScenePlan.model_validate(_plan([{"op": "set_camera", "name": "Kamera", "lens": 0.5}]))
    with pytest.raises(ValueError):
        ScenePlan.model_validate(_plan([{"op": "export", "format": "obj"}]))


# ========================================================================== driver


def test_the_driver_declares_a_relative_render_and_reads_back_every_new_control(tmp_path) -> None:
    driver = install_fake_bpy()
    plan = ScenePlan.model_validate(_plan(FULL)).as_dict()
    inspection = driver.apply_operations(plan, str(tmp_path))
    assert inspection["errors"] == []
    # The device refuses an absolute path; the driver now declares the render's own name.
    assert inspection["render"]["path"] == "render.png"
    assert (tmp_path / "render.png").exists()
    sphere = next(o for o in inspection["objects"] if o["name"] == "Kure")
    assert sphere["material_metallic"] == 0.3 and sphere["material_roughness"] == 0.4
    # Left at the first frame: the scene opens where its animation starts.
    assert sphere["location"] == [0.0, 0.0, 0.0]
    camera = next(o for o in inspection["objects"] if o["name"] == "Kamera")
    assert camera["lens"] == 35.0
    sun = next(light for light in inspection["lights"] if light["name"] == "Gunes")
    assert sun["color"] == [1.0, 0.5, 0.25]
    assert inspection["frames"] == {"start": 1, "end": 24, "fps": 24}
    track = next(t for t in inspection["animation"] if t["object"] == "Kure")
    assert track["channel"] == "location"
    assert track["frames"] == [1, 24]
    assert track["values"] == [[0.0, 0.0, 0.0], [0.0, 0.0, 2.0]]
    export = inspection["exports"][0]
    assert export["format"] == "glb" and export["path"] == "scene.glb"
    data = (tmp_path / "scene.glb").read_bytes()
    assert device_signature_ok("glb", data)
    assert export["sha256"] == hashlib.sha256(data).hexdigest()
    assert export["bytes"] == len(data)


def test_a_light_is_known_by_its_type_and_an_fbx_export_carries_its_magic(tmp_path) -> None:
    driver = install_fake_bpy()
    inspection = driver.apply_operations(
        _plan(
            [
                {"op": "create_scene"},
                {"op": "add_primitive", "kind": "camera", "name": "Kamera"},
                {"op": "set_light", "name": "Kamera", "energy": 5.0},
                {"op": "export", "format": "fbx"},
            ]
        ),
        str(tmp_path),
    )
    assert "set_light: light 'Kamera' not found" in inspection["errors"]
    export = inspection["exports"][0]
    assert export["path"] == "scene.fbx"
    assert device_signature_ok("fbx", (tmp_path / "scene.fbx").read_bytes())


# ========================================================================= compare


def test_compare_checks_every_new_control_against_the_read_back_and_the_devices_proof(
    tmp_path,
) -> None:
    driver = install_fake_bpy()
    plan = ScenePlan.model_validate(_plan(FULL))
    inspection = driver.apply_operations(plan.as_dict(), str(tmp_path))
    png = (tmp_path / "render.png").read_bytes()
    declared = inspection["exports"][0]
    proof = [
        {
            "format": "glb",
            "path": "scene.glb",
            "bytes": declared["bytes"],
            "sha256": declared["sha256"],
            "verified": True,
        }
    ]
    matched = compare(plan, inspection, render_bytes=png, device_exports=proof)
    assert matched.ok, matched.as_dict()

    def fields_of(changed: dict[str, Any], exports: list[dict[str, Any]] | None = proof) -> set:
        result = compare(plan, changed, render_bytes=png, device_exports=exports)
        assert not result.ok
        return {m.field for m in result.mismatches}

    drifted = copy.deepcopy(inspection)
    next(t for t in drifted["animation"] if t["object"] == "Kure")["frames"] = [1, 12]
    assert "animation.location.frames" in fields_of(drifted)

    drifted = copy.deepcopy(inspection)
    next(t for t in drifted["animation"] if t["object"] == "Kure")["values"][1] = [0.0, 0.0, 9.0]
    assert "animation.location@24" in {
        f.split(".")[0] + "." + f.split(".")[1] for f in fields_of(drifted)
    }

    drifted = copy.deepcopy(inspection)
    drifted["frames"]["end"] = 48
    assert "frames" in fields_of(drifted)

    drifted = copy.deepcopy(inspection)
    next(o for o in drifted["objects"] if o["name"] == "Kamera")["lens"] = 50.0
    assert "lens" in fields_of(drifted)

    drifted = copy.deepcopy(inspection)
    next(light for light in drifted["lights"] if light["name"] == "Gunes")["color"] = [1, 1, 1]
    assert "color" in fields_of(drifted)

    drifted = copy.deepcopy(inspection)
    next(o for o in drifted["objects"] if o["name"] == "Kure")["material_metallic"] = 0.9
    assert "material_metallic" in fields_of(drifted)

    assert "export.glb" in fields_of(inspection, exports=[])
    lying = [{**proof[0], "sha256": "0" * 64}]
    assert "export.glb.sha256" in fields_of(inspection, exports=lying)


# ===================================================================== fake device


def test_the_fake_device_refuses_what_the_device_refuses(tmp_path) -> None:
    fake = FakeCreative3DDevice(render_dir=tmp_path)
    png = tmp_path / "render.png"
    png.write_bytes(b"\x89PNG not really")
    digest = hashlib.sha256(png.read_bytes()).hexdigest()

    fake._inspections["p1"] = {"render": {"path": str(png), "sha256": digest}}
    absolute = fake.inspect({"project_id": "p1"})
    assert not absolute.ok and absolute.error_class == "permission_denied"

    fake._inspections["p1"] = {"render": {"path": "render.png", "sha256": "0" * 64}}
    mismatch = fake.inspect({"project_id": "p1"})
    assert not mismatch.ok and mismatch.message == "render_sha256_mismatch"

    glb = tmp_path / "scene.glb"
    glb.write_bytes(b"not a glb at all")
    fake._inspections["p1"] = {
        "exports": [
            {
                "format": "glb",
                "path": "scene.glb",
                "sha256": hashlib.sha256(glb.read_bytes()).hexdigest(),
            }
        ]
    }
    signature = fake.inspect({"project_id": "p1"})
    assert not signature.ok and signature.message == "export_signature_mismatch"

    assert fake.inspect({"project_id": "nobody"}).message == "inspection_missing"


def test_the_fake_scene_inspect_answers_the_devices_own_keys_read_from_its_source(
    db: Session, device: FakeDeviceAction, fake_device: FakeCreative3DDevice, service: SceneService
) -> None:
    source = DEVICE_PROJECTS.read_text("utf-8")
    start = source.index("private JsonObject Inspect(JsonObject payload)")
    end = source.index("// =====", start)
    device_keys = set(re.findall(r'\["([a-z_0-9]+)"\]\s*=', source[start:end]))
    # A guard on the guard: a reader that finds nothing would make the equality vacuous.
    assert {"render", "png_base64", "exports", "verified", "inspection"} <= device_keys

    created = service.create(db, device, plan=_plan(FULL), session_id="s")
    assert created["execution_status"] == "executed", created
    answer = fake_device.inspect({"project_id": created["scene_id"]}).result
    fake_keys = set(answer) | set(answer["render"]) | set(answer["exports"][0])
    assert fake_keys == device_keys


# ========================================================================= service


def test_a_device_shaped_render_now_reaches_the_cloud_and_the_export_proof_is_kept(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    created = service.create(db, device, plan=_plan(FULL), session_id="s")
    assert created["execution_status"] == "executed", created
    assert created["state"] == "verified", created["compare"]
    row = db.get(SceneRow, uuid.UUID(created["scene_id"]))
    assert row.render_object_key and row.render_sha256 and row.render_bytes
    assert len(row.exports_json) == 1
    export = row.exports_json[0]
    assert (
        export["format"] == "glb" and export["path"] == "scene.glb" and export["verified"] is True
    )
    assert "GLB olarak dışa aktarıldı" in created["speech"]

    # A later export of the same scene opens the scene file and writes FBX beside it.
    again = service.apply(
        db,
        device,
        target=created["scene_id"],
        operations=[{"op": "export", "format": "fbx"}],
        capability="scene.export",
        session_id="s",
    )
    assert again["execution_status"] == "executed", again
    assert again["state"] == "verified", again["compare"]
    assert [e["format"] for e in db.get(SceneRow, uuid.UUID(created["scene_id"])).exports_json] == [
        "glb",
        "fbx",
    ]


# ========================================================================== router


def test_the_router_gives_the_scene_its_export_and_animation_words() -> None:
    assert resolve_intent("Sahneyi dışa aktar.").intent is Intent.SCENE_EXPORT
    fbx = resolve_intent("Sahneyi FBX olarak dışa aktar.")
    assert fbx.intent is Intent.SCENE_EXPORT and fbx.scene_format == "fbx"
    assert resolve_intent("Bunu GLB olarak dışa aktar.").intent is Intent.SCENE_EXPORT
    assert resolve_intent("Küreye bir animasyon ekle.").intent is Intent.SCENE_ANIMATE
    assert resolve_intent("Küpü canlandır.").intent is Intent.SCENE_ANIMATE
    # The neighbours keep their words.
    assert resolve_intent("Bir küp ekle.").intent is Intent.SCENE_ADD
    assert resolve_intent("Bunu PNG olarak dışa aktar.").intent is Intent.CREATIVE_EXPORT
    assert resolve_intent("Render al.").intent is Intent.SCENE_RENDER


# =========================================================================== voice


def test_the_voice_animates_and_exports_the_scene_in_focus() -> None:
    h = build_harness()
    h.seed(CTX_SCENE_BLENDER)
    sid = h.new_session()
    said = h.say(sid, "Blender'da bir küre ekle.")
    assert said["resolved_intents"][-1]["intent"] == "scene_add"
    added = h.tool(sid, "c-1", "scene.add", {"kind": "sphere", "name": "Kure"})
    assert added["result"]["execution_status"] == "executed", added

    said = h.say(sid, "Küreye bir animasyon ekle.", turn=2)
    assert said["resolved_intents"][-1]["intent"] == "scene_animate"
    animated = h.tool(
        sid, "c-2", "scene.animate", {"name": "Kure", "to": [0.0, 0.0, 2.0], "seconds": 1}
    )
    body = animated["result"]
    assert body["execution_status"] == "executed", body
    assert body["state"] == "verified", body["compare"]
    track = next(t for t in body["inspection"]["animation"] if t["object"] == "Kure")
    assert track["frames"] == [1, 25]

    said = h.say(sid, "Sahneyi FBX olarak dışa aktar.", turn=3)
    assert said["resolved_intents"][-1]["intent"] == "scene_export"
    exported = h.tool(sid, "c-3", "scene.export", {"format": "glb"})
    body = exported["result"]
    assert body["execution_status"] == "executed", body
    # The owner's word (FBX) wins over the model's argument (glb).
    assert "FBX olarak dışa aktarıldı" in body["speech"]
    with h.factory() as db:
        row = db.get(SceneRow, uuid.UUID(h.ids["scene:current"]))
        assert [e["format"] for e in row.exports_json] == ["fbx"]


def test_an_animation_of_something_the_scene_does_not_hold_asks_before_any_device_call() -> None:
    h = build_harness()
    h.seed(CTX_SCENE_BLENDER)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Küpü canlandır.")
    asked = h.tool(sid, "c-1", "scene.animate", {"name": "Kup", "to": [0.0, 0.0, 1.0]})
    assert asked["result"]["status"] == "needs_clarification"
    assert "Kup" in asked["result"]["speech"]
    assert h.device.capabilities_called() == []


def test_the_two_tools_are_registered_and_tiered() -> None:
    from app.security.step_up import TIER_SENSITIVE, tier_of
    from app.voice.realtime_sessions.tools import default_registry

    names = set(default_registry().names())
    for tool in ("scene.animate", "scene.export"):
        assert tool in names
        assert tier_of(tool) == TIER_SENSITIVE


# ============================================================================ REST


def test_the_production_rest_path_creates_and_modifies_a_scene() -> None:
    h = build_harness()
    h.client.app.state.device_action = h.device
    made = h.client.post(
        "/v1/scenes",
        json={
            "plan": _plan(
                [
                    {"op": "create_scene"},
                    {"op": "add_primitive", "kind": "cube", "name": "Kup"},
                ]
            )
        },
    )
    assert made.status_code == 201, made.text
    scene_id = made.json()["scene_id"]
    assert made.json()["state"] == "verified"

    applied = h.client.post(
        f"/v1/scenes/{scene_id}/apply", json={"operations": [{"op": "export", "format": "glb"}]}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == "verified"
    row = h.client.get(f"/v1/scenes/{scene_id}").json()
    assert row["exports"][0]["format"] == "glb" and row["exports"][0]["verified"] is True

    refused = h.client.post(
        f"/v1/scenes/{scene_id}/apply", json={"operations": [{"op": "explode"}]}
    )
    assert refused.status_code == 422
    unknown = h.client.post(
        f"/v1/scenes/{uuid.uuid4()}/apply", json={"operations": [{"op": "inspect"}]}
    )
    assert unknown.status_code == 404
    invalid = h.client.post("/v1/scenes", json={"plan": {"tool": "maya"}})
    assert invalid.status_code == 422
