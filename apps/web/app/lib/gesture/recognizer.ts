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
 *
 * ## Stage 2 (ADR-0199): the pinch-mouse and the fist-drag
 *
 * `updatePointerState()` (near the bottom) adds the pinch-mouse and the fist-drag on top of
 * everything above, per hand, independent of the swipe/rotate/spread machinery. Both ride
 * hysteresis this file already computes:
 * - MOUSE MODE rides the exact same "ring" pinch `pinch_start`/`pinch_release` already
 *   detects (thumb-index `pinchRatio()` <= `tightPinchOnRatio` with `openness()` >=
 *   `pinchMinOpenness`) — held continuously for `mouseStartHoldMs` → `mouse_start`; the
 *   palm's frame-to-frame displacement (dead-zoned) → `mouse_move`; the ring opening back to
 *   `tightPinchOffRatio` → `mouse_end`.
 * - A CLICK is a NESTED, deeper pinch inside mouse mode: the ratio dropping to
 *   `tightPinchOnRatio * clickPinchRatioFrac` ("tips pressed") and back up is a `left_click`
 *   when released within `leftClickMaxMs`, a `right_click` when held at least
 *   `rightClickMinMs` before releasing (a release between the two is ambiguous and
 *   swallowed on purpose — an owner's brief, uncertain wobble should not fire either
 *   button). Resolved BEFORE `mouse_end` on the same frame, so a fast full-open that skips
 *   past both thresholds in one frame still counts as a click, not a dropped one (owner,
 *   second trial: "hızlı yaptığımda da algılasın").
 * - DRAG rides the same openness `fistMaxOpenness` fist detection: continuously fist for
 *   `dragStartHoldMs` → `drag_start`; palm displacement (dead-zoned) → `drag_move`; opening
 *   past `fistMaxOpenness` → `drag_end`. Mouse mode and drag can never both be active on one
 *   hand — `pinchMinOpenness` (0.35) sits ABOVE `fistMaxOpenness` (0.34) on purpose (see the
 *   options' own comment), so a fist never also reads as a ring pinch.
 *
 * Stage 2's events are gated by the SAME engagement gate (`isArmed`) as everything else —
 * and, per the task brief, mouse/drag mode EXTENDS it on every start/move/click/end, so a
 * held pinch or fist never has to fight the gate's 4 s window mid-gesture — but are
 * deliberately NOT run through `emit()`'s shared `cooldownMs`/`returnSuppressMs`, for the
 * same reason `pinch_start`/`pinch_release` already are not (see `RecognizerOptions`
 * docstring): they are edge-triggered continuous state, not one-shot discrete commands, and
 * the owner's own click timing (350 ms / 600 ms) is far tighter than the swipe family's
 * shared 450 ms cooldown would allow — a `left_click` following a `mouse_start` by 200 ms
 * must never be swallowed by the OTHER family's cooldown timer.
 */

import { type GestureEvent, type GestureName, type HandednessLabel, type Point, type TrackedFrame } from "./types";

/** What one frame measures as, for the owner's calibration readout (`GestureRecognizer.measure`). */
export type FrameMeasure = {
  hands: number;
  /** Per hand, `openness()` (>= openHandMinRatio counts as open). */
  openness: number[];
  /** Per hand, the thumb-index `pinchRatio()` (<= tightPinchOnRatio is a pinch). */
  pinch: number[];
  /** Wrist-to-wrist distance in frame widths with two hands, else null. */
  wristDistance: number | null;
  /** The engagement gate's state at this frame (`measureNow`); false from the static `measure`). */
  armed: boolean;
};

/** How long two hands may drop to one (MediaPipe merging two touching hands) without the
 * two-hand state resetting. */
export const TWO_HANDS_GRACE_MS = 700;

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
  /**
   * The rotate's "C" pose: thumb-index distance / hand size BETWEEN these two, with the
   * other fingers at least half open (`rotateMinOpenness`). Measured on the owner's own
   * camera (2026-09-21): C pose 0.66 / openness 0.66; a pinch 0.40 / 0.42; a fist 0.44 / 0.29
   * - so the ratio alone cannot tell a rotating hand from a fist; the openness can.
   */
  looseIndexPinchMinRatio: number;
  looseIndexPinchMaxRatio: number;
  rotateMinOpenness: number;
  /** A TIGHT pinch needs the other fingers more open than a fist (0.42 vs 0.29 measured).
   * Deliberately ABOVE `fistMaxOpenness` (0.35 > 0.34): the one knob that keeps mouse mode
   * (Stage 2, rides this same threshold) and drag (rides `fistMaxOpenness`) from ever both
   * reading true on the same hand at once. */
  pinchMinOpenness: number;
  /** A fist (stage 2: the left button held) is a hand this closed. */
  fistMaxOpenness: number;
  /**
   * The engagement gate (ADR-0199): nothing is a gesture until the owner ARMS the
   * recogniser - one open hand, fingers up, held still (`armStillFrac` of the frame per
   * sample) for `armHoldMs`. Armed lasts `armedForMs`, extended by every gesture; a hand
   * at the chin, a cigarette or a cup does nothing while disarmed. `false` only in tests.
   */
  engagementGate: boolean;
  armHoldMs: number;
  armStillFrac: number;
  armedForMs: number;
  /** Total accumulated rotation (degrees) needed for rotate_cw/rotate_ccw. */
  rotateMinDegrees: number;
  /** ...within this many ms of the loose-pinch anchor. */
  rotateMaxMs: number;
  /** spread = two OPEN hands held at least this far apart (wrist to wrist, frame widths)... */
  spreadWideFrac: number;
  /** ...for at least this long... */
  spreadHoldMs: number;
  /** ...and it re-arms only once they come back closer than this (or a hand is lost). */
  spreadRearmFrac: number;
  /** gather = two OPEN hands brought together, wrists closer than this... */
  gatherCloseFrac: number;
  /** ...held for at least this long, after having been apart (>= spreadRearmFrac). */
  gatherHoldMs: number;
  /** The merge rule: two hands last seen closer than this that become ONE hand for
   * `gatherHoldMs` are a gather (MediaPipe merges touching hands). */
  gatherMergeFrac: number;
  /** thumb-index ratio at/under which a TIGHT pinch begins (`pinch_start`); also the
   * "ring" pose Stage 2's mouse mode arms on (see the module docstring). */
  tightPinchOnRatio: number;
  /** thumb-index ratio at/over which a TIGHT pinch ends (`pinch_release`); hysteresis band
   * above `tightPinchOnRatio` so a ratio hovering near one threshold cannot flicker. Also
   * mouse mode's own `mouse_end` threshold — the ring opening all the way. */
  tightPinchOffRatio: number;
  /** Shared cooldown after any swipe/rotate/spread emission — the task brief's "one gesture
   * at a time" rule. Pinch start/release, and Stage 2's mouse/drag events, are edge-triggered
   * hysteresis, not debounced discrete actions, and are deliberately NOT subject to this
   * cooldown: Stage 2's pinch-mouse needs the pinch state itself, not a throttled pulse of
   * it, and the click family's own timing is tighter than this window. */
  cooldownMs: number;
  /**
   * After a swipe or a rotate, the OPPOSITE gesture within this window is the hand
   * coming back to where it started, not a new command (second live trial, 2026-09-21:
   * "sağ döndürüyorum, elimi eski pozisyona getirirken sol algılıyor"). Swallowed, and it
   * does not restart the cooldown. The SAME direction again is a real repeat.
   */
  returnSuppressMs: number;
  /** How far back frame history is kept, for all windows above; must exceed every *MaxMs. */
  historyMs: number;

  // ------------------------------------------------------------- ADR-0199, Stage 2

  /** The "ring" pinch (mouse mode's arming pose, `tightPinchOnRatio`/`pinchMinOpenness`)
   * held continuously this long → `mouse_start`. Owner's brief: "after 150 ms mouse_start". */
  mouseStartHoldMs: number;
  /** `mouse_move`'s dead zone, in the same FRAME units as `swipeMinDistanceFrac` (frame
   * width/height = 1.0) — a palm displacement smaller than this between sends is jitter,
   * not an intended move, and is folded into the NEXT frame's delta rather than dropped
   * (a slow drift still eventually crosses the zone and moves the cursor). Owner's brief:
   * "dead zone 0.005". */
  mouseMoveDeadZone: number;
  /** The click's nested, deeper pinch: `pinchRatio() <= tightPinchOnRatio * clickPinchRatioFrac`
   * ("tips pressed", owner's brief: "≤ 0.25 of the ring's own ratio"). A fraction of
   * `tightPinchOnRatio` rather than a fixed ratio so it retunes itself if the ring
   * threshold ever does. */
  clickPinchRatioFrac: number;
  /** The click released within this long after pressing → `left_click`. */
  leftClickMaxMs: number;
  /** The click held at least this long before releasing → `right_click`. A release between
   * `leftClickMaxMs` and `rightClickMinMs` is ambiguous on purpose and fires neither. */
  rightClickMinMs: number;
  /** A FIST (`openness() <= fistMaxOpenness`) held continuously this long → `drag_start`.
   * Owner's brief: "held 150 ms → drag_start" (same figure as `mouseStartHoldMs` — one
   * "how long is a deliberate hold" knob for both poses, not two to retune separately). */
  dragStartHoldMs: number;
  /** `drag_move`'s dead zone — see `mouseMoveDeadZone`; the same figure by default (no
   * reason in the brief to tune the drag differently from the mouse). */
  dragMoveDeadZone: number;
};

/**
 * Retuned on the owner's first live trial (2026-09-21, Logi C615 at ~45 fps): the first
 * values (swipe >= 1/4 of the frame within 600 ms, a fully open hand, 60 deg of rotation,
 * a 700 ms cooldown) recognised, but only with an exaggerated, tiring gesture - "işlemleri
 * yapana kadar canım çıkıyor". A swipe is now a hand crossing ~1/6 of the frame within
 * 900 ms with most fingers out; a rotate is 40 deg; the cooldown is 450 ms.
 */
export const DEFAULT_RECOGNIZER_OPTIONS: RecognizerOptions = {
  // Owner, 2026-09-21 evening: "ilk haline getir" - the values of the first working trial
  // (commit 6dcbb73). The camera-calibrated set (pinch 0.45 with an openness floor, fist
  // 0.34, C pose 0.5-0.85, open 0.75) read "tam tersi" to the owner and is kept ONLY as the
  // recorded measurements below, not as defaults: palm 0.84, C pose 0.66/0.66, pinch
  // 0.40/0.42, fist 0.44/0.29 (pinch ratio / openness). Retune from these one knob at a
  // time, with the owner watching the HUD, never all at once again.
  openHandMinRatio: 0.45,
  swipeMinDistanceFrac: 0.16,
  swipeMaxMs: 900,
  looseIndexPinchMinRatio: 0,
  looseIndexPinchMaxRatio: 0.8,
  rotateMinOpenness: 0,
  // ...and the pinch's openness floor is the same knob: on this camera a fist measures
  // 0.44 / 0.29, inside the ratio band - only the openness tells the two apart.
  pinchMinOpenness: 0.35,
  fistMaxOpenness: 0.34,
  engagementGate: true,
  armHoldMs: 400,
  armStillFrac: 0.02,
  armedForMs: 4_000,
  rotateMinDegrees: 40,
  rotateMaxMs: 1_000,
  spreadWideFrac: 0.4,
  spreadHoldMs: 250,
  spreadRearmFrac: 0.3,
  gatherCloseFrac: 0.28,
  gatherHoldMs: 300,
  gatherMergeFrac: 0.4,
  // The ONE knob changed after the restore, with the owner watching: the touching-tips
  // pinch measures 0.40 on their camera (the landmarks sit at the nails), 0.20 never fired.
  tightPinchOnRatio: 0.45,
  tightPinchOffRatio: 0.55,
  cooldownMs: 450,
  returnSuppressMs: 1_500,
  historyMs: 1_500,
  // ADR-0199, Stage 2 — see RecognizerOptions' own comments for the reasoning per knob.
  mouseStartHoldMs: 150,
  mouseMoveDeadZone: 0.005,
  clickPinchRatioFrac: 0.25,
  leftClickMaxMs: 350,
  rightClickMinMs: 600,
  dragStartHoldMs: 150,
  dragMoveDeadZone: 0.005,
};

/** The gesture a hand makes on its way BACK from `name`, or null for the rest. */
export function oppositeOf(name: GestureName): GestureName | null {
  switch (name) {
    case "swipe_left":
      return "swipe_right";
    case "swipe_right":
      return "swipe_left";
    case "swipe_up":
      return "swipe_down";
    case "swipe_down":
      return "swipe_up";
    case "rotate_cw":
      return "rotate_ccw";
    case "rotate_ccw":
      return "rotate_cw";
    case "spread":
      return "gather";
    case "gather":
      return "spread";
    default:
      return null;
  }
}

// ------------------------------------------------------------------- track

type Sample = { t_ms: number; m: Point[]; palm: Point; open: boolean; loosePinch: boolean; angle: number };

type PinchState = "idle" | "pinched";
type ClickState = "idle" | "pressed";

type HandTrack = {
  buffer: Sample[];
  /** `t_ms` since this hand became continuously open, or `null`; reset on any non-open sample. */
  openSince: number | null;
  /** `t_ms` since this hand became continuously loose-pinched, or `null`. */
  looseSince: number | null;
  /** Cumulative signed rotation (degrees) accumulated since `looseSince`. */
  rotationDeg: number;
  pinchState: PinchState;
  /** `t_ms` since this hand has been open, upright and still - the arming pose - or null. */
  stillSince: number | null;

  // ------------------------------------------------------------- ADR-0199, Stage 2
  /** `t_ms` since `pinchState` last became (and has stayed) `"pinched"`, or `null` — the
   * mouse mode hold-timer's anchor. */
  mouseRingSince: number | null;
  /** `true` once `mouse_start` has fired for this hand's current ring pinch. */
  mouseActive: boolean;
  /** The mirrored palm position `mouse_move`'s next delta is measured from. */
  mouseLastPalm: Point | null;
  /** The click's own nested hysteresis, live only while `mouseActive`. */
  clickState: ClickState;
  clickStartT: number | null;
  /** `t_ms` since this hand became continuously a fist (`openness() <= fistMaxOpenness`),
   * or `null` — the drag hold-timer's anchor. */
  fistSince: number | null;
  /** `true` once `drag_start` has fired for this hand's current fist. */
  dragActive: boolean;
  /** The mirrored palm position `drag_move`'s next delta is measured from. */
  dragLastPalm: Point | null;
};

function emptyTrack(): HandTrack {
  return {
    buffer: [],
    openSince: null,
    looseSince: null,
    rotationDeg: 0,
    pinchState: "idle",
    stillSince: null,
    mouseRingSince: null,
    mouseActive: false,
    mouseLastPalm: null,
    clickState: "idle",
    clickStartT: null,
    fistSince: null,
    dragActive: false,
    dragLastPalm: null,
  };
}

// -------------------------------------------------------------- recognizer

export class GestureRecognizer {
  private readonly opts: RecognizerOptions;
  private readonly tracks = new Map<HandednessLabel, HandTrack>();
  /** Since when two OPEN hands have been held wide apart; null once they came back. */
  private wideSince: number | null = null;
  /** The last spread fired and the hands have not come back since: no second spread. */
  private spreadArmed = true;
  /** Since when two OPEN hands have been held close together; null once they part. */
  private closeSince: number | null = null;
  /** A gather needs the hands to have been APART first (>= spreadRearmFrac). */
  private gatherArmed = false;
  private lastEmit: { name: GestureName; t_ms: number } | null = null;
  /** The engagement gate: `t_ms` until which gestures count, or null while disarmed. */
  private armedUntil: number | null = null;
  /** The last frame that showed two hands (a brief one-hand frame while two hands overlap
   * must not reset the two-hand state - MediaPipe merges touching hands). */
  private twoHandsLastSeen: number | null = null;
  /** The last wrist distance measured with two hands in view (for the merge rule). */
  private lastTwoHandDistance: number | null = null;

  constructor(options: Partial<RecognizerOptions> = {}) {
    this.opts = { ...DEFAULT_RECOGNIZER_OPTIONS, ...options };
  }

  /** Feed one tracker tick; returns the gesture events (usually 0 or 1 non-pinch event, plus
   * at most one pinch_start/pinch_release per hand, plus Stage 2's pointer events) this tick
   * produced. */
  ingest(frame: TrackedFrame): GestureEvent[] {
    const events: GestureEvent[] = [];
    const seen = new Set<HandednessLabel>();

    for (const hand of frame.hands) {
      seen.add(hand.handedness);
      const track = this.tracks.get(hand.handedness) ?? emptyTrack();
      this.tracks.set(hand.handedness, track);
      this.updateTrack(track, hand.landmarks, frame.t_ms);

      // The arming pose held long enough arms (or re-arms) the gate.
      if (track.stillSince !== null && frame.t_ms - track.stillSince >= this.opts.armHoldMs) {
        this.armedUntil = frame.t_ms + this.opts.armedForMs;
      }
    }
    if (this.armedUntil !== null && frame.t_ms > this.armedUntil) this.armedUntil = null;
    if (frame.hands.length === 0) this.armedUntil = null;

    for (const hand of frame.hands) {
      const track = this.tracks.get(hand.handedness);
      if (!track) continue;
      // Pinch hysteresis: independent of the cooldown, one edge per tick at most. A
      // pinch STARTS only while armed; a started pinch always RELEASES (stage 2 holds a
      // mouse button on it - a release must never be gated away).
      const mirrored = mirror(hand.landmarks);
      const ratio = pinchRatio(mirrored);
      const pinchOpen = openness(mirrored) >= this.opts.pinchMinOpenness;
      if (track.pinchState === "idle" && ratio <= this.opts.tightPinchOnRatio && pinchOpen && this.isArmed(frame.t_ms)) {
        track.pinchState = "pinched";
        this.extendArmed(frame.t_ms);
        events.push({ name: "pinch_start", t_ms: frame.t_ms });
      } else if (track.pinchState === "pinched" && ratio >= this.opts.tightPinchOffRatio) {
        track.pinchState = "idle";
        events.push({ name: "pinch_release", t_ms: frame.t_ms });
      }
    }
    // A hand that left the frame stops accumulating (its track simply is not updated again);
    // Stage 1 does not need to synthesize a pinch_release for a hand that vanished mid-pinch.

    // ADR-0199, Stage 2: the pinch-mouse and the fist-drag, per hand, independent of the
    // one-hand/two-hand branch below (like the pinch hysteresis above) - see the module
    // docstring's "Stage 2" section for the full state machine.
    for (const hand of frame.hands) {
      const track = this.tracks.get(hand.handedness);
      if (!track) continue;
      this.updatePointerState(track, frame.t_ms, events);
    }

    // "One gesture at a time": at most one non-pinch (swipe/rotate/spread) event per tick,
    // spread checked first (it needs both hands and is the most specific precondition).
    // A swipe and a rotate are ONE-hand gestures: with two hands in view the only thing
    // looked for is the spread, and the per-hand motion histories restart at the current
    // sample - two hands parting or meeting are not two swipes (found by the spread tests
    // once the pose itself became the gesture).
    let nonPinch: GestureEvent | null;
    if (frame.hands.length >= 2) {
      nonPinch = this.detectSpread(frame);
      for (const track of this.tracks.values()) {
        const last = track.buffer.at(-1);
        track.buffer = last ? [last] : [];
      }
    } else {
      nonPinch = this.detectPerHand(frame);
    }
    if (nonPinch) events.push(nonPinch);

    if (frame.hands.length >= 2) this.twoHandsLastSeen = frame.t_ms;
    else if (
      frame.hands.length === 1 &&
      this.twoHandsLastSeen !== null &&
      this.gatherArmed &&
      this.lastTwoHandDistance !== null &&
      this.lastTwoHandDistance <= this.opts.gatherMergeFrac &&
      frame.t_ms - this.twoHandsLastSeen >= this.opts.gatherHoldMs
    ) {
      // The merge rule (owner, third trial: "tam ekran küçültme algılanmıyor"): two hands
      // brought together in front of the face become ONE hand to MediaPipe. Two hands that
      // were close a moment ago and are now one hand, held, IS the gather.
      this.gatherArmed = false;
      this.closeSince = null;
      this.twoHandsLastSeen = null;
      this.lastTwoHandDistance = null;
      const merged = this.emit("gather", frame.t_ms);
      if (merged) events.push(merged);
    } else if (this.twoHandsLastSeen === null || frame.t_ms - this.twoHandsLastSeen > TWO_HANDS_GRACE_MS) {
      // Gone for real (not a one-frame merge of two touching hands): reset the two-hand state.
      this.wideSince = null;
      this.closeSince = null;
      this.spreadArmed = true;
      this.gatherArmed = false;
      this.twoHandsLastSeen = null;
      this.lastTwoHandDistance = null;
    }
    return events;
  }

  /**
   * The one gate every swipe/rotate/spread passes on its way out: the shared cooldown,
   * and the return-suppression window (`returnSuppressMs`) for the OPPOSITE of the last
   * gesture. Returns the event to emit, or null when it is swallowed.
   */
  /** Whether gestures count right now (the engagement gate; always true when the gate is off). */
  isArmed(now: number): boolean {
    if (!this.opts.engagementGate) return true;
    return this.armedUntil !== null && now <= this.armedUntil;
  }

  private extendArmed(now: number): void {
    if (this.opts.engagementGate) this.armedUntil = now + this.opts.armedForMs;
  }

  private emit(name: GestureName, now: number): GestureEvent | null {
    if (!this.isArmed(now)) return null;
    const last = this.lastEmit;
    if (last !== null) {
      if (now - last.t_ms < this.opts.cooldownMs) return null;
      if (oppositeOf(last.name) === name && now - last.t_ms < this.opts.returnSuppressMs) return null;
    }
    this.lastEmit = { name, t_ms: now };
    this.extendArmed(now);
    return { name, t_ms: now };
  }

  /** Like `emit()`, but for Stage 2's pointer events: armed-gated only, never
   * cooldown/return-suppressed (module docstring). Still extends the engagement gate on
   * every call - "mouse/drag mode keeps the gate armed while active" (task brief) - so a
   * long drag or a slow mouse move never has to fight the gate's own timeout. */
  private emitPointer(name: GestureName, now: number, dx?: number, dy?: number): GestureEvent | null {
    if (!this.isArmed(now)) return null;
    this.extendArmed(now);
    return dx === undefined || dy === undefined ? { name, t_ms: now } : { name, t_ms: now, dx, dy };
  }

  private updateTrack(track: HandTrack, raw: readonly Point[], t_ms: number): void {
    const m = mirror(raw);
    const open = openness(m);
    const isOpen = open >= this.opts.openHandMinRatio;
    const ratio = pinchRatio(m);
    const loose =
      ratio >= this.opts.looseIndexPinchMinRatio &&
      ratio <= this.opts.looseIndexPinchMaxRatio &&
      open >= this.opts.rotateMinOpenness;
    const angle = thumbIndexAngle(m);

    if (isOpen) track.openSince ??= t_ms;
    else track.openSince = null;

    // The arming pose: open, fingers UP (the index and middle tips above the wrist by
    // half a hand), and still against the previous sample.
    const prevSample = track.buffer.at(-1);
    const palm = palmCenter(m);
    const upright = m[WRIST].y - Math.max(m[INDEX_TIP].y, m[MIDDLE_TIP].y) >= handSize(m) * 0.5;
    const still = prevSample !== undefined && dist(palm, prevSample.palm) <= this.opts.armStillFrac;
    if (isOpen && upright && still) track.stillSince ??= prevSample?.t_ms ?? t_ms;
    else track.stillSince = null;

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

    track.buffer.push({ t_ms, m, palm, open: isOpen, loosePinch: loose, angle });
    const cutoff = t_ms - this.opts.historyMs;
    while (track.buffer.length > 1 && track.buffer[0].t_ms < cutoff) track.buffer.shift();
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
    const current = track.buffer.at(-1);
    if (!current) return null;
    // The hand must be OPEN where the swipe starts (a fist or a pinch moving across the
    // frame is not a swipe); what it looks like at the end is not held against it - a
    // swiping hand tilts and its 2D openness drops mid-motion (second live trial).
    const anchorTime = now - this.opts.swipeMaxMs;
    const anchor = track.buffer.find((s) => s.t_ms >= anchorTime && s.open);
    if (!anchor || anchor.t_ms >= now) return null;
    const dx = current.palm.x - anchor.palm.x;
    const dy = current.palm.y - anchor.palm.y;
    const horizontal = Math.abs(dx) >= Math.abs(dy);
    const magnitude = horizontal ? Math.abs(dx) : Math.abs(dy);
    if (magnitude < this.opts.swipeMinDistanceFrac) return null;
    // Crossed: this motion is spent whether or not it is emitted - the buffer restarts at
    // the current sample so the same displacement cannot re-fire when the cooldown lapses.
    track.buffer = [current];
    track.openSince = current.open ? now : null;
    const name: GestureName = horizontal ? (dx > 0 ? "swipe_right" : "swipe_left") : dy > 0 ? "swipe_down" : "swipe_up";
    return this.emit(name, now);
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
    return this.emit(degrees > 0 ? "rotate_cw" : "rotate_ccw", now);
  }

  private detectSpread(frame: TrackedFrame): GestureEvent | null {
    if (frame.hands.length < 2) return null;
    const [a, b] = frame.hands;
    const ma = mirror(a.landmarks);
    const mb = mirror(b.landmarks);
    const now = frame.t_ms;
    const distance = dist(ma[WRIST], mb[WRIST]);
    this.lastTwoHandDistance = distance;
    const bothOpen = openness(ma) >= this.opts.openHandMinRatio && openness(mb) >= this.opts.openHandMinRatio;
    if (distance < this.opts.spreadRearmFrac) this.spreadArmed = true;
    if (distance >= this.opts.spreadRearmFrac) this.gatherArmed = true;
    if (!bothOpen) {
      this.wideSince = null;
      this.closeSince = null;
      return null;
    }
    // spread: held wide.
    if (distance >= this.opts.spreadWideFrac) {
      this.closeSince = null;
      this.wideSince ??= now;
      if (this.spreadArmed && now - this.wideSince >= this.opts.spreadHoldMs) {
        this.spreadArmed = false;
        this.wideSince = null;
        return this.emit("spread", now);
      }
      return null;
    }
    this.wideSince = null;
    // gather: held together, after having been apart (owner, second trial: "iki elle
    // kapatma da ekle, tam ekranı küçültecek").
    if (distance <= this.opts.gatherCloseFrac) {
      this.closeSince ??= now;
      if (this.gatherArmed && now - this.closeSince >= this.opts.gatherHoldMs) {
        this.gatherArmed = false;
        this.closeSince = null;
        return this.emit("gather", now);
      }
      return null;
    }
    this.closeSince = null;
    return null;
  }

  /**
   * ADR-0199, Stage 2: the pinch-mouse and the fist-drag for ONE hand, for the current
   * frame. See the module docstring's "Stage 2" section for the full state machine; this
   * method is the code for it. Click resolution runs BEFORE the ring-open check so a fast
   * full release still resolves as a click on the same frame it ends mouse mode.
   */
  private updatePointerState(track: HandTrack, now: number, events: GestureEvent[]): void {
    const current = track.buffer.at(-1);
    if (!current) return;

    // ---- the ring pinch -> mouse mode (rides pinch_start/pinch_release's own hysteresis).
    if (track.pinchState === "pinched") {
      track.mouseRingSince ??= now;
    } else {
      track.mouseRingSince = null;
    }

    if (track.mouseActive) {
      // The nested, deeper pinch: a click.
      const ratio = pinchRatio(current.m);
      const pressed = ratio <= this.opts.tightPinchOnRatio * this.opts.clickPinchRatioFrac;
      if (track.clickState === "idle" && pressed) {
        track.clickState = "pressed";
        track.clickStartT = now;
      } else if (track.clickState === "pressed" && !pressed) {
        const heldMs = now - (track.clickStartT ?? now);
        track.clickState = "idle";
        track.clickStartT = null;
        if (heldMs < this.opts.leftClickMaxMs) {
          const e = this.emitPointer("left_click", now);
          if (e) events.push(e);
        } else if (heldMs >= this.opts.rightClickMinMs) {
          const e = this.emitPointer("right_click", now);
          if (e) events.push(e);
        }
        // Between the two windows: ambiguous, deliberately neither (see the module docstring).
      }

      if (track.mouseLastPalm) {
        const dx = current.palm.x - track.mouseLastPalm.x;
        const dy = current.palm.y - track.mouseLastPalm.y;
        if (Math.hypot(dx, dy) >= this.opts.mouseMoveDeadZone) {
          const e = this.emitPointer("mouse_move", now, dx, dy);
          if (e) {
            events.push(e);
            track.mouseLastPalm = current.palm;
          }
        }
      }

      if (track.mouseRingSince === null) {
        // The ring opened all the way: mouse mode ends.
        track.mouseActive = false;
        track.mouseLastPalm = null;
        track.clickState = "idle";
        track.clickStartT = null;
        const e = this.emitPointer("mouse_end", now);
        if (e) events.push(e);
      }
    } else if (track.mouseRingSince !== null && now - track.mouseRingSince >= this.opts.mouseStartHoldMs) {
      const e = this.emitPointer("mouse_start", now);
      if (e) {
        events.push(e);
        track.mouseActive = true;
        track.mouseLastPalm = current.palm;
      }
    }

    // ---- the fist -> drag.
    const openNow = openness(current.m);
    const isFist = openNow <= this.opts.fistMaxOpenness;
    if (isFist) track.fistSince ??= now;
    else track.fistSince = null;

    if (track.dragActive) {
      if (track.dragLastPalm) {
        const dx = current.palm.x - track.dragLastPalm.x;
        const dy = current.palm.y - track.dragLastPalm.y;
        if (Math.hypot(dx, dy) >= this.opts.dragMoveDeadZone) {
          const e = this.emitPointer("drag_move", now, dx, dy);
          if (e) {
            events.push(e);
            track.dragLastPalm = current.palm;
          }
        }
      }
      if (track.fistSince === null) {
        track.dragActive = false;
        track.dragLastPalm = null;
        const e = this.emitPointer("drag_end", now);
        if (e) events.push(e);
      }
    } else if (track.fistSince !== null && now - track.fistSince >= this.opts.dragStartHoldMs) {
      const e = this.emitPointer("drag_start", now);
      if (e) {
        events.push(e);
        track.dragActive = true;
        track.dragLastPalm = current.palm;
      }
    }
  }

  /** `measure()` plus this recogniser's own gate state, for the HUD. */
  measureNow(frame: TrackedFrame): FrameMeasure {
    return { ...GestureRecognizer.measure(frame), armed: this.isArmed(frame.t_ms) };
  }

  /** Live measurements for the calibration readout - what THIS frame looks like to the
   * rules above, in the same mirrored units the thresholds use. Pure; emits nothing. */
  static measure(frame: TrackedFrame): FrameMeasure {
    const hands = frame.hands.map((h) => mirror(h.landmarks));
    return {
      hands: hands.length,
      openness: hands.map((m) => Math.round(openness(m) * 100) / 100),
      pinch: hands.map((m) => Math.round(pinchRatio(m) * 100) / 100),
      wristDistance: hands.length >= 2 ? Math.round(dist(hands[0][WRIST], hands[1][WRIST]) * 100) / 100 : null,
      armed: false,
    };
  }

}
