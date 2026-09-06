"use client";

/**
 * The WebGL surface. Loaded only in the browser, and only after
 * `detectRenderCapability` has said there is a context to draw into.
 *
 * Kept separate from `CoreScene` so that `three` is behind a `dynamic()`
 * boundary: a machine that falls back to the 2D view never downloads or parses
 * the 3D library at all, which is the difference between a fallback and a
 * fallback that still costs 600 KB.
 */

import { Canvas } from "@react-three/fiber";

import type { QualityTier } from "../lib/uistate/quality";
import { TIER_BUDGETS } from "../lib/uistate/quality";
import type { VisualIntent } from "../lib/uistate/visual";
import CoreScene from "./CoreScene";

export type CoreCanvasProps = {
  intent: VisualIntent;
  tier: QualityTier;
  /** Motion is reduced: draw the intent once, still, whenever it changes. */
  still: boolean;
  /** The tab is hidden: draw nothing and run nothing until it is shown again. */
  hidden: boolean;
};

export default function CoreCanvas({ intent, tier, still, hidden }: CoreCanvasProps) {
  const budget = TIER_BUDGETS[tier];
  return (
    <Canvas
      // `demand` while still or hidden: three renders only when the scene asks
      // (a settled frame on an intent change under reduced motion; never while
      // hidden), rather than spinning a loop that early-returns.
      frameloop={still || hidden ? "demand" : "always"}
      dpr={[1, budget.maxPixelRatio]}
      // Framed so the widest thing the scene can draw - the evidence field at
      // radius 2.0 - stays inside the frustum: tan(fov/2) * z = 0.3839 * 5.6 =
      // 2.15 world units of half-extent.
      camera={{ position: [0, 0, 5.6], fov: 42 }}
      gl={{
        antialias: tier === "high",
        // The Core is ambient and often left open; a low-power context asks
        // the OS for the integrated GPU on laptops rather than the discrete one.
        powerPreference: tier === "high" ? "high-performance" : "low-power",
        alpha: true,
      }}
      data-render-mode="3d"
      data-tier={tier}
      data-still={still || hidden ? "yes" : "no"}
      data-hidden={hidden ? "yes" : "no"}
    >
      <CoreScene intent={intent} tier={tier} still={still} hidden={hidden} />
    </Canvas>
  );
}
