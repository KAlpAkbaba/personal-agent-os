"""Layers, PSD, OpenRaster and SVG (B43 req 506-508) - the in-house LAYERED document.

* :class:`LayeredDocument`: named RGBA layers with an offset, visibility and opacity, a
  ``flatten()`` that composites them in order - the document model behind the
  ``layered`` tool (Paint's stays a flat bitmap, ADR-0093 decision 2).
* PSD: READ through Pillow's own PSD plugin (composite + every layer with its bbox).
  Pillow cannot WRITE PSD, and this repository ships no second image stack; a layered
  document is written as OpenRaster (``.ora`` - a zip with ``stack.xml``, one PNG per
  layer and the merged image, the open format GIMP and Krita read) plus its flattened
  PNG. The PSD limitation is stated, never papered over.
* SVG: WRITTEN as a real vector document for what IS vector - the shapes and texts the
  plan drew, each an ``<rect>``/``<ellipse>``/``<line>``/``<polygon>``/``<text>`` - with raster
  layers as ``<image>`` elements; READ for the same closed element set, into plan
  operations (``shape``/``draw``/``add_text``) the executor already knows.
"""

from __future__ import annotations

import base64
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any, Final

from PIL import Image

MAX_LAYERS: Final = 64
MAX_SVG_ELEMENTS: Final = 256
ORA_MIMETYPE: Final = "image/openraster"
SVG_NS: Final = "http://www.w3.org/2000/svg"
XLINK_NS: Final = "http://www.w3.org/1999/xlink"


class LayerError(ValueError):
    pass


@dataclass(slots=True)
class Layer:
    name: str
    image: Image.Image
    offset: tuple[int, int] = (0, 0)
    visible: bool = True
    opacity: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "width": self.image.width,
            "height": self.image.height,
            "offset": list(self.offset),
            "visible": self.visible,
            "opacity": self.opacity,
        }


@dataclass(slots=True)
class LayeredDocument:
    width: int
    height: int
    layers: list[Layer] = field(default_factory=list)

    def add(
        self, name: str, image: Image.Image | None = None, *, offset: tuple[int, int] = (0, 0)
    ) -> Layer:
        if len(self.layers) >= MAX_LAYERS:
            raise LayerError(f"at most {MAX_LAYERS} layers")
        if any(layer.name == name for layer in self.layers):
            raise LayerError(f"layer {name!r} already exists")
        layer = Layer(
            name,
            (image or Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))).convert("RGBA"),
            offset,
        )
        self.layers.append(layer)
        return layer

    def get(self, name: str) -> Layer:
        for layer in self.layers:
            if layer.name == name:
                return layer
        raise LayerError(f"no layer named {name!r}")

    def merge(self, name: str) -> Layer:
        """Merge the named layer DOWN into the one below it (a top or only layer merges
        into a new base)."""
        index = next((i for i, layer in enumerate(self.layers) if layer.name == name), -1)
        if index < 0:
            raise LayerError(f"no layer named {name!r}")
        upper = self.layers.pop(index)
        if index == 0:
            base = Layer(upper.name, Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0)))
            self.layers.insert(0, base)
            index = 1
        lower = self.layers[index - 1]
        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        canvas.alpha_composite(lower.image, lower.offset)
        if upper.visible:
            canvas.alpha_composite(_with_opacity(upper), upper.offset)
        lower.image = canvas
        lower.offset = (0, 0)
        return lower

    def flatten(self) -> Image.Image:
        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        for layer in self.layers:
            if layer.visible:
                canvas.alpha_composite(_with_opacity(layer), layer.offset)
        return canvas

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "layers": [layer.as_dict() for layer in self.layers],
        }


def _with_opacity(layer: Layer) -> Image.Image:
    if layer.opacity >= 1.0:
        return layer.image
    alpha = layer.image.getchannel("A").point(lambda a: int(a * max(0.0, min(1.0, layer.opacity))))
    out = layer.image.copy()
    out.putalpha(alpha)
    return out


# ----------------------------------------------------------------------------- PSD


def from_psd(data: bytes) -> LayeredDocument:
    """Every layer of a PSD (name, bbox, pixels) through Pillow's PSD plugin; the
    composite is layer zero when the file carries no layers."""
    try:
        im = Image.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise LayerError(f"not a readable PSD: {exc}") from exc
    if im.format != "PSD":
        raise LayerError(f"not a PSD ({im.format})")
    doc = LayeredDocument(im.width, im.height)
    layers = list(getattr(im, "layers", []) or [])
    if not layers:
        doc.add("composite", im.convert("RGBA"))
        return doc
    for index, entry in enumerate(layers[:MAX_LAYERS]):
        name, _mode, bbox = entry[0], entry[1], entry[2]
        try:
            im.seek(index + 1)
            pixels = im.convert("RGBA")
        except Exception:  # noqa: BLE001 - a layer Pillow cannot decode is an empty one
            pixels = Image.new(
                "RGBA", (max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])), (0, 0, 0, 0)
            )
        label = str(name or f"layer-{index + 1}")
        if any(layer.name == label for layer in doc.layers):
            label = f"{label}-{index + 1}"
        doc.add(label, pixels, offset=(int(bbox[0]), int(bbox[1])))
    return doc


# ----------------------------------------------------------------------- OpenRaster


def to_ora(doc: LayeredDocument) -> bytes:
    """OpenRaster: the layered interchange format Pillow can WRITE the pieces of."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), ORA_MIMETYPE, compress_type=zipfile.ZIP_STORED)
        stack = ET.Element("image", {"version": "0.0.3", "w": str(doc.width), "h": str(doc.height)})
        stack_el = ET.SubElement(stack, "stack")
        for index, layer in enumerate(reversed(doc.layers)):
            src = f"data/{len(doc.layers) - index:03d}-{_slug(layer.name)}.png"
            ET.SubElement(
                stack_el,
                "layer",
                {
                    "name": layer.name,
                    "src": src,
                    "x": str(layer.offset[0]),
                    "y": str(layer.offset[1]),
                    "opacity": f"{layer.opacity:.3f}",
                    "visibility": "visible" if layer.visible else "hidden",
                },
            )
            zf.writestr(src, _png_bytes(layer.image))
        zf.writestr("stack.xml", ET.tostring(stack, encoding="unicode", xml_declaration=True))
        merged = doc.flatten()
        zf.writestr("mergedimage.png", _png_bytes(merged))
        thumb = merged.copy()
        thumb.thumbnail((256, 256))
        zf.writestr("Thumbnails/thumbnail.png", _png_bytes(thumb))
    return buf.getvalue()


def from_ora(data: bytes) -> LayeredDocument:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise LayerError("not an OpenRaster file") from exc
    with zf:
        if zf.read("mimetype").decode("ascii", "replace").strip() != ORA_MIMETYPE:
            raise LayerError("not an OpenRaster file (mimetype)")
        root = ET.fromstring(zf.read("stack.xml"))
        doc = LayeredDocument(int(root.get("w") or 0), int(root.get("h") or 0))
        entries = list(root.iter("layer"))
        for element in reversed(entries[:MAX_LAYERS]):
            src = element.get("src") or ""
            image = Image.open(io.BytesIO(zf.read(src))).convert("RGBA")
            layer = doc.add(
                element.get("name") or src,
                image,
                offset=(int(element.get("x") or 0), int(element.get("y") or 0)),
            )
            layer.opacity = float(element.get("opacity") or 1.0)
            layer.visible = (element.get("visibility") or "visible") != "hidden"
    return doc


# ----------------------------------------------------------------------------- SVG


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "layer"


def _png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _rgba(color: Any) -> str:
    try:
        r, g, b, a = (int(c) for c in color)
    except (TypeError, ValueError):
        return "rgba(0,0,0,1)"
    return f"rgba({r},{g},{b},{a / 255:.3f})"


def to_svg(
    *,
    width: int,
    height: int,
    shapes: list[dict[str, Any]] | None = None,
    texts: list[dict[str, Any]] | None = None,
    raster_layers: list[Layer] | None = None,
) -> bytes:
    """A REAL vector document: the drawn shapes and texts as SVG elements, raster layers
    (if any) as embedded PNG images. What was never vector is never claimed to be."""
    root = ET.Element(
        "svg",
        {
            "xmlns": SVG_NS,
            "xmlns:xlink": XLINK_NS,
            "width": str(width),
            "height": str(height),
            "viewBox": f"0 0 {width} {height}",
        },
    )
    for layer in raster_layers or []:
        if not layer.visible:
            continue
        encoded = base64.b64encode(_png_bytes(layer.image)).decode("ascii")
        ET.SubElement(
            root,
            "image",
            {
                "x": str(layer.offset[0]),
                "y": str(layer.offset[1]),
                "width": str(layer.image.width),
                "height": str(layer.image.height),
                "opacity": f"{layer.opacity:.3f}",
                "xlink:href": f"data:image/png;base64,{encoded}",
                "data-layer": layer.name,
            },
        )
    for shape in (shapes or [])[:MAX_SVG_ELEMENTS]:
        kind = str(shape.get("kind") or "")
        fill = _rgba(shape.get("fill")) if shape.get("fill") is not None else "none"
        stroke = _rgba(shape.get("stroke")) if shape.get("stroke") is not None else "none"
        box = shape.get("box")
        if not box and shape.get("points") and kind in ("rect", "ellipse", "line"):
            pts = [(float(p[0]), float(p[1])) for p in shape["points"]]
            box = [
                min(p[0] for p in pts),
                min(p[1] for p in pts),
                max(p[0] for p in pts),
                max(p[1] for p in pts),
            ]
            if kind == "line":
                box = [pts[0][0], pts[0][1], pts[-1][0], pts[-1][1]]
        if kind == "rect" and box:
            x0, y0, x1, y1 = (float(v) for v in box)
            ET.SubElement(
                root,
                "rect",
                {
                    "x": str(x0),
                    "y": str(y0),
                    "width": str(x1 - x0),
                    "height": str(y1 - y0),
                    "fill": fill,
                    "stroke": stroke,
                },
            )
        elif kind == "ellipse" and box:
            x0, y0, x1, y1 = (float(v) for v in box)
            ET.SubElement(
                root,
                "ellipse",
                {
                    "cx": str((x0 + x1) / 2),
                    "cy": str((y0 + y1) / 2),
                    "rx": str((x1 - x0) / 2),
                    "ry": str((y1 - y0) / 2),
                    "fill": fill,
                    "stroke": stroke,
                },
            )
        elif kind == "line" and box:
            x0, y0, x1, y1 = (float(v) for v in box)
            ET.SubElement(
                root,
                "line",
                {
                    "x1": str(x0),
                    "y1": str(y0),
                    "x2": str(x1),
                    "y2": str(y1),
                    "stroke": stroke if stroke != "none" else fill,
                    "stroke-width": str(shape.get("width") or 1),
                },
            )
        elif kind == "polygon" and shape.get("points"):
            pts = " ".join(f"{float(p[0])},{float(p[1])}" for p in shape["points"])
            ET.SubElement(root, "polygon", {"points": pts, "fill": fill, "stroke": stroke})
    for text in (texts or [])[:MAX_SVG_ELEMENTS]:
        pos = text.get("position") or text.get("at") or (0, 0)
        el = ET.SubElement(
            root,
            "text",
            {
                "x": str(float(pos[0])),
                "y": str(float(pos[1]) + float(text.get("size") or 12)),
                "font-size": str(text.get("size") or 12),
                "fill": _rgba(text.get("colour"))
                if text.get("colour") is not None
                else "rgba(0,0,0,1)",
            },
        )
        el.text = str(text.get("text") or "")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


_RGBA_RE: Final = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+))?\s*\)")
_HEX_RE: Final = re.compile(r"^#([0-9a-fA-F]{6})$")


def _parse_color(
    value: str | None, default: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    if not value or value == "none":
        return default
    m = _RGBA_RE.match(value.strip())
    if m:
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(round(a * 255))
    h = _HEX_RE.match(value.strip())
    if h:
        v = h.group(1)
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 255
    named = {
        "black": (0, 0, 0, 255),
        "white": (255, 255, 255, 255),
        "red": (255, 0, 0, 255),
        "green": (0, 128, 0, 255),
        "blue": (0, 0, 255, 255),
    }
    return named.get(value.strip().lower(), default)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def from_svg(data: bytes) -> dict[str, Any]:
    """The closed element set (rect / ellipse / circle / line / polygon / text / image)
    read into plan operations; anything else is counted in ``unsupported`` and named,
    never silently dropped."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise LayerError(f"not a readable SVG: {exc}") from exc
    if _local(root.tag) != "svg":
        raise LayerError("not an SVG document")

    def _dim(name: str) -> int:
        raw = (root.get(name) or "").strip()
        m = re.match(r"^([0-9.]+)", raw)
        if m:
            return int(float(m.group(1)))
        vb = (root.get("viewBox") or "").split()
        if len(vb) == 4:
            return int(float(vb[2] if name == "width" else vb[3]))
        return 0

    width, height = _dim("width"), _dim("height")
    operations: list[dict[str, Any]] = [
        {"op": "new", "width": max(1, width), "height": max(1, height)}
    ]
    unsupported: list[str] = []
    for element in list(root.iter())[1 : MAX_SVG_ELEMENTS + 1]:
        tag = _local(element.tag)
        fill = _parse_color(element.get("fill"), (0, 0, 0, 255))
        stroke = _parse_color(element.get("stroke"), fill)
        g = element.get
        try:
            if tag == "rect":
                x, y, w, h = (
                    float(g("x") or 0),
                    float(g("y") or 0),
                    float(g("width") or 0),
                    float(g("height") or 0),
                )
                operations.append(
                    {"op": "shape", "kind": "rect", "box": [x, y, x + w, y + h], "fill": list(fill)}
                )
            elif tag in ("ellipse", "circle"):
                cx, cy = float(g("cx") or 0), float(g("cy") or 0)
                rx = float(g("rx") or g("r") or 0)
                ry = float(g("ry") or g("r") or 0)
                operations.append(
                    {
                        "op": "shape",
                        "kind": "ellipse",
                        "box": [cx - rx, cy - ry, cx + rx, cy + ry],
                        "fill": list(fill),
                    }
                )
            elif tag == "line":
                operations.append(
                    {
                        "op": "shape",
                        "kind": "line",
                        "box": [
                            float(g("x1") or 0),
                            float(g("y1") or 0),
                            float(g("x2") or 0),
                            float(g("y2") or 0),
                        ],
                        "fill": list(stroke),
                    }
                )
            elif tag == "polygon":
                pts = [
                    [float(p.split(",")[0]), float(p.split(",")[1])]
                    for p in (g("points") or "").split()
                    if "," in p
                ]
                if len(pts) >= 3:
                    operations.append(
                        {"op": "draw", "shape": "polygon", "points": pts, "fill": list(fill)}
                    )
            elif tag == "text":
                size = int(float(re.match(r"^([0-9.]+)", g("font-size") or "12").group(1)))  # type: ignore[union-attr]
                operations.append(
                    {
                        "op": "add_text",
                        "text": (element.text or "")[:200],
                        "position": [float(g("x") or 0), float(g("y") or 0) - size],
                        "size": size,
                        "colour": list(fill),
                    }
                )
            elif tag == "image":
                href = g(f"{{{XLINK_NS}}}href") or g("href") or ""
                if href.startswith("data:image/png;base64,"):
                    operations.append(
                        {
                            "op": "_raster",
                            "x": float(g("x") or 0),
                            "y": float(g("y") or 0),
                            "png_base64": href.split(",", 1)[1],
                        }
                    )
                else:
                    unsupported.append("image (external href)")
            elif tag in ("g", "defs", "title", "desc", "metadata"):
                continue
            else:
                unsupported.append(tag)
        except (TypeError, ValueError, AttributeError):
            unsupported.append(tag)
    return {"width": width, "height": height, "operations": operations, "unsupported": unsupported}


__all__ = [
    "MAX_LAYERS",
    "ORA_MIMETYPE",
    "Layer",
    "LayerError",
    "LayeredDocument",
    "from_ora",
    "from_psd",
    "from_svg",
    "to_ora",
    "to_svg",
]
