"""Shared fixtures for the M25 3D creation unit/tool/corpus suites.

Two independent fakes:

* :func:`build_fake_bpy` — a small, fixed subset of Blender's own ``bpy`` module,
  enough for ``app.creative3d.drivers.blender_driver`` to run every operation kind
  against it with no real Blender (``tests/unit/test_blender_driver.py``). It writes a
  REAL (tiny) PNG through Pillow when ``bpy.ops.render.render`` runs, so the driver's
  own sha256/byte-count logic is genuinely exercised rather than stubbed.
* :func:`creative3d_capability_results` — the fake device's ``project.*``/``scene.*``
  family (DEVICE_PROTOCOL.md §6l + the M25 additions ADR-0088 names): never a real
  Windows Job Object, never a real Blender/Unity process — echoes back the shapes the
  real companion answers with, the same discipline ``tests.appfactory_support`` already
  establishes for its own ``project.*`` family.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from app.routines.dispatch import DeviceRunResult

# ============================================================================ bpy


class FakeMaterial:
    def __init__(self, name: str) -> None:
        self.name = name
        self.diffuse_color: tuple[float, float, float, float] = (0.8, 0.8, 0.8, 1.0)
        self.metallic = 0.0
        self.roughness = 0.5


class _FakeMaterialsCollection:
    def __init__(self) -> None:
        self._all: dict[str, FakeMaterial] = {}

    def new(self, name: str) -> FakeMaterial:
        mat = FakeMaterial(name)
        self._all[name] = mat
        return mat


class _FakeMeshMaterials(list):
    """``obj.data.materials`` — a plain list with Blender's own ``.append`` semantics
    (subclassing list already gives us that, ``bool()``, indexing and iteration)."""


class FakeLightData:
    def __init__(self) -> None:
        self.energy = 0.0


class FakeMeshData:
    def __init__(self) -> None:
        self.materials: _FakeMeshMaterials = _FakeMeshMaterials()


class FakeObject:
    def __init__(self, name: str, obj_type: str) -> None:
        self.name = name
        self.type = obj_type
        self.location: list[float] = [0.0, 0.0, 0.0]
        self.rotation_euler: list[float] = [0.0, 0.0, 0.0]
        self.scale: list[float] = [1.0, 1.0, 1.0]
        if obj_type == "LIGHT":
            self.data: Any = FakeLightData()
        elif obj_type == "CAMERA":
            self.data = None
        else:
            self.data = FakeMeshData()


class _FakeObjectsCollection:
    """Looks objects up by their CURRENT ``.name`` at access time (never a dict keyed
    at insertion time) — the driver creates an object with a temporary placeholder
    name and renames it immediately after (``obj.name = name``), the same way real
    Blender's own auto-numbered "Cube.001" gets overwritten; a dict keyed at
    insertion time would silently never find it again by its real name."""

    def __init__(self) -> None:
        self._all: list[FakeObject] = []

    def __iter__(self):
        return iter(list(self._all))

    def __len__(self) -> int:
        return len(self._all)

    def get(self, name: str | None) -> FakeObject | None:
        if not name:
            return None
        for obj in self._all:
            if obj.name == name:
                return obj
        return None

    def _add(self, obj: FakeObject) -> None:
        self._all.append(obj)

    def remove(self, obj: FakeObject, do_unlink: bool = True) -> None:
        self._all.remove(obj)


class _FakeImageSettings:
    file_format = "PNG"


class _FakeRenderSettings:
    def __init__(self) -> None:
        self.engine = "BLENDER_WORKBENCH"
        self.resolution_x = 320
        self.resolution_y = 240
        self.resolution_percentage = 100
        self.filepath = ""
        self.image_settings = _FakeImageSettings()


class _FakeScene:
    def __init__(self) -> None:
        self.camera: FakeObject | None = None
        self.render = _FakeRenderSettings()


class _FakeContext:
    def __init__(self, scene: _FakeScene) -> None:
        self.active_object: FakeObject | None = None
        self.scene = scene


class _FakeOpsMesh:
    def __init__(self, data: "_FakeData", context: _FakeContext) -> None:
        self._data = data
        self._context = context

    def _add(self, kind: str, location: tuple[float, float, float]) -> None:
        # A deterministic, never-colliding placeholder name — the driver renames it
        # (``obj.name = name``) immediately after, the same way real Blender's own
        # auto-numbered "Cube.001" names get overwritten.
        obj = FakeObject(f"__tmp_{len(self._data.objects._all)}", "MESH")
        obj.location = list(location)
        self._data.objects._add(obj)
        self._context.active_object = obj

    def primitive_cube_add(self, location=(0.0, 0.0, 0.0), **_: Any) -> None:
        self._add("cube", location)

    def primitive_uv_sphere_add(self, location=(0.0, 0.0, 0.0), **_: Any) -> None:
        self._add("sphere", location)

    def primitive_cylinder_add(self, location=(0.0, 0.0, 0.0), **_: Any) -> None:
        self._add("cylinder", location)

    def primitive_plane_add(self, location=(0.0, 0.0, 0.0), **_: Any) -> None:
        self._add("plane", location)


class _FakeOpsObject:
    def __init__(self, data: "_FakeData", context: _FakeContext) -> None:
        self._data = data
        self._context = context

    def camera_add(self, location=(0.0, 0.0, 0.0), **_: Any) -> None:
        obj = FakeObject(f"__tmp_{len(self._data.objects._all)}", "CAMERA")
        obj.location = list(location)
        self._data.objects._add(obj)
        self._context.active_object = obj

    def light_add(self, location=(0.0, 0.0, 0.0), **_: Any) -> None:
        obj = FakeObject(f"__tmp_{len(self._data.objects._all)}", "LIGHT")
        obj.location = list(location)
        self._data.objects._add(obj)
        self._context.active_object = obj


class _FakeOpsRender:
    def __init__(self, context: _FakeContext) -> None:
        self._context = context

    def render(self, write_still: bool = True) -> None:
        import random

        from PIL import Image

        render = self._context.scene.render
        # A genuinely NON-uniform, effectively-incompressible tiny image (seeded
        # per-pixel noise, never a smooth gradient a PNG encoder could shrink under
        # the "real render" byte-count floor), so a test that feeds this straight
        # into app.creative3d.compare.check_render sees a real pass, not a rigged
        # one — the same "prove it against something real" discipline the module
        # docstring states.
        width, height = max(1, render.resolution_x), max(1, render.resolution_y)
        rng = random.Random(1234)
        img = Image.new("RGB", (width, height))
        pixels = img.load()
        for x in range(width):
            for y in range(height):
                pixels[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        Path(render.filepath).parent.mkdir(parents=True, exist_ok=True)
        img.save(render.filepath, "PNG")


class _FakeOpsWm:
    def save_as_mainfile(self, filepath: str = "") -> None:
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(filepath).write_bytes(b"FAKE-BLEND-FILE")


class _FakeOps:
    def __init__(self, data: "_FakeData", context: _FakeContext) -> None:
        self.mesh = _FakeOpsMesh(data, context)
        self.object = _FakeOpsObject(data, context)
        self.render = _FakeOpsRender(context)
        self.wm = _FakeOpsWm()


class _FakeData:
    def __init__(self) -> None:
        self.objects = _FakeObjectsCollection()
        self.materials = _FakeMaterialsCollection()


class _FakeApp:
    version = (4, 5, 4)


def build_fake_bpy() -> ModuleType:
    """A fresh fake ``bpy`` module — new object graph every call, so tests never leak
    state into each other through a shared fake."""
    module = ModuleType("bpy")
    data = _FakeData()
    scene = _FakeScene()
    context = _FakeContext(scene)
    module.data = data  # type: ignore[attr-defined]
    module.context = context  # type: ignore[attr-defined]
    module.ops = _FakeOps(data, context)  # type: ignore[attr-defined]
    module.app = _FakeApp()  # type: ignore[attr-defined]
    return module


def install_fake_bpy() -> ModuleType:
    """Installs a fresh fake ``bpy`` into ``sys.modules`` and (re)imports the driver
    module bound to it — the ``import bpy`` statement is only re-executed on
    ``reload``, so a test that wants isolated state must go through this helper
    rather than importing the driver directly."""
    fake = build_fake_bpy()
    sys.modules["bpy"] = fake
    module_name = "app.creative3d.drivers.blender_driver"
    if module_name in sys.modules:
        driver = importlib.reload(sys.modules[module_name])
    else:
        driver = importlib.import_module(module_name)
    return driver


# ======================================================================= device


_BASE_ROOT = "C:/Users/owner/Documents/PagentOS Projects/3d"


def scene_scaffold_ok(payload: dict[str, Any]) -> DeviceRunResult:
    slug = str(payload.get("slug") or "scene-fixture")
    root_path = f"{_BASE_ROOT}/{slug}"
    return DeviceRunResult(True, result={"root_path": root_path, "files_written": 3})


def scene_run_ok(payload: dict[str, Any]) -> DeviceRunResult:
    plan = payload.get("plan") or {}
    operations = plan.get("operations") or []
    objects = []
    camera = None
    lights = []
    for op in operations:
        if op.get("op") == "add_primitive":
            objects.append(
                {
                    "name": op["name"],
                    "type": "CAMERA" if op["kind"] == "camera" else (
                        "LIGHT" if op["kind"].startswith("light_") else "MESH"
                    ),
                    "location": list(op.get("location", [0.0, 0.0, 0.0])),
                    "rotation": list(op.get("rotation", [0.0, 0.0, 0.0])),
                    "scale": list(op.get("scale", [1.0, 1.0, 1.0])),
                }
            )
            if op["kind"] == "camera":
                camera = op["name"]
            if op["kind"].startswith("light_"):
                lights.append({"name": op["name"], "energy": 10.0})
    inspection = {"objects": objects, "camera": camera, "lights": lights, "render": None, "errors": []}
    return DeviceRunResult(
        True,
        result={
            "project_id": str(payload.get("project_id") or ""),
            "state": "applied",
            "inspection": inspection,
        },
    )


def scene_inspect_ok(_payload: dict[str, Any]) -> DeviceRunResult:
    digest = hashlib.sha256(b"fake-render").hexdigest()
    return DeviceRunResult(
        True,
        result={
            "inspection": {
                "objects": [
                    {
                        "name": "Kure",
                        "type": "MESH",
                        "location": [0.0, 0.0, 0.0],
                        "rotation": [0.0, 0.0, 0.0],
                        "scale": [1.0, 1.0, 1.0],
                        "material_color": [1.0, 0.0, 0.0, 1.0],
                    }
                ],
                "camera": None,
                "lights": [],
                "render": {
                    "path": "render.png",
                    "sha256": digest,
                    "bytes": 4096,
                    "width": 320,
                    "height": 240,
                    "engine": "workbench",
                },
                "errors": [],
            },
            "render_png_base64": None,
        },
    )


def scene_unity_no_license(_payload: dict[str, Any]) -> DeviceRunResult:
    """The honest Unity refusal (spec §1, §6, ADR-0088 decision 5): the licensing
    client's own words, never a crash, never "done"."""
    return DeviceRunResult(
        False,
        "dependency_unavailable",
        "No valid Unity Editor license found. Please activate your license.",
    )


def creative3d_capability_results() -> dict[str, Any]:
    """``{capability: DeviceRunResult | callable}`` for ``tests.alarms_support.
    FakeDeviceAction`` (the same shape ``tests.appfactory_support.
    appfactory_capability_results`` returns)."""
    return {
        "project.scaffold": scene_scaffold_ok,
        "project.run": scene_run_ok,
        "scene.inspect": scene_inspect_ok,
    }


__all__ = [
    "FakeObject",
    "build_fake_bpy",
    "creative3d_capability_results",
    "install_fake_bpy",
    "scene_inspect_ok",
    "scene_run_ok",
    "scene_scaffold_ok",
    "scene_unity_no_license",
]
