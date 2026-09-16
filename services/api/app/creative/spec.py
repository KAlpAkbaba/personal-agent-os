"""``CreativePlan``: the structured, validated input to a creative-tool edit
(docs/M27_CREATIVE_TOOLS_SPEC.md §2, ADR-0093 decisions 1 and 4).

The plan is data, never code — the same rule ``app.creative3d.spec.ScenePlan`` states
for M25 and this module restates for M27: a closed operation vocabulary, closed-alphabet
names, bounded numbers, bounded text, and a canonical ``plan_json()`` (sorted keys, no
incidental whitespace — the exact bytes any evidence hash is taken over). Every text
field is drawn as PIXELS by Pillow (:mod:`app.creative.execute`) or carried as inert JSON
data to a fixed driver — never spliced into generated code (ADR-0093 decision 6).

The output-naming rule (module-level :func:`next_output_name`) and the ``name``/``source``
slug checks are refused BEFORE anything ever touches a file (ADR-0093 decision 5): a
path-shaped or traversing name is rejected by ``CreativePlan``'s own validators, the same
"the plan cannot even describe the owner's real project" discipline
``app.creative3d.spec.ScenePlan._project_slug`` already established for
``E:\\hologram\\HologramVehicleTest``.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- vocabulary

TOOL_PAINT = "paint"
TOOL_PHOTOSHOP = "photoshop"
TOOL_ILLUSTRATOR = "illustrator"
TOOL_FIGMA = "figma"
#: B43 (req 506-508): the in-house LAYERED editor - Pillow layers, PSD read, OpenRaster
#: and SVG write/read - always installed because it is this process.
TOOL_LAYERED = "layered"
TOOLS: tuple[str, ...] = (TOOL_PAINT, TOOL_PHOTOSHOP, TOOL_ILLUSTRATOR, TOOL_FIGMA, TOOL_LAYERED)

#: docs/M27_CREATIVE_TOOLS_SPEC.md §2/§4: the CLOSED operation vocabulary. "shape" is kept
#: as its own op, distinct from "draw" — the spec's own enumeration lists both by name.
#: The router's own reasonable extension (the same latitude
#: ``app.creative3d.spec``'s module docstring took for "scene.material"): ``draw`` takes an
#: explicit point list for a line/polygon; ``shape`` is the friendlier box-bounded
#: constructor a bare "kırmızı bir dikdörtgen çiz" naturally produces, for a rect/ellipse
#: only. Both compile to the same Pillow primitive in ``app.creative.execute``.
OPERATIONS: tuple[str, ...] = (
    "new",
    "open",
    "inspect",
    "draw",
    "add_text",
    "shape",
    "transform",
    "color_adjust",
    "crop",
    "background_remove",
    "layer",
    "export",
    # B43 (req 489-498): generation, semantic edits, styles, enhancement, upscale, check.
    "generate",
    "object_remove",
    "object_add",
    "style",
    "enhance",
    "upscale",
    "semantic_check",
)

DRAW_KINDS: tuple[str, ...] = ("line", "rect", "ellipse", "polygon")
SHAPE_KINDS: tuple[str, ...] = ("rect", "ellipse", "line")
TRANSFORM_KINDS: tuple[str, ...] = ("rotate", "flip", "scale")
FLIP_DIRECTIONS: tuple[str, ...] = ("horizontal", "vertical")
COLOR_ADJUST_FIELDS: tuple[str, ...] = ("brightness", "contrast", "saturation", "levels")
BACKGROUND_REMOVE_METHODS: tuple[str, ...] = ("flood", "threshold")
LAYER_OPS: tuple[str, ...] = ("add", "merge")
#: B43 (req 507): "ora" (OpenRaster) is the layered export; PSD is READ, never written
#: (Pillow has no PSD writer and the limitation is stated, not papered over).
EXPORT_FORMATS: tuple[str, ...] = ("png", "jpg", "svg", "pdf", "ora")

#: docs/M27_CREATIVE_TOOLS_SPEC.md §2: a closed, bounded font catalogue — never an
#: arbitrary owner/model string resolved to a filesystem path (that would be exactly the
#: kind of free-text-into-a-file-lookup this project's own ``_no_control_characters``
#: family of checks exists to close off). ``app.creative.execute`` maps each to a bundled
#: Pillow-loadable font; "default" is Pillow's own built-in bitmap font.
FONT_CATALOGUE: tuple[str, ...] = ("default", "sans", "serif", "mono")

MAX_OPERATIONS = 64
MAX_LABEL_CHARS = 200
MAX_TEXT_CHARS = 200

#: docs/M27_CREATIVE_TOOLS_SPEC.md §7: images are bounded — a runaway request is refused
#: at plan-validation time, well before any pixel is ever touched.
MAX_DIMENSION = 8192
MAX_POLYGON_POINTS = 64
MAX_LAYER_NAME_CHARS = 64

#: A plan/output name — the same shape ``app.creative3d.spec._SLUG_RE`` already uses for
#: its own project/scene identity: lowercase ASCII, dashes, bounded. A path (a drive
#: letter, a separator, "..") never matches this, so the owner's real project
#: (``E:\\hologram\\HologramVehicleTest``) is refused here, before any file operation is
#: ever attempted — the CLAUDE.md hard constraint, enforced at the schema boundary.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: An object-store key or a documents-root-relative reference to an EXISTING source
#: image: plain segments separated by "/", no drive letter, no "..", no leading
#: separator. Never a raw filesystem path the owner or the model could smuggle a real
#: project location through.
_SOURCE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*(?:/[a-zA-Z0-9][a-zA-Z0-9_.\-]*)*$")

#: Every character that can end a line/statement somewhere a value might be read by a
#: shell, a log line, or a JS-shaped surface — restated here for ``label``/``text``, the
#: only free-text-shaped fields anywhere in this schema (module docstring).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


class PlanValidationError(ValueError):
    """A ``CreativePlan`` field failed its own bound or shape check."""


def _no_control_characters(value: str, field: str) -> str:
    hit = _CONTROL_RE.search(value)
    if hit is not None:
        raise ValueError(f"{field} must not contain the control character U+{ord(hit.group()):04X}")
    return value


def _bounded(value: float, lo: float, hi: float, field: str) -> float:
    if not (lo <= value <= hi):
        raise ValueError(f"{field} must be within [{lo}, {hi}], got {value}")
    return value


def _is_path_shaped(value: str) -> bool:
    """True for anything that looks like it names a real filesystem location rather
    than a plain slug/object-key: a drive letter, a UNC/POSIX separator, or a ``..``
    traversal segment. The refusal every ``name``/``source`` field in this module
    applies BEFORE a single byte is ever touched (module docstring)."""
    if ".." in value:
        return True
    if "\\" in value:
        return True
    if re.match(r"^[A-Za-z]:", value):
        return True
    if value.startswith("/"):
        return True
    return False


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Point = tuple[float, float]
#: RGBA, each channel 0..255 — the same 8-bit-per-channel convention Pillow itself uses,
#: rather than the 0..1 float convention ``app.creative3d.spec.Color`` picked for
#: Blender/Unity (a deliberate difference: each module matches ITS OWN tool's native
#: convention, never a shared type that would need silent rescaling somewhere).
Color = tuple[int, int, int, int]
Box = tuple[float, float, float, float]

COLOR_CHANNEL_MIN, COLOR_CHANNEL_MAX = 0, 255
COORD_BOUND = float(MAX_DIMENSION)


def _color_field(value: Color, field: str) -> Color:
    for channel in value:
        _bounded(float(channel), COLOR_CHANNEL_MIN, COLOR_CHANNEL_MAX, field)
    return value


# ------------------------------------------------------------------ the operations


class New(_StrictModel):
    op: Literal["new"] = "new"
    width: int = Field(ge=1, le=MAX_DIMENSION)
    height: int = Field(ge=1, le=MAX_DIMENSION)
    background: Color = (255, 255, 255, 255)

    @field_validator("background")
    @classmethod
    def _bg(cls, value: Color) -> Color:
        return _color_field(value, "new.background")


class Open(_StrictModel):
    """Loads ``CreativePlan.source`` as the current canvas — the same "no argument of
    its own, the plan's own identity says what" shape ``app.creative3d.spec.CreateScene``
    documents for its own no-arg operation."""

    op: Literal["open"] = "open"


class Inspect(_StrictModel):
    op: Literal["inspect"] = "inspect"


class Draw(_StrictModel):
    op: Literal["draw"] = "draw"
    shape: Literal["line", "rect", "ellipse", "polygon"]
    points: list[Point] = Field(min_length=2, max_length=MAX_POLYGON_POINTS)
    stroke: Color | None = None
    fill: Color | None = None
    stroke_width: int = Field(default=1, ge=1, le=64)

    @field_validator("stroke", "fill")
    @classmethod
    def _colors(cls, value: Color | None, info: Any) -> Color | None:
        return None if value is None else _color_field(value, f"draw.{info.field_name}")

    @model_validator(mode="after")
    def _some_ink(self) -> Draw:
        if self.stroke is None and self.fill is None:
            raise ValueError("draw must set stroke and/or fill")
        return self


class AddText(_StrictModel):
    op: Literal["add_text"] = "add_text"
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    position: Point
    size: int = Field(default=16, ge=1, le=512)
    colour: Color = (0, 0, 0, 255)
    font: Literal["default", "sans", "serif", "mono"] = "default"

    @field_validator("text")
    @classmethod
    def _text_plain(cls, value: str) -> str:
        return _no_control_characters(value, "add_text.text")

    @field_validator("colour")
    @classmethod
    def _colour(cls, value: Color) -> Color:
        return _color_field(value, "add_text.colour")


class Shape(_StrictModel):
    """A box-bounded rect/ellipse/line — the friendlier constructor spec §2 names
    alongside ``draw`` (module comment above :data:`OPERATIONS`)."""

    op: Literal["shape"] = "shape"
    kind: Literal["rect", "ellipse", "line"]
    box: Box
    stroke: Color | None = None
    fill: Color | None = None
    stroke_width: int = Field(default=1, ge=1, le=64)

    @field_validator("stroke", "fill")
    @classmethod
    def _colors(cls, value: Color | None, info: Any) -> Color | None:
        return None if value is None else _color_field(value, f"shape.{info.field_name}")

    @model_validator(mode="after")
    def _some_ink(self) -> Shape:
        if self.stroke is None and self.fill is None:
            raise ValueError("shape must set stroke and/or fill")
        return self


class Transform(_StrictModel):
    op: Literal["transform"] = "transform"
    kind: Literal["rotate", "flip", "scale"]
    #: Degrees for "rotate" ([-360, 360]); ignored for "flip"; the scale FACTOR for
    #: "scale" (bounds enforced by the model validator below, since the legal range
    #: differs per ``kind``).
    value: float = 0.0
    direction: Literal["horizontal", "vertical"] | None = None

    @model_validator(mode="after")
    def _kind_bounds(self) -> Transform:
        if self.kind == "rotate":
            _bounded(self.value, -360.0, 360.0, "transform.value")
        elif self.kind == "flip":
            if self.direction is None:
                raise ValueError("transform kind=flip requires a direction")
        elif self.kind == "scale":
            _bounded(self.value, 0.01, 100.0, "transform.value")
        return self


class ColorAdjust(_StrictModel):
    op: Literal["color_adjust"] = "color_adjust"
    field: Literal["brightness", "contrast", "saturation", "levels"]
    #: A multiplicative factor around 1.0 (Pillow's own ``ImageEnhance`` convention):
    #: 1.0 is unchanged, 0.0 is fully reduced. Bounded generously rather than promising a
    #: real edit is ever this extreme (module docstring's own "runaway request" posture).
    value: float = Field(ge=0.0, le=4.0)


class Crop(_StrictModel):
    op: Literal["crop"] = "crop"
    box: Box


class BackgroundRemove(_StrictModel):
    op: Literal["background_remove"] = "background_remove"
    method: Literal["flood", "threshold"] = "threshold"
    #: The colour-distance tolerance a pixel must be within to count as "background"
    #: (threshold) or "connected to the seed" (flood) — a bounded, deterministic knob,
    #: never a learned/opaque decision (spec §2: "a bounded, deterministic method").
    tolerance: int = Field(default=32, ge=0, le=255)
    seed: Point = (0.0, 0.0)


class Layer(_StrictModel):
    op: Literal["layer"] = "layer"
    action: Literal["add", "merge"]
    name: str = Field(min_length=1, max_length=MAX_LAYER_NAME_CHARS)

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        return _no_control_characters(value, "layer.name")


class Export(_StrictModel):
    op: Literal["export"] = "export"
    format: Literal["png", "jpg", "svg", "pdf", "ora"]
    #: Optional — when omitted, the service derives the name from the plan's own
    #: ``name`` field via :func:`next_output_name` (the naming rule, module docstring).
    #: When given, it is validated the SAME way ``CreativePlan.name`` is: a plain slug,
    #: never a path (refused before any file operation, module docstring).
    path: str | None = Field(default=None, max_length=200)

    @field_validator("path")
    @classmethod
    def _path_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _is_path_shaped(value) or not _SLUG_RE.match(value.rsplit(".", 1)[0]):
            raise ValueError(
                "export.path must be a plain slug-shaped file name, never a path "
                "(a drive letter, a separator or '..' is refused here)"
            )
        return value


# ------------------------------------------------------------------- B43: 489-498


class Generate(_StrictModel):
    """Req 492: an image from the owner's prompt, through the configured provider."""

    op: Literal["generate"] = "generate"
    prompt: str = Field(min_length=1, max_length=1000)
    width: int = Field(default=1024, ge=16, le=MAX_DIMENSION)
    height: int = Field(default=1024, ge=16, le=MAX_DIMENSION)

    @field_validator("prompt")
    @classmethod
    def _plain_prompt(cls, value: str) -> str:
        return _no_control_characters(value, "generate.prompt")


class ObjectRemove(_StrictModel):
    """Req 490: the box's content removed - locally by continuing its surroundings, or
    by the provider when a prompt names what to remove."""

    op: Literal["object_remove"] = "object_remove"
    box: Box
    prompt: str | None = Field(default=None, max_length=500)


class ObjectAdd(_StrictModel):
    """Req 491: a shape, a text, a stored image or (with a prompt) a provider-made object
    placed INTO the box."""

    op: Literal["object_add"] = "object_add"
    box: Box
    kind: Literal["rect", "ellipse", "text", "image", "prompt"] = "rect"
    fill: Color = (0, 0, 0, 255)
    text: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    asset: str | None = Field(default=None, max_length=256)
    prompt: str | None = Field(default=None, max_length=500)

    @field_validator("asset")
    @classmethod
    def _asset_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _is_path_shaped(value) or not _SOURCE_RE.match(value):
            raise ValueError("object_add.asset must be an object-key-shaped reference")
        return value


class Style(_StrictModel):
    """Req 493: a named local style, or a provider style when a prompt is given."""

    op: Literal["style"] = "style"
    kind: Literal["grayscale", "sepia", "posterize", "edges", "invert"] = "grayscale"
    prompt: str | None = Field(default=None, max_length=500)


class Enhance(_StrictModel):
    """Req 494 / 512: autocontrast + unsharp mask (auto), or one of them, or a denoise."""

    op: Literal["enhance"] = "enhance"
    kind: Literal["auto", "sharpen", "denoise", "autocontrast"] = "auto"


class Upscale(_StrictModel):
    """Req 495: Lanczos x2 / x4, bounded by MAX_DIMENSION."""

    op: Literal["upscale"] = "upscale"
    factor: Literal[2, 4] = 2


class SemanticCheck(_StrictModel):
    """Req 498: what the output must SHOW, asked of the vision provider after the run."""

    op: Literal["semantic_check"] = "semantic_check"
    expectation: str = Field(min_length=1, max_length=200)

    @field_validator("expectation")
    @classmethod
    def _plain_expectation(cls, value: str) -> str:
        return _no_control_characters(value, "semantic_check.expectation")


Operation = Annotated[
    New
    | Open
    | Inspect
    | Draw
    | AddText
    | Shape
    | Transform
    | ColorAdjust
    | Crop
    | BackgroundRemove
    | Layer
    | Export
    | Generate
    | ObjectRemove
    | ObjectAdd
    | Style
    | Enhance
    | Upscale
    | SemanticCheck,
    Field(discriminator="op"),
]


# --------------------------------------------------------------------------- the plan


class CreativePlan(_StrictModel):
    tool: Literal[TOOL_PAINT, TOOL_PHOTOSHOP, TOOL_ILLUSTRATOR, TOOL_FIGMA, TOOL_LAYERED]
    #: The base name every output is derived from (the naming rule, module docstring) —
    #: a plain slug, never a path.
    name: str = Field(min_length=1, max_length=64)
    #: An object-store key / documents-root-relative reference to an EXISTING source
    #: image, or ``None`` for a brand-new canvas (the plan's first operation is then
    #: ``new``). Never a raw filesystem path (module docstring).
    source: str | None = Field(default=None, max_length=256)
    operations: list[Operation] = Field(default_factory=list, max_length=MAX_OPERATIONS)
    #: The ONE free-text-shaped field beyond ``add_text.text`` — never spliced into a
    #: driver, carried only into a receipt/ledger row (the same rule
    #: ``app.creative3d.spec.ScenePlan.label`` states for its own family).
    label: str | None = Field(default=None, max_length=MAX_LABEL_CHARS)

    @field_validator("name")
    @classmethod
    def _name_slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError(
                "name must be a lowercase slug ([a-z0-9][a-z0-9-]{0,63}); a path, a "
                "drive letter or an owner project name is refused here"
            )
        return value

    @field_validator("source")
    @classmethod
    def _source_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _is_path_shaped(value) or not _SOURCE_RE.match(value):
            raise ValueError(
                "source must be a plain object-key-shaped reference, never a raw "
                "filesystem path (a drive letter, a separator prefix or '..' is "
                "refused here) — the owner's own projects are never named this way"
            )
        return value

    @field_validator("label")
    @classmethod
    def _label_plain(cls, value: str | None) -> str | None:
        return None if value is None else _no_control_characters(value, "label")

    @model_validator(mode="after")
    def _layer_only_where_supported(self) -> CreativePlan:
        # ADR-0093 decision 2: Paint supports everything except ``layer`` (it has no
        # layer model at all — the document IS the flat bitmap). A plan naming ``layer``
        # for Paint is refused here, before it ever reaches a provider, rather than
        # silently ignored or answered ``capability_missing`` after the fact.
        if self.tool == TOOL_PAINT:
            for op in self.operations:
                if op.op == "layer":
                    raise ValueError("layer is not supported by tool='paint' (no layer model)")
        return self

    @model_validator(mode="after")
    def _crop_and_export_boxes_bounded(self) -> CreativePlan:
        for op in self.operations:
            box = getattr(op, "box", None)
            if box is not None:
                for coord in box:
                    _bounded(float(coord), -COORD_BOUND, COORD_BOUND, f"{op.op}.box")
            points = getattr(op, "points", None)
            if points is not None:
                for point in points:
                    for coord in point:
                        _bounded(float(coord), -COORD_BOUND, COORD_BOUND, f"{op.op}.points")
            position = getattr(op, "position", None)
            if position is not None:
                for coord in position:
                    _bounded(float(coord), -COORD_BOUND, COORD_BOUND, f"{op.op}.position")
            seed = getattr(op, "seed", None)
            if seed is not None:
                for coord in seed:
                    _bounded(float(coord), -COORD_BOUND, COORD_BOUND, f"{op.op}.seed")
        return self

    @model_validator(mode="after")
    def _open_needs_source(self) -> CreativePlan:
        has_open = any(op.op == "open" for op in self.operations)
        if has_open and self.source is None:
            raise ValueError("open requires a non-null source")
        return self

    def plan_json(self) -> str:
        """Canonical JSON: sorted keys, no incidental whitespace — the exact bytes any
        evidence hash is taken over (module docstring)."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_plan_json(cls, text: str) -> CreativePlan:
        return cls.model_validate(json.loads(text))

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# --------------------------------------------------------------------- output naming


def is_path_shaped(value: str) -> bool:
    """Public wrapper for :func:`_is_path_shaped` — the callers outside this module
    (``app.creative.service``) that must refuse a path-shaped/traversing name BEFORE any
    file operation, the same rule this schema enforces on its own fields (module
    docstring)."""
    return _is_path_shaped(value)


def next_output_name(base_name: str, ext: str, *, existing: frozenset[str] = frozenset()) -> str:
    """``<name>-pagentos-<n>.<ext>`` — the ORIGINAL is never overwritten (ADR-0093
    decision 5): ``n`` starts at 1 and increments past every name already in
    ``existing`` (the source's own name, and any output already produced for this
    plan), so a re-run of the same edit never lands on a file that is already there."""
    if is_path_shaped(base_name) or is_path_shaped(f"x.{ext}"):
        raise PlanValidationError(f"refusing a path-shaped output name: {base_name!r}/{ext!r}")
    n = 1
    while True:
        candidate = f"{base_name}-pagentos-{n}.{ext}"
        if candidate not in existing:
            return candidate
        n += 1


__all__ = [
    "BACKGROUND_REMOVE_METHODS",
    "COLOR_ADJUST_FIELDS",
    "DRAW_KINDS",
    "EXPORT_FORMATS",
    "FLIP_DIRECTIONS",
    "FONT_CATALOGUE",
    "LAYER_OPS",
    "MAX_DIMENSION",
    "MAX_LABEL_CHARS",
    "MAX_LAYER_NAME_CHARS",
    "MAX_OPERATIONS",
    "MAX_POLYGON_POINTS",
    "MAX_TEXT_CHARS",
    "OPERATIONS",
    "SHAPE_KINDS",
    "TOOLS",
    "TOOL_FIGMA",
    "TOOL_ILLUSTRATOR",
    "TOOL_PAINT",
    "TOOL_PHOTOSHOP",
    "TRANSFORM_KINDS",
    "AddText",
    "BackgroundRemove",
    "Box",
    "Color",
    "ColorAdjust",
    "CreativePlan",
    "Crop",
    "Draw",
    "Export",
    "Inspect",
    "Layer",
    "New",
    "Open",
    "Operation",
    "PlanValidationError",
    "Point",
    "Shape",
    "Transform",
    "is_path_shaped",
    "next_output_name",
]
