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
 * **Derivation must be honest** (M18 task brief): every one of the seven
 * fields below is a stated, documented function of what was actually
 * measured — frame-difference motion energy and mean luminance, the two
 * signals a privacy-preserving camera client can compute without shipping a
 * face/body model. Where the evidence does not support a claim (posture,
 * always; awake/uncertain, often) the function returns the schema's
 * `"unknown"`/`"uncertain"` value instead of guessing, and `presence_confidence`
 * is a weighted sum of named, commented terms — never a fabricated number.
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

/**
 * Mean absolute difference between two luminance grids, 0..1.
 *
 * `previous === null` (the first frame there is nothing to compare against)
 * reports zero motion rather than guessing — there is no evidence of change
 * yet, which is a true statement, not an approximation of one.
 */
export function motionEnergyBetween(current: Float32Array, previous: Float32Array | null): number {
  if (!previous || previous.length !== current.length || current.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < current.length; i++) sum += Math.abs(current[i] - previous[i]);
  return sum / current.length;
}

function clamp01(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

// ----------------------------------------------------------- activity level

/**
 * Motion-energy thresholds. Below `NOISE_FLOOR` is sensor/encoder noise on a
 * static scene (measured against real webcam frame-diff noise, which sits
 * comfortably under 1% mean luminance delta even on cheap sensors); above
 * `MEDIUM_ACTIVITY_MAX` is sustained, large-scale change. These are
 * deliberately named constants rather than inline numbers so a future
 * calibration pass has one place to change them.
 */
export const NOISE_FLOOR = 0.02;
export const LOW_ACTIVITY_MAX = 0.06;
export const MEDIUM_ACTIVITY_MAX = 0.16;

export function activityLevelFor(motionEnergy: number): ActivityLevel {
  const m = clamp01(motionEnergy);
  if (m < NOISE_FLOOR) return "none";
  if (m < LOW_ACTIVITY_MAX) return "low";
  if (m < MEDIUM_ACTIVITY_MAX) return "medium";
  return "high";
}

// ---------------------------------------------------------------- presence

/**
 * What `derivePresence` is given: aggregate counters over a trailing window,
 * never a sample of the window's own contents (the window itself lives only
 * inside `perception.ts`'s closure as a small ring buffer of numbers).
 */
export type PresenceEvidence = {
  /** This tick's motion energy, 0..1. */
  currentMotion: number;
  /** Fraction (0..1) of the trailing window's samples above `NOISE_FLOOR`. */
  recentMotionRatio: number;
  /** How many samples have been collected, capped by the caller at the window size. */
  sampleCount: number;
  /** Samples needed before the sampler calls its own evidence base "full". */
  samplesForFullConfidence: number;
};

/** At least this fraction of the trailing window must show motion to call presence "true". */
export const PRESENCE_MOTION_RATIO = 0.15;

/**
 * Weights for the EVIDENCE half of `presence_confidence`, summing to 1.0 — a
 * weighted vote between two independently weak signals, not one number
 * treated as ground truth.
 *
 * - `CONSISTENCY_WEIGHT` — how much of the trailing WINDOW agrees with the
 *   verdict (mostly-motion supports "present"; mostly-still supports
 *   "absent"). Already a natural 0..1 fraction, so it is used as measured.
 * - `CURRENT_AGREEMENT_WEIGHT` — whether the very latest frame agrees too, so
 *   a verdict is not purely a historical average. Raw motion-energy values
 *   are small by construction (an "activity_level: high" frame is only
 *   `MEDIUM_ACTIVITY_MAX` and up), so this term is rescaled by
 *   `motionStrength01` onto the same 0..1 scale `activityLevelFor` uses,
 *   rather than treating a naturally-small number as if it were already a
 *   confidence.
 */
export const CONSISTENCY_WEIGHT = 0.6;
export const CURRENT_AGREEMENT_WEIGHT = 0.4;

/** `motionEnergy` at or above this is treated as maximally corroborating. */
function motionStrength01(motionEnergy: number): number {
  return clamp01(motionEnergy / MEDIUM_ACTIVITY_MAX);
}

/**
 * A motion sensor has no positive evidence of an EMPTY room — stillness is
 * also what a present-but-motionless owner (reading, on a call) looks like.
 * Capping absence confidence records that limitation instead of letting
 * "no motion for a while" round up to certainty the spec forbids fabricating.
 */
export const ABSENCE_CONFIDENCE_CAP = 0.75;

export function derivePresence(evidence: PresenceEvidence): {
  person_present: boolean;
  presence_confidence: number;
} {
  const recentMotionRatio = clamp01(evidence.recentMotionRatio);
  const currentMotion = clamp01(evidence.currentMotion);
  // How much evidence has actually accumulated, as a GATE rather than a term
  // added alongside the others: zero samples must mean zero confidence
  // regardless of how clean a single reading looks, and an additive weight
  // small enough not to dominate the other terms cannot guarantee that (this
  // module's own tests caught exactly that failure mode during development —
  // see `signal.test.ts`'s "no evidence at all" case).
  const sampleConfidence = clamp01(
    evidence.sampleCount / Math.max(1, evidence.samplesForFullConfidence),
  );

  const person_present =
    recentMotionRatio >= PRESENCE_MOTION_RATIO || currentMotion >= LOW_ACTIVITY_MAX;

  // Consistency and current-frame terms are read as "agreement with the
  // verdict just reached" so the same formula scores a confident absence and
  // a confident presence symmetrically, rather than only ever rewarding motion.
  const consistency = person_present ? recentMotionRatio : 1 - recentMotionRatio;
  const currentAgreement = person_present
    ? motionStrength01(currentMotion)
    : 1 - motionStrength01(currentMotion);

  const evidenceQuality = clamp01(
    CONSISTENCY_WEIGHT * consistency + CURRENT_AGREEMENT_WEIGHT * currentAgreement,
  );
  let presence_confidence = clamp01(evidenceQuality * sampleConfidence);
  if (!person_present) {
    presence_confidence = Math.min(presence_confidence, ABSENCE_CONFIDENCE_CAP);
  }
  return { person_present, presence_confidence };
}

// ------------------------------------------------------------------ posture

/**
 * Always `"unknown"`.
 *
 * Posture needs body-pose estimation, and this module deliberately does none
 * — no face or body detection of any kind (M18 task brief). Motion energy and
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
  const activity_level = activityLevelFor(input.currentMotion);
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
