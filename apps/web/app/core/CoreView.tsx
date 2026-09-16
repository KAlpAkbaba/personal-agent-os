"use client";

/**
 * Chooses how to draw the Core, and guarantees that the choice never changes
 * *what* is drawn.
 *
 * The 3D and 2D paths take the same `VisualIntent` and are accompanied by the
 * same `StateReadout`. A machine with no WebGL loses fidelity and loses nothing
 * else — which is the only kind of graceful degradation worth having in a
 * system whose whole claim is that its interface is truthful.
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type QualityTier,
  type RenderCapability,
  detectRenderCapability,
  degradedTier,
  tierFor,
} from "../lib/uistate/quality";
import { usePageHidden, useReducedMotion } from "../lib/uistate/useCoreState";
import { useFrameHealth } from "../lib/uistate/useFrameHealth";
import type { VisualIntent } from "../lib/uistate/visual";
import CoreFallback2D from "./CoreFallback2D";

/**
 * `ssr: false` matters twice: `three` touches `window` at import time, and the
 * server has no GPU to detect, so a server-rendered 3D core would always be
 * wrong about its own capability.
 */
const CoreCanvas = dynamic(() => import("./CoreCanvas"), {
  ssr: false,
  loading: () => <div className="core-2d" aria-hidden />,
});

export type CoreViewProps = {
  intent: VisualIntent;
  tier: QualityTier;
  /** Force the non-3D path (the owner's toggle, and the tests). */
  force2d?: boolean;
};

export default function CoreView({ intent, tier, force2d = false }: CoreViewProps) {
  // `undefined` until probed: rendering a 3D canvas before we know whether
  // WebGL exists is how a fallback path stops being exercised.
  const [capability, setCapability] = useState<RenderCapability | undefined>(undefined);
  /**
   * B23 req 723: WebGL can go away AFTER the probe.
   *
   * `detectRenderCapability()` ran once, on mount, and nothing listened afterwards — so a
   * context lost to a driver reset, a laptop switching GPUs, or a browser reclaiming a
   * backgrounded context left a dead canvas on screen and the fallback, which exists
   * precisely for a machine without WebGL, never engaged. The owner would have been
   * looking at a black rectangle that still claimed to be the Core.
   *
   * `webglcontextlost` must have its default prevented for `webglcontextrestored` to ever
   * fire; the 2D view goes up in the meantime, showing the same facts, and says why.
   */
  const [contextLost, setContextLost] = useState<string | null>(null);
  const hidden = usePageHidden();
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    setCapability(detectRenderCapability());
  }, []);

  const onContextLost = useCallback((reason: string) => setContextLost(reason), []);
  const onContextRestored = useCallback(() => setContextLost(null), []);

  const capped = useMemo(
    () => (capability ? tierFor(tier, capability) : tier),
    [tier, capability],
  );

  // req 722: measure only while the 3D path is actually drawing — a hidden tab, reduced
  // motion and the 2D view all produce intervals that say nothing about the renderer.
  const measuring = capability?.kind !== "none" && !force2d && !hidden && !reducedMotion;
  const health = useFrameHealth(capped, measuring);
  const degraded = degradedTier(capped, health);
  const effectiveTier = degraded ?? capped;

  // Hidden tabs draw nothing and run nothing; reduced motion draws a still
  // frame per intent change. Both keep the readout exactly as it is. The 2D
  // view has one notion of stillness (its CSS animation is on or off); the 3D
  // view is told the two apart so a hidden tab can skip the frame entirely.
  const still = hidden || reducedMotion;

  if (capability === undefined) {
    return (
      <div className="core-stage" data-render-mode="probing">
        <p className="muted">Görüntüleme yetenekleri okunuyor…</p>
      </div>
    );
  }

  const use2d = force2d || capability.kind === "none" || contextLost !== null;
  const reason = force2d
    ? "2B görünüm seçildi. Gösterilen bilgiler aynıdır."
    : capability.kind === "none"
      ? `WebGL kullanılamıyor (${capability.reason}). 2B görünüm gösteriliyor; bilgiler aynıdır.`
      : contextLost !== null
        ? `WebGL bağlamı kayboldu (${contextLost}). 2B görünüme geçtim; bilgiler aynıdır.`
        : null;

  return (
    <div
      className="core-stage"
      data-effective-tier={effectiveTier}
      data-tier-degraded={degraded ? "yes" : "no"}
      data-frame-median-ms={health.samples ? Math.round(health.medianMs) : ""}
    >
      {use2d ? (
        <CoreFallback2D intent={intent} tier={effectiveTier} still={still} reason={reason} />
      ) : (
        <CoreCanvas
          intent={intent}
          tier={effectiveTier}
          still={reducedMotion}
          hidden={hidden}
          onContextLost={onContextLost}
          onContextRestored={onContextRestored}
        />
      )}
    </div>
  );
}
