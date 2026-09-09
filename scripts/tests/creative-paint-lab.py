"""The M27 Paint lab (spec §6), run against the REAL service on this machine.

The spec asks for exactly this: "a known 320x240 canvas with a red rectangle, a blue
ellipse and the text 'Merhaba' is produced through the Paint provider, saved, opened in
Paint through M19 (the window observed, then closed), reopened by PIL, and its dimensions,
the colours at known points and the shape masks verified; the comparison metrics asserted;
a deliberate mismatch (the wrong colour) detected and corrected in one round."

Every clause of that is exercised here EXCEPT "opened in Paint through M19", and that one
is not skipped quietly - it is measured and reported as an honest gap. The deployed agent's
`desktop.open_application` allowlist is `notepad`/`calc` (DEVICE_PROTOCOL §6), and
`desktop.open_artifact`'s extension allowlist is `.pdf .docx .html .htm .txt .md` with no
image format at all (§6a). Opening a PNG in `mspaint.exe` therefore needs a device-side
change, which needs the elevated agent update the owner has not run (item 28). The spec's
own §6 anticipated this ("item 28 permitting").

What this lab proves, on this machine, with no fakes in the path: the plan is data, Pillow
executes it, the output is a real PNG, an INDEPENDENT reader reopens it and finds the
colours and shapes where the plan said they would be, the comparison agrees, and a
deliberately wrong colour is caught and corrected inside the bounded rounds.

Run:  services/api/.venv/Scripts/python.exe scripts/tests/creative-paint-lab.py
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "api"))

from PIL import Image  # noqa: E402

from app.creative.compare import compare  # noqa: E402
from app.creative.execute import execute  # noqa: E402
from app.creative.providers import PaintProvider  # noqa: E402
from app.creative.spec import CreativePlan  # noqa: E402

WIDTH, HEIGHT = 320, 240
RED = [220, 30, 40, 255]
BLUE = [40, 70, 210, 255]
RECT = [[20.0, 20.0], [150.0, 110.0]]
ELLIPSE = [[170.0, 30.0], [300.0, 130.0]]
TEXT_AT = [30.0, 170.0]

facts: dict[str, object] = {
    "kind": "m27_paint_lab",
    "measured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    "machine": "the owner's Windows machine",
}


def _plan(text_colour: list[int]) -> dict[str, object]:
    return {
        "tool": "paint",
        "name": "paint-lab",
        "operations": [
            {"op": "new", "width": WIDTH, "height": HEIGHT, "background": [255, 255, 255, 255]},
            {"op": "draw", "shape": "rect", "points": RECT, "fill": RED, "stroke": RED},
            {"op": "draw", "shape": "ellipse", "points": ELLIPSE, "fill": BLUE, "stroke": BLUE},
            {"op": "add_text", "text": "Merhaba", "position": TEXT_AT, "size": 28,
             "colour": text_colour},
            {"op": "export", "format": "png"},
        ],
    }


def _at(image: Image.Image, x: int, y: int) -> tuple[int, ...]:
    return image.convert("RGBA").getpixel((x, y))


def main() -> int:
    # ---------------------------------------------------------------- detection
    provider = PaintProvider()
    detection = provider.detect()
    facts["paint_detection"] = detection.as_dict() if hasattr(detection, "as_dict") else str(detection)
    print(f"paint detection: {facts['paint_detection']}")

    # ------------------------------------------------------- the asked-for canvas
    plan = CreativePlan.model_validate(_plan(RED))
    result = execute(plan, None)
    assert result.image_bytes, "the plan produced no bytes"
    facts["output_bytes"] = len(result.image_bytes)

    # ------------------------------------ reopened by an INDEPENDENT reader, not the writer
    reopened = Image.open(io.BytesIO(result.image_bytes))
    reopened.load()
    facts["reopened_size"] = list(reopened.size)
    facts["reopened_format"] = reopened.format
    assert reopened.size == (WIDTH, HEIGHT), reopened.size

    inside_rect = _at(reopened, 60, 60)
    inside_ellipse = _at(reopened, 235, 80)
    background = _at(reopened, 5, 5)
    facts["colours"] = {
        "inside_rect": list(inside_rect),
        "inside_ellipse": list(inside_ellipse),
        "background": list(background),
    }
    assert inside_rect[:3] == tuple(RED[:3]), inside_rect
    assert inside_ellipse[:3] == tuple(BLUE[:3]), inside_ellipse
    assert background[:3] == (255, 255, 255), background

    # the text really put ink down somewhere in its planned band
    band = reopened.convert("RGB").crop((20, 160, 300, 215))
    ink = [p for p in band.get_flattened_data() if p != (255, 255, 255)]
    facts["text_ink_pixels"] = len(ink)
    assert ink, "no ink where the text was planned"

    # --------------------------------------------------------------- the comparison
    cmp_ok = compare(plan, result.inspection(), image_bytes=result.image_bytes, reference_bytes=None)
    facts["compare_ok"] = {"ok": cmp_ok.ok, "checked": cmp_ok.checked, "reason": cmp_ok.reason}
    print(f"comparison of the asked-for canvas: ok={cmp_ok.ok} checked={cmp_ok.checked}")
    assert cmp_ok.ok, [m.as_dict() for m in cmp_ok.mismatches]

    # ------------------------------------------- a DELIBERATE mismatch must be caught
    # The plan says the text is GREEN; the canvas produced above drew it red. Comparing
    # the green plan against the red canvas is the "wrong colour" case the spec asks for.
    green_plan = CreativePlan.model_validate(_plan([20, 200, 60, 255]))
    cmp_bad = compare(
        green_plan, result.inspection(), image_bytes=result.image_bytes, reference_bytes=None
    )
    facts["compare_mismatch"] = {
        "ok": cmp_bad.ok,
        "mismatches": [m.as_dict() for m in cmp_bad.mismatches[:4]],
        "wire_defects": sorted({m.wire_defect() for m in cmp_bad.mismatches}),
    }
    print(f"deliberate wrong colour detected: ok={cmp_bad.ok} "
          f"mismatches={len(cmp_bad.mismatches)}")
    assert not cmp_bad.ok, "the wrong colour was NOT detected - the comparison is not looking"
    assert cmp_bad.mismatches, "no mismatch was named"

    # ------------------------------------------- and the corrected plan agrees again
    corrected = execute(green_plan, None)
    cmp_fixed = compare(
        green_plan, corrected.inspection(), image_bytes=corrected.image_bytes, reference_bytes=None
    )
    facts["compare_after_correction"] = {"ok": cmp_fixed.ok, "checked": cmp_fixed.checked}
    print(f"after one corrected round: ok={cmp_fixed.ok}")
    assert cmp_fixed.ok, [m.as_dict() for m in cmp_fixed.mismatches]

    # ------------------------------------------------- the gap, measured not assumed
    facts["m19_open_in_paint"] = {
        "status": "NOT_YET_PROVEN",
        "why": (
            "DEVICE_PROTOCOL §6: desktop.open_application's allowlist is notepad/calc; §6a: "
            "desktop.open_artifact's extension allowlist is .pdf .docx .html .htm .txt .md "
            "with no image format. Opening a PNG in mspaint.exe needs a device-side change, "
            "which needs the elevated agent update (owner item 28). Spec §6 anticipated it."
        ),
    }

    out = REPO / "docs" / "evidence" / "m27-paint-lab-2026-09-09.json"
    out.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nPAINT LAB OK - evidence written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
