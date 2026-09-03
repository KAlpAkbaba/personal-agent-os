/**
 * Latency decomposition helpers (ADR-0047). Pure: the clock, the scheduler
 * and the stats source are injected, so the same code runs against the
 * browser's RTCRtpSender and against the deterministic fakes.
 *
 * - `UplinkProbe`: after the local gate opened, poll the transport's outbound
 *   audio counters and record the first INCREASE of `packetsSent` as the real
 *   first uplink packet (basis "rtp_stats"). Bounded; null when the transport
 *   has no counters or nothing was sent inside the bound.
 * - `mapAudioTimeToMainClock`: the audio thread's `contextTime` mapped onto
 *   the main thread's monotonic clock through a `(contextTime,
 *   performanceTime)` pair, so an analyser frame's end and a gain change's
 *   effective time can be compared with `performance.now()`.
 */

import type { Scheduler } from "./events";
import type { OutboundAudioStats } from "./transport";

export type UplinkProbeResult = {
  /** main-thread time the increase was first observed */
  rtpAt: number;
  /** `rtpAt − origin` */
  rtpMs: number;
  /** packets sent between the baseline and the observation */
  packets: number;
  polls: number;
};

export type UplinkProbeOptions = {
  stats: () => Promise<OutboundAudioStats | null>;
  now: () => number;
  scheduler: Scheduler;
  /** Poll interval; the observation resolution. */
  pollMs?: number;
  /** Give up after this long past the origin. */
  maxMs?: number;
};

export class UplinkProbe {
  private cancelled = false;
  private readonly pollMs: number;
  private readonly maxMs: number;

  constructor(private readonly options: UplinkProbeOptions) {
    this.pollMs = options.pollMs ?? 10;
    this.maxMs = options.maxMs ?? 400;
  }

  cancel(): void {
    this.cancelled = true;
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => {
      this.options.scheduler.setTimeout(resolve, ms);
    });
  }

  /** `origin` is the gate's decision time on the main clock. Never throws. */
  async run(origin: number): Promise<UplinkProbeResult | null> {
    let baseline: OutboundAudioStats | null;
    try {
      baseline = await this.options.stats();
    } catch {
      return null;
    }
    if (!baseline) return null;
    let polls = 0;
    while (!this.cancelled && this.options.now() - origin < this.maxMs) {
      await this.sleep(this.pollMs);
      if (this.cancelled) return null;
      let sample: OutboundAudioStats | null;
      try {
        sample = await this.options.stats();
      } catch {
        return null;
      }
      polls += 1;
      if (!sample) return null;
      if (sample.packetsSent > baseline.packetsSent) {
        const rtpAt = this.options.now();
        return { rtpAt, rtpMs: Math.max(0, rtpAt - origin), packets: sample.packetsSent - baseline.packetsSent, polls };
      }
    }
    return null;
  }
}

export type ClockPair = { contextTime: number; performanceTime: number };

/**
 * Map an audio-thread time (seconds on the AudioContext clock) onto the main
 * thread's clock (ms) given a pair from `AudioContext.getOutputTimestamp()`.
 * The pair says when render position `contextTime` reaches the OUTPUT; for an
 * input frame the render happened `outputLatencyMs` earlier.
 */
export function mapAudioTimeToMainClock(
  audioTimeSeconds: number,
  pair: ClockPair | null,
  outputLatencyMs = 0,
): number | null {
  if (!pair || !Number.isFinite(pair.contextTime) || !Number.isFinite(pair.performanceTime)) return null;
  return pair.performanceTime + (audioTimeSeconds - pair.contextTime) * 1000 - outputLatencyMs;
}

/** A finite, non-negative rounded ms value, or undefined (so the key is omitted, never a sentinel). */
export function msOrOmit(value: number | null | undefined): number | undefined {
  if (value === null || value === undefined || !Number.isFinite(value)) return undefined;
  return Math.max(0, Math.round(value));
}
