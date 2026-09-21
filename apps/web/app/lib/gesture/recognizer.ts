/**
 * El hareketi kumandası (ADR-0198, Stage 1): a small, PURE, DOM-free state machine that
 * turns a stream of MediaPipe hand-landmark frames into the closed set of discrete gesture
 * events (`types.ts`'s `GESTURE_NAMES`). No `HandLandmarker`, no `<video>`, no timers of its
 * own — `ingest()` is a synchronous function of "what came in" and "what came in before"; a
 * caller drives it (`tracker.ts` in production, a hand-written frame sequence in tests).
 *
 * Not to be confused with `@mediapipe/tasks-vision`'s own `GestureRecognizer` task (a
 * different, canned gesture vocabulary) — this module does not use it and the name
 * collision is coincidental; `tracker.ts` only ever imports `HandLandmarker` from that
 * package.
 *
 * ## The mirroring convention (read this before touching any sign in this file)
 *
 * The tracker feeds this module RAW camera-space landmarks — MediaPipe's own normalized
 * [0,1] coordinates, exactly as `detectForVideo` returns them for an UNFLIPPED `<video>`
 * element (the eye's offscreen video, `lib/eye/perception.ts`, is never CSS-mirrored: it is
 * never even attached to the DOM). Because the owner faces the camera, a raw frame's x axis
 * runs backwards relative to the owner's own body: the owner's RIGHT hand appears on the
 * camera's LEFT (small x) side of the frame, the same way another person facing you has
 * their right hand on your left — exactly the reason webcam preview apps CSS-mirror what
 * they show, so that "move left as I see myself" matches "move left on screen".
 *
 * `mirror()` (below) performs that same correction ONCE, at the front door: every landmark
 * this module's geometry ever looks at has `x' = 1 - x` applied first (`y` is untouched — a
 * left-right mirror does not affect up/down). Every direction and rotation sense computed
 * anywhere else in this file is therefore already in "as the owner sees their own hand"
 * space, and reads as a person looking at a normal mirror would expect:
 * - `swipe_right` = the owner's hand moving to THEIR right (mirrored x increasing).
 * - `rotate_cw` = the motion the owner would call clockwise watching their own hand turn —
 *   which is also what "sağa çevirmek" (owner, 2026-09-21: "turning right like opening a
 *   bottle cap" → volume up) names. In mirrored screen coordinates (x right, y DOWN), that
 *   is INCREASING `atan2(dy, dx)`: at angle 0 (pointing right, 3 o'clock) increasing angle
 *   moves toward 6 o'clock (pointing down) next, which is the visually-clockwise direction
 *   on a normal clock face.
 *
 * A mirror is orientation-reversing, and that applies to ROTATION just as much as it does to
 * left/right: mirroring only the x axis turns a vector's raw angle φ into `π - φ`, whose
 * derivative is `-dφ/dt` — a raw angle that is INCREASING (in the camera's own,
 * pre-mirror view) becomes a mirrored angle that is DECREASING, i.e. `rotate_ccw`, and vice
 * versa. This is easy to get backwards when hand-building a synthetic rotating landmark
 * sequence for a test (`recognizer.test.ts`'s first draft did): a fixture whose RAW
 * thumb→index angle sweeps from 0° to 100° produces `rotate_ccw`, not `rotate_cw` — only a
 * RAW sweep from 100° down to 0° produces `rotate_cw`, the exact mirror image of the swipe
 * sign flip above (RAW x decreasing → `swipe_right`).
 *
 * `recognizer.test.ts` pins both conventions with synthetic sequences whose RAW (pre-mirror)
 * coordinates move the "wrong" way on purpose, so a future edit that drops the mirror step
 * fails loudly rather than silently reversing every left/right and every cw/ccw gesture.
 */

import { type GestureEvent, type GestureName, type HandednessLabel, type Point, type TrackedFrame } from "./types";

// --------------------------------------------------------------- landmarks

// MediaPipe HandLandmarker's 21-point topology (indices verbatim from the model's own spec).
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

function mirror(landmarks: readonly Point[]): Point[] {
  return landmarks.map((p) => ({ x: 1 - p.x, y: p.y }));
}

function dist(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Wrist→middle-MCP distance: a scale-invariant "how big is this hand in THIS frame"
 * reference, per the task brief ("thresholds relative to hand size"). Never zero. */
function handSize(m: readonly Point[]): number {
  return Math.max(dist(m[WRIST], m[MIDDLE_MCP]), 1e-6);
}

/** A stable "palm" point — the average of the wrist and the four non-thumb MCPs — used for
 * swipe tracking instead of any single fingertip, so a curling finger cannot look like
 * hand translation. */
function palmCenter(m: readonly Point[]): Point {
  const idx = [WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP];
  let x = 0;
  let y = 0;
  for (const i of idx) {
    x += m[i].x;
    y += m[i].y;
  }
  return { x: x / idx.length, y: y / idx.length };
}

/** Average (tip→MCP distance / hand size) over the four fingers that curl into a fist —
 * large when the hand is open (fingers extended away from their MCPs), small when closed
 * or curled (fingertip near its own MCP). The thumb is excluded on purpose: a "loose pinch"
 * (thumb near index, see below) still has an extended middle/ring/pinky in some owners'
 * grips, but what actually distinguishes "open flat hand for a swipe" from "closing in for a
 * pinch/rotate" is the index/middle/ring/pinky curl, not the thumb. */
function openness(m: readonly Point[]): number {
  const size = handSize(m);
  const ratios = [
    dist(m[INDEX_TIP], m[INDEX_MCP]) / size,
    dist(m[MIDDLE_TIP], m[MIDDLE_MCP]) / size,
    dist(m[RING_TIP], m[RING_MCP]) / size,
    dist(m[PINKY_TIP], m[PINKY_MCP]) / size,
  ];
  return ratios.reduce((a, b) => a + b, 0) / ratios.length;
}

/** Thumb-tip↔index-tip distance / hand size — small when pinching (tight or loose). */
function pinchRatio(m: readonly Point[]): number {
  return dist(m[THUMB_TIP], m[INDEX_TIP]) / handSize(m);
}

function thumbIndexAngle(m: readonly Point[]): number {
  const a = m[THUMB_TIP];
  const b = m[INDEX_TIP];
  return Math.atan2(b.y - a.y, b.x - a.x);
}

/** Signed shortest angular step from `a` to `b`, in (-π, π]. */
function angleStep(a: number, b: number): number {
  let d = b - a;
  while (d > Math.PI) d -= 2 * Math.PI;
  while (d <= -Math.PI) d += 2 * Math.PI;
  return d;
}

// ----------------------------------------------------------------- options

export type RecognizerOptions = {
  /** A hand counts as OPEN (eligible for a swipe) once `openness()` reaches this. */
  openHandMinRatio: number;
  /** A swipe's dominant-axis displacement must reach this fraction of frame width (mirrored
   * coordinates are 0..1, so "frame width" is 1.0 — no separate width parameter needed). */
  swipeMinDistanceFrac: number;
  /** ...within this many ms of the open-hand anchor. */
  swipeMaxMs: number;
  /** thumb-index distance / hand size at/under which a rotate's "loose pinch" precondition holds. */
  looseIndexPinchMaxRatio: number;
  /** Total accumulated rotation (degrees) needed for rotate_cw/rotate_ccw. */
  rotateMinDegrees: number;
  /** ...within this many ms of the loose-pinch anchor. */
  rotateMaxMs: number;
  /** Two-hand wrist-to-wrist distance growth needed for `spread` (frame-width fraction). */
  spreadMinDistanceFrac: number;
  /** ...within this many ms of the two-hands-visible anchor. */
  spreadMaxMs: number;
  /** thumb-index ratio at/under which a TIGHT pinch begins (`pinch_start`). */
  tightPinchOnRatio: number;
  /** thumb-index ratio at/over which a TIGHT pinch ends (`pinch_release`); hysteresis band
   * above `tightPinchOnRatio` so a ratio hovering near one threshold cannot flicker. */
  tightPinchOffRatio: number;
  /** Shared cooldown after any swipe/rotate/spread emission — the task brief's "one gesture
   * at a time" rule. Pinch start/release are edge-triggered hysteresis, not debounced
   * discrete actions, and are deliberately NOT subject to this cooldown: Stage 2's
   * pinch-mouse needs the pinch state itself, not a throttled pulse of it. */
  cooldownMs: number;
  /** How far back frame history is kept, for all windows above; must exceed every *MaxMs. */
  historyMs: number;
};

export const DEFAULT_RECOGNIZER_OPTIONS: RecognizerOptions = {
  openHandMinRatio: 0.8,
  swipeMinDistanceFrac: 0.25,
  swipeMaxMs: 600,
  looseIndexPinchMaxRatio: 0.55,
  rotateMinDegrees: 60,
  rotateMaxMs: 800,
  spreadMinDistanceFrac: 0.5,
  spreadMaxMs: 800,
  tightPinchOnRatio: 0.2,
  tightPinchOffRatio: 0.32,
  cooldownMs: 700,
  historyMs: 1_200,
};

// ------------------------------------------------------------------- track

type Sample = { t_ms: number; m: Point[]; palm: Point; open: boolean; loosePinch: boolean; angle: number };

type PinchState = "idle" | "pinched";

type HandTrack = {
  buffer: Sample[];
  /** `t_ms` since this hand became continuously open, or `null`; reset on any non-open sample. */
  openSince: number | null;
  /** `t_ms` since this hand became continuously loose-pinched, or `null`. */
  looseSince: number | null;
  /** Cumulative signed rotation (degrees) accumulated since `looseSince`. */
  rotationDeg: number;
  pinchState: PinchState;
};

function emptyTrack(): HandTrack {
  return { buffer: [], openSince: null, looseSince: null, rotationDeg: 0, pinchState: "idle" };
}

// -------------------------------------------------------------- recognizer

export class GestureRecognizer {
  private readonly opts: RecognizerOptions;
  private readonly tracks = new Map<HandednessLabel, HandTrack>();
  private twoHandsSince: number | null = null;
  private spreadBuffer: Array<{ t_ms: number; dist: number }> = [];
  private lastEmitAtMs: number | null = null;

  constructor(options: Partial<RecognizerOptions> = {}) {
    this.opts = { ...DEFAULT_RECOGNIZER_OPTIONS, ...options };
  }

  /** Feed one tracker tick; returns the gesture events (usually 0 or 1 non-pinch event, plus
   * at most one pinch_start/pinch_release per hand) this tick produced. */
  ingest(frame: TrackedFrame): GestureEvent[] {
    const events: GestureEvent[] = [];
    const seen = new Set<HandednessLabel>();

    for (const hand of frame.hands) {
      seen.add(hand.handedness);
      const track = this.tracks.get(hand.handedness) ?? emptyTrack();
      this.tracks.set(hand.handedness, track);
      this.updateTrack(track, hand.landmarks, frame.t_ms);

      // Pinch hysteresis: independent of the cooldown, one edge per tick at most.
      const ratio = pinchRatio(mirror(hand.landmarks));
      if (track.pinchState === "idle" && ratio <= this.opts.tightPinchOnRatio) {
        track.pinchState = "pinched";
        events.push({ name: "pinch_start", t_ms: frame.t_ms });
      } else if (track.pinchState === "pinched" && ratio >= this.opts.tightPinchOffRatio) {
        track.pinchState = "idle";
        events.push({ name: "pinch_release", t_ms: frame.t_ms });
      }
    }
    // A hand that left the frame stops accumulating (its track simply is not updated again);
    // Stage 1 does not need to synthesize a pinch_release for a hand that vanished mid-pinch.

    // "One gesture at a time": at most one non-pinch (swipe/rotate/spread) event per tick,
    // spread checked first (it needs both hands and is the most specific precondition).
    const nonPinch = this.detectSpread(frame) ?? this.detectPerHand(frame);
    if (nonPinch) events.push(nonPinch);

    if (seen.size < 2) this.twoHandsSince = null;
    return events;
  }

  private updateTrack(track: HandTrack, raw: readonly Point[], t_ms: number): void {
    const m = mirror(raw);
    const isOpen = openness(m) >= this.opts.openHandMinRatio;
    const loose = pinchRatio(m) <= this.opts.looseIndexPinchMaxRatio;
    const angle = thumbIndexAngle(m);

    if (isOpen) track.openSince ??= t_ms;
    else track.openSince = null;

    if (loose) {
      if (track.looseSince === null) {
        track.looseSince = t_ms;
        track.rotationDeg = 0;
      } else {
        const prev = track.buffer.at(-1);
        if (prev && prev.loosePinch) track.rotationDeg += (angleStep(prev.angle, angle) * 180) / Math.PI;
      }
    } else {
      track.looseSince = null;
      track.rotationDeg = 0;
    }

    track.buffer.push({ t_ms, m, palm: palmCenter(m), open: isOpen, loosePinch: loose, angle });
    const cutoff = t_ms - this.opts.historyMs;
    while (track.buffer.length > 1 && track.buffer[0].t_ms < cutoff) track.buffer.shift();
  }

  private onCooldown(now: number): boolean {
    return this.lastEmitAtMs !== null && now - this.lastEmitAtMs < this.opts.cooldownMs;
  }

  private detectPerHand(frame: TrackedFrame): GestureEvent | null {
    for (const hand of frame.hands) {
      const track = this.tracks.get(hand.handedness);
      if (!track) continue;
      const swipe = this.detectSwipe(track, frame.t_ms);
      if (swipe) return swipe;
      const rotate = this.detectRotate(track, frame.t_ms);
      if (rotate) return rotate;
    }
    return null;
  }

  private detectSwipe(track: HandTrack, now: number): GestureEvent | null {
    if (track.openSince === null) return null;
    const current = track.buffer.at(-1);
    if (!current || !current.open) return null;
    const anchorTime = Math.max(track.openSince, now - this.opts.swipeMaxMs);
    const anchor = track.buffer.find((s) => s.t_ms >= anchorTime && s.open);
    if (!anchor || anchor.t_ms >= now) return null;
    const dx = current.palm.x - anchor.palm.x;
    const dy = current.palm.y - anchor.palm.y;
    const horizontal = Math.abs(dx) >= Math.abs(dy);
    const magnitude = horizontal ? Math.abs(dx) : Math.abs(dy);
    if (magnitude < this.opts.swipeMinDistanceFrac) return null;
    // A qualifying displacement always resets the anchor — emitted or not — so the same
    // already-crossed motion cannot re-fire the instant the cooldown lapses without any
    // fresh movement (see the module docstring's cooldown discussion).
    track.openSince = now;
    if (this.onCooldown(now)) return null;
    this.lastEmitAtMs = now;
    const name: GestureName = horizontal ? (dx > 0 ? "swipe_right" : "swipe_left") : dy > 0 ? "swipe_down" : "swipe_up";
    return { name, t_ms: now };
  }

  private detectRotate(track: HandTrack, now: number): GestureEvent | null {
    if (track.looseSince === null) return null;
    const current = track.buffer.at(-1);
    if (!current || !current.loosePinch) return null;
    if (now - track.looseSince > this.opts.rotateMaxMs) {
      // The window slid past without reaching the threshold: start a fresh window rather
      // than let a stale, capped accumulation linger.
      track.looseSince = now;
      track.rotationDeg = 0;
      return null;
    }
    const degrees = track.rotationDeg;
    if (Math.abs(degrees) < this.opts.rotateMinDegrees) return null;
    track.looseSince = now;
    track.rotationDeg = 0;
    if (this.onCooldown(now)) return null;
    this.lastEmitAtMs = now;
    return { name: degrees > 0 ? "rotate_cw" : "rotate_ccw", t_ms: now };
  }

  private detectSpread(frame: TrackedFrame): GestureEvent | null {
    if (frame.hands.length < 2) return null;
    const [a, b] = frame.hands;
    const ma = mirror(a.landmarks);
    const mb = mirror(b.landmarks);
    const distance = dist(ma[WRIST], mb[WRIST]);
    const now = frame.t_ms;
    if (this.twoHandsSince === null) this.twoHandsSince = now;
    this.spreadBuffer.push({ t_ms: now, dist: distance });
    const cutoff = now - this.opts.spreadMaxMs;
    while (this.spreadBuffer.length > 1 && this.spreadBuffer[0].t_ms < cutoff) this.spreadBuffer.shift();
    const anchorTime = Math.max(this.twoHandsSince, cutoff);
    const anchor = this.spreadBuffer.find((s) => s.t_ms >= anchorTime);
    if (!anchor || anchor.t_ms >= now) return null;
    const growth = distance - anchor.dist;
    if (growth < this.opts.spreadMinDistanceFrac) return null;
    this.twoHandsSince = now;
    this.spreadBuffer = [{ t_ms: now, dist: distance }];
    if (this.onCooldown(now)) return null;
    this.lastEmitAtMs = now;
    return { name: "spread", t_ms: now };
  }
}
