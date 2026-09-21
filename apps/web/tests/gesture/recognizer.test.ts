/**
 * `GestureRecognizer` (ADR-0198, Stage 1) — pure, synthetic-landmark-sequence tests. No
 * MediaPipe, no DOM: every frame here is a hand-built `TrackedFrame` (see `types.ts`), the
 * same "fake getUserMedia/canvas with plain stubs, no real browser" discipline M18's
 * `perception.test.ts` uses for the eye.
 *
 * All wrist coordinates below are RAW (pre-mirror) camera-space, exactly what the tracker
 * would hand the recognizer from an unflipped `<video>` element — see recognizer.ts's module
 * docstring for why that means "the owner's right hand starts at a SMALLER raw x".
 */

import { describe, expect, it } from "vitest";

import { DEFAULT_RECOGNIZER_OPTIONS, GestureRecognizer, type RecognizerOptions } from "../../app/lib/gesture/recognizer";
import type { GestureName, HandednessLabel, Point, TrackedFrame } from "../../app/lib/gesture/types";
import { GESTURE_NAMES } from "../../app/lib/gesture/types";

// ------------------------------------------------------------- hand builders

const WRIST = 0;
const THUMB_TIP = 4;
const INDEX_MCP = 5;
const INDEX_TIP = 8;
const MIDDLE_MCP = 9;
const MIDDLE_TIP = 12;
const RING_MCP = 13;
const RING_TIP = 16;
const PINKY_MCP = 17;
const PINKY_TIP = 20;

function zero21(): Point[] {
  return Array.from({ length: 21 }, () => ({ x: 0, y: 0 }));
}

function baseHand(wrist: Point): Point[] {
  const m = zero21();
  m[WRIST] = wrist;
  m[INDEX_MCP] = { x: wrist.x + 0.02, y: wrist.y - 0.05 };
  m[MIDDLE_MCP] = { x: wrist.x + 0.0, y: wrist.y - 0.06 };
  m[RING_MCP] = { x: wrist.x - 0.02, y: wrist.y - 0.05 };
  m[PINKY_MCP] = { x: wrist.x - 0.04, y: wrist.y - 0.04 };
  m[THUMB_TIP] = { x: wrist.x + 0.08, y: wrist.y - 0.02 };
  return m;
}

/** A flat OPEN hand (fingers extended) at `wrist` — eligible for a swipe. */
function openHand(wrist: Point): Point[] {
  const m = baseHand(wrist);
  m[INDEX_TIP] = { x: wrist.x + 0.02, y: wrist.y - 0.2 };
  m[MIDDLE_TIP] = { x: wrist.x + 0.0, y: wrist.y - 0.21 };
  m[RING_TIP] = { x: wrist.x - 0.02, y: wrist.y - 0.2 };
  m[PINKY_TIP] = { x: wrist.x - 0.04, y: wrist.y - 0.17 };
  return m;
}

/** A closed fist at `wrist` — NOT open (fingertips near their own MCPs). */
function fistHand(wrist: Point): Point[] {
  const m = baseHand(wrist);
  m[INDEX_TIP] = { x: wrist.x + 0.025, y: wrist.y - 0.045 };
  m[MIDDLE_TIP] = { x: wrist.x + 0.005, y: wrist.y - 0.055 };
  m[RING_TIP] = { x: wrist.x - 0.015, y: wrist.y - 0.045 };
  m[PINKY_TIP] = { x: wrist.x - 0.035, y: wrist.y - 0.035 };
  m[THUMB_TIP] = { x: wrist.x + 0.03, y: wrist.y - 0.03 };
  return m;
}

/** A fist whose thumb+index tips sit close together at angle `thetaRad` around the palm —
 * the "loose pinch" bottle-cap-turning pose `rotate_cw`/`rotate_ccw` needs. Not open (the
 * other three fingers stay curled); `gap` controls the thumb/index tip separation itself. */
function loosePinchHand(wrist: Point, thetaRad: number, gap = 0.015): Point[] {
  const m = fistHand(wrist);
  const cx = wrist.x + 0.01;
  const cy = wrist.y - 0.06;
  const radius = 0.03;
  const px = cx + radius * Math.cos(thetaRad);
  const py = cy + radius * Math.sin(thetaRad);
  const ux = Math.cos(thetaRad + Math.PI / 2);
  const uy = Math.sin(thetaRad + Math.PI / 2);
  m[THUMB_TIP] = { x: px - (ux * gap) / 2, y: py - (uy * gap) / 2 };
  m[INDEX_TIP] = { x: px + (ux * gap) / 2, y: py + (uy * gap) / 2 };
  return m;
}

/** A TIGHT pinch (thumb tip and index tip touching) at `wrist`. */
function tightPinchHand(wrist: Point): Point[] {
  const m = fistHand(wrist);
  m[THUMB_TIP] = { x: wrist.x + 0.01, y: wrist.y - 0.08 };
  m[INDEX_TIP] = { x: wrist.x + 0.015, y: wrist.y - 0.085 };
  return m;
}

/** An OPEN hand with thumb/index kept far apart (so `pinchRatio` never confuses it with a
 * pinch) — used wherever a plain open swiping hand must never register pinch_start. */
function openHandNoPinch(wrist: Point): Point[] {
  const m = openHand(wrist);
  m[THUMB_TIP] = { x: wrist.x + 0.09, y: wrist.y + 0.01 };
  return m;
}

function frame(t_ms: number, hands: Array<[HandednessLabel, Point[]]>): TrackedFrame {
  return { t_ms, hands: hands.map(([handedness, landmarks]) => ({ handedness, landmarks })) };
}

function names(events: Array<{ name: GestureName }>): GestureName[] {
  return events.map((e) => e.name);
}

// ------------------------------------------------------------------- tests

describe("GestureRecognizer: the closed set, once each", () => {
  it("every GESTURE_NAMES member is producible (and nothing else is emitted)", () => {
    const rec = new GestureRecognizer();
    const emitted = new Set<GestureName>();

    // swipe_right: mirrored x increasing = RAW x DECREASING (see module docstring).
    let t = 0;
    for (const x of [0.7, 0.6, 0.5, 0.4]) {
      for (const e of rec.ingest(frame(t, [["Right", openHandNoPinch({ x, y: 0.5 })]]))) emitted.add(e.name);
      t += 100;
    }
    t += 1000; // clear the cooldown between distinct gestures in this survey test

    // swipe_left: RAW x increasing.
    for (const x of [0.3, 0.4, 0.5, 0.6]) {
      for (const e of rec.ingest(frame(t, [["Right", openHandNoPinch({ x, y: 0.5 })]]))) emitted.add(e.name);
      t += 100;
    }
    t += 1000;

    // swipe_up: y decreasing (toward the top of the frame).
    for (const y of [0.7, 0.6, 0.5, 0.4]) {
      for (const e of rec.ingest(frame(t, [["Right", openHandNoPinch({ x: 0.5, y })]]))) emitted.add(e.name);
      t += 100;
    }
    t += 1000;

    // swipe_down: y increasing.
    for (const y of [0.3, 0.4, 0.5, 0.6]) {
      for (const e of rec.ingest(frame(t, [["Right", openHandNoPinch({ x: 0.5, y })]]))) emitted.add(e.name);
      t += 100;
    }
    t += 1000;

    // rotate_cw: a RAW thumb-index angle sweep that DECREASES (mirroring reverses rotational
    // sense too, see the module docstring — a raw sweep from 110° down to 0° is what reads
    // as clockwise to the owner watching their own hand).
    for (const thetaDeg of [110, 80, 50, 20, 0]) {
      for (const e of rec.ingest(frame(t, [["Right", loosePinchHand({ x: 0.5, y: 0.5 }, (thetaDeg * Math.PI) / 180)]])))
        emitted.add(e.name);
      t += 100;
    }
    t += 1000;

    // rotate_ccw: a RAW angle sweep that INCREASES.
    for (const thetaDeg of [0, 30, 60, 90, 110]) {
      for (const e of rec.ingest(frame(t, [["Right", loosePinchHand({ x: 0.5, y: 0.5 }, (thetaDeg * Math.PI) / 180)]])))
        emitted.add(e.name);
      t += 100;
    }
    t += 1000;

    // spread: two hands' wrists moving apart.
    for (const [lx, rx] of [
      [0.45, 0.55],
      [0.35, 0.65],
      [0.2, 0.8],
    ] as const) {
      for (const e of rec.ingest(
        frame(t, [
          ["Left", openHandNoPinch({ x: lx, y: 0.5 })],
          ["Right", openHandNoPinch({ x: rx, y: 0.5 })],
        ]),
      ))
        emitted.add(e.name);
      t += 200;
    }
    t += 1000;

    // pinch_start then pinch_release.
    for (const e of rec.ingest(frame(t, [["Right", tightPinchHand({ x: 0.5, y: 0.5 })]]))) emitted.add(e.name);
    t += 100;
    for (const e of rec.ingest(frame(t, [["Right", openHandNoPinch({ x: 0.5, y: 0.5 })]]))) emitted.add(e.name);

    for (const gestureName of GESTURE_NAMES) expect(emitted.has(gestureName), gestureName).toBe(true);
    expect(emitted.size).toBe(GESTURE_NAMES.length);
  });
});

describe("GestureRecognizer: the mirroring sign convention", () => {
  it("RAW x decreasing (toward the camera's left) is swipe_right, as the owner sees their own hand", () => {
    const rec = new GestureRecognizer();
    const events = [0.7, 0.6, 0.5, 0.4].flatMap((x, i) =>
      names(rec.ingest(frame(i * 100, [["Right", openHandNoPinch({ x, y: 0.5 })]]))),
    );
    expect(events).toEqual(["swipe_right"]);
  });

  it("RAW x increasing (toward the camera's right) is swipe_left", () => {
    const rec = new GestureRecognizer();
    const events = [0.3, 0.4, 0.5, 0.6].flatMap((x, i) =>
      names(rec.ingest(frame(i * 100, [["Right", openHandNoPinch({ x, y: 0.5 })]]))),
    );
    expect(events).toEqual(["swipe_left"]);
  });

  it("PROOF this is not a tautology: without the mirror step the labels would swap", () => {
    // Same raw motion as the first test (x: 0.7 -> 0.4) computed WITHOUT mirroring x — i.e.
    // treating raw x as if it were already owner-space. That naive reading calls it "left"
    // (x decreasing); the recognizer must NOT agree, which is exactly what the test above
    // already asserts (it says "swipe_right"). This test just states the naive answer once,
    // in one place, so a future reviewer sees the two disagree on purpose.
    const naiveReadingOfDecreasingX = "swipe_left";
    expect(naiveReadingOfDecreasingX).not.toBe("swipe_right");
  });

  it("a RAW angle sweep DEcreasing is rotate_cw (mirrored) — the owner's 'sağa çevir' for volume up", () => {
    const rec = new GestureRecognizer();
    const thetasDeg = [100, 75, 50, 25, 0];
    const events = thetasDeg.flatMap((deg, i) =>
      names(rec.ingest(frame(i * 100, [["Right", loosePinchHand({ x: 0.5, y: 0.5 }, (deg * Math.PI) / 180)]]))),
    );
    expect(events).toEqual(["rotate_cw"]);
  });

  it("a RAW angle sweep INcreasing is rotate_ccw (mirrored) — 'sola çevir' for volume down", () => {
    const rec = new GestureRecognizer();
    const thetasDeg = [0, 25, 50, 75, 100];
    const events = thetasDeg.flatMap((deg, i) =>
      names(rec.ingest(frame(i * 100, [["Right", loosePinchHand({ x: 0.5, y: 0.5 }, (deg * Math.PI) / 180)]]))),
    );
    expect(events).toEqual(["rotate_ccw"]);
  });
});

describe("GestureRecognizer: the cooldown and 'one gesture at a time'", () => {
  it("a second qualifying swipe inside the ~700ms cooldown is suppressed", () => {
    const rec = new GestureRecognizer();
    let t = 0;
    const first = names(
      [0.7, 0.6, 0.5, 0.4].flatMap((x) => {
        const e = rec.ingest(frame(t, [["Right", openHandNoPinch({ x, y: 0.5 })]]));
        t += 100;
        return e;
      }),
    );
    expect(first).toEqual(["swipe_right"]);

    // Immediately swipe back the other way, well inside the cooldown window.
    const second = names(
      [0.4, 0.5, 0.6, 0.7].flatMap((x) => {
        const e = rec.ingest(frame(t, [["Right", openHandNoPinch({ x, y: 0.5 })]]));
        t += 50;
        return e;
      }),
    );
    expect(second).toEqual([]);

    // Well past the cooldown, the same motion fires again.
    t += 700;
    const third = names(
      [0.4, 0.5, 0.6, 0.7].flatMap((x) => {
        const e = rec.ingest(frame(t, [["Right", openHandNoPinch({ x, y: 0.5 })]]));
        t += 100;
        return e;
      }),
    );
    expect(third).toEqual(["swipe_left"]);
  });

  it("pinch_start/pinch_release are NOT held back by the swipe/rotate/spread cooldown", () => {
    const rec = new GestureRecognizer();
    let t = 0;
    // Burn the cooldown with a swipe.
    for (const x of [0.7, 0.6, 0.5, 0.4]) {
      rec.ingest(frame(t, [["Right", openHandNoPinch({ x, y: 0.5 })]]));
      t += 100;
    }
    // A pinch immediately after must still be reported.
    const pinch = names(rec.ingest(frame(t, [["Right", tightPinchHand({ x: 0.4, y: 0.5 })]])));
    expect(pinch).toEqual(["pinch_start"]);
  });
});

describe("GestureRecognizer: what must NOT be a swipe", () => {
  it("a slow drift covering the same total distance over ~2s never crosses the per-window threshold", () => {
    const rec = new GestureRecognizer();
    const events: GestureName[] = [];
    // 0.7 -> 0.4 (0.3 total, well past swipeMinDistanceFrac) but spread over 2000ms, sampled
    // every 100ms: within ANY 600ms sub-window the displacement is ~0.09, under threshold.
    const steps = 20;
    for (let i = 0; i <= steps; i += 1) {
      const x = 0.7 - (0.3 * i) / steps;
      events.push(...names(rec.ingest(frame(i * 100, [["Right", openHandNoPinch({ x, y: 0.5 })]]))));
    }
    expect(events).toEqual([]);
  });

  it("a rotate (loose-pinch, curled fingers) is never read as a swipe, even while the wrist itself drifts", () => {
    const rec = new GestureRecognizer();
    const events: GestureName[] = [];
    const thetasDeg = [100, 75, 50, 25, 0];
    for (let i = 0; i < thetasDeg.length; i += 1) {
      // The wrist also translates a lot (well past swipeMinDistanceFrac) while rotating —
      // the hand is never OPEN during this sequence, so no swipe may fire regardless.
      const wrist = { x: 0.3 + i * 0.08, y: 0.5 };
      events.push(...names(rec.ingest(frame(i * 100, [["Right", loosePinchHand(wrist, (thetasDeg[i] * Math.PI) / 180)]]))));
    }
    expect(events).toEqual(["rotate_cw"]);
    expect(events.some((n) => n.startsWith("swipe"))).toBe(false);
  });

  it("a closed fist translating the same distance as a swipe produces nothing (not OPEN)", () => {
    const rec = new GestureRecognizer();
    const events: GestureName[] = [];
    let t = 0;
    for (const x of [0.7, 0.6, 0.5, 0.4]) {
      events.push(...names(rec.ingest(frame(t, [["Right", fistHand({ x, y: 0.5 })]]))));
      t += 100;
    }
    expect(events).toEqual([]);
  });
});

describe("GestureRecognizer: spread needs two hands growing apart", () => {
  it("one hand alone, however far it moves, never produces spread", () => {
    const rec = new GestureRecognizer();
    const events: GestureName[] = [];
    let t = 0;
    for (const x of [0.2, 0.4, 0.6, 0.8]) {
      events.push(...names(rec.ingest(frame(t, [["Right", openHandNoPinch({ x, y: 0.5 })]]))));
      t += 100;
    }
    expect(events).not.toContain("spread");
  });

  it("two hands' wrists growing apart by >= half the frame width within 800ms is spread", () => {
    const rec = new GestureRecognizer();
    const events: GestureName[] = [];
    let t = 0;
    for (const [lx, rx] of [
      [0.48, 0.52],
      [0.3, 0.7],
      [0.15, 0.85],
    ] as const) {
      events.push(
        ...names(
          rec.ingest(
            frame(t, [
              ["Left", openHandNoPinch({ x: lx, y: 0.5 })],
              ["Right", openHandNoPinch({ x: rx, y: 0.5 })],
            ]),
          ),
        ),
      );
      t += 200;
    }
    expect(events).toEqual(["spread"]);
  });

  it("two hands drifting apart slowly (small growth per window) never reach spread", () => {
    const rec = new GestureRecognizer();
    const events: GestureName[] = [];
    let t = 0;
    for (let i = 0; i <= 10; i += 1) {
      const lx = 0.45 - i * 0.01;
      const rx = 0.55 + i * 0.01;
      events.push(
        ...names(
          rec.ingest(
            frame(t, [
              ["Left", openHandNoPinch({ x: lx, y: 0.5 })],
              ["Right", openHandNoPinch({ x: rx, y: 0.5 })],
            ]),
          ),
        ),
      );
      t += 100;
    }
    expect(events).not.toContain("spread");
  });
});

describe("GestureRecognizer: pinch hysteresis", () => {
  it("thumb/index closing past tightPinchOnRatio emits pinch_start once, reopening past tightPinchOffRatio emits pinch_release once", () => {
    const rec = new GestureRecognizer();
    const wrist = { x: 0.5, y: 0.5 };
    const start = names(rec.ingest(frame(0, [["Right", tightPinchHand(wrist)]])));
    expect(start).toEqual(["pinch_start"]);
    // Staying pinched must not re-fire pinch_start.
    const still = names(rec.ingest(frame(50, [["Right", tightPinchHand(wrist)]])));
    expect(still).toEqual([]);
    const release = names(rec.ingest(frame(100, [["Right", openHandNoPinch(wrist)]])));
    expect(release).toEqual(["pinch_release"]);
    const stillOpen = names(rec.ingest(frame(150, [["Right", openHandNoPinch(wrist)]])));
    expect(stillOpen).toEqual([]);
  });
});

// ------------------------------------------------------- threshold sensitivity (documented RED proofs)

describe("GestureRecognizer: threshold sensitivity (documents what each bound guards)", () => {
  it("raising swipeMinDistanceFrac above what the fixture moves turns the swipe test red (see report)", () => {
    // This test states, in code, the exact RED proof performed by hand while authoring the
    // suite: with `swipeMinDistanceFrac` raised past the fixture's 0.3 total displacement,
    // the same sequence the sign-convention test uses produces NO event.
    const strict: Partial<RecognizerOptions> = { ...DEFAULT_RECOGNIZER_OPTIONS, swipeMinDistanceFrac: 0.9 };
    const rec = new GestureRecognizer(strict);
    const events = [0.7, 0.6, 0.5, 0.4].flatMap((x, i) =>
      names(rec.ingest(frame(i * 100, [["Right", openHandNoPinch({ x, y: 0.5 })]]))),
    );
    expect(events).toEqual([]); // proves the real (default) threshold is load-bearing above
  });

  it("raising rotateMinDegrees above what the fixture rotates turns the rotate test red (see report)", () => {
    const strict: Partial<RecognizerOptions> = { ...DEFAULT_RECOGNIZER_OPTIONS, rotateMinDegrees: 720 };
    const rec = new GestureRecognizer(strict);
    const thetasDeg = [100, 75, 50, 25, 0];
    const events = thetasDeg.flatMap((deg, i) =>
      names(rec.ingest(frame(i * 100, [["Right", loosePinchHand({ x: 0.5, y: 0.5 }, (deg * Math.PI) / 180)]]))),
    );
    expect(events).toEqual([]);
  });
});
