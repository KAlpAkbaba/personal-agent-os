"""B50 - the Unity path, as a licensed editor really runs it (ADR-0164).

Measured 2026-09-16, the first run after the owner's Unity licence became valid:

* the production driver (``drivers/SceneDriver.cs``) reads plans with Newtonsoft.Json, and no
  Unity project this system scaffolds ever declared that package - the driver could not
  compile ("The type or namespace name 'Newtonsoft' could not be found");
* ``attach_script`` resolves against three catalogue scripts no file in the repository held;
* the device lab never saw either, because it drove a lab driver of its own.

The Cloud Core now ships a pinned package manifest and the three scripts with every Unity
scaffold. This file holds that half; the device lab's ``SceneUnityProductionTests`` runs the
SAME bytes - read from this repository - in the real editor, against the plan fixture this
file keeps equal to ``ScenePlan.plan_json()``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.creative3d.compare import compare
from app.creative3d.models import SceneRow
from app.creative3d.service import DriverPinMismatch, SceneService, unity_support_files
from app.creative3d.spec import SCRIPT_CATALOGUE, ScenePlan
from app.ledger.models import ActivityEventRow
from app.object_store import InMemoryObjectStore
from app.operator.models import ObjectFocusRow
from tests.alarms_support import FakeDeviceAction
from tests.creative3d_support import FakeCreative3DDevice

API_ROOT = Path(__file__).resolve().parents[2]
DRIVERS = API_ROOT / "app" / "creative3d" / "drivers"
SUPPORT = DRIVERS / "unity_support"
PLAN_FIXTURE = API_ROOT / "tests" / "fixtures" / "creative3d" / "unity-plan.json"

#: The plan the device lab runs in the real editor: every operation the Unity driver handles.
UNITY_PLAN: dict[str, Any] = {
    "tool": "unity",
    "project": "lab",
    "scene": "unity-qualification",
    "operations": [
        {"op": "create_scene"},
        {"op": "add_primitive", "kind": "cube", "name": "Kutu", "location": [0.0, 0.5, 0.0]},
        {"op": "add_primitive", "kind": "sphere", "name": "Top", "location": [2.0, 0.5, 0.0]},
        {"op": "add_primitive", "kind": "plane", "name": "Zemin", "scale": [4.0, 1.0, 4.0]},
        {"op": "add_primitive", "kind": "light_sun", "name": "Gunes", "location": [0.0, 5.0, -2.0]},
        {"op": "add_primitive", "kind": "camera", "name": "Kamera", "location": [0.0, 3.0, -8.0]},
        {"op": "transform", "name": "Kutu", "rotation": [0.0, 45.0, 0.0]},
        {"op": "set_material", "name": "Kutu", "color": [0.9, 0.2, 0.1, 1.0]},
        {"op": "set_camera", "name": "Kamera", "look_at": "Kutu"},
        {"op": "set_light", "name": "Gunes", "energy": 2.0},
        {"op": "attach_script", "name": "Top", "script_id": "Spinner"},
        {"op": "attach_script", "name": "Zemin", "script_id": "Bouncer"},
        {"op": "attach_script", "name": "Kutu", "script_id": "ColorCycler"},
        {"op": "render", "width": 320, "height": 240},
        {"op": "run_tests", "frames": 30},
        {"op": "build_player", "target": "windows64"},
        {"op": "inspect"},
    ],
}


def test_every_support_file_matches_its_pin() -> None:
    pins = json.loads((DRIVERS / "manifest.json").read_text(encoding="utf-8"))["unity_support"][
        "files"
    ]
    on_disk = sorted(p.relative_to(SUPPORT).as_posix() for p in SUPPORT.rglob("*") if p.is_file())
    assert sorted(pins) == on_disk
    for relative, expected in pins.items():
        assert hashlib.sha256((SUPPORT / relative).read_bytes()).hexdigest() == expected, relative


def test_the_package_manifest_declares_what_the_driver_uses() -> None:
    driver = (DRIVERS / "SceneDriver.cs").read_text(encoding="utf-8")
    dependencies = json.loads((SUPPORT / "Packages" / "manifest.json").read_text(encoding="utf-8"))[
        "dependencies"
    ]
    assert "using Newtonsoft.Json" in driver
    assert "com.unity.nuget.newtonsoft-json" in dependencies
    assert "EncodeToPNG" in driver
    assert "com.unity.modules.imageconversion" in dependencies


def test_every_catalogue_script_exists_under_the_name_the_driver_resolves() -> None:
    driver = (DRIVERS / "SceneDriver.cs").read_text(encoding="utf-8")
    resolved = dict(re.findall(r'\{\s*"(\w+)",\s*"PagentOS\.Scripts\.(\w+)"\s*\}', driver))
    assert sorted(resolved) == sorted(SCRIPT_CATALOGUE)
    for script_id, class_name in resolved.items():
        source = (SUPPORT / "Assets" / "PagentOS" / "Scripts" / f"{class_name}.cs").read_text(
            encoding="utf-8"
        )
        assert "namespace PagentOS.Scripts" in source
        assert f"class {class_name} : MonoBehaviour" in source, script_id


def test_a_changed_support_file_is_never_shipped(monkeypatch, tmp_path) -> None:
    import app.creative3d.service as service_module

    copy = tmp_path / "drivers"
    import shutil

    shutil.copytree(DRIVERS, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "unity_support" / "Assets" / "PagentOS" / "Scripts" / "Spinner.cs").write_bytes(
        b"// tampered\n"
    )
    monkeypatch.setattr(service_module, "_DRIVERS_DIR", copy)
    with pytest.raises(DriverPinMismatch):
        unity_support_files()


def test_the_plan_fixture_the_device_lab_runs_is_the_canonical_plan() -> None:
    assert PLAN_FIXTURE.read_bytes() == ScenePlan.model_validate(UNITY_PLAN).plan_json().encode(
        "utf-8"
    )


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in (SceneRow.__table__, ObjectFocusRow.__table__, ActivityEventRow.__table__):
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def test_a_unity_scaffold_carries_the_manifest_and_the_catalogue_and_a_blender_one_does_not(
    db, tmp_path
) -> None:
    fake = FakeCreative3DDevice(unity_available=True, render_dir=tmp_path)
    device = FakeDeviceAction(results=fake.capability_results())
    service = SceneService(object_store=InMemoryObjectStore())

    service.create(db, device, plan=UNITY_PLAN, session_id="s")
    unity_paths = {f["path"] for f in device.payload_for("project.scaffold")["files"]}
    assert {
        "Packages/manifest.json",
        "Assets/PagentOS/Scripts/Spinner.cs",
        "Assets/PagentOS/Scripts/Bouncer.cs",
        "Assets/PagentOS/Scripts/ColorCycler.cs",
        "Assets/PagentOS/Editor/SceneDriver.cs",
    } <= unity_paths

    device.calls.clear()
    blender_plan = {
        **UNITY_PLAN,
        "tool": "blender",
        "scene": "blender-demo",
        "operations": [{"op": "create_scene"}],
    }
    service.create(db, device, plan=blender_plan, session_id="s")
    blender_paths = {f["path"] for f in device.payload_for("project.scaffold")["files"]}
    assert "Packages/manifest.json" not in blender_paths


# --------------------------------------------------------------- req 532 / 533


#: Unity with a build and a behaviour test and nothing a fake editor cannot honestly answer
#: (no render: the fake editor draws nothing).
BUILD_PLAN: dict[str, Any] = {
    "tool": "unity",
    "project": "lab",
    "scene": "build-demo",
    "operations": [
        {"op": "create_scene"},
        {"op": "add_primitive", "kind": "cube", "name": "Kutu"},
        {"op": "attach_script", "name": "Kutu", "script_id": "Spinner"},
        {"op": "run_tests", "frames": 10},
        {"op": "build_player"},
        {"op": "inspect"},
    ],
}


@pytest.mark.parametrize("op", [{"op": "build_player"}, {"op": "run_tests"}])
def test_build_and_test_operations_are_unity_only(op) -> None:
    with pytest.raises(ValueError, match="only valid when tool='unity'"):
        ScenePlan.model_validate(
            {**BUILD_PLAN, "tool": "blender", "operations": [{"op": "create_scene"}, op]}
        )


@pytest.mark.parametrize("frames", [0, 601])
def test_run_tests_frames_are_bounded(frames) -> None:
    with pytest.raises(ValueError):
        ScenePlan.model_validate(
            {**BUILD_PLAN, "operations": [{"op": "run_tests", "frames": frames}]}
        )


def _plan(*ops: dict[str, Any]) -> ScenePlan:
    return ScenePlan.model_validate({**BUILD_PLAN, "operations": list(ops)})


BUILT = {"result": "Succeeded", "path": "Build/build-demo.exe", "sha256": "a" * 64}


def test_a_build_counts_only_when_the_device_read_the_same_image_back() -> None:
    plan = _plan({"op": "build_player"})
    good = compare(plan, {"build": BUILT}, device_build={"verified": True, "sha256": "a" * 64})
    assert good.ok and good.checked == 1

    for inspection, device, field in [
        ({"build": {**BUILT, "result": "Failed"}}, {"verified": True, "sha256": "a" * 64}, "build"),
        ({"build": {**BUILT, "sha256": None}}, {"verified": True, "sha256": "a" * 64}, "build"),
        ({}, None, "build"),
        ({"build": BUILT}, None, "build"),
        ({"build": BUILT}, {"verified": False, "sha256": "a" * 64}, "build"),
        ({"build": BUILT}, {"verified": True, "sha256": "b" * 64}, "build.sha256"),
    ]:
        result = compare(plan, inspection, device_build=device)
        assert not result.ok, (inspection, device)
        assert result.mismatches[0].field == field


def test_a_behaviour_test_counts_only_when_every_script_acted() -> None:
    plan = _plan({"op": "run_tests"})
    passed = {"object": "Kutu", "script": "Spinner", "passed": True, "detail": "turned 15"}
    assert compare(plan, {"tests": [passed]}).ok

    failed = {**passed, "object": "Top", "passed": False, "detail": "turned 0"}
    result = compare(plan, {"tests": [passed, failed]})
    assert not result.ok
    assert result.mismatches[0].object_name == "Top"
    assert result.mismatches[0].field == "tests.Spinner"

    for empty in ({"tests": []}, {"tests": None}, {}):
        assert not compare(plan, empty).ok, empty


def test_a_plan_without_build_or_tests_is_not_judged_on_them() -> None:
    plan = _plan({"op": "create_scene"}, {"op": "add_primitive", "kind": "cube", "name": "Kutu"})
    inspection = {
        "objects": [
            {
                "name": "Kutu",
                "type": "MESH",
                "location": [0, 0, 0],
                "rotation": [0, 0, 0],
                "scale": [1, 1, 1],
            }
        ],
        "build": {"result": "Failed"},
        "tests": [],
    }
    assert compare(plan, inspection).ok


def _run_unity(db, tmp_path, **fake_kwargs):
    fake = FakeCreative3DDevice(unity_available=True, render_dir=tmp_path, **fake_kwargs)
    device = FakeDeviceAction(
        results={**fake.capability_results(), "file.inspect": fake.file_inspect}
    )
    service = SceneService(object_store=InMemoryObjectStore())
    receipt = service.create(db, device, plan=BUILD_PLAN, session_id="s")
    return receipt, device


def test_the_service_verifies_a_unity_build_through_the_device(db, tmp_path) -> None:
    receipt, device = _run_unity(db, tmp_path)
    assert receipt["compare"]["ok"], receipt["compare"]
    assert receipt["terminal_status"] == "verified"
    inspect_calls = [c for c in device.calls if c["capability"] == "file.inspect"]
    assert len(inspect_calls) == 1
    assert inspect_calls[0]["payload"]["path"].endswith(r"\Build\build-demo.exe")


def test_an_exe_that_is_not_a_windows_image_is_not_a_build(db, tmp_path) -> None:
    receipt, _ = _run_unity(db, tmp_path, unity_build=b"MZ" + b"\x00" * 200)
    assert not receipt["compare"]["ok"]
    assert receipt["terminal_status"] != "verified"
    assert receipt["compare"]["mismatches"][0]["field"] == "build"


def test_a_script_that_did_not_act_leaves_the_scene_unverified(db, tmp_path) -> None:
    receipt, _ = _run_unity(db, tmp_path, unity_tests_pass=False)
    assert not receipt["compare"]["ok"]
    assert receipt["compare"]["mismatches"][0]["field"] == "tests.Spinner"


@pytest.mark.parametrize("path", ["../escape.exe", "C:/Windows/notepad.exe", "Build/../../x.exe"])
def test_a_build_path_outside_the_project_is_never_read(path) -> None:
    device = FakeDeviceAction()
    row = SceneRow(root_path=r"C:\p\lab")
    result = SceneService._read_back_build(
        device, row, _plan({"op": "build_player"}), {"build": {**BUILT, "path": path}}
    )
    assert result == {"verified": False, "reason": "the build path is not inside the project"}
    assert device.calls == []


EVIDENCE = API_ROOT.parents[1] / "docs" / "evidence" / "b50-unity-production-2026-09-16.json"


def test_the_cloud_compare_accepts_what_the_licensed_editor_really_produced() -> None:
    """The device lab's measured run (SceneUnityProductionTests, PAGENTOS_B50_EVIDENCE_OUT):
    the inspection, render bytes and device build check of the REAL editor, judged by the
    Cloud Core's own compare against the same plan fixture - every constraint, the render's
    independent PIL read, the three script tests and the PE read-back."""
    import base64

    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["kind"] == "b50_unity_production"
    assert evidence["project_run_exit_code"] == 0
    plan = ScenePlan.model_validate_json(PLAN_FIXTURE.read_text(encoding="utf-8"))
    render_bytes = base64.b64decode(evidence["device_render_check"]["png_base64"])
    result = compare(
        plan,
        evidence["inspection"],
        render_bytes=render_bytes,
        device_build=evidence["device_build_check"],
    )
    assert result.ok, result.as_dict()
    assert evidence["inspection"]["build"]["sha256"] == evidence["device_build_check"]["sha256"]
    assert evidence["device_build_check"]["pe"]
    assert len(evidence["inspection"]["tests"]) == 3
    # The run used the bytes this repository ships.
    pins = json.loads((DRIVERS / "manifest.json").read_text(encoding="utf-8"))
    measured = evidence["scaffolded_file_sha256"]
    assert (
        measured.pop("Assets/PagentOS/Editor/SceneDriver.cs")
        == hashlib.sha256(
            (DRIVERS / "SceneDriver.cs").read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
    )
    assert measured == pins["unity_support"]["files"]


@pytest.mark.parametrize(
    ("rotation", "forward"),
    [
        ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        ((0.0, 90.0, 0.0), (1.0, 0.0, 0.0)),
        ((90.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
        ((0.0, 0.0, 70.0), (0.0, 0.0, 1.0)),
        ((0.0, 180.0, 0.0), (0.0, 0.0, -1.0)),
    ],
)
def test_a_unity_camera_looks_along_its_own_plus_z(rotation, forward) -> None:
    from app.creative3d.compare import unity_forward_vector

    assert unity_forward_vector(rotation) == pytest.approx(forward, abs=1e-9)


def test_a_unity_camera_is_judged_by_unity_s_axes_and_a_blender_one_by_blender_s() -> None:
    ops = (
        {"op": "add_primitive", "kind": "cube", "name": "Kutu"},
        {"op": "add_primitive", "kind": "camera", "name": "Kamera", "location": [0.0, 0.0, -8.0]},
        {"op": "set_camera", "name": "Kamera", "look_at": "Kutu"},
    )
    objects = [
        {
            "name": "Kutu",
            "type": "MESH",
            "location": [0, 0, 0],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        # Unity's LookAt from (0, 0, -8) toward the origin leaves the camera unrotated.
        {
            "name": "Kamera",
            "type": "CAMERA",
            "location": [0, 0, -8],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
        },
    ]
    unity = compare(_plan(*ops), {"objects": objects, "camera": "Kamera"})
    assert unity.ok, unity.as_dict()
    blender = compare(
        ScenePlan.model_validate({**BUILD_PLAN, "tool": "blender", "operations": list(ops)}),
        {"objects": objects, "camera": "Kamera"},
    )
    assert any(m.field == "camera_aim.angle_deg" for m in blender.mismatches)
