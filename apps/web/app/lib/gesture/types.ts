/**
 * El hareketi kumandası (ADR-0198 Stage 1, ADR-0199 Stage 2): the shared vocabulary
 * between the pure recognizer (`recognizer.ts`), the MediaPipe wrapper (`tracker.ts`) and
 * the wiring (`controller.ts`). Nothing here touches the DOM or a browser API — this
 * module is safe to import on the server (it never is, but the M18 eye's `perception.ts`
 * sets that bar and this module follows it).
 *
 * Two families live in the SAME closed set, but travel differently once emitted:
 * - Stage 1's discrete gestures (`swipe_*`, `rotate_*`, `spread`, `gather`,
 *   `pinch_start`/`pinch_release`) → the server's `app/voice/gestures.py` table, as a
 *   `gesture` client event (`LocalVoiceMode.dispatchGesture`).
 * - Stage 2's pointer events (`mouse_*`, `drag_*`, `left_click`, `right_click`) → NEVER a
 *   `gesture` client event (the server's closed set does not know them and would 422);
 *   `GestureController` routes them to `lib/gesture/pointer.ts`'s `PointerStreamClient`
 *   instead, which speaks the pointer WebSocket (ADR-0199) directly.
 */

/** Stage 1's discrete gestures — verbatim what the server's `app/voice/gestures.py`
 * `GESTURE_NAMES` accepts (an unknown name is a 422 there). */
export const SERVER_GESTURE_NAMES = [
  "swipe_left",
  "swipe_right",
  "swipe_up",
  "swipe_down",
  "rotate_cw",
  "rotate_ccw",
  "spread",
  "gather",
  "pinch_start",
  "pinch_release",
] as const;

/** Stage 2 (ADR-0199): the pinch-mouse and the fist-drag. Consumed locally by
 * `pointer.ts` — never posted to the server as a `gesture` client event. `mouse_move` and
 * `drag_move` carry `dx`/`dy` (see `GestureEvent`); the rest are discrete edges. */
export const POINTER_GESTURE_NAMES = [
  "mouse_start",
  "mouse_move",
  "mouse_end",
  "left_click",
  "right_click",
  "drag_start",
  "drag_move",
  "drag_end",
] as const;

/** The full closed set the recognizer may emit — the union of both families above. */
export const GESTURE_NAMES = [...SERVER_GESTURE_NAMES, ...POINTER_GESTURE_NAMES] as const;

export type ServerGestureName = (typeof SERVER_GESTURE_NAMES)[number];
export type PointerGestureName = (typeof POINTER_GESTURE_NAMES)[number];
export type GestureName = (typeof GESTURE_NAMES)[number];

export function isGestureName(value: unknown): value is GestureName {
  return typeof value === "string" && (GESTURE_NAMES as readonly string[]).includes(value);
}

/** True for a Stage 2 pointer event — the set `GestureController` must route to
 * `PointerStreamClient` instead of `LocalVoiceMode.dispatchGesture`. */
export function isPointerGestureName(value: unknown): value is PointerGestureName {
  return typeof value === "string" && (POINTER_GESTURE_NAMES as readonly string[]).includes(value);
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
  /** ADR-0199: `mouse_move`/`drag_move` only — the palm's frame-to-frame displacement, in
   * FRAME units (mirrored, same units `swipeMinDistanceFrac` uses — frame width/height is
   * 1.0), already past the recognizer's dead zone. `pointer.ts` turns this into device
   * pixels (`dx * screen.width * gain`). Absent on every other event. */
  dx?: number;
  dy?: number;
};
