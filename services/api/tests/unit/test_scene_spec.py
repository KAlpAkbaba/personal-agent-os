"""``app.creative3d.spec.ScenePlan`` (docs/M25_CREATIVE_3D_SPEC.md §2): the closed
operation vocabulary, closed-alphabet names, bounded numbers, the fixed Unity script
catalogue, and ``plan_json()`` canonical serialisation.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.creative3d.spec import MAX_OPERATIONS, ScenePlan

VALID_PLAN = {
    "tool": "blender",
    "project": "lab-fixture",
    "scene": "demo",
    "operations": [
        {"op": "create_scene"},
        {"op": "add_primitive", "kind": "sphere", "name": "Kure", "location": [0.0, 0.0, 0.0]},
        {"op": "render", "width": 320, "height": 240, "engine": "workbench"},
        {"op": "inspect"},
    ],
}


def test_a_valid_plan_parses() -> None:
    plan = ScenePlan.model_validate(VALID_PLAN)
    assert plan.tool == "blender"
    assert len(plan.operations) == 4


def test_plan_json_is_canonical_and_deterministic() -> None:
    plan = ScenePlan.model_validate(VALID_PLAN)
    first = plan.plan_json()
    second = ScenePlan.from_plan_json(first).plan_json()
    assert first == second
    assert json.loads(first) == json.loads(json.dumps(plan.model_dump(mode="json")))


@pytest.mark.parametrize(
    "bad_project", ["../x", "C:\\x", "E:\\hologram\\HologramVehicleTest", "LAB", "lab fixture", ""]
)
def test_a_path_shaped_project_is_refused(bad_project: str) -> None:
    plan = dict(VALID_PLAN)
    plan["project"] = bad_project
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_an_operation_outside_the_vocabulary_is_refused() -> None:
    plan = dict(VALID_PLAN)
    plan["operations"] = [{"op": "delete_scene"}]
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_a_name_outside_the_closed_alphabet_is_refused() -> None:
    plan = dict(VALID_PLAN)
    plan["operations"] = [
        {"op": "add_primitive", "kind": "cube", "name": "Küp; DROP TABLE", "location": [0, 0, 0]}
    ]
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


@pytest.mark.parametrize("location", [[10000.0, 0.0, 0.0], [0.0, -10000.0, 0.0]])
def test_an_out_of_bound_location_is_refused(location: list[float]) -> None:
    plan = dict(VALID_PLAN)
    plan["operations"] = [
        {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": location}
    ]
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_a_zero_or_negative_scale_is_refused() -> None:
    plan = dict(VALID_PLAN)
    plan["operations"] = [{"op": "transform", "name": "Kure", "scale": [0.0, 1.0, 1.0]}]
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_a_color_channel_out_of_zero_one_is_refused() -> None:
    plan = dict(VALID_PLAN)
    plan["operations"] = [{"op": "set_material", "name": "Kure", "color": [1.5, 0.0, 0.0, 1.0]}]
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_render_dimensions_are_bounded() -> None:
    plan = dict(VALID_PLAN)
    plan["operations"] = [{"op": "render", "width": 4000, "height": 240}]
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_more_than_max_operations_is_refused() -> None:
    plan = dict(VALID_PLAN)
    plan["operations"] = [{"op": "inspect"}] * (MAX_OPERATIONS + 1)
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_transform_with_nothing_to_do_is_refused() -> None:
    plan = dict(VALID_PLAN)
    plan["operations"] = [{"op": "transform", "name": "Kure"}]
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_attach_script_only_valid_for_unity() -> None:
    plan = dict(VALID_PLAN)
    plan["tool"] = "blender"
    plan["operations"] = [{"op": "attach_script", "name": "Kup", "script_id": "Spinner"}]
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)

    plan["tool"] = "unity"
    ScenePlan.model_validate(plan)  # now valid


def test_attach_script_only_from_the_fixed_catalogue() -> None:
    plan = dict(VALID_PLAN)
    plan["tool"] = "unity"
    plan["operations"] = [{"op": "attach_script", "name": "Kup", "script_id": "OwnerWrittenScript"}]
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_label_forbids_control_characters() -> None:
    plan = dict(VALID_PLAN)
    plan["label"] = "hello\x00world"
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_label_is_the_only_free_text_field_and_never_required() -> None:
    plan = dict(VALID_PLAN)
    plan["label"] = "a short note"
    parsed = ScenePlan.model_validate(plan)
    assert parsed.label == "a short note"


def test_extra_fields_are_forbidden() -> None:
    plan = dict(VALID_PLAN)
    plan["unexpected"] = "value"
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)


def test_unknown_tool_is_refused() -> None:
    plan = dict(VALID_PLAN)
    plan["tool"] = "maya"
    with pytest.raises(ValidationError):
        ScenePlan.model_validate(plan)
