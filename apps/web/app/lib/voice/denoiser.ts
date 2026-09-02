/**
 * Denoiser seam (ADR-0044 §6). NOT an RNNoise / WebRTC-APM implementation:
 * this task deliberately ships only the port and a passthrough, because the
 * owner's rule is that Turkish intelligibility beats denoising and an
 * advanced denoiser is promoted only behind measurements on the real
 * microphone. The microphone path calls `attach()` on whatever `Denoiser` the
 * page wires in; the passthrough returns the very same stream, so the
 * low-latency WebRTC track path is untouched.
 *
 * Promotion checklist — every item must hold on the owner's device before a
 * real denoiser replaces the passthrough (the bypass path stays selectable):
 */
export const DENOISER_PROMOTION_CHECKLIST = [
  "substantially better speech/noise separation than browser noiseSuppression on the 14-scenario matrix",
  "no unacceptable added latency on the uplink (budget: a few tens of ms, measured mic_to_uplink_ms)",
  "no metallic / artificial Turkish — owner listening test, not a metric",
  "no loss of quiet syllables or Turkish consonants (ç, ş, ğ, h, f, s onsets/endings)",
  "no harm to barge-in in headset and open-speaker modes (barge_in_to_stop_ms unchanged)",
  "bypass path kept: the owner can switch back to passthrough without a release",
] as const;

export interface Denoiser {
  readonly name: string;
  /** True for the passthrough: the stream returned is the input stream. */
  readonly bypass: boolean;
  /** Returns the stream the uplink should carry; may be the input itself. */
  attach(stream: MediaStream): Promise<MediaStream>;
  detach(): void;
}

export class PassthroughDenoiser implements Denoiser {
  readonly name = "passthrough";
  readonly bypass = true;

  attach(stream: MediaStream): Promise<MediaStream> {
    return Promise.resolve(stream);
  }

  detach(): void {
    /* nothing was inserted */
  }
}
