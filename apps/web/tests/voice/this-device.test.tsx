/**
 * ADR-0208: the browser says which enrolled computer it is on.
 *
 * The owner sits at the office PC, opens the web shell and says "hesap makinesini aç". Cloud
 * Core has two computers to choose from and no way to know which one he is at - unless the
 * browser tells it. What is pinned here is that it does, and only when it safely can:
 * `device_id` goes out on create and on re-attach to a server that speaks contract v3, and
 * NEVER to an older one (`extra="forbid"` would turn a network blip into a dead session).
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ThisDeviceView } from "../../app/voice/ThisDevice";
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
import { MemoryProfileStore } from "../../app/lib/voice/profile";
import { createVoiceRig } from "../../app/lib/voice/rig";
import { BUNDLED_CONTRACT, type ContractDocument } from "../../app/lib/voice/session-contract";
import { type DeviceStorage, THIS_DEVICE_KEY, getThisDeviceId, setThisDeviceId } from "../../app/lib/thisDevice";

const OFFICE = "9efa9d8b-b0e6-4758-a03a-387c3e20a0d2";
const HOME = "3f60fdb5-5022-48cf-bb3c-d7192466b701";

const tick = async (rounds = 6): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

class MemoryStorage implements DeviceStorage {
  readonly map = new Map<string, string>();
  getItem(key: string): string | null {
    return this.map.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
  removeItem(key: string): void {
    this.map.delete(key);
  }
}

const throwingStorage: DeviceStorage = {
  getItem() {
    throw new Error("storage disabled");
  },
  setItem() {
    throw new Error("storage disabled");
  },
  removeItem() {
    throw new Error("storage disabled");
  },
};

// ------------------------------------------------------------------ the memory

describe("lib/thisDevice", () => {
  it("remembers a device id and forgets it", () => {
    const storage = new MemoryStorage();
    expect(getThisDeviceId(storage)).toBeNull();
    expect(setThisDeviceId(OFFICE, storage)).toBe(true);
    expect(getThisDeviceId(storage)).toBe(OFFICE);
    expect(setThisDeviceId(null, storage)).toBe(true);
    expect(getThisDeviceId(storage)).toBeNull();
  });

  it("stores only a UUID, in lower case", () => {
    const storage = new MemoryStorage();
    expect(setThisDeviceId("ofis", storage)).toBe(false);
    expect(setThisDeviceId("'; drop table devices;--", storage)).toBe(false);
    expect(storage.map.size).toBe(0);
    expect(setThisDeviceId(OFFICE.toUpperCase(), storage)).toBe(true);
    expect(storage.map.get(THIS_DEVICE_KEY)).toBe(OFFICE);
  });

  it("reads back nothing when what is stored is not a UUID", () => {
    const storage = new MemoryStorage();
    storage.setItem(THIS_DEVICE_KEY, "ofis");
    expect(getThisDeviceId(storage)).toBeNull();
  });

  it("degrades to 'not said' when storage is unavailable or throws", () => {
    expect(getThisDeviceId(null)).toBeNull();
    expect(setThisDeviceId(OFFICE, null)).toBe(false);
    expect(getThisDeviceId(throwingStorage)).toBeNull();
    expect(setThisDeviceId(OFFICE, throwingStorage)).toBe(false);
  });
});

// ------------------------------------------------------------ the controller

function rig(options: FakeCloudCoreOptions, enrolledDeviceId: (() => string | null) | undefined) {
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc", ...options });
  const network = new FakeNetwork();
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => new FakeTransport({ now: scheduler.now }),
    playback: new FakePlayback(scheduler.now, () => {}),
    network,
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    enrolledDeviceId,
  });
  return { controller, core, network, scheduler };
}

const createBodies = (core: FakeCloudCore) =>
  core.requests.filter((r) => r.method === "POST" && r.path === "/v1/voice/realtime/sessions").map((r) => r.body);
const attachBodies = (core: FakeCloudCore) =>
  core.requests.filter((r) => r.method === "POST" && r.path.endsWith("/attach")).map((r) => r.body);

/** What a Cloud Core one contract version older serves: no `device_id` anywhere. */
function withoutDeviceId(name: "create_session" | "attach") {
  const { device_id: _dropped, ...rest } = BUNDLED_CONTRACT.requests[name].properties;
  return { ...BUNDLED_CONTRACT.requests[name], properties: rest };
}

function v2Document(): ContractDocument {
  const strip = withoutDeviceId;
  return {
    ...BUNDLED_CONTRACT,
    contract_version: 2,
    requests: { ...BUNDLED_CONTRACT.requests, create_session: strip("create_session"), attach: strip("attach") },
  };
}

describe("the controller declares the computer it is on", () => {
  it("sends device_id on create to a v3 server", async () => {
    const t = rig({}, () => OFFICE);
    await t.controller.connect({ language: "tr-TR" });
    await tick();
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(createBodies(t.core)).toEqual([{ client_kind: "web", language: "tr-TR", device_id: OFFICE }]);
    expect(t.controller.getSnapshot().contractNotice).toBeNull();
  });

  it("sends nothing new when the owner has not said (the request is what it always was)", async () => {
    for (const dep of [undefined, () => null]) {
      const t = rig({}, dep);
      await t.controller.connect({ language: "tr-TR" });
      await tick();
      expect(createBodies(t.core)).toEqual([{ client_kind: "web", language: "tr-TR" }]);
    }
  });

  it("leaves device_id out for a v2 server and says so in Turkish, the session still opens", async () => {
    const t = rig({ contract: v2Document() }, () => OFFICE);
    await t.controller.connect({ language: "tr-TR" });
    await tick();
    const snapshot = t.controller.getSnapshot();
    expect(snapshot.state).toBe("listening");
    expect(createBodies(t.core)).toEqual([{ client_kind: "web", language: "tr-TR" }]);
    expect(snapshot.contractNotice).toContain("'device_id' alanı bu sürümde yok; gönderilmedi");
  });

  it("leaves device_id out for a v1 server (404 on the contract route)", async () => {
    const t = rig({ contract: "legacy", legacyCreate: true }, () => OFFICE);
    await t.controller.connect({ language: "tr-TR" });
    await tick();
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(createBodies(t.core)).toEqual([{ client_kind: "web", language: "tr-TR" }]);
  });

  it("re-attaches with device_id on a v3 server: the leg that takes over says where it runs", async () => {
    const t = rig({}, () => OFFICE);
    await t.controller.connect({ language: "tr-TR" });
    await tick();
    t.network.set(false);
    t.scheduler.advance(2000);
    t.network.set(true);
    await tick(12);
    const attaches = attachBodies(t.core);
    expect(attaches.length).toBeGreaterThan(0);
    for (const body of attaches) expect(body).toMatchObject({ client_kind: "web", device_id: OFFICE });
  });

  it("never puts device_id on an attach to a v2 server (it would refuse the field)", async () => {
    const t = rig({ contract: v2Document() }, () => OFFICE);
    await t.controller.connect({ language: "tr-TR" });
    await tick();
    t.network.set(false);
    t.scheduler.advance(2000);
    t.network.set(true);
    await tick(12);
    const attaches = attachBodies(t.core);
    expect(attaches.length).toBeGreaterThan(0);
    for (const body of attaches) expect(body).not.toHaveProperty("device_id");
  });

  it("never puts device_id on an attach when the server's version was never learned", async () => {
    const t = rig({ contract: "network" }, () => OFFICE);
    await t.controller.connect({ language: "tr-TR" });
    await tick();
    // the unknown-version path creates with the bundled contract (v3) but has not CONFIRMED it
    t.network.set(false);
    t.scheduler.advance(2000);
    t.network.set(true);
    await tick(12);
    for (const body of attachBodies(t.core)) expect(body).not.toHaveProperty("device_id");
  });
});

describe("the rig hands the browser's answer to the controller", () => {
  it("a rig whose parts know the enrolled device creates its sessions with device_id", async () => {
    const scheduler = new FakeScheduler();
    const core = new FakeCloudCore({ transport: "webrtc" });
    const voiceRig = createVoiceRig(() => ({
      api: new VoiceSessionApi(core.fetcher),
      profiles: new MemoryProfileStore(),
      playback: new FakePlayback(scheduler.now),
      microphone: new FakeMicrophone(),
      detector: new FakeSpeechDetector(),
      network: new FakeNetwork(),
      transportFor: () => new FakeTransport({ now: scheduler.now }),
      now: scheduler.now,
      enrolledDeviceId: () => OFFICE,
    }));
    await voiceRig.controller.connect({ language: "tr-TR" });
    await tick();
    expect(createBodies(core)).toEqual([{ client_kind: "web", language: "tr-TR", device_id: OFFICE }]);
    voiceRig.dispose();
  });
});

// ---------------------------------------------------------------- the picker

describe("ThisDeviceView", () => {
  const devices = [
    { device_id: HOME, name: "MAIL", presence: "offline", aliases: ["ev"] },
    { device_id: OFFICE, name: "GMKADIRAKBABA", presence: "online", aliases: ["ofis", "iş"] },
    { device_id: "11111111-2222-4333-8444-555555555555", name: "ESKI", status: "revoked" },
  ];

  it("offers 'Belirtilmedi' and every device that is not revoked, with its presence", () => {
    const html = renderToStaticMarkup(<ThisDeviceView devices={devices} value="" onChange={() => {}} />);
    expect(html).toContain("Bu bilgisayar");
    expect(html).toContain("Belirtilmedi");
    expect(html).toContain("MAIL (çevrimdışı)");
    expect(html).toContain("GMKADIRAKBABA (çevrimiçi)");
    expect(html).not.toContain("ESKI");
    expect(html).toContain('data-this-device="none"');
    expect(html).toContain("hesap makinesini aç");
  });

  it("shows the remembered device as chosen", () => {
    const html = renderToStaticMarkup(<ThisDeviceView devices={devices} value={OFFICE} onChange={() => {}} />);
    expect(html).toContain(`data-this-device="${OFFICE}"`);
    expect(html).toMatch(new RegExp(`<option value="${OFFICE}" selected`));
  });

  it("keeps a remembered device that is no longer listed visible instead of reading 'Belirtilmedi'", () => {
    const html = renderToStaticMarkup(<ThisDeviceView devices={[devices[0]]} value={OFFICE} onChange={() => {}} />);
    expect(html).toContain("Kayıtlı cihaz listede yok");
  });

  it("says when the list could not be loaded", () => {
    const html = renderToStaticMarkup(<ThisDeviceView devices={[]} value="" onChange={() => {}} loadError="HTTP 503" />);
    expect(html).toContain("Cihaz listesi alınamadı: HTTP 503");
  });
});
