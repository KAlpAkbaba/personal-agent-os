"""The closed loop's own comparison (docs/M27_CREATIVE_TOOLS_SPEC.md §3, §4, ADR-0093
decision 4): Pillow alone, never SSIM, never a match over an empty comparison.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.creative.compare import (
    NO_CONSTRAINTS,
    check_output,
    compare,
    tiled_similarity,
)
from app.creative.execute import execute
from app.creative.spec import CreativePlan


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def test_module_never_imports_numpy() -> None:
    """ADR-0093 decision 4, enforced by a test: numpy is not a dependency of this
    repository, so this module must not IMPORT it under any name (the module's own
    docstring is allowed to quote the ADR text explaining why, which mentions the
    word — this checks for an actual import statement, not the word's presence)."""
    import ast

    import app.creative.compare as compare_module

    with open(compare_module.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])
    assert "numpy" not in imported_names


def test_module_never_says_ssim() -> None:
    """ADR-0093 decision 4: the aggregate is not SSIM and is never called that."""
    import app.creative.compare as compare_module

    with open(compare_module.__file__, encoding="utf-8") as fh:
        text = fh.read().lower()
    # "is not ssim"/"never called ssim" ARE allowed (they say what it is NOT); a bare
    # claim like "ssim similarity" or "compute ssim" would not be.
    assert "compute ssim" not in text
    assert "ssim(" not in text


def test_check_output_rejects_empty_bytes() -> None:
    mismatch = check_output(b"")
    assert mismatch is not None
    assert "empty" in mismatch.detail


def test_check_output_accepts_a_uniform_solid_colour_canvas() -> None:
    """A flat, single-colour canvas is a perfectly legitimate Paint output (a fresh
    "new" with a plain background) — NOT a defect. A real bug found while writing
    this suite: an earlier version of ``check_output`` borrowed
    ``app.creative3d.compare.check_render``'s own non-uniformity heuristic, which
    makes sense for a rendered 3D SCENE (a blank frame implies no camera/light
    fired) but wrongly rejected every solid-colour 2D canvas as "empty"."""
    flat = _png(50, 50, (128, 128, 128))
    assert check_output(flat) is None


def test_check_output_rejects_a_fully_transparent_image() -> None:
    buf = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buf, format="PNG")
    mismatch = check_output(buf.getvalue())
    assert mismatch is not None
    assert "transparent" in mismatch.actual


def test_check_output_accepts_a_real_image() -> None:
    plan = CreativePlan.model_validate(
        {
            "tool": "paint",
            "name": "lab",
            "operations": [
                {"op": "new", "width": 40, "height": 40, "background": [255, 255, 255, 255]},
                {"op": "shape", "kind": "rect", "box": [0, 0, 20, 20], "fill": [0, 0, 0, 255]},
            ],
        }
    )
    result = execute(plan, None)
    assert check_output(result.image_bytes) is None


def test_compare_reports_no_constraints_for_an_empty_plan() -> None:
    """A plan with no ``new`` (so no independently-knowable expected size), no
    shapes/text and no reference asks for nothing checkable at all — never a match
    over an empty comparison (module docstring)."""
    plan = CreativePlan.model_validate({"tool": "paint", "name": "lab", "operations": []})
    blank = _png(1, 1, (255, 255, 255))
    cmp_result = compare(plan, {}, image_bytes=blank)
    assert cmp_result.ok is False
    assert cmp_result.reason == NO_CONSTRAINTS
    assert cmp_result.checked == 0


def test_compare_checks_the_stated_canvas_size_when_the_plan_names_one() -> None:
    """A plan that DOES name a canvas size via ``new`` has one real, checkable
    constraint even with nothing else drawn — a genuine dimension check, not a
    vacuous "nothing to check"."""
    plan = CreativePlan.model_validate(
        {"tool": "paint", "name": "lab", "operations": [{"op": "new", "width": 10, "height": 10}]}
    )
    result = execute(plan, None)
    cmp_result = compare(plan, result.inspection(), image_bytes=result.image_bytes)
    assert cmp_result.ok is True
    assert cmp_result.checked == 1
    assert cmp_result.reason is None


def test_compare_finds_the_planned_shape_present() -> None:
    plan = CreativePlan.model_validate(
        {
            "tool": "paint",
            "name": "lab",
            "operations": [
                {"op": "new", "width": 100, "height": 100, "background": [255, 255, 255, 255]},
                {"op": "shape", "kind": "rect", "box": [10, 10, 50, 50], "fill": [255, 0, 0, 255]},
            ],
        }
    )
    result = execute(plan, None)
    cmp_result = compare(plan, result.inspection(), image_bytes=result.image_bytes)
    assert cmp_result.ok
    assert cmp_result.checked >= 2  # dimension + shape presence


def test_compare_names_a_missing_shape_as_a_defect() -> None:
    """A plan that PLANS a shape but whose executed image never actually drew it
    (constructed directly, bypassing the executor, to prove the comparison is a real
    independent check and not a restatement of the plan)."""
    plan = CreativePlan.model_validate(
        {
            "tool": "paint",
            "name": "lab",
            "operations": [
                {"op": "new", "width": 100, "height": 100, "background": [255, 255, 255, 255]},
                {"op": "shape", "kind": "rect", "box": [10, 10, 50, 50], "fill": [0, 255, 0, 255]},
            ],
        }
    )
    # A blank white image — as if the shape op silently failed.
    blank = _png(100, 100, (255, 255, 255))
    cmp_result = compare(plan, {}, image_bytes=blank)
    assert not cmp_result.ok
    assert any(m.field == "presence" for m in cmp_result.mismatches)


def test_compare_dimension_mismatch_is_named() -> None:
    plan = CreativePlan.model_validate(
        {"tool": "paint", "name": "lab", "operations": [{"op": "new", "width": 100, "height": 100}]}
    )
    wrong_size = _png(50, 50, (1, 2, 3))
    cmp_result = compare(plan, {}, image_bytes=wrong_size)
    assert not cmp_result.ok
    assert any(m.field == "size" for m in cmp_result.mismatches)


def test_tiled_similarity_is_high_for_identical_images() -> None:
    img = Image.new("RGBA", (80, 80), (10, 20, 30, 255))
    assert tiled_similarity(img, img) == pytest.approx(1.0)


def test_tiled_similarity_is_low_for_very_different_images() -> None:
    a = Image.new("RGBA", (80, 80), (255, 255, 255, 255))
    b = Image.new("RGBA", (80, 80), (0, 0, 0, 255))
    assert tiled_similarity(a, b) < 0.2


def test_compare_with_reference_uses_tiled_similarity() -> None:
    plan = CreativePlan.model_validate(
        {
            "tool": "paint",
            "name": "lab",
            "operations": [{"op": "new", "width": 40, "height": 40, "background": [1, 2, 3, 255]}],
        }
    )
    result = execute(plan, None)
    reference = _png(40, 40, (1, 2, 3))
    cmp_result = compare(
        plan, result.inspection(), image_bytes=result.image_bytes, reference_bytes=reference
    )
    assert cmp_result.similarity is not None
    assert cmp_result.similarity > 0.9
