/**
 * El hareketi kumandası (ADR-0198, Stage 1): the shared vocabulary between the pure
 * recognizer (`recognizer.ts`), the MediaPipe wrapper (`tracker.ts`) and the wiring
 * (`controller.ts`). Nothing here touches the DOM or a browser API — this module is safe
 * to import on the server (it never is, but the M18 eye's `perception.ts` sets that bar
 * and this module follows it).
 *
 * This is Stage 1 only: discrete gestures → the existing tools, through the server's
 * `app/voice/gestures.py` table (services/api). Stage 2's pinch-mouse is NOT implemented
 * here — `pinch_start`/`pinch_release` are part of the closed set and ARE emitted (so
 * Stage 2 has something to build on), but nothing in this codebase acts on them yet.
 */

/** The closed set of events the recognizer may emit — verbatim what the server's
 * `app/voice/gestures.py` `GESTURE_NAMES` accepts (an unknown name is a 422 there). */
export const GESTURE_NAMES = [
  "swipe_left",
  "swipe_right",
  "swipe_up",
  "swipe_down",
  "rotate_cw",
  "rotate_ccw",
  "spread",
  "pinch_start",
  "pinch_release",
] as const;

export type GestureName = (typeof GESTURE_NAMES)[number];

export function isGestureName(value: unknown): value is GestureName {
  return typeof value === "string" && (GESTURE_NAMES as readonly string[]).includes(value);
}

/** A 2D point in MediaPipe's own normalized landmark space (0..1 of the RAW, UNMIRRORED
 * camera frame — see recognizer.ts's module docstring for the mirroring convention). */
export type Point = { x: number; y: number };

/** MediaPipe HandLandmarker: exactly 21 landmarks per detected hand, RAW camera-space. */
export type HandLandmarks = readonly Point[];

/** MediaPipe's own handedness label. Used here only as a stable per-hand track key for
 * Stage 1 (which hand is anatomically which is irrelevant to swipe/rotate/spread/pinch) —
 * never trusted for anything privacy- or safety-relevant. */
export type HandednessLabel = "Left" | "Right";

export type HandSample = {
  handedness: HandednessLabel;
  landmarks: HandLandmarks;
};

/** One tracker tick: 0, 1 or 2 hands, at a monotonic clock reading. `t_ms` is whatever
 * clock the caller uses consistently (the recognizer only ever compares deltas within it —
 * `performance.now()` in the browser, a manual counter in tests). */
export type TrackedFrame = {
  t_ms: number;
  hands: readonly HandSample[];
};

export type GestureEvent = {
  name: GestureName;
  t_ms: number;
};
