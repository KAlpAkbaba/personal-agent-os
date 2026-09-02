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

export interface Microphone {
  open(deviceId?: string): Promise<MediaStream>;
  readonly stream: MediaStream | null;
  close(): void;
}

export interface SpeechDetector {
  start(stream: MediaStream): void;
  stop(): void;
  onSpeechStart(sink: (at: number) => void): Unsubscribe;
  onSpeechEnd(sink: (at: number) => void): Unsubscribe;
}

export interface NetworkMonitor {
  readonly online: boolean;
  onChange(sink: (online: boolean) => void): Unsubscribe;
}

export type AudioDevice = { deviceId: string; label: string; kind: "audioinput" | "audiooutput" };
