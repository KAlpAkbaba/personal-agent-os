"""``ScenePlan``: the structured, validated input to 3D scene creation
(docs/M25_CREATIVE_3D_SPEC.md §2, ADR-0088 decision 1).

The plan is data, never code (the M23 review's lesson, "free text never lands in
code", applied before the first line of a driver is written): a closed operation
vocabulary, closed-alphabet names for every object/project/scene, bounded numbers, a
FIXED script catalogue for ``attach_script`` (Unity only — a script id names one of the
repository's own shipped ``.cs`` files, never model-authored C#), and a bounded ``label``
that is the ONLY free-text-shaped field anywhere in the schema — and even it is never
spliced into generated code, only carried through to a receipt or a ledger row.

``plan_json()`` is the canonical serialisation: sorted keys, no incidental whitespace —
the exact bytes a driver reads (``blender_driver.py`` / ``SceneDriver.cs``) and the exact
bytes any evidence hash is taken over, so the same plan always serialises identically.

Bounds are sanity bounds against a runaway request, the same posture
``app.appfactory.spec``/``app.artifacts.spec`` already state for their own numbers —
never a promise that a real scene is ever this large.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- vocabulary

TOOL_BLENDER = "blender"
TOOL_UNITY = "unity"
TOOLS: tuple[str, ...] = (TOOL_BLENDER, TOOL_UNITY)

PRIMITIVE_KINDS: tuple[str, ...] = (
    "cube",
    "sphere",
    "cylinder",
    "plane",
    "light_sun",
    "light_point",
    "camera",
)
RENDER_ENGINES: tuple[str, ...] = ("workbench", "eevee")

#: The fixed catalogue of shipped C# scripts ``attach_script`` may name (Unity only,
#: spec §2/§7): never a script id the model or the owner invents. Each is a fixed
#: ``Assets/PagentOS/Scripts/<id>.cs`` file the fixture project ships, committed data,
#: never generated from a plan or from prose.
SCRIPT_CATALOGUE: tuple[str, ...] = ("Spinner", "Bouncer", "ColorCycler")

MAX_OPERATIONS = 64
MAX_LABEL_CHARS = 200

#: A project/scene slug — the same shape ``app.appfactory.spec.slug_for_name`` produces
#: for its own device-root child directory: lowercase ASCII, dashes, bounded. A path
#: (a drive letter, a separator, "..") never matches this pattern, so the owner's real
#: project (``E:\hologram\HologramVehicleTest``) is refused here, before any device is
#: ever asked, the same way ``AppSpec._name_shape`` refuses a path-shaped app name.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: An object/light/camera name: a plain ASCII identifier, closed-alphabet by construction
#: — never punctuation, never a path, never anything an owner's free words could smuggle
#: through. The assistant names objects from its own fixed vocabulary (spec §5's receipts:
#: "Kure", "Kup", "Kamera"), never from unvalidated owner prose.
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

#: Every character that can end a line or a statement somewhere a value might be read by
#: a shell, a log line or a JS-shaped surface — the same control-character refusal
#: ``app.appfactory.spec._CONTROL_RE`` applies to its own free-text fields, restated here
#: for ``label`` (the ONE field in this schema that carries prose at all).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _no_control_characters(value: str, field: str) -> str:
    hit = _CONTROL_RE.search(value)
    if hit is not None:
        raise ValueError(f"{field} must not contain the control character U+{ord(hit.group()):04X}")
    return value


def _bounded(value: float, lo: float, hi: float, field: str) -> float:
    if not (lo <= value <= hi):
        raise ValueError(f"{field} must be within [{lo}, {hi}], got {value}")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Vec3 = tuple[float, float, float]
Color = tuple[float, float, float, float]

#: Location/rotation bound: generous but sane for a fixture scene (a runaway-response
#: guard, module docstring). Rotation is Euler degrees (spec §2 is silent on unit; degrees
#: is what an owner/receipt would ever say — "45 derece döndür" — and what Unity's own
#: Editor API takes natively; the Blender driver converts to radians itself).
LOCATION_BOUND = 1000.0
ROTATION_BOUND = 360.0
SCALE_MIN, SCALE_MAX = 0.001, 1000.0
ENERGY_MIN, ENERGY_MAX = 0.0, 100_000.0
RENDER_WIDTH_MAX = 1920
RENDER_HEIGHT_MAX = 1080

#: B44 (req 524-527): the production path's controls, bounded like every number above.
ANIMATABLE_CHANNELS: tuple[str, ...] = ("location", "rotation", "scale")
SCENE_EXPORT_FORMATS: tuple[str, ...] = ("glb", "fbx")
MAX_KEYFRAMES = 32
FRAME_BOUND = 10_000
FPS_MIN, FPS_MAX = 1, 120
LENS_MIN, LENS_MAX = 1.0, 500.0
#: Operations only the Blender driver implements and the Blender lab proves. The Unity
#: driver is pinned and its licence is the owner's to obtain, so a Unity plan naming one is
#: refused by name before any device is asked - attach_script's rule, the other way round.
BLENDER_ONLY_OPS: tuple[str, ...] = ("set_frames", "animate", "export")


# ------------------------------------------------------------------ the operations


class CreateScene(_StrictModel):
    op: Literal["create_scene"] = "create_scene"


class AddPrimitive(_StrictModel):
    op: Literal["add_primitive"] = "add_primitive"
    kind: Literal["cube", "sphere", "cylinder", "plane", "light_sun", "light_point", "camera"]
    name: str = Field(min_length=1, max_length=64)
    location: Vec3 = (0.0, 0.0, 0.0)
    rotation: Vec3 = (0.0, 0.0, 0.0)
    scale: Vec3 = (1.0, 1.0, 1.0)


class Transform(_StrictModel):
    op: Literal["transform"] = "transform"
    name: str = Field(min_length=1, max_length=64)
    location: Vec3 | None = None
    rotation: Vec3 | None = None
    scale: Vec3 | None = None

    @model_validator(mode="after")
    def _something_to_do(self) -> Transform:
        if self.location is None and self.rotation is None and self.scale is None:
            raise ValueError("transform must set at least one of location/rotation/scale")
        return self


class SetMaterial(_StrictModel):
    op: Literal["set_material"] = "set_material"
    name: str = Field(min_length=1, max_length=64)
    color: Color
    metallic: float | None = None
    roughness: float | None = None


class SetCamera(_StrictModel):
    op: Literal["set_camera"] = "set_camera"
    name: str = Field(min_length=1, max_length=64)
    look_at: str | None = Field(default=None, min_length=1, max_length=64)
    #: B44 (req 525): the camera's focal length in millimetres.
    lens: float | None = Field(default=None, ge=LENS_MIN, le=LENS_MAX)


class SetLight(_StrictModel):
    op: Literal["set_light"] = "set_light"
    name: str = Field(min_length=1, max_length=64)
    energy: float
    #: B44 (req 524): the light's colour, linear RGB in [0, 1].
    color: tuple[float, float, float] | None = None


class AttachScript(_StrictModel):
    op: Literal["attach_script"] = "attach_script"
    name: str = Field(min_length=1, max_length=64)
    script_id: Literal["Spinner", "Bouncer", "ColorCycler"]


class Render(_StrictModel):
    op: Literal["render"] = "render"
    width: int = 320
    height: int = 240
    engine: Literal["workbench", "eevee"] = "workbench"


class SetFrames(_StrictModel):
    """B44 (req 526): the animation's frame range and rate."""

    op: Literal["set_frames"] = "set_frames"
    start: int = 1
    end: int = 48
    fps: int = 24

    @model_validator(mode="after")
    def _ordered(self) -> SetFrames:
        _bounded(self.start, 0, FRAME_BOUND, "set_frames.start")
        _bounded(self.end, 0, FRAME_BOUND, "set_frames.end")
        _bounded(self.fps, FPS_MIN, FPS_MAX, "set_frames.fps")
        if self.end <= self.start:
            raise ValueError("set_frames.end must come after set_frames.start")
        return self


class Keyframe(_StrictModel):
    frame: int
    value: Vec3


class Animate(_StrictModel):
    """B44 (req 526): keyframes on one object's location / rotation / scale. The driver
    inserts them with Blender's own ``keyframe_insert``; the read-back comes from the
    action's F-curves, never from this list restated."""

    op: Literal["animate"] = "animate"
    name: str = Field(min_length=1, max_length=64)
    channel: Literal["location", "rotation", "scale"]
    keyframes: list[Keyframe] = Field(min_length=2, max_length=MAX_KEYFRAMES)

    @model_validator(mode="after")
    def _frames_increase(self) -> Animate:
        frames = [k.frame for k in self.keyframes]
        for frame in frames:
            _bounded(frame, 0, FRAME_BOUND, "animate.frame")
        if any(later <= earlier for earlier, later in zip(frames, frames[1:], strict=False)):
            raise ValueError("animate.keyframes must be in strictly increasing frame order")
        for keyframe in self.keyframes:
            for component in keyframe.value:
                if self.channel == "scale":
                    _bounded(float(component), SCALE_MIN, SCALE_MAX, "animate.value")
                elif self.channel == "rotation":
                    _bounded(float(component), -ROTATION_BOUND, ROTATION_BOUND, "animate.value")
                else:
                    _bounded(float(component), -LOCATION_BOUND, LOCATION_BOUND, "animate.value")
        return self


class ExportScene(_StrictModel):
    """B44 (req 527): the scene written as GLB or FBX beside the scene file, on the owner's
    disk; the device verifies the file (hash and format signature) before it is reported."""

    op: Literal["export"] = "export"
    format: Literal["glb", "fbx"] = "glb"


class Inspect(_StrictModel):
    op: Literal["inspect"] = "inspect"


Operation = Annotated[
    CreateScene
    | AddPrimitive
    | Transform
    | SetMaterial
    | SetCamera
    | SetLight
    | AttachScript
    | Render
    | Inspect
    | SetFrames
    | Animate
    | ExportScene,
    Field(discriminator="op"),
]

#: Operation kinds that carry a plain identifier the closed-alphabet name check applies
#: to, and which attribute names to check on them.
_NAME_BEARING_OPS: tuple[str, ...] = (
    "add_primitive",
    "transform",
    "set_material",
    "set_camera",
    "set_light",
    "attach_script",
    "animate",
)


# --------------------------------------------------------------------------- the plan


class ScenePlan(_StrictModel):
    tool: Literal["blender", "unity"]
    project: str = Field(min_length=1, max_length=64)
    scene: str = Field(min_length=1, max_length=64)
    operations: list[Operation] = Field(default_factory=list, max_length=MAX_OPERATIONS)
    #: The ONE free-text-shaped field in this schema (module docstring) — never spliced
    #: into generated code, carried only into a receipt/ledger row.
    label: str | None = Field(default=None, max_length=MAX_LABEL_CHARS)

    @field_validator("project")
    @classmethod
    def _project_slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError(
                "project must be a lowercase slug ([a-z0-9][a-z0-9-]{0,63}); "
                "a path, a drive letter or an owner project name is refused here"
            )
        return value

    @field_validator("scene")
    @classmethod
    def _scene_slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError("scene must be a lowercase slug ([a-z0-9][a-z0-9-]{0,63})")
        return value

    @field_validator("label")
    @classmethod
    def _label_plain(cls, value: str | None) -> str | None:
        return None if value is None else _no_control_characters(value, "label")

    @model_validator(mode="after")
    def _names_closed_alphabet(self) -> ScenePlan:
        for op in self.operations:
            if op.op in _NAME_BEARING_OPS:
                name = op.name
                if not _NAME_RE.match(name):
                    raise ValueError(
                        f"{op.op}.name {name!r} is not a plain identifier "
                        "([A-Za-z][A-Za-z0-9_]{0,63})"
                    )
            look_at = getattr(op, "look_at", None)
            if look_at is not None and not _NAME_RE.match(look_at):
                raise ValueError(f"set_camera.look_at {look_at!r} is not a plain identifier")
        return self

    @model_validator(mode="after")
    def _bounded_numbers(self) -> ScenePlan:
        for op in self.operations:
            for attr in ("location", "rotation"):
                vec = getattr(op, attr, None)
                if vec is None:
                    continue
                bound = ROTATION_BOUND if attr == "rotation" else LOCATION_BOUND
                for component in vec:
                    _bounded(float(component), -bound, bound, f"{op.op}.{attr}")
            scale = getattr(op, "scale", None)
            if scale is not None:
                for component in scale:
                    _bounded(float(component), SCALE_MIN, SCALE_MAX, f"{op.op}.scale")
            color = getattr(op, "color", None)
            if color is not None:
                for component in color:
                    _bounded(float(component), 0.0, 1.0, f"{op.op}.color")
            for attr in ("metallic", "roughness"):
                val = getattr(op, attr, None)
                if val is not None:
                    _bounded(float(val), 0.0, 1.0, f"{op.op}.{attr}")
            energy = getattr(op, "energy", None)
            if energy is not None:
                _bounded(float(energy), ENERGY_MIN, ENERGY_MAX, "set_light.energy")
            width = getattr(op, "width", None)
            if width is not None:
                _bounded(int(width), 1, RENDER_WIDTH_MAX, "render.width")
            height = getattr(op, "height", None)
            if height is not None:
                _bounded(int(height), 1, RENDER_HEIGHT_MAX, "render.height")
        return self

    @model_validator(mode="after")
    def _blender_only_controls(self) -> ScenePlan:
        if self.tool == TOOL_BLENDER:
            return self
        for op in self.operations:
            if op.op in BLENDER_ONLY_OPS:
                raise ValueError(f"{op.op} is only valid when tool='blender'")
            if op.op == "set_light" and op.color is not None:
                raise ValueError("set_light.color is only valid when tool='blender'")
            if op.op == "set_camera" and op.lens is not None:
                raise ValueError("set_camera.lens is only valid when tool='blender'")
        return self

    @model_validator(mode="after")
    def _attach_script_unity_only(self) -> ScenePlan:
        if self.tool != TOOL_UNITY:
            for op in self.operations:
                if op.op == "attach_script":
                    raise ValueError("attach_script is only valid when tool='unity'")
        return self

    def plan_json(self) -> str:
        """Canonical JSON: sorted keys, no incidental whitespace — the exact bytes a
        driver reads and the exact bytes any evidence hash is taken over."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_plan_json(cls, text: str) -> ScenePlan:
        return cls.model_validate(json.loads(text))

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "ENERGY_MAX",
    "ENERGY_MIN",
    "LOCATION_BOUND",
    "MAX_LABEL_CHARS",
    "MAX_OPERATIONS",
    "PRIMITIVE_KINDS",
    "RENDER_ENGINES",
    "RENDER_HEIGHT_MAX",
    "RENDER_WIDTH_MAX",
    "ROTATION_BOUND",
    "SCALE_MAX",
    "SCALE_MIN",
    "SCRIPT_CATALOGUE",
    "TOOLS",
    "TOOL_BLENDER",
    "TOOL_UNITY",
    "AddPrimitive",
    "AttachScript",
    "Color",
    "CreateScene",
    "Inspect",
    "Operation",
    "Render",
    "ScenePlan",
    "SetCamera",
    "SetLight",
    "SetMaterial",
    "Transform",
    "Vec3",
]
