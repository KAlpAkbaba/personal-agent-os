import { describe, expect, it } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { isForbiddenKey } from "../../app/lib/voice/contract";
import { VoiceSessionController } from "../../app/lib/voice/controller";
import { numbersOnly } from "../../app/lib/voice/events";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";

const tick = async (rounds = 4): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

async function setup(voice?: string) {
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
      const transport = new FakeTransport({ log: (op) => log.push(op), now: scheduler.now });
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
    log: (op) => log.push(op),
  });
  await controller.connect(voice ? { voice } : {});
  await tick();
  return {
    controller,
    core,
    scheduler,
    playback,
    microphone,
    localSpeech,
    log,
    get transport() {
      return transports[transports.length - 1];
    },
  };
}

/** Every payload in every /events request, for the forbidden-key sweep. */
function payloads(core: FakeCloudCore): Array<Record<string, unknown>> {
  return core.events.filter((e) => e.payload).map((e) => e.payload as Record<string, unknown>);
}

describe("noise qualification metrics (ADR-0044 §7)", () => {
  it("a local gate opening the provider never confirms is a false start; the turn is released", async () => {
    const t = await setup();
    t.scheduler.advance(500);
    t.localSpeech.speechStart(500);
    expect(t.controller.getSnapshot().turn).toBe(1);
    t.scheduler.advance(200);
    t.localSpeech.speechEnd(700);
    t.scheduler.advance(699);
    expect(t.controller.getSnapshot().micMetrics.false_starts).toBe(0);
    t.scheduler.advance(1);
    expect(t.controller.getSnapshot().micMetrics.false_starts).toBe(1);
    expect(t.log).toContain("gate.false_start");
    // The next real speech (provider VAD) is a NEW turn, not the stale one's uplink ack.
    t.scheduler.advance(1000);
    t.transport.emit({ type: "speech_started", at: 2400 });
    expect(t.controller.getSnapshot().turn).toBe(2);
    await t.controller.flushEvents();
    const starts = t.core.events.filter((e) => e.kind === "mic_speech_start");
    expect(starts.map((e) => e.payload?.source)).toEqual(["local", "provider"]);
    expect(t.core.events.filter((e) => e.kind === "uplink_first_packet")).toHaveLength(0);
    const metric = t.core.events.find((e) => e.kind === "state" && e.payload?.mic_metrics === 1);
    expect(metric).toMatchObject({ t_ms: 700, payload: { false_start: 1, false_starts: 1, gate_opens: 1 } });
  });

  it("a provider confirmation inside the grace keeps the turn (no false start)", async () => {
    const t = await setup();
    t.localSpeech.speechStart(100);
    t.scheduler.advance(300);
    t.localSpeech.speechEnd(300);
    t.scheduler.advance(200);
    t.transport.emit({ type: "speech_started", at: 500 }); // late but inside the grace
    t.scheduler.advance(1000);
    expect(t.controller.getSnapshot().micMetrics.false_starts).toBe(0);
    expect(t.controller.getSnapshot().turn).toBe(1);
    await t.controller.flushEvents();
    expect(t.core.events.find((e) => e.kind === "uplink_first_packet")).toBeDefined();
  });

  it("a false start that already stopped the assistant is a false barge-in and returns to LISTENING", async () => {
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 0 });
    t.scheduler.advance(400);
    t.localSpeech.speechStart(400);
    expect(t.controller.getSnapshot().state).toBe("interrupted");
    expect(t.log).toContain("playback.stop"); // stop-first ordering untouched
    t.scheduler.advance(100);
    t.localSpeech.speechEnd(500);
    t.scheduler.advance(700);
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.controller.getSnapshot().micMetrics).toMatchObject({ false_starts: 1, false_barge_ins: 1 });
    await t.controller.flushEvents();
    const kinds = t.core.kinds();
    expect(kinds.indexOf("barge_in_start")).toBeLessThan(kinds.lastIndexOf("state"));
    expect(t.core.events[t.core.events.length - 1].payload).toEqual({ state: "LISTENING" });
  });

  it("a provider-confirmed turn with no transcript is a false turn, judged when the next turn starts", async () => {
    const t = await setup();
    t.transport.emit({ type: "speech_started", at: 100 });
    t.scheduler.advance(300);
    t.transport.emit({ type: "speech_stopped", at: 400 });
    t.scheduler.advance(1000);
    expect(t.controller.getSnapshot().micMetrics.false_turns).toBe(0); // the transcript may still be coming
    t.transport.emit({ type: "speech_started", at: 1400 });
    expect(t.controller.getSnapshot().micMetrics.false_turns).toBe(1);
    t.transport.emit({ type: "owner_transcript", at: 1800, text: "saat kaç", final: true });
    t.transport.emit({ type: "speech_stopped", at: 1900 });
    t.scheduler.advance(600);
    t.transport.emit({ type: "speech_started", at: 2500 });
    expect(t.controller.getSnapshot().micMetrics.false_turns).toBe(1); // turn 2 had words
    await t.controller.flushEvents();
    expect(t.core.events.find((e) => e.kind === "state" && e.payload?.false_turn === 1)).toBeDefined();
  });

  it("reports a completed calibration and the session totals at close, numbers only, server-safe keys", async () => {
    const t = await setup();
    t.localSpeech.emitCalibration({ noise_floor_db: -41.5, env_class: 3, sensitivity_class: 3, trigger: 0 });
    expect(t.controller.getSnapshot().micMetrics).toMatchObject({ noise_floor_db: -41.5, env: 3, calibrations: 1 });
    t.localSpeech.counters.gated_out = 7;
    t.localSpeech.counters.click_rejects = 12;
    await t.controller.disconnect();
    const calibration = t.core.events.find((e) => e.kind === "state" && e.payload?.mic_calibration === 1);
    expect(calibration?.payload).toMatchObject({
      noise_floor_db: -41.5,
      env_class: 3,
      sensitivity_class: 3,
      measured: 1,
      open_margin_db: 10,
      min_onset_ms: 70,
      hang_ms: 450,
      pre_roll_ms: 120,
    });
    const totals = t.core.events.find((e) => e.kind === "state" && e.payload?.session_end === 1);
    expect(totals?.payload).toMatchObject({ mic_metrics: 1, gated_out: 7, click_rejects: 12, false_starts: 0, false_turns: 0 });
    const kinds = t.core.kinds();
    expect(kinds[kinds.length - 1]).toBe("state");
    expect(t.core.events[t.core.events.length - 1].payload).toEqual({ state: "CLOSED" });
    for (const payload of payloads(t.core)) {
      for (const [key, value] of Object.entries(payload)) {
        expect(isForbiddenKey(key)).toBe(false);
        if (payload.mic_metrics === 1 || payload.mic_calibration === 1) expect(typeof value).toBe("number");
      }
    }
  });

  it("a reattach re-wires the local gate instead of stacking sinks: one calibration, one speech start, reported once", async () => {
    const t = await setup();
    // The same path a network restore uses: the leg drops, attach opens a new one.
    t.transport.emit({ type: "disconnected", at: 100, reason: "peer_failed" });
    await tick(8);
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.localSpeech.started).toBe(2); // detector restarted on the new leg
    t.log.length = 0;
    t.localSpeech.emitCalibration({ noise_floor_db: -47 });
    t.scheduler.advance(500);
    t.localSpeech.speechStart(600);
    await t.controller.flushEvents();
    expect(t.log.filter((op) => op === "report.mic_calibration")).toHaveLength(1);
    expect(t.core.events.filter((e) => e.kind === "state" && e.payload?.mic_calibration === 1)).toHaveLength(1);
    expect(t.core.events.filter((e) => e.kind === "mic_speech_start")).toHaveLength(1);
    expect(t.controller.getSnapshot().turn).toBe(1);
    expect(t.controller.getSnapshot().micMetrics.noise_floor_db).toBe(-47);
  });

  it("the metric payload builder drops anything that is not a safe number", () => {
    const built = numbersOnly({
      rms_db: -42.2,
      speech_prob: 0.8,
      gate_opens: 3,
      contaminated: true,
      transcript_chars: 12, // forbidden: "transcript"
      context_id: 5, // forbidden: contains "text"
      apiKey: 1,
      label: "K66",
      nested: { a: 1 },
      nan: Number.NaN,
    });
    expect(built).toEqual({ rms_db: -42.2, speech_prob: 0.8, gate_opens: 3, contaminated: 1 });
  });
});

/** ADR-0045: the contract probe (GET) precedes the create (POST). */
const create = (requests: { method: string; body: unknown }[]) => requests.find((r) => r.method === "POST")?.body;

describe("voice selection (ADR-0043 A/B)", () => {
  it("the create body carries the selected voice and the snapshot shows voice + profile", async () => {
    const t = await setup("cedar");
    expect(create(t.core.requests)).toMatchObject({ client_kind: "web", voice: "cedar" });
    expect(t.controller.getSnapshot()).toMatchObject({ voice: "cedar", voiceProfile: "arbor" });
    const plain = await setup();
    expect((create(plain.core.requests) as Record<string, unknown>).voice).toBeUndefined();
    expect(plain.controller.getSnapshot().voice).toBeNull();
  });
});
