import { describe, expect, it } from "vitest";

import { type Fetcher, VoiceSessionApi } from "../../app/lib/voice/api";
import { VoiceSessionController } from "../../app/lib/voice/controller";
import {
  DELEGATION_CALL_ID_PREFIX,
  DELEGATION_CANCELLED_EVENT,
  DelegationBridge,
} from "../../app/lib/voice/delegation";
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
import type { SidebandFrame } from "../../app/lib/voice/contract";
import { NOT_UNDERSTOOD_TR, TOOL_FAILED_TR } from "../../app/lib/voice/localMode";
import type { RealtimeTransport } from "../../app/lib/voice/transport";
import { WebRtcTransport } from "../../app/lib/voice/webrtc";

/**
 * The delegation bridge (ADR in team/plans/gpt-live-web-bridge-adr.md): a GPT-Live
 * delegation goes to the relay exactly the way local mode's sentences do (an `utterance`
 * event, the deterministic router, the same `/tool-calls`), and the receipted answer
 * comes back as `session.commentary.append` for the SAME delegation id - never for
 * another one, never for a cancelled one.
 */

const tick = async (rounds = 6): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

const LIGHTS_SPEECH = "Salonun ışığı açıldı efendim.";
const RESEARCH_SPEECH = "Araştırmayı başlattım efendim.";

function router(text: string): Array<Record<string, unknown>> {
  // `/i` does not fold the Turkish dotless I: "Işığı" needs its own class.
  if (/[ıI]şı/.test(text)) return [{ intent: "lights", tool: "lights_on" }];
  if (/araştır/i.test(text)) return [{ intent: "research", tool: "research_start" }];
  return [];
}

/** A relay whose `/tool-calls` can be held per tool name, to order answers by hand. */
function heldRelay(options: FakeCloudCoreOptions = {}) {
  const core = new FakeCloudCore({
    resolveIntents: router,
    toolResponses: {
      lights_on: { status: "succeeded", result: { speech: LIGHTS_SPEECH } },
      research_start: { status: "succeeded", result: { speech: RESEARCH_SPEECH } },
    },
    ...options,
  });
  const holds = new Map<string, { promise: Promise<void>; release: () => void }>();
  const hold = (name: string): (() => void) => {
    let release!: () => void;
    const promise = new Promise<void>((resolve) => {
      release = resolve;
    });
    holds.set(name, { promise, release });
    return release;
  };
  const fetcher: Fetcher = async (path, init) => {
    if (path.endsWith("/tool-calls") && typeof init?.body === "string") {
      const name = (JSON.parse(init.body) as { name: string }).name;
      const held = holds.get(name);
      if (held) await held.promise;
    }
    return core.fetcher(path, init);
  };
  return { core, hold, api: new VoiceSessionApi(fetcher) };
}

const SESSION = "11111111-2222-4333-8444-555555555555";

function bridgeOn(api: VoiceSessionApi, options: { channelOpen?: boolean } = {}) {
  const said: Array<[string, string]> = [];
  const log: string[] = [];
  const sideband: SidebandFrame[] = [];
  const bridge = new DelegationBridge({
    api,
    sessionId: SESSION,
    commentary: (delegationId, text) => {
      if (options.channelOpen === false) return false;
      said.push([delegationId, text]);
      return true;
    },
    sideband: (frame) => sideband.push(frame),
    clock: () => 1234,
    turn: () => 3,
    log: (op) => log.push(op),
  });
  return { bridge, said, log, sideband };
}

const cancelledEvents = (core: FakeCloudCore) =>
  core.events.filter((e) => e.payload?.event === DELEGATION_CANCELLED_EVENT);

describe("DelegationBridge", () => {
  it("(a) delegation -> utterance {source, delegation_id} -> /tool-calls -> commentary.append with the same id", async () => {
    const { core, api } = heldRelay();
    const { bridge, said } = bridgeOn(api);
    bridge.noteOwnerText("Salonun ışığını ");
    bridge.noteOwnerText("aç.");
    await bridge.onDelegation("item_delegation_123");

    expect(core.events).toEqual([
      {
        kind: "utterance",
        t_ms: 1234,
        turn: 3,
        text: "Salonun ışığını aç.",
        payload: { source: "delegation", delegation_id: "item_delegation_123" },
      },
    ]);
    const call = core.requests.find((r) => r.path.endsWith("/tool-calls"));
    expect(call?.body).toEqual({
      call_id: `${DELEGATION_CALL_ID_PREFIX}item_delegation_123-1`,
      name: "lights_on",
      arguments: {},
    });
    expect(said).toEqual([["item_delegation_123", LIGHTS_SPEECH]]);
    expect(bridge.stateOf("item_delegation_123")).toBe("bitti");
  });

  it("(b) a finished delegation's result is never sent a second time", async () => {
    const { api } = heldRelay();
    const { bridge, said } = bridgeOn(api);
    bridge.noteOwnerText("Işığı aç.");
    await bridge.onDelegation("d1");
    expect(said).toEqual([["d1", LIGHTS_SPEECH]]);
    expect(bridge.deliver("d1", "İkinci sonuç")).toBe(false);
    expect(said).toEqual([["d1", LIGHTS_SPEECH]]);
  });

  it("a result the transport cannot carry is not marked done", async () => {
    const { api } = heldRelay();
    const { bridge, said, log } = bridgeOn(api, { channelOpen: false });
    bridge.noteOwnerText("Işığı aç.");
    await bridge.onDelegation("d1");
    expect(said).toEqual([]);
    expect(bridge.stateOf("d1")).toBe("gonderilemedi");
    expect(log).toContain("delegation.undelivered d1");
    expect(log).not.toContain("delegation.done d1");
  });

  it("the relay's other pending messages go to the sideband handler, not the floor", async () => {
    const { core, hold, api } = heldRelay();
    const { bridge, said, sideband } = bridgeOn(api);
    core.queueSideband("plan_changed", { scope: "brifing", revision: 2 });
    core.queueSideband("say", { text: "Sabah brifingi hazır." });
    bridge.noteOwnerText("Işığı aç.");
    await bridge.onDelegation("d1");
    // `say` lines are part of the spoken result; everything else is handed on.
    expect(said).toEqual([["d1", `Sabah brifingi hazır. ${LIGHTS_SPEECH}`]]);
    expect(sideband.map((f) => f.event)).toEqual(["plan_changed"]);

    // The cancel report's answer carries the queue too: none of it is dropped.
    const release = hold("lights_on");
    bridge.noteOwnerText("Işığı aç.");
    const running = bridge.onDelegation("d2");
    await tick();
    core.queueSideband("tool_progress", { step: 1 });
    await bridge.cancel("d2", "session_closed");
    release();
    await running;
    expect(sideband.map((f) => f.event)).toEqual(["plan_changed", "tool_progress"]);
  });

  it("(b) a result without a delegation id, or for an id never delegated, is never sent", async () => {
    const { core, api } = heldRelay();
    const { bridge, said } = bridgeOn(api);
    expect(bridge.deliver("", "Bir şey")).toBe(false);
    expect(bridge.deliver(undefined as unknown as string, "Bir şey")).toBe(false);
    expect(bridge.deliver("never_delegated", "Bir şey")).toBe(false);
    await bridge.onDelegation("");
    expect(said).toEqual([]);
    expect(core.events).toEqual([]);
  });

  it("(c) the late result of a cancelled delegation is never sent, and the cancellation is written to the relay", async () => {
    const { core, hold, api } = heldRelay();
    const { bridge, said } = bridgeOn(api);
    const release = hold("lights_on");
    bridge.noteOwnerText("Işığı aç.");
    const running = bridge.onDelegation("d1");
    await tick();
    expect(bridge.stateOf("d1")).toBe("bekliyor");

    await bridge.cancel("d1", "session_closed");
    expect(bridge.stateOf("d1")).toBe("iptal");
    release();
    await running;

    expect(said).toEqual([]);
    expect(bridge.deliver("d1", LIGHTS_SPEECH)).toBe(false);
    expect(said).toEqual([]);
    const written = cancelledEvents(core);
    expect(written).toHaveLength(1);
    expect(written[0]).toMatchObject({
      kind: "state",
      turn: 3,
      payload: { event: DELEGATION_CANCELLED_EVENT, delegation_id: "d1", reason: "session_closed" },
    });
    // Cancelling it again, or a finished/unknown one, writes nothing more.
    await bridge.cancel("d1", "again");
    await bridge.cancel("unknown", "x");
    expect(cancelledEvents(core)).toHaveLength(1);
  });

  it("(c) a cancelled delegation runs no further tools", async () => {
    const both = (text: string) =>
      /ve/.test(text) ? [...router("ışı"), ...router("araştır")] : router(text);
    const { core, hold, api } = heldRelay({ resolveIntents: both });
    const { bridge, said } = bridgeOn(api);
    const release = hold("lights_on");
    bridge.noteOwnerText("Işığı aç ve telefonları araştır.");
    const running = bridge.onDelegation("d1");
    await tick();
    await bridge.cancel("d1", "session_closed");
    release();
    await running;
    const calls = core.requests.filter((r) => r.path.endsWith("/tool-calls"));
    expect(calls.map((r) => (r.body as { name: string }).name)).toEqual(["lights_on"]);
    expect(said).toEqual([]);
  });

  it("(c) closing the session cancels every pending delegation", async () => {
    const { core, hold, api } = heldRelay();
    const { bridge, said } = bridgeOn(api);
    const releaseLights = hold("lights_on");
    const releaseResearch = hold("research_start");
    bridge.noteOwnerText("Işığı aç.");
    const one = bridge.onDelegation("d1");
    await tick();
    bridge.noteOwnerText("Şunu araştır.");
    const two = bridge.onDelegation("d2");
    await tick();
    await bridge.cancelAll("client_closed");
    releaseLights();
    releaseResearch();
    await Promise.all([one, two]);
    expect(said).toEqual([]);
    expect(cancelledEvents(core).map((e) => e.payload?.delegation_id)).toEqual(["d1", "d2"]);
  });

  it("(d) a failed tool never yields a success sentence: the relay's failure line is what is said", async () => {
    const failing = heldRelay({
      toolResponses: {
        lights_on: { status: "failed", result: null, error: { speech: "Işık cihazına ulaşılamadı efendim." } },
      },
    });
    const one = bridgeOn(failing.api);
    one.bridge.noteOwnerText("Işığı aç.");
    await one.bridge.onDelegation("d1");
    expect(one.said).toEqual([["d1", "Işık cihazına ulaşılamadı efendim."]]);

    // A failure with no sentence of its own -> the honest generic line.
    const bare = heldRelay({ toolResponses: { lights_on: { status: "failed", result: null, error: null } } });
    const two = bridgeOn(bare.api);
    two.bridge.noteOwnerText("Işığı aç.");
    await two.bridge.onDelegation("d2");
    expect(two.said).toEqual([["d2", TOOL_FAILED_TR]]);

    // The request itself failing -> the same honest line, never a guess.
    const down = heldRelay();
    down.core.failNext("/tool-calls", 500, { detail: "boom" });
    const three = bridgeOn(down.api);
    three.bridge.noteOwnerText("Işığı aç.");
    await three.bridge.onDelegation("d3");
    expect(three.said).toEqual([["d3", TOOL_FAILED_TR]]);

    // Nothing resolved -> "Anlayamadım", not a success.
    const four = bridgeOn(heldRelay().api);
    four.bridge.noteOwnerText("Bugün nasılsın?");
    await four.bridge.onDelegation("d4");
    expect(four.said).toEqual([["d4", NOT_UNDERSTOOD_TR]]);

    for (const said of [one.said, two.said, three.said, four.said]) {
      expect(said.flat().join(" ")).not.toContain("açıldı");
    }
  });

  it("(f) two concurrent delegations never swap results, whatever order the relay answers in", async () => {
    const { core, hold, api } = heldRelay();
    const { bridge, said } = bridgeOn(api);
    const releaseResearch = hold("research_start");
    const releaseLights = hold("lights_on");
    bridge.noteOwnerText("Yeni telefonları araştır.");
    const first = bridge.onDelegation("d_research");
    await tick();
    bridge.noteOwnerText("Bu arada ışığı aç.");
    const second = bridge.onDelegation("d_lights");
    await tick();

    // Each delegation took only the words said since the previous one.
    const utterances = core.events.filter((e) => e.kind === "utterance");
    expect(utterances.map((e) => [e.payload?.delegation_id, e.text])).toEqual([
      ["d_research", "Yeni telefonları araştır."],
      ["d_lights", "Bu arada ışığı aç."],
    ]);

    releaseLights(); // the later one answers first
    await second;
    releaseResearch();
    await first;
    expect(said).toEqual([
      ["d_lights", LIGHTS_SPEECH],
      ["d_research", RESEARCH_SPEECH],
    ]);
  });
});

// ------------------------------------------------------------ controller

class LiveFakeTransport extends FakeTransport {
  readonly commentary: Array<[string, string]> = [];
  appendCommentary(delegationId: string, text: string): void {
    this.commentary.push([delegationId, text]);
  }
}

async function liveController(options: { withCommentary?: boolean } = {}) {
  const log: string[] = [];
  const scheduler = new FakeScheduler();
  const relay = heldRelay({ transport: "webrtc", descriptor: { dialect: "openai-live" }, provider: "openai-live" });
  const transports: LiveFakeTransport[] = [];
  const playback = new FakePlayback(scheduler.now, (op) => log.push(op));
  const Transport = options.withCommentary === false ? FakeTransport : LiveFakeTransport;
  const controller = new VoiceSessionController({
    api: relay.api,
    transportFactory: () => {
      const transport = new Transport({ log: (op) => log.push(op), now: scheduler.now }) as LiveFakeTransport;
      transports.push(transport);
      return transport;
    },
    playback,
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    reattach: { maxAttempts: 3, baseDelayMs: 100 },
    preferProvider: () => null,
    log: (op) => log.push(op),
  });
  await controller.connect();
  await tick();
  return { controller, relay, scheduler, playback, log, transport: () => transports[transports.length - 1] };
}

describe("controller + openai-live", () => {
  it("(e) a barge-in while a delegation runs does not cancel it: the result is still spoken for its id", async () => {
    const t = await liveController();
    const release = t.relay.hold("lights_on");
    const transport = t.transport();
    transport.emit({ type: "owner_transcript", at: 100, text: "Işığı aç.", final: false });
    transport.emit({ type: "delegation", at: 120, delegationId: "d1" });
    await tick();

    // The assistant is talking; the owner talks over it with a stop phrase.
    t.scheduler.advance(1000);
    transport.emit({ type: "response_started", at: 1000 });
    t.scheduler.advance(300);
    t.playback.activity(1300);
    t.scheduler.advance(200);
    transport.emit({ type: "speech_started", at: 1480 });
    transport.emit({ type: "owner_transcript", at: 1520, text: "dur", final: false });
    expect(t.controller.getSnapshot().state).toBe("interrupted");

    release();
    await tick();
    expect(transport.commentary).toEqual([["d1", LIGHTS_SPEECH]]);
    await t.controller.flushEvents();
    expect(cancelledEvents(t.relay.core)).toEqual([]);
  });

  it("closing the session cancels a pending delegation and writes delegation_cancelled before the close", async () => {
    const t = await liveController();
    const release = t.relay.hold("lights_on");
    const transport = t.transport();
    transport.emit({ type: "owner_transcript", at: 100, text: "Işığı aç.", final: false });
    transport.emit({ type: "delegation", at: 120, delegationId: "d9" });
    await tick();
    await t.controller.disconnect();
    release();
    await tick();
    expect(transport.commentary).toEqual([]);
    expect(cancelledEvents(t.relay.core).map((e) => e.payload?.delegation_id)).toEqual(["d9"]);
    expect(t.relay.core.closed).toBe("client_closed");
  });

  it("a transport with no commentary path logs it and the delegation is not reported done", async () => {
    const t = await liveController({ withCommentary: false });
    const transport = t.transport();
    transport.emit({ type: "owner_transcript", at: 100, text: "Işığı aç.", final: false });
    transport.emit({ type: "delegation", at: 120, delegationId: "d1" });
    await tick();
    expect(t.log).toContain("delegation.commentary_unsupported d1");
    expect(t.log).toContain("delegation.undelivered d1");
    expect(t.log).not.toContain("delegation.done d1");
  });

  it("relay messages answered to a delegation reach the controller's sideband log", async () => {
    const t = await liveController();
    const transport = t.transport();
    t.relay.core.queueSideband("plan_changed", { scope: "brifing", revision: 4 });
    transport.emit({ type: "owner_transcript", at: 100, text: "Işığı aç.", final: false });
    transport.emit({ type: "delegation", at: 120, delegationId: "d1" });
    await tick();
    expect(transport.commentary).toEqual([["d1", LIGHTS_SPEECH]]);
    expect(t.controller.getSnapshot().sidebandLog).toContain("plan değişti: brifing (rev 4)");
  });

  it("the real WebRTC transport carries commentary to the data channel", () => {
    // Without it no delegation result ever reaches GPT-Live in the real shell.
    expect(typeof (WebRtcTransport.prototype as RealtimeTransport).appendCommentary).toBe("function");
  });

  it("the vendor closing the session cancels the pending delegation", async () => {
    const t = await liveController();
    const release = t.relay.hold("lights_on");
    const transport = t.transport();
    transport.emit({ type: "owner_transcript", at: 100, text: "Işığı aç.", final: false });
    transport.emit({ type: "delegation", at: 120, delegationId: "d7" });
    await tick();
    transport.emit({ type: "disconnected", at: 200, reason: "session_closed:expired" });
    release();
    await tick();
    expect(transport.commentary).toEqual([]);
    expect(cancelledEvents(t.relay.core).map((e) => e.payload?.delegation_id)).toEqual(["d7"]);
  });
});
