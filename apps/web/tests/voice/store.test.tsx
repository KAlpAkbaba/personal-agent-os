/**
 * The one-session property (ADR-0061 §1).
 *
 * `/core`, `/core/cockpit` and `/voice` all read the tab's voice session
 * through `VoiceStore`. What must be true, and is asserted here with the
 * instance registry rather than assumed: however many views subscribe, and
 * however often one of them mounts and unmounts, there is exactly ONE rig,
 * ONE `VoiceSessionController`, ONE microphone open (one `getUserMedia`), ONE
 * transport and ONE realtime session.
 *
 * The consumers are exercised the way `useSyncExternalStore` drives the hook
 * — `subscribe` in an effect, `getSnapshot` on every render — because there is
 * no browser here to mount React in. The server path (`getServerSnapshot`) is
 * asserted through `react-dom/server` to build nothing at all.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";
import { MemoryProfileStore } from "../../app/lib/voice/profile";
import { type VoiceRigBuilder, voiceInstances } from "../../app/lib/voice/rig";
import { VoiceStore, getVoiceStore, installVoiceStore } from "../../app/lib/voice/store";
import { useVoiceSession } from "../../app/lib/voice/useVoiceSession";

const tick = async (rounds = 6): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

class MemoryStorage {
  private items = new Map<string, string>();
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
}

function fakeRig() {
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
  const microphone = new FakeMicrophone();
  const playback = new FakePlayback(scheduler.now);
  const transports: FakeTransport[] = [];
  let builds = 0;
  const build: VoiceRigBuilder = () => {
    builds += 1;
    return {
      api: new VoiceSessionApi(core.fetcher),
      profiles: new MemoryProfileStore(),
      playback,
      microphone,
      detector: new FakeSpeechDetector(),
      network: new FakeNetwork(),
      transportFor: () => {
        const transport = new FakeTransport({ now: scheduler.now });
        transports.push(transport);
        return transport;
      },
      now: scheduler.now,
    };
  };
  /** A fake `window` for `pagehide`: the test fires it the way a reload would. */
  const hideListeners = new Set<() => void>();
  const unload = {
    addEventListener: (_type: "pagehide", listener: () => void) => void hideListeners.add(listener),
    removeEventListener: (_type: "pagehide", listener: () => void) => void hideListeners.delete(listener),
    fire: () => hideListeners.forEach((l) => l()),
    get listeners() {
      return hideListeners.size;
    },
  };
  const store = new VoiceStore({
    build,
    listDevices: async () => [
      { deviceId: "mic-1", kind: "audioinput", label: "Masa mikrofonu", groupId: "g1" },
      { deviceId: "spk-1", kind: "audiooutput", label: "Hoparlör", groupId: "g1" },
    ],
    storage: new MemoryStorage(),
    unloadTarget: unload,
  });
  return {
    store,
    core,
    unload,
    microphone,
    playback,
    transports,
    get builds() {
      return builds;
    },
    sessionsCreated: () =>
      core.requests.filter((r) => r.method === "POST" && r.path === "/v1/voice/realtime/sessions").length,
  };
}

/** A consumer exactly like the Core's: one hook call, one span. */
function Probe() {
  const { voice } = useVoiceSession();
  return <span data-ready={voice.ready ? "yes" : "no"}>{voice.controller.state}</span>;
}

beforeEach(() => {
  voiceInstances.reset();
  installVoiceStore(null);
});

describe("one controller per tab, however many views read it", () => {
  it("a second consumer and a remount subscribe to the same controller; nothing is built twice", () => {
    const t = fakeRig();
    // First view mounts: render (getSnapshot), then effect (subscribe).
    const first = t.store.getSnapshot();
    const unsubA = t.store.subscribe(() => {});
    // Second view (the cockpit next to the core, or /voice in another route).
    t.store.getSnapshot();
    const unsubB = t.store.subscribe(() => {});
    // The first view unmounts and mounts again.
    unsubA();
    t.store.getSnapshot();
    const unsubC = t.store.subscribe(() => {});

    expect(t.builds).toBe(1);
    expect(voiceInstances.snapshot()).toMatchObject({ rigs: 1, controllers: 1 });
    const rig = t.store.peekRig();
    expect(rig).not.toBeNull();
    // The snapshot each consumer reads is the same controller's.
    expect(t.store.getSnapshot().controller).toBe(rig?.controller.getSnapshot());
    expect(first.ready).toBe(true);
    unsubB();
    unsubC();
  });

  it("getSnapshot is referentially stable until something changes (the useSyncExternalStore contract)", () => {
    const t = fakeRig();
    const a = t.store.getSnapshot();
    const b = t.store.getSnapshot();
    expect(a).toBe(b);
    t.store.setVoice("cedar");
    const c = t.store.getSnapshot();
    expect(c).not.toBe(a);
    expect(c.voice).toBe("cedar");
  });

  it("connecting from two consumers at once opens ONE microphone, ONE transport and ONE session", async () => {
    const t = fakeRig();
    t.store.subscribe(() => {});
    t.store.subscribe(() => {});
    // Both views fire connect on the same gesture (e.g. a stale double click).
    await Promise.all([t.store.connect(), t.store.connect()]);
    await tick();

    expect(t.store.getSnapshot().controller.state).toBe("listening");
    expect(t.microphone.opened).toHaveLength(1);
    expect(t.transports).toHaveLength(1);
    expect(t.sessionsCreated()).toBe(1);
    expect(voiceInstances.snapshot()).toEqual({
      rigs: 1,
      controllers: 1,
      microphonesOpened: 1,
      transports: 1,
      sessionsCreated: 1,
    });
  });

  it("a connect while a leg is live is a no-op, and a remount after connect does not reopen the microphone", async () => {
    const t = fakeRig();
    const unsub = t.store.subscribe(() => {});
    await t.store.connect();
    await tick();
    unsub();
    // The view is gone; the session is not.
    expect(t.store.getSnapshot().controller.state).toBe("listening");
    t.store.subscribe(() => {});
    await t.store.connect();
    await tick();
    expect(t.microphone.opened).toHaveLength(1);
    expect(t.sessionsCreated()).toBe(1);
    expect(voiceInstances.snapshot().controllers).toBe(1);
  });

  it("reconnect closes the open leg and opens exactly one new session on the same controller", async () => {
    const t = fakeRig();
    t.store.subscribe(() => {});
    await t.store.connect();
    await tick();
    const controller = t.store.peekRig()?.controller;
    await t.store.reconnect();
    await tick();
    expect(t.store.getSnapshot().controller.state).toBe("listening");
    expect(t.sessionsCreated()).toBe(2);
    expect(t.microphone.opened).toHaveLength(2);
    expect(voiceInstances.snapshot().controllers).toBe(1);
    expect(t.store.peekRig()?.controller).toBe(controller);
  });

  it("disconnect leaves the controller in place for the next connect: closed, then listening again", async () => {
    const t = fakeRig();
    t.store.subscribe(() => {});
    await t.store.connect();
    await tick();
    await t.store.disconnect();
    expect(t.store.getSnapshot().controller.state).toBe("closed");
    expect(t.core.closed).toBe("client_closed");
    await t.store.connect();
    await tick();
    expect(t.store.getSnapshot().controller.state).toBe("listening");
    expect(voiceInstances.snapshot().controllers).toBe(1);
  });
});

describe("the page going away ends the session on the Cloud Core", () => {
  const closes = (t: ReturnType<typeof fakeRig>) =>
    t.core.requests.filter((r) => r.method === "POST" && /\/v1\/voice\/realtime\/sessions\/[^/]+\/close$/.test(r.path));

  it("pagehide with a live session posts a keepalive close for THAT session (owner reload, 2026-09-06: session a2ac0716 stayed active)", async () => {
    const t = fakeRig();
    t.store.subscribe(() => {});
    expect(t.unload.listeners).toBe(1); // one rig, one listener
    await t.store.connect();
    await tick();
    const id = t.store.getSnapshot().controller.sessionId;
    expect(id).toBeTruthy();

    t.unload.fire();
    await tick();
    expect(closes(t)).toHaveLength(1);
    expect(closes(t)[0].path).toBe(`/v1/voice/realtime/sessions/${id}/close`);
    expect(closes(t)[0].body).toEqual({ reason: "page_unload" });
  });

  it("pagehide with no live session posts nothing, and after a disconnect nothing either", async () => {
    const t = fakeRig();
    t.store.subscribe(() => {});
    t.unload.fire();
    await tick();
    expect(closes(t)).toHaveLength(0);

    await t.store.connect();
    await tick();
    await t.store.disconnect();
    await tick();
    const before = closes(t).length; // the disconnect's own close
    t.unload.fire();
    await tick();
    expect(closes(t)).toHaveLength(before);
  });
});

describe("the module singleton", () => {
  it("getVoiceStore returns the installed store, and the same one every time", () => {
    const t = fakeRig();
    installVoiceStore(t.store);
    expect(getVoiceStore()).toBe(t.store);
    expect(getVoiceStore()).toBe(getVoiceStore());
  });

  it("the server render path builds nothing: useVoiceSession on the server reads the server snapshot", () => {
    const t = fakeRig();
    installVoiceStore(t.store);
    const html = renderToStaticMarkup(<Probe />);
    expect(html).toContain('data-ready="no"');
    expect(html).toContain("idle");
    expect(t.builds).toBe(0);
    expect(voiceInstances.snapshot()).toEqual({
      rigs: 0,
      controllers: 0,
      microphonesOpened: 0,
      transports: 0,
      sessionsCreated: 0,
    });
  });
});

describe("what the store reports", () => {
  it("device selection resolves a profile and names the microphone", async () => {
    const t = fakeRig();
    t.store.subscribe(() => {});
    await t.store.refreshDevices();
    await t.store.setMicrophone("mic-1");
    const snap = t.store.getSnapshot();
    expect(snap.micId).toBe("mic-1");
    expect(snap.profile?.friendlyName).toBe("Masa mikrofonu");
    expect(snap.profile?.environmentMode).toBe("auto");
    t.store.setNoiseMode("noisy");
    expect(t.store.getSnapshot().profile?.environmentMode).toBe("noisy");
  });

  it("the output level is the playback's own measurement: null before a path exists, 0 when silent", () => {
    const t = fakeRig();
    t.store.subscribe(() => {});
    t.playback.level = null;
    expect(t.store.outputLevel()).toBeNull();
    t.playback.level = 0.4;
    // Not playing: the analyser would read silence, and so does the port.
    expect(t.store.outputLevel()).toBe(0);
    t.playback.arm();
    expect(t.store.outputLevel()).toBe(0.4);
    t.playback.stop();
    expect(t.store.outputLevel()).toBe(0);
  });
});
