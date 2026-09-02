import { describe, expect, it } from "vitest";

import {
  DEFAULT_VOICE,
  LocalStorageProfileStore,
  VOICE_CANDIDATES,
  constraintsFor,
  defaultProfile,
  effectiveAgc,
  fingerprintDevice,
  loadVoiceChoice,
  normalizeProfile,
  normalizeVoiceChoice,
  saveVoiceChoice,
  scoreBenchmarkSample,
  type StorageLike,
} from "../../app/lib/voice/profile";

class MemoryStorage implements StorageLike {
  items = new Map<string, string>();
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

class BrokenStorage implements StorageLike {
  getItem(): string | null {
    throw new Error("blocked");
  }
  setItem(): void {
    throw new Error("quota");
  }
  removeItem(): void {
    throw new Error("blocked");
  }
}

const k66 = { deviceId: "abc123", label: "K66 (USB Audio)", groupId: "grp-k66" };
const laptop = { deviceId: "def456", label: "Dahili Mikrofon", groupId: "grp-int" };

describe("MicrophoneProfile", () => {
  it("fingerprints by label + groupId, not by the rotating deviceId", () => {
    expect(fingerprintDevice(k66.label, k66.groupId)).toBe(fingerprintDevice(k66.label, k66.groupId));
    expect(fingerprintDevice(k66.label, k66.groupId)).not.toBe(fingerprintDevice(laptop.label, laptop.groupId));
    expect(defaultProfile({ ...k66, deviceId: "rotated" }).fingerprint).toBe(defaultProfile(k66).fingerprint);
    expect(fingerprintDevice(k66.label, k66.groupId)).toMatch(/^[0-9a-f]{8}$/);
  });

  it("defaults are safe for a sensitive microphone: AGC off, suppression on, Otomatik", () => {
    const profile = defaultProfile(k66);
    expect(profile.agcPreference).toBe("auto");
    expect(effectiveAgc(profile)).toBe(false);
    expect(profile.environmentMode).toBe("auto");
    expect(constraintsFor(profile)).toEqual({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false,
      channelCount: 1,
      voiceIsolation: true,
    });
  });

  it("the AGC decision comes from the benchmark under auto, never from a global constant", () => {
    const profile = defaultProfile(k66);
    const off = { noise_floor_db: -58, spread_db: 3, clip_risk: 0, score: 0 };
    const on = { noise_floor_db: -44, spread_db: 9, clip_risk: 0.3, score: 0 };
    off.score = scoreBenchmarkSample(off);
    on.score = scoreBenchmarkSample(on);
    expect(off.score).toBeGreaterThan(on.score); // AGC lifted the room: off wins
    profile.agcBenchmark = { off, on, recommended: on.score > off.score ? "on" : "off", at: "2026-09-02T00:00:00Z" };
    expect(effectiveAgc(profile)).toBe(false);
    profile.agcBenchmark.recommended = "on";
    expect(effectiveAgc(profile)).toBe(true);
    profile.agcPreference = "off"; // an explicit owner choice beats the benchmark
    expect(effectiveAgc(profile)).toBe(false);
  });

  it("round-trips through storage per device; two devices keep independent profiles", () => {
    const storage = new MemoryStorage();
    const store = new LocalStorageProfileStore(storage);
    const a = { ...defaultProfile(k66), measuredNoiseFloorDb: -41.5, environmentMode: "noisy" as const, lastCalibratedAt: "2026-09-02T10:00:00Z" };
    const b = { ...defaultProfile(laptop), measuredNoiseFloorDb: -60 };
    store.save(a);
    store.save(b);
    const reloaded = new LocalStorageProfileStore(storage);
    expect(reloaded.load(a.fingerprint)).toEqual(a);
    expect(reloaded.load(b.fingerprint)).toEqual(b);
    expect(reloaded.list().map((p) => p.fingerprint).toSorted()).toEqual([a.fingerprint, b.fingerprint].toSorted());
    reloaded.remove(a.fingerprint);
    expect(reloaded.load(a.fingerprint)).toBeNull();
    expect(new LocalStorageProfileStore(storage).list()).toHaveLength(1);
  });

  it("tolerates a corrupt entry and a throwing storage without losing the session", () => {
    const storage = new MemoryStorage();
    storage.setItem("pagentos.voice.mic.deadbeef", "{not json");
    const store = new LocalStorageProfileStore(storage);
    expect(store.load("deadbeef")).toBeNull();
    const broken = new LocalStorageProfileStore(new BrokenStorage());
    const profile = defaultProfile(k66);
    expect(() => broken.save(profile)).not.toThrow();
    expect(broken.load(profile.fingerprint)).toEqual(profile); // in-memory mirror
    expect(broken.list()).toHaveLength(1);
    expect(new LocalStorageProfileStore(null).load("x")).toBeNull();
  });

  it("normalizes foreign values back to the allowed sets", () => {
    const raw: Record<string, unknown> = {
      fingerprint: "00ff00ff",
      friendlyName: "K66",
      agcPreference: "always",
      environmentMode: "loud",
      preferredVadSensitivity: 3,
      measuredNoiseFloorDb: "quiet",
      qualificationScore: 87,
    };
    const profile = normalizeProfile(raw);
    expect(profile).toMatchObject({
      fingerprint: "00ff00ff",
      agcPreference: "auto",
      environmentMode: "auto",
      preferredVadSensitivity: "auto",
      measuredNoiseFloorDb: null,
      qualificationScore: 87,
    });
    expect(normalizeProfile({ friendlyName: "no fingerprint" })).toBeNull();
    expect(normalizeProfile("nope")).toBeNull();
  });
});

describe("voice choice (ADR-0043 A/B)", () => {
  it("offers exactly marin and cedar, defaults to marin, and cannot select anything else", () => {
    expect([...VOICE_CANDIDATES]).toEqual(["marin", "cedar"]);
    expect(DEFAULT_VOICE).toBe("marin");
    expect(normalizeVoiceChoice("cedar")).toBe("cedar");
    expect(normalizeVoiceChoice("arbor")).toBe("marin");
    expect(normalizeVoiceChoice("alloy")).toBe("marin");
    expect(normalizeVoiceChoice("")).toBe("marin");
    expect(normalizeVoiceChoice(undefined)).toBe("marin");
  });

  it("persists per owner with a guarded storage", () => {
    const storage = new MemoryStorage();
    saveVoiceChoice("cedar", storage);
    expect(loadVoiceChoice(storage)).toBe("cedar");
    storage.setItem("pagentos.voice.choice", "shimmer");
    expect(loadVoiceChoice(storage)).toBe("marin");
    expect(() => saveVoiceChoice("cedar", new BrokenStorage())).not.toThrow();
    expect(loadVoiceChoice(new BrokenStorage())).toBe("marin");
  });
});
