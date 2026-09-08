"""``app.creative3d.drivers.blender_driver`` (docs/M25_CREATIVE_3D_SPEC.md §3): the
sha256 pin, the pure functions (argv parsing, the look-at trigonometry), and every
operation kind run against ``tests.creative3d_support``'s fake ``bpy`` — no real
Blender needed (the real Blender lab is ``scripts/tests/blender-scene-lab.py``).
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from tests.creative3d_support import install_fake_bpy

DRIVERS_DIR = Path(__file__).resolve().parents[2] / "app" / "creative3d" / "drivers"


# ============================================================================ manifest


def test_manifest_pins_match_the_files_on_disk() -> None:
    manifest = json.loads((DRIVERS_DIR / "manifest.json").read_text(encoding="utf-8"))
    for filename, entry in manifest["drivers"].items():
        data = (DRIVERS_DIR / filename).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], (
            f"{filename} does not match its pinned sha256 — the driver changed "
            "without the manifest being updated"
        )


# =================================================================== pure functions


@pytest.fixture()
def driver():
    return install_fake_bpy()


def test_parse_argv_takes_the_tail_after_the_blender_separator(driver) -> None:
    plan_path, out_path = driver.parse_argv(
        ["blender", "-b", "file.blend", "--python", "blender_driver.py", "--", "plan.json", "out.json"]
    )
    assert (plan_path, out_path) == ("plan.json", "out.json")


def test_parse_argv_without_a_separator_takes_the_whole_list(driver) -> None:
    assert driver.parse_argv(["plan.json", "out.json"]) == ("plan.json", "out.json")


def test_parse_argv_wrong_count_refused(driver) -> None:
    with pytest.raises(ValueError, match="expected exactly 2 arguments"):
        driver.parse_argv(["--", "only_one.json"])


@pytest.mark.parametrize(
    "cam_loc,target_loc,expected_rx_deg,expected_rz_deg",
    [
        # Camera 5 units on -Y, looking at the origin: points straight along +Y.
        ((0.0, -5.0, 0.0), (0.0, 0.0, 0.0), 90.0, 0.0),
        # Camera directly above the origin, looking straight down: the camera's
        # UNROTATED default already looks down -Z, so this needs no rotation at all.
        ((0.0, 0.0, 5.0), (0.0, 0.0, 0.0), 0.0, 0.0),
        # Camera 5 units on +X, looking at the origin: points along -X, a 90 degree yaw.
        ((5.0, 0.0, 0.0), (0.0, 0.0, 0.0), 90.0, 90.0),
    ],
)
def test_look_at_euler_matches_the_true_direction(
    driver, cam_loc, target_loc, expected_rx_deg, expected_rz_deg
) -> None:
    from app.creative3d.compare import forward_vector

    rx, ry, rz = driver.look_at_euler_rad(cam_loc, target_loc)
    assert ry == 0.0
    assert math.degrees(rx) == pytest.approx(expected_rx_deg, abs=1e-6)
    assert math.degrees(rz) == pytest.approx(expected_rz_deg, abs=1e-6)

    # Cross-check against the INDEPENDENT recovery in app.creative3d.compare (never
    # importing look_at_euler_rad itself) - the forward vector it recovers from this
    # rotation must actually point from the camera at the target.
    fwd = forward_vector((math.degrees(rx), math.degrees(ry), math.degrees(rz)))
    wanted = tuple(t - c for t, c in zip(target_loc, cam_loc, strict=True))
    dot = sum(f * w for f, w in zip(fwd, wanted, strict=True))
    mag_f = math.sqrt(sum(f * f for f in fwd))
    mag_w = math.sqrt(sum(w * w for w in wanted))
    cos_theta = dot / (mag_f * mag_w)
    assert cos_theta == pytest.approx(1.0, abs=1e-6)


def test_look_at_euler_degenerate_same_point_is_identity(driver) -> None:
    assert driver.look_at_euler_rad((1.0, 1.0, 1.0), (1.0, 1.0, 1.0)) == (0.0, 0.0, 0.0)


# ============================================================================ ops


def _basic_plan() -> dict:
    return {
        "tool": "blender",
        "project": "lab",
        "scene": "demo",
        "operations": [
            {"op": "create_scene"},
            {
                "op": "add_primitive",
                "kind": "sphere",
                "name": "Kure",
                "location": [0.0, 0.0, 0.0],
            },
            {
                "op": "add_primitive",
                "kind": "camera",
                "name": "Kamera",
                "location": [0.0, -5.0, 0.0],
            },
            {"op": "set_camera", "name": "Kamera", "look_at": "Kure"},
            {
                "op": "set_material",
                "name": "Kure",
                "color": [1.0, 0.0, 0.0, 1.0],
                "metallic": 0.2,
                "roughness": 0.5,
            },
            {
                "op": "add_primitive",
                "kind": "light_sun",
                "name": "Sun",
                "location": [0.0, 0.0, 5.0],
            },
            {"op": "set_light", "name": "Sun", "energy": 3.0},
        ],
    }


def test_apply_operations_builds_the_expected_inspection(driver, tmp_path) -> None:
    inspection = driver.apply_operations(_basic_plan(), str(tmp_path))
    assert inspection["errors"] == []
    names = {o["name"] for o in inspection["objects"]}
    assert names == {"Kure", "Kamera", "Sun"}
    assert inspection["camera"] == "Kamera"
    assert inspection["lights"] == [{"name": "Sun", "energy": 3.0}]

    kure = next(o for o in inspection["objects"] if o["name"] == "Kure")
    assert kure["type"] == "MESH"
    assert kure["location"] == [0.0, 0.0, 0.0]
    assert kure["material_color"] == [1.0, 0.0, 0.0, 1.0]

    kamera = next(o for o in inspection["objects"] if o["name"] == "Kamera")
    assert kamera["type"] == "CAMERA"
    # Pointed at Kure (0,0,0) from (0,-5,0): 90 degrees on X, 0 on Z.
    assert kamera["rotation"][0] == pytest.approx(90.0, abs=1e-3)
    assert kamera["rotation"][2] == pytest.approx(0.0, abs=1e-3)


def test_transform_moves_an_existing_object(driver, tmp_path) -> None:
    plan = _basic_plan()
    plan["operations"].append({"op": "transform", "name": "Kure", "location": [1.0, 2.0, 3.0], "scale": [2.0, 2.0, 2.0]})
    inspection = driver.apply_operations(plan, str(tmp_path))
    kure = next(o for o in inspection["objects"] if o["name"] == "Kure")
    assert kure["location"] == [1.0, 2.0, 3.0]
    assert kure["scale"] == [2.0, 2.0, 2.0]


def test_transform_of_unknown_object_is_an_error_not_a_crash(driver, tmp_path) -> None:
    plan = {
        "tool": "blender",
        "project": "lab",
        "scene": "demo",
        "operations": [{"op": "transform", "name": "Ghost", "location": [1.0, 1.0, 1.0]}],
    }
    inspection = driver.apply_operations(plan, str(tmp_path))
    assert any("Ghost" in e for e in inspection["errors"])


def test_unknown_operation_recorded_as_an_error(driver, tmp_path) -> None:
    plan = {"tool": "blender", "project": "lab", "scene": "demo", "operations": [{"op": "delete_everything"}]}
    inspection = driver.apply_operations(plan, str(tmp_path))
    assert any("unknown operation" in e for e in inspection["errors"])


def test_create_scene_clears_prior_objects(driver, tmp_path) -> None:
    plan1 = {
        "tool": "blender",
        "project": "lab",
        "scene": "demo",
        "operations": [{"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [0, 0, 0]}],
    }
    driver.apply_operations(plan1, str(tmp_path))
    assert len(list(driver.bpy.data.objects)) == 1

    plan2 = {"tool": "blender", "project": "lab", "scene": "demo", "operations": [{"op": "create_scene"}]}
    inspection = driver.apply_operations(plan2, str(tmp_path))
    assert inspection["objects"] == []
    assert len(list(driver.bpy.data.objects)) == 0


def test_render_writes_a_real_nonuniform_png_and_reports_sha256(driver, tmp_path) -> None:
    from app.creative3d.compare import check_render

    plan = _basic_plan()
    plan["operations"].append({"op": "render", "width": 64, "height": 48, "engine": "workbench"})
    inspection = driver.apply_operations(plan, str(tmp_path))
    render = inspection["render"]
    assert render is not None
    assert render["width"] == 64
    assert render["height"] == 48
    png_bytes = Path(render["path"]).read_bytes()
    assert render["bytes"] == len(png_bytes)
    assert render["sha256"] == hashlib.sha256(png_bytes).hexdigest()
    # The independent reader (the same one app.creative3d.service uses before ever
    # storing a render) must accept it as a genuine, non-trivial image.
    assert check_render(png_bytes) is None


def test_render_without_a_camera_is_an_error(driver, tmp_path) -> None:
    plan = {
        "tool": "blender",
        "project": "lab",
        "scene": "demo",
        "operations": [
            {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [0, 0, 0]},
            {"op": "render"},
        ],
    }
    inspection = driver.apply_operations(plan, str(tmp_path))
    assert inspection["render"] is None
    assert any("no camera" in e for e in inspection["errors"])


def test_attach_script_is_refused_by_the_blender_driver(driver, tmp_path) -> None:
    plan = {
        "tool": "blender",
        "project": "lab",
        "scene": "demo",
        "operations": [
            {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [0, 0, 0]},
            {"op": "attach_script", "name": "Kup", "script_id": "Spinner"},
        ],
    }
    inspection = driver.apply_operations(plan, str(tmp_path))
    assert any("not supported" in e for e in inspection["errors"])


def test_main_writes_out_json_and_a_blend_file(driver, tmp_path) -> None:
    plan_path = tmp_path / "plan.json"
    out_path = tmp_path / "out.json"
    plan_path.write_text(json.dumps(_basic_plan()), encoding="utf-8")
    exit_code = driver.main(["--", str(plan_path), str(out_path)])
    assert exit_code == 0
    assert out_path.exists()
    inspection = json.loads(out_path.read_text(encoding="utf-8"))
    assert inspection["errors"] == []
    assert (tmp_path / "demo.blend").exists()


def test_write_out_json_bounds_a_runaway_inspection(driver, tmp_path) -> None:
    out_path = tmp_path / "out.json"
    huge_objects = [
        {"name": f"o{i}", "type": "MESH", "location": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}
        for i in range(5000)
    ]
    inspection = {"objects": huge_objects, "camera": None, "lights": [], "render": None, "errors": []}
    driver.write_out_json(str(out_path), inspection)
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(out_path.read_bytes()) <= driver.MAX_OUT_JSON_BYTES
    assert len(written["objects"]) <= 50
    assert any("truncated" in e for e in written["errors"])
