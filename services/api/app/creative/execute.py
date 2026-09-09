"""The Pillow execution of every operation in the CLOSED vocabulary, for Paint
(docs/M27_CREATIVE_TOOLS_SPEC.md §1, §2, ADR-0093 decision 1): "Paint has no scripting
surface: its document/object model IS the bitmap. So a Paint edit is performed on the
FILE with Pillow — deterministic, inspectable, testable."

This module never imports ``app.creative.providers`` or touches ``mspaint.exe`` — it is
pure image processing over a :class:`app.creative.spec.CreativePlan`, given the SOURCE
bytes (or ``None`` for a brand-new canvas) and returning the produced bytes plus an
INSPECTION (module docstring's own "deterministic, inspectable" — the same "the
inspection, never the plan, is what the comparison reads back" discipline
``app.creative3d.drivers.blender_driver.build_inspection`` already established for M25).

No text is ever drawn from anything but ``AddText.text`` as PIXELS — there is no code
path from an operation's string fields to anything executed (ADR-0093 decision 6).
"""

from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont, ImageOps

from app.creative.spec import (
    AddText,
    BackgroundRemove,
    ColorAdjust,
    CreativePlan,
    Crop,
    Draw,
    Export,
    Inspect,
    Layer,
    New,
    Open,
    Shape,
    Transform,
    next_output_name,
)

MAX_SOURCE_BYTES = 50 * 1024 * 1024


class ExecutionError(RuntimeError):
    """The source image could not even be opened — never raised for an operation that
    merely names something not found in the canvas (that is an ``errors[]`` entry,
    exactly the defensive posture ``app.creative3d.drivers.blender_driver`` already
    established for its own operations)."""


@dataclass(slots=True)
class DrawnShape:
    kind: str
    box: tuple[float, float, float, float] | None = None
    points: list[tuple[float, float]] | None = None
    fill: tuple[int, int, int, int] | None = None
    stroke: tuple[int, int, int, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "box": list(self.box) if self.box else None,
            "points": [list(p) for p in self.points] if self.points else None,
            "fill": list(self.fill) if self.fill else None,
            "stroke": list(self.stroke) if self.stroke else None,
        }


@dataclass(slots=True)
class DrawnText:
    text: str
    position: tuple[float, float]
    size: int
    colour: tuple[int, int, int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "position": list(self.position),
            "size": self.size,
            "colour": list(self.colour),
        }


@dataclass(slots=True)
class ExecutionResult:
    image_bytes: bytes
    width: int
    height: int
    format: str
    drawn_shapes: list[DrawnShape] = field(default_factory=list)
    drawn_texts: list[DrawnText] = field(default_factory=list)
    background_removed: bool = False
    output_name: str | None = None
    errors: list[str] = field(default_factory=list)

    def inspection(self) -> dict[str, Any]:
        """The independent-reader-shaped read-back: every fact
        ``app.creative.compare`` checks a plan's constraints against, and NOTHING the
        plan itself said restated (module docstring)."""
        return {
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "shapes": [s.as_dict() for s in self.drawn_shapes],
            "texts": [t.as_dict() for t in self.drawn_texts],
            "background_removed": self.background_removed,
            "output_name": self.output_name,
            "errors": list(self.errors),
        }


def _open_source(source_bytes: bytes) -> Image.Image:
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise ExecutionError(f"source image exceeds {MAX_SOURCE_BYTES} bytes")
    try:
        img = Image.open(io.BytesIO(source_bytes))
        img.load()
    except Exception as exc:  # noqa: BLE001 - an unreadable source is a hard refusal
        raise ExecutionError(f"source image could not be decoded: {exc}") from exc
    return img.convert("RGBA")


def _apply_new(op: New) -> Image.Image:
    return Image.new("RGBA", (op.width, op.height), tuple(op.background))


def _apply_draw(canvas: Image.Image, op: Draw, drawn: list[DrawnShape]) -> None:
    drawer = ImageDraw.Draw(canvas)
    points = [tuple(p) for p in op.points]
    fill = tuple(op.fill) if op.fill else None
    stroke = tuple(op.stroke) if op.stroke else None
    if op.shape == "line":
        drawer.line(points, fill=stroke or fill, width=op.stroke_width)
    elif op.shape == "rect":
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        box = (min(xs), min(ys), max(xs), max(ys))
        drawer.rectangle(box, fill=fill, outline=stroke, width=op.stroke_width)
    elif op.shape == "ellipse":
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        box = (min(xs), min(ys), max(xs), max(ys))
        drawer.ellipse(box, fill=fill, outline=stroke, width=op.stroke_width)
    elif op.shape == "polygon":
        drawer.polygon(points, fill=fill, outline=stroke)
    drawn.append(DrawnShape(kind=op.shape, points=points, fill=fill, stroke=stroke))


def _apply_shape(canvas: Image.Image, op: Shape, drawn: list[DrawnShape]) -> None:
    drawer = ImageDraw.Draw(canvas)
    box = tuple(op.box)
    fill = tuple(op.fill) if op.fill else None
    stroke = tuple(op.stroke) if op.stroke else None
    if op.kind == "rect":
        drawer.rectangle(box, fill=fill, outline=stroke, width=op.stroke_width)
    elif op.kind == "ellipse":
        drawer.ellipse(box, fill=fill, outline=stroke, width=op.stroke_width)
    elif op.kind == "line":
        drawer.line(list(box), fill=stroke or fill, width=op.stroke_width)
    drawn.append(DrawnShape(kind=op.kind, box=box, fill=fill, stroke=stroke))


#: Pillow ships no bundled TrueType font, so every catalogue entry (spec §2's own
#: closed, bounded font list — never an arbitrary owner/model string resolved to a
#: filesystem path) maps to Pillow's own built-in bitmap font at the requested size.
#: Pillow 10+'s ``load_default(size=...)`` scales it; a distinct family per catalogue
#: entry is future work once real font files are bundled — never a defect the owner can
#: be misled by (the family name is not measured or claimed anywhere; only the pixels
#: this function actually draws are).
def _font_for(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - older Pillow without the size kwarg
        return ImageFont.load_default()


def _apply_add_text(canvas: Image.Image, op: AddText, drawn: list[DrawnText]) -> None:
    drawer = ImageDraw.Draw(canvas)
    font = _font_for(op.size)
    drawer.text(tuple(op.position), op.text, fill=tuple(op.colour), font=font)
    drawn.append(
        DrawnText(text=op.text, position=tuple(op.position), size=op.size, colour=tuple(op.colour))
    )


def _apply_transform(canvas: Image.Image, op: Transform) -> Image.Image:
    if op.kind == "rotate":
        return canvas.rotate(op.value, expand=True)
    if op.kind == "flip":
        if op.direction == "horizontal":
            return canvas.transpose(Image.FLIP_LEFT_RIGHT)
        return canvas.transpose(Image.FLIP_TOP_BOTTOM)
    # scale
    new_size = (max(1, round(canvas.width * op.value)), max(1, round(canvas.height * op.value)))
    return canvas.resize(new_size)


def _apply_color_adjust(canvas: Image.Image, op: ColorAdjust) -> Image.Image:
    if op.field == "brightness":
        return ImageEnhance.Brightness(canvas).enhance(op.value)
    if op.field == "contrast":
        return ImageEnhance.Contrast(canvas).enhance(op.value)
    if op.field == "saturation":
        return ImageEnhance.Color(canvas).enhance(op.value)
    # "levels": a bounded autocontrast stretch — deterministic and Pillow-only (ADR-0093
    # decision 4), scaled by ``value`` (0..4) into a 0..49% cutoff ``ImageOps.autocontrast``
    # accepts, never an unbounded/opaque transform.
    cutoff = max(0, min(49, round(op.value * 12)))
    rgb = canvas.convert("RGB")
    stretched = ImageOps.autocontrast(rgb, cutoff=cutoff)
    stretched = stretched.convert("RGBA")
    stretched.putalpha(canvas.getchannel("A"))
    return stretched


def _apply_crop(canvas: Image.Image, op: Crop) -> Image.Image:
    x0, y0, x1, y1 = op.box
    x0 = max(0, min(canvas.width, round(x0)))
    y0 = max(0, min(canvas.height, round(y0)))
    x1 = max(x0 + 1, min(canvas.width, round(x1)))
    y1 = max(y0 + 1, min(canvas.height, round(y1)))
    return canvas.crop((x0, y0, x1, y1))


def _color_distance(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b, strict=True)))


def _apply_background_remove(canvas: Image.Image, op: BackgroundRemove) -> Image.Image:
    """A bounded, deterministic method (spec §2) — never a learned/opaque model.
    ``threshold``: every pixel within ``tolerance`` colour-distance of the seed pixel's
    RGB becomes transparent. ``flood``: the same test, but restricted to the
    seed-connected region (Pillow's own ``ImageDraw.floodfill``, which already performs
    exactly this connected-region walk)."""
    out = canvas.copy()
    seed_x = max(0, min(canvas.width - 1, round(op.seed[0])))
    seed_y = max(0, min(canvas.height - 1, round(op.seed[1])))
    seed_rgb = canvas.convert("RGB").getpixel((seed_x, seed_y))
    if op.method == "flood":
        rgb = out.convert("RGB")
        ImageDraw.floodfill(rgb, (seed_x, seed_y), (0, 0, 0), thresh=op.tolerance)
        # floodfill marks the connected region black; recover WHICH pixels changed by
        # diffing against the original RGB, then zero their alpha — deterministic,
        # Pillow-only (module docstring), no numpy anywhere in this file.
        diff = ImageChops.difference(canvas.convert("RGB"), rgb)
        mask = diff.convert("L").point(lambda p: 255 if p > 0 or (seed_rgb == (0, 0, 0)) else 0)
        alpha = out.getchannel("A")
        alpha = ImageChops.subtract(alpha, mask)
        out.putalpha(alpha)
        return out
    # threshold
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if _color_distance((r, g, b), seed_rgb) <= op.tolerance:
                px[x, y] = (r, g, b, 0)
    return out


def _export_bytes(canvas: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    if fmt == "png":
        canvas.save(buf, format="PNG")
    elif fmt == "jpg":
        canvas.convert("RGB").save(buf, format="JPEG")
    elif fmt == "pdf":
        canvas.convert("RGB").save(buf, format="PDF")
    elif fmt == "svg":
        # Pillow produces raster images only; Paint's own document model is a bitmap
        # (ADR-0093 decision 1), so "export as svg" from Paint is an HONEST vector
        # WRAPPER around the real raster bytes — never a claim of real vector paths —
        # rather than a silent substitution of a different format.
        png = io.BytesIO()
        canvas.save(png, format="PNG")
        encoded = base64.b64encode(png.getvalue()).decode("ascii")
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas.width}" '
            f'height="{canvas.height}"><image width="{canvas.width}" '
            f'height="{canvas.height}" xlink:href="data:image/png;base64,{encoded}"/></svg>'
        )
        return svg.encode("utf-8")
    else:  # pragma: no cover - the schema's Literal already excludes this
        raise ExecutionError(f"unknown export format {fmt!r}")
    return buf.getvalue()


def execute(plan: CreativePlan, source_bytes: bytes | None) -> ExecutionResult:
    """Runs every operation in ``plan.operations`` in order over an in-memory Pillow
    canvas, returning the produced bytes and the read-back inspection
    (:meth:`ExecutionResult.inspection`). Never raises for an operation that merely
    names something invalid at RUNTIME (recorded in ``errors[]`` instead, the same
    defensive posture ``app.creative3d.drivers.blender_driver.apply_operations``
    already established) — only :class:`ExecutionError` for a source image that could
    not even be opened, which the caller (``app.creative.service``) turns into a
    refused receipt before anything is stored."""
    canvas: Image.Image | None = None
    errors: list[str] = []
    drawn_shapes: list[DrawnShape] = []
    drawn_texts: list[DrawnText] = []
    background_removed = False
    export_format = "png"
    output_name: str | None = None
    final_bytes: bytes | None = None

    for op in plan.operations:
        try:
            if isinstance(op, New):
                canvas = _apply_new(op)
            elif isinstance(op, Open):
                if source_bytes is None:
                    errors.append("open: no source bytes provided")
                    continue
                canvas = _open_source(source_bytes)
            elif isinstance(op, Inspect):
                continue  # the inspection is always produced, at the end, regardless.
            elif canvas is None:
                errors.append(f"{op.op}: no canvas yet (missing 'new' or 'open')")
            elif isinstance(op, Draw):
                _apply_draw(canvas, op, drawn_shapes)
            elif isinstance(op, Shape):
                _apply_shape(canvas, op, drawn_shapes)
            elif isinstance(op, AddText):
                _apply_add_text(canvas, op, drawn_texts)
            elif isinstance(op, Transform):
                canvas = _apply_transform(canvas, op)
            elif isinstance(op, ColorAdjust):
                canvas = _apply_color_adjust(canvas, op)
            elif isinstance(op, Crop):
                canvas = _apply_crop(canvas, op)
            elif isinstance(op, BackgroundRemove):
                canvas = _apply_background_remove(canvas, op)
                background_removed = True
            elif isinstance(op, Layer):
                # Paint carries no layer model (``CreativePlan`` already refuses this
                # op for tool="paint" before it ever reaches here); kept only so a
                # tampered/foreign plan fails loudly, never silently — the same
                # defensive line ``blender_driver.apply_operations`` keeps for its own
                # Unity-only op.
                errors.append("layer: not supported by the Paint executor")
            elif isinstance(op, Export):
                if canvas is None:
                    errors.append("export: no canvas to export")
                    continue
                export_format = op.format
                output_name = op.path or next_output_name(plan.name, op.format)
                final_bytes = _export_bytes(canvas, export_format)
        except ExecutionError:
            # A source image that could not even be opened is a hard refusal the
            # caller (``app.creative.service``) turns into a refused receipt BEFORE
            # anything is stored — never downgraded to a soft ``errors[]`` entry the
            # way a runtime-only problem with a single operation is (module
            # docstring).
            raise
        except Exception as exc:  # noqa: BLE001 - captured as data, never crashes the run
            errors.append(f"{op.op}: {exc}")

    if canvas is None:
        canvas = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        errors.append("plan produced no canvas")
    if final_bytes is None:
        final_bytes = _export_bytes(canvas, "png")

    return ExecutionResult(
        image_bytes=final_bytes,
        width=canvas.width,
        height=canvas.height,
        format=export_format,
        drawn_shapes=drawn_shapes,
        drawn_texts=drawn_texts,
        background_removed=background_removed,
        output_name=output_name,
        errors=errors,
    )


__all__ = ["DrawnShape", "DrawnText", "ExecutionError", "ExecutionResult", "execute"]
