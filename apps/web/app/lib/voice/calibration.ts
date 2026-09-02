/**
 * Noise-floor calibration, environment modes and the derivation of the local
 * speech-gate parameters (ADR-0044, layer 2).
 *
 * - `NoiseFloorCalibrator` consumes ~1.5–2 s of frame features at microphone
 *   or session start and produces derived numbers only: room floor (dBFS RMS),
 *   a stationary-noise estimate, spread (how intermittent the noise is), a
 *   sensitivity indication, clipping risk and a contamination flag (the owner
 *   was talking during the measurement). No audio is kept.
 * - `deriveGateParameters` turns a calibration + an owner-facing mode
 *   ("Otomatik" / "Sessiz ortam" / "Gürültülü ortam" / "Çok gürültülü ortam")
 *   + the profile's sensitivity preference into `GateParameters`. The table
 *   below is the documented contract the tests pin.
 * - `RunningNoiseFloor` follows the floor between turns and flags a drift
 *   large enough to warrant an automatic recalibration.
 *
 * Pure and deterministic; the caller owns the clock.
 */

import { clamp, type FrameFeatures, percentile, round1 } from "./dsp";

// ------------------------------------------------------------- calibration

export type EnvironmentClass = "quiet" | "normal" | "noisy" | "very_noisy";

/** Owner-facing mode; "auto" derives the class from the calibration. */
export type EnvironmentMode = "auto" | "quiet" | "noisy" | "very_noisy";

export type VadSensitivity = "auto" | "low" | "normal" | "high";

export type SensitivityClass = "low" | "normal" | "high";

export const ENVIRONMENT_CLASS_INDEX: Record<EnvironmentClass, number> = {
  quiet: 0,
  normal: 1,
  noisy: 2,
  very_noisy: 3,
};

export const SENSITIVITY_INDEX: Record<SensitivityClass, number> = { low: 0, normal: 1, high: 2 };

export type CalibrationResult = {
  /** Median RMS level of the measurement window, dBFS. */
  noiseFloorDb: number;
  /** 20th percentile: the stationary part (fan, hum) with intermittent events excluded. */
  stationaryNoiseDb: number;
  /** p90 − p10 of the frame levels: > ~10 dB means intermittent noise is present. */
  spreadDb: number;
  /** Loudest frame of the window, dBFS. */
  peakDb: number;
  /** 0..1 — how close the ambient level already is to clipping. */
  clipRisk: number;
  /** Mean low-band (< 150 Hz) energy share: mains hum / rumble indication. */
  humRatio: number;
  /** Share of frames that looked like speech: > 0.3 means the window was contaminated. */
  speechLikeRatio: number;
  contaminated: boolean;
  /** A hot microphone shows a high floor even in a normal room. */
  sensitivity: SensitivityClass;
  environment: EnvironmentClass;
  frames: number;
  durationMs: number;
};

export type CalibratorOptions = {
  /** Length of the measurement window. */
  durationMs?: number;
  /** Below this the result is not trusted. */
  minFrames?: number;
};

const DEFAULT_CALIBRATION_MS = 1800;
const DEFAULT_MIN_FRAMES = 20;

/** Practical minimum: below this a browser microphone is not producing a meaningful signal. */
export const MIN_FLOOR_DB = -75;

export function classifyEnvironment(noiseFloorDb: number, spreadDb: number): EnvironmentClass {
  let index = 0;
  if (noiseFloorDb >= -38) index = 3;
  else if (noiseFloorDb >= -48) index = 2;
  else if (noiseFloorDb >= -60) index = 1;
  // Intermittent noise (keyboard, people, street) is harder than its median suggests.
  if (spreadDb > 12 && index < 3) index += 1;
  return (Object.keys(ENVIRONMENT_CLASS_INDEX) as EnvironmentClass[])[index];
}

export function classifySensitivity(noiseFloorDb: number): SensitivityClass {
  if (noiseFloorDb >= -40) return "high";
  if (noiseFloorDb >= -55) return "normal";
  return "low";
}

function looksLikeSpeech(f: FrameFeatures): boolean {
  return f.speechBandRatio > 0.5 && f.flatness < 0.35 && f.zcr > 0.003 && f.zcr < 0.25;
}

export class NoiseFloorCalibrator {
  private readonly durationMs: number;
  private readonly minFrames: number;
  private levels: number[] = [];
  private peaks: number[] = [];
  private humSum = 0;
  private speechLike = 0;
  private clipped = 0;
  private elapsed = 0;
  private done = false;
  private cached: CalibrationResult | null = null;

  constructor(options: CalibratorOptions = {}) {
    this.durationMs = options.durationMs ?? DEFAULT_CALIBRATION_MS;
    this.minFrames = options.minFrames ?? DEFAULT_MIN_FRAMES;
  }

  /** Feed one frame; returns true once the window is complete. */
  push(features: FrameFeatures): boolean {
    if (this.done) return true;
    this.levels.push(features.rmsDb);
    this.peaks.push(features.peak);
    this.humSum += features.lowBandRatio;
    if (looksLikeSpeech(features)) this.speechLike += 1;
    if (features.clipRatio > 0) this.clipped += 1;
    this.elapsed += features.durationMs;
    if (this.elapsed >= this.durationMs && this.levels.length >= this.minFrames) {
      this.done = true;
    }
    return this.done;
  }

  get complete(): boolean {
    return this.done;
  }

  get progress(): number {
    return clamp(this.elapsed / this.durationMs, 0, 1);
  }

  reset(): void {
    this.levels = [];
    this.peaks = [];
    this.humSum = 0;
    this.speechLike = 0;
    this.clipped = 0;
    this.elapsed = 0;
    this.done = false;
    this.cached = null;
  }

  /** The derived numbers; null until the window is complete. */
  result(): CalibrationResult | null {
    if (!this.done) return null;
    if (this.cached) return this.cached;
    const n = this.levels.length;
    const noiseFloorDb = round1(Math.max(MIN_FLOOR_DB, percentile(this.levels, 0.5)));
    const stationaryNoiseDb = round1(Math.max(MIN_FLOOR_DB, percentile(this.levels, 0.2)));
    const spreadDb = round1(Math.max(0, percentile(this.levels, 0.9) - percentile(this.levels, 0.1)));
    const peakAmplitude = Math.max(0, ...this.peaks);
    const peakDb = round1(peakAmplitude > 0 ? Math.max(-100, 20 * Math.log10(peakAmplitude)) : -100);
    // Ambient peaks at −20 dBFS or louder mean normal speech will clip.
    const clipRisk = this.clipped > 0 ? 1 : round1(clamp((peakDb + 20) / 20, 0, 1));
    const speechLikeRatio = round1(n > 0 ? this.speechLike / n : 0);
    this.cached = {
      noiseFloorDb,
      stationaryNoiseDb,
      spreadDb,
      peakDb,
      clipRisk,
      humRatio: round1(n > 0 ? this.humSum / n : 0),
      speechLikeRatio,
      contaminated: speechLikeRatio > 0.3,
      sensitivity: classifySensitivity(noiseFloorDb),
      environment: classifyEnvironment(noiseFloorDb, spreadDb),
      frames: n,
      durationMs: Math.round(this.elapsed),
    };
    return this.cached;
  }
}

// ---------------------------------------------------------------- presets

export type GateParameters = {
  /** Calibrated floor the energy margin is measured from, dBFS. */
  floorDb: number;
  /** dB above the floor at which the energy score is 0.5 (gate may open). */
  openMarginDb: number;
  /** dB above the floor below which an open gate is no longer voiced (hysteresis). */
  closeMarginDb: number;
  /** Speech probability needed to start an onset candidate. */
  openProb: number;
  /** Speech probability below which the release hang starts counting. */
  closeProb: number;
  /** An onset must persist this long before the gate opens (an isolated click never does). */
  minOnsetMs: number;
  /** Release hang: the gate stays open this long after the last voiced frame. */
  hangMs: number;
  /** The reported speech start is backdated by this much (no clipped word beginnings). */
  preRollMs: number;
  /** A burst above the floor that is shorter than this counts as a click, never speech. */
  maxClickMs: number;
  /** Extra energy margin while the assistant is audible (echo is a feature, not a mute). */
  echoExtraMarginDb: number;
  /** Uplink attenuation during confident non-speech; 0 = passthrough (default, see uplink.ts). */
  attenuationDb: number;
};

type Preset = Pick<
  GateParameters,
  "openMarginDb" | "openProb" | "minOnsetMs" | "hangMs" | "preRollMs" | "maxClickMs" | "echoExtraMarginDb"
>;

/**
 * The documented mode table (ADR-0044 §3). "Otomatik" picks the row from the
 * calibrated environment class and adds the spread adjustment below; the
 * explicit modes use the row as is.
 */
export const MODE_PRESETS: Record<EnvironmentClass, Preset> = {
  quiet: { openMarginDb: 8, openProb: 0.55, minOnsetMs: 50, hangMs: 500, preRollMs: 150, maxClickMs: 40, echoExtraMarginDb: 4 },
  normal: { openMarginDb: 10, openProb: 0.6, minOnsetMs: 70, hangMs: 450, preRollMs: 120, maxClickMs: 40, echoExtraMarginDb: 6 },
  noisy: { openMarginDb: 14, openProb: 0.65, minOnsetMs: 90, hangMs: 400, preRollMs: 100, maxClickMs: 40, echoExtraMarginDb: 8 },
  very_noisy: { openMarginDb: 18, openProb: 0.7, minOnsetMs: 110, hangMs: 350, preRollMs: 100, maxClickMs: 40, echoExtraMarginDb: 10 },
};

/** Floor assumed before the first calibration completes. */
export const DEFAULT_FLOOR_DB = -60;
/** Word endings: the hang never drops below this whatever the mode says. */
export const MIN_HANG_MS = 300;
/** Turn latency: the onset requirement never exceeds this. */
export const MAX_ONSET_MS = 140;
export const MIN_ONSET_MS = 40;
const HYSTERESIS_DB = 4;
const CLOSE_PROB_DROP = 0.25;

export type DerivedGateParameters = GateParameters & {
  /** Which preset row the parameters came from. */
  basis: EnvironmentClass;
  /** dB added by the spread adjustment ("Otomatik" only). */
  spreadAdjustDb: number;
};

export function modeToClass(mode: EnvironmentMode, calibration: CalibrationResult | null): EnvironmentClass {
  if (mode === "auto") return calibration?.environment ?? "normal";
  return mode;
}

export function deriveGateParameters(input: {
  calibration: CalibrationResult | null;
  mode: EnvironmentMode;
  sensitivity?: VadSensitivity;
  attenuationDb?: number;
}): DerivedGateParameters {
  const basis = modeToClass(input.mode, input.calibration);
  const preset = MODE_PRESETS[basis];
  let openMarginDb = preset.openMarginDb;
  let minOnsetMs = preset.minOnsetMs;
  let spreadAdjustDb = 0;
  if (input.mode === "auto" && input.calibration) {
    // Intermittent noise beyond 6 dB of spread earns up to +3 dB of margin.
    spreadAdjustDb = round1(clamp((input.calibration.spreadDb - 6) * 0.5, 0, 3));
    openMarginDb += spreadAdjustDb;
  }
  switch (input.sensitivity ?? "auto") {
    case "low":
      openMarginDb += 3;
      minOnsetMs += 20;
      break;
    case "high":
      openMarginDb -= 2;
      minOnsetMs -= 20;
      break;
    default:
      break;
  }
  const floorDb = Math.max(MIN_FLOOR_DB, input.calibration?.noiseFloorDb ?? DEFAULT_FLOOR_DB);
  return {
    basis,
    spreadAdjustDb,
    floorDb,
    openMarginDb: round1(openMarginDb),
    closeMarginDb: round1(openMarginDb - HYSTERESIS_DB),
    openProb: preset.openProb,
    closeProb: round1(preset.openProb - CLOSE_PROB_DROP),
    minOnsetMs: clamp(minOnsetMs, MIN_ONSET_MS, MAX_ONSET_MS),
    hangMs: Math.max(MIN_HANG_MS, preset.hangMs),
    preRollMs: preset.preRollMs,
    maxClickMs: preset.maxClickMs,
    echoExtraMarginDb: preset.echoExtraMarginDb,
    attenuationDb: Math.max(0, input.attenuationDb ?? 0),
  };
}

// ---------------------------------------------------------- running floor

export type RunningFloorOptions = {
  calibratedDb: number;
  /** Drift beyond this (either direction) for `sustainMs` triggers recalibration. */
  driftDb?: number;
  sustainMs?: number;
  /** Slow when the floor rises (speech must not drag it up), fast when it falls. */
  riseTauMs?: number;
  fallTauMs?: number;
};

/**
 * Minimum-statistics style tracker fed only with frames the gate considers
 * non-speech. Reports `drifted` once the running floor has stayed more than
 * `driftDb` away from the calibrated one for `sustainMs`.
 */
export class RunningNoiseFloor {
  running: number;
  private calibrated: number;
  private readonly driftDb: number;
  private readonly sustainMs: number;
  private readonly riseTauMs: number;
  private readonly fallTauMs: number;
  private lastAt: number | null = null;
  private driftSince: number | null = null;

  constructor(options: RunningFloorOptions) {
    this.running = options.calibratedDb;
    this.calibrated = options.calibratedDb;
    this.driftDb = options.driftDb ?? 8;
    this.sustainMs = options.sustainMs ?? 5000;
    this.riseTauMs = options.riseTauMs ?? 3000;
    this.fallTauMs = options.fallTauMs ?? 500;
  }

  /** A new calibration became authoritative. */
  reset(calibratedDb: number): void {
    this.calibrated = calibratedDb;
    this.running = calibratedDb;
    this.driftSince = null;
    this.lastAt = null;
  }

  /** Feed a non-speech frame level; returns whether a sustained drift is present. */
  update(rmsDb: number, now: number): { running: number; drifted: boolean; driftDb: number } {
    const dt = this.lastAt === null ? 0 : Math.max(0, now - this.lastAt);
    this.lastAt = now;
    const level = Math.max(MIN_FLOOR_DB, rmsDb);
    const tau = level > this.running ? this.riseTauMs : this.fallTauMs;
    const alpha = dt <= 0 ? 0 : 1 - Math.exp(-dt / tau);
    this.running += (level - this.running) * alpha;
    const drift = this.running - this.calibrated;
    if (Math.abs(drift) > this.driftDb) {
      if (this.driftSince === null) this.driftSince = now;
    } else {
      this.driftSince = null;
    }
    const drifted = this.driftSince !== null && now - this.driftSince >= this.sustainMs;
    return { running: this.running, drifted, driftDb: round1(drift) };
  }
}
