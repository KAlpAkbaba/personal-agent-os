"""``CreativePlan`` (docs/M27_CREATIVE_TOOLS_SPEC.md §2, ADR-0093): the closed
operation vocabulary, bounded numbers, bounded text, and the output-naming rule's own
path/traversal refusal, BEFORE anything ever touches a file.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.creative.spec import (
    CreativePlan,
    PlanValidationError,
    next_output_name,
)


def test_new_canvas_plan_validates() -> None:
    plan = CreativePlan.model_validate(
        {
            "tool": "paint",
            "name": "lab",
            "operations": [
                {"op": "new", "width": 320, "height": 240, "background": [255, 255, 255, 255]},
                {
                    "op": "shape",
                    "kind": "rect",
                    "box": [10, 10, 100, 80],
                    "fill": [255, 0, 0, 255],
                },
                {"op": "export", "format": "png"},
            ],
        }
    )
    assert plan.tool == "paint"
    assert len(plan.operations) == 3


def test_plan_json_is_canonical_and_stable() -> None:
    plan = CreativePlan.model_validate(
        {"tool": "paint", "name": "lab", "operations": [{"op": "new", "width": 10, "height": 10}]}
    )
    a = plan.plan_json()
    b = CreativePlan.from_plan_json(a).plan_json()
    assert a == b
    assert " " not in a  # no incidental whitespace


@pytest.mark.parametrize(
    "name",
    [
        "E:\\hologram\\HologramVehicleTest",
        "../escape",
        "C:/whatever",
        "/etc/passwd",
        "a/../b",
    ],
)
def test_name_refuses_path_shaped_values(name: str) -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate({"tool": "paint", "name": name, "operations": []})


@pytest.mark.parametrize(
    "source",
    [
        "E:\\hologram\\HologramVehicleTest\\scene.png",
        "../escape.png",
        "C:/whatever.png",
        "/etc/passwd",
    ],
)
def test_source_refuses_path_shaped_values(source: str) -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(
            {"tool": "paint", "name": "lab", "source": source, "operations": []}
        )


def test_layer_op_refused_for_paint() -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(
            {
                "tool": "paint",
                "name": "lab",
                "operations": [{"op": "layer", "action": "add", "name": "bg"}],
            }
        )


def test_layer_op_allowed_for_photoshop() -> None:
    plan = CreativePlan.model_validate(
        {
            "tool": "photoshop",
            "name": "lab",
            "operations": [{"op": "layer", "action": "add", "name": "bg"}],
        }
    )
    assert plan.operations[0].op == "layer"


def test_open_requires_source() -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(
            {"tool": "paint", "name": "lab", "operations": [{"op": "open"}]}
        )


def test_dimension_bound_enforced() -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(
            {
                "tool": "paint",
                "name": "lab",
                "operations": [{"op": "new", "width": 999999, "height": 10}],
            }
        )


def test_color_channel_bound_enforced() -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(
            {
                "tool": "paint",
                "name": "lab",
                "operations": [
                    {"op": "new", "width": 10, "height": 10, "background": [999, 0, 0, 0]}
                ],
            }
        )


def test_max_operations_bound() -> None:
    ops = [{"op": "inspect"} for _ in range(65)]
    with pytest.raises(ValidationError):
        CreativePlan.model_validate({"tool": "paint", "name": "lab", "operations": ops})


def test_draw_requires_fill_or_stroke() -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(
            {
                "tool": "paint",
                "name": "lab",
                "operations": [
                    {"op": "draw", "shape": "rect", "points": [[0, 0], [1, 1]]},
                ],
            }
        )


def test_next_output_name_never_overwrites_the_source() -> None:
    existing = frozenset({"lab-pagentos-1.png"})
    name = next_output_name("lab", "png", existing=existing)
    assert name == "lab-pagentos-2.png"


def test_next_output_name_refuses_path_shaped_base() -> None:
    with pytest.raises(PlanValidationError):
        next_output_name("../escape", "png")


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(
            {"tool": "paint", "name": "lab", "operations": [], "not_a_real_field": 1}
        )


def test_control_characters_refused_in_label() -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(
            {"tool": "paint", "name": "lab", "operations": [], "label": "a\x00b"}
        )


def test_export_path_refuses_traversal() -> None:
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(
            {
                "tool": "paint",
                "name": "lab",
                "operations": [{"op": "export", "format": "png", "path": "../x.png"}],
            }
        )
