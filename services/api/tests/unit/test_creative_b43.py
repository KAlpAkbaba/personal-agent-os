"""B43 - creative generation and delivery (req 489-495, 498, 500, 502, 504, 506-509, 511,
512).

Measured before: the creative service could draw, adjust, crop and remove a background
with Pillow, and nothing else: no generation, no object removal/addition, no style, no
enhancement, no upscale, no semantic check, no layers/PSD/SVG, no delivery to the owner's
disk (the output lived only in the object store), no undo/redo, no way to drive Paint.

Every seam is proven by executing it: the local provider's pixels re-read by Pillow; the
scripted provider's prompts; the refusal by name without a provider; layers flattened and
written as OpenRaster and read back; a PSD read through Pillow's own plugin; SVG written
as real vector elements and read back into operations; the history pointer moved by undo
and redo; the delivery registering an image artifact (B42) and reaching the fake device's
``file.fetch``; the driver's steps run through the operator's task runner against a
scripted device; the routes and the voice tools through the real application object; the
router's creative words.
"""

from __future__ import annotations

import base64
import io
import json
import uuid
import zipfile
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select

from app.artifacts import service as artifact_service
from app.artifacts.models import Artifact
from app.creative import drivers, imaging, layers
from app.creative.execute import ProviderRefusal, execute
from app.creative.imaging import LocalImageProvider, ScriptedImageProvider
from app.creative.models import CreativeRunRow
from app.creative.spec import TOOL_LAYERED, CreativePlan
from app.ledger.models import ActivityEventRow
from app.operator.vision import FakeVisionProvider
from app.routines.dispatch import DeviceRunResult
from app.voice.intents import Intent, resolve_intent
from tests.alarms_support import FakeDeviceAction, ok
from tests.artifacts_support import file_fetch_ok
from tests.voice_corpus.corpus import CTX_CREATIVE_PAINT
from tests.voice_corpus.harness import build_harness


def _png(
    width: int = 64, height: int = 48, color: tuple[int, int, int, int] = (200, 30, 30, 255)
) -> bytes:
    im = Image.new("RGBA", (width, height), color)
    for x in range(width // 4, width // 2):
        for y in range(height // 4, height // 2):
            im.putpixel((x, y), (10, 10, 240, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _plan(ops: list[dict[str, Any]], *, tool: str = "paint", name: str = "deneme") -> CreativePlan:
    body: dict[str, Any] = {"tool": tool, "name": name, "operations": ops}
    if any(op.get("op") == "open" for op in ops):
        # The schema requires an opened plan to name its source; the bytes are passed
        # to execute() directly, as the service does.
        body["source"] = "fixture-source"
    return CreativePlan.model_validate(body)


# ------------------------------------------------------- the local provider (489-495)


def test_the_local_provider_upscales_enhances_styles_and_removes_an_object_by_its_border() -> None:
    provider = LocalImageProvider()
    src = _png()
    up = _open(provider.upscale(src, factor=2))
    assert (up.width, up.height) == (128, 96)
    styled = _open(provider.style(src, kind="grayscale"))
    r, g, b, _a = styled.getpixel((2, 2))
    assert r == g == b
    inverted = _open(provider.style(src, kind="invert"))
    assert inverted.getpixel((2, 2))[:3] == (55, 225, 225)
    enhanced = _open(provider.enhance(src, kind="auto"))
    assert enhanced.size == (64, 48)
    removed = _open(provider.object_remove(src, box=(16, 12, 32, 24)))
    # The blue block is gone: the box now continues its red surroundings.
    assert removed.getpixel((24, 18))[0] > 150 and removed.getpixel((24, 18))[2] < 100
    assert _open(src).getpixel((24, 18))[2] == 240
    with pytest.raises(imaging.ImageProviderError):
        provider.upscale(src, factor=3)


def test_object_add_places_a_shape_a_text_or_a_stored_image_into_the_box() -> None:
    src = _png()
    added = _open(
        imaging.object_add(src, box=(40, 30, 60, 46), kind="ellipse", fill=(0, 255, 0, 255))
    )
    assert added.getpixel((50, 38))[:3] == (0, 255, 0)
    # A UNIFORM sprite: _png paints its blue patch into every image, and a scaled patch
    # would land exactly on the sampled pixel.
    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 0, 0, 255)).save(buf, format="PNG")
    sprite = buf.getvalue()
    composed = _open(imaging.object_add(src, box=(0, 0, 16, 16), kind="image", asset=sprite))
    assert composed.getpixel((4, 4))[:3] == (0, 0, 0)
    with pytest.raises(imaging.ImageProviderError):
        imaging.object_add(src, box=(0, 0, 8, 8), kind="image", asset=None)


def test_without_a_configured_provider_generation_is_refused_by_name_never_drawn() -> None:
    provider = LocalImageProvider()
    with pytest.raises(imaging.ImageProviderError) as excinfo:
        provider.generate("mavi bir dalga", width=64, height=64)
    assert excinfo.value.error_class == imaging.ERROR_PROVIDER_NOT_CONFIGURED
    with pytest.raises(ProviderRefusal) as refused:
        execute(
            _plan([{"op": "generate", "prompt": "mavi bir dalga", "width": 64, "height": 64}]), None
        )
    assert refused.value.error_class == imaging.ERROR_PROVIDER_NOT_CONFIGURED


def test_the_executor_runs_the_new_operations_through_the_provider() -> None:
    provider = ScriptedImageProvider()
    result = execute(
        _plan(
            [
                {"op": "generate", "prompt": "mavi bir dalga", "width": 64, "height": 64},
                {
                    "op": "object_add",
                    "box": [8, 8, 24, 24],
                    "kind": "rect",
                    "fill": [255, 255, 0, 255],
                },
                {"op": "object_remove", "box": [8, 8, 24, 24]},
                {
                    "op": "object_add",
                    "box": [30, 30, 40, 40],
                    "kind": "prompt",
                    "prompt": "kırmızı bir top",
                },
                {"op": "style", "kind": "sepia"},
                {"op": "enhance", "kind": "sharpen"},
                {"op": "upscale", "factor": 2},
                {"op": "semantic_check", "expectation": "mavi bir dalga"},
                {"op": "export", "format": "png"},
            ]
        ),
        None,
        image_provider=provider,
    )
    assert result.errors == []
    assert (result.width, result.height) == (128, 128)
    assert result.semantic_expectations == ["mavi bir dalga"]
    assert [p["op"] for p in provider.prompts] == ["generate", "edit"]
    assert provider.prompts[1]["box"] == (30, 30, 40, 40)


def test_the_openai_provider_speaks_the_images_api_and_re_validates_the_bytes() -> None:
    import httpx

    seen: list[dict[str, Any]] = []
    generated = base64.b64encode(_png(32, 32)).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"path": request.url.path, "auth": request.headers.get("authorization")})
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"b64_json": generated}]})
        if request.url.path.endswith("/images/edits"):
            return httpx.Response(200, json={"data": [{"b64_json": "bm90IGFuIGltYWdl"}]})
        return httpx.Response(500)

    provider = imaging.OpenAIImageProvider(
        api_key="sk-test", transport=httpx.MockTransport(handler)
    )
    out = provider.generate("mavi bir dalga", width=32, height=32)
    assert _open(out).size == (32, 32)
    assert seen[0]["auth"] == "Bearer sk-test"
    with pytest.raises(imaging.ImageProviderError) as excinfo:
        provider.edit(_png(), prompt="x", box=(0, 0, 4, 4))
    assert excinfo.value.error_class == imaging.ERROR_PROVIDER_FAILED


def test_the_settings_choose_the_provider_and_a_keyless_openai_falls_back_to_local() -> None:
    from app.config import Settings

    # Every setting the choice reads is passed explicitly: a real key in the owner's
    # environment must not change what this test proves.
    local = imaging.build_image_provider(
        Settings(_env_file=None, creative_image_provider="local", voice_openai_api_key="")
    )
    assert local.name == "local"
    keyless = imaging.build_image_provider(
        Settings(_env_file=None, creative_image_provider="openai", voice_openai_api_key="")
    )
    assert keyless.name == "local"
    keyed = imaging.build_image_provider(
        Settings(_env_file=None, creative_image_provider="openai", voice_openai_api_key="sk-x")
    )
    assert keyed.name == "openai" and keyed.model == "gpt-image-1"


# ------------------------------------------------------ layers, PSD, ORA, SVG (506-508)


def test_layers_flatten_in_order_merge_down_and_round_trip_through_openraster() -> None:
    doc = layers.LayeredDocument(40, 30)
    doc.add("arka plan", Image.new("RGBA", (40, 30), (255, 255, 255, 255)))
    top = doc.add("kutu")
    for x in range(10, 20):
        for y in range(10, 20):
            top.image.putpixel((x, y), (0, 0, 255, 255))
    top.opacity = 0.5
    flat = doc.flatten()
    assert flat.getpixel((15, 15))[2] == 255 and 100 < flat.getpixel((15, 15))[0] < 200
    ora = layers.to_ora(doc)
    with zipfile.ZipFile(io.BytesIO(ora)) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype" and zf.read("mimetype") == b"image/openraster"
        assert "stack.xml" in names and "mergedimage.png" in names
    back = layers.from_ora(ora)
    assert [layer.name for layer in back.layers] == ["arka plan", "kutu"]
    assert back.layers[1].opacity == 0.5
    merged = doc.merge("kutu")
    assert merged.name == "arka plan" and len(doc.layers) == 1
    with pytest.raises(layers.LayerError):
        doc.get("kutu")


def _minimal_psd(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A genuine, minimal PSD written by hand from the format's own header (8BPS, version
    1, 3 channels, 8-bit RGB, no layers, raw composite planes) - what Pillow's PSD plugin
    reads as the composite; this repository ships no PSD writer, so the fixture is the
    format itself, not a Pillow round trip."""
    import struct

    header = (
        b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6 + struct.pack(">HIIHH", 3, height, width, 8, 3)
    )
    sections = struct.pack(">I", 0) * 3  # colour mode data, image resources, layer & mask
    planes = b"".join(bytes([c]) * (width * height) for c in rgb)
    return header + sections + struct.pack(">H", 0) + planes


def test_a_psd_is_read_through_pillows_own_plugin() -> None:
    doc = layers.from_psd(_minimal_psd(12, 9, (0, 128, 255)))
    assert (doc.width, doc.height) == (12, 9)
    assert [layer.name for layer in doc.layers] == ["composite"]
    assert doc.flatten().getpixel((3, 3))[:3] == (0, 128, 255)
    with pytest.raises(layers.LayerError):
        layers.from_psd(_png())
    # ...and a PSD source opens through the layered tool as its layer stack.
    result = execute(
        _plan([{"op": "open"}, {"op": "export", "format": "ora"}], tool=TOOL_LAYERED),
        _minimal_psd(6, 6, (255, 0, 0)),
    )
    assert result.errors == [] and [layer["name"] for layer in result.layers] == ["composite"]


def test_svg_is_written_as_real_vector_elements_and_read_back_into_operations() -> None:
    svg = layers.to_svg(
        width=100,
        height=80,
        shapes=[
            {"kind": "rect", "box": [10, 10, 40, 30], "fill": [255, 0, 0, 255], "stroke": None},
            {"kind": "ellipse", "box": [50, 10, 90, 50], "fill": None, "stroke": [0, 0, 255, 255]},
            {
                "kind": "polygon",
                "points": [[1, 1], [20, 1], [10, 15]],
                "fill": [0, 255, 0, 255],
                "stroke": None,
            },
        ],
        texts=[{"text": "Merhaba", "position": [5, 60], "size": 12, "colour": [0, 0, 0, 255]}],
    )
    text = svg.decode("utf-8")
    assert "<rect" in text and "<ellipse" in text and "<polygon" in text and "<text" in text
    assert "base64" not in text
    parsed = layers.from_svg(svg)
    ops = [op["op"] for op in parsed["operations"]]
    assert ops == ["new", "shape", "shape", "draw", "add_text"]
    assert parsed["operations"][1]["box"] == [10.0, 10.0, 40.0, 30.0]
    assert parsed["operations"][4]["text"] == "Merhaba"
    assert parsed["unsupported"] == []
    result = execute(_plan([{"op": "open"}, {"op": "export", "format": "png"}]), svg)
    assert (result.width, result.height) == (100, 80)
    assert _open(result.image_bytes).getpixel((20, 20))[:3] == (255, 0, 0)


def test_the_layered_tool_keeps_layers_through_the_plan_and_exports_openraster() -> None:
    result = execute(
        _plan(
            [
                {"op": "new", "width": 30, "height": 30, "background": [255, 255, 255, 255]},
                {"op": "layer", "action": "add", "name": "zemin"},
                {"op": "shape", "kind": "rect", "box": [5, 5, 15, 15], "fill": [255, 0, 0, 255]},
                {"op": "export", "format": "ora"},
            ],
            tool=TOOL_LAYERED,
        ),
        None,
    )
    assert result.errors == []
    assert [layer["name"] for layer in result.layers] == ["zemin"]
    back = layers.from_ora(result.image_bytes)
    assert [layer.name for layer in back.layers] == ["zemin", "working"]
    assert back.flatten().getpixel((10, 10))[:3] == (255, 0, 0)
    # A tool with no layer model refuses the plan when it is validated, before anything runs.
    with pytest.raises(ValidationError, match="layer"):
        _plan(
            [{"op": "new", "width": 8, "height": 8}, {"op": "layer", "action": "add", "name": "x"}]
        )


def test_the_layered_provider_is_installed_by_construction_and_offers_layers() -> None:
    from app.creative.providers import ALL_CAPABILITIES, LayeredProvider, default_providers

    provider = default_providers()[TOOL_LAYERED]
    assert isinstance(provider, LayeredProvider)
    caps = provider.capabilities()
    assert (
        caps.ok and "layer" in caps.capabilities and set(caps.capabilities) == set(ALL_CAPABILITIES)
    )
    assert {
        "generate",
        "object_remove",
        "object_add",
        "style",
        "enhance",
        "upscale",
        "semantic_check",
    } <= set(ALL_CAPABILITIES)


# ------------------------------------------------------------ the service (498, 511, 512)


def _service(h, **kwargs):
    from app.creative.providers import (
        FigmaProvider,
        IllustratorProvider,
        LayeredProvider,
        PhotoshopProvider,
    )
    from app.creative.service import CreativeService
    from tests.voice_corpus.harness import _FixtureInstalledPaintProvider

    return CreativeService(
        object_store=h.runtime.artifacts.store,
        providers={
            "paint": _FixtureInstalledPaintProvider(),
            "photoshop": PhotoshopProvider(),
            "illustrator": IllustratorProvider(),
            "figma": FigmaProvider(token_present=False),
            "layered": LayeredProvider(),
        },
        **kwargs,
    )


def _draw_plan(name: str = "logo") -> dict[str, Any]:
    return {
        "tool": "paint",
        "name": name,
        "operations": [
            {"op": "new", "width": 40, "height": 40, "background": [255, 255, 255, 255]},
            {"op": "shape", "kind": "rect", "box": [5, 5, 20, 20], "fill": [255, 0, 0, 255]},
            {"op": "export", "format": "png"},
        ],
    }


def _low_contrast_plan(name: str = "foto") -> dict[str, Any]:
    """A washed-out photo: two close greys, so an auto enhancement has something to fix."""
    return {
        "tool": "paint",
        "name": name,
        "operations": [
            {"op": "new", "width": 40, "height": 40, "background": [120, 120, 120, 255]},
            {"op": "shape", "kind": "rect", "box": [5, 5, 20, 20], "fill": [140, 132, 126, 255]},
            {"op": "export", "format": "png"},
        ],
    }


def _low_contrast_png(width: int = 32, height: int = 32) -> bytes:
    im = Image.new("RGBA", (width, height), (120, 120, 120, 255))
    for x in range(4, 16):
        for y in range(4, 16):
            im.putpixel((x, y), (140, 132, 126, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_generate_makes_a_run_from_the_providers_answer_and_records_the_check() -> None:
    h = build_harness()
    provider = ScriptedImageProvider()
    vision = FakeVisionProvider(answer="Evet, mavi bir dalga var.")
    service = _service(h, image_provider=provider, vision_provider=vision)
    with h.factory() as db:
        receipt = service.generate(
            db,
            prompt="mavi bir dalga",
            name="dalga",
            width=64,
            height=64,
            expectation="mavi bir dalga",
            session_id="s",
        )
        assert receipt["execution_status"] == "executed", receipt
        row = db.get(CreativeRunRow, uuid.UUID(receipt["run_id"]))
        assert row.output_object_key and h.runtime.artifacts.store.exists(row.output_object_key)
        assert row.semantic_json["checked"] is True and row.semantic_json["ok"] is True
        assert row.semantic_json["provider"] == "fake"
        assert vision.questions and "mavi bir dalga" in vision.questions[0]
        assert provider.prompts[0]["prompt"] == "mavi bir dalga"
        assert len(row.history_json) == 1 and row.history_index == 0
        events = list(
            db.scalars(
                select(ActivityEventRow).where(ActivityEventRow.event_type == "creative.generate")
            )
        )
        assert len(events) == 1 and events[0].detail_json["provider"] == "scripted"


def test_a_semantic_no_marks_the_run_unverified_and_no_vision_provider_is_recorded_not_run() -> (
    None
):
    h = build_harness()
    with h.factory() as db:
        doubting = _service(
            h,
            image_provider=ScriptedImageProvider(),
            vision_provider=FakeVisionProvider(answer="Hayır, boş bir kare."),
        )
        receipt = doubting.generate(
            db,
            prompt="x",
            name="kusku",
            width=32,
            height=32,
            expectation="bir kedi",
            session_id="s",
        )
        row = db.get(CreativeRunRow, uuid.UUID(receipt["run_id"]))
        assert row.semantic_json["ok"] is False and row.state == "unverified"
        blind = _service(h, image_provider=ScriptedImageProvider(), vision_provider=None)
        receipt = blind.generate(
            db, prompt="x", name="kor", width=32, height=32, expectation="bir kedi", session_id="s"
        )
        row = db.get(CreativeRunRow, uuid.UUID(receipt["run_id"]))
        assert row.semantic_json == {
            "expectation": "bir kedi",
            "checked": False,
            "ok": None,
            "answer": None,
            "provider": None,
        }


def test_generation_without_a_provider_is_a_refused_receipt_with_the_owner_sentence() -> None:
    h = build_harness()
    service = _service(h)
    with h.factory() as db:
        receipt = service.generate(
            db, prompt="mavi bir dalga", name="yok", width=32, height=32, session_id="s"
        )
        assert receipt["execution_status"] == "refused"
        assert receipt["error_class"] == "provider_not_configured"
        assert "sağlayıcı tanımlı değil" in receipt["speech"]
        row = db.get(CreativeRunRow, uuid.UUID(receipt["run_id"]))
        assert row.state == "failed" and row.output_object_key is None


def test_undo_and_redo_move_the_pointer_over_the_runs_own_outputs() -> None:
    h = build_harness()
    service = _service(h)
    with h.factory() as db:
        created = service.create(db, plan=_draw_plan(), session_id="s")
        run_id = created["run_id"]
        first_key = db.get(CreativeRunRow, uuid.UUID(run_id)).output_object_key
        applied = service.apply(
            db,
            target=run_id,
            operations=[{"op": "style", "kind": "invert"}, {"op": "export", "format": "png"}],
            session_id="s",
        )
        assert applied["execution_status"] == "executed", applied
        row = db.get(CreativeRunRow, uuid.UUID(run_id))
        second_key = row.output_object_key
        assert second_key != first_key and len(row.history_json) == 2 and row.history_index == 1
        assert h.runtime.artifacts.store.exists(first_key) and h.runtime.artifacts.store.exists(
            second_key
        )
        undone = service.undo(db, target=run_id, session_id="s")
        assert undone["execution_status"] == "executed" and undone["index"] == 0
        row = db.get(CreativeRunRow, uuid.UUID(run_id))
        assert row.output_object_key == first_key
        again = service.undo(db, target=run_id, session_id="s")
        assert again["execution_status"] == "refused" and again["error_class"] == "nothing_to_undo"
        redone = service.redo(db, target=run_id, session_id="s")
        assert redone["execution_status"] == "executed" and redone["index"] == 1
        assert db.get(CreativeRunRow, uuid.UUID(run_id)).output_object_key == second_key
        beyond = service.redo(db, target=run_id, session_id="s")
        assert beyond["error_class"] == "nothing_to_redo"
        history = service.history(db, target=run_id)
        assert [e["ops"][-1] for e in history["entries"]] == ["export", "export"] and history[
            "index"
        ] == 1
        # A new edit after an undo drops the redo branch (a linear stack, like every editor).
        service.undo(db, target=run_id, session_id="s")
        service.apply(
            db,
            target=run_id,
            operations=[{"op": "style", "kind": "grayscale"}, {"op": "export", "format": "png"}],
            session_id="s",
        )
        row = db.get(CreativeRunRow, uuid.UUID(run_id))
        assert (
            len(row.history_json) == 2
            and row.history_index == 1
            and row.output_object_key != second_key
        )


def test_enhance_is_the_photo_fix_on_the_run_in_focus_and_asks_when_there_is_none() -> None:
    h = build_harness()
    service = _service(h)
    with h.factory() as db:
        assert (
            service.enhance(db, target="current", session_id="s")["status"] == "needs_clarification"
        )
        created = service.create(db, plan=_low_contrast_plan("foto"), session_id="s")
        fixed = service.enhance(db, target=created["run_id"], session_id="s")
        assert fixed["execution_status"] == "executed" and fixed["changed"] is True, fixed
        row = db.get(CreativeRunRow, uuid.UUID(created["run_id"]))
        assert (
            row.history_json[-1]["ops"][:1] == ["open"] and "enhance" in row.history_json[-1]["ops"]
        )


def test_a_photo_that_needs_no_fix_is_said_so_and_leaves_no_empty_step() -> None:
    h = build_harness()
    service = _service(h)
    with h.factory() as db:
        created = service.create(db, plan=_draw_plan("duz"), session_id="s")
        result = service.enhance(db, target=created["run_id"], session_id="s")
        assert result["execution_status"] == "executed" and result["changed"] is False
        assert "düzeltecek bir şey bulmadım" in result["speech"]
        row = db.get(CreativeRunRow, uuid.UUID(created["run_id"]))
        assert len(row.history_json) == 1 and row.history_index == 0


# ------------------------------------------------------------ delivery and driving (509, 500)


def test_deliver_registers_an_image_artifact_and_reaches_the_devices_file_fetch() -> None:
    h = build_harness()
    h.device.reset()
    service = _service(h)
    with h.factory() as db:
        created = service.create(db, plan=_draw_plan("teslim"), session_id="s")
        delivered = service.deliver(
            db,
            h.device,
            target=created["run_id"],
            base_url="https://cloud.example",
            session_id="s",
            application="mspaint",
        )
        assert delivered["execution_status"] == "executed", delivered
        fetch = h.device.payload_for("file.fetch")
        assert fetch is not None and fetch["open"] is True and fetch["application"] == "mspaint"
        assert fetch["url"].startswith("https://cloud.example/") and fetch["name"].endswith(".png")
        row = db.get(CreativeRunRow, uuid.UUID(created["run_id"]))
        assert row.artifact_id is not None and row.delivery_json["sha256"] == row.output_sha256
        artifact = artifact_service.get_artifact(db, row.artifact_id)
        assert artifact.kind == "image"
        version = artifact_service.get_current_version(db, artifact.id)
        assert version.source_manifest_json["sources"][0]["creative_run_id"] == created["run_id"]
        assert version.provenance_json["actor"]["kind"] == "owner_voice"
        # The artifact owns its COPY: the run's own object is untouched by the artifact.
        renders = artifact_service.list_renders(db, version.id)
        assert renders[0].object_key != row.output_object_key
        events = list(
            db.scalars(
                select(ActivityEventRow).where(ActivityEventRow.event_type == "creative.deliver")
            )
        )
        assert len(events) == 1
        # Delivering the SAME bytes again reuses the artifact; a new output makes a new one.
        service.deliver(
            db, h.device, target=created["run_id"], base_url="https://cloud.example", session_id="s"
        )
        assert db.query(Artifact).count() == 1
        service.apply(
            db,
            target=created["run_id"],
            operations=[{"op": "style", "kind": "invert"}, {"op": "export", "format": "png"}],
            session_id="s",
        )
        service.deliver(
            db, h.device, target=created["run_id"], base_url="https://cloud.example", session_id="s"
        )
        assert db.query(Artifact).count() == 2


def test_deliver_without_an_output_or_a_device_is_refused_by_name() -> None:
    h = build_harness()
    service = _service(h)
    with h.factory() as db:
        assert (
            service.deliver(db, None, target="current", base_url="https://x", session_id="s")[
                "status"
            ]
            == "needs_clarification"
        )
        created = service.create(db, plan=_draw_plan("cihazsiz"), session_id="s")
        refused = service.deliver(
            db, None, target=created["run_id"], base_url="https://x", session_id="s"
        )
        assert (
            refused["execution_status"] == "refused"
            and refused["error_class"] == "capability_missing"
        )


def _driven_device() -> FakeDeviceAction:
    results = dict(h_results())
    return FakeDeviceAction(results=results)


def h_results() -> dict[str, Any]:
    return {
        "file.fetch": file_fetch_ok,
        "app.launch": ok(
            pid=4242, executable=r"C:\Windows\System32\mspaint.exe", process_alive=True
        ),
        "window.current": ok(
            window={
                "window_id": "w-1-1",
                "title": "teslim.png - Paint",
                "image": "mspaint.exe",
                "pid": 4242,
                "foreground": True,
            }
        ),
        "keyboard.shortcut": lambda payload: ok(
            keys=list(payload["keys"]),
            window={"window_id": payload["window_id"], "foreground": True},
        ),
        "keyboard.type": lambda payload: ok(typed=len(payload["text"])),
        "keyboard.key": lambda payload: ok(key=payload["key"]),
        "screen.capture": ok(png_base64=base64.b64encode(_png(8, 8)).decode("ascii")),
    }


def test_the_paint_driver_opens_the_delivered_file_and_presses_paints_own_shortcuts() -> None:
    h = build_harness()
    device = _driven_device()
    service = _service(h)
    with h.factory() as db:
        created = service.create(db, plan=_draw_plan("teslim"), session_id="s")
        delivered = service.deliver(
            db, device, target=created["run_id"], base_url="https://cloud.example", session_id="s"
        )
        assert delivered["execution_status"] == "executed", delivered
        row = db.get(CreativeRunRow, uuid.UUID(created["run_id"]))
        assert row.delivery_json["path"]
        driven = service.drive(
            db,
            device,
            target=created["run_id"],
            actions=[{"action": "resize", "percent": 50}, "invert", "save"],
            session_id="s",
        )
        assert driven["execution_status"] == "executed", driven
        assert driven["window_title"] == "teslim.png - Paint"
        launch = device.payload_for("app.launch")
        assert launch["application"] == "mspaint" and launch["args"] == [row.delivery_json["path"]]
        shortcuts = [
            c["payload"]["keys"] for c in device.calls if c["capability"] == "keyboard.shortcut"
        ]
        assert shortcuts == [["ctrl", "w"], ["ctrl", "a"], ["ctrl", "i"], ["ctrl", "s"]]
        assert all(
            c["payload"]["window_id"] == "w-1-1"
            for c in device.calls
            if c["capability"].startswith("keyboard.")
        )
        typed = [c["payload"]["text"] for c in device.calls if c["capability"] == "keyboard.type"]
        assert typed == ["50"]
        assert device.capabilities_called()[-1] == "screen.capture"
        events = list(
            db.scalars(
                select(ActivityEventRow).where(ActivityEventRow.event_type == "creative.drive")
            )
        )
        assert len(events) == 1 and events[0].detail_json["ok"] is True


def test_a_drive_that_fails_names_the_step_and_stops_there() -> None:
    h = build_harness()
    results = h_results()
    results["keyboard.shortcut"] = lambda payload: (
        DeviceRunResult(False, "device_error", "no dialog")
        if payload["keys"] == ["ctrl", "i"]
        else ok(keys=list(payload["keys"]))
    )
    device = FakeDeviceAction(results=results)
    service = _service(h)
    with h.factory() as db:
        created = service.create(db, plan=_draw_plan("takilan"), session_id="s")
        service.deliver(
            db, device, target=created["run_id"], base_url="https://cloud.example", session_id="s"
        )
        driven = service.drive(
            db, device, target=created["run_id"], actions=["invert", "save"], session_id="s"
        )
        assert driven["execution_status"] == "refused"
        assert driven["failed_step"] == "paint:0:invert"
        assert ["ctrl", "s"] not in [
            c["payload"]["keys"] for c in device.calls if c["capability"] == "keyboard.shortcut"
        ]
        before = service.drive(
            db, device, target=created["run_id"], actions=["resize"], session_id="s"
        )
        assert before["error_class"] == "drive_unsupported"


def test_drive_refuses_before_a_delivery_and_the_adobe_drivers_share_their_shortcuts() -> None:
    h = build_harness()
    service = _service(h)
    with h.factory() as db:
        created = service.create(db, plan=_draw_plan("erken"), session_id="s")
        early = service.drive(
            db, _driven_device(), target=created["run_id"], actions=["save"], session_id="s"
        )
        assert early["execution_status"] == "refused" and early["error_class"] == "no_output"
    steps, _session = drivers.AdobeDriver(application=drivers.APP_PHOTOSHOP).plan(
        r"C:\Users\o\Downloads\x.png",
        [drivers.DriveAction.parse("undo"), drivers.DriveAction.parse("save")],
        title_hint="x.png",
    )
    assert steps[0].payload == {"application": "photoshop", "args": [r"C:\Users\o\Downloads\x.png"]}
    assert [s.payload.get("keys") for s in steps if s.capability == "keyboard.shortcut"] == [
        ["ctrl", "alt", "z"],
        ["ctrl", "s"],
    ]
    with pytest.raises(drivers.DriverError):
        drivers.AdobeDriver(application=drivers.APP_ILLUSTRATOR).plan(
            "p", [drivers.DriveAction.parse("invert")], title_hint=""
        )
    with pytest.raises(drivers.DriverError):
        drivers.DriveAction.parse({"action": "resize", "percent": 900})


# ------------------------------------------------------------------------ routes


def test_the_creative_lifecycle_routes_act_through_the_application() -> None:
    h = build_harness()
    h.client.app.state.device_action = h.device
    # A washed-out image, so the enhancement below is a real step the history keeps.
    h.creative.image_provider.generated = _low_contrast_png()
    made = h.client.post(
        "/v1/creative/generate",
        json={
            "prompt": "mavi bir dalga",
            "name": "rota",
            "width": 32,
            "height": 32,
            "expectation": "mavi bir dalga",
        },
    )
    assert made.status_code == 201, made.text
    run_id = made.json()["run_id"]
    assert h.client.post(f"/v1/creative/{run_id}/undo").status_code == 422
    enhanced = h.client.post(f"/v1/creative/{run_id}/enhance", json={"kind": "auto"})
    assert enhanced.status_code == 200, enhanced.text
    history = h.client.get(f"/v1/creative/{run_id}/history")
    assert (
        history.status_code == 200
        and history.json()["index"] == 1
        and history.json()["semantic"]["ok"] is True
    )
    undone = h.client.post(f"/v1/creative/{run_id}/undo")
    assert undone.status_code == 200 and undone.json()["index"] == 0
    delivered = h.client.post(f"/v1/creative/{run_id}/deliver", json={"application": "mspaint"})
    assert delivered.status_code == 200, delivered.text
    assert h.device.payload_for("file.fetch")["application"] == "mspaint"
    assert h.client.get(f"/v1/creative/{uuid.uuid4()}/history").status_code == 404


# ------------------------------------------------------------------------- voice


@pytest.mark.parametrize(
    ("text", "intent", "focused"),
    [
        ("Bana bir logo üret: mavi bir dalga.", Intent.CREATIVE_GENERATE, False),
        ("Bir afiş oluştur: yaz konseri.", Intent.CREATIVE_GENERATE, False),
        ("Bu fotoğrafı düzelt.", Intent.CREATIVE_ENHANCE, False),
        ("Resmi netleştir.", Intent.CREATIVE_ENHANCE, False),
        ("Geri al.", Intent.CREATIVE_UNDO, True),
        ("Yinele.", Intent.CREATIVE_REDO, True),
        ("Bunu bilgisayarıma indir.", Intent.CREATIVE_DELIVER, True),
        ("Paint'te göster.", Intent.CREATIVE_DELIVER, True),
    ],
)
def test_the_router_gives_the_creative_family_its_lifecycle_words(
    text: str, intent: Intent, focused: bool
) -> None:
    resolved = resolve_intent(text, creative_focused=focused)
    assert resolved.intent is intent, (text, resolved.intent, resolved.matched)
    if intent is Intent.CREATIVE_GENERATE:
        assert (
            resolved.creative_prompt
            and "mavi bir dalga" in resolved.creative_prompt
            or "yaz konseri" in resolved.creative_prompt
        )
    if text == "Paint'te göster.":
        assert resolved.creative_application == "mspaint"


def test_without_a_creative_run_in_focus_the_lifecycle_words_keep_their_owners() -> None:
    for text in ("Geri al.", "Yinele.", "Bunu bilgisayarıma indir.", "Paint'te göster."):
        resolved = resolve_intent(text, creative_focused=False)
        assert resolved.intent not in (
            Intent.CREATIVE_UNDO,
            Intent.CREATIVE_REDO,
            Intent.CREATIVE_DELIVER,
        ), (text, resolved.intent)
    assert resolve_intent("Renkleri biraz düzelt.").intent is Intent.CREATIVE_ADJUST
    assert resolve_intent("Bu resmi Paint'te yeniden çiz.").intent is Intent.CREATIVE_REDRAW


def test_the_voice_generates_from_the_owners_own_sentence_never_the_models() -> None:
    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, "Bana bir logo üret: mavi bir dalga.")
    assert said["resolved_intents"][-1]["intent"] == "creative_generate"
    call = h.tool(
        sid, "c-1", "creative.generate", {"prompt": "the model's own idea", "name": "logo"}
    )
    assert call["status"] == "succeeded", call
    assert call["result"]["execution_status"] == "executed", call["result"]
    assert h.creative.image_provider.prompts[-1]["prompt"] == "mavi bir dalga"
    said = h.say(sid, "Geri al.", turn=2)
    assert said["resolved_intents"][-1]["intent"] == "creative_undo"
    undone = h.tool(sid, "c-2", "creative.undo", {})
    assert (
        undone["result"]["execution_status"] == "refused"
        and undone["result"]["error_class"] == "nothing_to_undo"
    )
    said = h.say(sid, "Bunu bilgisayarıma indir.", turn=3)
    assert said["resolved_intents"][-1]["intent"] == "creative_deliver"
    h.device.reset()
    delivered = h.tool(sid, "c-3", "creative.deliver", {})
    assert delivered["result"]["execution_status"] == "executed", delivered["result"]
    assert h.device.payload_for("file.fetch") is not None


def test_the_six_tools_are_registered_and_tiered() -> None:
    from app.security.step_up import TIER_SENSITIVE, tier_of
    from app.voice.realtime_sessions.tools import default_registry

    names = set(default_registry().names())
    for tool in (
        "creative.generate",
        "creative.enhance",
        "creative.undo",
        "creative.redo",
        "creative.deliver",
        "creative.drive",
    ):
        assert tool in names, tool
        assert tier_of(tool) == TIER_SENSITIVE


def test_the_corpus_fixture_run_can_be_enhanced_by_voice() -> None:
    h = build_harness()
    h.seed(CTX_CREATIVE_PAINT)
    # The fixture is a clean white canvas with a red rectangle - nothing to fix. Wash it out
    # first, as a photo that needs the fix would be.
    with h.factory() as db:
        washed = h.creative.apply(
            db,
            target="current",
            operations=[
                {"op": "color_adjust", "field": "contrast", "value": 0.3},
                {"op": "export", "format": "png"},
            ],
            session_id="seed",
        )
        assert washed["execution_status"] == "executed", washed
    sid = h.new_session()
    said = h.say(sid, "Bu fotoğrafı düzelt.")
    assert said["resolved_intents"][-1]["intent"] == "creative_enhance"
    fixed = h.tool(sid, "c-1", "creative.enhance", {})
    assert fixed["result"]["execution_status"] == "executed", fixed["result"]
    assert fixed["result"]["changed"] is True
    with h.factory() as db:
        row = db.get(CreativeRunRow, uuid.UUID(fixed["result"]["run_id"]))
        assert len(row.history_json) == 3 and "enhance" in row.history_json[-1]["ops"]
        assert json.dumps(row.history_json)
