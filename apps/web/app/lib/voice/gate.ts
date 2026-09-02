/**
 * Local speech gate (ADR-0044, layer 3): a lightweight speech-confidence
 * signal applied BEFORE the controller's own turn logic. It decides what the
 * CLIENT treats as an owner-speech start for barge-in / hesitation purposes and
 * what it counts as background; it never mutes the uplink and never replaces
 * the provider's semantic VAD (layer 4, server side, untouched).
 *
 * Per frame the probability combines:
 *   energy   — level relative to the calibrated floor (sigmoid around the margin);
 *   spectral — speech-band energy ratio, 1 − spectral flatness, a ZCR window;
 *   temporal — an onset must persist `minOnsetMs`; a burst shorter than
 *              `maxClickMs` is a click; an open gate hangs `hangMs` after the
 *              last voiced frame and the reported start is backdated `preRollMs`.
 *
 * Energy alone never equals speech: with a zero spectral score the probability
 * caps at ENERGY_ONLY_CAP, below every mode's open threshold. Speaker playback
 * is a FEATURE (a larger energy margin while the assistant is audible), so
 * barge-in keeps working in headset and open-speaker modes.
 *
 * Pure and deterministic; the caller supplies frames and the clock.
 */

import type { GateParameters } from "./calibration";
import { clamp, dbToAmplitude, type FrameFeatures, round1, sigmoid } from "./dsp";
import type { SpeechDetectorStats } from "./ports";

/** What a frame with no spectral evidence can reach at most; below every openProb. */
export const ENERGY_ONLY_CAP = 0.35;
const SPECTRAL_WEIGHT = 1 - ENERGY_ONLY_CAP;
const ENERGY_SLOPE_DB = 3;
/** Any energy rise above the floor by this much releases the (optional) attenuation immediately. */
const ATTENUATION_RELEASE_DB = 3;

export type SpeechScore = {
  prob: number;
  energyScore: number;
  spectralScore: number;
  marginDb: number;
};

function zcrScore(zcr: number): number {
  // Speech sits around 0.02–0.2 crossings per sample; taper outside.
  if (zcr >= 0.015 && zcr <= 0.2) return 1;
  if (zcr < 0.015) return clamp(zcr / 0.015, 0, 1);
  return clamp(1 - (zcr - 0.2) / 0.15, 0, 1);
}

export function speechProbability(
  features: FrameFeatures,
  params: GateParameters,
  playbackActive: boolean,
): SpeechScore {
  const extra = playbackActive ? params.echoExtraMarginDb : 0;
  const marginDb = features.rmsDb - params.floorDb - params.openMarginDb - extra;
  const energyScore = sigmoid(marginDb / ENERGY_SLOPE_DB);
  const spectralScore = clamp(
    0.4 * features.speechBandRatio + 0.4 * (1 - features.flatness) + 0.2 * zcrScore(features.zcr),
    0,
    1,
  );
  const prob = energyScore * (ENERGY_ONLY_CAP + SPECTRAL_WEIGHT * spectralScore);
  return { prob, energyScore, spectralScore, marginDb };
}

export type GateEvent =
  | { type: "open"; at: number }
  | { type: "close"; at: number }
  | { type: "background"; at: number; kind: "click" | "noise"; durationMs: number };

export type GateClassification = "speech" | "background" | "quiet";

export type GateSnapshot = {
  open: boolean;
  prob: number;
  rmsDb: number;
  floorDb: number;
  marginDb: number;
  spectralScore: number;
  clipRatio: number;
  classification: GateClassification;
  playbackActive: boolean;
  uplinkGain: number;
  stats: SpeechDetectorStats;
};

export class SpeechGate {
  private params: GateParameters;
  private open = false;
  private candidateSince: number | null = null;
  private lastVoicedAt = 0;
  private burstSince: number | null = null;
  private last: SpeechScore = { prob: 0, energyScore: 0, spectralScore: 0, marginDb: -100 };
  private lastFeatures: FrameFeatures | null = null;
  private lastPlayback = false;
  private uplinkGain = 1;
  private counters = { gate_opens: 0, gated_out: 0, click_rejects: 0, speech_ms: 0, calibrations: 0 };

  constructor(params: GateParameters) {
    this.params = params;
  }

  setParameters(params: GateParameters): void {
    this.params = params;
  }

  get parameters(): GateParameters {
    return this.params;
  }

  get isOpen(): boolean {
    return this.open;
  }

  /** Linear gain the uplink shaper should apply after the last frame (1 = untouched). */
  get uplinkGainTarget(): number {
    return this.uplinkGain;
  }

  /** A calibration completed (counted here so stats() has one source). */
  noteCalibration(): void {
    this.counters.calibrations += 1;
  }

  /** Feed one frame observed at `now` (ms, frame end). Returns the events it caused. */
  update(features: FrameFeatures, now: number, playbackActive = false): GateEvent[] {
    const events: GateEvent[] = [];
    const p = this.params;
    const score = speechProbability(features, p, playbackActive);
    this.last = score;
    this.lastFeatures = features;
    this.lastPlayback = playbackActive;
    const aboveFloor = features.rmsDb > p.floorDb + p.openMarginDb + (playbackActive ? p.echoExtraMarginDb : 0);

    if (!this.open) {
      if (score.prob >= p.openProb) {
        if (this.candidateSince === null) this.candidateSince = now;
      } else {
        this.candidateSince = null;
      }
      if (aboveFloor) {
        if (this.burstSince === null) this.burstSince = now;
      } else if (this.burstSince !== null) {
        const durationMs = Math.max(features.durationMs, now - this.burstSince);
        this.burstSince = null;
        if (durationMs <= p.maxClickMs) {
          this.counters.click_rejects += 1;
          events.push({ type: "background", at: now, kind: "click", durationMs });
        } else {
          this.counters.gated_out += 1;
          events.push({ type: "background", at: now, kind: "noise", durationMs });
        }
      }
      if (this.candidateSince !== null && now - this.candidateSince >= p.minOnsetMs) {
        this.open = true;
        this.lastVoicedAt = now;
        this.counters.gate_opens += 1;
        this.counters.speech_ms += now - this.candidateSince;
        events.push({ type: "open", at: Math.max(0, this.candidateSince - p.preRollMs) });
        this.candidateSince = null;
        this.burstSince = null;
      }
    } else if (
      score.prob >= p.closeProb ||
      (features.rmsDb > p.floorDb + p.closeMarginDb && score.spectralScore > 0.5)
    ) {
      // Voiced: either the probability holds or the level is still above the
      // close margin with a speech-like spectrum (hysteresis for quiet syllables).
      this.lastVoicedAt = now;
      this.counters.speech_ms += features.durationMs;
    } else if (now - this.lastVoicedAt >= p.hangMs) {
      this.open = false;
      events.push({ type: "close", at: this.lastVoicedAt });
    }

    // Optional uplink attenuation target (uplink.ts): only during floor-level
    // background, never while a candidate/open/any energy rise is present.
    const energyRise = features.rmsDb > p.floorDb + ATTENUATION_RELEASE_DB;
    this.uplinkGain =
      p.attenuationDb <= 0 || this.open || this.candidateSince !== null || energyRise
        ? 1
        : dbToAmplitude(-p.attenuationDb);
    return events;
  }

  /** Where the last frame landed, for diagnostics. */
  snapshot(): GateSnapshot {
    const f = this.lastFeatures;
    const classification: GateClassification = this.open
      ? "speech"
      : this.burstSince !== null || this.candidateSince !== null
        ? "background"
        : "quiet";
    return {
      open: this.open,
      prob: round1(this.last.prob * 100) / 100,
      rmsDb: f ? round1(f.rmsDb) : -100,
      floorDb: round1(this.params.floorDb),
      marginDb: round1(this.last.marginDb),
      spectralScore: round1(this.last.spectralScore * 100) / 100,
      clipRatio: f ? f.clipRatio : 0,
      classification,
      playbackActive: this.lastPlayback,
      uplinkGain: this.uplinkGain,
      stats: this.stats(),
    };
  }

  stats(): SpeechDetectorStats {
    return { ...this.counters, speech_ms: Math.round(this.counters.speech_ms) };
  }

  /** Forget in-flight state (device switch); counters are kept for the session. */
  resetState(): void {
    this.open = false;
    this.candidateSince = null;
    this.burstSince = null;
    this.uplinkGain = 1;
  }
}
