"""``app.creative3d.compare`` (docs/M25_CREATIVE_3D_SPEC.md §4): tolerances, the
camera-aim recovery, the render reader on a real (incompressible) PNG and on a
uniform one, and the empty-comparison refusal — the M24 lesson restated: never a
match over nothing actually checked.
"""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image

from app.creative3d.compare import (
    CAMERA_AIM_TOLERANCE_DEG,
    check_render,
    compare,
    forward_vector,
)
from app.creative3d.spec import ScenePlan


def _png_bytes(width: int, height: int, *, uniform: bool, color=(10, 20, 30)) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    if not uniform:
        rng = random.Random(7)
        pixels = img.load()
        for x in range(width):
            for y in range(height):
                pixels[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ============================================================================ render


def test_check_render_accepts_a_real_nonuniform_png() -> None:
    assert check_render(_png_bytes(64, 64, uniform=False)) is None


def test_check_render_refuses_a_uniform_image() -> None:
    # Large enough that the PNG itself clears the byte-count floor (a solid colour
    # compresses extremely well regardless of size), so this genuinely exercises the
    # stddev check rather than the separate "too small to be real" one.
    mismatch = check_render(_png_bytes(200, 200, uniform=True))
    assert mismatch is not None
    assert mismatch.field == "render.stddev"


def test_check_render_refuses_none() -> None:
    mismatch = check_render(None)
    assert mismatch is not None
    assert mismatch.object_name == "render"


def test_check_render_refuses_too_few_bytes() -> None:
    mismatch = check_render(b"\x00\x01")
    assert mismatch is not None
    assert "bytes" in mismatch.field


def test_check_render_refuses_undecodable_bytes() -> None:
    mismatch = check_render(b"not a png" * 40)
    assert mismatch is not None


# ========================================================================= forward


@pytest.mark.parametrize(
    "rotation_deg,expected",
    [
        ((0.0, 0.0, 0.0), (0.0, 0.0, -1.0)),
        ((90.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ((90.0, 0.0, 90.0), (-1.0, 0.0, 0.0)),
    ],
)
def test_forward_vector_known_rotations(rotation_deg, expected) -> None:
    fwd = forward_vector(rotation_deg)
    for actual, exp in zip(fwd, expected, strict=True):
        assert actual == pytest.approx(exp, abs=1e-9)


# ========================================================================= compare


def _sphere_plan(**camera_kwargs) -> ScenePlan:
    ops = [
        {"op": "create_scene"},
        {"op": "add_primitive", "kind": "sphere", "name": "Kure", "location": [0.0, 0.0, 0.0]},
        {"op": "add_primitive", "kind": "camera", "name": "Kamera", "location": [0.0, -5.0, 0.0]},
        {"op": "set_material", "name": "Kure", "color": [1.0, 0.0, 0.0, 1.0]},
    ]
    if camera_kwargs.get("look_at", True):
        ops.append({"op": "set_camera", "name": "Kamera", "look_at": "Kure"})
    return ScenePlan.model_validate({"tool": "blender", "project": "lab", "scene": "demo", "operations": ops})


def _matching_inspection() -> dict:
    return {
        "objects": [
            {
                "name": "Kure",
                "type": "MESH",
                "location": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "material_color": [1.0, 0.0, 0.0, 1.0],
            },
            {
                "name": "Kamera",
                "type": "CAMERA",
                "location": [0.0, -5.0, 0.0],
                "rotation": [90.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
        ],
        "camera": "Kamera",
        "lights": [],
        "render": None,
        "errors": [],
    }


def test_compare_matches_a_correct_inspection() -> None:
    result = compare(_sphere_plan(), _matching_inspection())
    assert result.ok, result.mismatches
    assert result.checked > 0


def test_compare_reports_a_mismatch_naming_the_object_and_axis() -> None:
    inspection = _matching_inspection()
    inspection["objects"][0]["location"] = [5.0, 0.0, 0.0]  # Kure moved on X
    result = compare(_sphere_plan(), inspection)
    assert not result.ok
    hit = [m for m in result.mismatches if m.object_name == "Kure" and m.field == "location.x"]
    assert hit, result.mismatches


def test_compare_missing_object_is_a_mismatch() -> None:
    inspection = _matching_inspection()
    inspection["objects"] = [o for o in inspection["objects"] if o["name"] != "Kure"]
    result = compare(_sphere_plan(), inspection)
    assert not result.ok
    assert any(m.object_name == "Kure" and m.field == "presence" for m in result.mismatches)


def test_compare_color_out_of_tolerance() -> None:
    inspection = _matching_inspection()
    inspection["objects"][0]["material_color"] = [0.0, 1.0, 0.0, 1.0]  # green, not red
    result = compare(_sphere_plan(), inspection)
    assert not result.ok
    assert any(m.field.startswith("material_color") for m in result.mismatches)


def test_compare_camera_aim_within_tolerance_passes() -> None:
    inspection = _matching_inspection()
    # Nudge the camera's rotation by less than the 2-degree tolerance.
    inspection["objects"][1]["rotation"] = [90.0 + (CAMERA_AIM_TOLERANCE_DEG * 0.5), 0.0, 0.0]
    result = compare(_sphere_plan(), inspection)
    assert result.ok, result.mismatches


def test_compare_camera_aim_outside_tolerance_fails() -> None:
    inspection = _matching_inspection()
    inspection["objects"][1]["rotation"] = [90.0 + (CAMERA_AIM_TOLERANCE_DEG * 5), 0.0, 0.0]
    result = compare(_sphere_plan(), inspection)
    assert not result.ok
    assert any(m.field == "camera_aim.angle_deg" for m in result.mismatches)


def test_compare_render_present_and_nontrivial() -> None:
    plan = ScenePlan.model_validate(
        {
            "tool": "blender",
            "project": "lab",
            "scene": "demo",
            "operations": [{"op": "render", "width": 64, "height": 48}],
        }
    )
    inspection = {"objects": [], "camera": None, "lights": [], "render": {"path": "x"}, "errors": []}
    result = compare(plan, inspection, render_bytes=_png_bytes(64, 48, uniform=False))
    assert result.ok

    bad = compare(plan, inspection, render_bytes=_png_bytes(64, 48, uniform=True))
    assert not bad.ok


def test_compare_never_reports_a_match_over_an_empty_comparison() -> None:
    """The M24 lesson (module docstring): a plan with nothing checkable is a
    mismatch, not a pass."""
    plan = ScenePlan.model_validate(
        {"tool": "blender", "project": "lab", "scene": "demo", "operations": [{"op": "inspect"}]}
    )
    result = compare(plan, {"objects": [], "camera": None, "lights": [], "render": None, "errors": []})
    assert not result.ok
    assert result.checked == 0
    assert result.reason == "no_constraints"


def test_compare_empty_plan_is_also_a_mismatch() -> None:
    plan = ScenePlan.model_validate({"tool": "blender", "project": "lab", "scene": "demo", "operations": []})
    result = compare(plan, _matching_inspection())
    assert not result.ok
    assert result.reason == "no_constraints"
