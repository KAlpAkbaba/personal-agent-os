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
  /** Stop drawing altogether: hidden tab, or reduced motion. */
  still: boolean;
};

export default function CoreCanvas({ intent, tier, still }: CoreCanvasProps) {
  const budget = TIER_BUDGETS[tier];
  return (
    <Canvas
      // `demand` while still: three renders once for the current values and
      // then stops entirely, rather than spinning a loop that early-returns.
      frameloop={still ? "demand" : "always"}
      dpr={[1, budget.maxPixelRatio]}
      // Framed so the widest thing the scene can draw - the SHADOW_READY
      // satellite and its halo at x=1.7+0.26 - stays inside the frustum:
      // tan(fov/2) * z = 0.3839 * 5.6 = 2.15 world units of half-extent.
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
      data-still={still ? "yes" : "no"}
    >
      <CoreScene intent={intent} tier={tier} still={still} />
    </Canvas>
  );
}
