/**
 * Quality tiers, capability detection and the frame budget.
 *
 * The constitution's rule for this file: **the renderer must never be on the
 * critical path of cognition.** The Core is a window onto the system, not part
 * of it, so every decision here errs towards doing less work:
 *
 * - the tier caps the frame rate and the geometry, and `low` is a real tier
 *   rather than a slightly cheaper `high`;
 * - a hidden tab renders nothing at all and polls slowly;
 * - `prefers-reduced-motion` is honoured by freezing motion, not by hiding
 *   information — the state readout is unchanged, only the animation stops;
 * - no WebGL means the 2D view, which shows exactly the same facts.
 *
 * Detection is pure where it can be and injectable where it cannot, so the
 * tests never need a browser.
 */

export type QualityTier = "high" | "balanced" | "low";

export const QUALITY_TIERS: QualityTier[] = ["high", "balanced", "low"];

export const TIER_LABEL: Record<QualityTier, string> = {
  high: "Yüksek",
  balanced: "Dengeli",
  low: "Düşük",
};

/**
 * What each tier is allowed to spend.
 *
 * `maxSatellites` bounds every count that comes from the API: a research run
 * that reports 400 candidates must not turn into 400 draw calls. The readout
 * always shows the true number even when the geometry is capped, so the cap
 * cannot quietly become a lie about how much evidence there is.
 */
export type TierBudget = {
  /** Frames per second the render loop is throttled to. */
  fps: number;
  /** Sphere subdivision for the core. */
  detail: number;
  /** Hard ceiling on drawn satellites, whatever the API reported. */
  maxSatellites: number;
  /** Lattice segments for the thinking topology. */
  latticeSegments: number;
  /** Device pixel ratio ceiling. */
  maxPixelRatio: number;
  /** Whether the additive glow shell is drawn at all. */
  glow: boolean;

  // ------------------------------------------- M18.1: the layered structure
  /** Internal concentric rings (the topology layers). */
  rings: number;
  /** Translucent structural shells standing off the nucleus. */
  shells: number;
  /**
   * Instanced energy particles, in total, across the two bounded flows (the
   * inward pull and the connection-path travellers). Zero drops the feature.
   */
  maxParticles: number;
  /** Whether the camera answers the pointer with a slight parallax. */
  parallax: boolean;
};

export const TIER_BUDGETS: Record<QualityTier, TierBudget> = {
  high: {
    fps: 60,
    detail: 4,
    maxSatellites: 64,
    latticeSegments: 48,
    maxPixelRatio: 2,
    glow: true,
    rings: 3,
    shells: 2,
    maxParticles: 160,
    parallax: true,
  },
  balanced: {
    fps: 30,
    detail: 3,
    maxSatellites: 32,
    latticeSegments: 24,
    maxPixelRatio: 1.5,
    glow: true,
    rings: 2,
    shells: 1,
    maxParticles: 80,
    parallax: true,
  },
  low: {
    fps: 20,
    detail: 1,
    maxSatellites: 12,
    latticeSegments: 0,
    maxPixelRatio: 1,
    glow: false,
    rings: 1,
    shells: 0,
    maxParticles: 0,
    parallax: false,
  },
};

/**
 * The most the 3D scene can mount at a tier, counted before any frame runs.
 *
 * `drawables` is the ceiling on three.js objects with their own draw call
 * (meshes, line sets, instanced meshes count once each); `instances` is the
 * ceiling on instanced copies across every instanced mesh; `maxParticles` is
 * the particle share of that. The figures are derived from the budget rather
 * than typed beside it, so the scene and this function cannot drift apart
 * without a test noticing: `CoreScene` mounts at most what is listed here.
 *
 * Every entry is a *maximum*: the scene draws far less in most states, because
 * a channel at zero mounts nothing.
 */
export type SceneBudget = {
  drawables: number;
  instances: number;
  maxParticles: number;
};

/** Peripheral capability nodes (SHADOW_READY) are capped separately and low. */
export const MAX_CAPABILITY_NODES = 8;

/** The evolution lab's construction layers: one ring per phase, four phases. */
const CONSTRUCTION_LAYERS = 4;

export function sceneBudgetFor(tier: QualityTier): SceneBudget {
  const b = TIER_BUDGETS[tier];
  const drawables =
    1 + // nucleus
    1 + // nucleus wire
    b.rings +
    b.shells +
    (b.latticeSegments > 0 ? 1 : 0) + // connection paths
    (b.maxParticles > 0 ? 2 : 0) + // inward flow, path travellers
    (b.glow ? 1 : 0) + // pulse shell
    1 + // evidence constellation (instanced)
    1 + // evidence field (instanced)
    1 + // constellation spokes
    1 + // capability nodes (instanced)
    1 + // eye aperture
    1 + // held boundary (waiting on the owner)
    1 + // error offset ring
    1 + // memory convergence ring
    CONSTRUCTION_LAYERS +
    2 + // release orbit: track and progress arc
    1; // the parked satellite's halo (its body is the first capability node)
  const instances = b.maxParticles + b.maxSatellites * 2 + MAX_CAPABILITY_NODES;
  return { drawables, instances, maxParticles: b.maxParticles };
}

export type RenderCapability =
  /** WebGL2 is available; the 3D core renders. */
  | { kind: "webgl2" }
  /** Only WebGL1; still 3D, but the tier is held at `balanced` or below. */
  | { kind: "webgl1" }
  /** No GPU surface at all. The 2D view is used, and it is not a downgrade in truth. */
  | { kind: "none"; reason: string };

/**
 * Probe for a usable GPU context.
 *
 * Creates a throwaway canvas and immediately loses the context: a probe that
 * leaks contexts will itself cause the failure it is testing for, since
 * browsers cap live WebGL contexts per page.
 */
export function detectRenderCapability(
  createCanvas: () => HTMLCanvasElement | null = () =>
    typeof document === "undefined" ? null : document.createElement("canvas"),
): RenderCapability {
  let canvas: HTMLCanvasElement | null = null;
  try {
    canvas = createCanvas();
    if (!canvas) return { kind: "none", reason: "tarayıcı ortamı yok" };
    const gl2 = canvas.getContext("webgl2");
    if (gl2) {
      gl2.getExtension("WEBGL_lose_context")?.loseContext();
      return { kind: "webgl2" };
    }
    const gl1 = canvas.getContext("webgl");
    if (gl1) {
      (gl1.getExtension("WEBGL_lose_context") as { loseContext(): void } | null)?.loseContext();
      return { kind: "webgl1" };
    }
    return { kind: "none", reason: "WebGL bağlamı oluşturulamadı" };
  } catch (err) {
    return {
      kind: "none",
      reason: err instanceof Error ? err.message : "WebGL kullanılamıyor",
    };
  } finally {
    canvas = null;
  }
}

/**
 * Cap a chosen tier by what the hardware can actually do.
 *
 * A WebGL1-only context is almost always old or software-rendered, so `high` is
 * refused there rather than delivered as a slideshow.
 */
export function tierFor(chosen: QualityTier, capability: RenderCapability): QualityTier {
  if (capability.kind === "none") return "low";
  if (capability.kind === "webgl1" && chosen === "high") return "balanced";
  return chosen;
}

/**
 * The default tier from coarse device signals, when the owner has not chosen.
 *
 * Deliberately conservative: a wrong guess towards `balanced` costs some visual
 * richness, a wrong guess towards `high` costs the owner's battery and, on a
 * shared machine, frames the rest of the desktop needs.
 */
export function defaultTier(
  hints: { deviceMemory?: number; hardwareConcurrency?: number; reducedMotion?: boolean } = {},
): QualityTier {
  if (hints.reducedMotion) return "low";
  const memory = hints.deviceMemory ?? 0;
  const cores = hints.hardwareConcurrency ?? 0;
  if (memory >= 8 && cores >= 8) return "high";
  if (memory > 0 && memory < 4) return "low";
  if (cores > 0 && cores < 4) return "low";
  return "balanced";
}

/** Read the coarse hints from the browser, if there is one. */
export function readDeviceHints(): {
  deviceMemory?: number;
  hardwareConcurrency?: number;
  reducedMotion?: boolean;
} {
  if (typeof navigator === "undefined") return {};
  const nav = navigator as Navigator & { deviceMemory?: number };
  const reducedMotion =
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
      : false;
  return {
    deviceMemory: nav.deviceMemory,
    hardwareConcurrency: nav.hardwareConcurrency,
    reducedMotion,
  };
}

// ------------------------------------------------------------------ polling

/**
 * Poll intervals. The visible figure is a compromise between a live-feeling
 * core and a request every frame; the hidden figure exists so a forgotten
 * background tab costs the Cloud Core almost nothing.
 */
export const POLL_VISIBLE_MS = 1_000;
export const POLL_HIDDEN_MS = 20_000;

/**
 * Back off after failures so an unreachable API is not hammered once a second.
 * Capped so recovery is still prompt once it comes back.
 */
export function backoffMs(consecutiveFailures: number, base = POLL_VISIBLE_MS): number {
  if (consecutiveFailures <= 0) return base;
  return Math.min(base * 2 ** Math.min(consecutiveFailures, 5), 30_000);
}

export function pollIntervalMs(hidden: boolean, consecutiveFailures: number): number {
  if (hidden) return Math.max(POLL_HIDDEN_MS, backoffMs(consecutiveFailures, POLL_HIDDEN_MS));
  return backoffMs(consecutiveFailures);
}

/**
 * Whether the render loop should draw this frame, given the tier's fps cap.
 *
 * Called from the animation loop; keeping it pure means the throttle is unit
 * tested rather than eyeballed.
 */
export function shouldRenderFrame(
  lastRenderAt: number,
  now: number,
  tier: QualityTier,
): boolean {
  return now - lastRenderAt >= 1000 / TIER_BUDGETS[tier].fps;
}

/** Clamp a reported count to what the tier may draw. The readout keeps the truth. */
export function drawableCount(reported: number, tier: QualityTier): number {
  return Math.max(0, Math.min(Math.floor(reported), TIER_BUDGETS[tier].maxSatellites));
}
