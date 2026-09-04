/**
 * The device-side ports the controller depends on besides the transport:
 * playback that can be silenced instantly, a microphone, an optional local
 * speech detector (so barge-in does not wait for the provider's VAD round
 * trip), and a network monitor. Browser implementations live in audio.ts;
 * deterministic fakes in fake.ts.
 */

import type { AudioOutput, Unsubscribe } from "./transport";

/**
 * What a silence request measured (ADR-0047 §2): `at` is the main-thread
 * monotonic time the request was made; `gainZeroAt` is when the audio thread
 * will have the gain at zero AT THE OUTPUT (mapped onto the same clock; null
 * when the platform cannot say); `outputLatencyMs` is the context's own
 * output latency; `changed` is false when the path was already silent.
 */
export type PlaybackStop = {
  at: number;
  gainZeroAt: number | null;
  outputLatencyMs: number | null;
  changed: boolean;
};

export interface Playback {
  attach(output: AudioOutput): void;
  /**
   * Silence the output path NOW (synchronous). This is the latency-critical
   * barge-in step; `playing` becomes false.
   */
  stop(): PlaybackStop;
  /**
   * Reversible silence (ADR-0047 §2): the gate saw a confident onset while the
   * assistant was audible. `playing` stays true; `unmute()` restores the gain
   * if the onset turns out to be nothing.
   */
  mute(): PlaybackStop;
  unmute(): void;
  /** Re-enable output for the next response. */
  arm(): void;
  /** Create/resume the output path inside a user gesture, so the first response never waits for it. */
  prepare?(): void;
  readonly playing: boolean;
  readonly muted: boolean;
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
  /** MediaTrackSettings.latency (seconds) as milliseconds where the browser reports it; null otherwise. */
  inputLatencyMs: number | null;
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
  /** ADR-0047 §2: reversible playback mutes the gate asked for, and how many collapsed. */
  evidence_events?: number;
  evidence_lost?: number;
  /** ADR-0047 §4: measured residual of the assistant's playback in the microphone (omitted until measured). */
  echo_residual_db?: number;
  /** The playback margin in force (preset or echo-derived). */
  echo_margin_db?: number;
};

/**
 * A completed noise-floor calibration, reduced to reportable numbers
 * (ADR-0047 §5): `measured: 1` and `samples` on every measurement, classes
 * 1-based, `peak_db` absent when no peak was observed — never a sentinel.
 */
export type SpeechDetectorCalibration = {
  measured: 1;
  /** live frames the numbers came from */
  samples: number;
  noise_floor_db: number;
  stationary_db: number;
  spread_db: number;
  peak_db?: number;
  clip_risk: number;
  hum_ratio: number;
  contaminated: 0 | 1;
  /** 1 low, 2 normal, 3 high */
  sensitivity_class: number;
  /** 1 quiet, 2 normal, 3 noisy, 4 very noisy */
  env_class: number;
  /** derived gate parameters actually applied */
  open_margin_db: number;
  min_onset_ms: number;
  hang_ms: number;
  pre_roll_ms: number;
  echo_margin_db: number;
  /** 1 when triggered by drift, 2 manual, 0 initial */
  trigger: number;
  /** all-zero frames skipped while measuring */
  dead_frames: number;
};

/** A calibration window that produced NO measurement (dead input, or contaminated and retried). */
export type SpeechDetectorCalibrationAttempt = {
  measured: 0;
  samples: number;
  dead_frames: number;
  contaminated: 0 | 1;
  trigger: number;
  /** 1 when another window follows, 0 when the detector gave up for now */
  retry: 0 | 1;
};

/**
 * The local gate's measured level for the onset under evaluation (two-stage
 * interruption, interruption.ts): the PEAK margin above the gate's open
 * threshold (floor + open margin + playback margin, learned offsets included)
 * and the mean spectral score over the onset's frames. Numbers only.
 */
export type OnsetLevel = {
  marginDb: number;
  spectralScore: number;
  frames: number;
};

/** Onset accounting handed to the controller with a local speech start (ADR-0047 §1). */
export type SpeechStartDetail = {
  candidateAt: number;
  decidedAt: number;
  preRollMs: number;
  /** main-thread observation of the onset frame minus its audio-thread time; null when unmeasurable */
  captureLagMs: number | null;
  duringPlayback: boolean;
};

export interface SpeechDetector {
  start(stream: MediaStream): void;
  stop(): void;
  onSpeechStart(sink: (at: number, detail?: SpeechStartDetail) => void): Unsubscribe;
  onSpeechEnd(sink: (at: number) => void): Unsubscribe;
  /** Optional: counters for the qualification matrix. */
  stats?(): SpeechDetectorStats;
  /** Optional: fires when a noise-floor calibration window completes (measurement or attempt). */
  onCalibration?(sink: (calibration: SpeechDetectorCalibration | SpeechDetectorCalibrationAttempt) => void): Unsubscribe;
  /** Optional (ADR-0047 §2): confident onset during playback → reversible local mute; and its collapse. */
  onEvidence?(sink: (at: number, candidateAt: number) => void): Unsubscribe;
  onEvidenceLost?(sink: (at: number) => void): Unsubscribe;
  /** Optional: the onset candidate under evaluation right now (provider-first barge-ins use it as the onset). */
  onsetCandidate?(): { candidateAt: number; preRollMs: number } | null;
  /**
   * Optional: the level of the onset the gate is evaluating or has opened on;
   * null when the gate sees no candidate and is closed (the speech never
   * cleared the calibrated margin). Absent = the detector measures no levels.
   */
  onsetLevel?(): OnsetLevel | null;
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
