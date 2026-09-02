/**
 * The device-side ports the controller depends on besides the transport:
 * playback that can be silenced instantly, a microphone, an optional local
 * speech detector (so barge-in does not wait for the provider's VAD round
 * trip), and a network monitor. Browser implementations live in audio.ts;
 * deterministic fakes in fake.ts.
 */

import type { AudioOutput, Unsubscribe } from "./transport";

export interface Playback {
  attach(output: AudioOutput): void;
  /**
   * Silence the output path NOW (synchronous) and return the monotonic time
   * at which it went silent. This is the latency-critical barge-in step.
   */
  stop(): number;
  /** Re-enable output for the next response. */
  arm(): void;
  readonly playing: boolean;
  /** First audible energy after `arm()`; used for first-audio timing. */
  onActivity(sink: (at: number) => void): Unsubscribe;
  setOutputDevice?(deviceId: string): Promise<void>;
  dispose(): void;
}

/** Layer 1 (ADR-0044): what we ASK the browser for; what it did is read back. */
export type MicrophoneConstraints = {
  echoCancellation: boolean;
  noiseSuppression: boolean;
  /** Never blindly on: a per-profile preference, default off for a sensitive microphone. */
  autoGainControl: boolean;
  channelCount: 1;
  /** Opportunistic (Chromium 148+ lists it in getSupportedConstraints); read back, never assumed. */
  voiceIsolation?: boolean;
  /** Open-speaker echo case; evaluated on the owner's device, not assumed. */
  suppressLocalAudioPlayback?: boolean;
};

/** The applied MediaTrackSettings / capabilities read back after getUserMedia. */
export type AppliedInputSettings = {
  label: string;
  deviceId: string | null;
  groupId: string | null;
  echoCancellation: boolean | null;
  noiseSuppression: boolean | null;
  autoGainControl: boolean | null;
  voiceIsolation: boolean | null;
  suppressLocalAudioPlayback: boolean | null;
  channelCount: number | null;
  sampleRate: number | null;
  /** Requested booleans the track did NOT honour (constraint names). */
  notHonoured: string[];
  requested: MicrophoneConstraints;
  /** `track.getSettings()` verbatim (bounded for display). */
  settings: Record<string, unknown>;
  /** `track.getCapabilities()` verbatim where available, else null. */
  capabilities: Record<string, unknown> | null;
  supportedConstraints: Record<string, boolean>;
};

export interface Microphone {
  open(deviceId?: string, constraints?: Partial<MicrophoneConstraints>): Promise<MediaStream>;
  readonly stream: MediaStream | null;
  /** Read back from the last successful open; null before / after close. */
  readonly applied?: AppliedInputSettings | null;
  close(): void;
}

/** Per-session counters for the noise qualification matrix — numbers only. */
export type SpeechDetectorStats = {
  gate_opens: number;
  /** Energy events above the floor the gate classified as background (not clicks). */
  gated_out: number;
  /** Bursts too short to be anything but a click/knock. */
  click_rejects: number;
  speech_ms: number;
  calibrations: number;
};

/** A completed noise-floor calibration, reduced to reportable numbers. */
export type SpeechDetectorCalibration = {
  noise_floor_db: number;
  stationary_db: number;
  spread_db: number;
  peak_db: number;
  clip_risk: number;
  hum_ratio: number;
  contaminated: 0 | 1;
  /** 0 low, 1 normal, 2 high */
  sensitivity: number;
  /** 0 quiet, 1 normal, 2 noisy, 3 very noisy */
  env: number;
  /** derived gate parameters actually applied */
  open_margin_db: number;
  min_onset_ms: number;
  hang_ms: number;
  pre_roll_ms: number;
  /** 1 when triggered by drift, 2 manual, 0 initial */
  trigger: number;
};

export interface SpeechDetector {
  start(stream: MediaStream): void;
  stop(): void;
  onSpeechStart(sink: (at: number) => void): Unsubscribe;
  onSpeechEnd(sink: (at: number) => void): Unsubscribe;
  /** Optional: counters for the qualification matrix. */
  stats?(): SpeechDetectorStats;
  /** Optional: fires when a noise-floor calibration completes. */
  onCalibration?(sink: (calibration: SpeechDetectorCalibration) => void): Unsubscribe;
}

export interface NetworkMonitor {
  readonly online: boolean;
  onChange(sink: (online: boolean) => void): Unsubscribe;
}

export type AudioDevice = {
  deviceId: string;
  label: string;
  kind: "audioinput" | "audiooutput";
  groupId: string;
};
