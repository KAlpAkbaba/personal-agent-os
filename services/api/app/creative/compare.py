"""The closed loop's own comparison (docs/M27_CREATIVE_TOOLS_SPEC.md §3, §4, ADR-0093
decision 4): the plan's constraints vs. what an INDEPENDENT re-open of the produced
bytes actually shows.

ADR-0093 decision 4, verbatim: "the spec's own draft said 'SSIM via PIL+numpy'. numpy
is NOT a dependency of this repository ... This module therefore measures: exact
dimensions; per-TILE mean colour distance over a fixed grid; the presence of asked-for
shapes and text by colour masks over their planned regions; and a bounded aggregate
similarity derived from those tiles. That aggregate is **not SSIM** and is not called
SSIM anywhere in the code, the receipts or the gate."

Every function in this module uses ``PIL`` alone — no ``numpy`` import anywhere, by
policy, not merely by accident (a test asserts this file never imports it). A match is
claimed only when ``checked > 0`` and every individual constraint passed — the M24/M25
lesson restated for M27 by name: never report a match over an empty comparison
(:data:`NO_CONSTRAINTS`).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageStat

from app.creative.spec import AddText, CreativePlan, Draw, Shape

#: The reason a comparison gives when the plan asked for nothing checkable — never a
#: match (the M24/M25 defect this module refuses) and never a disagreement either.
NO_CONSTRAINTS = "no_constraints"

#: Tolerances. Generous enough to survive PNG round-tripping and Pillow's own
#: antialiasing without hiding a real mismatch.
COLOR_TOLERANCE = 40.0
DIMENSION_TOLERANCE = 0
#: The fixed grid the tiled comparison is measured over (decision 4's own "a fixed
#: grid") — never derived from the image size, so two runs of the same plan are always
#: compared on the identically-shaped grid.
TILE_GRID = (8, 8)
#: The bounded aggregate similarity (decision 4) below which a reference comparison
#: counts as a mismatch — 0.0 (nothing alike) .. 1.0 (every tile within tolerance).
SIMILARITY_MIN = 0.6

RENDER_MIN_BYTES = 64
RENDER_MAX_WIDTH = 8192
RENDER_MAX_HEIGHT = 8192
RENDER_MAX_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One named defect — always names WHAT and WHERE, never a bare "mismatch" (spec
    §3: "a mismatch names the object and the axis")."""

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
    similarity: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "mismatches": [m.as_dict() for m in self.mismatches],
            "reason": self.reason,
            "similarity": self.similarity,
        }


def check_output(image_bytes: bytes | None) -> Mismatch | None:
    """An INDEPENDENT reader opens the produced bytes and measures that they are a
    real, non-trivial, in-bounds image (spec §7) — never trusting the executor's own
    claim that it produced something. Returns ``None`` on a genuine output; a
    :class:`Mismatch` naming the named defect (wrong size / empty output) otherwise."""
    if image_bytes is None:
        return Mismatch("output", "output", "present", None, "no output bytes provided")
    if len(image_bytes) < RENDER_MIN_BYTES:
        return Mismatch(
            "output", "output.bytes", f">= {RENDER_MIN_BYTES}", len(image_bytes), "empty output"
        )
    if len(image_bytes) > RENDER_MAX_BYTES:
        return Mismatch(
            "output",
            "output.bytes",
            f"<= {RENDER_MAX_BYTES}",
            len(image_bytes),
            "the output exceeds the size bound",
        )
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.load()
            width, height = img.size
            rgba = img.convert("RGBA")
            alpha_extrema = rgba.getchannel("A").getextrema()
    except Exception as exc:  # noqa: BLE001 - an undecodable "output" is a mismatch
        return Mismatch(
            "output", "output", "a decodable image", None, f"PIL could not open it: {exc}"
        )
    if width > RENDER_MAX_WIDTH or height > RENDER_MAX_HEIGHT:
        return Mismatch(
            "output",
            "output.size",
            f"<= {RENDER_MAX_WIDTH}x{RENDER_MAX_HEIGHT}",
            f"{width}x{height}",
            "the output is larger than the bound",
        )
    # A genuinely EMPTY output (spec §3's own named defect) means every pixel is fully
    # transparent — never "the whole canvas happens to be one solid colour", which is a
    # perfectly legitimate Paint canvas (a fresh "new" with a plain background) and was
    # wrongly rejected here before this was found by this module's own test suite: the
    # 3D-render non-uniformity heuristic ``app.creative3d.compare.check_render`` uses
    # makes sense for a RENDERED SCENE (a blank frame implies no camera/light fired) but
    # not for a flat 2D raster canvas, where uniform colour is normal, not a failure.
    if alpha_extrema == (0, 0):
        return Mismatch(
            "output", "output.alpha", "some opaque pixel", "fully transparent", "an empty output"
        )
    return None


def _tiles(image: Image.Image, grid: tuple[int, int]) -> list[Image.Image]:
    cols, rows = grid
    tw = max(1, image.width // cols)
    th = max(1, image.height // rows)
    out: list[Image.Image] = []
    for row in range(rows):
        for col in range(cols):
            box = (
                col * tw,
                row * th,
                min(image.width, (col + 1) * tw),
                min(image.height, (row + 1) * th),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            out.append(image.crop(box))
    return out


def _mean_rgb(tile: Image.Image) -> tuple[float, float, float]:
    stat = ImageStat.Stat(tile.convert("RGB"))
    return (stat.mean[0], stat.mean[1], stat.mean[2])


def tiled_similarity(
    produced: Image.Image, reference: Image.Image, *, grid: tuple[int, int] = TILE_GRID
) -> float:
    """Per-TILE mean colour distance over a FIXED grid (ADR-0093 decision 4) — resizes
    ``reference`` to ``produced``'s own size first (a fair per-tile comparison needs the
    same grid geometry on both sides), then returns the bounded aggregate: the fraction
    of tiles whose mean RGB is within :data:`COLOR_TOLERANCE` of the same tile in
    ``reference``. **This is not SSIM** (module docstring) — it is a plain per-tile
    colour-distance vote, and it is never called anything else."""
    if reference.size != produced.size:
        reference = reference.resize(produced.size)
    produced_tiles = _tiles(produced, grid)
    reference_tiles = _tiles(reference, grid)
    if not produced_tiles:
        return 0.0
    within = 0
    for p_tile, r_tile in zip(produced_tiles, reference_tiles, strict=True):
        distance = math.sqrt(
            sum((a - b) ** 2 for a, b in zip(_mean_rgb(p_tile), _mean_rgb(r_tile), strict=True))
        )
        if distance <= COLOR_TOLERANCE:
            within += 1
    return within / len(produced_tiles)


def _region_contains_color(
    image: Image.Image, box: tuple[float, float, float, float], color: tuple[int, int, int, int]
) -> bool:
    """True when at least one pixel inside ``box`` (clamped to the image) is within
    :data:`COLOR_TOLERANCE` of ``color`` — a colour-MASK presence check over the
    op's own PLANNED region (ADR-0093 decision 4: "the presence of asked-for shapes and
    text by colour masks over their planned regions"), never a full-image search that
    could match the wrong object."""
    x0, y0, x1, y1 = box
    x0 = max(0, min(image.width - 1, round(min(x0, x1))))
    y0 = max(0, min(image.height - 1, round(min(y0, y1))))
    x1 = max(x0 + 1, min(image.width, round(max(x0, x1)) + 1))
    y1 = max(y0 + 1, min(image.height, round(max(y0, y1)) + 1))
    region = image.convert("RGB").crop((x0, y0, x1, y1))
    target = color[:3]
    px = region.load()
    for y in range(region.height):
        for x in range(region.width):
            if _color_distance(px[x, y], target) <= COLOR_TOLERANCE:
                return True
    return False


def _color_distance(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b, strict=True)))


def _op_box(op: Draw | Shape) -> tuple[float, float, float, float]:
    if isinstance(op, Shape):
        return tuple(op.box)  # type: ignore[return-value]
    xs = [p[0] for p in op.points]
    ys = [p[1] for p in op.points]
    return (min(xs), min(ys), max(xs), max(ys))


def compare(
    plan: CreativePlan,
    inspection: dict[str, Any],
    *,
    image_bytes: bytes | None,
    reference_bytes: bytes | None = None,
) -> CompareResult:
    """Every requested constraint the plan named, checked against an INDEPENDENT
    re-open of ``image_bytes`` — never against the plan's own numbers restated, and
    never against ``inspection`` alone (``inspection`` is the executor's own claim;
    this function re-decodes the bytes itself for the pixel-level checks, the same
    "the check is not a restatement of the thing being checked" discipline
    ``app.creative3d.compare.forward_vector`` documents for its own camera-aim check).

    ``checked`` counts how many individual constraints were actually evaluated; ``ok``
    is true only when ``checked > 0`` and every one of them passed (module docstring)."""
    mismatches: list[Mismatch] = []
    checked = 0

    output_mismatch = check_output(image_bytes)
    if output_mismatch is not None:
        return CompareResult(ok=False, checked=1, mismatches=(output_mismatch,))

    assert image_bytes is not None
    with Image.open(io.BytesIO(image_bytes)) as raw:
        raw.load()
        produced = raw.convert("RGBA") if raw.mode != "RGB" else raw.convert("RGBA")

    # 1. exact dimensions — only checkable when the plan itself stated a canvas size
    # ("new"). A source-derived canvas with no explicit resize has no independently
    # knowable expected size, so this check is skipped rather than restating whatever
    # the executor happened to produce.
    new_ops = [op for op in plan.operations if op.op == "new"]
    has_scale_or_crop = any(op.op in ("crop",) for op in plan.operations) or any(
        op.op == "transform" and getattr(op, "kind", None) in ("scale", "rotate")
        for op in plan.operations
    )
    if new_ops and not has_scale_or_crop:
        expected_w, expected_h = new_ops[-1].width, new_ops[-1].height
        checked += 1
        if (produced.width, produced.height) != (expected_w, expected_h):
            mismatches.append(
                Mismatch(
                    "canvas",
                    "size",
                    f"{expected_w}x{expected_h}",
                    f"{produced.width}x{produced.height}",
                    "dimension mismatch",
                )
            )

    # 2. presence of asked-for shapes/text by colour mask over their planned region.
    for index, op in enumerate(plan.operations):
        if isinstance(op, Draw | Shape):
            checked += 1
            color = op.fill or op.stroke
            assert color is not None
            box = _op_box(op)
            if not _region_contains_color(produced, box, tuple(color)):
                mismatches.append(
                    Mismatch(
                        f"{op.op}[{index}]",
                        "presence",
                        list(color),
                        "not found",
                        f"no pixel within tolerance of {tuple(color)} in the planned region",
                    )
                )
        elif isinstance(op, AddText):
            checked += 1
            # A text region estimate: the position plus a bounded box sized from the
            # font size and the string length — generous, never exact glyph metrics,
            # since this check only asks "was ink of this colour put down here", never
            # "is the glyph shape correct" (spec §3's own "presence", not OCR).
            x, y = op.position
            width_estimate = max(8.0, len(op.text) * op.size * 0.7)
            height_estimate = max(8.0, op.size * 1.5)
            box = (x, y, x + width_estimate, y + height_estimate)
            if not _region_contains_color(produced, box, tuple(op.colour)):
                mismatches.append(
                    Mismatch(
                        f"add_text[{index}]",
                        "presence",
                        list(op.colour),
                        "not found",
                        f"no ink within tolerance of {tuple(op.colour)} near the planned position",
                    )
                )

    background_removed = any(op.op == "background_remove" for op in plan.operations)
    if background_removed:
        checked += 1
        if produced.mode != "RGBA" or produced.getchannel("A").getextrema()[0] == 255:
            mismatches.append(
                Mismatch(
                    "background",
                    "alpha",
                    "some transparency",
                    "none",
                    "background_remove requested but no pixel was made transparent",
                )
            )

    # 3. the tiled reference comparison — only when a reference image was actually
    # supplied (a redraw/"make it look like this" plan); its own bounded aggregate is
    # carried on the result but only fails the comparison below SIMILARITY_MIN.
    similarity: float | None = None
    if reference_bytes is not None:
        checked += 1
        with Image.open(io.BytesIO(reference_bytes)) as raw_ref:
            raw_ref.load()
            reference = raw_ref.convert("RGBA")
        similarity = tiled_similarity(produced, reference)
        if similarity < SIMILARITY_MIN:
            mismatches.append(
                Mismatch(
                    "reference",
                    "similarity",
                    f">= {SIMILARITY_MIN}",
                    round(similarity, 3),
                    "the tiled colour-distance comparison against the reference is too low",
                )
            )

    if checked == 0:
        return CompareResult(ok=False, checked=0, mismatches=(), reason=NO_CONSTRAINTS)
    return CompareResult(
        ok=not mismatches, checked=checked, mismatches=tuple(mismatches), similarity=similarity
    )


__all__ = [
    "COLOR_TOLERANCE",
    "DIMENSION_TOLERANCE",
    "NO_CONSTRAINTS",
    "RENDER_MAX_BYTES",
    "RENDER_MAX_HEIGHT",
    "RENDER_MAX_WIDTH",
    "RENDER_MIN_BYTES",
    "SIMILARITY_MIN",
    "TILE_GRID",
    "CompareResult",
    "Mismatch",
    "check_output",
    "compare",
    "tiled_similarity",
]
