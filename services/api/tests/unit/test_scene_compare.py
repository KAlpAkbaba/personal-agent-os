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
    return ScenePlan.model_validate(
        {"tool": "blender", "project": "lab", "scene": "demo", "operations": ops}
    )


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
    """Also the regression for a real bug the Blender lab found (2026-09-08):
    ``_sphere_plan()``'s camera is added with its own default rotation (0,0,0) and
    THEN aimed with ``set_camera ... look_at`` — before the fix, compare() checked
    the stale add_primitive rotation against the camera's real (aimed) rotation and
    reported a false mismatch on every correct plan that ever aims a camera it just
    created."""
    result = compare(_sphere_plan(), _matching_inspection())
    assert result.ok, result.mismatches
    assert result.checked > 0


def test_compare_folds_a_later_transform_over_an_earlier_add_primitive() -> None:
    """Regression (found running the real Blender lab, scripts/tests/blender-scene-
    lab.py, 2026-09-08): a plan that adds an object at its default scale and then
    transforms it must be checked against where it ENDS UP, never flagged for no
    longer matching add_primitive's own original scale — the M25 lesson mirrored
    from the M24 "never a vacuous gate" one: don't re-litigate a superseded
    constraint either."""
    plan = ScenePlan.model_validate(
        {
            "tool": "blender",
            "project": "lab",
            "scene": "demo",
            "operations": [
                {"op": "create_scene"},
                {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [0.0, 0.0, 0.0]},
                {"op": "transform", "name": "Kup", "scale": [1.5, 1.5, 1.5]},
            ],
        }
    )
    inspection = {
        "objects": [
            {
                "name": "Kup",
                "type": "MESH",
                "location": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.5, 1.5, 1.5],
            }
        ],
        "camera": None,
        "lights": [],
        "render": None,
        "errors": [],
    }
    result = compare(plan, inspection)
    assert result.ok, result.mismatches
    # The stale expectation (scale 1.0, add_primitive's own default) must never be
    # what gets checked - only ONE scale constraint per object, the folded one.
    assert not [m for m in result.mismatches if m.field == "scale.x"]


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
    inspection = {
        "objects": [],
        "camera": None,
        "lights": [],
        "render": {"path": "x"},
        "errors": [],
    }
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
    result = compare(
        plan, {"objects": [], "camera": None, "lights": [], "render": None, "errors": []}
    )
    assert not result.ok
    assert result.checked == 0
    assert result.reason == "no_constraints"


def test_compare_empty_plan_is_also_a_mismatch() -> None:
    plan = ScenePlan.model_validate(
        {"tool": "blender", "project": "lab", "scene": "demo", "operations": []}
    )
    result = compare(plan, _matching_inspection())
    assert not result.ok
    assert result.reason == "no_constraints"


# ------------------------------------------------- what the M25 security review found


def test_a_rotation_stated_after_an_aim_is_still_a_constraint() -> None:
    """The aim supersedes a rotation stated BEFORE it — that is the fold the Blender lab
    earned. It must not swallow a rotation the plan states AFTER the aim: the review showed
    a driver silently dropping that instruction was invisible whenever the leftover default
    happened to satisfy the aim tolerance."""
    plan = ScenePlan.model_validate(
        {
            "tool": "blender",
            "project": "lab",
            "scene": "demo",
            "operations": [
                {"op": "create_scene"},
                {
                    "op": "add_primitive",
                    "kind": "camera",
                    "name": "Kamera",
                    "location": [0.0, -6.0, 3.0],
                },
                {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [0.0, 0.0, 0.0]},
                {"op": "set_camera", "name": "Kamera", "look_at": "Kup"},
                {"op": "transform", "name": "Kamera", "rotation": [45.0, 0.0, 0.0]},
            ],
        }
    )
    inspection = {
        "camera": "Kamera",
        "objects": [
            {
                "name": "Kamera",
                "type": "CAMERA",
                "location": [0.0, -6.0, 3.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
            {
                "name": "Kup",
                "type": "MESH",
                "location": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
        ],
        "lights": [],
    }
    result = compare(plan, inspection)
    assert result.ok is False
    assert any(
        m.object_name == "Kamera" and m.field.startswith("rotation") for m in result.mismatches
    )


def test_an_aim_after_a_rotation_still_supersedes_it() -> None:
    """And the fold itself still holds: a rotation stated BEFORE the aim is the aim's to
    decide, so an add-then-aim plan is not flagged for the primitive's default rotation."""
    plan = ScenePlan.model_validate(
        {
            "tool": "blender",
            "project": "lab",
            "scene": "demo",
            "operations": [
                {"op": "create_scene"},
                {
                    "op": "add_primitive",
                    "kind": "camera",
                    "name": "Kamera",
                    "location": [0.0, -6.0, 3.0],
                },
                {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [0.0, 0.0, 0.0]},
                {"op": "transform", "name": "Kamera", "rotation": [10.0, 0.0, 0.0]},
                {"op": "set_camera", "name": "Kamera", "look_at": "Kup"},
            ],
        }
    )
    inspection = {
        "camera": "Kamera",
        "objects": [
            {
                "name": "Kamera",
                "type": "CAMERA",
                "location": [0.0, -6.0, 3.0],
                "rotation": [-1.0472, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
            {
                "name": "Kup",
                "type": "MESH",
                "location": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
        ],
        "lights": [],
    }
    result = compare(plan, inspection)
    assert not any(
        m.object_name == "Kamera" and m.field.startswith("rotation") for m in result.mismatches
    )


def test_a_render_larger_than_the_bound_is_a_mismatch() -> None:
    """The spec bounds the render; the independent reader is where that is measured, not
    trusted from the plan the driver was handed (the review's second Low)."""
    import io

    from PIL import Image

    from app.creative3d.compare import RENDER_MAX_WIDTH, check_render

    img = Image.new("RGB", (RENDER_MAX_WIDTH + 8, 64))
    for x in range(img.width):  # not uniform, so only the size can fail it
        for y in range(img.height):
            img.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    mismatch = check_render(buf.getvalue())
    assert mismatch is not None
    assert mismatch.field == "render.size"
