"use client";

/**
 * B23 req 722: what the machine actually delivers, measured.
 *
 * The tiers, their budgets and the owner's control have existed since M18; nothing ever
 * compared the tier to the frames the machine produced, so a Core that stuttered stayed
 * stuttering until the owner noticed the control and guessed. This is the subscription
 * half — a bounded ring of `requestAnimationFrame` intervals — and every judgement it
 * feeds is pure and lives in `quality.ts` (`frameHealthFrom`, `degradedTier`), which is
 * why the decision can be tested in Node with no browser.
 *
 * Main-thread rAF intervals rather than the renderer's own timing on purpose: what the
 * owner perceives as stutter is frames not reaching the screen, whatever caused it — the
 * scene, a heavy panel, another tab on the same GPU.
 *
 * Sampling stops when `active` is false (a hidden tab, reduced motion, the 2D view), so
 * the measurement never contributes to the load it is measuring.
 */

import { useEffect, useState } from "react";

import { type FrameHealth, type QualityTier, frameHealthFrom } from "./quality";

/** Intervals kept. About two seconds at 60 Hz: long enough to be a trend, short enough to forget a hitch. */
export const FRAME_WINDOW = 120;

/** How often the health is published to React. Coarse: this drives a tier, not a meter. */
export const PUBLISH_EVERY = 30;

const EMPTY: FrameHealth = { samples: 0, slowFrames: 0, medianMs: 0 };

export function useFrameHealth(tier: QualityTier, active: boolean): FrameHealth {
  const [health, setHealth] = useState<FrameHealth>(EMPTY);

  useEffect(() => {
    if (!active || typeof requestAnimationFrame !== "function") {
      // Not measuring is not the same as measuring zero: an empty sample makes
      // `degradedTier` answer null, which is the honest "I do not know yet".
      setHealth(EMPTY);
      return;
    }
    const intervals: number[] = [];
    let last = 0;
    let frames = 0;
    let raf = 0;
    let stopped = false;

    const tick = (now: number) => {
      if (stopped) return;
      if (last !== 0) {
        intervals.push(now - last);
        if (intervals.length > FRAME_WINDOW) intervals.shift();
        frames += 1;
        if (frames % PUBLISH_EVERY === 0) setHealth(frameHealthFrom(intervals, tier));
      }
      last = now;
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
    };
  }, [tier, active]);

  return health;
}
