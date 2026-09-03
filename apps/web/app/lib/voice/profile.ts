/**
 * MicrophoneProfile — the owner's persistent per-device input preference
 * (ADR-0044 §5). Not a global constant: the K66 gets its own profile, a laptop
 * microphone another, keyed by a fingerprint of label + groupId (deviceIds are
 * per-origin and can rotate). Persisted in localStorage behind a store port so
 * it can later be mirrored to Cloud Core owner preferences; every storage
 * access is wrapped, so a blocked or full storage degrades to in-memory.
 *
 * Also here: the owner's realtime voice choice for the A/B (Marin / Cedar),
 * which is a per-owner preference rather than a per-device one.
 */

import {
  boundAdaptation,
  EMPTY_ADAPTATION,
  type EnvironmentMode,
  type GateAdaptation,
  type VadSensitivity,
} from "./calibration";

export { ADAPTATION_LIMITS } from "./calibration";
import type { AppliedInputSettings, MicrophoneConstraints } from "./ports";

export type AgcPreference = "off" | "on" | "auto";
export type NoiseSuppressionMode = "auto" | "browser" | "off";
export type InputGainStrategy = "browser" | "gated_attenuation";

export type BenchmarkSample = {
  noise_floor_db: number;
  spread_db: number;
  clip_risk: number;
  score: number;
};

export type AgcBenchmark = {
  off: BenchmarkSample;
  on: BenchmarkSample;
  recommended: "off" | "on";
  at: string;
};

/** The browser's applied input settings as READ BACK (ADR-0047 §4): measured values, never the request. */
export type AppliedSettingsRecord = {
  echoCancellation: boolean | null;
  noiseSuppression: boolean | null;
  autoGainControl: boolean | null;
  voiceIsolation: boolean | null;
  sampleRate: number | null;
  channelCount: number | null;
  inputLatencyMs: number | null;
  notHonoured: string[];
  measuredAt: string;
};

/** What the profile learned from its own sessions (ADR-0047 §4). */
export type LearnedAdaptation = GateAdaptation & {
  /** sessions that contributed */
  sessions: number;
  updatedAt: string | null;
};

export type MicrophoneProfile = {
  schema: 1;
  deviceId: string;
  /** hash(label + groupId): stable across deviceId rotation. */
  fingerprint: string;
  friendlyName: string;
  inputGainStrategy: InputGainStrategy;
  /** Never blindly on: "auto" follows the A/B benchmark, else off for a sensitive microphone. */
  agcPreference: AgcPreference;
  noiseSuppressionMode: NoiseSuppressionMode;
  measuredNoiseFloorDb: number | null;
  preferredVadSensitivity: VadSensitivity;
  lastCalibratedAt: string | null;
  /** Filled by the noise qualification matrix (0..100); null until qualified. */
  qualificationScore: number | null;
  environmentMode: EnvironmentMode;
  agcBenchmark: AgcBenchmark | null;
  /** Read back from the last getUserMedia: which opportunistic constraints were honoured. */
  appliedVoiceIsolation: boolean | null;
  /** ADR-0047 §4: the full read-back of the last open, as measured. */
  appliedSettings: AppliedSettingsRecord | null;
  /** ADR-0047 §4: measured residual of the assistant's playback in this microphone (dBFS). */
  measuredEchoResidualDb: number | null;
  /** ADR-0047 §4: bounded per-device offsets learned from false starts. */
  learned: LearnedAdaptation;
};

export const EMPTY_LEARNED: LearnedAdaptation = { ...EMPTY_ADAPTATION, sessions: 0, updatedAt: null };

export type DeviceIdentity = { deviceId: string; label: string; groupId: string };

/** FNV-1a 32-bit over `label|groupId`, hex — no PII beyond what the label already is. */
export function fingerprintDevice(label: string, groupId: string): string {
  const input = `${label}|${groupId}`;
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

export function defaultProfile(device: DeviceIdentity): MicrophoneProfile {
  return {
    schema: 1,
    deviceId: device.deviceId,
    fingerprint: fingerprintDevice(device.label, device.groupId),
    friendlyName: device.label || "Mikrofon",
    inputGainStrategy: "browser",
    agcPreference: "auto",
    noiseSuppressionMode: "auto",
    measuredNoiseFloorDb: null,
    preferredVadSensitivity: "auto",
    lastCalibratedAt: null,
    qualificationScore: null,
    environmentMode: "auto",
    agcBenchmark: null,
    appliedVoiceIsolation: null,
    appliedSettings: null,
    measuredEchoResidualDb: null,
    learned: { ...EMPTY_LEARNED },
  };
}

const AGC_PREFERENCES: ReadonlySet<string> = new Set(["off", "on", "auto"]);
const NS_MODES: ReadonlySet<string> = new Set(["auto", "browser", "off"]);
const GAIN_STRATEGIES: ReadonlySet<string> = new Set(["browser", "gated_attenuation"]);
const SENSITIVITIES: ReadonlySet<string> = new Set(["auto", "low", "normal", "high"]);
const ENV_MODES: ReadonlySet<string> = new Set(["auto", "quiet", "noisy", "very_noisy"]);

function pick<T extends string>(value: unknown, allowed: ReadonlySet<string>, fallback: T): T {
  return typeof value === "string" && allowed.has(value) ? (value as T) : fallback;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function boolOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function sample(value: unknown): BenchmarkSample | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const floor = numberOrNull(v.noise_floor_db);
  const spread = numberOrNull(v.spread_db);
  const clip = numberOrNull(v.clip_risk);
  const score = numberOrNull(v.score);
  if (floor === null || spread === null || clip === null || score === null) return null;
  return { noise_floor_db: floor, spread_db: spread, clip_risk: clip, score };
}

/** Validate a stored/foreign object into a profile; null when it is not one. */
export function normalizeProfile(raw: unknown): MicrophoneProfile | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.fingerprint !== "string" || !r.fingerprint) return null;
  const base = defaultProfile({
    deviceId: typeof r.deviceId === "string" ? r.deviceId : "",
    label: typeof r.friendlyName === "string" ? r.friendlyName : "",
    groupId: "",
  });
  let agcBenchmark: AgcBenchmark | null = null;
  if (r.agcBenchmark && typeof r.agcBenchmark === "object") {
    const b = r.agcBenchmark as Record<string, unknown>;
    const off = sample(b.off);
    const on = sample(b.on);
    if (off && on) {
      agcBenchmark = {
        off,
        on,
        recommended: b.recommended === "on" ? "on" : "off",
        at: typeof b.at === "string" ? b.at : "",
      };
    }
  }
  let appliedSettings: AppliedSettingsRecord | null = null;
  if (r.appliedSettings && typeof r.appliedSettings === "object") {
    const s = r.appliedSettings as Record<string, unknown>;
    appliedSettings = {
      echoCancellation: boolOrNull(s.echoCancellation),
      noiseSuppression: boolOrNull(s.noiseSuppression),
      autoGainControl: boolOrNull(s.autoGainControl),
      voiceIsolation: boolOrNull(s.voiceIsolation),
      sampleRate: numberOrNull(s.sampleRate),
      channelCount: numberOrNull(s.channelCount),
      inputLatencyMs: numberOrNull(s.inputLatencyMs),
      notHonoured: Array.isArray(s.notHonoured) ? s.notHonoured.filter((x): x is string => typeof x === "string") : [],
      measuredAt: typeof s.measuredAt === "string" ? s.measuredAt : "",
    };
  }
  const learnedRaw = r.learned && typeof r.learned === "object" ? (r.learned as Record<string, unknown>) : null;
  const learned: LearnedAdaptation = {
    ...boundAdaptation(learnedRaw as Partial<GateAdaptation> | null),
    sessions: Math.max(0, Math.round(numberOrNull(learnedRaw?.sessions) ?? 0)),
    updatedAt: typeof learnedRaw?.updatedAt === "string" ? learnedRaw.updatedAt : null,
  };
  return {
    ...base,
    fingerprint: r.fingerprint,
    friendlyName: typeof r.friendlyName === "string" && r.friendlyName ? r.friendlyName : base.friendlyName,
    inputGainStrategy: pick(r.inputGainStrategy, GAIN_STRATEGIES, "browser"),
    agcPreference: pick(r.agcPreference, AGC_PREFERENCES, "auto"),
    noiseSuppressionMode: pick(r.noiseSuppressionMode, NS_MODES, "auto"),
    measuredNoiseFloorDb: numberOrNull(r.measuredNoiseFloorDb),
    preferredVadSensitivity: pick(r.preferredVadSensitivity, SENSITIVITIES, "auto"),
    lastCalibratedAt: typeof r.lastCalibratedAt === "string" ? r.lastCalibratedAt : null,
    qualificationScore: numberOrNull(r.qualificationScore),
    environmentMode: pick(r.environmentMode, ENV_MODES, "auto"),
    agcBenchmark,
    appliedVoiceIsolation: typeof r.appliedVoiceIsolation === "boolean" ? r.appliedVoiceIsolation : null,
    appliedSettings,
    measuredEchoResidualDb: numberOrNull(r.measuredEchoResidualDb),
    learned,
  };
}

/** The measured read-back of an open, as the profile stores it (ADR-0047 §4). */
export function appliedSettingsRecord(applied: AppliedInputSettings, at: string): AppliedSettingsRecord {
  return {
    echoCancellation: applied.echoCancellation,
    noiseSuppression: applied.noiseSuppression,
    autoGainControl: applied.autoGainControl,
    voiceIsolation: applied.voiceIsolation,
    sampleRate: applied.sampleRate,
    channelCount: applied.channelCount,
    inputLatencyMs: applied.inputLatencyMs,
    notHonoured: [...applied.notHonoured],
    measuredAt: at,
  };
}

/** What one session contributes to the profile's learning (numbers the controller already counts). */
export type SessionNoiseOutcome = {
  false_starts: number;
  false_barge_ins: number;
  gate_opens: number;
  /** provider-confirmed owner turns */
  confirmed_turns: number;
};

/**
 * Per-device learning from the session's own counters (ADR-0047 §4): the
 * profile learns, it never asks the owner. Bounded (ADAPTATION_LIMITS) and
 * reversible: a clean session with real turns decays the offsets so a noisy
 * afternoon cannot ratchet a microphone permanently deaf to quiet speech.
 *
 * - ≥ 2 false starts outside playback → +1 dB margin, +10 ms onset;
 * - ≥ 2 false barge-ins (false starts during playback) → +2 dB playback margin;
 * - 0 false starts with ≥ 5 confirmed turns → −0.5 dB margin, −1 dB playback margin;
 * - a session with no confirmed turns and no false starts teaches nothing.
 */
export function learnFromSession(profile: MicrophoneProfile, outcome: SessionNoiseOutcome, at: string): MicrophoneProfile {
  const current = boundAdaptation(profile.learned);
  const quiet = Math.max(0, outcome.false_starts - outcome.false_barge_ins);
  let { marginDb, onsetMs, echoMarginDb } = current;
  let taught = false;
  if (quiet >= 2) {
    marginDb += 1;
    onsetMs += 10;
    taught = true;
  }
  if (outcome.false_barge_ins >= 2) {
    echoMarginDb += 2;
    taught = true;
  }
  if (outcome.false_starts === 0 && outcome.confirmed_turns >= 5) {
    marginDb -= 0.5;
    echoMarginDb -= 1;
    taught = true;
  }
  if (!taught) return profile;
  const bounded = boundAdaptation({ marginDb, onsetMs, echoMarginDb });
  return {
    ...profile,
    learned: { ...bounded, sessions: profile.learned.sessions + 1, updatedAt: at },
  };
}

/** Effective AGC decision: the benchmark wins under "auto", else off (sensitive microphone default). */
export function effectiveAgc(profile: MicrophoneProfile): boolean {
  if (profile.agcPreference === "on") return true;
  if (profile.agcPreference === "off") return false;
  return profile.agcBenchmark?.recommended === "on";
}

/** Layer 1 constraints for getUserMedia derived from the profile (read back afterwards, never assumed). */
export function constraintsFor(profile: MicrophoneProfile): MicrophoneConstraints {
  const suppress = profile.noiseSuppressionMode !== "off";
  return {
    echoCancellation: true,
    noiseSuppression: suppress,
    autoGainControl: effectiveAgc(profile),
    channelCount: 1,
    // Opportunistic (Chromium 148+ lists it); honoured or not is read back.
    voiceIsolation: suppress,
  };
}

/** Higher is better: a quieter, steadier floor with no clipping. */
export function scoreBenchmarkSample(input: { noise_floor_db: number; spread_db: number; clip_risk: number }): number {
  return Math.round((-input.noise_floor_db - 1.5 * input.spread_db - 20 * input.clip_risk) * 10) / 10;
}

// ------------------------------------------------------------------ store

export interface MicrophoneProfileStore {
  load(fingerprint: string): MicrophoneProfile | null;
  save(profile: MicrophoneProfile): void;
  list(): MicrophoneProfile[];
  remove(fingerprint: string): void;
}

export class MemoryProfileStore implements MicrophoneProfileStore {
  private readonly items = new Map<string, MicrophoneProfile>();

  load(fingerprint: string): MicrophoneProfile | null {
    return this.items.get(fingerprint) ?? null;
  }

  save(profile: MicrophoneProfile): void {
    this.items.set(profile.fingerprint, { ...profile });
  }

  list(): MicrophoneProfile[] {
    return Array.from(this.items.values());
  }

  remove(fingerprint: string): void {
    this.items.delete(fingerprint);
  }
}

/** The subset of `Storage` we use; injectable so tests need no DOM. */
export type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

const PROFILE_PREFIX = "pagentos.voice.mic.";
const INDEX_KEY = `${PROFILE_PREFIX}index`;

function browserStorage(): StorageLike | null {
  try {
    if (typeof localStorage === "undefined") return null;
    return localStorage;
  } catch {
    return null;
  }
}

/**
 * localStorage-backed store. Every access is wrapped: a throwing storage
 * (private mode, quota, blocked site data) degrades to the in-memory mirror
 * for the life of the page and never breaks a session.
 */
export class LocalStorageProfileStore implements MicrophoneProfileStore {
  private readonly mirror = new MemoryProfileStore();
  private readonly storage: StorageLike | null;

  constructor(storage?: StorageLike | null) {
    this.storage = storage === undefined ? browserStorage() : storage;
  }

  private read(key: string): string | null {
    try {
      return this.storage?.getItem(key) ?? null;
    } catch {
      return null;
    }
  }

  private write(key: string, value: string): void {
    try {
      this.storage?.setItem(key, value);
    } catch {
      /* storage unavailable: the mirror keeps the value for this page */
    }
  }

  private index(): string[] {
    const raw = this.read(INDEX_KEY);
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw) as unknown;
      return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
    } catch {
      return [];
    }
  }

  load(fingerprint: string): MicrophoneProfile | null {
    const raw = this.read(PROFILE_PREFIX + fingerprint);
    if (raw) {
      try {
        const profile = normalizeProfile(JSON.parse(raw));
        if (profile) {
          this.mirror.save(profile);
          return profile;
        }
      } catch {
        /* corrupt entry: fall through to the mirror */
      }
    }
    return this.mirror.load(fingerprint);
  }

  save(profile: MicrophoneProfile): void {
    this.mirror.save(profile);
    this.write(PROFILE_PREFIX + profile.fingerprint, JSON.stringify(profile));
    const index = this.index();
    if (!index.includes(profile.fingerprint)) {
      this.write(INDEX_KEY, JSON.stringify([...index, profile.fingerprint]));
    }
  }

  list(): MicrophoneProfile[] {
    const seen = new Map<string, MicrophoneProfile>();
    for (const fingerprint of this.index()) {
      const profile = this.load(fingerprint);
      if (profile) seen.set(fingerprint, profile);
    }
    for (const profile of this.mirror.list()) if (!seen.has(profile.fingerprint)) seen.set(profile.fingerprint, profile);
    return Array.from(seen.values());
  }

  remove(fingerprint: string): void {
    this.mirror.remove(fingerprint);
    try {
      this.storage?.removeItem(PROFILE_PREFIX + fingerprint);
    } catch {
      /* nothing to remove */
    }
    this.write(INDEX_KEY, JSON.stringify(this.index().filter((f) => f !== fingerprint)));
  }
}

// ------------------------------------------------------------ voice choice

/** The owner's A/B candidates (ADR-0043): exactly these two, nothing free-text. */
export const VOICE_CANDIDATES = ["marin", "cedar"] as const;
export type VoiceChoice = (typeof VOICE_CANDIDATES)[number];
export const DEFAULT_VOICE: VoiceChoice = "marin";
const VOICE_KEY = "pagentos.voice.choice";

/** Anything outside the two candidates falls back to the default; unknown values cannot be selected. */
export function normalizeVoiceChoice(value: unknown): VoiceChoice {
  return typeof value === "string" && (VOICE_CANDIDATES as readonly string[]).includes(value)
    ? (value as VoiceChoice)
    : DEFAULT_VOICE;
}

export function loadVoiceChoice(storage: StorageLike | null = browserStorage()): VoiceChoice {
  try {
    return normalizeVoiceChoice(storage?.getItem(VOICE_KEY));
  } catch {
    return DEFAULT_VOICE;
  }
}

export function saveVoiceChoice(choice: VoiceChoice, storage: StorageLike | null = browserStorage()): void {
  try {
    storage?.setItem(VOICE_KEY, normalizeVoiceChoice(choice));
  } catch {
    /* storage unavailable */
  }
}
