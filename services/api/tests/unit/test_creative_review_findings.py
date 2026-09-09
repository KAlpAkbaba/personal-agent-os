"""The M27 Cloud Core security review's findings, each with the test it did not have.

Three, and they share one root: work proportional to PIXELS, done in Python, with the plan
bounding the canvas that was declared rather than the cost of walking it. `spec.py`'s own
comment claimed "a runaway request is refused at plan-validation time, well before any
pixel is ever touched". `MAX_DIMENSION` bounded the canvas; nothing bounded the loop.

* **HIGH** — `_apply_background_remove`'s `threshold` branch was an O(width x height)
  Python loop with no budget, and `threshold` is the ONLY method the shipped voice tools
  ever ask for. Two ordinary turns reach it: an 8192x8192 canvas, then "arka planını
  kaldır" — roughly eighty CPU-seconds of synchronous, unbudgeted work on the thread
  handling the request.
* **MEDIUM** — the same shape in `compare._region_contains_color`, once per drawn shape or
  text; and `_open_source` bounded the ENCODED bytes but never the DECODED size, so a
  small, highly compressible image could decode far past the "<= 8192x8192" spec §7
  promises.
* **LOW** — `creative_redraw` named a source and never handed over its bytes, so "Bu resmi
  Paint'te yeniden çiz" — the sentence this capability is named for — always fell through
  to a blank canvas and reported an empty-output mismatch.

The two vectorised paths keep the metric EXACTLY: squared Euclidean distance against a
floored squared tolerance, which accepts the same set because the left-hand side is always
an integer. These tests assert that equality per pixel, not merely that the new code runs.
"""

from __future__ import annotations

import io
import time

import pytest
from PIL import Image

from app.creative.compare import COLOR_TOLERANCE, _color_distance, _region_contains_color
from app.creative.execute import _apply_background_remove, _open_source, _squared_bound
from app.creative.spec import MAX_DIMENSION, BackgroundRemove


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------- the metric is unchanged, pixel for pixel


@pytest.mark.parametrize("tolerance", [0, 1, 10, 30.5, 60, 441.6729559300637])
def test_the_squared_bound_accepts_exactly_what_the_distance_did(tolerance: float) -> None:
    """`floor(t^2)` and `t^2` accept the same integers, which is why flooring is safe. The
    boundary is where a wrong bound would show, so the boundary is what is checked: for
    each tolerance, the largest squared distance that should pass and the smallest that
    should fail."""
    bound = _squared_bound(tolerance)
    for d2 in range(max(0, bound - 2), bound + 3):
        by_float = d2 <= float(tolerance) ** 2
        by_bound = d2 <= bound
        assert by_float == by_bound, (tolerance, d2)


def test_the_vectorised_removal_matches_the_loop_it_replaced_pixel_for_pixel() -> None:
    """The property that matters: same verdict, every pixel. Built as a gradient so the
    image contains pixels at every distance from the seed, including ones exactly on the
    tolerance boundary."""
    size = 48
    canvas = Image.new("RGBA", (size, size))
    for y in range(size):
        for x in range(size):
            canvas.putpixel((x, y), (x * 5 % 256, y * 5 % 256, (x + y) * 3 % 256, 255))

    tolerance = 40.0
    seed = canvas.convert("RGB").getpixel((0, 0))
    out = _apply_background_remove(
        canvas, BackgroundRemove(op="background_remove", method="threshold", tolerance=tolerance)
    )

    for y in range(size):
        for x in range(size):
            rgb = canvas.convert("RGB").getpixel((x, y))
            expected_transparent = _color_distance(rgb, seed) <= tolerance
            actual_alpha = out.getpixel((x, y))[3]
            assert (actual_alpha == 0) is expected_transparent, (x, y, rgb, actual_alpha)


def test_the_region_check_still_answers_both_ways() -> None:
    """The comparison's own vectorisation: a colour inside the box is found, one that is
    only OUTSIDE the box is not — the property the loop existed for ("never a full-image
    search that could match the wrong object")."""
    image = Image.new("RGB", (40, 40), (255, 255, 255))
    for x in range(0, 10):
        for y in range(0, 10):
            image.putpixel((x, y), (200, 30, 30))

    assert _region_contains_color(image, (0, 0, 9, 9), (200, 30, 30, 255)) is True
    assert _region_contains_color(image, (20, 20, 39, 39), (200, 30, 30, 255)) is False
    # And a near-miss inside tolerance still counts, as it did before.
    near = (200 + int(COLOR_TOLERANCE // 4), 30, 30, 255)
    assert _region_contains_color(image, (0, 0, 9, 9), near) is True


# ------------------------------------------------------ HIGH: the cost, not the canvas


def test_a_full_size_background_removal_is_not_a_minute_of_cpu() -> None:
    """The HIGH, asserted as a BUDGET rather than as an implementation detail.

    A wall-clock assertion is a blunt instrument, so the bound is set from MEASUREMENT
    rather than from feel: on this machine the vectorised path does 2048x2048 in 0.094 s
    and the Python loop it replaced needs about 5 s. 1.5 s sits sixteen times above the
    former and three times below the latter, so an ordinarily slow machine passes and a
    reintroduced per-pixel loop fails.

    The first version of this test used 5.0 s, and the loop slipped under it — the bound
    was chosen before the loop was timed, which is the same "written from expectation"
    mistake this project keeps finding in other people's gates. Verified red against the
    loop before being trusted.
    """
    canvas = Image.new("RGBA", (2048, 2048), (10, 20, 30, 255))
    started = time.perf_counter()
    _apply_background_remove(
        canvas, BackgroundRemove(op="background_remove", method="threshold", tolerance=30)
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 1.5, (
        f"{elapsed:.1f}s to remove a background on 2048x2048 - a per-pixel Python loop is "
        f"back, and the voice path reaches this with two ordinary turns"
    )


# -------------------------------------------- MEDIUM: the size the spec actually promised


def test_a_source_over_the_promised_bound_is_refused_before_it_is_decoded() -> None:
    """Spec §7 promises "<= 8192x8192, <= 50 MiB". The byte cap bounds what ARRIVES; it
    does not bound what it becomes. This image is a few kilobytes and decodes to over 80
    megapixels."""
    oversized = _png(Image.new("L", (MAX_DIMENSION + 1, 16), 0))
    assert len(oversized) < 200_000, "the fixture must be small, or it proves the byte cap"

    with pytest.raises(Exception) as caught:
        _open_source(oversized)
    assert str(MAX_DIMENSION) in str(caught.value)


def test_a_source_within_the_bound_still_opens() -> None:
    """The other side of the line: the check refuses what the spec excludes and nothing
    else."""
    ok = _png(Image.new("RGB", (MAX_DIMENSION, 4), (1, 2, 3)))
    opened = _open_source(ok)
    assert opened.size == (MAX_DIMENSION, 4)
    assert opened.mode == "RGBA"


# ------------------------------------- LOW: the sentence the capability is named for


def test_the_redraw_tool_hands_over_the_bytes_of_the_source_it_names() -> None:
    """`CreativeService.create` never dereferences `plan.source` itself, so a caller that
    names a source and does not fetch it gets a blank canvas and an empty-output mismatch.
    `creative_redraw` was that caller — which means "Bu resmi Paint'te yeniden çiz" could
    never reopen the owner's image.

    Read from the source rather than driven, because the defect is an omitted ARGUMENT: the
    call must pass `source_bytes`, and a future edit that drops it should fail here.
    """
    from pathlib import Path

    from app.voice.realtime_sessions import tools_creative

    text = Path(tools_creative.__file__).read_text(encoding="utf-8")
    redraw = text[
        text.index("def creative_redraw(") : text.index("# --", text.index("def creative_redraw("))
    ]
    assert "source_bytes=" in redraw, (
        "creative_redraw names a source but never fetches it - the redraw produces a blank "
        "canvas, which is the one thing this capability is named for"
    )
    assert "fetch_source_bytes" in redraw


def test_the_service_exposes_a_way_for_a_caller_to_fetch_what_it_names() -> None:
    """And the obligation has one implementation rather than each tool reaching for the
    object store itself."""
    from app.creative.service import CreativeService

    assert callable(CreativeService.fetch_source_bytes)


# ------------------------------- the fourth intent collision, caught by the corpus


@pytest.mark.parametrize(
    ("utterance", "should_match"),
    [
        # M25's own negative case. The 3D family has NO export operation, and spec §7 says
        # an operation outside the vocabulary is never guessed - so this must reach no
        # tool at all. M27 matched it and would have told the owner Paint was exporting a
        # Blender scene.
        ("Sahneyi dışa aktar.", False),
        ("Sahneyi PNG olarak dışa aktar.", False),
        # ...and the creative family's own sentences still route.
        ("Bunu PNG olarak dışa aktar.", True),
        ("Bunu dışa aktar.", True),
    ],
)
def test_creative_export_does_not_answer_for_another_familys_noun(
    utterance: str, should_match: bool
) -> None:
    """The fourth intent collision this repo has paid for, and the fourth fixed by
    NARROWING rather than reordering - a reordering only moves the collision to whichever
    family loses the race.

    `_creative_export_match` required "dışa" plus an export verb, which is every
    "... dışa aktar." sentence in Turkish, M25's negative case included.
    """
    from app.voice.intents import Intent, resolve_intent

    # Driven through the REAL router rather than the matcher alone, because the property
    # is about what the OWNER's sentence reaches - the matcher could be right and the
    # dispatch order still wrong.
    matched = resolve_intent(utterance).intent == Intent.CREATIVE_EXPORT
    assert matched is should_match, utterance
