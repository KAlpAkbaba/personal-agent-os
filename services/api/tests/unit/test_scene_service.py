"""``SceneService`` (docs/M25_CREATIVE_3D_SPEC.md §2-§4, ADR-0088): create -> apply ->
inspect -> compare -> render, against SQLite + the fake device
(``tests.creative3d_support.FakeCreative3DDevice``) — the same harness discipline
``test_appfactory_service.py`` establishes for its own device-calling service.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.creative3d.models import (
    STATE_APPLIED,
    STATE_DEPENDENCY_UNAVAILABLE,
    STATE_RENDERED,
    SceneRow,
)
from app.creative3d.service import MAX_ACTIVE_SCENES, SceneService
from app.ledger.models import ActivityEventRow
from app.object_store import InMemoryObjectStore
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_SCENE, ObjectFocusRow
from tests.alarms_support import FakeDeviceAction
from tests.creative3d_support import UNITY_LICENSE_MESSAGE, FakeCreative3DDevice

TABLES = [SceneRow.__table__, ObjectFocusRow.__table__, ActivityEventRow.__table__]


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
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


BLENDER_CREATE_PLAN = {
    "tool": "blender",
    "project": "lab",
    "scene": "demo",
    "operations": [
        {"op": "create_scene"},
        {"op": "add_primitive", "kind": "sphere", "name": "Kure", "location": [0.0, 0.0, 0.0]},
    ],
}


def test_create_scaffolds_and_applies_and_sets_focus(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    result = service.create(db, device, plan=BLENDER_CREATE_PLAN, session_id="s-1")
    assert result["execution_status"] == "executed", result
    assert result["state"] == STATE_APPLIED
    assert result["objects"] == 1
    assert device.capabilities_called() == ["project.scaffold", "project.run", "scene.inspect"]

    scene_id = result["scene_id"]
    row = db.get(SceneRow, uuid.UUID(scene_id))
    assert row is not None
    assert row.state == STATE_APPLIED
    assert row.inspection_json["objects"][0]["name"] == "Kure"

    current = focus_module.current(db, FOCUS_KIND_SCENE)
    assert current is not None
    assert current.object_id == scene_id


def test_create_with_an_invalid_plan_never_reaches_the_device(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    bad_plan = {"tool": "blender", "project": "LAB", "scene": "demo", "operations": []}
    result = service.create(db, device, plan=bad_plan)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "validation_error"
    assert device.calls == []
    assert db.query(SceneRow).count() == 0


def test_create_refuses_a_project_naming_the_owners_real_path(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    bad_plan = {
        "tool": "blender",
        "project": r"E:\hologram\HologramVehicleTest",
        "scene": "demo",
        "operations": [{"op": "create_scene"}],
    }
    result = service.create(db, device, plan=bad_plan)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "validation_error"
    assert device.calls == []


def test_apply_adds_a_primitive_onto_the_existing_scene(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    created = service.create(db, device, plan=BLENDER_CREATE_PLAN)
    scene_id = created["scene_id"]

    result = service.apply(
        db,
        device,
        target=scene_id,
        operations=[{"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [1.0, 1.0, 1.0]}],
        capability="scene.add",
    )
    assert result["execution_status"] == "executed", result
    assert result["objects"] == 2  # Kure from create() + Kup from apply(), same persistent state
    assert "eklendi" in result["speech"]
    assert "Kup" in result["speech"]


def test_apply_with_no_scene_created_yet_is_a_clarification(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    result = service.apply(db, device, target="current", operations=[{"op": "inspect"}])
    assert result["status"] == "needs_clarification"
    assert device.calls == []


def test_render_produces_a_stored_nontrivial_png(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    created = service.create(db, device, plan=BLENDER_CREATE_PLAN)
    scene_id = created["scene_id"]
    # A camera is required for a real render (the M25 lesson mirrored from Blender
    # itself: no camera, no render) — add one and aim it first.
    service.apply(
        db,
        device,
        target=scene_id,
        operations=[{"op": "add_primitive", "kind": "camera", "name": "Kamera", "location": [0.0, -5.0, 0.0]}],
    )
    service.apply(
        db, device, target=scene_id, operations=[{"op": "set_camera", "name": "Kamera", "look_at": "Kure"}]
    )
    result = service.render(db, device, target=scene_id, width=64, height=48)
    assert result["execution_status"] == "executed", result
    assert result["state"] == STATE_RENDERED

    row = db.get(SceneRow, uuid.UUID(scene_id))
    assert row.render_object_key is not None
    assert row.render_bytes is not None and row.render_bytes > 0
    stored = service._object_store.get(row.render_object_key)  # noqa: SLF001 - assert the real bytes landed
    assert len(stored) == row.render_bytes


def test_inspect_reads_back_without_mutating_state(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    created = service.create(db, device, plan=BLENDER_CREATE_PLAN)
    scene_id = created["scene_id"]
    device.calls.clear()
    result = service.inspect(db, device, target=scene_id)
    assert result["execution_status"] == "executed"
    assert result["objects"] == 1
    assert device.capabilities_called() == ["scene.inspect"]  # never scaffold/run again


def test_unity_licence_refusal_is_honest_never_a_crash_or_done(db: Session) -> None:
    fake_unity_device = FakeCreative3DDevice(unity_available=False)
    device = FakeDeviceAction(results=fake_unity_device.capability_results())
    service = SceneService()
    plan = {
        "tool": "unity",
        "project": "lab",
        "scene": "demo",
        "operations": [{"op": "create_scene"}],
    }
    result = service.create(db, device, plan=plan)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "dependency_unavailable"
    assert UNITY_LICENSE_MESSAGE in result["speech"]

    row = db.query(SceneRow).one()
    assert row.state == STATE_DEPENDENCY_UNAVAILABLE

    # A subsequent apply against the same row stays honest without calling the
    # device again — never silently "succeeds".
    device.calls.clear()
    result2 = service.apply(db, device, target=str(row.id), operations=[{"op": "inspect"}])
    assert result2["execution_status"] == "refused"
    assert device.calls == []


def test_unity_available_actually_applies(db: Session) -> None:
    fake_unity_device = FakeCreative3DDevice(unity_available=True)
    device = FakeDeviceAction(results=fake_unity_device.capability_results())
    service = SceneService()
    plan = {
        "tool": "unity",
        "project": "lab",
        "scene": "demo",
        "operations": [
            {"op": "create_scene"},
            {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [0.0, 0.0, 0.0]},
        ],
    }
    result = service.create(db, device, plan=plan)
    assert result["execution_status"] == "executed", result
    assert result["objects"] == 1


def test_two_scene_cap(db: Session, device: FakeDeviceAction, service: SceneService) -> None:
    plan_a = {**BLENDER_CREATE_PLAN, "project": "lab", "scene": "a"}
    plan_b = {**BLENDER_CREATE_PLAN, "project": "lab", "scene": "b"}
    plan_c = {**BLENDER_CREATE_PLAN, "project": "lab", "scene": "c"}
    r1 = service.create(db, device, plan=plan_a)
    assert r1["execution_status"] == "executed"
    r2 = service.create(db, device, plan=plan_b)
    assert r2["execution_status"] == "executed"
    r3 = service.create(db, device, plan=plan_c)
    assert r3["execution_status"] == "refused"
    assert r3["error_class"] == "invalid_argument"
    assert db.query(SceneRow).count() == 2 + 0  # the third never got created at all
    assert MAX_ACTIVE_SCENES == 2


def test_no_device_runtime_refuses_honestly(db: Session, service: SceneService) -> None:
    result = service.create(db, None, plan=BLENDER_CREATE_PLAN)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "capability_missing"


def test_status_and_list(db: Session, device: FakeDeviceAction, service: SceneService) -> None:
    created = service.create(db, device, plan=BLENDER_CREATE_PLAN)
    scene_id = created["scene_id"]
    status = service.status(db, target=scene_id)
    assert status["state"] == STATE_APPLIED
    listing = service.list(db)
    assert listing["execution_status"] == "executed"
    assert len(listing["scenes"]) == 1
    assert listing["scenes"][0]["scene_id"] == scene_id
