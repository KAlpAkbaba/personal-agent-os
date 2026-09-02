/**
 * Uplink shaper seam (ADR-0044 §4). The gate computes an `uplinkGain` target
 * (1 = untouched; < 1 only during floor-level background when the profile's
 * `inputGainStrategy` is "gated_attenuation" and `attenuationDb` > 0). Applying
 * it means routing the microphone through Web Audio and handing WebRTC a
 * derived track, which is a change to the proven low-latency path — so the
 * default is the passthrough below, and the Web Audio implementation in
 * audio.ts is opt-in from the diagnostics view for the owner's own A/B.
 *
 * The gate never asks for attenuation while it is open, while an onset
 * candidate is pending, or while the level is more than a few dB above the
 * floor, so word beginnings and quiet syllables are never attenuated; only the
 * stationary floor between words is. It is never a mute.
 */
export interface UplinkShaper {
  readonly name: string;
  readonly bypass: boolean;
  /** Returns the stream the transport should send; the passthrough returns the input. */
  attach(stream: MediaStream): MediaStream;
  /** Target linear gain (0..1]; ramped by the implementation, ignored by the passthrough. */
  setGain(gain: number): void;
  detach(): void;
}

export class PassthroughUplinkShaper implements UplinkShaper {
  readonly name = "passthrough";
  readonly bypass = true;

  attach(stream: MediaStream): MediaStream {
    return stream;
  }

  setGain(): void {
    /* the uplink is untouched */
  }

  detach(): void {
    /* nothing inserted */
  }
}
