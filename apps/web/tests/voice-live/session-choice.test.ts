import { afterEach, describe, expect, it, vi } from "vitest";

import { LIVE_PROVIDER, VoiceSessionApi, preferProviderFrom } from "../../app/lib/voice/api";
import { type ControllerDeps, VoiceSessionController } from "../../app/lib/voice/controller";
import {
  type FakeCloudCoreOptions,
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";
import { BUNDLED_CONTRACT, type ContractDocument } from "../../app/lib/voice/session-contract";

/**
 * Per-session provider choice for the GPT-Live measurement: `?ses=live` on the page
 * asks the Cloud Core for `prefer_provider: 'openai-live'`; without it the create body
 * is byte-for-byte what it was before this change (no key at all).
 */

const tick = async (rounds = 4): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

/** A server whose contract already knows the field (the gpt-live-provider card exports it). */
function contractWithPreferProvider(): ContractDocument {
  const doc = structuredClone(BUNDLED_CONTRACT);
  doc.requests.create_session.properties.prefer_provider = {
    anyOf: [{ type: "string", pattern: "^[a-z][a-z0-9-]{0,31}$" }, { type: "null" }],
    default: null,
  };
  return doc;
}

async function open(options: FakeCloudCoreOptions, extra: Partial<ControllerDeps> = {}) {
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc", ...options });
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => new FakeTransport({ now: scheduler.now }),
    playback: new FakePlayback(scheduler.now, () => {}),
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    ...extra,
  });
  await controller.connect();
  await tick();
  const create = core.requests.find((r) => r.method === "POST" && r.path === "/v1/voice/realtime/sessions");
  return { controller, core, body: create?.body as Record<string, unknown> };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("?ses=live", () => {
  it("parses only ses=live into the live provider", () => {
    expect(LIVE_PROVIDER).toBe("openai-live");
    expect(preferProviderFrom("?ses=live")).toBe("openai-live");
    expect(preferProviderFrom("?x=1&ses=live")).toBe("openai-live");
    expect(preferProviderFrom("ses=live")).toBe("openai-live");
    expect(preferProviderFrom("")).toBeNull();
    expect(preferProviderFrom("?ses=")).toBeNull();
    expect(preferProviderFrom("?ses=openai-realtime")).toBeNull();
    expect(preferProviderFrom(undefined)).toBeNull();
    expect(preferProviderFrom(null)).toBeNull();
  });

  it("with ?ses=live the create body carries prefer_provider: 'openai-live'", async () => {
    const { body } = await open({ contract: contractWithPreferProvider() }, { preferProvider: () => "openai-live" });
    expect(body).toEqual({ client_kind: "web", prefer_provider: "openai-live" });
  });

  it("the page address is read when the deps say nothing (the shell's path)", async () => {
    vi.stubGlobal("location", { search: "?ses=live" });
    const { body } = await open({ contract: contractWithPreferProvider() });
    expect(body).toEqual({ client_kind: "web", prefer_provider: "openai-live" });
  });

  it("without the parameter the body is exactly today's: no prefer_provider key at all", async () => {
    const injected = await open({ contract: contractWithPreferProvider() }, { preferProvider: () => null });
    expect(injected.body).toEqual({ client_kind: "web" });
    expect(Object.keys(injected.body)).not.toContain("prefer_provider");

    vi.stubGlobal("location", { search: "" });
    const fromPage = await open({ contract: contractWithPreferProvider() });
    expect(fromPage.body).toEqual({ client_kind: "web" });
    expect(fromPage.controller.getSnapshot().contractNotice ?? "").not.toContain("prefer_provider");
  });

  it("a server that does not know the field opens the current provider and the shell says which one", async () => {
    const { body, controller } = await open({ provider: "openai-realtime" }, { preferProvider: () => "openai-live" });
    expect(body).toEqual({ client_kind: "web" });
    const snapshot = controller.getSnapshot();
    expect(snapshot.state).toBe("listening");
    expect(snapshot.provider).toBe("openai-realtime");
    expect(snapshot.contractNotice).toContain("prefer_provider");
    expect(snapshot.contractNotice).toContain("istenen sağlayıcı openai-live, açılan openai-realtime");
  });

  it("when the server grants it, the notice says nothing about a mismatch", async () => {
    const { controller } = await open(
      { contract: contractWithPreferProvider(), provider: "openai-live" },
      { preferProvider: () => "openai-live" },
    );
    expect(controller.getSnapshot().provider).toBe("openai-live");
    expect(controller.getSnapshot().contractNotice ?? "").not.toContain("istenen sağlayıcı");
  });
});
