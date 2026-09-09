"""The Pillow execution of every operation (docs/M27_CREATIVE_TOOLS_SPEC.md §1, §2,
ADR-0093 decision 1): a Paint edit is performed on the FILE with Pillow, deterministic
and inspectable. Mirrors ``test_blender_driver.py``'s own "run every operation, read
the inspection back" discipline for M25.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.creative.execute import ExecutionError, execute
from app.creative.spec import CreativePlan


def _plan(operations: list[dict]) -> CreativePlan:
    return CreativePlan.model_validate({"tool": "paint", "name": "lab", "operations": operations})


def test_new_canvas_has_requested_size_and_background() -> None:
    plan = _plan([{"op": "new", "width": 320, "height": 240, "background": [10, 20, 30, 255]}])
    result = execute(plan, None)
    assert result.width == 320
    assert result.height == 240
    with Image.open(io.BytesIO(result.image_bytes)) as img:
        assert img.size == (320, 240)
        assert img.convert("RGB").getpixel((5, 5)) == (10, 20, 30)


def test_shape_and_add_text_are_recorded_in_the_inspection() -> None:
    plan = _plan(
        [
            {"op": "new", "width": 320, "height": 240, "background": [255, 255, 255, 255]},
            {"op": "shape", "kind": "rect", "box": [10, 10, 100, 80], "fill": [255, 0, 0, 255]},
            {
                "op": "shape",
                "kind": "ellipse",
                "box": [150, 20, 250, 120],
                "fill": [0, 0, 255, 255],
            },
            {
                "op": "add_text",
                "text": "Merhaba",
                "position": [10, 150],
                "size": 20,
                "colour": [0, 0, 0, 255],
            },
            {"op": "export", "format": "png"},
        ]
    )
    result = execute(plan, None)
    inspection = result.inspection()
    assert len(inspection["shapes"]) == 2
    assert len(inspection["texts"]) == 1
    assert inspection["texts"][0]["text"] == "Merhaba"
    assert not result.errors
    # The drawn colours are actually present at the pixel level (an independent
    # decode, not the executor's own bookkeeping).
    with Image.open(io.BytesIO(result.image_bytes)) as img:
        rgb = img.convert("RGB")
        assert rgb.getpixel((50, 40)) == (255, 0, 0)
        assert rgb.getpixel((200, 70)) == (0, 0, 255)


def test_transform_rotate_changes_dimensions() -> None:
    plan = _plan(
        [
            {"op": "new", "width": 100, "height": 50, "background": [255, 255, 255, 255]},
            {"op": "transform", "kind": "rotate", "value": 90.0},
        ]
    )
    result = execute(plan, None)
    assert result.width == 50
    assert result.height == 100


def test_transform_flip_horizontal() -> None:
    plan = _plan(
        [
            {"op": "new", "width": 100, "height": 100, "background": [255, 255, 255, 255]},
            {"op": "shape", "kind": "rect", "box": [0, 0, 10, 10], "fill": [255, 0, 0, 255]},
            {"op": "transform", "kind": "flip", "direction": "horizontal"},
        ]
    )
    result = execute(plan, None)
    with Image.open(io.BytesIO(result.image_bytes)) as img:
        rgb = img.convert("RGB")
        assert rgb.getpixel((95, 5)) == (255, 0, 0)
        assert rgb.getpixel((5, 5)) == (255, 255, 255)


def test_crop_bounds_are_clamped_to_the_canvas() -> None:
    plan = _plan(
        [
            {"op": "new", "width": 50, "height": 50, "background": [255, 255, 255, 255]},
            {"op": "crop", "box": [-100, -100, 1000, 1000]},
        ]
    )
    result = execute(plan, None)
    assert result.width == 50
    assert result.height == 50


def test_background_remove_threshold_makes_matching_pixels_transparent() -> None:
    plan = _plan(
        [
            {"op": "new", "width": 20, "height": 20, "background": [0, 255, 0, 255]},
            {"op": "shape", "kind": "rect", "box": [5, 5, 15, 15], "fill": [255, 0, 0, 255]},
            {"op": "background_remove", "method": "threshold", "tolerance": 10, "seed": [0, 0]},
        ]
    )
    result = execute(plan, None)
    assert result.background_removed
    with Image.open(io.BytesIO(result.image_bytes)) as img:
        assert img.mode == "RGBA"
        corner_alpha = img.getpixel((0, 0))[3]
        center_alpha = img.getpixel((10, 10))[3]
    assert corner_alpha == 0
    assert center_alpha == 255


def test_open_with_no_source_bytes_records_an_error_and_still_produces_output() -> None:
    # A validated plan (a non-null ``source``) whose bytes the CALLER could not
    # actually fetch (e.g. the object store had nothing under that key) — the
    # executor stays defensive rather than crashing (module docstring's "never
    # raises for a runtime-only problem" rule).
    plan = CreativePlan.model_validate(
        {"tool": "paint", "name": "lab", "source": "missing-key", "operations": [{"op": "open"}]}
    )
    result = execute(plan, None)
    assert any("no source bytes" in e for e in result.errors)


def test_open_unreadable_source_raises_execution_error() -> None:
    plan = CreativePlan.model_validate(
        {
            "tool": "paint",
            "name": "lab",
            "source": "not-a-real-image",
            "operations": [{"op": "open"}],
        }
    )
    with pytest.raises(ExecutionError):
        execute(plan, b"not a real image at all")


def test_export_svg_wraps_the_raster_honestly() -> None:
    plan = _plan(
        [
            {"op": "new", "width": 10, "height": 10, "background": [1, 2, 3, 255]},
            {"op": "export", "format": "svg"},
        ]
    )
    result = execute(plan, None)
    assert result.image_bytes.startswith(b"<svg")
    assert b"image/png;base64" in result.image_bytes


def test_output_name_uses_the_naming_rule() -> None:
    plan = _plan(
        [
            {"op": "new", "width": 10, "height": 10},
            {"op": "export", "format": "png"},
        ]
    )
    result = execute(plan, None)
    assert result.output_name == "lab-pagentos-1.png"


def test_no_canvas_at_all_is_recorded_as_an_error() -> None:
    plan = _plan([{"op": "inspect"}])
    result = execute(plan, None)
    assert result.width == 1 and result.height == 1
    assert any("no canvas" in e for e in result.errors)
