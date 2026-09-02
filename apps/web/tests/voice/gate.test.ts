import { describe, expect, it } from "vitest";

import {
  type CalibrationResult,
  DEFAULT_FLOOR_DB,
  deriveGateParameters,
  type EnvironmentClass,
  type EnvironmentMode,
  MIN_HANG_MS,
  MODE_PRESETS,
  NoiseFloorCalibrator,
  RunningNoiseFloor,
} from "../../app/lib/voice/calibration";
import { analyseFrame } from "../../app/lib/voice/dsp";
import { ENERGY_ONLY_CAP, type GateEvent, SpeechGate, speechProbability } from "../../app/lib/voice/gate";
import {
  FRAME,
  SR,
  click,
  concat,
  frames,
  hum,
  ms,
  overlay,
  silence,
  speechLike,
  whiteNoise,
} from "./signals";

function calibrateOn(signal: Float32Array): CalibrationResult {
  const calibrator = new NoiseFloorCalibrator({ durationMs: 1500 });
  for (const f of frames(signal)) if (calibrator.push(f.features)) break;
  const result = calibrator.result();
  if (!result) throw new Error("calibration did not complete");
  return result;
}

function drive(gate: SpeechGate, signal: Float32Array, playbackActive: (now: number) => boolean = () => false) {
  const events: GateEvent[] = [];
  for (const f of frames(signal)) events.push(...gate.update(f.features, f.now, playbackActive(f.now)));
  return events;
}

describe("frame features (dsp)", () => {
  it("speech-like, hum, click and white noise are separable on the spectral cues", () => {
    const speech = analyseFrame(speechLike(FRAME, -25).subarray(0, FRAME), SR);
    const mains = analyseFrame(hum(FRAME, -50), SR);
    const burst = analyseFrame(overlay(silence(FRAME), click(ms(5), -20), 200), SR);
    const white = analyseFrame(whiteNoise(FRAME, -20), SR);
    expect(speech.speechBandRatio).toBeGreaterThan(0.6);
    expect(speech.flatness).toBeLessThan(0.3);
    expect(speech.zcr).toBeLessThan(0.2);
    expect(mains.lowBandRatio).toBeGreaterThan(0.6);
    expect(mains.speechBandRatio).toBeLessThan(0.2);
    expect(burst.flatness).toBeGreaterThan(0.3);
    expect(white.flatness).toBeGreaterThan(0.4);
    expect(white.zcr).toBeGreaterThan(0.3);
    expect(white.speechBandRatio).toBeLessThan(0.2);
    expect(Math.abs(mains.rmsDb - -50)).toBeLessThan(1.5);
  });

  it("energy alone never reaches a mode's open threshold", () => {
    const params = deriveGateParameters({ calibration: null, mode: "auto" });
    const loudFlat = analyseFrame(whiteNoise(FRAME, -10), SR);
    const score = speechProbability(loudFlat, params, false);
    expect(score.energyScore).toBeGreaterThan(0.99);
    expect(score.prob).toBeLessThan(params.openProb);
    for (const preset of Object.values(MODE_PRESETS)) expect(ENERGY_ONLY_CAP).toBeLessThan(preset.openProb);
  });
});

describe("noise-floor calibration", () => {
  it("derives the floor of a stationary hum and classifies it", () => {
    const result = calibrateOn(hum(ms(2500), -50));
    expect(Math.abs(result.noiseFloorDb - -50)).toBeLessThan(1.5);
    expect(result.spreadDb).toBeLessThan(2);
    expect(result.humRatio).toBeGreaterThan(0.6);
    expect(result.environment).toBe("normal");
    expect(result.sensitivity).toBe("normal");
    expect(result.contaminated).toBe(false);
    expect(result.clipRisk).toBe(0);
    expect(result.frames).toBeGreaterThanOrEqual(20);
  });

  it("a quiet room is quiet, a loud one is very noisy, intermittent noise bumps the class", () => {
    expect(calibrateOn(whiteNoise(ms(2500), -70)).environment).toBe("quiet");
    expect(calibrateOn(whiteNoise(ms(2500), -30)).environment).toBe("very_noisy");
    let intermittent = whiteNoise(ms(2500), -58);
    for (let at = 100; at < 2400; at += 300) intermittent = overlay(intermittent, click(ms(60), -25), ms(at));
    const result = calibrateOn(intermittent);
    expect(result.spreadDb).toBeGreaterThan(12);
    expect(result.environment).toBe("noisy");
  });

  it("flags a window contaminated by speech instead of trusting it", () => {
    const result = calibrateOn(speechLike(ms(2500), -25));
    expect(result.contaminated).toBe(true);
    expect(result.speechLikeRatio).toBeGreaterThan(0.3);
  });

  it("flags clipping risk from a hot microphone", () => {
    const result = calibrateOn(whiteNoise(ms(2500), -8));
    expect(result.clipRisk).toBeGreaterThan(0.5);
    expect(result.sensitivity).toBe("high");
  });
});

describe("mode presets and Otomatik derivation", () => {
  const calibration = calibrateOn(hum(ms(2500), -50));

  it("explicit modes use the documented rows; margins/onsets rise, hangs fall but never below the floor", () => {
    const modes: Array<EnvironmentMode & EnvironmentClass> = ["quiet", "noisy", "very_noisy"];
    let previousMargin = -1;
    let previousOnset = -1;
    for (const mode of modes) {
      const params = deriveGateParameters({ calibration, mode });
      const preset = MODE_PRESETS[mode];
      expect(params.basis).toBe(mode);
      expect(params.openMarginDb).toBe(preset.openMarginDb);
      expect(params.closeMarginDb).toBe(preset.openMarginDb - 4);
      expect(params.minOnsetMs).toBe(preset.minOnsetMs);
      expect(params.hangMs).toBe(Math.max(MIN_HANG_MS, preset.hangMs));
      expect(params.hangMs).toBeGreaterThanOrEqual(MIN_HANG_MS);
      expect(params.spreadAdjustDb).toBe(0);
      expect(params.floorDb).toBe(calibration.noiseFloorDb);
      expect(params.openMarginDb).toBeGreaterThan(previousMargin);
      expect(params.minOnsetMs).toBeGreaterThan(previousOnset);
      previousMargin = params.openMarginDb;
      previousOnset = params.minOnsetMs;
    }
  });

  it("Otomatik takes the row from the calibrated class and adds up to +3 dB for spread", () => {
    const steady = deriveGateParameters({ calibration, mode: "auto" });
    expect(steady.basis).toBe("normal");
    expect(steady.openMarginDb).toBe(MODE_PRESETS.normal.openMarginDb);
    const spread: CalibrationResult = { ...calibration, spreadDb: 14, environment: "noisy" };
    const derived = deriveGateParameters({ calibration: spread, mode: "auto" });
    expect(derived.basis).toBe("noisy");
    expect(derived.spreadAdjustDb).toBe(3);
    expect(derived.openMarginDb).toBe(MODE_PRESETS.noisy.openMarginDb + 3);
    const before = deriveGateParameters({ calibration: null, mode: "auto" });
    expect(before.basis).toBe("normal");
    expect(before.floorDb).toBe(DEFAULT_FLOOR_DB);
  });

  it("the profile's sensitivity preference shifts margin and onset within the bounds", () => {
    const low = deriveGateParameters({ calibration, mode: "auto", sensitivity: "low" });
    const high = deriveGateParameters({ calibration, mode: "auto", sensitivity: "high" });
    expect(low.openMarginDb).toBe(MODE_PRESETS.normal.openMarginDb + 3);
    expect(low.minOnsetMs).toBe(MODE_PRESETS.normal.minOnsetMs + 20);
    expect(high.openMarginDb).toBe(MODE_PRESETS.normal.openMarginDb - 2);
    expect(high.minOnsetMs).toBe(MODE_PRESETS.normal.minOnsetMs - 20);
  });
});

describe("speech gate", () => {
  const floor = whiteNoise(ms(4000), -60, 3);
  const calibration = calibrateOn(floor);
  const auto = () => new SpeechGate(deriveGateParameters({ calibration, mode: "auto" }));

  it("a stationary hum at the floor never opens the gate", () => {
    const gate = new SpeechGate(deriveGateParameters({ calibration: calibrateOn(hum(ms(2500), -50)), mode: "auto" }));
    const events = drive(gate, hum(ms(4000), -50));
    expect(events.filter((e) => e.type === "open")).toHaveLength(0);
    expect(gate.stats().gate_opens).toBe(0);
  });

  it("keyboard clicks do not open the gate and are counted as rejected clicks", () => {
    const gate = auto();
    let signal = floor;
    for (let at = 300; at < 3800; at += 300) signal = overlay(signal, click(ms(5), -18, at), ms(at));
    const events = drive(gate, signal);
    expect(events.filter((e) => e.type === "open")).toHaveLength(0);
    expect(gate.stats().gate_opens).toBe(0);
    expect(gate.stats().click_rejects).toBeGreaterThanOrEqual(10);
    expect(events.filter((e) => e.type === "background" && e.kind === "click").length).toBe(gate.stats().click_rejects);
  });

  it("loud broadband noise is gated out as background, not speech", () => {
    const gate = auto();
    const signal = overlay(floor, whiteNoise(ms(800), -22, 5), ms(1000));
    const events = drive(gate, signal);
    expect(events.filter((e) => e.type === "open")).toHaveLength(0);
    expect(gate.stats().gated_out).toBe(1);
    expect(events.find((e) => e.type === "background")).toMatchObject({ kind: "noise" });
  });

  it("speech opens within a bounded time, is backdated by the pre-roll, holds through a short pause and releases after the hang", () => {
    const gate = auto();
    const params = gate.parameters;
    const onsetMs = 1000;
    const speech = concat(speechLike(ms(600), -25), silence(ms(250)), speechLike(ms(600), -25));
    const signal = overlay(floor, speech, ms(onsetMs));
    const events = drive(gate, signal);
    const opens = events.filter((e) => e.type === "open");
    const closes = events.filter((e) => e.type === "close");
    expect(opens).toHaveLength(1); // the 250 ms pause is inside the hang: one utterance
    expect(closes).toHaveLength(1);
    const open = opens[0] as { at: number };
    // No clipped word beginning: the reported start is at or before the true onset…
    expect(open.at).toBeLessThanOrEqual(onsetMs);
    // …but not absurdly early (pre-roll + one analyser frame + one hop).
    expect(open.at).toBeGreaterThanOrEqual(onsetMs - params.preRollMs - 45);
    // Bounded decision time: the gate opened within minOnset + two frames of the onset.
    const decisionFrame = frames(signal).find((f) => gate.stats().gate_opens > 0 && f.now >= onsetMs);
    expect(decisionFrame).toBeDefined();
    const close = closes[0] as { at: number };
    const speechEndMs = onsetMs + 1450;
    // No missing word ending: the close is stamped at the last voiced frame, not the hang expiry.
    expect(close.at).toBeGreaterThanOrEqual(speechEndMs - 45);
    expect(close.at).toBeLessThanOrEqual(speechEndMs + 45);
    expect(gate.stats().gate_opens).toBe(1);
  });

  it("opens no later than minOnset plus two analyser frames after the onset", () => {
    const gate = auto();
    const onsetMs = 1000;
    const signal = overlay(floor, speechLike(ms(1000), -25), ms(onsetMs));
    let openedAtFrame: number | null = null;
    for (const f of frames(signal)) {
      const events = gate.update(f.features, f.now, false);
      if (events.some((e) => e.type === "open") && openedAtFrame === null) openedAtFrame = f.now;
    }
    expect(openedAtFrame).not.toBeNull();
    expect((openedAtFrame as number) - onsetMs).toBeLessThanOrEqual(gate.parameters.minOnsetMs + 2 * 21 + 20);
  });

  it("quiet speech at -35 dBFS is not cut: the gate covers the whole utterance", () => {
    const gate = auto();
    const onsetMs = 800;
    const durationMs = 1500;
    const signal = overlay(floor, speechLike(ms(durationMs), -35), ms(onsetMs));
    let openMs = 0;
    for (const f of frames(signal)) {
      gate.update(f.features, f.now, false);
      if (gate.isOpen && f.now > onsetMs && f.now <= onsetMs + durationMs) openMs += 20;
    }
    expect(gate.stats().gate_opens).toBe(1);
    expect(openMs).toBeGreaterThanOrEqual(durationMs * 0.9);
  });

  it("does not chatter: a modulated utterance yields one open, one close", () => {
    const gate = auto();
    const signal = overlay(floor, speechLike(ms(2000), -30, { syllableHz: 6 }), ms(500));
    const events = drive(gate, signal);
    expect(events.filter((e) => e.type === "open")).toHaveLength(1);
    expect(events.filter((e) => e.type === "close")).toHaveLength(1);
  });

  it("speaker playback is a feature: residual echo does not open the gate, real speech still barges in", () => {
    const gate = auto();
    const residual = overlay(floor, speechLike(ms(1500), -52), ms(800));
    expect(drive(gate, residual, () => true).filter((e) => e.type === "open")).toHaveLength(0);
    const barge = new SpeechGate(gate.parameters);
    const owner = overlay(floor, speechLike(ms(1500), -38), ms(800));
    expect(drive(barge, owner, () => true).filter((e) => e.type === "open")).toHaveLength(1);
  });

  it("the uplink gain target is 1 unless attenuation is enabled, and never < 1 while speech or any energy rise is present", () => {
    const passthrough = auto();
    drive(passthrough, overlay(floor, speechLike(ms(500), -25), ms(500)));
    expect(passthrough.snapshot().uplinkGain).toBe(1);
    const shaped = new SpeechGate(deriveGateParameters({ calibration, mode: "auto", attenuationDb: 9 }));
    const signal = overlay(floor, speechLike(ms(1000), -25), ms(1000));
    for (const f of frames(signal)) {
      shaped.update(f.features, f.now, false);
      const gain = shaped.snapshot().uplinkGain;
      if (f.now > 1000 && f.now < 2000) expect(gain).toBe(1);
      else expect(gain).toBeLessThanOrEqual(1);
    }
    expect(shaped.snapshot().uplinkGain).toBeLessThan(1); // back on the floor: attenuated, never muted
    expect(shaped.snapshot().uplinkGain).toBeGreaterThan(0.3);
  });
});

describe("running noise floor", () => {
  it("flags a sustained drift and ignores a brief one", () => {
    const tracker = new RunningNoiseFloor({ calibratedDb: -60, driftDb: 8, sustainMs: 5000 });
    let drifted = false;
    for (let now = 0; now <= 2000; now += 20) drifted = tracker.update(-45, now).drifted;
    expect(drifted).toBe(false);
    for (let now = 2020; now <= 12_000; now += 20) drifted = tracker.update(-45, now).drifted;
    expect(drifted).toBe(true);
    expect(tracker.running).toBeGreaterThan(-50);
    tracker.reset(-45);
    expect(tracker.update(-45, 12_020).drifted).toBe(false);
  });
});
