import { describe, expect, it } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { VoiceSessionController } from "../../app/lib/voice/controller";
import {
  FakeCloudCore,
  type FakeCloudCoreOptions,
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

async function setup(options: FakeCloudCoreOptions = {}) {
  const log: string[] = [];
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc", ...options });
  const transports: FakeTransport[] = [];
  const playback = new FakePlayback(scheduler.now, (op) => log.push(op));
  const network = new FakeNetwork();
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
    network,
    microphone,
    localSpeech,
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    reattach: { maxAttempts: 3, baseDelayMs: 100 },
    log: (op) => log.push(op),
  });
  await controller.connect();
  await tick();
  return {
    controller,
    core,
    scheduler,
    playback,
    network,
    microphone,
    localSpeech,
    log,
    transports,
    get transport() {
      return transports[transports.length - 1];
    },
  };
}

describe("VoiceSessionController", () => {
  it("creates the session, opens the mic and the leg, and reports LISTENING", async () => {
    const t = await setup();
    expect(t.controller.getSnapshot().state).toBe("listening");
    // ADR-0045: the contract probe goes first, then the create.
    expect(t.core.requests[0]).toMatchObject({ method: "GET", path: "/v1/voice/realtime/contract" });
    expect(t.core.requests[1]).toMatchObject({ method: "POST", path: "/v1/voice/realtime/sessions" });
    expect((t.core.requests[1].body as { client_kind: string }).client_kind).toBe("web");
    expect(t.microphone.opened).toEqual([undefined]);
    expect(t.localSpeech.started).toBe(1);
    expect(t.transport.connects[0].credential.secret).toBe("ephemeral-1");
    expect(t.transport.connects[0].descriptor.kind).toBe("webrtc");
    await t.controller.flushEvents();
    // ADR-0047 §4: the input read-back (measured numbers) precedes LISTENING.
    expect(t.core.kinds()).toEqual(["state", "state"]);
    expect(t.core.events[0].payload).toMatchObject({ mic_input: 1, aec: 1, ns: 1, agc: 0, sample_rate: 48_000, channels: 1, not_honoured: 0 });
    expect(t.core.events[1].payload).toEqual({ state: "LISTENING" });
  });

  it("barge-in: stops local playback FIRST, then cancels, then reports with the measured latency", async () => {
    const t = await setup();
    t.scheduler.advance(1000);
    t.transport.emit({ type: "response_started", at: 1000 });
    expect(t.controller.getSnapshot().state).toBe("speaking");
    t.scheduler.advance(300);
    t.playback.activity(1300);
    expect(t.playback.playing).toBe(true);

    t.log.length = 0;
    t.scheduler.advance(200); // now = 1500: the owner starts talking
    t.transport.emit({ type: "speech_started", at: 1480 }); // provider VAD timestamp
    expect(t.log.slice(0, 4)).toEqual([
      "playback.stop", // 1. local playback silenced (the fake playback logs it)
      "fake.cancelResponse", // 2. provider cancel goes out on the transport
      "transport.cancel",
      "report.barge_in", // 3. reported with the measured latency
    ]);
    expect(t.playback.playing).toBe(false);
    expect(t.transport.sent).toEqual(["cancel"]);
    expect(t.controller.getSnapshot().state).toBe("interrupted");
    expect(t.controller.getSnapshot().latency.barge_in_to_stop_ms?.value).toBe(20);

    await t.controller.flushEvents();
    const kinds = t.core.kinds();
    expect(kinds.indexOf("barge_in_start")).toBeLessThan(kinds.indexOf("playback_stopped"));
    const barge = t.core.events.find((e) => e.kind === "barge_in_start");
    expect(barge).toMatchObject({ t_ms: 1480, turn: 1, payload: { playback_stopped_ms: 20, source: "provider" } });
    expect(t.core.events.find((e) => e.kind === "playback_stopped")).toMatchObject({ t_ms: 1500, turn: 1 });
    expect(t.core.events.find((e) => e.kind === "mic_speech_start")).toMatchObject({ t_ms: 1480, turn: 1 });
    expect(t.core.events.filter((e) => e.kind === "first_audio")).toHaveLength(1);
  });

  it("barge-in from the local detector does not wait for the provider's VAD", async () => {
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 0 });
    t.scheduler.advance(500);
    t.localSpeech.speechStart(500);
    expect(t.log).toContain("playback.stop");
    expect(t.transport.sent).toEqual(["cancel"]);
    // The provider's later speech_started is the same speech: no second barge-in.
    // Without transport counters (this fake has none) the uplink falls back to
    // the provider's confirmation, marked as such: basis 0 (ADR-0047 §1).
    t.scheduler.advance(90);
    t.transport.emit({ type: "speech_started", at: 590 });
    expect(t.transport.sent).toEqual(["cancel"]);
    await t.controller.flushEvents();
    expect(t.core.events.filter((e) => e.kind === "barge_in_start")).toHaveLength(1);
    expect(t.core.events.find((e) => e.kind === "uplink_first_packet")).toMatchObject({
      t_ms: 590,
      payload: { basis: 0 },
    });
    expect(t.controller.getSnapshot().latency.mic_to_uplink_ms?.value).toBe(90);
  });

  it("hesitation guard: a filler tail extends end-of-turn; resuming inside the hold is not a new turn", async () => {
    const t = await setup();
    t.scheduler.advance(100);
    t.transport.emit({ type: "speech_started", at: 100 });
    t.transport.emit({ type: "owner_transcript", at: 400, text: "raporun ikinci maddesini şey", final: true });
    t.scheduler.advance(400);
    t.transport.emit({ type: "speech_stopped", at: 500 });
    expect(t.controller.getSnapshot().hesitation.last).toBe("filler");
    // 200 base + 700 filler extension = 900 ms hold; nothing is reported yet.
    t.scheduler.advance(300);
    await t.controller.flushEvents();
    expect(t.core.kinds()).not.toContain("end_of_turn");

    // The provider commits a response prematurely, then the owner continues.
    t.transport.emit({ type: "response_started", at: 800 });
    t.scheduler.advance(100);
    t.transport.emit({ type: "speech_started", at: 900 });
    expect(t.transport.sent).toEqual(["cancel"]); // premature response cancelled
    expect(t.controller.getSnapshot().turn).toBe(1); // still the same turn
    expect(t.controller.getSnapshot().hesitation).toMatchObject({ held: 1, resumed_within_hold: 1 });

    t.transport.emit({ type: "owner_transcript", at: 1400, text: "tekrar oku", final: true });
    t.scheduler.advance(600);
    t.transport.emit({ type: "speech_stopped", at: 1500 });
    t.scheduler.advance(199);
    await t.controller.flushEvents();
    expect(t.core.kinds()).not.toContain("end_of_turn");
    t.scheduler.advance(1);
    await t.controller.flushEvents();
    const eot = t.core.events.filter((e) => e.kind === "end_of_turn");
    expect(eot).toHaveLength(1);
    expect(eot[0]).toMatchObject({ t_ms: 1500, turn: 1, payload: { hold_ms: 200, hesitation: "none" } });
    expect(t.core.events.filter((e) => e.kind === "mic_speech_start")).toHaveLength(1);
    expect(t.core.events.filter((e) => e.kind === "barge_in_start")[0].payload).toMatchObject({
      hesitation_resume: true,
    });
    // Both utterances reached Cloud Core for intent resolution; no audio did.
    expect(t.core.events.filter((e) => e.kind === "utterance").map((e) => e.text)).toEqual([
      "raporun ikinci maddesini şey",
      "tekrar oku",
    ]);
  });

  it("measures end-of-turn → first audio and reports response_done", async () => {
    const t = await setup();
    t.transport.emit({ type: "speech_started", at: 0 });
    t.transport.emit({ type: "owner_transcript", at: 900, text: "saat kaç", final: true });
    t.scheduler.advance(1000);
    t.transport.emit({ type: "speech_stopped", at: 1000 });
    t.scheduler.advance(200);
    t.transport.emit({ type: "response_started", at: 1400 });
    t.scheduler.advance(400);
    t.transport.emit({ type: "audio_started", at: 1600 });
    // ADR-0047 §3: the provider's audio-start is a component, not first audio;
    // the first AUDIBLE sample from the local analyser is.
    expect(t.controller.getSnapshot().latency.eot_to_first_audio_ms).toBeUndefined();
    t.scheduler.advance(20);
    t.playback.activity(1620);
    expect(t.controller.getSnapshot().latency.eot_to_first_audio_ms?.value).toBe(620);
    t.transport.emit({ type: "response_text", at: 1700, text: "Saat ", final: false });
    t.transport.emit({ type: "response_text", at: 1800, text: "on.", final: false });
    expect(t.controller.getSnapshot().assistantText).toBe("Saat on.");
    t.transport.emit({ type: "response_done", at: 2500 });
    expect(t.controller.getSnapshot().state).toBe("listening");
    await t.controller.flushEvents();
    expect(t.core.kinds()).toEqual([
      "state", // mic_input read-back (ADR-0047 §4)
      "state", // LISTENING
      "mic_speech_start",
      "state", // provider-first start: explicit unmatched marker (ADR-0047 §1)
      "utterance",
      "end_of_turn",
      "state",
      "first_audio",
      "response_done",
      "state",
    ]);
    expect(t.core.events[3].payload).toMatchObject({ mic_metrics: 1, unmatched: 1, provider_first: 1 });
    expect(t.core.events.find((e) => e.kind === "first_audio")?.payload).toMatchObject({
      basis: 1,
      response_created_ms: 400,
      first_delta_ms: 200,
      playback_ms: 20,
    });
    for (const event of t.core.events) {
      expect(Number.isInteger(event.t_ms)).toBe(true);
      expect(event.t_ms).toBeGreaterThanOrEqual(0);
    }
  });

  it("relays a tool call exactly once per call_id, however often the provider repeats it", async () => {
    const t = await setup();
    const call = { type: "tool_call" as const, at: 10, callId: "call-1", name: "clock.now", arguments: {} };
    t.transport.emit(call);
    t.transport.emit(call); // response.done repeats the function_call item
    await tick();
    t.transport.emit(call); // and a redelivery after that
    await tick();
    const relays = t.core.requests.filter((r) => r.path.endsWith("/tool-calls"));
    expect(relays).toHaveLength(1);
    expect(relays[0].body).toEqual({ call_id: "call-1", name: "clock.now", arguments: {} });
    expect(t.transport.sent.filter((s) => s.startsWith("submit:call-1"))).toHaveLength(1);
    expect(t.transport.sent[0]).toBe('submit:call-1:{"echo":{}}');
    expect(t.log.filter((op) => op === "tool.duplicate:call-1")).toHaveLength(2);
    expect(t.controller.getSnapshot().state).toBe("listening");
    await t.controller.flushEvents();
    expect(t.core.events.filter((e) => e.kind === "tool_call")).toHaveLength(1);
    expect(t.core.events.find((e) => e.kind === "tool_done")?.payload).toMatchObject({
      call_id: "call-1",
      status: "succeeded",
    });
  });

  it("long-running tool: preamble now, completion via the replayed sideband, speech resumed", async () => {
    const t = await setup({
      toolResponses: {
        "research.start": { status: "running", long_running: true, preamble: "Bakıyorum.", result: { plan_id: "p1" } },
      },
    });
    t.scheduler.advance(1000);
    t.transport.emit({ type: "tool_call", at: 1000, callId: "call-lr", name: "research.start", arguments: { topic: "ajanlar" } });
    await tick();
    expect(t.controller.getSnapshot().state).toBe("tool_running");
    expect(t.controller.getSnapshot().toolsRunning).toEqual(["research.start"]);
    expect(t.transport.sent).toEqual(['submit:call-lr:{"status":"running","preamble":"Bakıyorum.","result":{"plan_id":"p1"}}']);

    // The provider speaks the preamble.
    t.transport.emit({ type: "response_started", at: 1200 });
    t.scheduler.advance(400);
    t.playback.activity(1400);
    expect(t.controller.getSnapshot().latency.tool_preamble_ms?.value).toBe(400);
    t.transport.emit({ type: "response_done", at: 2000 });
    expect(t.controller.getSnapshot().state).toBe("tool_running");

    // Cloud Core finishes the tool; the web client learns it on its next /events.
    t.core.queueSideband("tool_completed", {
      call_id: "call-lr",
      name: "research.start",
      status: "succeeded",
      result: { summary: "üç bulgu" },
    });
    t.scheduler.advance(3000);
    await t.controller.flushEvents();
    expect(t.transport.sent[1]).toBe('completed:call-lr:research.start:{"summary":"üç bulgu"}');
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.controller.getSnapshot().toolsRunning).toEqual([]);

    t.transport.emit({ type: "response_started", at: 4500 });
    t.scheduler.advance(300);
    t.transport.emit({ type: "audio_started", at: 4700 });
    // No local analyser activity in this script: the provider's mark is used
    // after the playback-confirm bound (basis 0), stamped at the provider's time.
    t.scheduler.advance(300);
    await t.controller.flushEvents();
    const kinds = t.core.kinds();
    for (const kind of ["tool_call", "preamble_audio_start", "tool_done", "speech_resumed"]) {
      expect(kinds).toContain(kind);
    }
    expect(kinds.indexOf("tool_done")).toBeLessThan(kinds.indexOf("speech_resumed"));
    // tool_done was stamped when the sideband frame was processed (t = 4400).
    expect(t.controller.getSnapshot().latency.tool_done_to_speech_ms?.value).toBe(4700 - 4400);
  });

  it("network loss: network_lost, leg torn down, attach on restore with a fresh credential and replayed sideband", async () => {
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 0 });
    t.scheduler.advance(2000);
    t.network.set(false);
    expect(t.controller.getSnapshot().state).toBe("reconnecting");
    expect(t.transports[0].closedWith).toBe("offline");
    expect(t.playback.playing).toBe(false);

    // Cloud Core queues a push while we are away.
    t.core.queueSideband("say", { text: "Devam ediyorum." });
    t.scheduler.advance(5000);
    t.network.set(true);
    await tick(8);
    expect(t.transports).toHaveLength(2);
    expect(t.transports[1].connects[0].credential.secret).toBe("ephemeral-2");
    expect(t.transports[1].connected).toBe(true);
    expect(t.transports[1].sent).toEqual(["say:Devam ediyorum."]);
    expect(t.core.requests.some((r) => r.path.endsWith("/attach"))).toBe(true);
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.microphone.opened).toHaveLength(1); // the microphone survives the reattach

    const kinds = t.core.kinds();
    expect(kinds.indexOf("network_lost")).toBeGreaterThan(-1);
    expect(kinds.indexOf("network_lost")).toBeLessThan(kinds.indexOf("network_restored"));
    expect(t.core.events.find((e) => e.kind === "network_lost")).toMatchObject({ t_ms: 2000, payload: { reason: "offline" } });
    expect(t.core.events.find((e) => e.kind === "network_restored")?.t_ms).toBe(7000);
    expect(t.core.events.filter((e) => e.kind === "network_lost")).toHaveLength(1);
  });

  it("network loss from the transport itself reattaches immediately while the browser is online", async () => {
    const t = await setup();
    t.transport.emit({ type: "disconnected", at: 100, reason: "peer_failed" });
    await tick(8);
    expect(t.transports).toHaveLength(2);
    expect(t.controller.getSnapshot().state).toBe("listening");
    await t.controller.flushEvents();
    expect(t.core.events.find((e) => e.kind === "network_lost")?.payload).toEqual({ reason: "peer_failed" });
  });

  it("retries attach with backoff and gives up after the configured attempts", async () => {
    const t = await setup();
    t.core.failNext("/attach", 503);
    t.core.failNext("/attach", 503);
    t.transport.emit({ type: "disconnected", at: 100, reason: "peer_failed" });
    await tick(8);
    expect(t.transports).toHaveLength(1);
    expect(t.controller.getSnapshot().state).toBe("reconnecting");
    t.scheduler.advance(100); // first backoff
    await tick(8);
    t.scheduler.advance(200); // second backoff
    await tick(8);
    expect(t.transports).toHaveLength(2);
    expect(t.controller.getSnapshot().state).toBe("listening");
  });

  it("a stale leg (409) on a tool relay reattaches and retries once", async () => {
    const t = await setup();
    t.core.stealLeg();
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-9", name: "clock.now", arguments: {} });
    await tick(8);
    expect(t.core.requests.filter((r) => r.path.endsWith("/tool-calls"))).toHaveLength(2);
    expect(t.core.requests.filter((r) => r.path.endsWith("/attach"))).toHaveLength(1);
    expect(t.transports[1].sent).toEqual(['submit:call-9:{"echo":{}}']);
  });

  it("leg_closed from another device ends this leg without closing the session", async () => {
    const t = await setup();
    t.core.queueSideband("leg_closed", { reason: "attached_elsewhere", new_client_kind: "desktop" });
    t.controller.getSnapshot();
    await t.controller.flushEvents();
    expect(t.controller.getSnapshot().state).toBe("closed");
    expect(t.transport.closedWith).toBe("attached_elsewhere");
    expect(t.core.closed).toBeNull();
  });

  it("disconnect reports the summary and CLOSED, flushes, then closes the server session", async () => {
    const t = await setup();
    t.transport.emit({ type: "owner_transcript", at: 5, text: "dur", final: true });
    t.transport.emit({ type: "response_text", at: 6, text: "Durdum.", final: true });
    await t.controller.disconnect();
    expect(t.controller.getSnapshot().state).toBe("closed");
    expect(t.core.closed).toBe("client_closed");
    const summary = t.core.events.find((e) => e.kind === "summary");
    expect(summary?.text).toBe("Sahip: dur | Asistan: Durdum.");
    const last = t.core.requests[t.core.requests.length - 1];
    expect(last.path.endsWith("/close")).toBe(true);
    expect(t.core.requests.filter((r) => r.path.endsWith("/events")).length).toBeGreaterThan(0);
    expect(t.core.kinds()[t.core.kinds().length - 1]).toBe("state");
    expect(t.core.events[t.core.events.length - 1].payload).toEqual({ state: "CLOSED" });
    expect(t.microphone.stream).toBeNull();
  });

  it("never reports a payload the server would refuse", async () => {
    const t = await setup();
    await t.controller.flushEvents();
    for (const request of t.core.requests.filter((r) => r.path.endsWith("/events"))) {
      const text = JSON.stringify(request.body);
      expect(text).not.toMatch(/ephemeral-/);
      expect(text).not.toMatch(/"audio"/);
    }
  });
});
