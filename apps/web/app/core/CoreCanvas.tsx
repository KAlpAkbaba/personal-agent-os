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
import { CAMERA_DISTANCE, CAMERA_FOV_DEG } from "../lib/uistate/scene";
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
      // Framed so the widest thing the scene can draw - the outer field at
      // radius 2.18 (M18.3) - stays inside the frustum, and so that the Core's
      // principal structure fills `CORE_FILL` of the stage. Both figures are
      // derived from these two numbers in `lib/uistate/scene.ts`, and
      // `stageSizeFor` sizes the stage from them: change the framing here and
      // the layout follows rather than the coverage claim quietly going stale.
      camera={{ position: [0, 0, CAMERA_DISTANCE], fov: CAMERA_FOV_DEG }}
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
