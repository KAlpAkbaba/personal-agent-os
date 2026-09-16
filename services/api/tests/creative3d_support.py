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

import base64
import importlib
import sys
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
        self.color = [1.0, 1.0, 1.0]


class FakeCameraData:
    """B44: a camera carries data too - its focal length (Blender's default is 50 mm)."""

    def __init__(self) -> None:
        self.lens = 50.0


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
            self.data = FakeCameraData()
        else:
            self.data = FakeMeshData()
        self.animation_data: _FakeAnimationData | None = None

    def keyframe_insert(self, data_path: str, frame: int = 0, **_: Any) -> None:
        """Blender's own: the property's CURRENT value recorded at ``frame``, one F-curve
        per component."""
        if self.animation_data is None:
            self.animation_data = _FakeAnimationData()
        for index, value in enumerate(list(getattr(self, data_path))):
            self.animation_data.action.curve(data_path, index).insert(float(frame), float(value))


class _FakeKeyframePoint:
    def __init__(self, frame: float, value: float) -> None:
        self.co = (frame, value)


class _FakeFCurve:
    def __init__(self, data_path: str, array_index: int) -> None:
        self.data_path = data_path
        self.array_index = array_index
        self.keyframe_points: list[_FakeKeyframePoint] = []

    def insert(self, frame: float, value: float) -> None:
        self.keyframe_points = [p for p in self.keyframe_points if p.co[0] != frame]
        self.keyframe_points.append(_FakeKeyframePoint(frame, value))
        self.keyframe_points.sort(key=lambda p: p.co[0])

    def evaluate(self, frame: float) -> float:
        points = self.keyframe_points
        if not points:
            return 0.0
        if frame <= points[0].co[0]:
            return points[0].co[1]
        for left, right in zip(points, points[1:], strict=False):
            if left.co[0] <= frame <= right.co[0]:
                span = right.co[0] - left.co[0]
                t = 0.0 if span == 0 else (frame - left.co[0]) / span
                return left.co[1] + t * (right.co[1] - left.co[1])
        return points[-1].co[1]


class _FakeAction:
    """A LAYERED action, the Blender 4.4+ shape, with no legacy ``fcurves`` at all - so the
    unit suite runs the driver's layered read-back path (the real lab runs the other)."""

    def __init__(self) -> None:
        self._curves: dict[tuple[str, int], _FakeFCurve] = {}
        self.layers = [_FakeLayer(self)]

    def curve(self, data_path: str, index: int) -> _FakeFCurve:
        key = (data_path, index)
        if key not in self._curves:
            self._curves[key] = _FakeFCurve(data_path, index)
        return self._curves[key]


class _FakeChannelbag:
    def __init__(self, action: _FakeAction) -> None:
        self._action = action

    @property
    def fcurves(self) -> list[_FakeFCurve]:
        return list(self._action._curves.values())


class _FakeStrip:
    def __init__(self, action: _FakeAction) -> None:
        self.channelbags = [_FakeChannelbag(action)]


class _FakeLayer:
    def __init__(self, action: _FakeAction) -> None:
        self.strips = [_FakeStrip(action)]


class _FakeAnimationData:
    def __init__(self) -> None:
        self.action = _FakeAction()


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
        self.fps = 24


class _FakeScene:
    def __init__(self) -> None:
        self.camera: FakeObject | None = None
        self.render = _FakeRenderSettings()
        self.frame_start = 1
        self.frame_end = 250

    def frame_set(self, frame: int) -> None:
        """Blender's own: every animated property evaluated at ``frame``."""
        for obj in getattr(self, "_objects", None) or []:
            animation = getattr(obj, "animation_data", None)
            if animation is None:
                continue
            for (data_path, index), curve in animation.action._curves.items():
                values = list(getattr(obj, data_path))
                values[index] = curve.evaluate(float(frame))
                setattr(obj, data_path, values)


class _FakeContext:
    def __init__(self, scene: _FakeScene) -> None:
        self.active_object: FakeObject | None = None
        self.scene = scene


class _FakeOpsMesh:
    def __init__(self, data: _FakeData, context: _FakeContext) -> None:
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
    def __init__(self, data: _FakeData, context: _FakeContext) -> None:
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


class _FakeOpsExportScene:
    """Blender's bundled exporters, faked to write files with the REAL format signatures (a
    GLB header whose declared length is the file's; an FBX binary magic) - so the fake
    device's signature check runs against bytes shaped like the real thing."""

    def gltf(self, filepath: str = "", export_format: str = "GLB", **_: Any) -> None:
        import json as _json
        import struct

        body = _json.dumps({"asset": {"version": "2.0", "generator": "fake bpy"}}).encode("utf-8")
        body += b" " * ((4 - len(body) % 4) % 4)
        total = 12 + 8 + len(body)
        data = (
            b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I", len(body)) + b"JSON" + body
        )
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(filepath).write_bytes(data)

    def fbx(self, filepath: str = "", **_: Any) -> None:
        data = b"Kaydara FBX Binary  \x00\x1a\x00" + (7400).to_bytes(4, "little") + b"\x00" * 160
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(filepath).write_bytes(data)


class _FakeOps:
    def __init__(self, data: _FakeData, context: _FakeContext) -> None:
        self.mesh = _FakeOpsMesh(data, context)
        self.object = _FakeOpsObject(data, context)
        self.render = _FakeOpsRender(context)
        self.wm = _FakeOpsWm()
        self.export_scene = _FakeOpsExportScene()


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
    scene._objects = data.objects  # type: ignore[attr-defined]
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

#: The licensing client's own words (docs/M25_CREATIVE_3D_SPEC.md §1's own measured
#: evidence, docs/evidence/m25-tool-detection-2026-09-08.json) — reused verbatim so a
#: test proves the receipt carries the REAL refusal text, never a made-up one.
UNITY_LICENSE_MESSAGE = "No valid Unity Editor license found. Please activate your license."

#: The 3D root the scaffold must ask for; the editors are refused anywhere else
#: (DEVICE_PROTOCOL.md §6m). Spelled here rather than imported so the fake states the
#: expectation itself rather than agreeing with whatever the service happens to send.
PROJECT_ROOT_3D_NAME = "3d"


def _device_file_check(
    folder: Path, declared: dict[str, Any], what: str
) -> tuple[str, bytes, str] | DeviceRunResult:
    """SceneInspection.ReadRender / ReadExports, restated for the fake: a relative path
    inside the project (an absolute or escaping one is permission_denied), present, and
    hashing to the sha256 the driver declared."""
    import hashlib

    relative = declared.get("path")
    declared_sha = declared.get("sha256")
    if not isinstance(relative, str) or not relative:
        return DeviceRunResult(False, "postcondition_failed", f"{what}_undeclared")
    if not isinstance(declared_sha, str) or len(declared_sha) != 64:
        return DeviceRunResult(False, "postcondition_failed", f"{what}_undeclared")
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or ":" in relative
        or relative.startswith(("/", "\\"))
        or ".." in candidate.parts
    ):
        return DeviceRunResult(
            False, "permission_denied", f"the {what} path is not relative inside the project"
        )
    path = folder / relative
    if not path.exists():
        return DeviceRunResult(False, "postcondition_failed", f"{what}_missing")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != declared_sha.lower():
        return DeviceRunResult(False, "postcondition_failed", f"{what}_sha256_mismatch")
    return relative.replace("\\", "/"), content, digest


def device_signature_ok(fmt: str, content: bytes) -> bool:
    """SceneInspection.SignatureOk: a GLB header (glTF, version 2, the file's length); an FBX
    binary magic."""
    import struct

    if fmt == "glb":
        return (
            len(content) >= 12
            and content[:4] == b"glTF"
            and struct.unpack("<I", content[4:8])[0] == 2
            and struct.unpack("<I", content[8:12])[0] == len(content)
        )
    if fmt == "fbx":
        return content[:21] == b"Kaydara FBX Binary  \x00"
    return False


class FakeCreative3DDevice:
    """A stateful fake of the ``project.scaffold`` / ``project.run`` / ``scene.inspect``
    trio ``app.creative3d.service.SceneService`` calls (DEVICE_PROTOCOL.md §6l + the
    M25 additions, ADR-0088) — never a real Windows Job Object, never a real Blender/
    Unity process.

    For the Blender tool, ``run()`` re-uses the REAL, already-tested
    ``app.creative3d.drivers.blender_driver.apply_operations`` against a fake ``bpy``
    module kept PERSISTENT per ``project_id`` — the same object graph survives across
    calls exactly the way a real ``.blend`` file persists on disk between successive
    ``blender.exe -b`` invocations, so an ``apply()`` after a ``create()`` genuinely
    builds on the prior state rather than a hand-authored canned response. This is
    real driver logic under test, not a second, parallel simulation of it.

    ``unity_available`` toggles the honest ``dependency_unavailable`` refusal (spec
    §1, §6) — the licensing client's own words, never a crash, never "done".
    """

    def __init__(self, *, unity_available: bool = False, render_dir: Path | None = None) -> None:
        self.unity_available = unity_available
        self._render_dir = render_dir
        self._plans: dict[str, dict[str, Any]] = {}
        self._bpy_by_project: dict[str, ModuleType] = {}
        self._inspections: dict[str, dict[str, Any]] = {}
        self._unity_state: dict[str, dict[str, Any]] = {}

    # -------------------------------------------------------------- project.scaffold

    def scaffold(self, payload: dict[str, Any]) -> DeviceRunResult:
        """``project.scaffold`` is the SAME capability name the App Factory's own
        ``project.scaffold`` already uses (module docstring: one desktop authority,
        never a second path) — a real device tells the two apart by the payload's
        own shape, never by capability name, so this fake does too: a ``plan.json``
        file names a 3D scene; anything else falls through to
        ``tests.appfactory_support``'s own fake, so ONE fake device answers both
        families in the corpus harness exactly the way ONE real companion would."""
        files = payload.get("files") or []
        if not any(f.get("path") == "plan.json" for f in files):
            from tests.appfactory_support import project_scaffold_ok

            return project_scaffold_ok(payload)

        project_id = str(payload["project_id"])
        slug = str(payload.get("slug") or "scene-fixture")
        plan_text = next(f["text"] for f in files if f.get("path") == "plan.json")
        import json

        plan = json.loads(plan_text)

        # The real device refuses here, before a byte is written, when the manifest's run
        # command is not one it admits token for token — and this fake used to accept
        # anything, which is how a service sending the bare word "blender" survived a full
        # green suite (the M25 security review, 2026-09-08). The command must be one of the
        # two `run_command()` builds for this tool: the creation form and the apply form.
        from app.creative3d.service import RUN_COMMAND_KEY, run_command

        tool = str(plan.get("tool") or "blender")
        manifest = payload.get("manifest") or {}
        if str(payload.get("root") or "") != PROJECT_ROOT_3D_NAME:
            return DeviceRunResult(
                False, "permission_denied", "the 3D runtimes run only under the 3D root"
            )
        command = (manifest.get("run") or {}).get(RUN_COMMAND_KEY[tool])
        admitted = {
            run_command(tool, existing_scene=False),
            run_command(tool, existing_scene=True),
        }
        if command not in admitted:
            return DeviceRunResult(
                False,
                "permission_denied",
                f"manifest command {command!r} is not in the runtime allowlist; nothing was run",
            )

        self._plans[project_id] = plan
        return DeviceRunResult(
            True, result={"root_path": f"{_BASE_ROOT}/{slug}", "files_written": len(files)}
        )

    # ------------------------------------------------------------------ project.run

    def run(self, payload: dict[str, Any]) -> DeviceRunResult:
        project_id = str(payload["project_id"])
        if project_id not in self._plans:
            from tests.appfactory_support import project_run_ok

            return project_run_ok(payload)

        plan = self._plans.get(project_id) or {"operations": [], "tool": "blender"}
        tool = plan.get("tool", "blender")

        if tool == "unity":
            if not self.unity_available:
                return DeviceRunResult(False, "dependency_unavailable", UNITY_LICENSE_MESSAGE)
            state = self._unity_state.setdefault(
                project_id, {"objects": {}, "camera": None, "lights": {}}
            )
            _apply_plan_to_unity_state(state, plan)
            self._inspections[project_id] = _unity_state_to_inspection(state)
            return DeviceRunResult(True, result={})

        fake_bpy = self._bpy_by_project.setdefault(project_id, build_fake_bpy())
        sys.modules["bpy"] = fake_bpy
        module_name = "app.creative3d.drivers.blender_driver"
        driver = (
            importlib.reload(sys.modules[module_name])
            if module_name in sys.modules
            else importlib.import_module(module_name)
        )
        out_dir = self._render_dir or Path.cwd()
        inspection = driver.apply_operations(plan, str(out_dir))
        self._inspections[project_id] = inspection
        return DeviceRunResult(True, result={})

    # --------------------------------------------------------------- scene.inspect

    def inspect(self, payload: dict[str, Any]) -> DeviceRunResult:
        """The DEVICE's answer (ProjectCapabilities.Inspect / SceneInspection,
        DEVICE_PROTOCOL.md 6m): the render and every export are paths RELATIVE to the project
        folder, re-hashed against what the driver declared, each export's format signature
        read - and an absolute path is refused, as the device refuses it. B44: this fake used
        to hand back whatever path the driver wrote and a top-level ``render_png_base64`` the
        device never sends, which is how neither defect could be seen from here."""
        project_id = str(payload["project_id"])
        inspection = self._inspections.get(project_id)
        if inspection is None:
            return DeviceRunResult(False, "postcondition_failed", "inspection_missing")
        folder = Path(self._render_dir or Path.cwd())
        plan = self._plans.get(project_id, {})
        result: dict[str, Any] = {
            "project_id": project_id,
            "slug": f"{plan.get('project', 'scene')}-{plan.get('scene', 'scene')}",
            "root_path": str(folder),
            "inspection": inspection,
            "inspection_path": str(folder / "out.json"),
            "render": None,
            "exports": [],
        }
        render = inspection.get("render")
        if isinstance(render, dict):
            checked = _device_file_check(folder, render, "render")
            if isinstance(checked, DeviceRunResult):
                return checked
            relative, content, digest = checked
            result["render"] = {
                "path": relative,
                "bytes": len(content),
                "sha256": digest,
                "verified": True,
                "png_base64": base64.b64encode(content).decode("ascii"),
            }
        for declared in inspection.get("exports") or []:
            checked = _device_file_check(folder, declared, "export")
            if isinstance(checked, DeviceRunResult):
                return checked
            relative, content, digest = checked
            if not device_signature_ok(str(declared.get("format")), content):
                return DeviceRunResult(False, "postcondition_failed", "export_signature_mismatch")
            result["exports"].append(
                {
                    "format": declared.get("format"),
                    "path": relative,
                    "bytes": len(content),
                    "sha256": digest,
                    "verified": True,
                }
            )
        return DeviceRunResult(True, result=result)

    def capability_results(self) -> dict[str, Any]:
        """``{capability: callable}`` for ``tests.alarms_support.FakeDeviceAction``
        (the same shape ``tests.appfactory_support.appfactory_capability_results``
        returns)."""
        return {
            "project.scaffold": self.scaffold,
            "project.run": self.run,
            "scene.inspect": self.inspect,
        }


def _apply_plan_to_unity_state(state: dict[str, Any], plan: dict[str, Any]) -> None:
    """A minimal, honest simulation of what ``SceneDriver.cs`` would do — used only
    because Unity itself cannot run in this environment (spec §1); never claimed as
    a stand-in for the real Editor API the C# file actually calls."""
    for op in plan.get("operations") or []:
        kind = op.get("op")
        if kind == "create_scene":
            state["objects"].clear()
            state["camera"] = None
            state["lights"].clear()
        elif kind == "add_primitive":
            name = op["name"]
            obj_kind = op["kind"]
            obj_type = (
                "CAMERA"
                if obj_kind == "camera"
                else ("LIGHT" if obj_kind.startswith("light_") else "MESH")
            )
            state["objects"][name] = {
                "name": name,
                "type": obj_type,
                "location": list(op.get("location", [0.0, 0.0, 0.0])),
                "rotation": list(op.get("rotation", [0.0, 0.0, 0.0])),
                "scale": list(op.get("scale", [1.0, 1.0, 1.0])),
            }
            if obj_type == "CAMERA" and state["camera"] is None:
                state["camera"] = name
            if obj_type == "LIGHT":
                state["lights"][name] = 10.0
        elif kind == "transform":
            obj = state["objects"].get(op["name"])
            if obj:
                for field in ("location", "rotation", "scale"):
                    if op.get(field) is not None:
                        obj[field] = list(op[field])
        elif kind == "set_material":
            obj = state["objects"].get(op["name"])
            if obj:
                obj["material_color"] = list(op["color"])
        elif kind == "set_light":
            if op["name"] in state["lights"]:
                state["lights"][op["name"]] = float(op["energy"])


def _unity_state_to_inspection(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "objects": list(state["objects"].values()),
        "camera": state["camera"],
        "lights": [{"name": n, "energy": e} for n, e in state["lights"].items()],
        "render": None,
        "errors": [],
    }


def creative3d_capability_results(
    *, unity_available: bool = False, render_dir: Path | None = None
) -> dict[str, Any]:
    """Convenience wrapper for the common case: a fresh :class:`FakeCreative3DDevice`.
    Prefer constructing :class:`FakeCreative3DDevice` directly when a test needs to
    inspect its state afterward."""
    return FakeCreative3DDevice(
        unity_available=unity_available, render_dir=render_dir
    ).capability_results()


__all__ = [
    "UNITY_LICENSE_MESSAGE",
    "FakeCreative3DDevice",
    "FakeObject",
    "build_fake_bpy",
    "creative3d_capability_results",
    "install_fake_bpy",
]
