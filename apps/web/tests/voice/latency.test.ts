/**
 * ADR-0047 §1–§3 — the latency decomposition the owner's real K66 session
 * could not provide: mic→uplink from the transport's own counters, barge-in
 * split into detect / stop command / gain-to-zero with the reversible early
 * mute, first audio split into provider decision / generation / local
 * playback — and every local start settled by exactly one
 * `uplink_first_packet` or an explicit unmatched marker.
 *
 * Every test would fail on the defect it names (see the comments); the last
 * one sweeps every payload key through the server's forbidden-key rule.
 */
import { describe, expect, it } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { STATE_EVENT_KINDS, isForbiddenKey } from "../../app/lib/voice/contract";
import { VoiceSessionController } from "../../app/lib/voice/controller";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
  type FakeTransportOptions,
} from "../../app/lib/voice/fake";
import type { OutboundAudioStats } from "../../app/lib/voice/transport";

const tick = async (rounds = 4): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

/** A continuously streaming Opus track: one RTP packet every 20 ms. */
const streaming = (now: number): OutboundAudioStats => ({
  packetsSent: Math.floor(now / 20),
  bytesSent: Math.floor(now / 20) * 160,
  timestamp: now,
});

/** A DTX-silent track: the counters never move inside the probe's bound. */
const silent = (now: number): OutboundAudioStats => ({ packetsSent: 7, bytesSent: 700, timestamp: now });

async function setup(options: { transport?: FakeTransportOptions; probe?: { pollMs?: number; maxMs?: number } } = {}) {
  const log: string[] = [];
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
  const transports: FakeTransport[] = [];
  const playback = new FakePlayback(scheduler.now, (op) => log.push(op));
  const microphone = new FakeMicrophone();
  const localSpeech = new FakeSpeechDetector();
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => {
      const transport = new FakeTransport({ log: (op) => log.push(op), now: scheduler.now, ...options.transport });
      transports.push(transport);
      return transport;
    },
    playback,
    network: new FakeNetwork(),
    microphone,
    localSpeech,
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    localSpeechGraceMs: 700,
    uplinkProbe: options.probe,
    inputEvidence: () => ({ agc_bench: 1, agc_bench_off_score: 61.5, agc_bench_on_score: 44, agc_bench_recommended_on: 0, label: "dropped" }),
    log: (op) => log.push(op),
  });
  await controller.connect();
  await tick();
  /** Advance the manual clock in steps, letting the async probe run between them. */
  const pump = async (ms: number, step = 10): Promise<void> => {
    for (let i = 0; i < ms; i += step) {
      scheduler.advance(step);
      await tick();
    }
  };
  return {
    controller,
    core,
    scheduler,
    playback,
    localSpeech,
    log,
    pump,
    get transport() {
      return transports[transports.length - 1];
    },
  };
}

function assertServerSafe(core: FakeCloudCore): void {
  for (const event of core.events) {
    for (const key of Object.keys(event.payload ?? {})) expect(isForbiddenKey(key)).toBe(false);
  }
}

describe("§1 mic→uplink from the transport's counters", () => {
  it("measures the first RTP packet after the gate's decision and reports the split, basis 1", async () => {
    const t = await setup({ transport: { uplinkStats: streaming } });
    t.scheduler.advance(1000);
    // Gate: onset frame observed at 930 (capture lag 12), decided at 1000, pre-roll 50 → onset 880.
    t.localSpeech.speechStart(880, { candidateAt: 930, decidedAt: 1000, preRollMs: 50, captureLagMs: 12 });
    await t.pump(40);
    expect(t.log).toContain("uplink.rtp:20"); // the 1020 packet: first increase after the 1000 decision
    t.scheduler.advance(310);
    t.transport.emit({ type: "speech_started", at: 1350 });
    await t.controller.flushEvents();
    expect(t.core.events.find((e) => e.kind === "mic_speech_start")).toMatchObject({
      t_ms: 880,
      turn: 1,
      payload: { source: 1, gate_ms: 70, capture_lag_ms: 12, pre_roll_ms: 50 },
    });
    const uplinks = t.core.events.filter((e) => e.kind === "uplink_first_packet");
    expect(uplinks).toHaveLength(1);
    expect(uplinks[0]).toMatchObject({ t_ms: 1020, turn: 1, payload: { basis: 1, rtp_ms: 20, provider_ms: 350 } });
    expect(t.controller.getSnapshot().latency.mic_to_uplink_ms?.value).toBe(140);
    expect(t.controller.getSnapshot().latencyDetail.uplink).toEqual({
      basis: 1,
      rtp_ms: 20,
      provider_ms: 350,
      gate_ms: 70,
      capture_lag_ms: 12,
      pre_roll_ms: 50,
    });
    expect(t.core.events.some((e) => e.kind === "state" && e.payload?.unmatched === 1)).toBe(false);
    assertServerSafe(t.core);
  });

  it("a provider confirmation that lands while the probe still polls is reported once, by the probe", async () => {
    const t = await setup({ transport: { uplinkStats: streaming } });
    t.scheduler.advance(1000);
    t.localSpeech.speechStart(880, { candidateAt: 930, decidedAt: 1000, preRollMs: 50 });
    t.transport.emit({ type: "speech_started", at: 1005 }); // before any poll completed
    await t.pump(40);
    await t.controller.flushEvents();
    const uplinks = t.core.events.filter((e) => e.kind === "uplink_first_packet");
    expect(uplinks).toHaveLength(1);
    expect(uplinks[0]).toMatchObject({ t_ms: 1020, payload: { basis: 1, rtp_ms: 20, provider_ms: 5 } });
  });

  it("no packet inside the bound: falls back to the provider's confirmation, basis 0, rtp_ms absent", async () => {
    const t = await setup({ transport: { uplinkStats: silent }, probe: { maxMs: 200 } });
    t.scheduler.advance(1000);
    t.localSpeech.speechStart(880, { candidateAt: 930, decidedAt: 1000, preRollMs: 50 });
    await t.pump(100);
    t.transport.emit({ type: "speech_started", at: 1100 });
    await t.pump(150);
    await t.controller.flushEvents();
    const uplinks = t.core.events.filter((e) => e.kind === "uplink_first_packet");
    expect(uplinks).toHaveLength(1);
    expect(uplinks[0]).toMatchObject({ t_ms: 1100, payload: { basis: 0, provider_ms: 100 } });
    expect(uplinks[0].payload).not.toHaveProperty("rtp_ms");
    expect(uplinks[0].payload?.basis).toBeTypeOf("number");
  });

  it("every local start ends in exactly one uplink_first_packet or an explicit unmatched marker — never silent loss", async () => {
    const t = await setup({ transport: { uplinkStats: streaming } });
    // (1) a false start: the provider never confirms
    t.scheduler.advance(500);
    t.localSpeech.speechStart(450, { candidateAt: 480, decidedAt: 500, preRollMs: 30 });
    await t.pump(40);
    t.localSpeech.speechEnd(700);
    t.scheduler.advance(700);
    // (2) a provider-first start: no local onset to measure against
    t.scheduler.advance(300);
    t.transport.emit({ type: "speech_started", at: 1700 });
    t.transport.emit({ type: "speech_stopped", at: 2000 });
    t.scheduler.advance(300);
    // (3) a local start cut by network loss before any confirmation
    t.scheduler.advance(500);
    t.localSpeech.speechStart(2750, { candidateAt: 2780, decidedAt: 2800, preRollMs: 30 });
    await t.pump(40);
    t.transport.emit({ type: "disconnected", at: 2850, reason: "peer_failed" });
    await tick(8);
    await t.controller.flushEvents();
    const starts = t.core.events.filter((e) => e.kind === "mic_speech_start");
    expect(starts.map((e) => e.turn)).toEqual([1, 2, 3]);
    expect(t.core.events.filter((e) => e.kind === "uplink_first_packet")).toHaveLength(0);
    const markers = t.core.events.filter((e) => e.kind === "state" && e.payload?.unmatched === 1);
    expect(markers.map((e) => e.turn)).toEqual([1, 2, 3]);
    expect(markers[0].payload).toMatchObject({ mic_metrics: 1, false_start: 1 });
    expect(markers[1].payload).toMatchObject({ mic_metrics: 1, provider_first: 1 });
    expect(markers[2].payload).toMatchObject({ mic_metrics: 1, network_lost: 1 });
    assertServerSafe(t.core);
  });
});

describe("§2 barge-in split and the reversible early mute", () => {
  it("a confident onset during playback mutes locally before the turn is confirmed; the split is numbers", async () => {
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 0 });
    t.scheduler.advance(100);
    t.transport.emit({ type: "audio_started", at: 100 });
    t.scheduler.advance(20);
    t.playback.activity(120);
    t.scheduler.advance(420); // now = 540
    t.log.length = 0;
    t.localSpeech.evidence(540, 500);
    expect(t.log).toEqual(["playback.mute", "playback.mute@540"]);
    expect(t.playback.muted).toBe(true);
    expect(t.playback.playing).toBe(true); // reversible: nothing cancelled yet
    expect(t.transport.sent).toEqual([]);
    t.scheduler.advance(60); // now = 600: the gate confirms the open
    t.localSpeech.level = { marginDb: 16, spectralScore: 0.8, frames: 8 };
    t.localSpeech.speechStart(450, { candidateAt: 500, decidedAt: 600, preRollMs: 50, captureLagMs: 8, duringPlayback: true });
    // The open is a potential barge-in: the mute stays (no second mute), nothing is cancelled yet.
    expect(t.log).not.toContain("playback.stop");
    expect(t.transport.sent).toEqual([]);
    t.scheduler.advance(50);
    t.transport.emit({ type: "owner_transcript", at: 650, text: "bugün neler yaptın", final: false });
    t.scheduler.advance(150); // now = 800 = onset 450 + 350: stable → confirmed
    const stop = t.log.indexOf("playback.stop");
    expect(stop).toBeGreaterThan(-1);
    expect(t.log.slice(stop, stop + 4)).toEqual(["playback.stop", "fake.cancelResponse", "transport.cancel", "report.barge_in"]);
    expect(t.transport.sent).toEqual(["cancel"]);
    await t.controller.flushEvents();
    const barge = t.core.events.find((e) => e.kind === "barge_in_start");
    expect(barge).toMatchObject({
      t_ms: 450,
      turn: 1,
      payload: {
        playback_stopped_ms: 90, // onset 450 → silent at 540: below the 150 ms target
        detect_ms: 100,
        pre_roll_ms: 50,
        stop_command_ms: 0,
        gain_zero_ms: 3,
        output_latency_ms: 10,
        early_mute: 1,
        audible: 1,
        anomaly: 0,
        source: 1,
        lane: 2,
        confirm_ms: 350,
        near_field: 1,
      },
    });
    expect(t.core.events.find((e) => e.kind === "playback_stopped")).toMatchObject({ t_ms: 540, turn: 1, payload: { anomaly: 0, early_mute: 1 } });
    expect(t.core.events.find((e) => e.kind === "mic_speech_start")?.payload).toMatchObject({ gate_ms: 100, capture_lag_ms: 8, pre_roll_ms: 50 });
    expect(t.controller.getSnapshot().latency.barge_in_to_stop_ms?.value).toBe(90);
    expect(t.controller.getSnapshot().micMetrics.early_mutes).toBe(1);
    assertServerSafe(t.core);
  });

  it("a response that ends while an early mute is still pending restores playback (the uplink track is never touched)", async () => {
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 0 });
    t.transport.emit({ type: "audio_started", at: 100 });
    t.playback.activity(120);
    t.scheduler.advance(540);
    t.localSpeech.evidence(540, 500);
    expect(t.playback.muted).toBe(true);
    // Neither evidence_lost nor an open: the provider simply finishes.
    t.scheduler.advance(60);
    t.transport.emit({ type: "response_done", at: 600 });
    expect(t.playback.muted).toBe(false); // the gain is back at 1 NOW, not at the next arm()
    expect(t.playback.unmutes).toEqual([600]);
    // ADR-0066: generation done, audio draining; the provider's stop ends it.
    expect(t.controller.getSnapshot().state).toBe("speaking");
    expect(t.controller.getSnapshot().speech.phase).toBe("draining");
    t.transport.emit({ type: "audio_stopped", at: 600 });
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.controller.getSnapshot().micMetrics).toMatchObject({ early_mutes: 1, early_mute_reverts: 1 });
    expect(t.transport.sent).toEqual([]);
    expect(t.transport.audioIn).toEqual([]); // the microphone/uplink path was never touched
    // The same holds when the provider cancels the response underneath a pending mute.
    t.transport.emit({ type: "response_started", at: 1000 });
    t.transport.emit({ type: "audio_started", at: 1100 });
    t.playback.activity(1120);
    t.scheduler.advance(600); // now = 1200
    t.localSpeech.evidence(1200, 1160);
    expect(t.playback.muted).toBe(true);
    t.transport.emit({ type: "response_cancelled", at: 1250 });
    expect(t.playback.muted).toBe(false);
    expect(t.playback.unmutes).toEqual([600, 1200]);
  });

  it("an onset that collapses restores playback and cancels nothing", async () => {
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 0 });
    t.transport.emit({ type: "audio_started", at: 100 });
    t.playback.activity(120);
    t.scheduler.advance(540);
    t.localSpeech.evidence(540, 500);
    t.scheduler.advance(40);
    t.localSpeech.evidenceLost(580);
    expect(t.playback.muted).toBe(false);
    expect(t.playback.playing).toBe(true);
    expect(t.transport.sent).toEqual([]);
    expect(t.controller.getSnapshot().state).toBe("speaking");
    expect(t.controller.getSnapshot().micMetrics).toMatchObject({ early_mutes: 1, early_mute_reverts: 1 });
    await t.controller.flushEvents();
    expect(t.core.kinds()).not.toContain("barge_in_start");
  });

  it("the 0 ms sample cannot be silent: no audible playback or an unknown onset is flagged anomaly: 1", async () => {
    // Every stop here goes through the fast lane ("dur"): the onset accounting
    // is the same whichever lane confirmed the interruption.
    // (a) the response was armed but nothing audible had started
    const a = await setup();
    a.transport.emit({ type: "response_started", at: 0 });
    a.scheduler.advance(300);
    a.transport.emit({ type: "speech_started", at: 300 });
    a.transport.emit({ type: "owner_transcript", at: 320, text: "dur", final: false });
    await a.controller.flushEvents();
    expect(a.core.events.find((e) => e.kind === "barge_in_start")?.payload).toMatchObject({ playback_stopped_ms: 0, audible: 0, anomaly: 1 });
    expect(a.core.events.find((e) => e.kind === "playback_stopped")?.payload).toMatchObject({ anomaly: 1 });
    // (b) audible, but the provider spoke first and the gate had no onset candidate: the onset is unknown
    const b = await setup();
    b.transport.emit({ type: "response_started", at: 0 });
    b.transport.emit({ type: "audio_started", at: 100 });
    b.playback.activity(120);
    b.scheduler.advance(500);
    b.transport.emit({ type: "speech_started", at: 500 });
    b.transport.emit({ type: "owner_transcript", at: 520, text: "dur", final: false });
    await b.controller.flushEvents();
    expect(b.core.events.find((e) => e.kind === "barge_in_start")?.payload).toMatchObject({ playback_stopped_ms: 0, audible: 1, anomaly: 1, source: 2 });
    // (c) provider-first, but the gate had a candidate under evaluation: that is the measured onset
    const c = await setup();
    c.transport.emit({ type: "response_started", at: 0 });
    c.transport.emit({ type: "audio_started", at: 100 });
    c.playback.activity(120);
    c.scheduler.advance(500);
    c.localSpeech.candidate = { candidateAt: 470, preRollMs: 40 };
    c.transport.emit({ type: "speech_started", at: 500 });
    c.transport.emit({ type: "owner_transcript", at: 520, text: "dur", final: false });
    await c.controller.flushEvents();
    expect(c.core.events.find((e) => e.kind === "barge_in_start")).toMatchObject({
      t_ms: 430,
      payload: { playback_stopped_ms: 70, detect_ms: 30, pre_roll_ms: 40, audible: 1, anomaly: 0 },
    });
    expect(c.core.events.find((e) => e.kind === "mic_speech_start")).toMatchObject({ t_ms: 430, payload: { gate_ms: 30, pre_roll_ms: 40 } });
    assertServerSafe(c.core);
  });
});

describe("§3 end-of-turn → first audio decomposition", () => {
  it("first_audio is the first AUDIBLE sample and carries provider decision, generation and playback", async () => {
    const t = await setup();
    t.transport.emit({ type: "speech_started", at: 0 });
    t.transport.emit({ type: "owner_transcript", at: 900, text: "saat kaç", final: true });
    t.scheduler.advance(1000);
    t.transport.emit({ type: "speech_stopped", at: 1000 });
    t.scheduler.advance(400);
    t.transport.emit({ type: "response_started", at: 1400 });
    t.scheduler.advance(200);
    t.transport.emit({ type: "audio_started", at: 1600 });
    expect(t.controller.getSnapshot().latency.eot_to_first_audio_ms).toBeUndefined();
    t.scheduler.advance(50);
    t.playback.activity(1650);
    await t.controller.flushEvents();
    expect(t.core.events.find((e) => e.kind === "first_audio")).toMatchObject({
      t_ms: 1650,
      payload: { basis: 1, response_created_ms: 400, first_delta_ms: 200, playback_ms: 50 },
    });
    expect(t.controller.getSnapshot().latency.eot_to_first_audio_ms?.value).toBe(650);
    expect(t.core.events.filter((e) => e.kind === "first_audio")).toHaveLength(1);
    assertServerSafe(t.core);
  });

  it("without local audibility inside the bound, the provider's mark is used and flagged basis 0", async () => {
    const t = await setup();
    t.transport.emit({ type: "speech_started", at: 0 });
    t.scheduler.advance(1000);
    t.transport.emit({ type: "speech_stopped", at: 1000 });
    t.scheduler.advance(400);
    t.transport.emit({ type: "response_started", at: 1400 });
    t.scheduler.advance(200);
    t.transport.emit({ type: "audio_started", at: 1600 });
    t.scheduler.advance(299);
    expect(t.controller.getSnapshot().latency.eot_to_first_audio_ms).toBeUndefined();
    t.scheduler.advance(1);
    await t.controller.flushEvents();
    const first = t.core.events.find((e) => e.kind === "first_audio");
    expect(first).toMatchObject({ t_ms: 1600, payload: { basis: 0, response_created_ms: 400, first_delta_ms: 200 } });
    expect(first?.payload).not.toHaveProperty("playback_ms");
    expect(t.controller.getSnapshot().latency.eot_to_first_audio_ms?.value).toBe(600);
  });

  it("end_of_turn carries how far the provider's stop trailed the local gate's close", async () => {
    const t = await setup();
    t.scheduler.advance(100);
    t.localSpeech.speechStart(100, { candidateAt: 150, decidedAt: 170, preRollMs: 50 });
    t.scheduler.advance(300);
    t.transport.emit({ type: "speech_started", at: 400 });
    t.scheduler.advance(500);
    t.localSpeech.speechEnd(900);
    t.scheduler.advance(400);
    t.transport.emit({ type: "speech_stopped", at: 1300 });
    t.scheduler.advance(200);
    await t.controller.flushEvents();
    expect(t.core.events.find((e) => e.kind === "end_of_turn")).toMatchObject({
      t_ms: 1300,
      payload: { hold_ms: 200, hesitation: 0, vad_lag_ms: 400 },
    });
  });
});

describe("§4 input read-back evidence", () => {
  it("the applied input settings and the AGC A/B are reported as measured numbers only", async () => {
    const t = await setup();
    await t.controller.flushEvents();
    const readBack = t.core.events.find((e) => e.kind === "state" && e.payload?.mic_input === 1);
    expect(readBack?.payload).toEqual({
      mic_input: 1,
      aec: 1,
      ns: 1,
      agc: 0,
      sample_rate: 48_000,
      channels: 1,
      input_latency_ms: 10,
      not_honoured: 0,
      agc_bench: 1,
      agc_bench_off_score: 61.5,
      agc_bench_on_score: 44,
      agc_bench_recommended_on: 0,
    });
    // voiceIsolation was null in the read-back: omitted, never a sentinel; the label string was dropped.
    expect(readBack?.payload).not.toHaveProperty("voice_isolation");
    expect(readBack?.payload).not.toHaveProperty("label");
  });
});

describe("§5 calibration evidence semantics", () => {
  it("an uncalibrated session reports no calibration numbers; a measurement has measured: 1 and samples > 0; an attempt is not a calibration", async () => {
    const bare = await setup();
    await bare.controller.disconnect();
    for (const event of bare.core.events) {
      expect(event.payload).not.toHaveProperty("mic_calibration");
      for (const key of ["noise_floor_db", "peak_db", "env_class", "sensitivity_class", "env", "sensitivity"]) {
        expect(event.payload).not.toHaveProperty(key);
      }
    }
    const t = await setup();
    t.localSpeech.emitCalibrationAttempt({ samples: 0, dead_frames: 150, retry: 1 });
    t.localSpeech.emitCalibration({ samples: 84, noise_floor_db: -52.5 });
    t.localSpeech.emitCalibration({ samples: 60, peak_db: undefined });
    await t.controller.flushEvents();
    const attempts = t.core.events.filter((e) => e.kind === "state" && e.payload?.mic_calibration_attempt === 1);
    expect(attempts).toHaveLength(1);
    expect(attempts[0].payload).toMatchObject({ measured: 0, samples: 0, dead_frames: 150, retry: 1 });
    expect(attempts[0].payload).not.toHaveProperty("mic_calibration");
    expect(attempts[0].payload).not.toHaveProperty("noise_floor_db");
    const measured = t.core.events.filter((e) => e.kind === "state" && e.payload?.mic_calibration === 1);
    expect(measured).toHaveLength(2);
    expect(measured[0].payload).toMatchObject({ measured: 1, samples: 84, noise_floor_db: -52.5, env_class: 2, sensitivity_class: 2, peak_db: -30 });
    expect(measured[1].payload).not.toHaveProperty("peak_db"); // no peak observed → absent, not −100
    for (const event of measured) {
      expect(event.payload).not.toHaveProperty("env");
      expect(event.payload).not.toHaveProperty("sensitivity");
      expect(event.payload?.env_class).toBeGreaterThanOrEqual(1);
      expect(event.payload?.sensitivity_class).toBeGreaterThanOrEqual(1);
      for (const value of Object.values(event.payload ?? {})) expect(typeof value).toBe("number");
    }
    expect(t.controller.getSnapshot().micMetrics.env).toBe(2);
  });
});

describe("payload contract", () => {
  /** The latency-metric kinds are numbers-only without exception. */
  const METRIC_KINDS = ["mic_speech_start", "uplink_first_packet", "end_of_turn", "first_audio", "barge_in_start", "playback_stopped", "response_done"];
  /** Tool relay and network kinds carry the identifiers the server relays; everything else on them is a number too. */
  const IDENTIFIER_KEYS: Record<string, string[]> = {
    tool_call: ["call_id", "name"],
    tool_done: ["call_id", "name", "status"],
    preamble_audio_start: ["call_id"],
    speech_resumed: ["call_id"],
    /** ADR-0066: which response finished and how the end was judged; the durations are numbers. */
    audio_done: ["response_id", "basis"],
    network_lost: ["reason"],
    network_restored: [],
  };

  it("every timing-kind payload of a full synthetic session is numbers-only (identifiers excepted where the server relays them)", async () => {
    const t = await setup({
      transport: { uplinkStats: streaming },
    });
    // Turn 1: local start with accounting, RTP probe, provider confirmation, hesitation tail, response with a barge-in.
    t.scheduler.advance(1000);
    t.localSpeech.speechStart(880, { candidateAt: 930, decidedAt: 1000, preRollMs: 50, captureLagMs: 12 });
    await t.pump(40);
    t.scheduler.advance(310);
    t.transport.emit({ type: "speech_started", at: 1350 });
    t.transport.emit({ type: "owner_transcript", at: 1800, text: "raporu oku şey", final: true });
    t.scheduler.advance(650);
    t.transport.emit({ type: "speech_stopped", at: 2000 });
    t.scheduler.advance(900); // filler hold elapses → end_of_turn
    t.transport.emit({ type: "response_started", at: 3000 });
    t.scheduler.advance(200);
    t.transport.emit({ type: "audio_started", at: 3200 });
    t.scheduler.advance(30);
    t.playback.activity(3230);
    t.scheduler.advance(500); // now = 3730
    t.localSpeech.evidence(3730, 3690);
    t.scheduler.advance(60);
    t.localSpeech.speechStart(3640, { candidateAt: 3690, decidedAt: 3790, preRollMs: 50, captureLagMs: 9, duringPlayback: true });
    await t.pump(40);
    t.transport.emit({ type: "owner_transcript", at: 3800, text: "dur", final: false }); // fast lane
    t.transport.emit({ type: "speech_started", at: 4100 });
    t.transport.emit({ type: "response_cancelled", at: 4110 });
    // Turn 2: provider-first start inside a hesitation hold → premature response cancelled with hesitation_resume.
    t.transport.emit({ type: "owner_transcript", at: 4500, text: "yani", final: true });
    t.scheduler.advance(400);
    t.transport.emit({ type: "speech_stopped", at: 4500 });
    t.scheduler.advance(300);
    t.transport.emit({ type: "response_started", at: 4800 });
    t.scheduler.advance(100);
    t.transport.emit({ type: "speech_started", at: 4900 });
    t.transport.emit({ type: "response_cancelled", at: 4910 });
    t.transport.emit({ type: "owner_transcript", at: 5300, text: "devam et", final: true });
    t.scheduler.advance(500);
    t.transport.emit({ type: "speech_stopped", at: 5400 });
    t.scheduler.advance(200);
    t.transport.emit({ type: "response_started", at: 5600 });
    t.transport.emit({ type: "audio_started", at: 5800 });
    t.scheduler.advance(300); // provider fallback for first_audio
    t.transport.emit({ type: "response_done", at: 7000 });
    // A tool call, a network drop and a restore.
    t.transport.emit({ type: "tool_call", at: 7100, callId: "call-1", name: "clock.now", arguments: {} });
    await tick(8);
    t.transport.emit({ type: "disconnected", at: 7200, reason: "peer_failed" });
    await tick(8);
    await t.controller.disconnect();

    const timing = t.core.events.filter((e) => !(STATE_EVENT_KINDS as readonly string[]).includes(e.kind));
    const kindsSeen = new Set<string>(timing.map((e) => e.kind));
    for (const kind of [...METRIC_KINDS, "tool_call", "tool_done", "network_lost", "network_restored"]) {
      expect(kindsSeen.has(kind), `session emitted ${kind}`).toBe(true);
    }
    expect(timing.some((e) => e.kind === "barge_in_start" && e.payload?.hesitation_resume === 1)).toBe(true);
    expect(timing.some((e) => e.kind === "end_of_turn" && e.payload?.hesitation === 1)).toBe(true);
    for (const event of timing) {
      const allowed = IDENTIFIER_KEYS[event.kind] ?? [];
      if (!METRIC_KINDS.includes(event.kind)) expect(event.kind in IDENTIFIER_KEYS, `known timing kind ${event.kind}`).toBe(true);
      for (const [key, value] of Object.entries(event.payload ?? {})) {
        expect(isForbiddenKey(key), key).toBe(false);
        if (allowed.includes(key)) continue;
        expect(typeof value, `${event.kind}.${key}`).toBe("number");
        expect(Number.isFinite(value as number), `${event.kind}.${key}`).toBe(true);
      }
    }
  });

  it("every ADR-0047 key passes the server's forbidden-key rule; the rejected spelling is pinned", () => {
    const keys = [
      "source", "gate_ms", "capture_lag_ms", "pre_roll_ms",
      "basis", "rtp_ms", "provider_ms",
      "unmatched", "provider_first", "false_start", "false_barge_in", "network_lost", "superseded", "session_end", "hesitation",
      "playback_stopped_ms", "detect_ms", "stop_command_ms", "gain_zero_ms", "output_latency_ms", "early_mute", "audible", "anomaly", "hesitation_resume",
      "response_created_ms", "first_delta_ms", "playback_ms", "vad_lag_ms", "hold_ms", "hesitation",
      "mic_calibration", "mic_calibration_attempt", "measured", "samples", "dead_frames", "retry", "contaminated",
      "env_class", "sensitivity_class", "noise_floor_db", "stationary_db", "spread_db", "peak_db", "clip_risk", "hum_ratio",
      "open_margin_db", "min_onset_ms", "hang_ms", "echo_margin_db", "echo_residual_db", "trigger",
      "mic_input", "aec", "ns", "agc", "voice_isolation", "sample_rate", "channels", "input_latency_ms", "not_honoured",
      "agc_bench", "agc_bench_off_score", "agc_bench_on_score", "agc_bench_off_floor_db", "agc_bench_on_floor_db", "agc_bench_recommended_on",
      "mic_metrics", "false_starts", "false_barge_ins", "false_turns", "confirmed_turns", "early_mutes", "early_mute_reverts",
      "gate_opens", "gated_out", "click_rejects", "speech_ms", "calibrations", "evidence_events", "evidence_lost",
    ];
    for (const key of keys) expect(isForbiddenKey(key), key).toBe(false);
    // "stop_cmd_ms" normalizes to "stopcmdms", which contains "pcm": the server refuses it with a 422.
    expect(isForbiddenKey("stop_cmd_ms")).toBe(true);
  });
});
