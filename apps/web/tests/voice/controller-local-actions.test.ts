/**
 * The local half of an eye action, through the voice controller
 * (M18_ACTION_CONTRACT.md §5.1, §7.2, §7.3).
 *
 * The owner's 2026-09-06 run: "Gözünü kapat." disabled the eye server-side
 * and the assistant said "öyle olmuş gibi düşün"; "Gözünü aç" did nothing,
 * because the camera lives in the browser and nothing could reopen it. What
 * is pinned here: a provider `function_call` for `eye.enable` / `eye.disable`
 * runs THIS device's capability BEFORE the relay; the relayed `arguments`
 * carry exactly `{utterance, observed_after: {local: {...}}}`; a failing
 * local enable relays `local.state === "ERROR"` with its `error_class`; and
 * the result's `speech` reaches `submitToolResult` unchanged. First with a
 * scripted port (the controller's contract), then end to end through a real
 * `EyeStore` over a fake camera (the rig's wiring).
 */

import { describe, expect, it } from "vitest";

import type { EyeActionIdentity } from "../../app/lib/eye/client";
import { actionIdentity, eyeLocalActions, observedAfter, voiceReason } from "../../app/lib/eye/local-actions";
import type { FrameReducer, FrameSource } from "../../app/lib/eye/perception";
import { EyeStore, eyeInstances } from "../../app/lib/eye/store";
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
import type { LocalActionPort } from "../../app/lib/voice/ports";
import { flatFrame } from "../eye/fixtures";

const tick = async (rounds = 6): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

async function setup(options: FakeCloudCoreOptions, localActions?: LocalActionPort) {
  const log: string[] = [];
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc", ...options });
  const transports: FakeTransport[] = [];
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => {
      const transport = new FakeTransport({ now: scheduler.now });
      transports.push(transport);
      return transport;
    },
    playback: new FakePlayback(scheduler.now),
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    localActions,
    log: (op) => log.push(op),
  });
  await controller.connect();
  await tick();
  return {
    controller,
    core,
    log,
    get transport() {
      return transports[transports.length - 1];
    },
    relays: () => core.requests.filter((r) => r.path.endsWith("/tool-calls")),
  };
}

const AT = "2026-09-06T12:00:00.000Z";

/** The realtime session `FakeCloudCore` creates; the controller hands it to the port as `session_id`. */
const SESSION = "11111111-2222-4333-8444-555555555555";

/** What the Cloud Core answers for a verified disable (§5.5 receipt, with `speech`). */
const DISABLE_RECEIPT = {
  action_id: "call-1",
  capability: "eye.disable",
  requested_state: "disabled",
  execution_status: "executed",
  terminal_status: "verified",
  observed_after: { server: { eye_enabled: false }, local: { state: "DISABLED" } },
  evidence_refs: [{ kind: "ledger_event", ref: "42" }],
  error_class: null,
  speech: "Gözümü kapattım efendim.",
};

describe("the controller asks the local port first, then relays what it observed", () => {
  it("eye.disable: the local action runs BEFORE the relay; arguments carry observed_after; the speech reaches the provider unchanged", async () => {
    let relaysWhenLocalRan = -1;
    let seen: { name: string; args: Record<string, unknown> } | null = null;
    const port: LocalActionPort = {
      run: async (name, args) => {
        seen = { name, args };
        relaysWhenLocalRan = t.relays().length;
        return { local: { state: "DISABLED", running: false, camera_label: null, error_class: null, observed_at: AT } };
      },
    };
    const t = await setup({ toolResponses: { "eye.disable": { result: DISABLE_RECEIPT } } }, port);
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-1", name: "eye.disable", arguments: { utterance: "Gözünü kapat." } });
    await tick();

    // The port sees the provider's arguments PLUS the action's identity (call id, realtime session)…
    expect(seen).toEqual({ name: "eye.disable", args: { utterance: "Gözünü kapat.", call_id: "call-1", session_id: SESSION } });
    expect(relaysWhenLocalRan).toBe(0); // local first, relay after
    expect(t.relays()).toHaveLength(1);
    // …while the RELAYED arguments are the provider's own, unchanged (no call_id/session_id leaks into them).
    expect(t.relays()[0].body).toEqual({
      call_id: "call-1",
      name: "eye.disable",
      arguments: {
        utterance: "Gözünü kapat.",
        observed_after: { local: { state: "DISABLED", running: false, camera_label: null, error_class: null, observed_at: AT } },
      },
    });
    expect(t.transport.sent).toEqual([`submit:call-1:${JSON.stringify(DISABLE_RECEIPT)}`]);
    expect(t.log.indexOf("tool.local:call-1")).toBeGreaterThan(t.log.indexOf("tool.relay:call-1"));
    expect(t.log.indexOf("tool.local:call-1")).toBeLessThan(t.log.indexOf("tool.submit:call-1"));
    expect(t.controller.getSnapshot().state).toBe("listening");
    await t.controller.flushEvents();
    expect(t.core.events.find((e) => e.kind === "tool_done")?.payload).toMatchObject({ call_id: "call-1", name: "eye.disable", status: "succeeded" });
  });

  it("eye.enable that fails locally relays local.state ERROR with its error_class; the server's failed speech reaches the provider unchanged", async () => {
    const port: LocalActionPort = {
      run: async () => ({
        local: { state: "ERROR", running: false, camera_label: null, error_class: "permission_denied", observed_at: AT },
      }),
    };
    const receipt = {
      ...DISABLE_RECEIPT,
      capability: "eye.enable",
      requested_state: "active",
      execution_status: "failed",
      terminal_status: "failed",
      observed_after: { server: { eye_enabled: false }, local: { state: "ERROR" } },
      error_class: "permission_denied",
      speech: "Kamerayı açamadım; tarayıcı kamera izni vermedi.",
    };
    const t = await setup({ toolResponses: { "eye.enable": { result: receipt } } }, port);
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-1", name: "eye.enable", arguments: { utterance: "Gözünü aç." } });
    await tick();
    expect((t.relays()[0].body as { arguments: Record<string, unknown> }).arguments).toEqual({
      utterance: "Gözünü aç.",
      observed_after: { local: { state: "ERROR", running: false, camera_label: null, error_class: "permission_denied", observed_at: AT } },
    });
    expect(t.transport.sent).toEqual([`submit:call-1:${JSON.stringify(receipt)}`]);
  });

  it("a tool the port has nothing local for is relayed exactly as before (no observed_after key at all)", async () => {
    const port: LocalActionPort = { run: async () => null };
    const t = await setup({}, port);
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-2", name: "clock.now", arguments: {} });
    await tick();
    expect(t.relays()[0].body).toEqual({ call_id: "call-2", name: "clock.now", arguments: {} });
    expect(t.transport.sent).toEqual(['submit:call-2:{"echo":{}}']);
    expect(t.log).not.toContain("tool.local:call-2");
  });

  it("a port that throws does not break the relay: the call goes out without observed_after (the server reads that as capability_missing)", async () => {
    const port: LocalActionPort = {
      run: async () => {
        throw new Error("port broke");
      },
    };
    const t = await setup({}, port);
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-3", name: "eye.enable", arguments: { utterance: "Gözünü aç." } });
    await tick();
    expect(t.relays()[0].body).toEqual({ call_id: "call-3", name: "eye.enable", arguments: { utterance: "Gözünü aç." } });
    expect(t.log.some((op) => op.startsWith("tool.local_error:call-3"))).toBe(true);
    expect(t.transport.sent).toHaveLength(1);
  });

  it("without a local port (a client with no camera) nothing changes in the relay path", async () => {
    const t = await setup({});
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-4", name: "eye.disable", arguments: { utterance: "Gözünü kapat." } });
    await tick();
    expect(t.relays()[0].body).toEqual({ call_id: "call-4", name: "eye.disable", arguments: { utterance: "Gözünü kapat." } });
  });
});

// ---------------------------------------------------- end to end, real store

class Camera implements FrameSource {
  starts = 0;
  stops = 0;
  open = false;
  failWith: unknown = null;
  private readonly pixels = flatFrame(4, 4, 100);

  async start(): Promise<void> {
    this.starts += 1;
    if (this.failWith) throw this.failWith;
    this.open = true;
  }

  sample(reduce: FrameReducer): Float32Array | null {
    return this.open ? reduce(this.pixels, 4, 4) : null;
  }

  stop(): void {
    this.stops += 1;
    this.open = false;
  }

  label(): string | null {
    return this.open ? "Integrated Webcam" : null;
  }
}

function eyeStore(camera: Camera) {
  const durable: Array<{ kind: string; reason: string; identity: EyeActionIdentity | undefined }> = [];
  const store = new EyeStore({
    build: () => ({
      frameSource: camera,
      postObservation: async () => ({ status: "posted" as const }),
      sampleIntervalMs: 60_000,
      now: () => Date.parse(AT),
    }),
    durable: {
      enable: async (reason, identity) => {
        durable.push({ kind: "enable", reason, identity });
      },
      disable: async (reason, identity) => {
        durable.push({ kind: "disable", reason, identity });
      },
    },
  });
  return { store, durable };
}

describe("end to end: a provider function call opens and closes THIS device's camera through the one EyeStore", () => {
  it("eye.enable then eye.disable: camera first, relay with the real observation, reason voice:<utterance>", async () => {
    eyeInstances.reset();
    const camera = new Camera();
    const { store, durable } = eyeStore(camera);
    const t = await setup({}, eyeLocalActions(() => store));

    t.transport.emit({ type: "tool_call", at: 10, callId: "call-on", name: "eye.enable", arguments: { utterance: "Gözünü aç." } });
    await tick();
    expect(store.getSnapshot().state).toBe("ACTIVE");
    expect(camera.starts).toBe(1);
    // The durable call carries the action's identity: the ledger row names the same command as the receipt.
    expect(durable).toEqual([{ kind: "enable", reason: "voice:Gözünü aç.", identity: { action_id: "call-on", session_id: SESSION } }]);
    expect((t.relays()[0].body as { arguments: unknown }).arguments).toEqual({
      utterance: "Gözünü aç.",
      observed_after: {
        local: {
          state: "ACTIVE",
          running: true,
          camera_label: "Integrated Webcam",
          error_class: null,
          observed_at: AT,
          changed: true,
          media_track_ready_state: null, // the fake FrameSource holds no MediaStreamTrack
          action_trace: ["gen:1", "request:enable", "action:call-on", "getUserMedia:called", "loop:started", "durable:enable", "state:ENABLING->ACTIVE"],
        },
      },
    });

    // Already on: the second enable is idempotent locally and still relayed with the truth.
    t.transport.emit({ type: "tool_call", at: 20, callId: "call-on-2", name: "eye.enable", arguments: { utterance: "Kamerayı aç." } });
    await tick();
    expect(camera.starts).toBe(1);
    expect(durable).toHaveLength(1);
    const second = (t.relays()[1].body as { arguments: { observed_after: { local: { state: string; changed: boolean } } } }).arguments.observed_after.local;
    expect(second.state).toBe("ACTIVE");
    expect(second.changed).toBe(false); // the server reads this as "already" - no fake transition

    t.transport.emit({ type: "tool_call", at: 30, callId: "call-off", name: "eye.disable", arguments: { utterance: "Gözünü kapat." } });
    await tick();
    expect(store.getSnapshot().state).toBe("DISABLED");
    expect(camera.stops).toBe(1);
    expect(camera.open).toBe(false);
    expect(durable[1]).toEqual({ kind: "disable", reason: "voice:Gözünü kapat.", identity: { action_id: "call-off", session_id: SESSION } });
    expect((t.relays()[2].body as { arguments: unknown }).arguments).toEqual({
      utterance: "Gözünü kapat.",
      observed_after: {
        local: {
          state: "DISABLED",
          running: false,
          camera_label: null,
          error_class: null,
          observed_at: AT,
          changed: true,
          media_track_ready_state: null,
          action_trace: ["gen:2", "request:disable", "action:call-off", "durable:disable", "loop:stopped", "state:DISABLING->DISABLED"],
        },
      },
    });
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 1, loopsStarted: 1 });
    store.dispose();
  });

  it("the VENDOR spelling (eye__enable / eye__disable, as OpenAI Realtime delivers it) opens and closes the camera all the same", async () => {
    // The owner's run of 2026-09-06 (session 3eb6fee7): every eye call reached the
    // port as `eye__enable` / `eye__disable`, matched nothing, and the Cloud Core
    // recorded capability_missing. The port must see the Cloud Core spelling; the
    // relay must keep the vendor spelling the server's registry maps back.
    eyeInstances.reset();
    const camera = new Camera();
    const { store, durable } = eyeStore(camera);
    const t = await setup({}, eyeLocalActions(() => store));

    t.transport.emit({ type: "tool_call", at: 10, callId: "call-v-on", name: "eye__enable", arguments: { utterance: "Kamerayı aç." } });
    await tick();
    expect(store.getSnapshot().state).toBe("ACTIVE");
    expect(camera.starts).toBe(1);
    expect(durable).toEqual([{ kind: "enable", reason: "voice:Kamerayı aç.", identity: { action_id: "call-v-on", session_id: SESSION } }]);
    const on = t.relays()[0].body as { name: string; arguments: { observed_after: { local: { state: string } } } };
    expect(on.name).toBe("eye__enable"); // relayed verbatim
    expect(on.arguments.observed_after.local.state).toBe("ACTIVE");

    t.transport.emit({ type: "tool_call", at: 20, callId: "call-v-off", name: "eye__disable", arguments: { utterance: "Gözünü kapat." } });
    await tick();
    expect(store.getSnapshot().state).toBe("DISABLED");
    expect(camera.open).toBe(false);
    expect(durable[1]).toEqual({ kind: "disable", reason: "voice:Gözünü kapat.", identity: { action_id: "call-v-off", session_id: SESSION } });
    const off = t.relays()[1].body as { name: string; arguments: { observed_after: { local: { state: string; changed: boolean } } } };
    expect(off.name).toBe("eye__disable");
    expect(off.arguments.observed_after.local).toMatchObject({ state: "DISABLED", running: false, changed: true });
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 1, loopsStarted: 1 });
    store.dispose();
  });

  it("eye.enable when the browser refuses the camera: nothing durable, local.state ERROR permission_denied is relayed", async () => {
    const camera = new Camera();
    camera.failWith = new DOMException("Permission denied", "NotAllowedError");
    const { store, durable } = eyeStore(camera);
    const t = await setup({}, eyeLocalActions(() => store));
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-on", name: "eye.enable", arguments: { utterance: "Beni tekrar izle." } });
    await tick();
    expect(store.getSnapshot().state).toBe("ERROR");
    expect(durable).toEqual([]);
    expect((t.relays()[0].body as { arguments: unknown }).arguments).toEqual({
      utterance: "Beni tekrar izle.",
      // `changed: true` - the store did move (DISABLED -> ERROR); the server ignores it on
      // ERROR and answers `failed` with the permission sentence regardless.
      observed_after: {
        local: {
          state: "ERROR",
          running: false,
          camera_label: null,
          error_class: "permission_denied",
          observed_at: AT,
          changed: true,
          media_track_ready_state: null,
          action_trace: ["gen:1", "request:enable", "action:call-on", "getUserMedia:called", "getUserMedia:NotAllowedError", "state:ENABLING->ERROR"],
        },
      },
    });
    store.dispose();
  });
});

describe("the action's identity", () => {
  it("is read from the args the controller adds (call_id, session_id); anything else is null, so client.ts sends the body it always did", () => {
    expect(actionIdentity({ utterance: "Gözünü aç.", call_id: "call-1", session_id: SESSION })).toEqual({ action_id: "call-1", session_id: SESSION });
    expect(actionIdentity({ utterance: "Gözünü aç." })).toEqual({ action_id: null, session_id: null });
    expect(actionIdentity({ call_id: 7, session_id: null })).toEqual({ action_id: null, session_id: null });
  });
});

// ------------------------------------------------- the vendor spelling, pinned

describe("the provider's spelling of a tool name reaches the local port as the Cloud Core's", () => {
  const OBSERVED = {
    local: { state: "DISABLED", running: false, camera_label: null, error_class: null, observed_at: AT, changed: true },
  };

  it("eye__disable: the port is asked for eye.disable, the local action runs, observed_after is relayed under the vendor name", async () => {
    const seen: string[] = [];
    const port: LocalActionPort = {
      run: async (name) => {
        seen.push(name);
        return name === "eye.disable" ? OBSERVED : null;
      },
    };
    const receipt = { ...DISABLE_RECEIPT, action_id: "call-vd" };
    const t = await setup({ toolResponses: { eye__disable: { result: receipt } } }, port);
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-vd", name: "eye__disable", arguments: { utterance: "Gözünü kapat." } });
    await tick();
    expect(seen).toEqual(["eye.disable"]);
    expect(t.log).toContain("tool.local:call-vd");
    expect(t.relays()[0].body).toEqual({
      call_id: "call-vd",
      name: "eye__disable", // the relay keeps the vendor spelling; the server's registry maps it back
      arguments: { utterance: "Gözünü kapat.", observed_after: OBSERVED },
    });
    expect(t.transport.sent).toEqual([`submit:call-vd:${JSON.stringify(receipt)}`]);
  });

  it("eye__enable: likewise, and a port that only knows the Cloud Core spelling is enough", async () => {
    const seen: string[] = [];
    const port: LocalActionPort = {
      run: async (name) => {
        seen.push(name);
        if (name === "eye.enable") return { local: { ...OBSERVED.local, state: "ACTIVE", running: true, camera_label: "cam" } };
        return null;
      },
    };
    const t = await setup({}, port);
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-ve", name: "eye__enable", arguments: { utterance: "Gözünü aç." } });
    await tick();
    expect(seen).toEqual(["eye.enable"]);
    const body = t.relays()[0].body as { name: string; arguments: { observed_after?: { local: { state: string } } } };
    expect(body.name).toBe("eye__enable");
    expect(body.arguments.observed_after?.local.state).toBe("ACTIVE");
  });

  it("state__now has nothing local and is relayed under its vendor name without observed_after", async () => {
    const seen: string[] = [];
    const port: LocalActionPort = {
      run: async (name) => {
        seen.push(name);
        return null;
      },
    };
    const t = await setup({}, port);
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-sn", name: "state__now", arguments: { question: "Kamera açık mı?" } });
    await tick();
    expect(seen).toEqual(["state.now"]);
    expect(t.relays()[0].body).toEqual({ call_id: "call-sn", name: "state__now", arguments: { question: "Kamera açık mı?" } });
  });
});

describe("the shapes", () => {
  it("observed_after is exactly {local: {state, running, camera_label, error_class, observed_at, changed, media_track_ready_state, action_trace}} — the server honours `changed` only when its read-back agrees", () => {
    const trace = ["request:enable", "already:ACTIVE"];
    expect(
      observedAfter({
        state: "ACTIVE",
        running: true,
        camera_label: "cam",
        error_class: null,
        observed_at: AT,
        changed: false,
        media_track_ready_state: "live",
        action_trace: trace,
      }),
    ).toEqual({
      local: {
        state: "ACTIVE",
        running: true,
        camera_label: "cam",
        error_class: null,
        observed_at: AT,
        changed: false,
        media_track_ready_state: "live",
        action_trace: trace,
      },
    });
  });

  it("the durable reason is voice:<utterance>, trimmed and bounded to the server's 200 characters", () => {
    expect(voiceReason({ utterance: "  Gözünü kapat.  " })).toBe("voice:Gözünü kapat.");
    expect(voiceReason({})).toBe("voice:");
    expect(voiceReason({ utterance: 42 })).toBe("voice:");
    expect(voiceReason({ utterance: "a".repeat(500) })).toHaveLength(200);
  });
});

describe("a tool result that is a QUESTION (ADR-0077)", () => {
  it("needs_clarification reaches the provider as the result itself - the question to ask - never as a failure", async () => {
    // The owner's 2026-09-06 record: research.explain could not resolve which research
    // "OpenAI araştırmasını anlat" meant and returned one question. The relay must hand
    // the model that question, and the durable status the client reports is the
    // server's own word for it.
    const question = {
      status: "needs_clarification",
      speech: "Hangi araştırmayı kastediyorsunuz efendim?",
      research_job_id: null,
      routed: "research_reference",
    };
    const t = await setup({
      toolResponses: { "research.explain": { status: "needs_clarification", result: question } },
    });
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-q", name: "research.explain", arguments: { level: "technical" } });
    await tick();
    expect(t.transport.sent).toEqual([`submit:call-q:${JSON.stringify(question)}`]);
    expect(t.controller.getSnapshot().state).toBe("listening");
    await t.controller.flushEvents();
    expect(t.core.events.find((e) => e.kind === "tool_done")?.payload).toMatchObject({
      call_id: "call-q",
      name: "research.explain",
      status: "needs_clarification",
    });
  });

  it("a failed research answer (no report body, or an empty success the server refused) is still relayed as a failure with the server's sentence", async () => {
    const error = { error_class: "empty_result", speech: "Bu araştırmanın kayıtlı bir raporu yok efendim; anlatabileceğim bir sonuç bulamadım." };
    const t = await setup({ toolResponses: { "research.explain": { status: "failed", result: null, error } } });
    t.transport.emit({ type: "tool_call", at: 10, callId: "call-f", name: "research.explain", arguments: {} });
    await tick();
    expect(t.transport.sent).toEqual([`submit:call-f:${JSON.stringify({ status: "failed", error })}`]);
  });
});
