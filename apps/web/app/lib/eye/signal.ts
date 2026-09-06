/**
 * Deriving the seven observation fields honestly, from numbers alone.
 *
 * Everything in this file is a pure function over plain numeric arrays — no
 * `HTMLVideoElement`, no `CanvasRenderingContext2D`, no `MediaStream`. That is
 * deliberate twice over: it is what lets every one of these functions be
 * exercised with synthetic pixel arrays in a plain Node test (no browser, per
 * the M18 task brief), and it is half of the frame-never-escapes guarantee —
 * `perception.ts` is the only place a real camera frame exists, and the only
 * things it ever hands to this module are a small typed array of luminance
 * samples (already reduced from a frame, never the frame) and a handful of
 * counters. Nothing here could reconstruct an image even if it wanted to.
 *
 * ## What the first version got wrong (2026-09-06, the owner's real run)
 *
 * Presence was the whole-grid MEAN frame difference, gated at a 2% noise floor.
 * A seated owner — typing, turning their head — changes a few of the 108 cells
 * strongly while the mean over all of them stays under 1%. So the production
 * engine held `away` at confidence 0.75 for eleven minutes on 1,102 observations
 * of an owner sitting directly in front of the camera. A motion sensor whose
 * unit is the whole frame cannot see a person; its unit has to be the cell.
 *
 * Three honest signals, all still computable without any face/body model:
 *
 * 1. **Localised motion** — the FRACTION OF CELLS whose luminance changed by
 *    more than `CELL_CHANGE_THRESHOLD` since the previous sample. A hand or a
 *    head moving changes a handful of cells a lot; sensor noise averaged over
 *    a cell's thousands of pixels changes none.
 * 2. **Motion memory** — a person who is in the room moves within a minute or
 *    two, always: `person_present` is "meaningful movement within
 *    `PRESENCE_MEMORY_MS`", not "moving right now".
 * 3. **Exit versus stillness** — leaving a room is a LARGE burst of change
 *    followed by nothing; sitting still after a small movement is not. The
 *    magnitude of the last movement before the stillness began is kept and
 *    decides which way a long silence leans, at a confidence that says how
 *    weak that evidence is.
 *
 * Where the evidence does not support a claim (posture, always; awake/uncertain,
 * often) the function returns the schema's `"unknown"`/`"uncertain"` value
 * instead of guessing, and `presence_confidence` is a stated function of
 * measured quantities — never a fabricated number.
 */

import type { ActivityLevel, AwakeState, EyeObservation, Posture } from "./types";

// --------------------------------------------------------------- luminance

/**
 * A tiny grid of average luminance, not a picture.
 *
 * `gridCols` × `gridRows` (12×9 = 108 numbers by default) is coarse on
 * purpose: it is far too low-resolution to show a face or a recognisable
 * shape, but it is enough to notice that *something in the room changed*,
 * which is all frame-difference motion detection needs. This is the only
 * function in the module that touches raw pixels; everything downstream of
 * it works on this small array of floats.
 */
export const GRID_COLS = 12;
export const GRID_ROWS = 9;

export function computeGridLuminance(
  pixels: ArrayLike<number>,
  width: number,
  height: number,
  gridCols: number = GRID_COLS,
  gridRows: number = GRID_ROWS,
): Float32Array {
  const grid = new Float32Array(gridCols * gridRows);
  const counts = new Float32Array(gridCols * gridRows);
  if (width <= 0 || height <= 0) return grid;
  const cellW = width / gridCols;
  const cellH = height / gridRows;
  for (let y = 0; y < height; y++) {
    const gy = Math.min(gridRows - 1, Math.floor(y / cellH));
    const rowBase = y * width;
    for (let x = 0; x < width; x++) {
      const gx = Math.min(gridCols - 1, Math.floor(x / cellW));
      const idx = (rowBase + x) * 4;
      const r = pixels[idx] ?? 0;
      const g = pixels[idx + 1] ?? 0;
      const b = pixels[idx + 2] ?? 0;
      // ITU-R BT.709 luma weights, normalised to 0..1.
      const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
      const cell = gy * gridCols + gx;
      grid[cell] += luminance;
      counts[cell] += 1;
    }
  }
  for (let i = 0; i < grid.length; i++) {
    grid[i] = counts[i] > 0 ? grid[i] / counts[i] : 0;
  }
  return grid;
}

export function meanLuminance(grid: Float32Array): number {
  if (grid.length === 0) return 0;
  let sum = 0;
  for (const v of grid) sum += v;
  return sum / grid.length;
}

function clamp01(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

// ------------------------------------------------------------------ motion

/**
 * A cell counts as CHANGED when its average luminance moved by more than this
 * (0..1 scale; 0.05 ≈ 13/255). Each cell averages hundreds to thousands of
 * pixels, which averages sensor noise away, so a change of this size in a cell
 * is a real change in the scene, not noise. Named so a calibration pass has
 * one place to look.
 */
export const CELL_CHANGE_THRESHOLD = 0.05;

export type MotionMeasure = {
  /** Fraction (0..1) of grid cells that changed by more than the threshold. */
  changedCellRatio: number;
  /** The largest single-cell change, 0..1 — how strong the strongest change was. */
  maxCellDelta: number;
  /** The old whole-grid mean absolute difference, kept for the record. */
  meanDelta: number;
};

/**
 * What changed between two luminance grids.
 *
 * `previous === null` (the first frame there is nothing to compare against)
 * reports no motion rather than guessing — there is no evidence of change
 * yet, which is a true statement, not an approximation of one.
 */
export function measureMotion(current: Float32Array, previous: Float32Array | null): MotionMeasure {
  if (!previous || previous.length !== current.length || current.length === 0) {
    return { changedCellRatio: 0, maxCellDelta: 0, meanDelta: 0 };
  }
  let changed = 0;
  let max = 0;
  let sum = 0;
  for (let i = 0; i < current.length; i++) {
    const delta = Math.abs(current[i] - previous[i]);
    sum += delta;
    if (delta > max) max = delta;
    if (delta > CELL_CHANGE_THRESHOLD) changed += 1;
  }
  return {
    changedCellRatio: changed / current.length,
    maxCellDelta: max,
    meanDelta: sum / current.length,
  };
}

/** The old whole-grid measure, kept for callers and tests that want it explicitly. */
export function motionEnergyBetween(current: Float32Array, previous: Float32Array | null): number {
  return measureMotion(current, previous).meanDelta;
}

// ----------------------------------------------------------- activity level

/**
 * Activity buckets on the fraction of cells that changed. Two cells of 108
 * (~2%) is the least a real movement produces — a head turn, a hand on the
 * keyboard; a tenth of the grid is someone shifting or gesturing; a third is
 * someone walking through the frame or getting up. Named constants, one place.
 */
export const NONE_ACTIVITY_MAX = 0.02;
export const LOW_ACTIVITY_MAX = 0.1;
export const MEDIUM_ACTIVITY_MAX = 0.3;

export function activityLevelFor(changedCellRatio: number): ActivityLevel {
  const m = clamp01(changedCellRatio);
  if (m < NONE_ACTIVITY_MAX) return "none";
  if (m < LOW_ACTIVITY_MAX) return "low";
  if (m < MEDIUM_ACTIVITY_MAX) return "medium";
  return "high";
}

// ---------------------------------------------------------------- presence

/**
 * What `derivePresence` is given: a few counters `perception.ts` keeps, never
 * a sample of any frame.
 */
export type PresenceEvidence = {
  /** This tick's activity level, from `activityLevelFor`. */
  currentActivity: ActivityLevel;
  /** Milliseconds since the last meaningful (non-`none`) movement, or `null` if never. */
  msSinceLastMotion: number | null;
  /** The activity level of that last movement — an exit is `high`, a fidget is `low`. */
  lastMotionLevel: ActivityLevel;
  /** How many samples have been collected. */
  sampleCount: number;
  /** Samples needed before the sampler calls its own evidence base "full". */
  samplesForFullConfidence: number;
};

/**
 * A person who is in the room moves within this long, always (breathing is
 * below grid resolution, but typing, head turns and shifting are not). Within
 * it, the last movement is evidence of presence NOW.
 */
export const PRESENCE_MEMORY_MS = 90_000;

/**
 * Past the memory window, how long a small-movement-then-stillness is still
 * read as "present, very still" before the evidence is called exhausted. A
 * still owner at a desk or in bed makes a small movement every few minutes;
 * beyond this, "present" is a guess and is not made.
 */
export const LINGER_MS = 10 * 60_000;

/**
 * A movement at or above this level immediately before the stillness began is
 * read as an EXIT — walking out of frame changes a third of the grid or more —
 * so a long stillness after it leans "absent" rather than "very still".
 */
export const EXIT_ACTIVITY_LEVEL: ActivityLevel = "high";

/**
 * A motion sensor has no positive evidence of an EMPTY room — stillness is
 * also what a present-but-motionless owner looks like. Capping absence
 * confidence records that limitation instead of letting "no motion for a
 * while" round up to certainty the spec forbids fabricating.
 */
export const ABSENCE_CONFIDENCE_CAP = 0.75;

/** The floor of a still-but-present claim inside the linger window: weak, said so. */
export const LINGER_CONFIDENCE_FLOOR = 0.35;

export function derivePresence(evidence: PresenceEvidence): {
  person_present: boolean;
  presence_confidence: number;
} {
  // How much evidence has actually accumulated, as a GATE rather than a term:
  // zero samples must mean zero confidence regardless of how clean a single
  // reading looks (this module's tests caught the additive version of this
  // fabricating ~0.75 confidence with no samples at all).
  const sampleConfidence = clamp01(
    evidence.sampleCount / Math.max(1, evidence.samplesForFullConfidence),
  );
  const age = evidence.msSinceLastMotion;

  if (age === null) {
    // Never seen a movement. Nothing is known about the room; "absent" at low
    // confidence is the honest reading, and it can only rise as stillness lasts.
    return { person_present: false, presence_confidence: Math.min(0.2, sampleConfidence * 0.2) };
  }

  if (age <= PRESENCE_MEMORY_MS) {
    // Somebody moved recently. The more recent and the stronger, the surer.
    const recency = 1 - age / PRESENCE_MEMORY_MS; // 1 now .. 0 at the window's edge
    const strength = evidence.currentActivity === "none" ? 0.6 : 1.0; // moving NOW corroborates
    const confidence = clamp01((0.55 + 0.45 * recency) * strength * sampleConfidence);
    return { person_present: true, presence_confidence: Math.max(confidence, 0.35 * sampleConfidence) };
  }

  const beyond = age - PRESENCE_MEMORY_MS;

  if (evidence.lastMotionLevel !== EXIT_ACTIVITY_LEVEL && age <= PRESENCE_MEMORY_MS + LINGER_MS) {
    // A small movement, then stillness. More likely a very still owner than a
    // vanishing one - nobody leaves a room without a burst of change - but the
    // evidence weakens the whole time and says so.
    const fade = 1 - beyond / LINGER_MS; // 1 at the window's edge .. 0 at linger's end
    const confidence = clamp01(
      (LINGER_CONFIDENCE_FLOOR + 0.25 * fade) * sampleConfidence,
    );
    return { person_present: true, presence_confidence: confidence };
  }

  // Either the last movement was exit-sized, or the stillness has outlasted
  // any plausible "very still" - absent, at a confidence that grows with the
  // length of the silence and never past the cap.
  const grown = clamp01(beyond / PRESENCE_MEMORY_MS); // 0 at the edge .. 1 after another window
  const base = evidence.lastMotionLevel === EXIT_ACTIVITY_LEVEL ? 0.55 : 0.4;
  const confidence = Math.min(
    ABSENCE_CONFIDENCE_CAP,
    clamp01((base + (ABSENCE_CONFIDENCE_CAP - base) * grown) * sampleConfidence),
  );
  return { person_present: false, presence_confidence: confidence };
}

// ------------------------------------------------------------------ posture

/**
 * Always `"unknown"`.
 *
 * Posture needs body-pose estimation, and this module deliberately does none
 * — no face or body detection of any kind (M18 task brief). Motion and
 * luminance say nothing about whether a person is sitting or standing, so the
 * honest publication is the schema's own example: "if you cannot infer
 * something — posture, for instance — publish the schema's unknown value."
 */
export function derivePosture(): Posture {
  return "unknown";
}

// -------------------------------------------------------------- awake state

/** Activity levels strong enough to call "awake" on their own. */
const AWAKE_ACTIVITY_LEVELS: ReadonlySet<ActivityLevel> = new Set(["medium", "high"]);

/**
 * How long a present owner must show no meaningful motion before "resting"
 * is a defensible word rather than a guess about one still moment.
 */
export const RESTING_STILL_MS = 5 * 60 * 1000;

export function deriveAwakeState(
  personPresent: boolean,
  activityLevel: ActivityLevel,
  stillDurationMs: number,
): AwakeState {
  if (!personPresent) return "uncertain"; // no one to be awake or resting about
  if (AWAKE_ACTIVITY_LEVELS.has(activityLevel)) return "awake";
  if (activityLevel === "none" && stillDurationMs >= RESTING_STILL_MS) return "resting";
  return "uncertain"; // low activity, or stillness too brief to mean anything yet
}

// -------------------------------------------------------- full observation

export type DerivationInput = PresenceEvidence & {
  /** Milliseconds the owner has been present with `activity_level: "none"`, or 0. */
  stillDurationMs: number;
};

/**
 * One full, honest observation from the numbers `perception.ts` measured.
 *
 * This is the single function that produces an `EyeObservation`, so there is
 * exactly one place in the whole client where the seven fields are assembled
 * — a caller cannot construct a partial or a differently-derived one.
 */
export function deriveObservation(input: DerivationInput, observedAt: Date): EyeObservation {
  const activity_level = input.currentActivity;
  const { person_present, presence_confidence } = derivePresence(input);
  const awake_state = deriveAwakeState(person_present, activity_level, input.stillDurationMs);
  return {
    person_present,
    presence_confidence,
    activity_level,
    posture: derivePosture(),
    awake_state,
    observed_at: observedAt.toISOString(),
    source: "camera",
  };
}
