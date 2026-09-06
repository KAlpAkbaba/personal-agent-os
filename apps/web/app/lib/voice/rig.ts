/**
 * The voice rig: everything the browser leg of a realtime session needs,
 * built ONCE and handed to one `VoiceSessionController`.
 *
 * This is the construction that `/voice/page.tsx` used to do inside a React
 * effect, extracted so that `/core` and `/voice` share a single session
 * instead of each building their own (M18: the Core is the owner's voice
 * surface; ADR-0061). Nothing about the controller's behaviour lives here —
 * barge-in, the hesitation guard, the narration cursor and tool routing are
 * all still `controller.ts` — only the wiring of its ports, the per-device
 * microphone profile, and the two profile-learning hooks the page owned.
 *
 * Two shapes, on purpose:
 *
 * - `createVoiceRig(build)` takes a *builder* for the ports rather than the
 *   ports themselves, because the browser microphone and the local gate read
 *   the current profile lazily (constraints at open time, mode/sensitivity at
 *   every derivation) and the profile is owned by the rig. The builder gets
 *   the profile holder and closes over it.
 * - `browserRigParts` is the only builder that touches the browser. Tests pass
 *   a builder made of the fakes in `fake.ts`, and `voiceInstances` (below)
 *   counts what got built so "exactly one" is an assertion, not a hope.
 */

import { VoiceSessionApi } from "./api";
import {
  BrowserMicrophone,
  BrowserNetworkMonitor,
  type GatedDetectorSnapshot,
  GatedSpeechDetector,
  WebAudioPlayback,
  WebAudioUplinkShaper,
} from "./audio";
import { type ControllerSnapshot, VoiceSessionController } from "./controller";
import { PassthroughDenoiser } from "./denoiser";
import { FakeTransport } from "./fake";
import type {
  AppliedInputSettings,
  Microphone,
  NetworkMonitor,
  Playback,
  SpeechDetector,
} from "./ports";
import {
  type DeviceIdentity,
  LocalStorageProfileStore,
  type MicrophoneProfile,
  type MicrophoneProfileStore,
  appliedSettingsRecord,
  constraintsFor,
  defaultProfile,
  fingerprintDevice,
  learnFromSession,
} from "./profile";
import {
  type RealtimeTransport,
  type TransportDescriptor,
  TransportConfigError,
} from "./transport";
import type { Unsubscribe } from "./transport";
import type { UplinkShaper } from "./uplink";
import { WebRtcTransport } from "./webrtc";

/** Attenuation depth when the profile opts into the gated uplink shaper. */
export const GATED_ATTENUATION_DB = 9;

// ---------------------------------------------------------------- registry

/**
 * How many of each singleton-shaped thing this tab has built.
 *
 * The invariant the Core depends on (ADR-0061 §1) is "one controller, one
 * microphone stream, one transport, one realtime session per tab". A counter
 * is the cheapest thing that can *disprove* it, so every construction site
 * below bumps one, and the tests read them back after mounting the hook twice.
 */
export type VoiceInstanceCounts = {
  /** `createVoiceRig` calls. */
  rigs: number;
  /** `VoiceSessionController` constructions. */
  controllers: number;
  /** `microphone.open()` calls that reached the port (each is one `getUserMedia`). */
  microphonesOpened: number;
  /** Transports handed to the controller. */
  transports: number;
  /** `POST /v1/voice/realtime/sessions` answered 201. */
  sessionsCreated: number;
};

function emptyCounts(): VoiceInstanceCounts {
  return { rigs: 0, controllers: 0, microphonesOpened: 0, transports: 0, sessionsCreated: 0 };
}

class InstanceRegistry {
  private counts = emptyCounts();

  snapshot(): VoiceInstanceCounts {
    return { ...this.counts };
  }

  bump(key: keyof VoiceInstanceCounts): void {
    this.counts[key] += 1;
  }

  /** Tests only: a fresh tab. */
  reset(): void {
    this.counts = emptyCounts();
  }
}

export const voiceInstances = new InstanceRegistry();

// ------------------------------------------------------------------- parts

/** The per-device profile the ports read live; owned by the rig. */
export type ProfileHolder = { current: MicrophoneProfile | null };

/**
 * The optional extras of the real `GatedSpeechDetector` that the rig and the
 * diagnostics view use when present. The controller itself only needs
 * `SpeechDetector`.
 */
export type RigSpeechDetector = SpeechDetector & {
  onEchoResidualMeasured?(sink: (residualDb: number) => void): Unsubscribe;
  recalibrate?(trigger?: number): void;
  applyPreferences?(): void;
  snapshot?(): GatedDetectorSnapshot;
};

export type VoiceRigParts = {
  api: VoiceSessionApi;
  profiles: MicrophoneProfileStore;
  playback: Playback;
  microphone: Microphone;
  detector: RigSpeechDetector;
  network: NetworkMonitor;
  /** Builds a transport for a resolved descriptor; the rig counts and observes it. */
  transportFor: (descriptor: TransportDescriptor) => RealtimeTransport;
  now: () => number;
};

export type VoiceRigBuilder = (profile: ProfileHolder) => VoiceRigParts;

/**
 * A scripted transport for the offline simulator provider: no browser media
 * path exists for it, so the session, event, tool-relay and close paths are
 * exercised against the real API by a fake that plays one turn.
 */
export class ScriptedSimulatorTransport extends FakeTransport {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private calls = 0;

  constructor() {
    super({ now: () => performance.now() });
  }

  override async connect(...args: Parameters<FakeTransport["connect"]>): Promise<void> {
    await super.connect(...args);
    this.timer = setTimeout(
      () => this.demoTurn("Simülatör bağlandı; bu sağlayıcının tarayıcı ses yolu yok."),
      800,
    );
  }

  demoTurn(text: string): void {
    const at = performance.now();
    this.emit({ type: "response_started", at });
    this.emit({ type: "response_text", at, text, final: true });
    this.emit({ type: "audio_started", at: at + 120 });
    setTimeout(() => this.emit({ type: "response_done", at: performance.now() }), 1200);
  }

  demoToolCall(): void {
    this.calls += 1;
    this.emit({
      type: "tool_call",
      at: performance.now(),
      callId: `sim-web-${Date.now()}-${this.calls}`,
      name: "clock.now",
      arguments: {},
    });
  }

  override close(reason?: string): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    super.close(reason);
  }
}

export type BrowserRigOptions = {
  /** The owner-bearer fetcher (`apiFetch`). */
  fetcher: ConstructorParameters<typeof VoiceSessionApi>[0];
};

/**
 * The real ports, exactly as `/voice` built them before the extraction:
 * `BrowserMicrophone` is the ONLY `getUserMedia` for audio in this codebase,
 * and this is the only place it is constructed for a session.
 */
export function browserRigParts(options: BrowserRigOptions): VoiceRigBuilder {
  return (profile) => {
    const playback = new WebAudioPlayback();
    let shaper: UplinkShaper | null = null;
    const microphone = new BrowserMicrophone({
      constraintsFor: () => (profile.current ? constraintsFor(profile.current) : {}),
      denoiser: new PassthroughDenoiser(),
      uplinkFor: () => {
        shaper = profile.current?.inputGainStrategy === "gated_attenuation" ? new WebAudioUplinkShaper() : null;
        return shaper;
      },
    });
    const detector = new GatedSpeechDetector({
      playbackActive: () => playback.playing,
      mode: () => profile.current?.environmentMode ?? "auto",
      sensitivity: () => profile.current?.preferredVadSensitivity ?? "auto",
      attenuationDb: () => (profile.current?.inputGainStrategy === "gated_attenuation" ? GATED_ATTENUATION_DB : 0),
      uplink: () => shaper,
      analysisStream: () => microphone.raw,
      // ADR-0047 §4: the profile's learned offsets and last measured echo residual.
      adaptation: () => profile.current?.learned ?? null,
      initialEchoResidualDb: () => profile.current?.measuredEchoResidualDb ?? null,
    });
    return {
      api: new VoiceSessionApi(options.fetcher),
      profiles: new LocalStorageProfileStore(),
      playback,
      microphone,
      detector,
      network: new BrowserNetworkMonitor(),
      transportFor: (descriptor) => {
        if (descriptor.kind === "webrtc") return new WebRtcTransport();
        if (descriptor.kind === "simulated") return new ScriptedSimulatorTransport();
        throw new TransportConfigError(`tarayıcı ${descriptor.kind} taşıyıcısını açamaz`);
      },
      now: () => performance.now(),
    };
  };
}

// --------------------------------------------------------------------- rig

export type VoiceRig = {
  controller: VoiceSessionController;
  parts: VoiceRigParts;
  /** The current per-device profile (read live by the ports). */
  readonly profile: MicrophoneProfile | null;
  /** True once the server picked the simulator provider for the current leg. */
  readonly simulated: boolean;
  /** The simulator transport of the current leg, for the diagnostics demo. */
  readonly simulator: ScriptedSimulatorTransport | null;
  /** Profile changed (any source: resolve, calibration, owner edit). */
  onProfile(listener: (profile: MicrophoneProfile | null) => void): Unsubscribe;
  /** Transport kind changed for the current leg. */
  onTransport(listener: () => void): Unsubscribe;
  /** Persist + publish a profile change. */
  commitProfile(next: MicrophoneProfile): void;
  /** Load (or default) the profile for a device identity and make it current. */
  resolveProfile(identity: DeviceIdentity): MicrophoneProfile;
  /** The profile for the device the microphone actually opened, if any. */
  currentProfile(): MicrophoneProfile | null;
  /**
   * Connect with the page-level ceremony the controller does not own: warm the
   * output path inside the owner's gesture, then record the input read-back
   * into the profile once the grant reveals the device.
   */
  connect(options: { deviceId?: string; voice?: string }): Promise<void>;
  /** Disconnect, then let the profile learn from the session's own counters. */
  disconnect(): Promise<void>;
  /** The controller's snapshot right now (convenience). */
  snapshot(): ControllerSnapshot;
  /** The microphone's read-back, when open. */
  applied(): AppliedInputSettings | null;
  dispose(): void;
};

export function createVoiceRig(build: VoiceRigBuilder): VoiceRig {
  voiceInstances.bump("rigs");
  const profile: ProfileHolder = { current: null };
  const parts = build(profile);
  const profileListeners = new Set<(profile: MicrophoneProfile | null) => void>();
  const transportListeners = new Set<() => void>();
  let simulated = false;
  let simulator: ScriptedSimulatorTransport | null = null;

  const publishProfile = () => {
    for (const listener of profileListeners) listener(profile.current);
  };

  const commitProfile = (next: MicrophoneProfile): void => {
    profile.current = next;
    parts.profiles.save(next);
    publishProfile();
  };

  const resolveProfile = (identity: DeviceIdentity): MicrophoneProfile => {
    const fingerprint = fingerprintDevice(identity.label, identity.groupId);
    const found = parts.profiles.load(fingerprint);
    const next = found ? { ...found, deviceId: identity.deviceId || found.deviceId } : defaultProfile(identity);
    profile.current = next;
    publishProfile();
    return next;
  };

  /** The default device only reveals its identity after the grant. */
  const currentProfile = (): MicrophoneProfile | null => {
    const readBack = parts.microphone.applied;
    return (
      profile.current ??
      (readBack
        ? resolveProfile({ deviceId: readBack.deviceId ?? "", label: readBack.label, groupId: readBack.groupId ?? "" })
        : null)
    );
  };

  // ADR-0047 §5: only a MEASUREMENT updates the profile; a dead or contaminated
  // window is reported, never persisted as "the room".
  const unsubs: Unsubscribe[] = [];
  if (parts.detector.onCalibration) {
    unsubs.push(
      parts.detector.onCalibration((c) => {
        if (c.measured !== 1) return;
        const readBack = parts.microphone.applied;
        const current = currentProfile();
        if (!current) return;
        const at = new Date().toISOString();
        commitProfile({
          ...current,
          measuredNoiseFloorDb: c.noise_floor_db,
          lastCalibratedAt: at,
          appliedVoiceIsolation: readBack?.voiceIsolation ?? current.appliedVoiceIsolation,
          appliedSettings: readBack ? appliedSettingsRecord(readBack, at) : current.appliedSettings,
          friendlyName: readBack?.label || current.friendlyName,
        });
      }),
    );
  }
  // ADR-0047 §4: the measured echo residual is a per-device fact; persist it.
  if (parts.detector.onEchoResidualMeasured) {
    unsubs.push(
      parts.detector.onEchoResidualMeasured((residualDb) => {
        const current = currentProfile();
        if (!current || current.measuredEchoResidualDb === residualDb) return;
        commitProfile({ ...current, measuredEchoResidualDb: residualDb });
      }),
    );
  }

  // Count what the controller actually opens, at the port itself.
  const microphone: Microphone = {
    get stream() {
      return parts.microphone.stream;
    },
    get applied() {
      return parts.microphone.applied;
    },
    open: (deviceId, constraints) => {
      voiceInstances.bump("microphonesOpened");
      return parts.microphone.open(deviceId, constraints);
    },
    close: () => parts.microphone.close(),
  };

  unsubs.push(
    parts.api.onRequest((entry) => {
      if (entry.method === "POST" && entry.path === "/v1/voice/realtime/sessions" && entry.status === 201) {
        voiceInstances.bump("sessionsCreated");
      }
    }),
  );

  voiceInstances.bump("controllers");
  const controller = new VoiceSessionController({
    api: parts.api,
    transportFactory: (descriptor) => {
      const transport = parts.transportFor(descriptor);
      voiceInstances.bump("transports");
      simulated = descriptor.kind === "simulated";
      simulator = transport instanceof ScriptedSimulatorTransport ? transport : null;
      for (const listener of transportListeners) listener();
      return transport;
    },
    playback: parts.playback,
    microphone,
    localSpeech: parts.detector,
    network: parts.network,
    now: parts.now,
    // ADR-0047 §4: the AGC A/B result travels with the read-back, as numbers.
    inputEvidence: () => {
      const bench = profile.current?.agcBenchmark;
      if (!bench) return { agc_bench: 0 };
      return {
        agc_bench: 1,
        agc_bench_off_score: bench.off.score,
        agc_bench_on_score: bench.on.score,
        agc_bench_off_floor_db: bench.off.noise_floor_db,
        agc_bench_on_floor_db: bench.on.noise_floor_db,
        agc_bench_recommended_on: bench.recommended === "on" ? 1 : 0,
      };
    },
  });

  const connect = async (options: { deviceId?: string; voice?: string }): Promise<void> => {
    // ADR-0047 §3: the output AudioContext is created and resumed inside the
    // owner's click, so the first response never waits for `resume()`.
    parts.playback.prepare?.();
    await controller.connect(options);
    const readBack = parts.microphone.applied;
    if (readBack) {
      const current =
        profile.current ??
        resolveProfile({ deviceId: readBack.deviceId ?? "", label: readBack.label, groupId: readBack.groupId ?? "" });
      // ADR-0047 §4: the read-back is a measurement; the profile keeps it as one.
      commitProfile({ ...current, appliedSettings: appliedSettingsRecord(readBack, new Date().toISOString()) });
    }
  };

  const disconnect = async (): Promise<void> => {
    await controller.disconnect();
    // ADR-0047 §4: the profile learns from the session's own counters — bounded, reversible, no owner question.
    const current = profile.current;
    const m = controller.getSnapshot().micMetrics;
    if (!current) return;
    const learned = learnFromSession(
      current,
      {
        false_starts: m.false_starts,
        false_barge_ins: m.false_barge_ins,
        gate_opens: m.gate_opens,
        confirmed_turns: m.confirmed_turns,
      },
      new Date().toISOString(),
    );
    if (learned !== current) {
      commitProfile(learned);
      parts.detector.applyPreferences?.();
    }
  };

  return {
    controller,
    parts,
    get profile() {
      return profile.current;
    },
    get simulated() {
      return simulated;
    },
    get simulator() {
      return simulator;
    },
    onProfile(listener) {
      profileListeners.add(listener);
      return () => profileListeners.delete(listener);
    },
    onTransport(listener) {
      transportListeners.add(listener);
      return () => transportListeners.delete(listener);
    },
    commitProfile,
    resolveProfile,
    currentProfile,
    connect,
    disconnect,
    snapshot: () => controller.getSnapshot(),
    applied: () => parts.microphone.applied ?? null,
    dispose() {
      for (const unsub of unsubs) unsub();
      unsubs.length = 0;
      controller.dispose();
      parts.detector.stop();
      parts.playback.dispose();
      profileListeners.clear();
      transportListeners.clear();
    },
  };
}
