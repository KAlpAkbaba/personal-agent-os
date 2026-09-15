/**
 * B20 req 215/216/217: the input controls do what their labels say.
 *
 * All three were filed against `apps/web/` as "provider-side" or "missing", and the
 * measurement was out of date — the read-back of what the browser applied (ADR-0047 §4),
 * the measured echo residual, the per-device profile and a four-option sensitivity
 * selector all existed. What did NOT exist is the part that makes a control a control:
 *
 * - suppression offered "otomatik / tarayıcı / kapalı" and computed `!== "off"`, so two of
 *   the three options were the same behaviour and "otomatik" followed no measurement;
 * - sensitivity offered "otomatik / düşük / normal / yüksek" and derived byte-identical
 *   gate parameters for the first and the third;
 * - and every one of these choices reached the capture only at the NEXT `getUserMedia`, so
 *   an owner changing suppression mid-conversation changed nothing they could hear.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { BrowserMicrophone } from "../../app/lib/voice/audio";
import { deriveGateParameters } from "../../app/lib/voice/calibration";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";
import {
  MemoryProfileStore,
  type MicrophoneProfile,
  constraintsFor,
  defaultProfile,
  suppressionDecision,
} from "../../app/lib/voice/profile";
import type { VoiceRigBuilder } from "../../app/lib/voice/rig";
import { VoiceStore } from "../../app/lib/voice/store";

const tick = async (rounds = 6): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

const device = { deviceId: "abc123", label: "K66 (USB Audio)", groupId: "grp-k66" };

function profileWith(patch: Partial<MicrophoneProfile>): MicrophoneProfile {
  return { ...defaultProfile(device), ...patch };
}

describe("req 215: suppression follows the measurement under auto", () => {
  it("keeps suppression on for a device that has measured nothing", () => {
    // Silence is not evidence of quiet. A fresh profile keeps the safe default.
    const profile = profileWith({ noiseSuppressionMode: "auto", measuredNoiseFloorDb: null });

    expect(suppressionDecision(profile)).toEqual({ suppress: true, basis: "unmeasured" });
    expect(constraintsFor(profile).noiseSuppression).toBe(true);
  });

  it("turns suppression off once the device has measured a quiet room", () => {
    const profile = profileWith({ noiseSuppressionMode: "auto", measuredNoiseFloorDb: -64 });

    expect(suppressionDecision(profile)).toEqual({ suppress: false, basis: "measured" });
    const constraints = constraintsFor(profile);
    expect(constraints.noiseSuppression).toBe(false);
    // voiceIsolation follows suppression: it is the same trade, harder.
    expect(constraints.voiceIsolation).toBe(false);
    // Echo cancellation does NOT follow it. The assistant's own voice is in the room
    // whatever the room's floor is (req 216).
    expect(constraints.echoCancellation).toBe(true);
  });

  it("keeps suppression on in a room that measured normal or worse", () => {
    for (const floor of [-59, -48, -30]) {
      const profile = profileWith({ noiseSuppressionMode: "auto", measuredNoiseFloorDb: floor });
      expect(suppressionDecision(profile).suppress).toBe(true);
    }
  });

  it("lets the owner's declared environment beat the measurement, in both directions", () => {
    const quiet = profileWith({
      noiseSuppressionMode: "auto",
      environmentMode: "quiet",
      measuredNoiseFloorDb: -30,
    });
    expect(suppressionDecision(quiet)).toEqual({ suppress: false, basis: "owner" });

    const noisy = profileWith({
      noiseSuppressionMode: "auto",
      environmentMode: "noisy",
      measuredNoiseFloorDb: -70,
    });
    expect(suppressionDecision(noisy)).toEqual({ suppress: true, basis: "owner" });
  });

  it("gives the two explicit options their own behaviour, whatever the room measured", () => {
    const quietRoom = { measuredNoiseFloorDb: -70, environmentMode: "quiet" as const };
    expect(suppressionDecision(profileWith({ ...quietRoom, noiseSuppressionMode: "browser" }))).toEqual({
      suppress: true,
      basis: "forced",
    });
    const noisyRoom = { measuredNoiseFloorDb: -20, environmentMode: "very_noisy" as const };
    expect(suppressionDecision(profileWith({ ...noisyRoom, noiseSuppressionMode: "off" }))).toEqual({
      suppress: false,
      basis: "forced",
    });
  });

  it("no two of the three options are the same behaviour in the same room", () => {
    // The defect this requirement is really about: a menu whose options collapse. In the
    // one room where auto has an opinion of its own, the three answers must be three.
    const room = { measuredNoiseFloorDb: -64 };
    const auto = suppressionDecision(profileWith({ ...room, noiseSuppressionMode: "auto" }));
    const browser = suppressionDecision(profileWith({ ...room, noiseSuppressionMode: "browser" }));
    const off = suppressionDecision(profileWith({ ...room, noiseSuppressionMode: "off" }));
    expect(auto.basis).toBe("measured");
    expect(browser.suppress).toBe(true);
    expect(off.suppress).toBe(false);
    expect(auto.suppress).not.toBe(browser.suppress);
  });
});

describe("req 217: the sensitivity modes are four modes", () => {
  // Inside ADAPTATION_LIMITS, so the bounds are not what this test is measuring.
  const learned = { marginDb: 3, onsetMs: 30, echoMarginDb: 2 };
  const derive = (sensitivity: "auto" | "low" | "normal" | "high") =>
    deriveGateParameters({ calibration: null, mode: "auto", sensitivity, adaptation: learned });

  it("'normal' means the standard: what this device learned is set aside", () => {
    const auto = derive("auto");
    const normal = derive("normal");

    expect(auto.openMarginDb).toBe(normal.openMarginDb + learned.marginDb);
    expect(auto.minOnsetMs).toBe(normal.minOnsetMs + learned.onsetMs);
    expect(normal).not.toEqual(auto);
  });

  it("low and high still move the threshold, and every mode is distinguishable", () => {
    const modes = (["auto", "low", "normal", "high"] as const).map((s) => derive(s).openMarginDb);
    expect(new Set(modes).size).toBe(4);
    expect(derive("low").openMarginDb).toBeGreaterThan(derive("auto").openMarginDb);
    expect(derive("high").openMarginDb).toBeLessThan(derive("auto").openMarginDb);
  });

  it("a device that has learned nothing is unchanged by 'normal'", () => {
    const none = { marginDb: 0, onsetMs: 0, echoMarginDb: 0 };
    const auto = deriveGateParameters({ calibration: null, mode: "auto", sensitivity: "auto", adaptation: none });
    const normal = deriveGateParameters({ calibration: null, mode: "auto", sensitivity: "normal", adaptation: none });
    expect(normal).toEqual(auto);
  });
});

describe("req 215/216: a preference reaches the capture that is already open", () => {
  it("applies to the live track and answers with the read-back, not with the request", async () => {
    const mic = new FakeMicrophone();
    await mic.open("dev-1");
    expect(mic.applied?.noiseSuppression).toBe(true);

    const readBack = await mic.applyLive({ noiseSuppression: false, voiceIsolation: false });

    expect(readBack?.noiseSuppression).toBe(false);
    expect(mic.applied?.noiseSuppression).toBe(false);
    expect(mic.liveApplies).toHaveLength(1);
  });

  it("reports a browser that accepted the call and changed nothing", async () => {
    // The reason this answers with a read-back at all: `applyConstraints` resolving is not
    // evidence that anything changed.
    const mic = new FakeMicrophone();
    mic.refuses.add("noiseSuppression");
    await mic.open("dev-1");

    const readBack = await mic.applyLive({ noiseSuppression: false });

    expect(readBack?.noiseSuppression).toBe(true);
    expect(readBack?.notHonoured).toContain("noiseSuppression");
  });

  it("has nothing to apply to before the microphone is open", async () => {
    const mic = new FakeMicrophone();
    expect(await mic.applyLive({ noiseSuppression: false })).toBeNull();
  });
});

// ------------------------------------------------------------- the wiring

/**
 * The half that is easiest to leave out and impossible to notice: a port that can apply a
 * preference live, a profile that stores one, and nothing calling from one to the other.
 * This drives the real `VoiceStore` and the real `createVoiceRig` over the fakes.
 */
describe("req 215: the owner's change reaches the capture during the conversation", () => {
  async function liveStore() {
    const scheduler = new FakeScheduler();
    const core = new FakeCloudCore({ transport: "webrtc" });
    const microphone = new FakeMicrophone();
    const build: VoiceRigBuilder = () => ({
      api: new VoiceSessionApi(core.fetcher),
      profiles: new MemoryProfileStore(),
      playback: new FakePlayback(scheduler.now),
      microphone,
      detector: new FakeSpeechDetector(),
      network: new FakeNetwork(),
      transportFor: () => new FakeTransport({ now: scheduler.now }),
      now: scheduler.now,
    });
    const store = new VoiceStore({ build, listDevices: async () => [], storage: null, unloadTarget: null });
    await store.connect();
    await tick();
    return { store, microphone, core };
  }

  it("a suppression change mid-session lands on the open track", async () => {
    const t = await liveStore();
    expect(t.microphone.liveApplies).toHaveLength(0);

    t.store.patchProfile({ noiseSuppressionMode: "off" });
    await tick();

    expect(t.microphone.liveApplies).toHaveLength(1);
    expect(t.microphone.liveApplies[0]).toMatchObject({ noiseSuppression: false, voiceIsolation: false });
    expect(t.microphone.applied?.noiseSuppression).toBe(false);
  });

  it("stores what the browser applied, so the profile carries the measurement and not the wish", async () => {
    const t = await liveStore();
    t.microphone.refuses.add("noiseSuppression");

    t.store.patchProfile({ noiseSuppressionMode: "off" });
    await tick();

    const profile = t.store.getSnapshot().profile;
    expect(profile?.noiseSuppressionMode).toBe("off");
    expect(profile?.appliedSettings?.noiseSuppression).toBe(true);
    expect(profile?.appliedSettings?.notHonoured).toContain("noiseSuppression");
  });

  it("does not renegotiate the capture for a preference the capture does not carry", async () => {
    // Sensitivity is the local gate's business; re-applying constraints for it would be a
    // media renegotiation for nothing.
    const t = await liveStore();

    t.store.patchProfile({ preferredVadSensitivity: "high" });
    await tick();

    expect(t.microphone.liveApplies).toHaveLength(0);
  });
});

// --------------------------------------------------- the real browser microphone

/**
 * A `MediaStreamTrack` the way `applyConstraints` really behaves: the promise resolving is
 * not a promise that anything changed. `honours` lists the keys this stub actually moves.
 */
function stubTrack(initial: Record<string, unknown>, honours: string[]) {
  const settings = { ...initial };
  return {
    settings,
    applied: [] as Array<Record<string, unknown>>,
    kind: "audio",
    label: "Stub microphone",
    readyState: "live",
    muted: false,
    getSettings: () => ({ ...settings }),
    getCapabilities: () => ({ noiseSuppression: [true, false] }),
    async applyConstraints(constraints: Record<string, unknown>) {
      this.applied.push({ ...constraints });
      for (const [key, value] of Object.entries(constraints)) {
        if (honours.includes(key)) settings[key] = value;
      }
    },
    addEventListener() {},
    removeEventListener() {},
    stop() {},
  };
}

function stubNavigator(track: ReturnType<typeof stubTrack>, supported: Record<string, boolean>) {
  const stream = { id: "stub", getAudioTracks: () => [track], getTracks: () => [track] };
  vi.stubGlobal("navigator", {
    mediaDevices: {
      getSupportedConstraints: () => supported,
      getUserMedia: async () => stream,
    },
  });
  return stream;
}

describe("req 215/216: BrowserMicrophone is the one that has to do it", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("re-applies onto the open track instead of opening a second capture", async () => {
    const track = stubTrack(
      { echoCancellation: true, noiseSuppression: true, autoGainControl: false, deviceId: "dev-1" },
      ["echoCancellation", "noiseSuppression", "autoGainControl", "voiceIsolation"],
    );
    stubNavigator(track, { echoCancellation: true, noiseSuppression: true, voiceIsolation: true });
    const mic = new BrowserMicrophone();
    await mic.open("dev-1");
    let opens = 0;
    const original = navigator.mediaDevices.getUserMedia;
    navigator.mediaDevices.getUserMedia = async (...args: Parameters<typeof original>) => {
      opens += 1;
      return original(...args);
    };

    const readBack = await mic.applyLive({ noiseSuppression: false, voiceIsolation: false });

    expect(opens).toBe(0);
    expect(track.applied.at(-1)).toMatchObject({ noiseSuppression: false, voiceIsolation: false });
    expect(readBack?.noiseSuppression).toBe(false);
    // Untouched constraints are carried, not dropped: this is one capture being adjusted.
    expect(track.applied.at(-1)).toMatchObject({ echoCancellation: true });
  });

  it("never mentions an opportunistic constraint the browser does not list", async () => {
    const track = stubTrack({ echoCancellation: true, noiseSuppression: true, autoGainControl: false }, [
      "noiseSuppression",
    ]);
    stubNavigator(track, { echoCancellation: true, noiseSuppression: true });
    const mic = new BrowserMicrophone();
    await mic.open();

    await mic.applyLive({ noiseSuppression: false, voiceIsolation: true });

    expect(Object.keys(track.applied.at(-1) ?? {})).not.toContain("voiceIsolation");
  });

  it("survives a browser that rejects the change, and says what is actually in force", async () => {
    const track = stubTrack({ echoCancellation: true, noiseSuppression: true, autoGainControl: false }, []);
    stubNavigator(track, { echoCancellation: true, noiseSuppression: true });
    const mic = new BrowserMicrophone();
    await mic.open();
    track.applyConstraints = async () => {
      throw new Error("OverconstrainedError");
    };

    const readBack = await mic.applyLive({ noiseSuppression: false });

    expect(readBack?.noiseSuppression).toBe(true);
    expect(readBack?.notHonoured).toContain("noiseSuppression");
  });
});
