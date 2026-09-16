"""Blender headless driver (docs/M25_CREATIVE_3D_SPEC.md §3, ADR-0088 decision 2).

A FIXED repository file, sha256-pinned in ``manifest.json`` and asserted by
``tests/unit/test_blender_driver.py``. Runs inside Blender's own Python:

    blender.exe --factory-startup -b [scene.blend] --python blender_driver.py --
        <plan.json> <out.json>

Reads a ``ScenePlan`` (``app.creative3d.spec.ScenePlan.plan_json()`` — canonical JSON,
never Python generated from prose), executes every operation through ``bpy``, saves the
``.blend`` beside ``out.json``, renders when a ``render`` operation asks for it, and
writes an INSPECTION (``out.json``): every object with its type/location/rotation/scale/
material colour, the camera, the lights, the render path + sha256 + bytes, and any
``errors[]``. The inspection — never the plan — is what ``app.creative3d.compare`` and
every receipt reads back (spec §4's closed loop).

Importable standalone: this module's pure functions (argv parsing, plan loading, the
look-at trigonometry, the inspection shape) carry no ``bpy`` call of their own, and even
the ``bpy``-calling functions only ever touch the small, fixed surface
``tests/unit/test_blender_driver.py``'s fake ``bpy`` module provides — injected into
``sys.modules`` BEFORE this module is imported, so the whole driver runs against it with
no real Blender needed for the unit suite. The real Blender lab is
``scripts/tests/blender-scene-lab.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]  # Blender's own module, or a test fake.

#: DEVICE_PROTOCOL.md §6l: every device result is bounded — the inspection is capped the
#: same way any other capability result is.
MAX_OUT_JSON_BYTES = 256 * 1024

_PRIMITIVE_MESH_OPS: dict[str, str] = {
    "cube": "primitive_cube_add",
    "sphere": "primitive_uv_sphere_add",
    "cylinder": "primitive_cylinder_add",
    "plane": "primitive_plane_add",
}
_LIGHT_KINDS: dict[str, str] = {"light_sun": "SUN", "light_point": "POINT"}

#: Blender 4.5 LTS engine identifiers (workbench unchanged since 2.8; EEVEE renamed to
#: "Next" in 4.2 — docs/M25_CREATIVE_3D_SPEC.md §1's own detected version).
ENGINE_BY_NAME: dict[str, str] = {
    "workbench": "BLENDER_WORKBENCH",
    "eevee": "BLENDER_EEVEE_NEXT",
}


# ============================================================================ pure
# Every function in this section touches no ``bpy`` state — plain argv/JSON handling
# and trigonometry, unit-testable with no fake at all (though the module as a whole
# still needs one in ``sys.modules`` to import at all, since the ``import bpy`` above
# is unconditional — the same "fixed file, no environment-sniffing branch" discipline
# the module docstring states).


def parse_argv(argv: list[str]) -> tuple[str, str]:
    """Blender hands the running script everything after its own ``--``; a bare
    module invocation (tests, or a future non-Blender caller) passes the same tail
    directly. Exactly two positional arguments — ``plan.json`` then ``out.json`` —
    else a ``ValueError`` naming the count actually seen."""
    tail = argv[argv.index("--") + 1 :] if "--" in argv else list(argv)
    if len(tail) != 2:
        raise ValueError(f"expected exactly 2 arguments (plan.json out.json), got {len(tail)}")
    return tail[0], tail[1]


def load_plan(plan_path: str) -> dict[str, Any]:
    with open(plan_path, encoding="utf-8") as fh:
        return json.load(fh)


def look_at_euler_rad(
    cam_loc: tuple[float, float, float], target_loc: tuple[float, float, float]
) -> tuple[float, float, float]:
    """The rotation (radians, Blender's XYZ Euler order — ``R = Rz @ Ry @ Rx``) that
    points a camera's local -Z axis at ``target_loc`` from ``cam_loc``, with zero roll.

    Derived directly (no ``mathutils`` dependency, so this runs identically whether
    ``bpy`` is real or the test's fake): solving ``Rz(rz) @ Rx(rx) @ (0,0,-1) = d`` for
    the normalised direction ``d`` gives ``rx = acos(-d.z)``, ``rz = atan2(-d.x, d.y)``
    (``ry = 0``) — the same result ``mathutils.Vector.to_track_quat('-Z', 'Y')`` would
    produce, verified independently by ``app.creative3d.compare``'s own forward-vector
    recovery, which every ``test_scene_compare.py`` camera-aim case checks against.
    """
    dx = target_loc[0] - cam_loc[0]
    dy = target_loc[1] - cam_loc[1]
    dz = target_loc[2] - cam_loc[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist < 1e-9:
        return (0.0, 0.0, 0.0)
    dx, dy, dz = dx / dist, dy / dist, dz / dist
    rx = math.acos(max(-1.0, min(1.0, -dz)))
    sin_rx = math.sin(rx)
    rz = 0.0 if abs(sin_rx) < 1e-9 else math.atan2(-dx, dy)
    return (rx, 0.0, rz)


def sha256_bytes(path: str) -> tuple[str, int]:
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _round(vec: Any) -> list[float]:
    return [round(float(c), 6) for c in vec]


# ========================================================================= bpy ops
# Every function below touches the ``bpy`` surface — real under Blender, a small fixed
# fake under the unit suite (module docstring). Each takes the RAW operation dict (the
# plan already passed ``ScenePlan`` validation upstream; this layer stays defensive —
# ``errors[]`` catches anything it does not expect rather than crashing the run) and an
# ``errors`` list it appends to, never raises.


def clear_scene() -> None:
    """``create_scene`` — a clean slate regardless of what ``.blend`` was loaded, so
    the operation is idempotent and the driver's own tests need no fixture file."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def add_primitive(op: dict[str, Any], errors: list[str]) -> None:
    kind = op.get("kind")
    name = op.get("name")
    location = tuple(op.get("location", (0.0, 0.0, 0.0)))
    rotation = tuple(op.get("rotation", (0.0, 0.0, 0.0)))
    scale = tuple(op.get("scale", (1.0, 1.0, 1.0)))
    if kind in _PRIMITIVE_MESH_OPS:
        getattr(bpy.ops.mesh, _PRIMITIVE_MESH_OPS[kind])(location=location)
    elif kind in _LIGHT_KINDS:
        bpy.ops.object.light_add(type=_LIGHT_KINDS[kind], location=location)
    elif kind == "camera":
        bpy.ops.object.camera_add(location=location)
    else:
        errors.append(f"add_primitive: unknown kind {kind!r}")
        return
    obj = bpy.context.active_object
    if obj is None:
        errors.append(f"add_primitive: {name!r} could not be created")
        return
    obj.name = name
    obj.rotation_euler = tuple(math.radians(c) for c in rotation)
    obj.scale = scale
    if kind == "camera" and bpy.context.scene.camera is None:
        bpy.context.scene.camera = obj


def apply_transform(op: dict[str, Any], errors: list[str]) -> None:
    name = op.get("name")
    obj = bpy.data.objects.get(name)
    if obj is None:
        errors.append(f"transform: object {name!r} not found")
        return
    if op.get("location") is not None:
        obj.location = tuple(op["location"])
    if op.get("rotation") is not None:
        obj.rotation_euler = tuple(math.radians(c) for c in op["rotation"])
    if op.get("scale") is not None:
        obj.scale = tuple(op["scale"])


def apply_material(op: dict[str, Any], errors: list[str]) -> None:
    name = op.get("name")
    obj = bpy.data.objects.get(name)
    if obj is None:
        errors.append(f"set_material: object {name!r} not found")
        return
    materials = getattr(getattr(obj, "data", None), "materials", None)
    if materials is None:
        errors.append(f"set_material: {name!r} cannot carry a material")
        return
    mat = bpy.data.materials.new(name=f"{name}_mat")
    mat.diffuse_color = tuple(op["color"])
    if op.get("metallic") is not None:
        mat.metallic = float(op["metallic"])
    if op.get("roughness") is not None:
        mat.roughness = float(op["roughness"])
    materials.append(mat)


def apply_camera(op: dict[str, Any], errors: list[str]) -> None:
    name = op.get("name")
    obj = bpy.data.objects.get(name)
    if obj is None:
        errors.append(f"set_camera: object {name!r} not found")
        return
    bpy.context.scene.camera = obj
    if op.get("lens") is not None:
        # B44 (req 525): the camera's own focal length, read back from the same data.
        camera_data = getattr(obj, "data", None)
        if camera_data is None or not hasattr(camera_data, "lens"):
            errors.append(f"set_camera: {name!r} has no lens")
        else:
            camera_data.lens = float(op["lens"])
    look_at = op.get("look_at")
    if look_at is not None:
        target = bpy.data.objects.get(look_at)
        if target is None:
            errors.append(f"set_camera: look_at target {look_at!r} not found")
            return
        obj.rotation_euler = look_at_euler_rad(tuple(obj.location), tuple(target.location))


def apply_light(op: dict[str, Any], errors: list[str]) -> None:
    name = op.get("name")
    obj = bpy.data.objects.get(name)
    # A light is known by its TYPE: a camera carries data too, and setting energy on it
    # was an AttributeError the run turned into a vague error instead of this one.
    if obj is None or getattr(obj, "type", None) != "LIGHT" or getattr(obj, "data", None) is None:
        errors.append(f"set_light: light {name!r} not found")
        return
    obj.data.energy = float(op["energy"])
    if op.get("color") is not None:
        # B44 (req 524): the light's colour, linear RGB.
        obj.data.color = tuple(float(c) for c in op["color"])


def do_render(op: dict[str, Any], out_dir: str, errors: list[str]) -> dict[str, Any] | None:
    width = int(op.get("width", 320))
    height = int(op.get("height", 240))
    engine = str(op.get("engine", "workbench"))
    scene = bpy.context.scene
    if scene.camera is None:
        errors.append("render: no camera in the scene")
        return None
    scene.render.engine = ENGINE_BY_NAME.get(engine, "BLENDER_WORKBENCH")
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    render_path = str(Path(out_dir) / "render.png")
    scene.render.filepath = render_path
    bpy.ops.render.render(write_still=True)
    if not Path(render_path).exists():
        errors.append("render: no file was written")
        return None
    digest, size = sha256_bytes(render_path)
    return {
        # RELATIVE to the project folder: the device resolves every path in out.json
        # inside the project and refuses an absolute one (DEVICE_PROTOCOL.md 6m). B44
        # found the absolute path this used to write: every real render was refused.
        "path": Path(render_path).name,
        "sha256": digest,
        "bytes": size,
        "width": width,
        "height": height,
        "engine": engine,
    }


_ANIMATION_DATA_PATHS: dict[str, str] = {
    "location": "location",
    "rotation": "rotation_euler",
    "scale": "scale",
}
_CHANNEL_BY_DATA_PATH: dict[str, str] = {v: k for k, v in _ANIMATION_DATA_PATHS.items()}
#: B44 (req 527): one file per format, beside the scene file, named the same on every run.
EXPORT_FILE_BY_FORMAT: dict[str, str] = {"glb": "scene.glb", "fbx": "scene.fbx"}


def apply_frames(op: dict[str, Any], errors: list[str]) -> None:
    """B44 (req 526): the frame range and rate the animation plays over."""
    scene = bpy.context.scene
    scene.frame_start = int(op.get("start", 1))
    scene.frame_end = int(op.get("end", 48))
    scene.render.fps = int(op.get("fps", 24))


def apply_animation(op: dict[str, Any], errors: list[str]) -> None:
    """B44 (req 526): each keyframe's value set on the property and recorded with Blender's
    own ``keyframe_insert`` - the read-back comes from the F-curves this creates."""
    name = op.get("name")
    obj = bpy.data.objects.get(name)
    if obj is None:
        errors.append(f"animate: object {name!r} not found")
        return
    channel = op.get("channel")
    data_path = _ANIMATION_DATA_PATHS.get(channel or "")
    if data_path is None:
        errors.append(f"animate: unknown channel {channel!r}")
        return
    for keyframe in op.get("keyframes") or []:
        value = tuple(float(c) for c in keyframe["value"])
        if channel == "rotation":
            value = tuple(math.radians(c) for c in value)
        setattr(obj, data_path, value)
        obj.keyframe_insert(data_path=data_path, frame=int(keyframe["frame"]))
    # The scene opens at its first frame: evaluate the animation there, so every
    # read-back of this channel is the value the scene starts with, not the last
    # keyframe this loop happened to write.
    scene = bpy.context.scene
    frame_set = getattr(scene, "frame_set", None)
    if callable(frame_set):
        frame_set(int(getattr(scene, "frame_start", 1)))


def do_export(op: dict[str, Any], out_dir: str, errors: list[str]) -> dict[str, Any] | None:
    """B44 (req 527): the scene written by Blender's own bundled exporters (glTF 2.0 binary,
    FBX binary) beside the scene file, declared RELATIVE with its size and sha256 - the
    device re-hashes the file and reads its format signature before it reports it."""
    fmt = str(op.get("format", "glb"))
    file_name = EXPORT_FILE_BY_FORMAT.get(fmt)
    if file_name is None:
        errors.append(f"export: unknown format {fmt!r}")
        return None
    target = str(Path(out_dir) / file_name)
    if fmt == "glb":
        bpy.ops.export_scene.gltf(filepath=target, export_format="GLB", export_animations=True)
    else:
        bpy.ops.export_scene.fbx(filepath=target, bake_anim=True)
    if not Path(target).exists():
        errors.append(f"export: no {fmt} file was written")
        return None
    digest, size = sha256_bytes(target)
    return {"format": fmt, "path": file_name, "sha256": digest, "bytes": size}


def _fcurves_of(action: Any) -> list[Any]:
    """An action's F-curves: the legacy ``action.fcurves`` where Blender still offers it,
    else every channel bag of the layered action (Blender 4.4+)."""
    if action is None:
        return []
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        try:
            curves = list(legacy)
        except Exception:  # noqa: BLE001 - a deprecated accessor that refuses is not fatal
            curves = []
        if curves:
            return curves
    found: list[Any] = []
    for layer in getattr(action, "layers", None) or []:
        for strip in getattr(layer, "strips", None) or []:
            for bag in getattr(strip, "channelbags", None) or []:
                found.extend(list(getattr(bag, "fcurves", None) or []))
    return found


def animation_tracks() -> list[dict[str, Any]]:
    """Every animated object and channel, read from the F-curves Blender holds: the key
    frames and the evaluated value at each (rotation in degrees, like every read-back)."""
    tracks: list[dict[str, Any]] = []
    for obj in bpy.data.objects:
        animation = getattr(obj, "animation_data", None)
        curves = _fcurves_of(getattr(animation, "action", None))
        by_channel: dict[str, dict[int, Any]] = {}
        for curve in curves:
            channel = _CHANNEL_BY_DATA_PATH.get(getattr(curve, "data_path", ""))
            if channel is None:
                continue
            by_channel.setdefault(channel, {})[int(curve.array_index)] = curve
        for channel, curve_by_index in sorted(by_channel.items()):
            frames = sorted(
                {
                    int(round(point.co[0]))
                    for curve in curve_by_index.values()
                    for point in curve.keyframe_points
                }
            )
            values: list[list[float]] = []
            for frame in frames:
                vec = [
                    float(curve_by_index[i].evaluate(frame)) if i in curve_by_index else 0.0
                    for i in range(3)
                ]
                if channel == "rotation":
                    vec = [math.degrees(c) for c in vec]
                values.append(_round(vec))
            tracks.append(
                {"object": obj.name, "channel": channel, "frames": frames, "values": values}
            )
    return tracks


def build_inspection(
    errors: list[str],
    render_info: dict[str, Any] | None,
    exports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    camera_name: str | None = None
    lights: list[dict[str, Any]] = []
    for obj in bpy.data.objects:
        obj_type = getattr(obj, "type", "UNKNOWN")
        entry: dict[str, Any] = {
            "name": obj.name,
            "type": obj_type,
            "location": _round(obj.location),
            "rotation": _round(tuple(math.degrees(c) for c in obj.rotation_euler)),
            "scale": _round(obj.scale),
        }
        materials = getattr(getattr(obj, "data", None), "materials", None)
        if materials:
            mat = materials[0]
            if mat is not None:
                entry["material_color"] = _round(
                    getattr(mat, "diffuse_color", (0.8, 0.8, 0.8, 1.0))
                )
                # B44 (req 523): the whole material the plan can set, not only its colour.
                entry["material_metallic"] = round(float(getattr(mat, "metallic", 0.0)), 6)
                entry["material_roughness"] = round(float(getattr(mat, "roughness", 0.5)), 6)
        objects.append(entry)
        if obj_type == "CAMERA":
            camera_name = obj.name
            lens = getattr(getattr(obj, "data", None), "lens", None)
            if lens is not None:
                entry["lens"] = round(float(lens), 6)
        if obj_type == "LIGHT":
            light: dict[str, Any] = {
                "name": obj.name,
                "energy": float(getattr(obj.data, "energy", 0.0)),
            }
            light_color = getattr(obj.data, "color", None)
            if light_color is not None:
                light["color"] = _round(light_color)
            lights.append(light)
    scene = bpy.context.scene
    return {
        "objects": objects,
        "camera": camera_name,
        "lights": lights,
        "render": render_info,
        # B44 (req 526, 527): the frame range, the F-curves' own keyframes, the exports.
        "frames": {
            "start": int(getattr(scene, "frame_start", 1)),
            "end": int(getattr(scene, "frame_end", 250)),
            "fps": int(getattr(scene.render, "fps", 24)),
        },
        "animation": animation_tracks(),
        "exports": list(exports or []),
        "errors": errors,
    }


def apply_operations(plan: dict[str, Any], out_dir: str) -> dict[str, Any]:
    errors: list[str] = []
    render_info: dict[str, Any] | None = None
    exports: list[dict[str, Any]] = []
    for op in plan.get("operations", []):
        kind = op.get("op")
        try:
            if kind == "create_scene":
                clear_scene()
            elif kind == "add_primitive":
                add_primitive(op, errors)
            elif kind == "transform":
                apply_transform(op, errors)
            elif kind == "set_material":
                apply_material(op, errors)
            elif kind == "set_camera":
                apply_camera(op, errors)
            elif kind == "set_light":
                apply_light(op, errors)
            elif kind == "attach_script":
                # Unity-only by construction (``ScenePlan``'s own validator refuses
                # this op for tool="blender" before the plan ever reaches a driver);
                # kept here only so a tampered plan.json fails loudly, not silently.
                errors.append("attach_script: not supported by the Blender driver")
            elif kind == "render":
                info = do_render(op, out_dir, errors)
                render_info = info if info is not None else render_info
            elif kind == "set_frames":
                apply_frames(op, errors)
            elif kind == "animate":
                apply_animation(op, errors)
            elif kind == "export":
                exported = do_export(op, out_dir, errors)
                if exported is not None:
                    exports.append(exported)
            elif kind == "inspect":
                pass  # the inspection is always written, at the end, regardless.
            else:
                errors.append(f"unknown operation {kind!r}")
        except Exception as exc:  # noqa: BLE001 - captured as data, never crashes the run
            errors.append(f"{kind}: {exc}")
    return build_inspection(errors, render_info, exports)


#: The one scene file a project holds; the device's command names it literally.
SCENE_FILE_NAME = "scene.blend"


def save_blend(blend_path: str) -> None:
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)


def write_out_json(out_path: str, inspection: dict[str, Any]) -> None:
    text = json.dumps(inspection, sort_keys=True)
    if len(text.encode("utf-8")) > MAX_OUT_JSON_BYTES:
        # Truncate the least essential thing first rather than silently growing past
        # the protocol's own bound (DEVICE_PROTOCOL.md §6l: every result is bounded).
        bounded = dict(inspection)
        bounded["objects"] = list(inspection.get("objects", []))[:50]
        bounded["errors"] = list(inspection.get("errors", [])) + [
            "inspection truncated: exceeded the size bound"
        ]
        text = json.dumps(bounded, sort_keys=True)
    Path(out_path).write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    plan_path, out_path = parse_argv(argv)
    plan = load_plan(plan_path)
    out_dir = str(Path(out_path).resolve().parent)
    inspection = apply_operations(plan, out_dir)
    # ALWAYS "scene.blend", never the plan's own scene word: the name is half of the
    # command the device's allowlist matches token for token (DEVICE_PROTOCOL.md 6m,
    # `blender --factory-startup -b scene.blend ...`), so a driver saving under another name
    # left every later run with nothing to open. Measured 2026-09-08 by running the Cloud
    # Core's own command against the real editor
    # (scripts/tests/blender-service-command-lab.py): the first run saved `servis.blend` and
    # the second exited 1. One project, one scene file.
    blend_path = str(Path(out_dir) / SCENE_FILE_NAME)
    try:
        save_blend(blend_path)
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal to the inspection
        inspection["errors"].append(f"save_as_mainfile: {exc}")
    write_out_json(out_path, inspection)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
