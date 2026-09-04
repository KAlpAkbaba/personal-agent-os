/**
 * Two-stage interruption while the assistant speaks (owner-observed defect,
 * 2026-09-04, real realtime session over WebRTC on the owner's very sensitive
 * K66 microphone: other people talking in the room cut the assistant off).
 * The provider no longer cancels on speech (`interrupt_response = false`);
 * the client owns interruption:
 *
 *  A. fast control lane — "dur" (and the other control phrases) in any owner
 *     transcript stops IMMEDIATELY through the proven barge-in path, even
 *     after the conversational lane rejected the same speech;
 *  B. conversational lane — a speech onset only mutes reversibly and must earn
 *     its cancel inside BARGE_IN_CONFIRM_WINDOW_MS: a stable onset
 *     (≥ BARGE_IN_STABLE_ONSET_MS), near-field confidence from the local gate,
 *     a plausible word; otherwise playback resumes and nothing was cancelled.
 *
 * Each test fails on the defect it names. The metrics are the numbers the
 * benchmark will judge the owner's rerun by.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { deriveGateParameters } from "../../app/lib/voice/calibration";
import { isForbiddenKey } from "../../app/lib/voice/contract";
import { VoiceSessionController } from "../../app/lib/voice/controller";
import { EventReporter, type ReportInput } from "../../app/lib/voice/events";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";
import { SpeechGate } from "../../app/lib/voice/gate";
import {
  BARGE_IN_CONFIRM_WINDOW_MS,
  BARGE_IN_STABLE_ONSET_MS,
  CONTROL_PHRASES,
  FALSE_INTERRUPTION_WINDOW_MS,
  NEAR_FIELD_MIN_MARGIN_DB,
  NEAR_FIELD_MIN_SPECTRAL,
  findControlPhrase,
  hasPlausibleWord,
  isNearField,
  strongerLevel,
} from "../../app/lib/voice/interruption";
import type { OnsetLevel } from "../../app/lib/voice/ports";
import { frames, ms, overlay, speechLike, whiteNoise } from "./signals";
import { NoiseFloorCalibrator } from "../../app/lib/voice/calibration";

/**
 * The critical barge-in sequence, without the decision lane's own bookkeeping.
 * The two-lane policy logs WHY it interrupted (`barge_in.accepted@…`,
 * `barge_in.dropped`, `barge_in.explicit_stop`) before it acts; the ORDER that
 * must never change is the acting part (ADR-0040 §3), so the assertions below
 * read that and check the decision separately.
 */
const acted = (log: readonly string[]): string[] => log.filter((line) => !line.startsWith("barge_in."));


const tick = async (rounds = 4): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

/** What the gate reports for the owner at normal speaking level, one metre from the K66. */
const NEAR_FIELD_OWNER: OnsetLevel = { marginDb: 14, spectralScore: 0.8, frames: 12 };
/** Someone across the room: above the calibrated margin, but only just, and less speech-band energy. */
const DISTANT_VOICE: OnsetLevel = { marginDb: 2, spectralScore: 0.4, frames: 20 };
const OWNER_WORDS = "bugün neler yaptın";

/** The playback fake logs `playback.*`; every reporter call is logged as `report.<kind>` next to it. */
async function setup(options: { localSpeech?: FakeSpeechDetector | null } = {}) {
  const log: string[] = [];
  const original = EventReporter.prototype.report;
  vi.spyOn(EventReporter.prototype, "report").mockImplementation(function (this: EventReporter, input: ReportInput) {
    log.push(`report.${input.kind}`);
    return original.call(this, input);
  });
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
  const transports: FakeTransport[] = [];
  const playback = new FakePlayback(scheduler.now, (op) => log.push(op));
  const localSpeech = options.localSpeech === undefined ? new FakeSpeechDetector() : options.localSpeech;
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => {
      const transport = new FakeTransport({ log: (op) => log.push(op), now: scheduler.now });
      transports.push(transport);
      return transport;
    },
    playback,
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: localSpeech ?? undefined,
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    localSpeechGraceMs: 700,
    log: (op) => log.push(op),
  });
  await controller.connect();
  await tick();
  return {
    controller,
    core,
    scheduler,
    playback,
    localSpeech,
    log,
    get transport() {
      return transports[transports.length - 1];
    },
    events: (kind: string) => core.events.filter((e) => e.kind === kind),
    metrics: () => controller.getSnapshot().micMetrics,
  };
}

type T = Awaited<ReturnType<typeof setup>>;

/** A response that is audible at `at + 300` and still speaking when the script continues. */
function speakingResponse(t: T, at: number, text = "İkinci madde: dağıtım gecikti."): void {
  t.scheduler.advance(at - t.scheduler.now());
  t.transport.emit({ type: "response_started", at });
  t.transport.emit({ type: "response_text", at: at + 100, text, final: false });
  t.scheduler.advance(300);
  t.transport.emit({ type: "audio_started", at: at + 300 });
  t.playback.activity(at + 300);
  expect(t.playback.playing).toBe(true);
}

/** Someone starts talking at 1480 while the assistant speaks (the script is at 1500). */
function onsetDuringPlayback(t: T, level: OnsetLevel | null): void {
  speakingResponse(t, 1000);
  t.scheduler.advance(200); // now = 1500
  if (t.localSpeech) t.localSpeech.level = level;
  t.transport.emit({ type: "speech_started", at: 1480 });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("control phrases (token match, Turkish-safe folding)", () => {
  it("matches the phrase list on tokens, never on substrings", () => {
    expect(CONTROL_PHRASES).toEqual(["dur", "durdur", "bekle", "sus", "kes", "yeter", "bir dakika", "tamam dur"]);
    expect(findControlPhrase("dur")).toBe("dur");
    expect(findControlPhrase("Dur!")).toBe("dur");
    expect(findControlPhrase("DUR.")).toBe("dur");
    expect(findControlPhrase("raporu durdur")).toBe("durdur");
    expect(findControlPhrase("İyi, bekle")).toBe("bekle");
    expect(findControlPhrase("bir dakika ya")).toBe("bir dakika");
    expect(findControlPhrase("tamam dur")).toBe("dur"); // "dur" alone already stops
    expect(findControlPhrase("durum kötü")).toBeNull(); // "durum" is not "dur"
    expect(findControlPhrase("dakika")).toBeNull(); // half of a multi-word phrase
    expect(findControlPhrase("yeterince")).toBeNull();
    expect(findControlPhrase("susuz")).toBeNull();
    expect(findControlPhrase("kesinlikle")).toBeNull();
    expect(findControlPhrase("")).toBeNull();
    expect(findControlPhrase("   ")).toBeNull();
  });

  it("a plausible word is any non-filler token of two letters or more", () => {
    expect(hasPlausibleWord("bugün")).toBe(true);
    expect(hasPlausibleWord("Şey, bugün")).toBe(true);
    expect(hasPlausibleWord("şey")).toBe(false);
    expect(hasPlausibleWord("yani hani")).toBe(false);
    expect(hasPlausibleWord("ııı")).toBe(false);
    expect(hasPlausibleWord("e")).toBe(false);
    expect(hasPlausibleWord("a")).toBe(false);
    expect(hasPlausibleWord("...")).toBe(false);
    expect(hasPlausibleWord("")).toBe(false);
  });
});

describe("near-field confidence rule", () => {
  it("passes on level AND spectrum; a gate that saw no candidate is not near-field; no gate at all is unknown", () => {
    expect(isNearField(NEAR_FIELD_OWNER)).toBe(true);
    expect(isNearField(DISTANT_VOICE)).toBe(false);
    expect(isNearField({ marginDb: NEAR_FIELD_MIN_MARGIN_DB, spectralScore: NEAR_FIELD_MIN_SPECTRAL, frames: 1 })).toBe(true);
    expect(isNearField({ marginDb: NEAR_FIELD_MIN_MARGIN_DB - 0.1, spectralScore: 0.9, frames: 5 })).toBe(false);
    expect(isNearField({ marginDb: 20, spectralScore: NEAR_FIELD_MIN_SPECTRAL - 0.01, frames: 5 })).toBe(false);
    expect(isNearField(null)).toBe(false);
    expect(isNearField(undefined)).toBeUndefined();
    expect(strongerLevel(null, null)).toBeNull();
    expect(strongerLevel(DISTANT_VOICE, NEAR_FIELD_OWNER)).toEqual({ marginDb: 14, spectralScore: 0.8, frames: 20 });
    expect(strongerLevel(NEAR_FIELD_OWNER, null)).toBe(NEAR_FIELD_OWNER);
  });

  it("the real gate measures it: the owner at −22 dBFS during playback is near-field, a voice at −46 dBFS is not", () => {
    const floor = whiteNoise(ms(4000), -60, 3);
    const calibrator = new NoiseFloorCalibrator({ durationMs: 1500 });
    for (const f of frames(floor)) if (calibrator.push(f.features)) break;
    const calibration = calibrator.result();
    expect(calibration).not.toBeNull();
    const params = deriveGateParameters({ calibration, mode: "auto" });
    const levelOf = (signal: Float32Array): OnsetLevel | null => {
      const gate = new SpeechGate(params);
      let best: OnsetLevel | null = null;
      for (const f of frames(signal)) {
        gate.update(f.features, f.now, true);
        best = strongerLevel(best, gate.onsetLevel());
      }
      return best;
    };
    const owner = levelOf(overlay(floor, speechLike(ms(1500), -22), ms(800)));
    expect(owner).not.toBeNull();
    expect(isNearField(owner)).toBe(true);
    expect(owner!.marginDb).toBeGreaterThanOrEqual(NEAR_FIELD_MIN_MARGIN_DB);
    expect(owner!.spectralScore).toBeGreaterThanOrEqual(NEAR_FIELD_MIN_SPECTRAL);
    // Below the playback threshold (floor + open margin + playback margin): no candidate, or a margin under the rule.
    const distant = levelOf(overlay(floor, speechLike(ms(1500), -46), ms(800)));
    expect(isNearField(distant)).toBe(false);
    // Idle floor: nothing to measure.
    expect(levelOf(floor)).toBeNull();
    // The accumulator resets with the gate.
    const gate = new SpeechGate(params);
    for (const f of frames(overlay(floor, speechLike(ms(600), -22), ms(800)))) gate.update(f.features, f.now, true);
    gate.resetState();
    expect(gate.onsetLevel()).toBeNull();
  });
});

describe("A. fast control lane", () => {
  it("(1) 'dur' in a provisional transcript during playback stops at once, in the proven order; 'durum' does not", async () => {
    const t = await setup();
    onsetDuringPlayback(t, null); // the level does not matter for the fast lane
    expect(t.transport.sent).toEqual([]);
    expect(t.playback.muted).toBe(true); // reversible early mute at the onset
    t.transport.emit({ type: "owner_transcript", at: 1510, text: "durum", final: false });
    expect(t.transport.sent).toEqual([]);
    expect(t.playback.playing).toBe(true);

    t.log.length = 0;
    t.transport.emit({ type: "owner_transcript", at: 1520, text: " dur", final: false });
    expect(acted(t.log).slice(0, 7)).toEqual([
      "playback.stop",
      "fake.cancelResponse",
      "transport.cancel",
      "report.spoken",
      "report.barge_in_start",
      "report.playback_stopped",
      "report.barge_in",
    ]);
    expect(t.playback.playing).toBe(false);
    expect(t.transport.sent).toEqual(["cancel"]);
    expect(t.controller.getSnapshot().state).toBe("interrupted");
    expect(t.metrics()).toMatchObject({
      speech_detected: 1,
      potential_barge_in: 1,
      explicit_stop_command: 1,
      accepted_owner_interruption: 0,
      rejected_background_speech: 0,
      false_interruption: 0,
    });
    await t.controller.flushEvents();
    const kinds = t.core.kinds();
    expect(kinds.indexOf("spoken")).toBeLessThan(kinds.indexOf("barge_in_start"));
    expect(kinds.indexOf("barge_in_start")).toBeLessThan(kinds.indexOf("playback_stopped"));
    const [barge] = t.events("barge_in_start");
    expect(barge).toMatchObject({
      t_ms: 1480,
      turn: 1,
      payload: { lane: 1, provider_cancel: 1, early_mute: 1, playback_stopped_ms: 20, source: 2 },
    });
    expect(barge.payload).toHaveProperty("stop_command_ms");
    // A final transcript repeating the phrase stops nothing twice.
    t.transport.emit({ type: "response_cancelled", at: 1600 });
    t.transport.emit({ type: "owner_transcript", at: 1900, text: "Dur.", final: true });
    expect(t.transport.sent).toEqual(["cancel"]);
    expect(t.metrics().explicit_stop_command).toBe(1);
  });

  it("every control phrase stops, with punctuation and Turkish casing", async () => {
    for (const phrase of ["Bekle!", "SUS", "kes", "Yeter,", "bir dakika", "Tamam dur", "İyi durdur"]) {
      const t = await setup();
      onsetDuringPlayback(t, null);
      t.transport.emit({ type: "owner_transcript", at: 1520, text: phrase, final: false });
      expect(t.transport.sent, phrase).toEqual(["cancel"]);
      expect(t.metrics().explicit_stop_command, phrase).toBe(1);
      vi.restoreAllMocks();
    }
  });

  it("a control phrase when nothing is playing stops nothing (the utterance still reaches Cloud Core)", async () => {
    const t = await setup();
    t.transport.emit({ type: "speech_started", at: 100 });
    t.transport.emit({ type: "owner_transcript", at: 400, text: "dur", final: true });
    expect(t.transport.sent).toEqual([]);
    expect(t.metrics()).toMatchObject({ speech_detected: 1, potential_barge_in: 0, explicit_stop_command: 0 });
    await t.controller.flushEvents();
    expect(t.events("utterance")).toHaveLength(1);
    expect(t.events("barge_in_start")).toHaveLength(0);
  });
});

describe("B. conversational barge-in lane", () => {
  it("(2) a brief background burst: early mute, then revert on speech_stopped — no cancel, no barge_in_start, rejected_background_speech 1", async () => {
    const t = await setup();
    onsetDuringPlayback(t, null); // the gate saw no candidate: too quiet / distant
    expect(t.log).toContain("playback.mute");
    expect(t.playback.muted).toBe(true);
    expect(t.playback.playing).toBe(true);
    expect(t.controller.getSnapshot().state).toBe("speaking");
    expect(t.metrics()).toMatchObject({ speech_detected: 1, potential_barge_in: 1, early_mutes: 1 });

    t.scheduler.advance(150); // now = 1650
    t.transport.emit({ type: "speech_stopped", at: 1630 }); // 150 ms of speech
    expect(t.log).toContain("playback.unmute");
    expect(t.playback.muted).toBe(false);
    expect(t.playback.playing).toBe(true);
    expect(t.transport.sent).toEqual([]);
    expect(t.log).not.toContain("playback.stop");
    expect(t.log).not.toContain("transport.cancel");
    expect(t.controller.getSnapshot().state).toBe("speaking");
    expect(t.metrics()).toMatchObject({
      rejected_background_speech: 1,
      accepted_owner_interruption: 0,
      explicit_stop_command: 0,
      early_mutes: 1,
      early_mute_reverts: 1,
    });
    await t.controller.flushEvents();
    expect(t.events("barge_in_start")).toHaveLength(0);
    expect(t.events("playback_stopped")).toHaveLength(0);
    const rejected = t.events("state").filter((e) => e.payload?.background_speech_rejected === 1);
    expect(rejected).toHaveLength(1);
    expect(rejected[0]).toMatchObject({
      turn: 1,
      payload: { background_speech_rejected: 1, short_burst: 1, stable: 0, near_field: 0, plausible_word: 0, onset_ms: 170, source: 2 },
    });
    for (const [key, value] of Object.entries(rejected[0].payload ?? {})) {
      expect(isForbiddenKey(key), key).toBe(false);
      expect(typeof value, key).toBe("number");
    }
    // The assistant simply goes on; its completion is a normal one.
    t.scheduler.advance(300);
    t.transport.emit({ type: "response_done", at: 2000 });
    expect(t.controller.getSnapshot().state).toBe("listening");
  });

  it("(3) distant continuous speech: long onset with words but a level below near-field confidence → rejected when the window elapses", async () => {
    const t = await setup();
    onsetDuringPlayback(t, DISTANT_VOICE);
    t.scheduler.advance(200);
    t.transport.emit({ type: "owner_transcript", at: 1700, text: OWNER_WORDS, final: false });
    t.scheduler.advance(BARGE_IN_STABLE_ONSET_MS); // well past stability
    expect(t.transport.sent).toEqual([]);
    expect(t.playback.muted).toBe(true); // still waiting inside the window
    t.scheduler.advance(BARGE_IN_CONFIRM_WINDOW_MS); // the window (from 1500) is over
    expect(t.playback.muted).toBe(false);
    expect(t.playback.playing).toBe(true);
    expect(t.transport.sent).toEqual([]);
    expect(t.metrics()).toMatchObject({ rejected_background_speech: 1, accepted_owner_interruption: 0 });
    await t.controller.flushEvents();
    expect(t.events("barge_in_start")).toHaveLength(0);
    const [rejected] = t.events("state").filter((e) => e.payload?.background_speech_rejected === 1);
    expect(rejected).toMatchObject({
      t_ms: 1500 + BARGE_IN_CONFIRM_WINDOW_MS,
      payload: { window_elapsed: 1, stable: 1, near_field: 0, plausible_word: 1, margin_db: 2, spectral: 0.4 },
    });
  });

  it("(3') near-field level but no plausible transcript (fillers only) → rejected too", async () => {
    const t = await setup();
    onsetDuringPlayback(t, NEAR_FIELD_OWNER);
    t.transport.emit({ type: "owner_transcript", at: 1600, text: "ııı şey", final: false });
    t.scheduler.advance(BARGE_IN_CONFIRM_WINDOW_MS + 10);
    expect(t.transport.sent).toEqual([]);
    expect(t.playback.muted).toBe(false);
    expect(t.metrics()).toMatchObject({ rejected_background_speech: 1, accepted_owner_interruption: 0 });
    await t.controller.flushEvents();
    const [rejected] = t.events("state").filter((e) => e.payload?.background_speech_rejected === 1);
    expect(rejected.payload).toMatchObject({ window_elapsed: 1, stable: 1, near_field: 1, plausible_word: 0 });
  });

  it("(4) genuine owner speech: stable onset + near-field level + a provisional word → confirmed inside the window, cancel sent, accepted_owner_interruption 1", async () => {
    const t = await setup();
    onsetDuringPlayback(t, NEAR_FIELD_OWNER);
    t.scheduler.advance(200); // now = 1700
    t.transport.emit({ type: "owner_transcript", at: 1700, text: OWNER_WORDS, final: false });
    expect(t.transport.sent).toEqual([]); // 220 ms of speech: not yet stable
    t.scheduler.advance(129); // now = 1829
    expect(t.transport.sent).toEqual([]);
    t.log.length = 0;
    t.scheduler.advance(1); // now = 1830 = onset 1480 + 350
    expect(acted(t.log).slice(0, 7)).toEqual([
      "playback.stop",
      "fake.cancelResponse",
      "transport.cancel",
      "report.spoken",
      "report.barge_in_start",
      "report.playback_stopped",
      "report.barge_in",
    ]);
    expect(t.transport.sent).toEqual(["cancel"]);
    expect(t.playback.playing).toBe(false);
    expect(t.controller.getSnapshot().state).toBe("interrupted");
    expect(t.metrics()).toMatchObject({
      speech_detected: 1,
      potential_barge_in: 1,
      accepted_owner_interruption: 1,
      rejected_background_speech: 0,
      explicit_stop_command: 0,
      early_mutes: 1,
      early_mute_reverts: 0,
    });
    await t.controller.flushEvents();
    const [barge] = t.events("barge_in_start");
    expect(barge).toMatchObject({
      t_ms: 1480,
      turn: 1,
      payload: {
        lane: 2,
        confirm_ms: 350,
        near_field: 1,
        margin_db: 14,
        spectral: 0.8,
        early_mute: 1,
        playback_stopped_ms: 20, // the owner heard silence at the mute (1500), not at the confirmation
        provider_cancel: 1,
        stop_command_ms: 0,
        source: 2,
      },
    });
    expect(t.events("playback_stopped")[0]).toMatchObject({ t_ms: 1500, payload: { early_mute: 1 } });
    expect(t.controller.getSnapshot().latency.barge_in_to_stop_ms?.value).toBe(20);
    expect(t.controller.getSnapshot().latencyDetail.barge_in).toMatchObject({ lane: 2, early_mute: 1, provider_cancel: 1 });
    // The owner then says something: no false interruption.
    t.transport.emit({ type: "response_cancelled", at: 1900 });
    t.transport.emit({ type: "owner_transcript", at: 2400, text: OWNER_WORDS, final: true });
    t.scheduler.advance(FALSE_INTERRUPTION_WINDOW_MS + 100);
    expect(t.metrics().false_interruption).toBe(0);
  });

  it("(5) an accepted interruption followed by no final owner utterance within 3 s is a false_interruption", async () => {
    const t = await setup();
    onsetDuringPlayback(t, NEAR_FIELD_OWNER);
    t.transport.emit({ type: "owner_transcript", at: 1600, text: OWNER_WORDS, final: false });
    t.scheduler.advance(330); // now = 1830: confirmed
    expect(t.transport.sent).toEqual(["cancel"]);
    t.transport.emit({ type: "response_cancelled", at: 1900 });
    t.scheduler.advance(300);
    t.transport.emit({ type: "speech_stopped", at: 2200 });
    t.scheduler.advance(FALSE_INTERRUPTION_WINDOW_MS - 300 - 1); // 4829: one ms short of the watch
    expect(t.metrics().false_interruption).toBe(0);
    t.scheduler.advance(1); // 4830 = confirmation 1830 + 3000
    expect(t.metrics()).toMatchObject({ accepted_owner_interruption: 1, false_interruption: 1 });
    await t.controller.flushEvents();
    const marks = t.events("state").filter((e) => e.payload?.false_interruption === 1);
    expect(marks).toHaveLength(1);
    expect(marks[0]).toMatchObject({ t_ms: 4830, turn: 1, payload: { false_interruption: 1, wait_ms: FALSE_INTERRUPTION_WINDOW_MS } });
    for (const value of Object.values(marks[0].payload ?? {})) expect(typeof value).toBe("number");
  });

  it("(6) the fast lane still fires after the conversational lane rejected the same speech", async () => {
    const t = await setup();
    onsetDuringPlayback(t, null);
    t.scheduler.advance(150);
    t.transport.emit({ type: "speech_stopped", at: 1630 }); // a burst: rejected, playback resumed
    expect(t.playback.muted).toBe(false);
    expect(t.metrics().rejected_background_speech).toBe(1);
    // The transcription of that burst lands later: it was the owner's "dur".
    t.scheduler.advance(250); // now = 1900
    t.log.length = 0;
    t.transport.emit({ type: "owner_transcript", at: 1900, text: "Dur", final: true });
    // the final transcript is reported as an utterance too; the acting order among the
    // three critical steps is what must hold
    const critical = acted(t.log).filter((line) => line !== "report.utterance");
    expect(critical.slice(0, 3)).toEqual(["playback.stop", "fake.cancelResponse", "transport.cancel"]);
    expect(t.transport.sent).toEqual(["cancel"]);
    expect(t.playback.playing).toBe(false);
    // The owner had already stopped speaking when the transcription landed, so the FSM is
    // back at LISTENING: the point of this case is that the stop still happened.
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.metrics()).toMatchObject({ rejected_background_speech: 1, explicit_stop_command: 1, accepted_owner_interruption: 0 });
    await t.controller.flushEvents();
    const [barge] = t.events("barge_in_start");
    // The onset is the turn's; the stop latency is honest about the resumed playback in between.
    expect(barge).toMatchObject({ t_ms: 1480, turn: 1, payload: { lane: 1, after_reject: 1, early_mute: 0, playback_stopped_ms: 420 } });
    t.transport.emit({ type: "response_cancelled", at: 1950 });
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.controller.getSnapshot().lastError).toBeNull();
  });

  it("the mute stays through the window even when the response completes underneath it; a rejection then resumes the draining audio", async () => {
    const t = await setup();
    onsetDuringPlayback(t, DISTANT_VOICE);
    t.transport.emit({ type: "response_done", at: 1550 });
    expect(t.playback.muted).toBe(true); // lane B owns the mute (draining audio)
    expect(t.playback.playing).toBe(true);
    t.scheduler.advance(BARGE_IN_CONFIRM_WINDOW_MS);
    expect(t.playback.muted).toBe(false);
    expect(t.transport.sent).toEqual([]);
    expect(t.metrics()).toMatchObject({ rejected_background_speech: 1, early_mute_reverts: 1 });
  });

  it("a confirmation over draining audio stops locally and cancels nothing (provider_cancel 0)", async () => {
    const t = await setup();
    onsetDuringPlayback(t, NEAR_FIELD_OWNER);
    t.transport.emit({ type: "response_done", at: 1550 });
    t.transport.emit({ type: "owner_transcript", at: 1600, text: OWNER_WORDS, final: false });
    t.scheduler.advance(330);
    expect(t.playback.playing).toBe(false);
    expect(t.transport.sent).toEqual([]);
    expect(t.log).toContain("transport.cancel_skipped");
    await t.controller.flushEvents();
    expect(t.events("barge_in_start")[0].payload).toMatchObject({ lane: 2, provider_cancel: 0 });
    expect(t.events("error")).toHaveLength(0);
  });

  it("without a local gate the near-field rule is unknown and the other two pieces of evidence decide", async () => {
    const t = await setup({ localSpeech: null });
    onsetDuringPlayback(t, null);
    t.transport.emit({ type: "owner_transcript", at: 1600, text: OWNER_WORDS, final: false });
    t.scheduler.advance(330);
    expect(t.transport.sent).toEqual(["cancel"]);
    await t.controller.flushEvents();
    const [barge] = t.events("barge_in_start");
    expect(barge.payload).toMatchObject({ lane: 2 });
    expect(barge.payload).not.toHaveProperty("near_field");
  });

  it("a hesitation resume against a premature response is still cancelled at once (the guard is untouched)", async () => {
    const t = await setup();
    t.transport.emit({ type: "speech_started", at: 100 });
    t.transport.emit({ type: "owner_transcript", at: 400, text: "raporun ikinci maddesini şey", final: true });
    t.scheduler.advance(400);
    t.transport.emit({ type: "speech_stopped", at: 500 }); // filler tail: 900 ms hold
    t.scheduler.advance(300);
    t.transport.emit({ type: "response_started", at: 800 });
    t.scheduler.advance(100);
    t.transport.emit({ type: "speech_started", at: 900 });
    expect(t.transport.sent).toEqual(["cancel"]);
    expect(t.controller.getSnapshot().turn).toBe(1);
    await t.controller.flushEvents();
    expect(t.events("barge_in_start")[0].payload).toMatchObject({ hesitation_resume: 1, lane: 0 });
    expect(t.metrics()).toMatchObject({ potential_barge_in: 0, accepted_owner_interruption: 0, explicit_stop_command: 0 });
  });
});

describe("C. metrics", () => {
  it("(7) the mic_metrics state report at close carries all six counters, numbers only, server-safe keys", async () => {
    const t = await setup();
    // 1. explicit stop
    onsetDuringPlayback(t, null);
    t.transport.emit({ type: "owner_transcript", at: 1520, text: "dur", final: false });
    t.transport.emit({ type: "response_cancelled", at: 1600 });
    t.transport.emit({ type: "owner_transcript", at: 1700, text: "dur", final: true });
    t.scheduler.advance(300);
    t.transport.emit({ type: "speech_stopped", at: 1800 });
    t.scheduler.advance(300);
    // 2. a background burst, rejected
    speakingResponse(t, 3000);
    t.scheduler.advance(200);
    t.transport.emit({ type: "speech_started", at: 3480 });
    t.scheduler.advance(100);
    t.transport.emit({ type: "speech_stopped", at: 3580 });
    t.scheduler.advance(300);
    t.transport.emit({ type: "response_done", at: 3900 });
    // 3. a confirmed interruption with nothing said afterwards
    speakingResponse(t, 5000);
    t.scheduler.advance(200);
    t.localSpeech!.level = NEAR_FIELD_OWNER;
    t.transport.emit({ type: "speech_started", at: 5480 });
    t.transport.emit({ type: "owner_transcript", at: 5600, text: OWNER_WORDS, final: false });
    t.scheduler.advance(330);
    t.transport.emit({ type: "response_cancelled", at: 5900 });
    t.transport.emit({ type: "speech_stopped", at: 6000 });
    t.scheduler.advance(FALSE_INTERRUPTION_WINDOW_MS + 500);

    const expected = {
      speech_detected: 3,
      potential_barge_in: 3,
      accepted_owner_interruption: 1,
      rejected_background_speech: 1,
      explicit_stop_command: 1,
      false_interruption: 1,
    };
    expect(t.metrics()).toMatchObject(expected);
    await t.controller.disconnect();
    const closing = t.events("state").filter((e) => e.payload?.mic_metrics === 1 && e.payload?.session_end === 1);
    expect(closing).toHaveLength(1);
    expect(closing[0].payload).toMatchObject({ ...expected, early_mutes: 3, early_mute_reverts: 1 });
    for (const event of t.events("state").filter((e) => e.payload?.mic_metrics === 1)) {
      for (const key of Object.keys(expected)) expect(event.payload, key).toHaveProperty(key);
      for (const [key, value] of Object.entries(event.payload ?? {})) {
        expect(isForbiddenKey(key), key).toBe(false);
        expect(typeof value, key).toBe("number");
      }
    }
  });

  it("every new payload key passes the server's forbidden-key rule", () => {
    const keys = [
      "speech_detected", "potential_barge_in", "accepted_owner_interruption", "rejected_background_speech", "explicit_stop_command", "false_interruption",
      "background_speech_rejected", "onset_ms", "stable", "near_field", "plausible_word", "margin_db", "spectral",
      "short_burst", "window_elapsed", "superseded", "false_start", "wait_ms", "lane", "confirm_ms", "after_reject",
    ];
    for (const key of keys) expect(isForbiddenKey(key), key).toBe(false);
  });
});
