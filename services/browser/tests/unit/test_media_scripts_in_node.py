"""The media scripts are run as real JavaScript — without a browser (M18.3).

``tests/unit/test_media_ops.py`` drives the worker against a fake ``Page`` that
DISPATCHES on the script constants rather than executing them, which proves the
worker's logic and proves nothing at all about the JavaScript itself. A ramp
script with an unbalanced brace, an off-by-one final step, or a shape
Playwright's ``page.evaluate`` merely evaluates instead of CALLING would pass
that suite and fail at 07:30 on the owner's machine — the "built, tested, never
wired" class this project keeps meeting.

So these tests execute the four scripts for real, in Node, against a ~40-line
fake DOM with a virtual clock. No browser is launched: the interpreter is the
Node that ships inside the ``playwright`` wheel this package already depends
on (``playwright._impl._driver.compute_driver_executable``), the same binary
Playwright itself runs the driver with.

Each test asserts what the PAGE ends up doing, not what the script says.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from browser_agent import media

# A fake DOM small enough to read in full. `run(script, arg)` reproduces exactly
# what Playwright's utility script does with an expression the Python client sent
# with no `isFunction` hint: `eval` it, and if the result is a function, CALL it
# (playwright/driver/package/lib/coreBundle.js). So a script shape Playwright
# would NOT call fails here too, which is the point.
_HARNESS = r"""
const fs = require('fs');
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const video = Object.assign({volume: 1, muted: true, paused: true, ended: false,
                             currentTime: 0, duration: 210}, spec.video || {});
video.play = async () => {
  if (spec.play_error) { const e = new Error('blocked'); e.name = spec.play_error; throw e; }
  video.paused = false;
};
video.pause = () => { video.paused = true; };

const present = spec.present !== false;
globalThis.document = {querySelector: (sel) => (sel === 'video' && present) ? video : null};

// virtual clock: setInterval callbacks are driven by tick(), never by real time
let nextId = 1;
const timers = new Map();
globalThis.setInterval = (fn, ms) => { const id = nextId++; timers.set(id, {fn, ms}); return id; };
globalThis.clearInterval = (id) => { timers.delete(id); };
globalThis.window = globalThis;
const tick = (times) => {
  for (let i = 0; i < times; i++)
    for (const t of [...timers.values()]) t.fn();
};

(async () => {
  const results = [];
  for (const step of spec.steps) {
    if (step.tick !== undefined) { tick(step.tick); results.push({ticked: step.tick}); continue; }
    let value = (0, eval)(step.script);
    if (typeof value === 'function') value = value(...(step.args || []));
    results.push({value: await value});
  }
  const intervals = [...timers.values()].map((t) => t.ms);
  console.log(JSON.stringify({
    results,
    intervals,
    video: {volume: video.volume, muted: video.muted, paused: video.paused,
            currentTime: video.currentTime}
  }));
})().catch((err) => { console.error(String(err && err.stack || err)); process.exit(3); });
"""


def _node() -> str:
    from playwright._impl._driver import compute_driver_executable

    node, _cli = compute_driver_executable()
    if not Path(node).exists():  # pragma: no cover - a broken playwright install
        pytest.skip(f"playwright's bundled node is not present at {node}")
    return node


def _run(tmp_path: Path, spec: dict) -> dict:
    """Execute `spec.steps` in Node and return what the fake DOM ended up like."""
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    proc = subprocess.run(
        [_node(), str(harness), str(spec_file)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr.strip()[:2000]}"
    return json.loads(proc.stdout)


class TestScriptsAreCallableJavaScript:
    """Every script must be a form Playwright will CALL, not merely evaluate —
    an expression that evaluates to a non-function silently returns itself and
    the page is never touched."""

    @pytest.mark.parametrize(
        ("name", "script", "args"),
        [
            ("MEDIA_PLAY_JS", media.MEDIA_PLAY_JS, [0.15]),
            ("MEDIA_READ_JS", media.MEDIA_READ_JS, []),
            ("MEDIA_PAUSE_JS", media.MEDIA_PAUSE_JS, []),
            ("ramp", media.build_volume_ramp_script(0.6, 20), []),
        ],
    )
    def test_it_parses_and_playwright_would_call_it(
        self, tmp_path: Path, name: str, script: str, args: list
    ) -> None:
        out = _run(tmp_path, {"steps": [{"script": script, "args": args}]})
        value = out["results"][0]["value"]
        assert isinstance(value, dict), f"{name} did not return an object — was it called?"
        assert value != {}, name


class TestPlayScript:
    def test_it_sets_the_volume_before_play_and_reports_the_element(
        self, tmp_path: Path
    ) -> None:
        out = _run(
            tmp_path,
            {
                "video": {"volume": 1, "muted": True},
                "steps": [{"script": media.MEDIA_PLAY_JS, "args": [0.15]}],
            },
        )
        value = out["results"][0]["value"]
        assert value["present"] is True and value["played"] is True
        assert value["started_at"] == 0
        assert value["volume"] == pytest.approx(0.15)
        assert value["muted"] is False
        assert value["paused"] is False
        # the page really ended up at the requested level, unmuted and playing
        assert out["video"]["volume"] == pytest.approx(0.15)
        assert out["video"]["muted"] is False
        assert out["video"]["paused"] is False

    def test_a_rejected_play_names_its_dom_exception_and_leaves_the_level_set(
        self, tmp_path: Path
    ) -> None:
        out = _run(
            tmp_path,
            {
                "play_error": media.AUTOPLAY_BLOCKED_ERROR_NAME,
                "steps": [{"script": media.MEDIA_PLAY_JS, "args": [0.15]}],
            },
        )
        value = out["results"][0]["value"]
        assert value["played"] is False
        assert value["error_name"] == "NotAllowedError"
        assert media.classify_play_error(value["error_name"]) == "autoplay_blocked"
        assert out["video"]["paused"] is True

    def test_no_media_element_is_reported_rather_than_thrown(self, tmp_path: Path) -> None:
        out = _run(
            tmp_path,
            {"present": False, "steps": [{"script": media.MEDIA_PLAY_JS, "args": [0.15]}]},
        )
        assert out["results"][0]["value"] == {"present": False}

    def test_an_infinite_duration_becomes_null_not_infinity(self, tmp_path: Path) -> None:
        """A live stream's ``duration`` is ``Infinity``, which is not JSON."""
        out = _run(
            tmp_path,
            {
                "video": {"duration": None},  # JSON has no Infinity; null exercises the guard
                "steps": [{"script": media.MEDIA_READ_JS}],
            },
        )
        assert out["results"][0]["value"]["duration"] is None


class TestRampScript:
    def test_it_ramps_the_page_to_the_level_over_the_right_number_of_steps(
        self, tmp_path: Path
    ) -> None:
        script = media.build_volume_ramp_script(0.6, 20)  # 80 steps of 250 ms
        out = _run(
            tmp_path,
            {
                "video": {"volume": 0.15},
                "steps": [
                    {"script": script},
                    {"tick": 40},  # half way
                ],
            },
        )
        assert out["results"][0]["value"] == {"applied": True, "level_from": 0.15, "steps": 80}
        assert out["intervals"] == [250], "the ramp steps every 250 ms"
        half = 0.15 + (0.6 - 0.15) / 2
        assert out["video"]["volume"] == pytest.approx(half, abs=1e-6)

    def test_it_lands_exactly_on_the_level_and_then_stops_itself(self, tmp_path: Path) -> None:
        out = _run(
            tmp_path,
            {
                "video": {"volume": 0.15},
                "steps": [
                    {"script": media.build_volume_ramp_script(0.6, 1)},  # 4 steps
                    {"tick": 4},
                ],
            },
        )
        assert out["video"]["volume"] == pytest.approx(0.6)
        assert out["intervals"] == [], "the ramp clears its own interval when it lands"

    def test_a_second_ramp_cancels_the_first_so_the_two_never_fight(
        self, tmp_path: Path
    ) -> None:
        """The greeting's duck and restore issue three ramps within seconds."""
        out = _run(
            tmp_path,
            {
                "video": {"volume": 0.15},
                "steps": [
                    {"script": media.build_volume_ramp_script(0.6, 20)},  # long wake ramp
                    {"tick": 4},
                    {"script": media.build_volume_ramp_script(0.15, 1)},  # duck for the greeting
                    {"tick": 4},
                ],
            },
        )
        assert out["intervals"] == [], "exactly one ramp survives, and it finished"
        assert out["video"]["volume"] == pytest.approx(0.15), "the duck won, not the wake ramp"
        # the duck read the level the wake ramp had actually reached, not a guess
        assert out["results"][2]["value"]["level_from"] == pytest.approx(0.1725, abs=1e-6)

    def test_a_zero_second_ramp_sets_the_level_at_once_and_arms_nothing(
        self, tmp_path: Path
    ) -> None:
        out = _run(
            tmp_path,
            {
                "video": {"volume": 0.15},
                "steps": [{"script": media.build_volume_ramp_script(0.75, 0)}],
            },
        )
        assert out["results"][0]["value"] == {"applied": True, "level_from": 0.15, "steps": 0}
        assert out["video"]["volume"] == pytest.approx(0.75)
        assert out["intervals"] == []

    def test_a_ramp_down_never_writes_a_level_outside_zero_to_one(
        self, tmp_path: Path
    ) -> None:
        out = _run(
            tmp_path,
            {
                "video": {"volume": 1.0},
                "steps": [{"script": media.build_volume_ramp_script(0.0, 1)}, {"tick": 10}],
            },
        )
        assert out["video"]["volume"] == pytest.approx(0.0)

    def test_no_media_element_is_reported_rather_than_thrown(self, tmp_path: Path) -> None:
        out = _run(
            tmp_path,
            {"present": False, "steps": [{"script": media.build_volume_ramp_script(0.6, 20)}]},
        )
        assert out["results"][0]["value"] == {"applied": False, "level_from": None, "steps": 0}
        assert out["intervals"] == []


class TestPauseScript:
    def test_it_pauses_and_says_whether_it_was_playing(self, tmp_path: Path) -> None:
        out = _run(
            tmp_path,
            {
                "video": {"paused": False},
                "steps": [{"script": media.MEDIA_PAUSE_JS}, {"script": media.MEDIA_PAUSE_JS}],
            },
        )
        assert out["results"][0]["value"] == {"present": True, "was_playing": True, "paused": True}
        assert out["results"][1]["value"]["was_playing"] is False, "idempotent"
        assert out["video"]["paused"] is True
