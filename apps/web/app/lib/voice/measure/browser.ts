/**
 * The real browser objects behind a measurement take's seams.
 *
 * - Microphone: `BrowserMicrophone`, the real voice path's own class, built the way the rig
 *   builds it (the profile's constraints, the passthrough denoiser). The samples come from
 *   its `stream`: the processed sound the system hears, browser noise suppression included.
 *   The gated-attenuation uplink shaper is not attached - it is driven by the session's
 *   speech detector, which does not run here.
 * - PCM capture: an AudioContext reading that stream through a ScriptProcessorNode (no
 *   worklet module to serve; the node writes silence to a muted gain).
 * - Recogniser: `SpeechRecognition` only when the browser can take a track (Chrome 135+).
 *   An older Chrome ignores the argument and would listen to the default microphone, a
 *   different sound - so below 135, or with no SpeechRecognition at all, there is none.
 */

import { BrowserMicrophone } from "../audio";
import { PassthroughDenoiser } from "../denoiser";
import type { Clock, PcmCapture, RecognizerLike, RecorderDeps } from "./recorder";

/** The first Chrome whose `SpeechRecognition.start()` accepts a MediaStreamTrack. */
export const TRACK_START_MIN_CHROME = 135;

export function chromeMajor(userAgent: string): number | null {
  const match = /Chrome\/(\d+)/.exec(userAgent);
  return match ? Number(match[1]) : null;
}

export function browserRecognizer(): (() => RecognizerLike) | null {
  if (typeof window === "undefined") return null;
  const scope = window as unknown as {
    SpeechRecognition?: new () => RecognizerLike;
    webkitSpeechRecognition?: new () => RecognizerLike;
  };
  const Recognition = scope.SpeechRecognition ?? scope.webkitSpeechRecognition;
  if (!Recognition) return null;
  const major = chromeMajor(navigator.userAgent);
  if (major === null || major < TRACK_START_MIN_CHROME) return null;
  return () => new Recognition();
}

export function scriptProcessorCapture(stream: MediaStream): PcmCapture {
  const context = new AudioContext();
  try {
    const source = context.createMediaStreamSource(stream);
    const node = context.createScriptProcessor(4096, 1, 1);
    const mute = context.createGain();
    mute.gain.value = 0;
    const chunks: Float32Array[] = [];
    node.onaudioprocess = (event) => {
      chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    };
    source.connect(node);
    node.connect(mute);
    mute.connect(context.destination);
    return {
      sampleRate: context.sampleRate,
      async stop() {
        node.onaudioprocess = null;
        source.disconnect();
        node.disconnect();
        mute.disconnect();
        await context.close().catch(() => {});
        const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
        const out = new Float32Array(total);
        let at = 0;
        for (const chunk of chunks) {
          out.set(chunk, at);
          at += chunk.length;
        }
        return out;
      },
    };
  } catch (error) {
    void context.close().catch(() => {});
    throw error;
  }
}

const browserClock: Clock = {
  setTimeout(fn, ms) {
    const id = window.setTimeout(fn, ms);
    return () => window.clearTimeout(id);
  },
};

/** Fresh seams for one take: a new probe microphone that the take closes in its finally. */
export function browserRecorderDeps(): Omit<RecorderDeps, "upload"> {
  return {
    microphone: new BrowserMicrophone({ denoiser: new PassthroughDenoiser() }),
    capture: scriptProcessorCapture,
    recognizer: browserRecognizer(),
    clock: browserClock,
  };
}
