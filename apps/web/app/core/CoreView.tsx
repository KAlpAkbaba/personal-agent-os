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
import { useEffect, useMemo, useState } from "react";

import {
  type QualityTier,
  type RenderCapability,
  detectRenderCapability,
  tierFor,
} from "../lib/uistate/quality";
import { usePageHidden, useReducedMotion } from "../lib/uistate/useCoreState";
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
  const hidden = usePageHidden();
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    setCapability(detectRenderCapability());
  }, []);

  const effectiveTier = useMemo(
    () => (capability ? tierFor(tier, capability) : tier),
    [tier, capability],
  );

  // Hidden tabs draw nothing; reduced motion draws a still frame. Both keep the
  // readout exactly as it is.
  const still = hidden || reducedMotion;

  if (capability === undefined) {
    return (
      <div className="core-stage" data-render-mode="probing">
        <p className="muted">Görüntüleme yetenekleri okunuyor…</p>
      </div>
    );
  }

  const use2d = force2d || capability.kind === "none";
  const reason = force2d
    ? "2B görünüm seçildi. Gösterilen bilgiler aynıdır."
    : capability.kind === "none"
      ? `WebGL kullanılamıyor (${capability.reason}). 2B görünüm gösteriliyor; bilgiler aynıdır.`
      : null;

  return (
    <div className="core-stage">
      {use2d ? (
        <CoreFallback2D intent={intent} tier={effectiveTier} still={still} reason={reason} />
      ) : (
        <CoreCanvas intent={intent} tier={effectiveTier} still={still} />
      )}
    </div>
  );
}
