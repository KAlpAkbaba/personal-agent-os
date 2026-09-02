/**
 * Browser audio adapters for the voice client: device enumeration,
 * microphone capture with the browser's own processing REQUESTED AND READ
 * BACK (ADR-0044 layer 1), a playback path that can be silenced instantly,
 * the calibrated local speech gate (layers 2–3) that feeds barge-in without
 * waiting for the provider's VAD round trip, and the network monitor.
 * Browser-only — the controller never imports this file; the page wires it in.
 */

import {
  type CalibrationResult,
  type DerivedGateParameters,
  deriveGateParameters,
  ENVIRONMENT_CLASS_INDEX,
  type EnvironmentMode,
  NoiseFloorCalibrator,
  RunningNoiseFloor,
  SENSITIVITY_INDEX,
  type VadSensitivity,
} from "./calibration";
import { type Denoiser, PassthroughDenoiser } from "./denoiser";
import { analyseFrame } from "./dsp";
import { type GateSnapshot, SpeechGate } from "./gate";
import type {
  AppliedInputSettings,
  AudioDevice,
  Microphone,
  MicrophoneConstraints,
  NetworkMonitor,
  Playback,
  SpeechDetector,
  SpeechDetectorCalibration,
  SpeechDetectorStats,
} from "./ports";
import type { AudioOutput, Unsubscribe } from "./transport";
import type { UplinkShaper } from "./uplink";

export async function listAudioDevices(): Promise<AudioDevice[]> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.enumerateDevices) return [];
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((d) => d.kind === "audioinput" || d.kind === "audiooutput")
    .map((d, i) => ({
      deviceId: d.deviceId,
      kind: d.kind as AudioDevice["kind"],
      label: d.label || `${d.kind === "audioinput" ? "Mikrofon" : "Hoparlör"} ${i + 1}`,
      groupId: d.groupId,
    }));
}

// ------------------------------------------------------------ microphone

/**
 * What we ask for when no profile says otherwise. AGC is OFF: on a sensitive
 * microphone it lifts the room into the uplink; the profile's A/B benchmark is
 * the only thing that turns it on. voiceIsolation is opportunistic.
 */
export const DEFAULT_MICROPHONE_CONSTRAINTS: MicrophoneConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: false,
  channelCount: 1,
  voiceIsolation: true,
};

const READ_BACK_BOOLEANS = [
  "echoCancellation",
  "noiseSuppression",
  "autoGainControl",
  "voiceIsolation",
  "suppressLocalAudioPlayback",
] as const;

const MAX_VERBATIM_KEYS = 40;

function bounded(record: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!record) return null;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(record).slice(0, MAX_VERBATIM_KEYS)) {
    const value = record[key];
    out[key] = typeof value === "string" ? value.slice(0, 120) : value;
  }
  return out;
}

function boolOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Read the applied MediaTrackSettings/Capabilities back; never assume a constraint was honoured. */
export function readBackTrack(
  track: MediaStreamTrack,
  requested: MicrophoneConstraints,
  supportedConstraints: Record<string, boolean>,
): AppliedInputSettings {
  let settings: Record<string, unknown> = {};
  try {
    settings = { ...(track.getSettings() as Record<string, unknown>) };
  } catch {
    settings = {};
  }
  let capabilities: Record<string, unknown> | null = null;
  try {
    const withCaps = track as MediaStreamTrack & { getCapabilities?: () => Record<string, unknown> };
    capabilities = typeof withCaps.getCapabilities === "function" ? { ...withCaps.getCapabilities() } : null;
  } catch {
    capabilities = null;
  }
  const notHonoured: string[] = [];
  for (const key of READ_BACK_BOOLEANS) {
    const wanted = requested[key];
    if (wanted === undefined) continue;
    if (settings[key] !== wanted) notHonoured.push(key);
  }
  return {
    label: track.label ?? "",
    deviceId: typeof settings.deviceId === "string" ? settings.deviceId : null,
    groupId: typeof settings.groupId === "string" ? settings.groupId : null,
    echoCancellation: boolOrNull(settings.echoCancellation),
    noiseSuppression: boolOrNull(settings.noiseSuppression),
    autoGainControl: boolOrNull(settings.autoGainControl),
    voiceIsolation: boolOrNull(settings.voiceIsolation),
    suppressLocalAudioPlayback: boolOrNull(settings.suppressLocalAudioPlayback),
    channelCount: numberOrNull(settings.channelCount),
    sampleRate: numberOrNull(settings.sampleRate),
    notHonoured,
    requested,
    settings: bounded(settings) ?? {},
    capabilities: bounded(capabilities),
    supportedConstraints,
  };
}

export type BrowserMicrophoneOptions = {
  /** Per-device constraints (from the MicrophoneProfile); merged over the defaults. */
  constraintsFor?: (deviceId?: string) => Partial<MicrophoneConstraints>;
  /** Denoiser seam; the passthrough returns the captured stream itself. */
  denoiser?: Denoiser;
  /** Uplink shaper seam, resolved at open time (null/passthrough = untouched track). */
  uplinkFor?: () => UplinkShaper | null;
};

export class BrowserMicrophone implements Microphone {
  stream: MediaStream | null = null;
  applied: AppliedInputSettings | null = null;
  /** The capture as the browser delivered it — what the gate analyses. */
  raw: MediaStream | null = null;
  private uplink: UplinkShaper | null = null;
  private readonly denoiser: Denoiser;

  constructor(private readonly options: BrowserMicrophoneOptions = {}) {
    this.denoiser = options.denoiser ?? new PassthroughDenoiser();
  }

  async open(deviceId?: string, constraints?: Partial<MicrophoneConstraints>): Promise<MediaStream> {
    this.close();
    const requested: MicrophoneConstraints = {
      ...DEFAULT_MICROPHONE_CONSTRAINTS,
      ...this.options.constraintsFor?.(deviceId),
      ...constraints,
      channelCount: 1,
    };
    let supported: Record<string, boolean> = {};
    try {
      supported = navigator.mediaDevices.getSupportedConstraints() as unknown as Record<string, boolean>;
    } catch {
      supported = {};
    }
    const audio: MediaTrackConstraints & Record<string, unknown> = {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      echoCancellation: requested.echoCancellation,
      noiseSuppression: requested.noiseSuppression,
      autoGainControl: requested.autoGainControl,
      channelCount: 1,
    };
    // Opportunistic constraints are only mentioned when the browser lists them;
    // as ideal (non-exact) values they never make getUserMedia fail.
    if (requested.voiceIsolation !== undefined && supported.voiceIsolation) {
      audio.voiceIsolation = requested.voiceIsolation;
    }
    if (requested.suppressLocalAudioPlayback !== undefined && supported.suppressLocalAudioPlayback) {
      audio.suppressLocalAudioPlayback = requested.suppressLocalAudioPlayback;
    }
    const raw = await navigator.mediaDevices.getUserMedia({ audio, video: false });
    const track = raw.getAudioTracks()[0];
    this.applied = track ? readBackTrack(track, requested, supported) : null;
    this.raw = raw;
    const denoised = await this.denoiser.attach(raw);
    this.uplink = this.options.uplinkFor?.() ?? null;
    this.stream = this.uplink && !this.uplink.bypass ? this.uplink.attach(denoised) : denoised;
    return this.stream;
  }

  close(): void {
    this.uplink?.detach();
    this.uplink = null;
    this.denoiser.detach();
    for (const track of this.raw?.getTracks() ?? []) track.stop();
    if (this.stream && this.stream !== this.raw) for (const track of this.stream.getTracks()) track.stop();
    this.raw = null;
    this.stream = null;
    this.applied = null;
  }
}

/**
 * Stand-alone ambient measurement on any stream (the AGC A/B benchmark uses
 * it on a temporary capture). Derived numbers only; the context is closed
 * when the window completes.
 */
export function measureNoiseFloor(
  stream: MediaStream,
  durationMs = 2000,
  options: { fftSize?: number; pollMs?: number } = {},
): Promise<CalibrationResult> {
  return new Promise((resolve, reject) => {
    const context = new AudioContext();
    const analyser = context.createAnalyser();
    analyser.fftSize = options.fftSize ?? 1024;
    const source = context.createMediaStreamSource(stream);
    source.connect(analyser);
    const calibrator = new NoiseFloorCalibrator({ durationMs });
    const frame = new Float32Array(analyser.fftSize);
    const started = performance.now();
    const poll = setInterval(() => {
      analyser.getFloatTimeDomainData(frame);
      const done = calibrator.push(analyseFrame(frame, context.sampleRate));
      if (done || performance.now() - started > durationMs * 3) {
        clearInterval(poll);
        source.disconnect();
        void context.close();
        const result = calibrator.result();
        if (result) resolve(result);
        else reject(new Error("ambient measurement did not complete"));
      }
    }, options.pollMs ?? 20);
  });
}

// -------------------------------------------------------------- playback

/**
 * Remote audio → (hidden, muted element keeps the WebRTC track flowing in
 * Chromium) → AudioContext source → gain → destination. `stop()` drives the
 * gain to zero on the audio thread's next quantum, which is the fastest
 * silence a page can produce; the provider-side cancel follows separately.
 */
export class WebAudioPlayback implements Playback {
  playing = false;
  private context: AudioContext | null = null;
  private gain: GainNode | null = null;
  private analyser: AnalyserNode | null = null;
  private element: HTMLAudioElement | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private activitySinks = new Set<(at: number) => void>();
  private poll: ReturnType<typeof setInterval> | null = null;
  private armedSinceActivity = false;

  constructor(private readonly activityThreshold = 0.01) {}

  private ensureContext(): AudioContext {
    if (!this.context) {
      this.context = new AudioContext();
      this.gain = this.context.createGain();
      this.analyser = this.context.createAnalyser();
      this.analyser.fftSize = 512;
      this.gain.connect(this.analyser);
      this.analyser.connect(this.context.destination);
      this.poll = setInterval(() => this.sample(), 20);
    }
    return this.context;
  }

  attach(output: AudioOutput): void {
    if (output.kind !== "stream") return;
    const context = this.ensureContext();
    void context.resume();
    if (!this.element) {
      this.element = document.createElement("audio");
      this.element.autoplay = true;
      this.element.muted = true;
      this.element.style.display = "none";
      document.body.appendChild(this.element);
    }
    this.element.srcObject = output.stream;
    void this.element.play().catch(() => undefined);
    this.source?.disconnect();
    this.source = context.createMediaStreamSource(output.stream);
    this.source.connect(this.gain as GainNode);
  }

  private sample(): void {
    if (!this.analyser || !this.armedSinceActivity) return;
    const buffer = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buffer);
    let sum = 0;
    for (const v of buffer) sum += v * v;
    const rms = Math.sqrt(sum / buffer.length);
    if (rms > this.activityThreshold) {
      this.armedSinceActivity = false;
      const at = performance.now();
      for (const sink of this.activitySinks) sink(at);
    }
  }

  stop(): number {
    this.playing = false;
    if (this.gain && this.context) {
      this.gain.gain.cancelScheduledValues(this.context.currentTime);
      this.gain.gain.setValueAtTime(0, this.context.currentTime);
    }
    this.armedSinceActivity = false;
    return performance.now();
  }

  arm(): void {
    this.playing = true;
    this.armedSinceActivity = true;
    if (this.gain && this.context) {
      this.gain.gain.cancelScheduledValues(this.context.currentTime);
      this.gain.gain.setValueAtTime(1, this.context.currentTime);
      void this.context.resume();
    }
  }

  onActivity(sink: (at: number) => void): Unsubscribe {
    this.activitySinks.add(sink);
    return () => this.activitySinks.delete(sink);
  }

  async setOutputDevice(deviceId: string): Promise<void> {
    const context = this.ensureContext() as AudioContext & {
      setSinkId?: (id: string) => Promise<void>;
    };
    if (typeof context.setSinkId === "function") await context.setSinkId(deviceId);
  }

  dispose(): void {
    if (this.poll) clearInterval(this.poll);
    this.poll = null;
    this.source?.disconnect();
    this.element?.remove();
    void this.context?.close();
    this.context = null;
    this.gain = null;
    this.analyser = null;
    this.element = null;
    this.source = null;
    this.activitySinks.clear();
  }
}

// ---------------------------------------------------------- speech gate

export type DetectorPhase = "idle" | "calibrating" | "running";

export type GatedDetectorOptions = {
  /** Speaker playback is a gate FEATURE (extra margin), never a mute. */
  playbackActive?: () => boolean;
  /** Read live at every (re)derivation so the owner's mode change applies at once. */
  mode?: () => EnvironmentMode;
  sensitivity?: () => VadSensitivity;
  /** > 0 only when the profile opts into gated attenuation (uplink.ts). */
  attenuationDb?: () => number;
  uplink?: () => UplinkShaper | null;
  /** Analyse this stream instead of the one handed to start() (the raw capture when a shaper is active). */
  analysisStream?: () => MediaStream | null;
  pollMs?: number;
  fftSize?: number;
  calibrationMs?: number;
  autoRecalibrate?: boolean;
  driftDb?: number;
  driftSustainMs?: number;
  recalibrateCooldownMs?: number;
  now?: () => number;
};

export type GatedDetectorSnapshot = {
  phase: DetectorPhase;
  calibrationProgress: number;
  calibration: CalibrationResult | null;
  params: DerivedGateParameters;
  gate: GateSnapshot;
  runningFloorDb: number | null;
  driftDb: number;
  /** 0 initial, 1 drift, 2 manual */
  lastTrigger: number;
  contaminatedRetries: number;
};

/**
 * The calibrated speech gate on the microphone stream. Barge-in and the
 * client's own turn bookkeeping listen to `onSpeechStart`/`onSpeechEnd`; the
 * uplink track itself is untouched (unless an UplinkShaper is opted in).
 */
export class GatedSpeechDetector implements SpeechDetector {
  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private poll: ReturnType<typeof setInterval> | null = null;
  private frame: Float32Array<ArrayBuffer> | null = null;
  private gate: SpeechGate;
  private params: DerivedGateParameters;
  private calibrator: NoiseFloorCalibrator | null = null;
  private calibration: CalibrationResult | null = null;
  private tracker: RunningNoiseFloor | null = null;
  private lastDrift = 0;
  private lastTrigger = 0;
  private lastCalibrationAt = -Infinity;
  private contaminatedRetries = 0;
  private startSinks = new Set<(at: number) => void>();
  private endSinks = new Set<(at: number) => void>();
  private calibrationSinks = new Set<(calibration: SpeechDetectorCalibration) => void>();
  private readonly now: () => number;

  constructor(private readonly options: GatedDetectorOptions = {}) {
    this.now = options.now ?? (() => performance.now());
    this.params = this.derive(null);
    this.gate = new SpeechGate(this.params);
  }

  private derive(calibration: CalibrationResult | null): DerivedGateParameters {
    return deriveGateParameters({
      calibration,
      mode: this.options.mode?.() ?? "auto",
      sensitivity: this.options.sensitivity?.() ?? "auto",
      attenuationDb: this.options.attenuationDb?.() ?? 0,
    });
  }

  start(stream: MediaStream): void {
    this.stop();
    this.context = new AudioContext();
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = this.options.fftSize ?? 1024;
    this.frame = new Float32Array(this.analyser.fftSize);
    this.source = this.context.createMediaStreamSource(this.options.analysisStream?.() ?? stream);
    this.source.connect(this.analyser);
    // A new stream is a new device or a new session: calibrate it on its own.
    this.calibration = null;
    this.contaminatedRetries = 0;
    this.gate.resetState();
    this.recalibrate(0);
    this.poll = setInterval(() => this.sample(), this.options.pollMs ?? 20);
  }

  /** Start a fresh ambient window; 0 initial, 1 drift, 2 manual ("Yeniden ölçümle"). */
  recalibrate(trigger = 2): void {
    this.calibrator = new NoiseFloorCalibrator({ durationMs: this.options.calibrationMs ?? 1800 });
    this.lastTrigger = trigger;
    if (trigger !== 0) this.contaminatedRetries = 0;
  }

  /** The owner changed the mode / sensitivity / attenuation preference: re-derive now. */
  applyPreferences(): void {
    this.params = this.derive(this.calibration);
    this.gate.setParameters(this.params);
  }

  private sample(): void {
    if (!this.analyser || !this.context || !this.frame) return;
    this.analyser.getFloatTimeDomainData(this.frame);
    const features = analyseFrame(this.frame, this.context.sampleRate);
    const now = this.now();
    const playback = this.options.playbackActive?.() ?? false;

    // Layer 2: the measurement skips frames while the assistant is audible so
    // its (echo-cancelled) residual never becomes "the room".
    if (this.calibrator && !playback && !this.gate.isOpen) {
      if (this.calibrator.push(features)) this.finishCalibration(now);
    }

    // Layer 3.
    for (const event of this.gate.update(features, now, playback)) {
      if (event.type === "open") for (const sink of this.startSinks) sink(event.at);
      else if (event.type === "close") for (const sink of this.endSinks) sink(event.at);
    }
    this.options.uplink?.()?.setGain(this.gate.uplinkGainTarget);

    // Running floor between turns; a sustained drift triggers recalibration.
    if (this.tracker && !this.gate.isOpen && !playback && !this.calibrator) {
      const drift = this.tracker.update(features.rmsDb, now);
      this.lastDrift = drift.driftDb;
      const cooldown = this.options.recalibrateCooldownMs ?? 30_000;
      if (
        drift.drifted &&
        (this.options.autoRecalibrate ?? true) &&
        now - this.lastCalibrationAt >= cooldown
      ) {
        this.recalibrate(1);
      }
    }
  }

  private finishCalibration(now: number): void {
    const result = this.calibrator?.result() ?? null;
    this.calibrator = null;
    if (!result) return;
    if (result.contaminated && this.contaminatedRetries < 2) {
      // The owner was talking: keep the current parameters and measure again.
      this.contaminatedRetries += 1;
      this.calibrator = new NoiseFloorCalibrator({ durationMs: this.options.calibrationMs ?? 1800 });
      return;
    }
    this.calibration = result;
    this.lastCalibrationAt = now;
    this.params = this.derive(result);
    this.gate.setParameters(this.params);
    this.gate.noteCalibration();
    if (this.tracker) this.tracker.reset(this.params.floorDb);
    else {
      this.tracker = new RunningNoiseFloor({
        calibratedDb: this.params.floorDb,
        driftDb: this.options.driftDb,
        sustainMs: this.options.driftSustainMs,
      });
    }
    const summary: SpeechDetectorCalibration = {
      noise_floor_db: result.noiseFloorDb,
      stationary_db: result.stationaryNoiseDb,
      spread_db: result.spreadDb,
      peak_db: result.peakDb,
      clip_risk: result.clipRisk,
      hum_ratio: result.humRatio,
      contaminated: result.contaminated ? 1 : 0,
      sensitivity: SENSITIVITY_INDEX[result.sensitivity],
      env: ENVIRONMENT_CLASS_INDEX[result.environment],
      open_margin_db: this.params.openMarginDb,
      min_onset_ms: this.params.minOnsetMs,
      hang_ms: this.params.hangMs,
      pre_roll_ms: this.params.preRollMs,
      trigger: this.lastTrigger,
    };
    for (const sink of this.calibrationSinks) sink(summary);
  }

  stop(): void {
    if (this.poll) clearInterval(this.poll);
    this.poll = null;
    this.source?.disconnect();
    void this.context?.close();
    this.context = null;
    this.analyser = null;
    this.source = null;
    this.frame = null;
    this.calibrator = null;
    this.gate.resetState();
  }

  onSpeechStart(sink: (at: number) => void): Unsubscribe {
    this.startSinks.add(sink);
    return () => this.startSinks.delete(sink);
  }

  onSpeechEnd(sink: (at: number) => void): Unsubscribe {
    this.endSinks.add(sink);
    return () => this.endSinks.delete(sink);
  }

  onCalibration(sink: (calibration: SpeechDetectorCalibration) => void): Unsubscribe {
    this.calibrationSinks.add(sink);
    return () => this.calibrationSinks.delete(sink);
  }

  stats(): SpeechDetectorStats {
    return this.gate.stats();
  }

  snapshot(): GatedDetectorSnapshot {
    return {
      phase: !this.context ? "idle" : this.calibrator ? "calibrating" : "running",
      calibrationProgress: this.calibrator?.progress ?? (this.calibration ? 1 : 0),
      calibration: this.calibration,
      params: this.params,
      gate: this.gate.snapshot(),
      runningFloorDb: this.tracker ? Math.round(this.tracker.running * 10) / 10 : null,
      driftDb: this.lastDrift,
      lastTrigger: this.lastTrigger,
      contaminatedRetries: this.contaminatedRetries,
    };
  }
}

/**
 * Layer 3 bypass: the pre-ADR-0044 RMS detector (energy only, fixed threshold).
 * Kept selectable from diagnostics so the owner can A/B the gate against it.
 */
export class RmsSpeechDetector implements SpeechDetector {
  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private poll: ReturnType<typeof setInterval> | null = null;
  private speaking = false;
  private aboveSince: number | null = null;
  private belowSince: number | null = null;
  private startSinks = new Set<(at: number) => void>();
  private endSinks = new Set<(at: number) => void>();

  constructor(
    private readonly options = { threshold: 0.02, attackMs: 40, releaseMs: 300, pollMs: 20 },
  ) {}

  start(stream: MediaStream): void {
    this.stop();
    this.context = new AudioContext();
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 512;
    this.source = this.context.createMediaStreamSource(stream);
    this.source.connect(this.analyser);
    this.poll = setInterval(() => this.sample(), this.options.pollMs);
  }

  private sample(): void {
    if (!this.analyser) return;
    const buffer = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buffer);
    let sum = 0;
    for (const v of buffer) sum += v * v;
    const rms = Math.sqrt(sum / buffer.length);
    const now = performance.now();
    if (rms > this.options.threshold) {
      this.belowSince = null;
      if (this.aboveSince === null) this.aboveSince = now;
      if (!this.speaking && now - this.aboveSince >= this.options.attackMs) {
        this.speaking = true;
        for (const sink of this.startSinks) sink(this.aboveSince);
      }
    } else {
      this.aboveSince = null;
      if (this.belowSince === null) this.belowSince = now;
      if (this.speaking && now - this.belowSince >= this.options.releaseMs) {
        this.speaking = false;
        for (const sink of this.endSinks) sink(this.belowSince);
      }
    }
  }

  stop(): void {
    if (this.poll) clearInterval(this.poll);
    this.poll = null;
    this.source?.disconnect();
    void this.context?.close();
    this.context = null;
    this.analyser = null;
    this.source = null;
    this.speaking = false;
    this.aboveSince = null;
    this.belowSince = null;
  }

  onSpeechStart(sink: (at: number) => void): Unsubscribe {
    this.startSinks.add(sink);
    return () => this.startSinks.delete(sink);
  }

  onSpeechEnd(sink: (at: number) => void): Unsubscribe {
    this.endSinks.add(sink);
    return () => this.endSinks.delete(sink);
  }
}

// ---------------------------------------------------------------- uplink

/**
 * Opt-in uplink attenuation (uplink.ts): microphone → gain → derived track.
 * Release (gain up) is 5 ms so a word onset is never held down; attack (gain
 * down) is slow so there is no pumping. Never reaches zero.
 */
export class WebAudioUplinkShaper implements UplinkShaper {
  readonly name = "webaudio-gain";
  readonly bypass = false;
  private context: AudioContext | null = null;
  private gain: GainNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private destination: MediaStreamAudioDestinationNode | null = null;
  private target = 1;

  attach(stream: MediaStream): MediaStream {
    this.detach();
    this.context = new AudioContext();
    this.source = this.context.createMediaStreamSource(stream);
    this.gain = this.context.createGain();
    this.destination = this.context.createMediaStreamDestination();
    this.source.connect(this.gain);
    this.gain.connect(this.destination);
    this.target = 1;
    return this.destination.stream;
  }

  setGain(gain: number): void {
    const clamped = Math.min(1, Math.max(0.1, gain));
    if (!this.gain || !this.context || clamped === this.target) return;
    const rising = clamped > this.target;
    this.target = clamped;
    this.gain.gain.cancelScheduledValues(this.context.currentTime);
    this.gain.gain.setTargetAtTime(clamped, this.context.currentTime, rising ? 0.005 : 0.05);
  }

  detach(): void {
    this.source?.disconnect();
    this.gain?.disconnect();
    void this.context?.close();
    this.context = null;
    this.gain = null;
    this.source = null;
    this.destination = null;
    this.target = 1;
  }
}

// --------------------------------------------------------------- network

export class BrowserNetworkMonitor implements NetworkMonitor {
  get online(): boolean {
    return typeof navigator === "undefined" ? true : navigator.onLine;
  }

  onChange(sink: (online: boolean) => void): Unsubscribe {
    const on = () => sink(true);
    const off = () => sink(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }
}
