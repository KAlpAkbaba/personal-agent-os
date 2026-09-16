"""Image generation and semantic edits behind ONE provider interface (B43 req 489-495).

Two providers:

* :class:`LocalImageProvider` - Pillow only, deterministic, offline. It does what pixels
  alone can do: enhance (autocontrast + sharpen), upscale (Lanczos x2/x4), style
  (grayscale / sepia / posterize / edges / invert), object removal inside a named box
  (the box is filled from its own border - a bounded, honest inpaint, never a guess at
  what was behind), object addition (a shape, a text or another stored image composited
  into a box) and background removal (the executor's own threshold/flood). It REFUSES
  generation from a prompt and prompt-driven edits by name (``provider_not_configured``)
  because no local model exists for them.
* :class:`OpenAIImageProvider` - the same interface over the OpenAI Images API
  (``gpt-image-1``): generation, prompt edits (object removal/addition/style by prompt).
  Chosen only when ``settings.creative_image_provider == "openai"`` AND the owner's key is
  present; never constructed in tests; every call is bounded by a timeout and the bytes
  it returns are re-opened by Pillow before they are trusted.

Every operation returns PNG bytes; the executor re-validates them. The provider is the
ONE seam the owner decides at (checkpoint: "görsel sağlayıcı").
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any, Final, Protocol

from PIL import Image, ImageDraw, ImageFilter, ImageOps

ERROR_PROVIDER_NOT_CONFIGURED = "provider_not_configured"
ERROR_PROVIDER_FAILED = "provider_failed"

STYLE_KINDS: Final[tuple[str, ...]] = ("grayscale", "sepia", "posterize", "edges", "invert")
ENHANCE_KINDS: Final[tuple[str, ...]] = ("auto", "sharpen", "denoise", "autocontrast")
UPSCALE_FACTORS: Final[tuple[int, ...]] = (2, 4)
MAX_UPSCALE_SIDE: Final = 8192
MAX_PROMPT_CHARS: Final = 1000
PROVIDER_LOCAL: Final = "local"
PROVIDER_OPENAI: Final = "openai"


class ImageProviderError(RuntimeError):
    def __init__(self, error_class: str, message: str) -> None:
        self.error_class = error_class
        super().__init__(message)


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, *, width: int, height: int) -> bytes: ...

    def edit(
        self, image: bytes, *, prompt: str, box: tuple[int, int, int, int] | None
    ) -> bytes: ...

    def upscale(self, image: bytes, *, factor: int) -> bytes: ...

    def enhance(self, image: bytes, *, kind: str) -> bytes: ...

    def style(self, image: bytes, *, kind: str) -> bytes: ...

    def object_remove(self, image: bytes, *, box: tuple[int, int, int, int]) -> bytes: ...


def _open(image: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(image))
        im.load()
    except Exception as exc:  # noqa: BLE001 - one honest error class
        raise ImageProviderError(
            ERROR_PROVIDER_FAILED, f"image could not be opened: {exc}"
        ) from exc
    return im.convert("RGBA")


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _clamp_box(box: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    w, h = size
    x0, x1 = sorted((max(0, min(w, int(x0))), max(0, min(w, int(x1)))))
    y0, y1 = sorted((max(0, min(h, int(y0))), max(0, min(h, int(y1)))))
    return x0, y0, x1, y1


# --------------------------------------------------------------------------- local


@dataclass(slots=True)
class LocalImageProvider:
    """Pillow only: what pixels alone can do, deterministically."""

    name: str = PROVIDER_LOCAL

    def generate(self, prompt: str, *, width: int, height: int) -> bytes:
        raise ImageProviderError(
            ERROR_PROVIDER_NOT_CONFIGURED,
            "image generation needs a configured provider (creative_image_provider)",
        )

    def edit(self, image: bytes, *, prompt: str, box: tuple[int, int, int, int] | None) -> bytes:
        raise ImageProviderError(
            ERROR_PROVIDER_NOT_CONFIGURED,
            "a prompt-driven edit needs a configured provider (creative_image_provider)",
        )

    def upscale(self, image: bytes, *, factor: int) -> bytes:
        if factor not in UPSCALE_FACTORS:
            raise ImageProviderError(ERROR_PROVIDER_FAILED, f"unsupported upscale factor {factor}")
        im = _open(image)
        width, height = im.width * factor, im.height * factor
        if max(width, height) > MAX_UPSCALE_SIDE:
            raise ImageProviderError(
                ERROR_PROVIDER_FAILED, f"upscaled side would exceed {MAX_UPSCALE_SIDE}px"
            )
        return _png(im.resize((width, height), Image.Resampling.LANCZOS))

    def enhance(self, image: bytes, *, kind: str) -> bytes:
        if kind not in ENHANCE_KINDS:
            raise ImageProviderError(ERROR_PROVIDER_FAILED, f"unsupported enhance kind {kind}")
        im = _open(image)
        rgb, alpha = im.convert("RGB"), im.getchannel("A")
        if kind in ("auto", "autocontrast"):
            rgb = ImageOps.autocontrast(rgb, cutoff=1)
        if kind in ("auto", "sharpen"):
            rgb = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))
        if kind == "denoise":
            rgb = rgb.filter(ImageFilter.MedianFilter(size=3))
        out = rgb.convert("RGBA")
        out.putalpha(alpha)
        return _png(out)

    def style(self, image: bytes, *, kind: str) -> bytes:
        if kind not in STYLE_KINDS:
            raise ImageProviderError(ERROR_PROVIDER_FAILED, f"unsupported style kind {kind}")
        im = _open(image)
        rgb, alpha = im.convert("RGB"), im.getchannel("A")
        if kind == "grayscale":
            rgb = ImageOps.grayscale(rgb).convert("RGB")
        elif kind == "sepia":
            gray = ImageOps.grayscale(rgb)
            rgb = ImageOps.colorize(gray, black=(40, 26, 13), white=(255, 240, 210)).convert("RGB")
        elif kind == "posterize":
            rgb = ImageOps.posterize(rgb, 3)
        elif kind == "edges":
            rgb = ImageOps.grayscale(rgb).filter(ImageFilter.FIND_EDGES).convert("RGB")
        elif kind == "invert":
            rgb = ImageOps.invert(rgb)
        out = rgb.convert("RGBA")
        out.putalpha(alpha)
        return _png(out)

    def object_remove(self, image: bytes, *, box: tuple[int, int, int, int]) -> bytes:
        """The box is filled from its OWN border: the median colour of the ring of pixels
        around it, blended and blurred inward - a bounded inpaint that never invents
        content, only continues the surroundings."""
        im = _open(image)
        x0, y0, x1, y1 = _clamp_box(box, im.size)
        if x1 - x0 < 1 or y1 - y0 < 1:
            raise ImageProviderError(ERROR_PROVIDER_FAILED, "object_remove box is empty")
        ring: list[tuple[int, int, int, int]] = []
        pad = 4
        rx0, ry0 = max(0, x0 - pad), max(0, y0 - pad)
        rx1, ry1 = min(im.width, x1 + pad), min(im.height, y1 + pad)
        px = im.load()
        for x in range(rx0, rx1):
            for y in range(ry0, ry1):
                if x0 <= x < x1 and y0 <= y < y1:
                    continue
                ring.append(px[x, y])
        if not ring:
            ring = [(255, 255, 255, 255)]
        fill = tuple(sorted(c[i] for c in ring)[len(ring) // 2] for i in range(4))
        patch = Image.new("RGBA", (x1 - x0, y1 - y0), fill)
        im.paste(patch, (x0, y0))
        region = im.crop((rx0, ry0, rx1, ry1)).filter(ImageFilter.GaussianBlur(radius=2))
        im.paste(region, (rx0, ry0))
        return _png(im)


def object_add(
    image: bytes,
    *,
    box: tuple[int, int, int, int],
    kind: str,
    fill: tuple[int, int, int, int] = (0, 0, 0, 255),
    content: str | None = None,
    asset: bytes | None = None,
) -> bytes:
    """A shape, a text or another stored image placed INTO the box (req 491's honest
    local half: the added thing is what the owner named, never a synthesised object)."""
    im = _open(image)
    x0, y0, x1, y1 = _clamp_box(box, im.size)
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise ImageProviderError(ERROR_PROVIDER_FAILED, "object_add box is empty")
    draw = ImageDraw.Draw(im)
    if kind == "rect":
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=fill)
    elif kind == "ellipse":
        draw.ellipse((x0, y0, x1 - 1, y1 - 1), fill=fill)
    elif kind == "text":
        draw.text((x0, y0), content or "", fill=fill)
    elif kind == "image":
        if asset is None:
            raise ImageProviderError(ERROR_PROVIDER_FAILED, "object_add image needs an asset")
        sprite = _open(asset).resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
        im.alpha_composite(sprite, (x0, y0))
    else:
        raise ImageProviderError(ERROR_PROVIDER_FAILED, f"unsupported object kind {kind}")
    return _png(im)


# ------------------------------------------------------------------------- scripted


class ScriptedImageProvider(LocalImageProvider):
    """A test double for the PROMPT half: a scripted PNG for ``generate``/``edit``, and a
    record of every prompt it was given (never the network)."""

    def __init__(self, *, generated: bytes | None = None, fail: str | None = None) -> None:
        self.name = "scripted"
        self.generated = generated
        self.fail = fail
        self.prompts: list[dict[str, Any]] = []

    def generate(self, prompt: str, *, width: int, height: int) -> bytes:
        self.prompts.append({"op": "generate", "prompt": prompt, "width": width, "height": height})
        if self.fail:
            raise ImageProviderError(self.fail, "scripted failure")
        if self.generated is not None:
            return self.generated
        return _png(Image.new("RGBA", (width, height), (120, 160, 200, 255)))

    def edit(self, image: bytes, *, prompt: str, box: tuple[int, int, int, int] | None) -> bytes:
        self.prompts.append({"op": "edit", "prompt": prompt, "box": box})
        if self.fail:
            raise ImageProviderError(self.fail, "scripted failure")
        im = _open(image)
        if box is not None:
            x0, y0, x1, y1 = _clamp_box(box, im.size)
            ImageDraw.Draw(im).rectangle((x0, y0, x1 - 1, y1 - 1), fill=(0, 200, 0, 255))
        return _png(im)


# --------------------------------------------------------------------------- openai


class OpenAIImageProvider(LocalImageProvider):
    """The OpenAI Images API behind the same interface. The key is never logged or
    echoed; only the model name and the byte counts reach the ledger."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-image-1",
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 60.0,
        transport: Any | None = None,
    ) -> None:
        self.name = PROVIDER_OPENAI
        self._api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._transport = transport

    def _client(self) -> Any:
        import httpx

        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self.timeout_s,
            transport=self._transport,
        )

    @staticmethod
    def _bytes_of(payload: dict[str, Any]) -> bytes:
        data = payload.get("data") or []
        if not data or not isinstance(data[0], dict) or not data[0].get("b64_json"):
            raise ImageProviderError(ERROR_PROVIDER_FAILED, "provider answered without image bytes")
        raw = base64.b64decode(str(data[0]["b64_json"]))
        _open(raw)  # re-validated before it is trusted
        return raw

    def generate(self, prompt: str, *, width: int, height: int) -> bytes:
        size = f"{width}x{height}"
        with self._client() as client:
            response = client.post(
                "/images/generations",
                json={
                    "model": self.model,
                    "prompt": prompt[:MAX_PROMPT_CHARS],
                    "size": size,
                    "n": 1,
                },
            )
        if response.status_code != 200:
            raise ImageProviderError(
                ERROR_PROVIDER_FAILED, f"provider answered {response.status_code}"
            )
        return self._bytes_of(response.json())

    def edit(self, image: bytes, *, prompt: str, box: tuple[int, int, int, int] | None) -> bytes:
        files: dict[str, Any] = {"image": ("image.png", image, "image/png")}
        if box is not None:
            im = _open(image)
            mask = Image.new("RGBA", im.size, (0, 0, 0, 255))
            x0, y0, x1, y1 = _clamp_box(box, im.size)
            ImageDraw.Draw(mask).rectangle((x0, y0, x1 - 1, y1 - 1), fill=(0, 0, 0, 0))
            files["mask"] = ("mask.png", _png(mask), "image/png")
        with self._client() as client:
            response = client.post(
                "/images/edits",
                data={"model": self.model, "prompt": prompt[:MAX_PROMPT_CHARS], "n": 1},
                files=files,
            )
        if response.status_code != 200:
            raise ImageProviderError(
                ERROR_PROVIDER_FAILED, f"provider answered {response.status_code}"
            )
        return self._bytes_of(response.json())


def build_image_provider(settings: Any) -> ImageProvider:
    """The provider the settings name: ``local`` (the default) or ``openai`` when the
    owner's key is present; a named-but-keyless ``openai`` falls back to local so the
    deterministic half keeps working and generation refuses by name."""
    wanted = str(getattr(settings, "creative_image_provider", PROVIDER_LOCAL) or PROVIDER_LOCAL)
    key = str(getattr(settings, "voice_openai_api_key", "") or "")
    if wanted == PROVIDER_OPENAI and key:
        return OpenAIImageProvider(
            api_key=key,
            model=str(getattr(settings, "creative_openai_image_model", "gpt-image-1")),
            base_url=str(
                getattr(settings, "voice_realtime_openai_base_url", "https://api.openai.com/v1")
            ),
            timeout_s=float(getattr(settings, "creative_image_timeout_s", 60.0)),
        )
    return LocalImageProvider()


__all__ = [
    "ENHANCE_KINDS",
    "ERROR_PROVIDER_FAILED",
    "ERROR_PROVIDER_NOT_CONFIGURED",
    "MAX_PROMPT_CHARS",
    "PROVIDER_LOCAL",
    "PROVIDER_OPENAI",
    "STYLE_KINDS",
    "UPSCALE_FACTORS",
    "ImageProvider",
    "ImageProviderError",
    "LocalImageProvider",
    "OpenAIImageProvider",
    "ScriptedImageProvider",
    "build_image_provider",
    "object_add",
]
