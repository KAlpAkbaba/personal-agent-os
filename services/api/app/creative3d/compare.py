"""The closed loop's own comparison (docs/M25_CREATIVE_3D_SPEC.md §4, ADR-0088
decision 4): the plan's constraints vs. what the driver's inspection actually says.

owner intent -> ``ScenePlan`` -> the driver creates -> render/preview -> **inspect**
(the tool's own read-back) -> **compare** (this module) -> modify -> rerender ->
validate. A mismatch names the object and the axis; a match is claimed only when
every requested constraint was actually checked against something real.

The M24 lesson, restated for this module by name (module docstring of
``app.genesis.interface`` and the M24 security review): **never report a match over
an empty comparison**. If a plan asks for nothing checkable — no primitive, no
material, no camera aim, no render — that is a mismatch (``no_constraints``), not a
pass. A vacuous gate is a defect, the same one M24's own security review found and
fixed; this module is built so it cannot recur here.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any

from app.creative3d.spec import ScenePlan

#: Tolerances (spec §4). Location/scale/colour tolerances are generous enough to
#: survive float round-tripping through JSON and Blender's own unit conversions
#: (degrees<->radians for rotation) without hiding a real mismatch.
LOCATION_TOLERANCE = 0.01
ROTATION_TOLERANCE_DEG = 1.0
SCALE_TOLERANCE = 0.01
COLOR_TOLERANCE = 0.02
CAMERA_AIM_TOLERANCE_DEG = 2.0
#: A render is "non-trivial" when its pixel-value standard deviation exceeds this —
#: a genuinely flat/uniform image (a bug, a black frame, a missing camera) reads as
#: (near) zero; any real render of a lit primitive against a background is well above
#: it. Measured on a greyscale downsample so colour and luminance renders both work.
RENDER_NONUNIFORM_STDDEV_MIN = 1.0
#: A render this small in bytes cannot possibly carry a real image (spec §4: "the
#: render file present and non-trivial").
RENDER_MIN_BYTES = 256


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One thing the inspection did not confirm — always names the object AND the
    axis/field, never a bare "mismatch" (spec §4)."""

    object_name: str
    field: str
    expected: Any
    actual: Any
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "object": self.object_name,
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CompareResult:
    ok: bool
    checked: int
    mismatches: tuple[Mismatch, ...] = field(default_factory=tuple)
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "mismatches": [m.as_dict() for m in self.mismatches],
            "reason": self.reason,
        }


def _find_object(inspection: dict[str, Any], name: str) -> dict[str, Any] | None:
    for obj in inspection.get("objects") or []:
        if obj.get("name") == name:
            return obj
    return None


def _vec_mismatches(
    object_name: str,
    field_prefix: str,
    expected: tuple[float, float, float],
    actual: list[float] | None,
    tolerance: float,
) -> list[Mismatch]:
    out: list[Mismatch] = []
    if actual is None or len(actual) != 3:
        return [
            Mismatch(object_name, field_prefix, list(expected), actual, "missing from inspection")
        ]
    axes = ("x", "y", "z")
    for axis, exp_c, act_c in zip(axes, expected, actual, strict=True):
        if abs(float(exp_c) - float(act_c)) > tolerance:
            out.append(
                Mismatch(
                    object_name,
                    f"{field_prefix}.{axis}",
                    exp_c,
                    act_c,
                    f"|{exp_c} - {act_c}| > {tolerance}",
                )
            )
    return out


def forward_vector(rotation_deg: tuple[float, float, float]) -> tuple[float, float, float]:
    """The world-space direction a camera's local -Z axis points, given Blender's own
    XYZ Euler rotation (degrees) — ``R = Rz(rz) @ Ry(ry) @ Rx(rx)`` applied to
    ``(0, 0, -1)``. Derived independently of
    ``app.creative3d.drivers.blender_driver.look_at_euler_rad`` (never imports it): this
    is the CHECK, not a restatement of the thing being checked — a bug in one must not
    be invisible to the other."""
    rx, ry, rz = (math.radians(c) for c in rotation_deg)
    # step1 = Rx(rx) @ (0, 0, -1)
    x1, y1, z1 = 0.0, math.sin(rx), -math.cos(rx)
    # step2 = Ry(ry) @ step1
    x2 = x1 * math.cos(ry) + z1 * math.sin(ry)
    y2 = y1
    z2 = -x1 * math.sin(ry) + z1 * math.cos(ry)
    # step3 = Rz(rz) @ step2
    x3 = x2 * math.cos(rz) - y2 * math.sin(rz)
    y3 = x2 * math.sin(rz) + y2 * math.cos(rz)
    z3 = z2
    return (x3, y3, z3)


def _angle_between_deg(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    mag_a = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
    mag_b = math.sqrt(b[0] ** 2 + b[1] ** 2 + b[2] ** 2)
    if mag_a < 1e-9 or mag_b < 1e-9:
        return 180.0
    cos_theta = max(-1.0, min(1.0, dot / (mag_a * mag_b)))
    return math.degrees(math.acos(cos_theta))


def check_render(png_bytes: bytes | None) -> Mismatch | None:
    """``PIL`` opens the render and measures that it is not uniform (spec §4) — an
    INDEPENDENT reader, never trusting the driver's own claim that it rendered
    something. Returns ``None`` on a genuine, non-trivial render; a
    :class:`Mismatch` naming what failed otherwise."""
    if png_bytes is None:
        return Mismatch("render", "render", "present", None, "no render bytes provided")
    if len(png_bytes) < RENDER_MIN_BYTES:
        return Mismatch(
            "render",
            "render.bytes",
            f">= {RENDER_MIN_BYTES}",
            len(png_bytes),
            "too small to be real",
        )
    try:
        from PIL import Image, ImageStat

        with Image.open(io.BytesIO(png_bytes)) as img:
            img.load()
            grey = img.convert("L")
            stat = ImageStat.Stat(grey)
            stddev = stat.stddev[0]
    except Exception as exc:  # noqa: BLE001 - an unreadable "render" is a mismatch, not a crash
        return Mismatch(
            "render", "render", "a decodable PNG", None, f"PIL could not open it: {exc}"
        )
    if stddev < RENDER_NONUNIFORM_STDDEV_MIN:
        return Mismatch(
            "render",
            "render.stddev",
            f">= {RENDER_NONUNIFORM_STDDEV_MIN}",
            round(stddev, 4),
            "the image is (near) uniform — a blank/failed render",
        )
    return None


def compare(
    plan: ScenePlan, inspection: dict[str, Any], *, render_bytes: bytes | None = None
) -> CompareResult:
    """Every requested constraint the plan named, checked against the inspection —
    NEVER against the plan's own numbers restated. ``checked`` counts how many
    individual constraints were actually evaluated; ``ok`` is true only when
    ``checked > 0`` and every one of them passed (module docstring: never a match
    over an empty comparison).

    Per-object constraints are FOLDED across the whole plan before anything is
    compared: a later ``transform``/``add_primitive`` for the same object wins over
    an earlier one for the same field, so a plan that adds an object and then moves
    it is checked against where it ends up, never flagged for not still being where
    it started. Found the real way, running the real Blender lab
    (``scripts/tests/blender-scene-lab.py``): an add-then-transform plan on a real
    driver reported a "mismatch" for a scale the plan itself had asked to change —
    the comparison was re-litigating a constraint its own later operation had
    superseded, not a real defect in the driver."""
    mismatches: list[Mismatch] = []
    checked = 0

    expected_by_object: dict[str, dict[str, Any]] = {}
    camera_aim: dict[str, str] = {}
    light_energy: dict[str, float] = {}
    last_render_op = None

    for op in plan.operations:
        if op.op == "add_primitive":
            entry = expected_by_object.setdefault(op.name, {})
            entry["location"] = op.location
            entry["scale"] = op.scale
            entry["rotation"] = op.rotation
        elif op.op == "transform":
            entry = expected_by_object.setdefault(op.name, {})
            if op.location is not None:
                entry["location"] = op.location
            if op.scale is not None:
                entry["scale"] = op.scale
            if op.rotation is not None:
                entry["rotation"] = op.rotation
        elif op.op == "set_material":
            entry = expected_by_object.setdefault(op.name, {})
            entry["material_color"] = op.color
        elif op.op == "set_camera" and op.look_at is not None:
            camera_aim[op.name] = op.look_at
        elif op.op == "set_light":
            light_energy[op.name] = op.energy
        elif op.op == "render":
            last_render_op = op

    # A ``set_camera ... look_at`` supersedes whatever rotation ``add_primitive``/
    # ``transform`` last stated for that SAME object, exactly the way a later
    # ``transform`` already supersedes an earlier one above — the aim is what the
    # plan actually asked for; the literal numeric rotation ``add_primitive``'s own
    # default (or an earlier explicit one) named is no longer a real constraint once
    # a look_at determines it instead. Checked instead, and independently, by the
    # camera_aim loop below (found the same way as the fold itself: running the real
    # Blender lab, scripts/tests/blender-scene-lab.py).
    for cam_name in camera_aim:
        expected_by_object.get(cam_name, {}).pop("rotation", None)

    for name, expected in expected_by_object.items():
        obj = _find_object(inspection, name)
        if obj is None:
            checked += 1
            mismatches.append(
                Mismatch(name, "presence", "present", "absent", "object not found in inspection")
            )
            continue
        if "location" in expected:
            checked += 1
            mismatches.extend(
                _vec_mismatches(
                    name, "location", expected["location"], obj.get("location"), LOCATION_TOLERANCE
                )
            )
        if "scale" in expected:
            checked += 1
            mismatches.extend(
                _vec_mismatches(name, "scale", expected["scale"], obj.get("scale"), SCALE_TOLERANCE)
            )
        if "rotation" in expected:
            checked += 1
            mismatches.extend(
                _vec_mismatches(
                    name,
                    "rotation",
                    expected["rotation"],
                    obj.get("rotation"),
                    ROTATION_TOLERANCE_DEG,
                )
            )
        if "material_color" in expected:
            checked += 1
            expected_color = expected["material_color"]
            actual_color = obj.get("material_color")
            if actual_color is None or len(actual_color) != 4:
                mismatches.append(
                    Mismatch(
                        name,
                        "material_color",
                        list(expected_color),
                        actual_color,
                        "no material colour in inspection",
                    )
                )
            else:
                for channel, exp_c, act_c in zip(
                    ("r", "g", "b", "a"), expected_color, actual_color, strict=True
                ):
                    if abs(float(exp_c) - float(act_c)) > COLOR_TOLERANCE:
                        mismatches.append(
                            Mismatch(
                                name,
                                f"material_color.{channel}",
                                exp_c,
                                act_c,
                                f"|{exp_c} - {act_c}| > {COLOR_TOLERANCE}",
                            )
                        )

    for cam_name, look_at in camera_aim.items():
        checked += 1
        cam = _find_object(inspection, cam_name)
        target = _find_object(inspection, look_at)
        if cam is None or target is None:
            mismatches.append(
                Mismatch(
                    cam_name,
                    "camera_aim",
                    look_at,
                    None,
                    "camera or look_at target not found in inspection",
                )
            )
            continue
        cam_rot = cam.get("rotation")
        cam_loc = cam.get("location")
        target_loc = target.get("location")
        if not cam_rot or not cam_loc or not target_loc:
            mismatches.append(
                Mismatch(
                    cam_name, "camera_aim", look_at, None, "missing location/rotation in inspection"
                )
            )
            continue
        actual_forward = forward_vector((cam_rot[0], cam_rot[1], cam_rot[2]))
        wanted = (
            target_loc[0] - cam_loc[0],
            target_loc[1] - cam_loc[1],
            target_loc[2] - cam_loc[2],
        )
        angle = _angle_between_deg(actual_forward, wanted)
        if angle > CAMERA_AIM_TOLERANCE_DEG:
            mismatches.append(
                Mismatch(
                    cam_name,
                    "camera_aim.angle_deg",
                    f"<= {CAMERA_AIM_TOLERANCE_DEG}",
                    round(angle, 3),
                    f"camera not aimed at {look_at!r}",
                )
            )

    for light_name, energy in light_energy.items():
        checked += 1
        lights = {light.get("name"): light for light in inspection.get("lights") or []}
        actual = lights.get(light_name)
        if actual is None:
            mismatches.append(
                Mismatch(light_name, "energy", energy, None, "light not found in inspection")
            )
        elif abs(float(actual.get("energy", 0.0)) - energy) > max(1.0, energy * 0.05):
            mismatches.append(
                Mismatch(light_name, "energy", energy, actual.get("energy"), "energy mismatch")
            )

    if last_render_op is not None:
        checked += 1
        render_mismatch = check_render(render_bytes)
        if render_mismatch is not None:
            mismatches.append(render_mismatch)
        elif inspection.get("render") is None:
            mismatches.append(
                Mismatch("render", "render", "present", None, "driver reported no render info")
            )

    if checked == 0:
        return CompareResult(ok=False, checked=0, mismatches=(), reason="no_constraints")

    return CompareResult(ok=not mismatches, checked=checked, mismatches=tuple(mismatches))


__all__ = [
    "CAMERA_AIM_TOLERANCE_DEG",
    "COLOR_TOLERANCE",
    "LOCATION_TOLERANCE",
    "RENDER_MIN_BYTES",
    "RENDER_NONUNIFORM_STDDEV_MIN",
    "ROTATION_TOLERANCE_DEG",
    "SCALE_TOLERANCE",
    "CompareResult",
    "Mismatch",
    "check_render",
    "compare",
    "forward_vector",
]
