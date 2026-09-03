/**
 * ADR-0047 §4/§5 on the synthetic signal set — hum, clicks, speech-like,
 * quiet Turkish speech at −35 dBFS, and the new "speaker echo of assistant
 * speech": the echo-aware playback margin is MEASURED (never a constant),
 * the playback-only temporal/spectral consistency is pinned, the per-device
 * adaptation is bounded and reversible, the measured pre-roll caps the
 * table's, and a dead (all-zero) calibration window can never become "the
 * room" — the defect behind the owner's persisted `env=0, peak_db=-100`.
 */
import { describe, expect, it } from "vitest";

import {
  ADAPTATION_LIMITS,
  BARGE_ONSET_EXTRA_MS,
  type CalibrationResult,
  ECHO_THRESHOLD_CAP_DBFS,
  EchoResidualTracker,
  ENVIRONMENT_CLASS_INDEX,
  EVIDENCE_ONSET_MS,
  boundAdaptation,
  deriveGateParameters,
  echoMarginFor,
  MODE_PRESETS,
  NoiseFloorCalibrator,
  SENSITIVITY_INDEX,
} from "../../app/lib/voice/calibration";
import { analyseFrame } from "../../app/lib/voice/dsp";
import { type GateEvent, SpeechGate } from "../../app/lib/voice/gate";
import { defaultProfile, learnFromSession, normalizeProfile } from "../../app/lib/voice/profile";
import { FRAME, SR, click, concat, echoResidual, frames, hum, ms, overlay, silence, speechLike, whiteNoise } from "./signals";

function calibrateOn(signal: Float32Array): CalibrationResult {
  const calibrator = new NoiseFloorCalibrator({ durationMs: 1500 });
  for (const f of frames(signal)) if (calibrator.push(f.features)) break;
  const result = calibrator.result();
  if (!result) throw new Error("calibration did not complete");
  return result;
}

function drive(gate: SpeechGate, signal: Float32Array, playback: boolean): GateEvent[] {
  const events: GateEvent[] = [];
  for (const f of frames(signal)) events.push(...gate.update(f.features, f.now, playback));
  return events;
}

const opens = (events: GateEvent[]) => events.filter((e) => e.type === "open");

const floor = whiteNoise(ms(4000), -60, 3);
const calibration = calibrateOn(floor);

/** The residual as the detector would measure it: closed-gate playback frames into the tracker. */
function measureResidual(signal: Float32Array): number {
  const tracker = new EchoResidualTracker();
  for (const f of frames(signal)) tracker.push(f.features.rmsDb, f.features.durationMs);
  const residual = tracker.residualDb ?? tracker.flush();
  if (residual === null) throw new Error("no residual measured");
  return residual;
}

describe("§4 echo-aware playback margin (measured, not a constant)", () => {
  const echo = overlay(floor, echoResidual(ms(3000), -38), ms(500));

  it("the ADR-0044 constant margin lets a hot loudspeaker residual open the gate; the measured residual closes it", () => {
    // Before: the preset's +6 dB (threshold −44 dBFS) against a −38 dBFS residual.
    const before = new SpeechGate({ ...deriveGateParameters({ calibration, mode: "auto" }), bargeOnsetExtraMs: 0 });
    expect(opens(drive(before, echo, true)).length).toBeGreaterThanOrEqual(1);
    // After: the residual is measured on closed-gate playback frames and the
    // threshold sits ECHO_RESIDUAL_HEADROOM_DB above its p80.
    const residualDb = measureResidual(echo);
    expect(residualDb).toBeGreaterThan(-42);
    expect(residualDb).toBeLessThan(-33);
    const params = deriveGateParameters({ calibration, mode: "auto", echoResidualDb: residualDb });
    expect(params.echoExtraMarginDb).toBeGreaterThan(MODE_PRESETS.normal.echoExtraMarginDb);
    expect(params.echoResidualAdjustDb).toBeCloseTo(params.echoExtraMarginDb - MODE_PRESETS.normal.echoExtraMarginDb, 6);
    const after = new SpeechGate(params);
    expect(opens(drive(after, echo, true))).toHaveLength(0);
    // The owner still barges in through it.
    const owner = new SpeechGate(params);
    expect(opens(drive(owner, overlay(floor, speechLike(ms(1500), -22), ms(800)), true))).toHaveLength(1);
    // And the margin outside playback is untouched: quiet speech coverage stays.
    const quiet = new SpeechGate(params);
    let openMs = 0;
    for (const f of frames(overlay(floor, speechLike(ms(1500), -35), ms(800)))) {
      quiet.update(f.features, f.now, false);
      if (quiet.isOpen && f.now > 800 && f.now <= 2300) openMs += 20;
    }
    expect(openMs).toBeGreaterThanOrEqual(1500 * 0.9);
  });

  it("the preset is the minimum and the threshold is capped so barge-in stays physically possible", () => {
    const base = { floorDb: -60, openMarginDb: 10, presetEchoExtraDb: 6 };
    expect(echoMarginFor({ ...base, echoResidualDb: null })).toEqual({ echoExtraMarginDb: 6, echoResidualAdjustDb: 0 });
    expect(echoMarginFor({ ...base, echoResidualDb: -58 }).echoExtraMarginDb).toBe(6); // a quiet residual never weakens the preset
    expect(echoMarginFor({ ...base, echoResidualDb: -40 }).echoExtraMarginDb).toBe(16); // −40 + 6 − (−60) − 10
    const capped = echoMarginFor({ ...base, echoResidualDb: -20 }).echoExtraMarginDb;
    expect(-60 + 10 + capped).toBe(ECHO_THRESHOLD_CAP_DBFS);
  });

  it("the residual tracker rises at once and falls slowly; a one-word answer measures nothing", () => {
    const tracker = new EchoResidualTracker();
    for (let i = 0; i < 80; i += 1) tracker.push(-45 + (i % 5), 21.3);
    expect(tracker.residualDb).not.toBeNull();
    const first = tracker.residualDb as number;
    expect(first).toBeGreaterThanOrEqual(-42);
    for (let i = 0; i < 80; i += 1) tracker.push(-60, 21.3);
    expect(tracker.residualDb).toBe(Math.round((first - 1) * 10) / 10); // at most 1 dB per window
    for (let i = 0; i < 80; i += 1) tracker.push(-30, 21.3);
    expect(tracker.residualDb).toBe(-30); // louder: immediately
    const short = new EchoResidualTracker();
    for (let i = 0; i < 5; i += 1) short.push(-30, 21.3);
    expect(short.flush()).toBeNull();
    expect(short.residualDb).toBeNull();
  });
});

describe("§2/§4 playback-only consistency: reversible evidence first, then the irreversible open", () => {
  const params = deriveGateParameters({ calibration, mode: "auto" });

  it("owner speech during playback: evidence after EVIDENCE_ONSET_MS, open after minOnset + BARGE_ONSET_EXTRA_MS", () => {
    const gate = new SpeechGate(params);
    const events = drive(gate, overlay(floor, speechLike(ms(1500), -22), ms(800)), true);
    const evidence = events.find((e) => e.type === "evidence");
    const open = events.find((e) => e.type === "open");
    expect(evidence).toBeDefined();
    expect(open).toBeDefined();
    if (!evidence || !open || evidence.type !== "evidence" || open.type !== "open") throw new Error("unreachable");
    expect(evidence.at).toBeLessThan(open.decidedAt);
    expect(evidence.at - evidence.candidateAt).toBeGreaterThanOrEqual(EVIDENCE_ONSET_MS);
    expect(open.decidedAt - open.candidateAt).toBeGreaterThanOrEqual(params.minOnsetMs + BARGE_ONSET_EXTRA_MS);
    expect(open.duringPlayback).toBe(true);
    expect(open.candidateAt).toBe(evidence.candidateAt);
    // Outside playback the open needs only minOnset and no evidence is emitted.
    const plain = new SpeechGate(params);
    const quietEvents = drive(plain, overlay(floor, speechLike(ms(1500), -22), ms(800)), false);
    expect(quietEvents.some((e) => e.type === "evidence")).toBe(false);
    const plainOpen = quietEvents.find((e) => e.type === "open");
    if (!plainOpen || plainOpen.type !== "open") throw new Error("no open");
    expect(plainOpen.decidedAt - plainOpen.candidateAt).toBeLessThan(params.minOnsetMs + BARGE_ONSET_EXTRA_MS);
  });

  it("clicks during playback never produce evidence; a burst that collapses is evidence_lost, not an open", () => {
    const clicks = new SpeechGate(params);
    let signal = floor;
    for (let at = 300; at < 3800; at += 300) signal = overlay(signal, click(ms(5), -18, at), ms(at));
    const clickEvents = drive(clicks, signal, true);
    expect(clickEvents.some((e) => e.type === "evidence" || e.type === "open")).toBe(false);
    const burst = new SpeechGate(params);
    const shortBurst = overlay(floor, speechLike(ms(70), -22), ms(1000));
    const burstEvents = drive(burst, shortBurst, true);
    expect(burstEvents.some((e) => e.type === "evidence")).toBe(true);
    expect(burstEvents.some((e) => e.type === "evidence_lost")).toBe(true);
    expect(opens(burstEvents)).toHaveLength(0);
    expect(burst.stats().evidence_lost).toBe(1);
  });

  it("the synthetic set: fewer false opens than the ADR-0044 gate, quiet-speech coverage ≥ 90 %, barge-in kept", () => {
    const echo = overlay(floor, echoResidual(ms(3000), -38), ms(500));
    const residualDb = measureResidual(echo);
    const oldParams = { ...deriveGateParameters({ calibration, mode: "auto" }), bargeOnsetExtraMs: 0 };
    const newParams = deriveGateParameters({ calibration, mode: "auto", echoResidualDb: residualDb });
    const nonSpeech: Array<[Float32Array, boolean]> = [
      [hum(ms(3000), -50), false],
      [(() => {
        let s = floor;
        for (let at = 300; at < 3800; at += 300) s = overlay(s, click(ms(5), -18, at), ms(at));
        return s;
      })(), false],
      [overlay(floor, whiteNoise(ms(800), -22, 5), ms(1000)), false],
      [echo, true],
      [overlay(floor, echoResidual(ms(1500), -40), ms(600)), true],
    ];
    const falseOpens = (p: typeof oldParams) => nonSpeech.reduce((n, [signal, playback]) => n + opens(drive(new SpeechGate(p), signal, playback)).length, 0);
    const before = falseOpens(oldParams);
    const after = falseOpens(newParams);
    expect(before).toBeGreaterThan(0);
    expect(after).toBe(0);
    expect(after).toBeLessThan(before);
    // Quiet Turkish speech at −35 dBFS, no playback: coverage.
    const quiet = new SpeechGate(newParams);
    let openMs = 0;
    for (const f of frames(overlay(floor, speechLike(ms(1500), -35), ms(800)))) {
      quiet.update(f.features, f.now, false);
      if (quiet.isOpen && f.now > 800 && f.now <= 2300) openMs += 20;
    }
    expect(openMs / 1500).toBeGreaterThanOrEqual(0.9);
    // Barge-in through the measured echo margin.
    expect(opens(drive(new SpeechGate(newParams), overlay(floor, speechLike(ms(1500), -22), ms(800)), true))).toHaveLength(1);
  });
});

describe("§1 measured pre-roll", () => {
  it("a measured pre-roll caps the table's; without a measurement the table applies", () => {
    const params = deriveGateParameters({ calibration, mode: "auto" });
    const signal = overlay(floor, speechLike(ms(1000), -25), ms(1000));
    const measured = new SpeechGate(params);
    let open: GateEvent | undefined;
    for (const f of frames(signal)) {
      open ??= measured.update(f.features, f.now, false, { preRollMs: 55 }).find((e) => e.type === "open");
    }
    if (!open || open.type !== "open") throw new Error("no open");
    expect(open.preRollMs).toBe(55);
    expect(open.at).toBe(open.candidateAt - 55);
    expect(open.decidedAt - open.candidateAt).toBeGreaterThanOrEqual(params.minOnsetMs);
    const table = new SpeechGate(params);
    const plain = drive(table, signal, false).find((e) => e.type === "open");
    if (!plain || plain.type !== "open") throw new Error("no open");
    expect(plain.preRollMs).toBe(params.preRollMs);
    // A "measured" value larger than the table is capped: the table is the maximum backdating.
    const large = new SpeechGate(params);
    let capped: GateEvent | undefined;
    for (const f of frames(signal)) capped ??= large.update(f.features, f.now, false, { preRollMs: 400 }).find((e) => e.type === "open");
    if (!capped || capped.type !== "open") throw new Error("no open");
    expect(capped.preRollMs).toBe(params.preRollMs);
  });
});

describe("§4 per-device learned adaptation", () => {
  const k66 = defaultProfile({ deviceId: "abc", label: "K66 (USB Audio)", groupId: "grp" });

  it("learns from the session's own false starts, is bounded, and decays on clean sessions", () => {
    let profile = k66;
    // The owner's real session: 5 false starts, 4 of them during playback.
    profile = learnFromSession(profile, { false_starts: 5, false_barge_ins: 4, gate_opens: 15, confirmed_turns: 9 }, "2026-09-03T10:00:00Z");
    expect(profile.learned).toMatchObject({ marginDb: 0, onsetMs: 0, echoMarginDb: 2, sessions: 1 }); // 1 quiet false start: not enough to teach margin
    profile = learnFromSession(profile, { false_starts: 3, false_barge_ins: 0, gate_opens: 10, confirmed_turns: 6 }, "2026-09-03T11:00:00Z");
    expect(profile.learned).toMatchObject({ marginDb: 1, onsetMs: 10, echoMarginDb: 2, sessions: 2 });
    for (let i = 0; i < 10; i += 1) {
      profile = learnFromSession(profile, { false_starts: 4, false_barge_ins: 2, gate_opens: 10, confirmed_turns: 5 }, "2026-09-03T12:00:00Z");
    }
    expect(profile.learned.marginDb).toBe(ADAPTATION_LIMITS.marginDb);
    expect(profile.learned.onsetMs).toBe(ADAPTATION_LIMITS.onsetMs);
    expect(profile.learned.echoMarginDb).toBe(ADAPTATION_LIMITS.echoMarginDb);
    const clean = learnFromSession(profile, { false_starts: 0, false_barge_ins: 0, gate_opens: 6, confirmed_turns: 6 }, "2026-09-03T13:00:00Z");
    expect(clean.learned.marginDb).toBe(ADAPTATION_LIMITS.marginDb - 0.5);
    expect(clean.learned.echoMarginDb).toBe(ADAPTATION_LIMITS.echoMarginDb - 1);
    // A session with nothing to say teaches nothing (same object back).
    expect(learnFromSession(clean, { false_starts: 0, false_barge_ins: 0, gate_opens: 0, confirmed_turns: 1 }, "x")).toBe(clean);
    // Whatever the store carries is bounded on the way in.
    const foreign = normalizeProfile({ fingerprint: "00ff00ff", learned: { marginDb: 99, onsetMs: -5, echoMarginDb: "x", sessions: 3 } });
    expect(foreign?.learned).toMatchObject({ marginDb: ADAPTATION_LIMITS.marginDb, onsetMs: 0, echoMarginDb: 0, sessions: 3 });
    expect(boundAdaptation(null)).toEqual({ marginDb: 0, onsetMs: 0, echoMarginDb: 0 });
  });

  it("at the adaptation cap the gate still covers quiet speech at −35 dBFS and never exceeds the onset bound", () => {
    const params = deriveGateParameters({
      calibration,
      mode: "auto",
      adaptation: { marginDb: ADAPTATION_LIMITS.marginDb, onsetMs: ADAPTATION_LIMITS.onsetMs, echoMarginDb: ADAPTATION_LIMITS.echoMarginDb },
    });
    expect(params.openMarginDb).toBe(MODE_PRESETS.normal.openMarginDb + ADAPTATION_LIMITS.marginDb);
    expect(params.minOnsetMs).toBe(MODE_PRESETS.normal.minOnsetMs + ADAPTATION_LIMITS.onsetMs);
    expect(params.minOnsetMs).toBeLessThanOrEqual(140);
    expect(params.echoExtraMarginDb).toBe(MODE_PRESETS.normal.echoExtraMarginDb + ADAPTATION_LIMITS.echoMarginDb);
    const gate = new SpeechGate(params);
    let openMs = 0;
    for (const f of frames(overlay(floor, speechLike(ms(1500), -35), ms(800)))) {
      gate.update(f.features, f.now, false);
      if (gate.isOpen && f.now > 800 && f.now <= 2300) openMs += 20;
    }
    expect(openMs / 1500).toBeGreaterThanOrEqual(0.9);
  });
});

describe("§5 calibration can never be a sentinel", () => {
  it("a dead (all-zero) window is an attempt, not a measurement; the owner's env=0 / peak_db=−100 is impossible", () => {
    const dead = new NoiseFloorCalibrator({ durationMs: 1500, maxDeadMs: 600 });
    let done = false;
    for (const f of frames(silence(ms(2000)))) {
      done = dead.push(f.features);
      if (done) break;
    }
    expect(done).toBe(true);
    expect(dead.result()).toBeNull();
    const outcome = dead.outcome();
    expect(outcome?.kind).toBe("dead");
    if (outcome?.kind !== "dead") throw new Error("unreachable");
    expect(outcome.deadFrames).toBeGreaterThan(0);
    expect(outcome.liveFrames).toBe(0);
    // A window that starts dead and then flows measures the LIVE part only.
    const mixed = new NoiseFloorCalibrator({ durationMs: 1500, maxDeadMs: 3000 });
    for (const f of frames(concat(silence(ms(600)), whiteNoise(ms(2500), -55, 9)))) if (mixed.push(f.features)) break;
    const result = mixed.result();
    expect(result).not.toBeNull();
    expect(result?.deadFrames).toBeGreaterThan(0);
    expect(result?.frames).toBeGreaterThanOrEqual(20);
    expect(Math.abs((result?.noiseFloorDb ?? 0) - -55)).toBeLessThan(1.5);
    expect(result?.peakDb).not.toBeNull();
    expect(result?.peakDb).toBeGreaterThan(-100);
    // Classes are 1-based: a reported 0 cannot mean "the first class".
    for (const value of Object.values(ENVIRONMENT_CLASS_INDEX)) expect(value).toBeGreaterThanOrEqual(1);
    for (const value of Object.values(SENSITIVITY_INDEX)) expect(value).toBeGreaterThanOrEqual(1);
    expect(ENVIRONMENT_CLASS_INDEX.quiet).toBe(1);
    expect(SENSITIVITY_INDEX.low).toBe(1);
  });

  it("a live frame always has a peak; digital silence inside a live window never produces a −100 peak", () => {
    const f = analyseFrame(speechLike(FRAME, -25).subarray(0, FRAME), SR);
    expect(f.peak).toBeGreaterThan(0);
    expect(analyseFrame(silence(FRAME), SR).peak).toBe(0);
  });
});
