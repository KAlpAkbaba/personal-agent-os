/**
 * Browser audio adapters for the voice client: device enumeration,
 * microphone capture with AEC/NS/AGC, a playback path that can be silenced
 * instantly, a local RMS speech detector (so barge-in does not wait for the
 * provider's VAD round trip), and the network monitor. Browser-only — the
 * controller never imports this file; the page wires it in.
 */

import type { AudioDevice, Microphone, NetworkMonitor, Playback, SpeechDetector } from "./ports";
import type { AudioOutput, Unsubscribe } from "./transport";

export async function listAudioDevices(): Promise<AudioDevice[]> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.enumerateDevices) return [];
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((d) => d.kind === "audioinput" || d.kind === "audiooutput")
    .map((d, i) => ({
      deviceId: d.deviceId,
      kind: d.kind as AudioDevice["kind"],
      label: d.label || `${d.kind === "audioinput" ? "Mikrofon" : "Hoparlör"} ${i + 1}`,
    }));
}

export class BrowserMicrophone implements Microphone {
  stream: MediaStream | null = null;

  async open(deviceId?: string): Promise<MediaStream> {
    this.close();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
      video: false,
    });
    this.stream = stream;
    return stream;
  }

  close(): void {
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;
  }
}

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

/** Local RMS-based speech detector on the microphone stream. */
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
