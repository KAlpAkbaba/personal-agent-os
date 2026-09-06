/**
 * The one voice session of this tab (M18; ADR-0061 §1).
 *
 * `getVoiceStore()` returns a module-level singleton that lazily builds the
 * rig (`rig.ts`) and the `VoiceSessionController` the first time anything
 * needs them, and never builds them again for the life of the tab. `/core`,
 * `/core/cockpit` and `/voice` all read the same store; mounting a second
 * view, or unmounting and remounting one, subscribes and unsubscribes a
 * listener and does nothing else. The controller, the microphone stream, the
 * transport and the realtime session are therefore shared, which is what lets
 * the owner reconnect from the Core without going to `/voice`, and what makes
 * the Core's voice overlay a *report* of the real session rather than a
 * second one.
 *
 * The snapshot is a plain, referentially-stable object rebuilt only when
 * something changed, so `useSyncExternalStore` can compare it by identity.
 * Everything React-shaped is in `useVoiceSession.ts`; this file has no React.
 */

import { type GatedDetectorSnapshot, listAudioDevices } from "./audio";
import type { ControllerSnapshot, VoiceUiState } from "./controller";
import type { AppliedInputSettings, AudioDevice } from "./ports";
import type { EnvironmentMode } from "./calibration";
import {
  type MicrophoneProfile,
  type StorageLike,
  type VoiceChoice,
  loadVoiceChoice,
  normalizeVoiceChoice,
  saveVoiceChoice,
} from "./profile";
import {
  type VoiceInstanceCounts,
  type VoiceRig,
  type VoiceRigBuilder,
  browserRigParts,
  createVoiceRig,
  voiceInstances,
} from "./rig";
import { apiFetch } from "../session";

export type VoiceStoreSnapshot = {
  /** False on the server and before the rig exists; every other field is then a placeholder. */
  ready: boolean;
  controller: ControllerSnapshot;
  devices: AudioDevice[];
  /** Selected microphone id; "" = the browser default. */
  micId: string;
  /** Selected speaker id; "" = the browser default. */
  speakerId: string;
  voice: VoiceChoice;
  profile: MicrophoneProfile | null;
  /** What the browser applied to the input track, once open. */
  applied: AppliedInputSettings | null;
  /** The server chose the simulator provider for the current leg. */
  simulated: boolean;
  instances: VoiceInstanceCounts;
};

/** States in which a media leg is open. */
export const LIVE_STATES: ReadonlySet<VoiceUiState> = new Set<VoiceUiState>([
  "listening",
  "speaking",
  "tool_running",
  "interrupted",
  "reconnecting",
]);

/** States in which a connect is in progress and a second one must not start. */
export const BUSY_STATES: ReadonlySet<VoiceUiState> = new Set<VoiceUiState>(["creating", "connecting"]);

export function isLiveState(state: VoiceUiState): boolean {
  return LIVE_STATES.has(state);
}

export function isBusyState(state: VoiceUiState): boolean {
  return BUSY_STATES.has(state);
}

/** The placeholder controller snapshot before a rig exists (server render). */
function idleController(): ControllerSnapshot {
  return {
    state: "idle",
    sessionId: null,
    provider: null,
    transport: null,
    voice: null,
    voiceProfile: null,
    micMetrics: {
      false_starts: 0,
      false_barge_ins: 0,
      false_turns: 0,
      confirmed_turns: 0,
      early_mutes: 0,
      early_mute_reverts: 0,
      gate_opens: 0,
      gated_out: 0,
      click_rejects: 0,
      calibrations: 0,
      noise_floor_db: null,
      env: null,
      echo_residual_db: null,
      cancel_noop_errors: 0,
      speech_detected: 0,
      potential_barge_in: 0,
      accepted_owner_interruption: 0,
      rejected_background_speech: 0,
      explicit_stop_command: 0,
      false_interruption: 0,
    },
    turn: 0,
    assistantText: "",
    ownerText: "",
    lastError: null,
    lastErrorLines: [],
    contract: null,
    contractNotice: null,
    requestLog: [],
    latency: {},
    latencyDetail: {},
    toolsRunning: [],
    sidebandLog: [],
    narrationCursor: null,
    hesitation: { held: 0, resumed_within_hold: 0 },
    online: true,
    eventsAccepted: 0,
    eventsPending: 0,
    legs: 1,
  };
}

const SERVER_SNAPSHOT: VoiceStoreSnapshot = Object.freeze({
  ready: false,
  controller: idleController(),
  devices: [],
  micId: "",
  speakerId: "",
  voice: "marin",
  profile: null,
  applied: null,
  simulated: false,
  instances: { rigs: 0, controllers: 0, microphonesOpened: 0, transports: 0, sessionsCreated: 0 },
});

export type VoiceStoreOptions = {
  /** Builds the rig's ports; the browser builder by default (see `getVoiceStore`). */
  build: VoiceRigBuilder;
  /** Device enumeration; `listAudioDevices` in the browser. */
  listDevices?: () => Promise<AudioDevice[]>;
  /** Where the voice choice persists; localStorage in the browser, injectable for tests. */
  storage?: StorageLike | null;
  /** Where `pagehide` is heard; `window` in the browser, a fake target in tests, `null` on the server. */
  unloadTarget?: UnloadTarget | null;
};

export type UnloadTarget = {
  addEventListener(type: "pagehide", listener: () => void): void;
  removeEventListener(type: "pagehide", listener: () => void): void;
};

export class VoiceStore {
  private rig: VoiceRig | null = null;
  private readonly listeners = new Set<() => void>();
  private snapshot: VoiceStoreSnapshot = SERVER_SNAPSHOT;
  private devices: AudioDevice[] = [];
  private micId = "";
  private speakerId = "";
  private voice: VoiceChoice = "marin";
  private unsubs: Array<() => void> = [];

  constructor(private readonly options: VoiceStoreOptions) {}

  // -------------------------------------------------------------- lifecycle

  /**
   * Build the rig if it does not exist. Idempotent by construction: the only
   * assignment to `this.rig` is guarded by its own null check, and there is
   * one store per tab (`getVoiceStore`). `voiceInstances` proves it.
   */
  private ensureRig(): VoiceRig {
    if (this.rig) return this.rig;
    const rig = createVoiceRig(this.options.build);
    this.rig = rig;
    this.voice = loadVoiceChoice(this.options.storage);
    this.unsubs.push(
      rig.controller.subscribe(() => this.publish()),
      rig.onProfile(() => this.publish()),
      rig.onTransport(() => this.publish()),
      this.watchUnload(rig),
    );
    this.publish();
    return rig;
  }

  /**
   * A reload or a closed tab ends the session on the Cloud Core too.
   *
   * The store lives for the tab, so navigating within the app keeps the
   * session (ADR-0061); but the page going away takes the microphone and the
   * WebRTC leg with it, and until 2026-09-06 nothing told the Cloud Core: the
   * owner's reload left session a2ac0716 "active" while the next page created
   * another, and the two overlapped on the record. `pagehide` fires on both a
   * reload and a close; the close request is `keepalive` so it outlives the
   * page. The local rig is not torn down here — the page is gone anyway — and
   * a `pagehide` for a bfcache freeze with no live session does nothing.
   */
  private watchUnload(rig: VoiceRig): () => void {
    const target = this.options.unloadTarget ?? (typeof window !== "undefined" ? window : null);
    if (!target) return () => {};
    const onHide = () => {
      const snap = rig.controller.getSnapshot();
      if (!snap.sessionId || !(isLiveState(snap.state) || isBusyState(snap.state))) return;
      void rig.parts.api.close(snap.sessionId, "page_unload", { keepalive: true }).catch(() => {
        /* the page is going away; there is nobody to tell */
      });
    };
    target.addEventListener("pagehide", onHide);
    return () => target.removeEventListener("pagehide", onHide);
  }

  private probed = false;

  /**
   * Ask the server which contract it speaks before the owner clicks Connect,
   * so the voice selector can already say when the choice will not apply.
   * Done on first subscribe (an effect), never from `getSnapshot` (a render).
   */
  private probeOnce(): void {
    if (this.probed) return;
    this.probed = true;
    void this.ensureRig().controller.probeContract();
  }

  /** The rig, if one has been built; never builds one. */
  peekRig(): VoiceRig | null {
    return this.rig;
  }

  private publish(): void {
    const rig = this.rig;
    this.snapshot = rig
      ? {
          ready: true,
          controller: rig.controller.getSnapshot(),
          devices: this.devices,
          micId: this.micId,
          speakerId: this.speakerId,
          voice: this.voice,
          profile: rig.profile,
          applied: rig.applied(),
          simulated: rig.simulated,
          instances: voiceInstances.snapshot(),
        }
      : SERVER_SNAPSHOT;
    for (const listener of this.listeners) listener();
  }

  // ------------------------------------------------------------ observers

  /** `useSyncExternalStore` contract: stable until something changes. */
  getSnapshot = (): VoiceStoreSnapshot => {
    if (!this.rig) this.ensureRig();
    return this.snapshot;
  };

  /** The server has no rig and never builds one. */
  getServerSnapshot = (): VoiceStoreSnapshot => SERVER_SNAPSHOT;

  subscribe = (listener: () => void): (() => void) => {
    this.ensureRig();
    this.probeOnce();
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  // ------------------------------------------------------------- commands

  async refreshDevices(): Promise<void> {
    const list = this.options.listDevices ?? listAudioDevices;
    this.devices = await list();
    // The selected device's profile (labels are only known after a grant).
    const device = this.micId ? this.devices.find((d) => d.kind === "audioinput" && d.deviceId === this.micId) : undefined;
    if (device && device.label) this.ensureRig().resolveProfile(device);
    this.publish();
  }

  /** Open the one session. A no-op while one is live or being created. */
  async connect(): Promise<void> {
    const rig = this.ensureRig();
    const state = rig.controller.getSnapshot().state;
    if (isLiveState(state) || isBusyState(state)) return;
    await rig.connect({ deviceId: this.micId || undefined, voice: this.voice });
    // Labels are only revealed after a getUserMedia grant.
    await this.refreshDevices();
  }

  async disconnect(): Promise<void> {
    const rig = this.ensureRig();
    await rig.disconnect();
    this.publish();
  }

  /** Close whatever is open (if anything) and open a fresh session. */
  async reconnect(): Promise<void> {
    const rig = this.ensureRig();
    const state = rig.controller.getSnapshot().state;
    if (isBusyState(state)) return;
    if (isLiveState(state)) await rig.disconnect();
    await this.connect();
  }

  async setMicrophone(deviceId: string): Promise<void> {
    const rig = this.ensureRig();
    this.micId = deviceId;
    const device = this.devices.find((d) => d.kind === "audioinput" && d.deviceId === deviceId);
    if (device && device.label) rig.resolveProfile(device);
    this.publish();
    const state = rig.controller.getSnapshot().state;
    if (["listening", "speaking", "tool_running", "interrupted"].includes(state)) {
      // A new device: its own profile, its own calibration (the detector recalibrates on start).
      await rig.controller.switchMicrophone(deviceId);
      this.publish();
    }
  }

  async setSpeaker(deviceId: string): Promise<void> {
    const rig = this.ensureRig();
    this.speakerId = deviceId;
    this.publish();
    await rig.parts.playback.setOutputDevice?.(deviceId);
  }

  setVoice(value: string): void {
    this.ensureRig();
    const choice = normalizeVoiceChoice(value);
    this.voice = choice;
    saveVoiceChoice(choice, this.options.storage);
    this.publish();
  }

  setNoiseMode(mode: EnvironmentMode): void {
    this.patchProfile({ environmentMode: mode });
  }

  patchProfile(partial: Partial<MicrophoneProfile>): void {
    const rig = this.ensureRig();
    const current = rig.profile;
    if (!current) return;
    rig.commitProfile({ ...current, ...partial });
    rig.parts.detector.applyPreferences?.();
  }

  /** Persist a whole profile (the diagnostics benchmark writes one). */
  commitProfile(profile: MicrophoneProfile): void {
    this.ensureRig().commitProfile(profile);
  }

  recalibrate(): void {
    this.ensureRig().parts.detector.recalibrate?.(2);
  }

  // ---------------------------------------------------------- measurements

  /** The local gate's live snapshot (diagnostics), or null when it has none. */
  detectorSnapshot(): GatedDetectorSnapshot | null {
    return this.rig?.parts.detector.snapshot?.() ?? null;
  }

  /**
   * The owner's microphone level as a bounded 0..1 figure, or `null` when the
   * gate is not running (no measurement exists). Derived from the gate's own
   * RMS the way `/voice`'s meter is: −80 dBFS → 0, 0 dBFS → 1.
   */
  micLevel(): number | null {
    const snap = this.detectorSnapshot();
    if (!snap || snap.phase !== "running") return null;
    const rmsDb = snap.gate.rmsDb;
    if (!Number.isFinite(rmsDb)) return null;
    return Math.min(1, Math.max(0, (rmsDb + 80) / 80));
  }

  /** The assistant's real output envelope, from the playback analyser. */
  outputLevel(): number | null {
    return this.rig?.parts.playback.outputLevel?.() ?? null;
  }

  /** Tests only: tear the rig down so the next store starts from nothing. */
  dispose(): void {
    for (const unsub of this.unsubs) unsub();
    this.unsubs = [];
    this.rig?.dispose();
    this.rig = null;
    this.probed = false;
    this.listeners.clear();
    this.snapshot = SERVER_SNAPSHOT;
  }
}

// ------------------------------------------------------------- singleton

let singleton: VoiceStore | null = null;

/**
 * The tab's one store. Constructing it is free of side effects (the rig is
 * built on first subscribe/snapshot, never at import), so the server can
 * import this module and still never touch a browser API; tests never call
 * this and build their own `VoiceStore` from fakes.
 */
export function getVoiceStore(): VoiceStore {
  if (singleton) return singleton;
  singleton = new VoiceStore({ build: browserRigParts({ fetcher: apiFetch }) });
  return singleton;
}

/** Tests only: replace the singleton (e.g. with one built from fakes). */
export function installVoiceStore(store: VoiceStore | null): void {
  singleton?.dispose();
  singleton = store;
}

// ----------------------------------------------------------------- views

/** The owner-facing name of the selected microphone. */
export function microphoneName(snapshot: VoiceStoreSnapshot): string {
  return (
    snapshot.applied?.label ||
    snapshot.profile?.friendlyName ||
    snapshot.devices.find((d) => d.kind === "audioinput" && d.deviceId === snapshot.micId)?.label ||
    "Varsayılan"
  );
}

/** The owner-facing name of the selected speaker. */
export function speakerName(snapshot: VoiceStoreSnapshot): string {
  return snapshot.devices.find((d) => d.kind === "audiooutput" && d.deviceId === snapshot.speakerId)?.label || "Varsayılan";
}
