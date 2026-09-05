/**
 * The Core without a GPU.
 *
 * This is a *fallback in fidelity, not in truth*: it shows every fact the 3D
 * view shows, from the same `VisualIntent`, and the readout beside it is
 * literally the same component. An owner on a machine with no WebGL is not
 * given a lesser account of what the system is doing.
 *
 * Implementation notes that matter:
 *
 * - **Pure SVG, animated by CSS only.** No `requestAnimationFrame`, no JS
 *   timers. That keeps it off the main thread's critical path, lets it render
 *   identically on the server, and makes the whole thing assertable as static
 *   markup in Node — which is why this file, not the 3D scene, carries the
 *   rendering tests.
 * - **No randomness.** Every node position is a deterministic function of its
 *   index, so server and client markup match and a test can name a coordinate.
 * - **Zero channels draw nothing.** A zero `breathAmplitude` emits no animation
 *   at all rather than an animation of amplitude zero, so "still" is visible in
 *   the markup and cannot drift back into motion by CSS default.
 */

import type { VisualIntent } from "../lib/uistate/visual";
import { drawableCount, type QualityTier, TIER_BUDGETS } from "../lib/uistate/quality";

const VIEW = 320;
const CENTER = VIEW / 2;
const BASE_RADIUS = 74;

export type CoreFallback2DProps = {
  intent: VisualIntent;
  tier: QualityTier;
  /** Freeze all motion (prefers-reduced-motion, or a hidden tab). */
  still?: boolean;
  /** Why the 2D view is being used, when it is not merely the chosen tier. */
  reason?: string | null;
};

/** Evenly spaced points on a circle. Deterministic by construction. */
function ring(count: number, radius: number, phase = 0) {
  const points: Array<{ x: number; y: number; angle: number }> = [];
  for (let i = 0; i < count; i += 1) {
    const angle = phase + (i / Math.max(1, count)) * Math.PI * 2;
    points.push({
      x: CENTER + Math.cos(angle) * radius,
      y: CENTER + Math.sin(angle) * radius,
      angle,
    });
  }
  return points;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

export default function CoreFallback2D({
  intent,
  tier,
  still = false,
  reason = null,
}: CoreFallback2DProps) {
  const budget = TIER_BUDGETS[tier];
  const radius = round(BASE_RADIUS * intent.scale);
  const opacity = round(1 - intent.dim * 0.75);
  const animate = !still && intent.breathAmplitude > 0 && intent.breathHz > 0;

  const drawnNodes = intent.sourceNodesKnown
    ? drawableCount(intent.sourceNodes, tier)
    : 0;
  const nodes = ring(drawnNodes, radius + 34);

  // The thinking lattice: chords across the core, count bounded by the tier.
  // `low` has zero lattice segments, so the tier genuinely does less work
  // rather than doing the same work with smaller numbers.
  const latticeCount =
    intent.topology > 0 ? Math.round(intent.topology * (budget.latticeSegments / 4)) : 0;
  const lattice = ring(latticeCount * 2, radius * 0.82);

  // Listening: inward ticks. Length tracks the reported inward flow.
  const tickCount = intent.inwardFlow > 0 ? Math.min(16, budget.maxSatellites) : 0;
  const ticks = ring(tickCount, radius + 26);

  const style: React.CSSProperties & Record<string, string | number> = {
    "--breath-amp": intent.breathAmplitude,
    "--breath-dur": intent.breathHz > 0 ? `${round(1 / intent.breathHz)}s` : "0s",
    "--core-opacity": opacity,
  };

  return (
    <div
      className="core-2d"
      data-render-mode="2d"
      data-tier={tier}
      data-still={still ? "yes" : "no"}
      data-core-kind={intent.kind}
      style={style}
    >
      <svg
        viewBox={`0 0 ${VIEW} ${VIEW}`}
        role="img"
        aria-label={`Ajan çekirdeği: ${intent.kind}`}
        className={`core-svg palette-${intent.palette}`}
        data-radius={radius}
        data-opacity={opacity}
      >
        {/* Speaking: an outer pulse ring, sized by the reported energy only. */}
        {intent.pulse > 0 && (
          <circle
            className="core-pulse"
            cx={CENTER}
            cy={CENTER}
            r={round(radius + 14 + intent.pulse * 26)}
            data-pulse={round(intent.pulse)}
          />
        )}

        {/* Error: one offset ring. Bounded, static offset — never a strobe. */}
        {intent.agitation > 0 && (
          <circle
            className="core-agitation"
            cx={round(CENTER + intent.agitation * 6)}
            cy={CENTER}
            r={round(radius + 8)}
            data-agitation={round(intent.agitation)}
          />
        )}

        {/* Waiting on the owner: a held, dashed boundary. */}
        {intent.restraint > 0 && (
          <circle
            className="core-restraint"
            cx={CENTER}
            cy={CENTER}
            r={round(radius + 18)}
            data-restraint={round(intent.restraint)}
          />
        )}

        {/* The core itself. */}
        <circle
          className={`core-body ${animate ? "breathing" : ""}`}
          cx={CENTER}
          cy={CENTER}
          r={radius}
          data-breathing={animate ? "yes" : "no"}
        />

        {/* Thinking: internal topology. */}
        {latticeCount > 0 && (
          <g className="core-lattice" data-lattice-segments={latticeCount}>
            {Array.from({ length: latticeCount }, (_, i) => {
              const a = lattice[i];
              const b = lattice[(i + latticeCount) % lattice.length];
              return (
                <line
                  key={`lat-${i}`}
                  x1={round(a.x)}
                  y1={round(a.y)}
                  x2={round(b.x)}
                  y2={round(b.y)}
                />
              );
            })}
          </g>
        )}

        {/* Listening: energy drawn inward. */}
        {tickCount > 0 && (
          <g className="core-inward" data-inward-ticks={tickCount}>
            {ticks.map((p, i) => (
              <line
                key={`tick-${i}`}
                x1={round(p.x)}
                y1={round(p.y)}
                x2={round(CENTER + Math.cos(p.angle) * (radius + 6 - intent.inwardFlow * 10))}
                y2={round(CENTER + Math.sin(p.angle) * (radius + 6 - intent.inwardFlow * 10))}
              />
            ))}
          </g>
        )}

        {/* Memory: a convergence ring, drawn only against real progress. */}
        {intent.convergenceKnown && (
          <circle
            className="core-convergence"
            cx={CENTER}
            cy={CENTER}
            r={round(radius + 30 - intent.convergence * 30)}
            data-convergence={round(intent.convergence)}
          />
        )}

        {/* Research: one node per source the publisher actually counted. */}
        {drawnNodes > 0 && (
          <g className="core-sources" data-drawn-nodes={drawnNodes}>
            {nodes.map((p, i) => (
              <circle key={`src-${i}`} cx={round(p.x)} cy={round(p.y)} r={3.5} />
            ))}
          </g>
        )}

        {/* Evolution: the construction layers reached so far. */}
        {intent.constructionLayer > 0 && (
          <g className="core-construction" data-construction-layer={intent.constructionLayer}>
            {Array.from({ length: intent.constructionLayer }, (_, i) => (
              <circle
                key={`layer-${i}`}
                cx={CENTER}
                cy={CENTER}
                r={round(radius + 12 + i * 9)}
                strokeDasharray="6 10"
              />
            ))}
          </g>
        )}

        {/* SHADOW_READY: one completed satellite, parked outside the core. */}
        {intent.satelliteComplete && (
          <g className="core-satellite" data-satellite="complete">
            <circle cx={round(CENTER + radius + 34)} cy={CENTER} r={11} />
            <circle
              className="core-satellite-halo"
              cx={round(CENTER + radius + 34)}
              cy={CENTER}
              r={18}
            />
          </g>
        )}
      </svg>

      {reason && (
        <p className="muted core-fallback-reason" data-fallback-reason>
          {reason}
        </p>
      )}
    </div>
  );
}
